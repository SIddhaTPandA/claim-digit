"""
PDF Processor for extracting content from SPD/SBC documents.

Supports multiple extraction strategies (in priority order):
1. Azure Document Intelligence (RECOMMENDED) - accurate table and layout extraction
2. pdfplumber (fallback) - good local table detection
3. pypdf (basic fallback) - simple text extraction

Azure Document Intelligence is STRONGLY RECOMMENDED for production use
as it provides:
- Accurate table structure with cell boundaries
- Proper handling of multi-column layouts
- OCR for scanned documents
- Confidence scores for extracted content
"""

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ExtractionResult:
    """Result of PDF content extraction."""
    text: str = ""
    tables: List[Dict[str, Any]] = field(default_factory=list)
    pages: List[Dict[str, Any]] = field(default_factory=list)
    page_count: int = 0
    extraction_method: str = "unknown"
    confidence: float = 0.0
    warnings: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for backward compatibility."""
        return {
            "text": self.text,
            "tables": self.tables,
            "pages": self.pages,
            "page_count": self.page_count,
            "extraction_method": self.extraction_method,
            "confidence": self.confidence,
            "warnings": self.warnings,
        }


class PDFProcessor:
    """
    Extracts text and table content from PDF documents.
    
    RECOMMENDED: Configure Azure Document Intelligence for production.
    The processor automatically selects the best available extraction method.
    
    Priority order:
    1. Azure Document Intelligence (if configured)
    2. pdfplumber (if installed)
    3. pypdf (always available)
    """

    def __init__(
        self,
        use_azure: bool = True,  # Changed default to True
        endpoint: Optional[str] = None,
        key: Optional[str] = None,
        prefer_tables: bool = True,
    ):
        """
        Initialize the PDF processor.
        
        Args:
            use_azure: Whether to attempt Azure Document Intelligence (default: True)
            endpoint: Azure Document Intelligence endpoint (or from env)
            key: Azure Document Intelligence API key (or from env)
            prefer_tables: Prioritize table extraction over raw text
        """
        self.endpoint = endpoint or os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
        self.key = key or os.getenv("AZURE_DOCUMENT_INTELLIGENCE_KEY")
        self.prefer_tables = prefer_tables
        
        # Determine if Azure is available
        self.azure_available = False
        self._azure_client = None
        
        if use_azure and self.endpoint and self.key:
            self.azure_available = self._init_azure_client()
        
        # Check for pdfplumber
        self.pdfplumber_available = self._check_pdfplumber()
        
        # Log available methods
        methods = []
        if self.azure_available:
            methods.append("Azure Document Intelligence (PRIMARY)")
        if self.pdfplumber_available:
            methods.append("pdfplumber")
        methods.append("pypdf")
        
        logger.info(f"PDF extraction methods available: {', '.join(methods)}")

    def _init_azure_client(self) -> bool:
        """Initialize Azure Document Intelligence client."""
        try:
            from azure.ai.documentintelligence import DocumentIntelligenceClient
            from azure.core.credentials import AzureKeyCredential
            
            # Check if SSL verification should be disabled (for corporate proxies)
            verify_ssl = os.getenv("AZURE_SSL_VERIFY", "true").lower() != "false"
            
            self._azure_client = DocumentIntelligenceClient(
                endpoint=self.endpoint,
                credential=AzureKeyCredential(self.key),
                connection_verify=verify_ssl,
            )
            if not verify_ssl:
                logger.warning("SSL verification disabled for Azure Document Intelligence")
            logger.info("Azure Document Intelligence client initialized successfully")
            return True
        except ImportError:
            logger.warning(
                "azure-ai-documentintelligence not installed. "
                "Install with: pip install azure-ai-documentintelligence"
            )
            return False
        except Exception as e:
            logger.error(f"Failed to initialize Azure client: {e}")
            return False

    def _check_pdfplumber(self) -> bool:
        """Check if pdfplumber is available."""
        try:
            import pdfplumber
            return True
        except ImportError:
            return False

    def extract_content(self, pdf_path: str) -> Dict[str, Any]:
        """
        Extract text and tables from a PDF document.
        
        Uses the best available extraction method automatically.
        
        Args:
            pdf_path: Path to the PDF file
            
        Returns:
            Dictionary containing:
            - text: Full extracted text
            - tables: List of extracted tables with cell data
            - pages: List of page-specific content
            - page_count: Number of pages
            - extraction_method: Method used
            - confidence: Extraction confidence (0-1)
        """
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF not found: {pdf_path}")
        
        result: Optional[ExtractionResult] = None
        
        # Try Azure Document Intelligence first (RECOMMENDED)
        logger.info(f"Azure available: {self.azure_available}, Azure client: {self._azure_client} =================S======")
        if self.azure_available and self._azure_client:
            try:
                result = self._extract_with_azure(pdf_path)
                if result and result.tables:
                    logger.info(
                        f"Azure DI extracted {len(result.tables)} tables, "
                        f"{result.page_count} pages"
                        "------------------AZURE----------------"
                    )
                    return result.to_dict()
            except Exception as e:
                logger.warning(f"Azure extraction failed: {e}. Trying fallback...")
        
        # Try pdfplumber (good for tables)
        if self.pdfplumber_available:
            try:
                result = self._extract_with_pdfplumber(pdf_path)
                if result and (result.tables or result.text):
                    logger.info(
                        f"pdfplumber extracted {len(result.tables)} tables, "
                        f"{result.page_count} pages"
                    )
                    return result.to_dict()
            except Exception as e:
                logger.warning(f"pdfplumber extraction failed: {e}. Trying pypdf...")
        
        # Fall back to pypdf
        result = self._extract_with_pypdf(pdf_path)
        logger.info(f"pypdf extracted {result.page_count} pages (text only)")
        return result.to_dict()

    def _extract_with_azure(self, pdf_path: str) -> ExtractionResult:
        """Extract content using Azure Document Intelligence."""
        logger.info(f"Extracting with Azure Document Intelligence: {pdf_path}")
        
        result = ExtractionResult(extraction_method="azure_document_intelligence")
        
        with open(pdf_path, "rb") as f:
            poller = self._azure_client.begin_analyze_document(
                "prebuilt-layout",
                body=f,
                content_type="application/pdf",
            )
        
        analysis_result = poller.result()
        
        # Extract full text
        result.text = analysis_result.content if hasattr(analysis_result, "content") else ""
        
        # Extract tables with full cell information
        if hasattr(analysis_result, "tables") and analysis_result.tables:
            for table_idx, table in enumerate(analysis_result.tables):
                table_data = self._parse_azure_table(table, table_idx)
                result.tables.append(table_data)
        
        # Extract page content
        if hasattr(analysis_result, "pages"):
            for page in analysis_result.pages:
                page_text = ""
                if hasattr(page, "lines"):
                    page_text = "\n".join(line.content for line in page.lines)
                result.pages.append({
                    "page_number": page.page_number,
                    "text": page_text,
                    "width": getattr(page, "width", 0),
                    "height": getattr(page, "height", 0),
                })
            result.page_count = len(result.pages)
        
        # Calculate confidence
        if result.tables:
            result.confidence = 0.95  # High confidence with Azure + tables
        elif result.text:
            result.confidence = 0.80
        
        return result

    def _parse_azure_table(self, table, table_idx: int) -> Dict[str, Any]:
        """
        Parse an Azure Document Intelligence table into structured format.
        
        FIXED: Properly handles merged cells by propagating content across all spanned columns.
        For example, if a cell with "80%" spans both In-Network and Out-of-Network columns,
        the value will now appear in both columns instead of just the first one.
        """
        cells = []
        rows_dict: Dict[int, Dict[int, str]] = {}
        
        row_count = getattr(table, "row_count", 0)
        column_count = getattr(table, "column_count", 0)
        
        # Track which cells have been filled by merged cells
        merged_cell_map: Dict[Tuple[int, int], str] = {}
        
        for cell in table.cells:
            row_idx = cell.row_index
            col_idx = cell.column_index
            content = cell.content.strip() if cell.content else ""
            row_span = getattr(cell, "row_span", 1)
            col_span = getattr(cell, "column_span", 1)
            
            # Store cell metadata
            cells.append({
                "row_index": row_idx,
                "column_index": col_idx,
                "content": content,
                "row_span": row_span,
                "column_span": col_span,
                "kind": getattr(cell, "kind", "content"),  # "columnHeader", "rowHeader", "content"
            })
            
            # Build row-based structure with merged cell propagation
            if row_idx not in rows_dict:
                rows_dict[row_idx] = {}
            
            # FIXED: Propagate merged cell content across all spanned columns and rows
            for r_offset in range(row_span):
                for c_offset in range(col_span):
                    target_row = row_idx + r_offset
                    target_col = col_idx + c_offset
                    
                    # Only propagate within table bounds
                    if target_row < row_count and target_col < column_count:
                        if target_row not in rows_dict:
                            rows_dict[target_row] = {}
                        
                        # Fill the cell with content (propagate merged value)
                        rows_dict[target_row][target_col] = content
                        merged_cell_map[(target_row, target_col)] = content
                        
                        # Log merged cell propagation for debugging
                        if col_span > 1 or row_span > 1:
                            if r_offset == 0 and c_offset == 0:
                                logger.debug(
                                    f"Table {table_idx}: Merged cell at ({row_idx},{col_idx}) "
                                    f"with span ({row_span}x{col_span}) - propagating '{content}'"
                                )
        
        # Convert to list of lists
        rows = []
        for row_idx in range(row_count):
            row = []
            for col_idx in range(column_count):
                row.append(rows_dict.get(row_idx, {}).get(col_idx, ""))
            rows.append(row)
        
        # Detect header row
        header_row_idx = -1
        for cell in cells:
            if cell.get("kind") == "columnHeader":
                header_row_idx = cell["row_index"]
                break
        
        return {
            "table_index": table_idx,
            "rows": rows,
            "cells": cells,
            "row_count": row_count,
            "column_count": column_count,
            "header_row_index": header_row_idx,
            "merged_cells_count": len([c for c in cells if c.get("column_span", 1) > 1 or c.get("row_span", 1) > 1]),
        }

    def _extract_with_pdfplumber(self, pdf_path: str) -> ExtractionResult:
        """Extract content using pdfplumber (good for table detection)."""
        import pdfplumber
        
        logger.info(f"Extracting with pdfplumber: {pdf_path}")
        result = ExtractionResult(extraction_method="pdfplumber")
        
        with pdfplumber.open(pdf_path) as pdf:
            result.page_count = len(pdf.pages)
            
            all_text = []
            table_idx = 0
            
            for page_num, page in enumerate(pdf.pages, 1):
                # Extract text
                page_text = page.extract_text() or ""
                all_text.append(page_text)
                
                result.pages.append({
                    "page_number": page_num,
                    "text": page_text,
                    "width": page.width,
                    "height": page.height,
                })
                
                # Extract tables
                tables = page.extract_tables() or []
                
                for table in tables:
                    if table and len(table) >= 2:  # At least header + 1 row
                        # Convert to our format
                        rows = []
                        cells = []
                        
                        for row_idx, row in enumerate(table):
                            row_data = []
                            for col_idx, cell in enumerate(row):
                                cell_text = str(cell).strip() if cell else ""
                                row_data.append(cell_text)
                                cells.append({
                                    "row_index": row_idx,
                                    "column_index": col_idx,
                                    "content": cell_text,
                                })
                            rows.append(row_data)
                        
                        result.tables.append({
                            "table_index": table_idx,
                            "rows": rows,
                            "cells": cells,
                            "row_count": len(rows),
                            "column_count": max(len(r) for r in rows) if rows else 0,
                            "page_number": page_num,
                        })
                        table_idx += 1
            
            result.text = "\n".join(all_text)
        
        # Set confidence based on results
        if result.tables:
            result.confidence = 0.85
        elif result.text:
            result.confidence = 0.70
        
        return result

    def _extract_with_pypdf(self, pdf_path: str) -> ExtractionResult:
        """Extract content using pypdf library (basic fallback)."""
        logger.info(f"Extracting with pypdf: {pdf_path}")
        
        result = ExtractionResult(extraction_method="pypdf")
        
        try:
            from pypdf import PdfReader
        except ImportError:
            logger.warning("pypdf not installed. Trying PyPDF2...")
            try:
                from PyPDF2 import PdfReader
            except ImportError:
                raise ImportError(
                    "Neither pypdf nor PyPDF2 is installed. "
                    "Install with: pip install pypdf"
                )
        
        reader = PdfReader(pdf_path)
        result.page_count = len(reader.pages)
        
        all_text = []
        
        for page_num, page in enumerate(reader.pages, 1):
            page_text = page.extract_text() or ""
            all_text.append(page_text)
            result.pages.append({
                "page_number": page_num,
                "text": page_text,
            })
        
        result.text = "\n".join(all_text)
        
        # Try to extract tables from text (basic heuristic)
        result.tables = self._extract_tables_from_text(result.text)
        
        # Lower confidence for pypdf text-based extraction
        if result.tables:
            result.confidence = 0.60  # Low confidence for heuristic tables
            result.warnings.append("Tables extracted using text heuristics - verify accuracy")
        elif result.text:
            result.confidence = 0.50
            result.warnings.append("No tables detected - using text-only extraction")
        
        return result

    def _extract_tables_from_text(self, text: str) -> List[Dict[str, Any]]:
        """
        Attempt to extract table-like structures from plain text.
        
        This is a heuristic approach that looks for aligned columns
        and repeated patterns typical of benefit tables.
        """
        tables = []
        lines = text.split("\n")
        
        # Look for benefit table patterns
        current_table = []
        in_table = False
        
        # Common benefit table header patterns
        header_patterns = [
            r"(in-?network|out-?of-?network|in network|out of network)",
            r"(you pay|plan pays|coinsurance|copay|deductible)",
            r"(service|benefit|coverage|what you pay)",
        ]
        
        for line in lines:
            line = line.strip()
            if not line:
                if current_table and len(current_table) >= 2:
                    tables.append({
                        "rows": current_table,
                        "row_count": len(current_table),
                        "column_count": max(len(row) for row in current_table) if current_table else 0,
                    })
                    current_table = []
                    in_table = False
                continue
            
            # Check if this looks like a header row
            is_header = any(
                re.search(pattern, line, re.IGNORECASE)
                for pattern in header_patterns
            )
            
            if is_header:
                in_table = True
                current_table = []
            
            if in_table:
                # Split by multiple spaces or tabs
                cells = re.split(r"\s{2,}|\t", line)
                cells = [c.strip() for c in cells if c.strip()]
                if cells:
                    current_table.append(cells)
        
        # Add final table if exists
        if current_table and len(current_table) >= 2:
            tables.append({
                "rows": current_table,
                "row_count": len(current_table),
                "column_count": max(len(row) for row in current_table) if current_table else 0,
            })
        
        return tables

    def extract_text(self, pdf_path: str) -> str:
        """Extract only text from PDF."""
        content = self.extract_content(pdf_path)
        return content.get("text", "")

    def extract_tables(self, pdf_path: str) -> List[Dict[str, Any]]:
        """Extract only tables from PDF."""
        content = self.extract_content(pdf_path)
        return content.get("tables", [])

    def read_pdf(self, pdf_path: str = None) -> Dict[str, Any]:
        """
        Read PDF and return content (legacy method for compatibility).
        
        Args:
            pdf_path: Path to PDF file (uses instance file_path if not provided)
        """
        path = pdf_path or getattr(self, "file_path", None)
        if not path:
            raise ValueError("No PDF path provided")
        return self.extract_content(path)