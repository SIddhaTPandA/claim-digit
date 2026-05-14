"""
Document Classifier for SPD/SBC benefit plan PDFs.

Performs rule-based (no LLM) classification of the extracted text and tables
to produce a DocumentProfile that downstream components use to:
  - Select the correct LLM prompt template
  - Enable/disable specific table pre-processing transformations

Design principles:
  - Conservative: when uncertain, default to existing SPD behavior
  - No API calls: all signals are regex/structural, zero latency overhead
  - Auditable: every classification flag records the signal(s) that triggered it
  - Non-breaking: an UNKNOWN classification falls through to the current pipeline
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Signal patterns
# ---------------------------------------------------------------------------

# SBC documents always contain this exact CMS-mandated phrase in their header
_SBC_DEFINITIVE = re.compile(
    r"Summary of Benefits and Coverage",
    re.IGNORECASE,
)
# Secondary SBC signals (each weak alone, strong in combination)
_SBC_SUPPORTING = [
    re.compile(r"What You Will Pay", re.IGNORECASE),
    re.compile(r"Network Provider\s*\(You will pay the least\)", re.IGNORECASE),
    re.compile(r"Out-of-Network Provider\s*\(You will pay the most\)", re.IGNORECASE),
    re.compile(r"This is only a summary", re.IGNORECASE),
    re.compile(r"Coverage Period:", re.IGNORECASE),
]

_SPD_SIGNALS = [
    re.compile(r"Summary Plan Description", re.IGNORECASE),
    re.compile(r"This document constitutes the Summary Plan Description", re.IGNORECASE),
]

# Value-perspective signals: plan-pays language
_PLAN_PAYS_RE = re.compile(
    r"(your\s+)?plan\s+pays\s+\d+\s*%|the\s+plan\s+pays\s+\d+\s*%",
    re.IGNORECASE,
)
# Value-perspective signals: member-pays language
_MEMBER_PAYS_RE = re.compile(
    r"\d+\s*%\s+coinsurance|you\s+pay\s+\$[\d,]+|no\s+charge\b|not\s+applicable\b",
    re.IGNORECASE,
)

# Network model signals
_HDHP_RE = re.compile(r"\bHDHP\b|\bHSA\b|High\s+Deductible\s+Health\s+Plan", re.IGNORECASE)
_HMO_RE = re.compile(r"\bHMO\b|Health\s+Maintenance\s+Organization", re.IGNORECASE)
_PPO_RE = re.compile(r"\bPPO\b|Preferred\s+Provider\s+Organization", re.IGNORECASE)

# Wide / multi-place-of-service table signals (header cell content)
_PLACE_OF_SERVICE_TERMS = [
    re.compile(r"physician'?s?\s+office", re.IGNORECASE),
    re.compile(r"independent\s+lab", re.IGNORECASE),
    re.compile(r"emergency\s+room", re.IGNORECASE),
    re.compile(r"urgent\s+care", re.IGNORECASE),
    re.compile(r"outpatient\s+facility", re.IGNORECASE),
    re.compile(r"inpatient\s+(hospital|facility)", re.IGNORECASE),
]

# Combined deductible cell: "Individual: $1,500 Family: $3,000"
_COMBINED_DEDUCTIBLE_RE = re.compile(
    r"individual\s*:?\s*\$[\d,]+\s+family\s*:?\s*\$[\d,]+",
    re.IGNORECASE,
)

# Cross-reference: "Covered same as plan's X"
_CROSS_REF_RE = re.compile(
    r"covered\s+same\s+as\s+plan'?s?\s+\w",
    re.IGNORECASE,
)

# SBC Limitations column header
_LIMITATIONS_HEADER_RE = re.compile(
    r"limitation|exception|important\s+information",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# DocumentProfile
# ---------------------------------------------------------------------------

@dataclass
class DocumentProfile:
    """Classification result for a single benefit plan document."""

    doc_type: str = "UNKNOWN"
    # "SPD"     -- Summary Plan Description (employer-written, variable format)
    # "SBC"     -- Summary of Benefits and Coverage (CMS-standardized federal form)
    # "UNKNOWN" -- could not determine; pipeline falls back to current SPD behavior

    value_perspective: str = "unknown"
    # "plan_pays"   -- document expresses benefits as what the plan pays ("plan pays 80%")
    # "member_pays" -- document expresses benefits as what the member pays ("20% coinsurance")
    # "mixed"       -- both styles present (e.g. copays member-perspective, coinsurance plan-perspective)
    # "unknown"     -- not enough signal to determine

    network_model: str = "UNKNOWN"
    # "PPO" | "HMO" | "HDHP" | "UNKNOWN"

    has_wide_tables: bool = False
    # True when at least one table has >4 columns AND the header contains
    # place-of-service labels -- the Cigna multi-place-of-service pattern.

    has_combined_deductible_cells: bool = False
    # True when any table cell contains both "Individual: $X" and "Family: $Y"
    # packed into the same cell (Cigna deductible format).

    has_cross_references: bool = False
    # True when any table cell contains "Covered same as plan's X".

    has_sbc_limitations_column: bool = False
    # True when a table has 3+ columns and the last column header matches
    # the SBC "Limitations, Exceptions & Other Important Information" pattern.

    confidence: float = 0.0
    # Overall classification confidence 0.0–1.0.
    # < 0.5 -> UNKNOWN forced; pipeline uses existing behavior.

    signals: List[str] = field(default_factory=list)
    # Human-readable log of every signal that fired, for debugging.


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

class DocumentClassifier:
    """
    Classifies a benefit plan document using only text patterns and table structure.
    No LLM call; deterministic and zero-latency.
    """

    @staticmethod
    def classify(text: str, tables: List[Dict[str, Any]]) -> DocumentProfile:
        """
        Classify the document and return a DocumentProfile.

        Args:
            text:   Full extracted text from the PDF (Azure DI / pdfplumber / pypdf).
            tables: List of table dicts as produced by PDFProcessor.extract_content().

        Returns:
            DocumentProfile with all flags set and confidence score.
        """
        profile = DocumentProfile()
        signals: List[str] = []

        # ── Step 1: doc_type ─────────────────────────────────────────────────
        # Use first 5,000 chars for doc_type detection (title/header area).
        header_text = text[:5000]

        sbc_score = 0
        if _SBC_DEFINITIVE.search(header_text):
            sbc_score += 3
            signals.append("SBC definitive phrase found: 'Summary of Benefits and Coverage'")
        for pat in _SBC_SUPPORTING:
            if pat.search(header_text):
                sbc_score += 1
                signals.append(f"SBC supporting signal: '{pat.pattern}'")

        spd_score = 0
        for pat in _SPD_SIGNALS:
            if pat.search(header_text):
                spd_score += 2
                signals.append(f"SPD signal: '{pat.pattern}'")

        # Also check table headers for SBC patterns (Azure DI may extract table
        # headers before the document title appears in running text)
        for tbl in tables:
            for row in tbl.get("rows", [])[:3]:
                row_text = " ".join(str(c) for c in row)
                if _SBC_DEFINITIVE.search(row_text):
                    sbc_score += 2
                    signals.append("SBC definitive phrase found in table header row")
                for pat in _SBC_SUPPORTING[:3]:  # first 3 are strongest
                    if pat.search(row_text):
                        sbc_score += 1
                        signals.append(f"SBC supporting signal in table: '{pat.pattern}'")

        if sbc_score >= 3:
            profile.doc_type = "SBC"
            profile.confidence = min(1.0, 0.7 + sbc_score * 0.05)
        elif spd_score >= 2:
            profile.doc_type = "SPD"
            profile.confidence = min(1.0, 0.7 + spd_score * 0.1)
        elif sbc_score >= 1:
            # Weak SBC signal -- label as SBC but low confidence
            profile.doc_type = "SBC"
            profile.confidence = 0.5
        else:
            # Default: treat as SPD (existing behavior)
            profile.doc_type = "SPD"
            profile.confidence = 0.5
            signals.append("No definitive doc_type signal -- defaulting to SPD")

        # ── Step 2: value_perspective ────────────────────────────────────────
        # Scan ALL text (not just header) and all table cells.
        all_text = text
        for tbl in tables:
            for row in tbl.get("rows", []):
                all_text += " ".join(str(c) for c in row) + " "

        plan_pays_count = len(_PLAN_PAYS_RE.findall(all_text))
        member_pays_count = len(_MEMBER_PAYS_RE.findall(all_text))

        if plan_pays_count >= 3 and member_pays_count < 3:
            profile.value_perspective = "plan_pays"
            signals.append(f"plan_pays: {plan_pays_count} plan-pays patterns found")
        elif member_pays_count >= 3 and plan_pays_count < 3:
            profile.value_perspective = "member_pays"
            signals.append(f"member_pays: {member_pays_count} member-pays patterns found")
        elif plan_pays_count >= 3 and member_pays_count >= 3:
            profile.value_perspective = "mixed"
            signals.append(
                f"mixed perspective: {plan_pays_count} plan-pays + {member_pays_count} member-pays"
            )
        else:
            profile.value_perspective = "unknown"
            signals.append("value_perspective unknown: insufficient signal")

        # ── Step 3: network_model ────────────────────────────────────────────
        if _HDHP_RE.search(header_text):
            profile.network_model = "HDHP"
            signals.append("HDHP/HSA network model detected")
        elif _HMO_RE.search(header_text):
            profile.network_model = "HMO"
            signals.append("HMO network model detected")
        elif _PPO_RE.search(header_text):
            profile.network_model = "PPO"
            signals.append("PPO network model detected")
        else:
            profile.network_model = "UNKNOWN"

        # ── Step 4: structural table flags ───────────────────────────────────
        for tbl in tables:
            col_count = tbl.get("column_count", 0)
            rows = tbl.get("rows", [])
            if not rows:
                continue

            # Flatten first two rows for header analysis
            header_cells = []
            for row in rows[:2]:
                header_cells.extend(str(c) for c in row)
            header_str = " ".join(header_cells)

            # has_wide_tables: >4 columns AND ≥2 place-of-service terms in header
            if col_count > 4:
                pos_matches = sum(
                    1 for pat in _PLACE_OF_SERVICE_TERMS if pat.search(header_str)
                )
                if pos_matches >= 2:
                    profile.has_wide_tables = True
                    signals.append(
                        f"Wide table detected: {col_count} cols, "
                        f"{pos_matches} place-of-service header terms"
                    )

            # has_sbc_limitations_column: ≥3 cols + last header matches limitations RE
            if col_count >= 3:
                last_header = str(rows[0][-1]) if rows[0] else ""
                if not last_header and len(rows) > 1:
                    last_header = str(rows[1][-1])
                if _LIMITATIONS_HEADER_RE.search(last_header):
                    profile.has_sbc_limitations_column = True
                    signals.append(
                        f"SBC limitations column detected: '{last_header}'"
                    )

            # has_combined_deductible_cells and has_cross_references: scan all cells
            for row in rows:
                for cell in row:
                    cell_str = str(cell)
                    if not profile.has_combined_deductible_cells and _COMBINED_DEDUCTIBLE_RE.search(cell_str):
                        profile.has_combined_deductible_cells = True
                        signals.append(
                            f"Combined deductible cell: '{cell_str[:80]}'"
                        )
                    if not profile.has_cross_references and _CROSS_REF_RE.search(cell_str):
                        profile.has_cross_references = True
                        signals.append(
                            f"Cross-reference cell: '{cell_str[:80]}'"
                        )

        profile.signals = signals
        logger.info(
            "DocumentClassifier: type=%s perspective=%s network=%s "
            "wide_tables=%s combined_ded=%s cross_refs=%s sbc_limits=%s "
            "confidence=%.2f",
            profile.doc_type, profile.value_perspective, profile.network_model,
            profile.has_wide_tables, profile.has_combined_deductible_cells,
            profile.has_cross_references, profile.has_sbc_limitations_column,
            profile.confidence,
        )
        for sig in signals:
            logger.debug("  signal: %s", sig)

        return profile
