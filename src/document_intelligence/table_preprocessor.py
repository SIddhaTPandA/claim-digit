"""
Table Pre-Processor for SPD/SBC benefit plan PDFs.

Applies deterministic (non-LLM) transformations to the raw table list produced
by PDFProcessor before it is serialised to text and sent to the LLM.

Five transformations in fixed order:
  T1 -- Footnote / noise stripping          (always)
  T2 -- Column header normalisation         (always)
  T3 -- Wide table decomposition            (has_wide_tables)
  T4 -- Cross-reference resolution          (has_cross_references)
  T5 -- SBC limitations column extraction  (has_sbc_limitations_column)

Design principles:
  - Every transformation is wrapped in try/except; on failure it logs a
    warning and returns the input unchanged -- never crashes the pipeline.
  - All transformations are pure functions: (tables, profile) -> tables.
  - Dickenson County tables (3-column, no wide tables, no cross-refs) pass
    through all transformations untouched.
"""

import copy
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared patterns
# ---------------------------------------------------------------------------

_IN_NET_RE = re.compile(r"\bin[-\s]?network\b|network\s+provider", re.IGNORECASE)
_OUT_NET_RE = re.compile(
    r"\b(non[-\s]?network|out[-\s]?of[-\s]?network|out-of-network\s+provider)\b",
    re.IGNORECASE,
)

# Benefit-value pattern used in several transformations
_BENEFIT_VALUE_RE = re.compile(
    r"\d+\s*%|\$[\d,]+|not\s+covered|no\s+coverage|no\s+charge|"
    r"deductible\s+waived|100%;|copay|coinsurance|as\s+any|plan\s+pays|"
    r"covered\s+same\s+as",
    re.IGNORECASE,
)

# Footnote markers to strip from the END of cell values
_FOOTNOTE_RE = re.compile(r"\s*[\^*†‡§¶#]\s*$")

# SBC pre-auth and visit-limit patterns for T5
_PREAUTH_RE = re.compile(
    r"(pre-?authorization|pre-?auth|prior\s+authorization|"
    r"precertification|prior\s+approval)\s*required",
    re.IGNORECASE,
)
_VISIT_LIMIT_RE = re.compile(
    r"(\d+)\s+(visits?|days?|sessions?|treatments?)\s+per\s+"
    r"(calendar\s+year|benefit\s+year|plan\s+year|year|lifetime)",
    re.IGNORECASE,
)

# Place-of-service label patterns (for T3 wide-table detection)
_POS_PATTERNS = [
    (re.compile(r"physician'?s?\s+office", re.IGNORECASE), "Physician's Office"),
    (re.compile(r"independent\s+lab(?:oratory)?", re.IGNORECASE), "Independent Lab"),
    (re.compile(r"emergency\s+room\s*/?\s*urgent\s+care\s+facility", re.IGNORECASE), "Emergency Room/Urgent Care Facility"),
    (re.compile(r"emergency\s+room", re.IGNORECASE), "Emergency Room"),
    (re.compile(r"urgent\s+care", re.IGNORECASE), "Urgent Care"),
    (re.compile(r"outpatient\s+facility", re.IGNORECASE), "Outpatient Facility"),
    (re.compile(r"outpatient\s+professional\s+services?", re.IGNORECASE), "Outpatient Professional Services"),
    (re.compile(r"inpatient\s+hospital\s+facility", re.IGNORECASE), "Inpatient Hospital Facility"),
    (re.compile(r"inpatient\s+(?:hospital|facility)", re.IGNORECASE), "Inpatient Facility"),
    (re.compile(r"inpatient\s+professional\s+services?", re.IGNORECASE), "Inpatient Professional Services"),
    (re.compile(r"\*?ambulance", re.IGNORECASE), "Ambulance"),
]

# Header normalisation map: (compiled_regex, canonical_label)
_HEADER_NORMALIZATIONS = [
    # SBC-style verbose headers -> canonical
    (re.compile(r"network\s+provider\s*\(you will pay the least\)", re.IGNORECASE), "In-Network"),
    (re.compile(r"out-of-network\s+provider\s*\(you will pay the most\)", re.IGNORECASE), "Out-of-Network"),
    (re.compile(r"in-network\s+provider.*", re.IGNORECASE), "In-Network"),
    (re.compile(r"out-of-network\s+provider.*", re.IGNORECASE), "Out-of-Network"),
    (re.compile(r"limitations?,\s*exceptions?\s*.*important\s+information.*", re.IGNORECASE), "Limitations"),
    (re.compile(r"limitations?\s*,\s*exceptions?.*", re.IGNORECASE), "Limitations"),
    # SPD variants
    (re.compile(r"^non-?network$", re.IGNORECASE), "Out-of-Network"),
    (re.compile(r"^out\s+of\s+network$", re.IGNORECASE), "Out-of-Network"),
    (re.compile(r"^in\s+network$", re.IGNORECASE), "In-Network"),
]

# Cross-reference phrase -- re.DOTALL so .+ captures across embedded newlines
_CROSS_REF_PHRASE_RE = re.compile(
    r"covered\s+same\s+as\s+(?:the\s+)?plan'?s?\s+(.+)",
    re.IGNORECASE | re.DOTALL,
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

class TablePreprocessor:
    """
    Applies deterministic table transformations before LLM extraction.
    All methods are static; class exists for namespacing only.
    """

    @staticmethod
    def preprocess(
        tables: List[Dict[str, Any]],
        doc_profile: Any,
    ) -> List[Dict[str, Any]]:
        """
        Run all applicable transformations on the table list.

        Returns a new list of table dicts (deep-copied); originals unchanged.
        If any transformation raises, that transformation is skipped and the
        tables from the previous step are used.
        """
        if not tables:
            return tables

        # Deep copy so we never mutate the original extraction result
        result = copy.deepcopy(tables)

        steps = [
            ("T1:footnote_strip",        _t1_footnote_strip,        True),
            ("T2:header_normalise",      _t2_header_normalise,      True),
            ("T3:wide_table_decompose",  _t3_wide_table_decompose,  getattr(doc_profile, "has_wide_tables", False)),
            ("T4:cross_ref_resolve",     _t4_cross_ref_resolve,     getattr(doc_profile, "has_cross_references", False)),
            ("T5:sbc_limitations",       _t5_sbc_limitations,       getattr(doc_profile, "has_sbc_limitations_column", False)),
        ]

        for name, fn, enabled in steps:
            if not enabled:
                logger.debug("TablePreprocessor: %s skipped (not applicable)", name)
                continue
            try:
                before_count = len(result)
                result = fn(result)
                after_count = len(result)
                logger.info(
                    "TablePreprocessor: %s applied -- tables: %d -> %d",
                    name, before_count, after_count,
                )
            except Exception as exc:
                logger.warning(
                    "TablePreprocessor: %s failed (%s) -- using tables from previous step",
                    name, exc, exc_info=True,
                )

        return result


# ---------------------------------------------------------------------------
# T1 -- Footnote and noise stripping
# ---------------------------------------------------------------------------

def _t1_footnote_strip(tables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Strip trailing footnote markers (^, *, †, etc.) from all cell values.
    Also truncate cells longer than 400 characters to prevent a single long
    narrative cell from dominating the structured-table section.
    """
    for tbl in tables:
        new_rows = []
        for row in tbl.get("rows", []):
            new_row = []
            for cell in row:
                c = str(cell)
                c = _FOOTNOTE_RE.sub("", c).strip()
                if len(c) > 400:
                    c = c[:400] + "…"
                new_row.append(c)
            new_rows.append(new_row)
        tbl["rows"] = new_rows
    return tables


# ---------------------------------------------------------------------------
# T2 -- Column header normalisation
# ---------------------------------------------------------------------------

def _t2_header_normalise(tables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Normalise column header cells in the first two rows of every table.
    Replaces SBC-style verbose headers and SPD variant spellings with
    canonical labels: 'In-Network', 'Out-of-Network', 'Limitations'.
    Only touches header rows -- never data rows.
    """
    for tbl in tables:
        rows = tbl.get("rows", [])
        # Identify which rows are header rows (first 2, or rows with Azure
        # columnHeader kind recorded in cells metadata)
        header_row_indices = {0, 1}
        for cell_meta in tbl.get("cells", []):
            if cell_meta.get("kind") == "columnHeader":
                header_row_indices.add(cell_meta["row_index"])

        for ri, row in enumerate(rows):
            if ri not in header_row_indices:
                continue
            for ci, cell in enumerate(row):
                cell_str = str(cell).strip()
                for pattern, canonical in _HEADER_NORMALIZATIONS:
                    if pattern.fullmatch(cell_str) or (
                        10 < len(cell_str) <= 80 and pattern.search(cell_str)
                    ):
                        if cell_str != canonical:
                            logger.debug(
                                "T2: normalised header '%s' -> '%s'", cell_str, canonical
                            )
                        rows[ri][ci] = canonical
                        break
        tbl["rows"] = rows
    return tables


# ---------------------------------------------------------------------------
# T3 -- Wide table decomposition
# ---------------------------------------------------------------------------

def _t3_wide_table_decompose(tables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Decompose multi-place-of-service tables (>4 columns with place-of-service
    header labels) into one standard 3-column table per place of service.

    Handles the Cigna pattern where a single table encodes Laboratory,
    Radiology, Emergency Care, etc. across Physician's Office / Independent Lab /
    Emergency Room / Outpatient Facility column pairs.
    """
    output = []
    for tbl in tables:
        decomposed = _decompose_one_table(tbl)
        output.extend(decomposed)
    return output


def _decompose_one_table(tbl: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Returns a list of tables. If the table is not a wide multi-POS table,
    returns [tbl] unchanged. If it is, returns 2+ decomposed tables.
    """
    rows = tbl.get("rows", [])
    col_count = tbl.get("column_count", 0)

    if col_count <= 4 or len(rows) < 2:
        return [tbl]

    # ── Find the two header rows ──────────────────────────────────────────
    # Row 0 should contain place-of-service labels (repeated for each col pair)
    # Row 1 should contain alternating In-Network / Out-of-Network labels
    pos_row_idx, inout_row_idx = _find_header_rows(rows)
    if pos_row_idx is None or inout_row_idx is None:
        return [tbl]

    pos_row   = rows[pos_row_idx]
    inout_row = rows[inout_row_idx]

    # ── Map column indices to (POS label, in_or_out) ─────────────────────
    # Col 0 is always the service name column
    service_col = 0
    col_map: List[Optional[Tuple[str, str]]] = [None] * col_count

    # Determine POS for each column from the POS row.
    # Adjacent identical POS labels indicate a col pair.
    current_pos = None
    for ci in range(1, col_count):
        cell = str(pos_row[ci]).strip()
        if cell:
            matched_pos = _match_pos_label(cell)
            if matched_pos:
                current_pos = matched_pos
        if current_pos:
            inout_label = str(inout_row[ci]).strip().lower() if ci < len(inout_row) else ""
            if "in-network" in inout_label or inout_label == "in-network":
                col_map[ci] = (current_pos, "in_network")
            elif "out" in inout_label or "non" in inout_label:
                col_map[ci] = (current_pos, "out_network")

    # Collect unique POS labels in order of first appearance
    seen_pos: List[str] = []
    for entry in col_map:
        if entry and entry[0] not in seen_pos:
            seen_pos.append(entry[0])

    if len(seen_pos) < 2:
        # Not enough place-of-service columns identified -- leave unchanged
        logger.debug("T3: table %s -- fewer than 2 POS labels identified, skipping", tbl.get("table_index"))
        return [tbl]

    # ── Build per-POS sub-tables ──────────────────────────────────────────
    data_rows = [r for i, r in enumerate(rows) if i not in (pos_row_idx, inout_row_idx)]
    base_idx = tbl.get("table_index", 0)
    sub_tables = []

    for pos_label in seen_pos:
        in_cols  = [ci for ci, e in enumerate(col_map) if e and e[0] == pos_label and e[1] == "in_network"]
        out_cols = [ci for ci, e in enumerate(col_map) if e and e[0] == pos_label and e[1] == "out_network"]

        in_col  = in_cols[0]  if in_cols  else None
        out_col = out_cols[0] if out_cols else None

        if in_col is None:
            continue

        sub_rows = [["Service", "In-Network", "Out-of-Network"]]
        for row in data_rows:
            svc_raw = str(row[service_col]).strip() if service_col < len(row) else ""
            if not svc_raw:
                continue
            svc = f"{svc_raw} ({pos_label})"
            in_val  = str(row[in_col]).strip()  if in_col  < len(row) else ""
            out_val = str(row[out_col]).strip() if out_col is not None and out_col < len(row) else ""
            sub_rows.append([svc, in_val, out_val])

        if len(sub_rows) <= 1:
            continue  # no data rows for this POS

        sub_tbl = {
            "table_index":       f"{base_idx}_{pos_label.replace(' ', '_')}",
            "rows":              sub_rows,
            "cells":             [],
            "row_count":         len(sub_rows),
            "column_count":      3,
            "merged_cells_count": 0,
            "decomposed_from":   base_idx,
            "place_of_service":  pos_label,
        }
        sub_tables.append(sub_tbl)
        logger.debug(
            "T3: decomposed table %s into sub-table for '%s' (%d data rows)",
            base_idx, pos_label, len(sub_rows) - 1,
        )

    if len(sub_tables) < 2:
        return [tbl]

    logger.info(
        "T3: wide table %s decomposed into %d sub-tables: %s",
        base_idx, len(sub_tables), [s["place_of_service"] for s in sub_tables],
    )
    return sub_tables


def _find_header_rows(rows: List[List]) -> Tuple[Optional[int], Optional[int]]:
    """
    Find the row indices for (place-of-service row, in/out row) in a wide table.
    Returns (None, None) if the pattern is not recognised.
    """
    pos_row_idx = None
    inout_row_idx = None

    for ri, row in enumerate(rows[:4]):  # only scan first 4 rows
        row_text = " ".join(str(c) for c in row)
        pos_matches = sum(1 for p, _ in _POS_PATTERNS if p.search(row_text))
        in_out_matches = sum(
            1 for cell in row
            if _IN_NET_RE.search(str(cell)) or _OUT_NET_RE.search(str(cell))
        )
        if pos_matches >= 2 and pos_row_idx is None:
            pos_row_idx = ri
        if in_out_matches >= 2 and inout_row_idx is None:
            inout_row_idx = ri

    return pos_row_idx, inout_row_idx


def _match_pos_label(cell_text: str) -> Optional[str]:
    """Return the canonical place-of-service label for a cell, or None."""
    for pattern, label in _POS_PATTERNS:
        if pattern.search(cell_text):
            return label
    return None


# ---------------------------------------------------------------------------
# T4 -- Cross-reference resolution
# ---------------------------------------------------------------------------

def _t4_cross_ref_resolve(tables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Two-pass resolution of 'Covered same as plan's X' cross-references.

    Pass 1: Build a lookup mapping normalised service names to their
            {in_network, out_network} coinsurance values.
    Pass 2: For every cell matching the cross-reference pattern, replace the
            cell value with the looked-up coinsurance from the correct column.
            If lookup fails, leave the value unchanged (logged as warning).
    """
    lookup = _build_coinsurance_lookup(tables)
    if not lookup:
        logger.debug("T4: cross-ref lookup empty -- no resolutions possible")
        return tables

    resolved_count = 0
    for tbl in tables:
        rows = tbl.get("rows", [])
        # Identify In-Network and Out-of-Network column indices
        in_col, out_col = _find_inout_cols(rows)

        for ri, row in enumerate(rows):
            for ci, cell in enumerate(row):
                m = _CROSS_REF_PHRASE_RE.search(str(cell))
                if not m:
                    continue
                ref_phrase = m.group(1).strip().rstrip(".")
                resolved = _lookup_coinsurance(lookup, ref_phrase, ci, in_col, out_col)
                if resolved:
                    rows[ri][ci] = resolved
                    resolved_count += 1
                    logger.debug(
                        "T4: resolved '%s' -> '%s' (col %d)",
                        cell, resolved, ci,
                    )
                else:
                    logger.warning(
                        "T4: could not resolve cross-ref '%s' -- left unchanged", cell
                    )
        tbl["rows"] = rows

    if resolved_count:
        logger.info("T4: %d cross-references resolved", resolved_count)
    return tables


def _build_coinsurance_lookup(tables: List[Dict[str, Any]]) -> Dict[str, Dict[str, str]]:
    """
    Scan all table rows and build: normalised_service -> {in_network, out_network}.
    Only includes rows where the In-Network value contains a benefit value and
    does NOT itself contain a cross-reference.
    """
    lookup: Dict[str, Dict[str, str]] = {}

    for tbl in tables:
        rows = tbl.get("rows", [])
        in_col, out_col = _find_inout_cols(rows)
        if in_col is None:
            continue

        for row in rows[1:]:  # skip header row
            if len(row) <= in_col:
                continue
            svc = str(row[0]).strip()
            in_val = str(row[in_col]).strip() if in_col < len(row) else ""
            out_val = str(row[out_col]).strip() if out_col is not None and out_col < len(row) else ""

            if not svc or not in_val:
                continue
            if _CROSS_REF_PHRASE_RE.search(in_val):
                continue  # skip rows that are themselves cross-references
            if not _BENEFIT_VALUE_RE.search(in_val):
                continue  # skip non-benefit rows (headers, notes)

            key = _normalise_lookup_key(svc)
            if key and key not in lookup:
                lookup[key] = {"in_network": in_val, "out_network": out_val}

    return lookup


def _normalise_lookup_key(text: str) -> str:
    """Lower-case, strip possessives + punctuation, collapse whitespace for fuzzy matching."""
    text = text.lower().strip()
    text = text.replace('�', ' ')          # Unicode replacement char from corrupted PDF encoding
    text = re.sub(r"'s\b", "", text)           # strip possessives: "physician's" -> "physician"
    # Replace punctuation character by character to avoid regex range issues
    for ch in "·•–—'-/\\()[]{}":
        text = text.replace(ch, " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _find_inout_cols(rows: List[List]) -> Tuple[Optional[int], Optional[int]]:
    """Return (in_col, out_col) indices from a table's header rows, or (None, None)."""
    in_col = out_col = None
    for row in rows[:3]:
        for ci, cell in enumerate(row):
            c = str(cell)
            if in_col is None and _IN_NET_RE.search(c) and not _OUT_NET_RE.search(c):
                in_col = ci
            if out_col is None and _OUT_NET_RE.search(c):
                out_col = ci
        if in_col is not None and out_col is not None:
            break
    return in_col, out_col


def _lookup_coinsurance(
    lookup: Dict[str, Dict[str, str]],
    ref_phrase: str,
    col_idx: int,
    in_col: Optional[int],
    out_col: Optional[int],
) -> Optional[str]:
    """
    Find the best match for ref_phrase in the lookup, then return either
    the in_network or out_network value depending on col_idx.
    """
    key = _normalise_lookup_key(ref_phrase)
    # Remove common filler words for broader matching
    key_words = set(
        w for w in re.split(r"\s+", key)
        if len(w) > 2 and w not in {"the", "and", "for", "per", "any", "all", "plan", "plans", "services", "benefit", "benefits"}
    )

    best_key = None
    best_score = 0
    for lk in lookup:
        lk_words = set(re.split(r"\s+", lk))
        overlap = len(key_words & lk_words)
        if overlap > best_score:
            best_score = overlap
            best_key = lk

    if best_key is None or best_score < 2:
        return None

    entry = lookup[best_key]
    # Determine whether caller wants in-network or out-of-network value
    if out_col is not None and col_idx == out_col:
        return entry.get("out_network") or entry.get("in_network")
    return entry.get("in_network")


# ---------------------------------------------------------------------------
# T5 -- SBC limitations column extraction
# ---------------------------------------------------------------------------

def _t5_sbc_limitations(tables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    For SBC tables with a 'Limitations' column, append structured annotations
    to the limitations cell so the LLM can reliably extract pre-auth and visit
    limits without having to parse free-form sentences.

    Annotations appended (if found):
      [Pre-auth: Yes]
      [Limit: N visits per Benefit Year]
    """
    for tbl in tables:
        rows = tbl.get("rows", [])
        if not rows:
            continue

        # Find the limitations column index
        lim_col = None
        for ri, row in enumerate(rows[:2]):
            for ci, cell in enumerate(row):
                if re.search(r"limitation|exception|important\s+information", str(cell), re.IGNORECASE):
                    lim_col = ci
                    break
            if lim_col is not None:
                break

        if lim_col is None:
            continue

        annotated = 0
        for ri, row in enumerate(rows):
            if ri == 0:  # skip header row
                continue
            if lim_col >= len(row):
                continue
            lim_text = str(row[lim_col]).strip()
            if not lim_text or lim_text in ("-", "---", "N/A", "None"):
                continue

            additions = []
            if _PREAUTH_RE.search(lim_text):
                additions.append("[Pre-auth: Yes]")
            else:
                additions.append("[Pre-auth: No]")

            vm = _VISIT_LIMIT_RE.search(lim_text)
            if vm:
                qty    = vm.group(1)
                unit   = vm.group(2).rstrip("s")  # normalise "visits" -> "visit"
                period = vm.group(3).title().replace("Calendar Year", "Benefit Year")
                additions.append(f"[Limit: {qty} {unit}s per {period}]")

            if additions:
                row[lim_col] = lim_text + "  " + "  ".join(additions)
                annotated += 1

        if annotated:
            logger.debug("T5: %d limitation cells annotated in table %s", annotated, tbl.get("table_index"))
        tbl["rows"] = rows

    return tables
