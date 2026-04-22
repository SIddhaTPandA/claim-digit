"""
Orchestrator for SPD Benefits Extraction Pipeline.

Coordinates the full extraction workflow:
1. PDF Processing (Azure Document Intelligence / pdfplumber)
2. CrewAI Benefits Extraction (single Azure OpenAI GPT-backed agent)
3. Excel Output (ExcelGenerator)
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from src.document_intelligence.pdf_processor import PDFProcessor
from src.agents.benefits_extraction_crew import run_benefits_extraction_crew

logger = logging.getLogger(__name__)


@dataclass
class ProcessingResult:
    """Result of processing a single document."""
    source_file: str
    output_file: Optional[str]
    success: bool
    records_extracted: int
    overall_confidence: float
    requires_review: bool
    error_message: Optional[str] = None
    validation_issues: int = 0


class Orchestrator:
    """
    Main orchestrator for SPD Benefits extraction pipeline.
    Extracts PDF text then hands it to the CrewAI extraction crew,
    which returns structured rows written to Excel.
    """

    def __init__(
        self,
        output_dir: str = "OutputExcel",
        confidence_threshold: float = 0.70,
        enable_validation: bool = True,
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.confidence_threshold = confidence_threshold
        self.enable_validation = enable_validation
        self.pdf_processor = PDFProcessor()
        logger.info(
            "Orchestrator initialized. Output dir: %s, Confidence threshold: %.0f%%",
            self.output_dir, confidence_threshold * 100,
        )

    # ------------------------------------------------------------------
    def process_document(
        self,
        pdf_path: str,
        output_filename: Optional[str] = None,
    ) -> ProcessingResult:
        """Process a single PDF document through the full pipeline."""
        pdf_path = Path(pdf_path)
        source_name = pdf_path.stem
        logger.info("Processing document: %s", pdf_path.name)

        try:
            # ── Step 1: Extract text from PDF ────────────────────────────────
            logger.info("Step 1: Extracting PDF content...")
            pdf_content = self.pdf_processor.extract_content(str(pdf_path))

            if not pdf_content:
                return ProcessingResult(
                    source_file=str(pdf_path), output_file=None,
                    success=False, records_extracted=0, overall_confidence=0.0,
                    requires_review=True,
                    error_message="Failed to extract content from PDF",
                )

            text_content = pdf_content.get("text", "")

            # Save raw text for debugging
            txt_path = self.output_dir / f"{source_name}_extracted.txt"
            txt_path.write_text(text_content, encoding="utf-8")
            logger.info("Raw text saved: %s", txt_path)

            # ── Step 2: CrewAI extraction crew ───────────────────────────────
            logger.info("Step 2: Running CrewAI benefits extraction crew...")
            output_name = output_filename or f"{source_name}.xlsx"
            output_path = self.output_dir / output_name

            records = run_benefits_extraction_crew(
                text_content=text_content,
                output_excel_path=str(output_path),
            )

            records_extracted = len(records)
            if records_extracted == 0:
                return ProcessingResult(
                    source_file=str(pdf_path), output_file=None,
                    success=False, records_extracted=0, overall_confidence=0.0,
                    requires_review=True,
                    error_message="Crew extracted 0 records from document",
                )

            # Compute average confidence from Confidence Score column
            scores = []
            for r in records:
                try:
                    scores.append(float(r.get("Confidence Score") or 0))
                except (ValueError, TypeError):
                    pass
            overall_confidence = sum(scores) / len(scores) if scores else 0.0
            requires_review = overall_confidence < self.confidence_threshold

            logger.info(
                "Successfully processed %s: %d records, confidence: %.0f%%",
                pdf_path.name, records_extracted, overall_confidence * 100,
            )

            return ProcessingResult(
                source_file=str(pdf_path),
                output_file=str(output_path),
                success=True,
                records_extracted=records_extracted,
                overall_confidence=overall_confidence,
                requires_review=requires_review,
                validation_issues=0,
            )

        except Exception as exc:
            logger.error("Error processing %s: %s", pdf_path.name, exc, exc_info=True)
            return ProcessingResult(
                source_file=str(pdf_path), output_file=None,
                success=False, records_extracted=0, overall_confidence=0.0,
                requires_review=True, error_message=str(exc),
            )

    # ------------------------------------------------------------------
    def process_directory(
        self,
        input_dir: str,
        file_pattern: str = "*.pdf",
    ) -> List[ProcessingResult]:
        """Process all PDF files in a directory."""
        input_path = Path(input_dir)
        pdf_files  = list(input_path.glob(file_pattern))
        logger.info("Found %d PDF files in %s", len(pdf_files), input_dir)

        results = [self.process_document(str(f)) for f in pdf_files]

        successful    = sum(1 for r in results if r.success)
        total_records = sum(r.records_extracted for r in results)
        logger.info(
            "Processing complete: %d/%d successful, %d total records",
            successful, len(results), total_records,
        )
        return results

    # ------------------------------------------------------------------
    def orchestrate(self, documents: List[str]) -> List[ProcessingResult]:
        """Process multiple documents."""
        return [self.process_document(doc) for doc in documents]
