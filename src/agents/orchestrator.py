
"""
Orchestrator for SPD Benefits Extraction Pipeline.
Coordinates the full extraction workflow:
1. PDF Processing (Azure Document Intelligence / pdfplumber)
2. CrewAI Benefits Extraction (single Azure OpenAI GPT-backed agent)
3. Excel Output (ExcelGenerator)
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.document_intelligence.pdf_processor import PDFProcessor
from src.agents.benefits_extraction_crew import run_benefits_extraction_crew

logger = logging.getLogger(__name__)


@dataclass
class ProcessingResult:
    source_file: str
    output_file: Optional[str]
    success: bool
    records_extracted: int
    overall_confidence: float
    requires_review: bool
    error_message: Optional[str] = None
    validation_issues: int = 0


class Orchestrator:
    def __init__(self, output_dir="OutputExcel", text_output_dir="Output", confidence_threshold=0.70, enable_validation=True):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.text_output_dir = Path(text_output_dir)
        self.text_output_dir.mkdir(parents=True, exist_ok=True)
        self.confidence_threshold = confidence_threshold
        self.enable_validation = enable_validation
        self.pdf_processor = PDFProcessor()
        logger.info("Orchestrator initialized. Excel dir: %s, Text dir: %s, Confidence threshold: %.0f%%", self.output_dir, self.text_output_dir, confidence_threshold * 100)

    @staticmethod
    def _tables_to_text(tables):
        if not tables:
            return ""
        lines = [
            "",
            "",
            "--- STRUCTURED TABLES (merged cells expanded across all spanned columns) ---",
            "NOTE: When a cell spans multiple columns its value is repeated in each column below.",
            "Treat each column value independently for extraction.",
            "",
        ]
        for tbl in tables:
            idx = tbl.get("table_index", "?")
            merged_count = tbl.get("merged_cells_count", 0)
            lines.append(f"[TABLE {idx}]  merged-cell spans expanded: {merged_count}")
            for row in tbl.get("rows", []):
                # Skip section-header rows: these are rows where Azure DI repeats
                # the section title across every column (e.g.
                # | Emergency and Urgent Care Services | Emergency and Urgent Care Services |)
                # They are table dividers, not benefit rows, and cause the LLM to
                # extract them as duplicate service entries.
                non_empty = [str(c).strip() for c in row if str(c).strip()]
                if len(non_empty) >= 2 and len(set(non_empty)) == 1:
                    continue  # all non-empty cells identical → section-header row
                lines.append("| " + " | ".join(str(c) for c in row) + " |")
            lines.append("")
        lines.append("--- END STRUCTURED TABLES ---")
        lines.append("")
        return "\n".join(lines)

    def process_document(self, pdf_path, output_filename=None):
        pdf_path = Path(pdf_path)
        source_name = pdf_path.stem
        logger.info("Processing document: %s", pdf_path.name)
        try:
            logger.info("Step 1: Extracting PDF content...")
            pdf_content = self.pdf_processor.extract_content(str(pdf_path))
            if not pdf_content:
                return ProcessingResult(source_file=str(pdf_path), output_file=None, success=False, records_extracted=0, overall_confidence=0.0, requires_review=True, error_message="Failed to extract content from PDF")
            text_content = pdf_content.get("text", "")
            tables = pdf_content.get("tables", [])
            if tables:
                tables_text = self._tables_to_text(tables)
                logger.info(
                    "Prepending %d structured table(s) to LLM input "
                    "(merged cells already expanded by PDFProcessor). "
                    "Tables placed at TOP so LLM sees correct merged-cell values "
                    "before raw OCR text, ensuring deduplication keeps table values.",
                    len(tables),
                )
                # PREPEND tables so they appear in the FIRST chunk sent to the LLM.
                # Previously tables were appended to the end, so raw OCR chunks
                # (where merged cells show only one column value) were processed
                # first and won deduplication. Prepending guarantees the correct
                # per-column values from the structured table are seen first.
                text_content = tables_text + text_content
            txt_path = self.text_output_dir / f"{source_name}_extracted.txt"
            txt_path.write_text(text_content, encoding="utf-8")
            logger.info("Raw text saved: %s", txt_path)

            # ── Early-exit check ──────────────────────────────────────────────────────
            # Set EXTRACT_ONLY=true in .env (or environment) to stop after the text
            # extraction step and skip the LLM parsing phase entirely.
            # Useful for inspecting the raw extracted text before committing to a
            # full LLM run.
            if os.getenv("EXTRACT_ONLY", "false").strip().lower() == "true":
                logger.info(
                    "EXTRACT_ONLY=true -- stopping after text extraction. "
                    "LLM parsing skipped. Text saved to: %s", txt_path
                )
                return ProcessingResult(
                    source_file=str(pdf_path),
                    output_file=None,
                    success=True,
                    records_extracted=0,
                    overall_confidence=0.0,
                    requires_review=False,
                    error_message="EXTRACT_ONLY mode: LLM parsing skipped.",
                )
            # ─────────────────────────────────────────────────────────────────────────

            logger.info("Step 2: Running LLM powered benefits extraction ...")
            output_name = output_filename or f"{source_name}.xlsx"
            output_path = self.output_dir / output_name
            records = run_benefits_extraction_crew(text_content=text_content, output_excel_path=str(output_path))
            records_extracted = len(records)
            if records_extracted == 0:
                return ProcessingResult(source_file=str(pdf_path), output_file=None, success=False, records_extracted=0, overall_confidence=0.0, requires_review=True, error_message="Crew extracted 0 records from document")
            scores = []
            for r in records:
                try:
                    scores.append(float(r.get("Confidence Score") or 0))
                except (ValueError, TypeError):
                    pass
            overall_confidence = sum(scores) / len(scores) if scores else 0.0
            requires_review = overall_confidence < self.confidence_threshold
            logger.info("Successfully processed %s: %d records, confidence: %.0f%%", pdf_path.name, records_extracted, overall_confidence * 100)
            return ProcessingResult(source_file=str(pdf_path), output_file=str(output_path), success=True, records_extracted=records_extracted, overall_confidence=overall_confidence, requires_review=requires_review, validation_issues=0)
        except Exception as exc:
            logger.error("Error processing %s: %s", pdf_path.name, exc, exc_info=True)
            return ProcessingResult(source_file=str(pdf_path), output_file=None, success=False, records_extracted=0, overall_confidence=0.0, requires_review=True, error_message=str(exc))

    def process_directory(self, input_dir, file_pattern="*.pdf"):
        input_path = Path(input_dir)
        pdf_files = list(input_path.glob(file_pattern))
        logger.info("Found %d PDF files in %s", len(pdf_files), input_dir)
        results = [self.process_document(str(f)) for f in pdf_files]
        successful = sum(1 for r in results if r.success)
        total_records = sum(r.records_extracted for r in results)
        logger.info("Processing complete: %d/%d successful, %d total records", successful, len(results), total_records)
        return results

    def orchestrate(self, documents):
        return [self.process_document(doc) for doc in documents]
