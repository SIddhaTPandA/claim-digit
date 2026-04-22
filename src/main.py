#!/usr/bin/env python
"""
SPD Benefits Extraction - Main Entry Point

Extracts healthcare benefits data from SPD/SBC PDF documents
and generates standardized Excel output files.

Usage:
    python -m src.main --input Plans --output OutputExcel
    python -m src.main --file "Plans/document.pdf"
    python -m src.main --input Plans --output OutputExcel --verbose
"""

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import List, Optional

# Silence noisy third-party loggers BEFORE any other imports
logging.getLogger("pdfminer").setLevel(logging.ERROR)
logging.getLogger("pdfplumber").setLevel(logging.ERROR)

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


# Load .env BEFORE importing agents/processors that read env vars
from dotenv import load_dotenv
load_dotenv(project_root / '.env')

from src.agents.orchestrator import Orchestrator, ProcessingResult
from src.utils.logging_config import setup_logging


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="SPD Benefits Extraction - Extract healthcare benefits from PDF documents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Process all PDFs in a directory:
    python -m src.main --input Plans --output OutputExcel

  Process a single file:
    python -m src.main --file "Plans/M000-999 HDHP 1500 (Q)_HDHP_SPD.pdf"

  Process with verbose logging:
    python -m src.main --input Plans --output OutputExcel --verbose
        """,
    )
    
    parser.add_argument(
        "--input", "-i",
        type=str,
        default="Plans",
        help="Input directory containing PDF files (default: Plans)",
    )
    
    parser.add_argument(
        "--output", "-o",
        type=str,
        default="OutputExcel",
        help="Output directory for Excel files (default: OutputExcel)",
    )
    
    parser.add_argument(
        "--file", "-f",
        type=str,
        help="Process a single PDF file",
    )
    
    parser.add_argument(
        "--confidence-threshold",
        type=float,
        default=0.70,
        help="Minimum confidence score (0.0-1.0) for auto-approval (default: 0.70)",
    )
    
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Skip validation checks",
    )
    
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress output except errors",
    )
    
    return parser.parse_args()


def print_results_summary(results: List[ProcessingResult]) -> None:
    """Print a summary of processing results."""
    if not results:
        print("\nNo documents processed.")
        return
    
    successful = [r for r in results if r.success]
    failed = [r for r in results if not r.success]
    
    print("\n" + "=" * 60)
    print("PROCESSING SUMMARY")
    print("=" * 60)
    print(f"Total documents processed: {len(results)}")
    print(f"Successful: {len(successful)}")
    print(f"Failed: {len(failed)}")
    print(f"Total records extracted: {sum(r.records_extracted for r in results)}")
    
    if successful:
        avg_confidence = sum(r.overall_confidence for r in successful) / len(successful)
        print(f"Average confidence: {avg_confidence:.1%}")
        needs_review = sum(1 for r in successful if r.requires_review)
        print(f"Requires human review: {needs_review}")
    
    if successful:
        print("\n" + "-" * 60)
        print("SUCCESSFUL EXTRACTIONS:")
        for r in successful:
            review_flag = " [NEEDS REVIEW]" if r.requires_review else ""
            print(
                f"  {Path(r.source_file).name} -> {Path(r.output_file).name} "
                f"({r.records_extracted} records, {r.overall_confidence:.0%}){review_flag}"
            )
    
    if failed:
        print("\n" + "-" * 60)
        print("FAILED:")
        for r in failed:
            print(f"  {Path(r.source_file).name}: {r.error_message}")
    
    print("=" * 60 + "\n")


def main() -> int:
    """Main entry point."""
    args = parse_args()
    
    # Configure logging
    if args.quiet:
        log_level = logging.ERROR
    elif args.verbose:
        log_level = logging.DEBUG
    else:
        log_level = logging.INFO
    
    setup_logging(level=log_level)
    logger = logging.getLogger(__name__)
    
    # Resolve paths
    if args.file:
        # Single file mode
        pdf_path = Path(args.file)
        if not pdf_path.is_absolute():
            pdf_path = project_root / pdf_path
        
        if not pdf_path.exists():
            logger.error(f"File not found: {pdf_path}")
            return 1
        
        output_dir = Path(args.output)
        if not output_dir.is_absolute():
            output_dir = project_root / output_dir
        
        # Initialize orchestrator
        orchestrator = Orchestrator(
            output_dir=str(output_dir),
            confidence_threshold=args.confidence_threshold,
            enable_validation=not args.skip_validation,
        )
        
        print(f"Processing: {pdf_path.name}")
        result = orchestrator.process_document(str(pdf_path))
        results = [result]
        
    else:
        # Directory mode
        input_dir = Path(args.input)
        if not input_dir.is_absolute():
            input_dir = project_root / input_dir
        
        output_dir = Path(args.output)
        if not output_dir.is_absolute():
            output_dir = project_root / output_dir
        
        if not input_dir.exists():
            logger.error(f"Input directory not found: {input_dir}")
            return 1
        
        # Find PDF files
        pdf_files = list(input_dir.glob("*.pdf"))
        if not pdf_files:
            logger.warning(f"No PDF files found in {input_dir}")
            return 0
        
        print(f"Found {len(pdf_files)} PDF files in {input_dir}")
        print(f"Output directory: {output_dir}")
        print("-" * 40)
        
        # Initialize orchestrator
        orchestrator = Orchestrator(
            output_dir=str(output_dir),
            confidence_threshold=args.confidence_threshold,
            enable_validation=not args.skip_validation,
        )
        
        # Process all files
        results = orchestrator.process_directory(str(input_dir))
    
    # Print summary
    if not args.quiet:
        print_results_summary(results)
    
    # Return exit code based on results
    failed_count = sum(1 for r in results if not r.success)
    return 1 if failed_count == len(results) else 0


if __name__ == "__main__":
    sys.exit(main())