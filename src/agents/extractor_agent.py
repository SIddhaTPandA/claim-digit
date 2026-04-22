"""
Extractor Agent for SPD Benefits Extraction.

Extracts benefit service rows from document tables and text,
capturing raw In-Network/Out-of-Network values and limits.

Design Principles:
- Schema-bound extraction: Column roles detected before extraction
- Azure DI first: Prioritizes accurate table structure from Azure
- Header filtering: Removes repeated headers across pages
- Multi-strategy: Falls back gracefully through extraction methods
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from src.config.extraction_config import (
    DEFAULT_CONFIG,
    ExtractionConfig,
)
from src.models.benefit_record import (
    RawExtractionRecord,
    DocumentClassification,
)
from src.parsers.benefit_text_parser import BenefitTextParser, ParsedBenefit
from src.parsers.table_extractor import StructuredTableExtractor, ExtractedRow
from src.parsers.record_builder import SchemaRecordBuilder, RecordDeduplicator

logger = logging.getLogger(__name__)


class ExtractorAgent:
    """
    Agent responsible for extracting raw benefit data from SPD/SBC documents.
    
    Uses schema-bound extraction with the StructuredTableExtractor for
    reliable column mapping and header filtering.
    
    Extraction Priority:
    1. Structured table extraction (Azure DI / pdfplumber tables)
    2. Text-based parsing (fallback for documents without clear tables)
    """

    def __init__(
        self,
        config: Optional[ExtractionConfig] = None,
        document_intelligence_client: Optional[Any] = None,
        use_schema_extraction: bool = True,
    ):
        """
        Initialize the ExtractorAgent.
        
        Args:
            config: Extraction configuration (uses DEFAULT_CONFIG if not provided)
            document_intelligence_client: Optional Azure Document Intelligence client
            use_schema_extraction: Whether to use the new schema-bound extraction
        """
        self.config = config or DEFAULT_CONFIG
        self.patterns = self.config.compile_patterns()
        self.di_client = document_intelligence_client
        self.use_schema_extraction = use_schema_extraction
        
        # Initialize extractors
        self.table_extractor = StructuredTableExtractor(
            min_columns=self.config.min_cells_benefit_row,
            min_rows=self.config.min_table_rows,
        )
        self.record_builder = SchemaRecordBuilder()
        self.deduplicator = RecordDeduplicator()
        self.text_parser = BenefitTextParser()
        
        logger.info(f"ExtractorAgent initialized (schema_extraction={use_schema_extraction})")

    def extract_from_content(
        self,
        document_content: Any,
        tables: Optional[List[Dict[str, Any]]] = None,
        classification: Optional[DocumentClassification] = None,
    ) -> List[RawExtractionRecord]:
        """
        Extract raw benefit records from document content.
        
        This is the main entry point for extraction. It uses a multi-strategy
        approach to ensure maximum coverage:
        
        1. If tables available: Use schema-bound table extraction
        2. If text available: Use text parsing as supplement/fallback
        3. Deduplicate and merge results
        
        Args:
            document_content: Text content or dict with 'text' and 'tables' keys
            tables: Optional list of extracted table data
            classification: Optional document classification for context
            
        Returns:
            List of RawExtractionRecord objects
        """
        records: List[RawExtractionRecord] = []
        
        # Handle dict input (from PDF processor)
        text_content = ""
        extraction_method = "unknown"
        
        if isinstance(document_content, dict):
            text_content = document_content.get('text', '')
            if tables is None:
                tables = document_content.get('tables', [])
            extraction_method = document_content.get('extraction_method', 'unknown')
        elif isinstance(document_content, str):
            text_content = document_content
        
        # Reset table extractor state for new document
        self.table_extractor.reset()
        
        # STRATEGY 1: Schema-bound table extraction (PREFERRED)
        if tables and self.use_schema_extraction:
            logger.info(f"Extracting from {len(tables)} tables using schema-bound method...")
            
            try:
                # Extract rows from tables using StructuredTableExtractor
                extracted_rows = self.table_extractor.extract_from_tables(tables)
                
                if extracted_rows:
                    # Build records using SchemaRecordBuilder
                    table_records = self.record_builder.build_from_extracted_rows(
                        extracted_rows,
                        document_type=classification.document_type.value if classification else "SPD"
                    )
                    records.extend(table_records)
                    logger.info(f"Schema extraction: {len(table_records)} records from tables")
            except Exception as e:
                logger.warning(f"Schema extraction failed: {e}. Falling back to legacy method.")
                # Fallback to legacy table extraction
                if tables:
                    legacy_records = self._extract_from_tables(tables, classification)
                    records.extend(legacy_records)
        
        # STRATEGY 2: Legacy table extraction (if schema extraction disabled/failed)
        elif tables and not self.use_schema_extraction:
            table_records = self._extract_from_tables(tables, classification)
            records.extend(table_records)
            logger.info(f"Legacy extraction: {len(table_records)} records from tables")
        
        # STRATEGY 3: Text-based extraction (supplement or fallback)
        # Use text parsing if:
        # - Few records from tables (< 10 for SPD documents, < 5 for others)
        # - No tables available
        # - Text-only extraction method was used
        # - Document type is SPD (these often have more narrative than tabular content)
        is_spd_document = classification and classification.document_type.value.upper() == "SPD"
        text_threshold = 10 if is_spd_document else 5
        
        should_use_text = (
            len(records) < text_threshold or
            not tables or
            extraction_method == "pypdf" or
            is_spd_document  # Always try text extraction for SPDs
        )
        
        if text_content and should_use_text:
            logger.info(f"Supplementing with text-based extraction (SPD={is_spd_document})...")
            text_records = self._extract_from_text_with_parser(text_content)
            
            # Add text records that don't duplicate table records
            existing_services = {
                self._normalize_service_name(r.service_name)
                for r in records
                if r.service_name
            }
            
            new_from_text = 0
            skipped_invalid = 0
            for tr in text_records:
                if tr.service_name:
                    # Skip invalid service names (explanatory text, headers, etc.)
                    if not self._is_valid_service_name(tr.service_name):
                        skipped_invalid += 1
                        continue
                    
                    normalized = self._normalize_service_name(tr.service_name)
                    if normalized not in existing_services:
                        records.append(tr)
                        existing_services.add(normalized)
                        new_from_text += 1
            
            if skipped_invalid > 0:
                logger.debug(f"Skipped {skipped_invalid} invalid text extraction records")
            if new_from_text > 0:
                logger.info(f"Text extraction added {new_from_text} new records")
        
        # Deduplicate final results
        records = self.deduplicator.deduplicate(records)
        
        logger.info(f"Total extraction: {len(records)} unique records")
        return records

    def _normalize_service_name(self, name: str) -> str:
        """Normalize service name for deduplication comparison."""
        if not name:
            return ""
        # Lowercase, remove punctuation, normalize whitespace
        normalized = name.lower().strip()
        normalized = re.sub(r'[^\w\s]', '', normalized)
        normalized = re.sub(r'\s+', ' ', normalized)
        return normalized[:30]  # First 30 chars for matching

    def _is_valid_service_name(self, service_name: str) -> bool:
        """
        Check if service name is valid (not explanatory text or header).
        
        Returns False if the text appears to be explanatory paragraphs,
        column headers, or other non-service content.
        """
        if not service_name or len(service_name.strip()) < 3:
            return False
        
        name_lower = service_name.lower().strip()
        
        # Skip header-like terms
        header_terms = {
            "in-network", "out-of-network", "in network", "out of network",
            "covered services", "what you pay", "service", "benefit",
            "description", "your cost", "plan pays", "you pay",
            "network provider", "non-network provider",
        }
        if name_lower in header_terms:
            return False
        
        # Skip if it starts with explanatory text patterns
        explanatory_starters = [
            "see the ", "see also ", "refer to ", "please ", "note:", "note that",
            "if you ", "when you ", "once you ", "after you ", "before you ",
            "you may ", "you will ", "you can ", "you must ", "you should ",
            "the plan ", "this plan ", "your plan ", "the mit ", "mit's ",
            "benefits are ", "benefits will ", "coverage is ", "coverage will ",
            "includes ", "including ", "excludes ", "excluding ",
            "for more ", "for additional ", "for further ", "for details ",
            "failure to ", "in the event ", "in accordance ",
            "deductibles and ", "maximums are ", "limits are ",
            "charges for ", "charges made ",
            "individual will ", "individual and ", 
            "member pays ", "member must ", "members must ",
            "family members ", "family out-of-pocket ",
            "pursuant to ", "according to ", "based on ",
            "participants are ", "participants must ",
            "determined based ", "subject to ",
            "procedures are ", "services are ", "treatments are ",
            "enrolled in ", "must be enrolled ",
            "(booklet)", "(spd)", "booklet will ",
            "following ", "the following ",
            "complications of ", "reversal of ", "termination of ",
            "for the most ", "practices. ",
        ]
        
        for starter in explanatory_starters:
            if name_lower.startswith(starter):
                return False
        
        # Skip if it contains sentence fragments that indicate explanatory text
        explanatory_patterns = [
            r"\bwill be\s+(based|determined|considered|provided|covered)\b",
            r"\bwill result in\b",
            r"\bis required\b$",
            r"\bare covered\b$",
            r"\bare not\b",
            r"\bhas been met\b",
            r"\bprior to\b",
            r"^\d+\s*day\s+supply$",  # e.g., "30 day supply"
        ]
        
        for pattern in explanatory_patterns:
            if re.search(pattern, name_lower):
                return False
        
        # Skip generic words that are not services
        generic_terms = {
            "individual", "family", "generic", "preferred brand", "non-preferred brand",
            "incentive", "deductible", "30 day supply", "retail pharmacy", "mandatory",
            "maximum family", "out-of-pocket individual",
        }
        if name_lower in generic_terms:
            return False
        
        # Skip if too many words (likely a sentence, not a service)
        words = name_lower.split()
        if len(words) > 10:
            return False
        
        # Skip if it ends with incomplete sentence patterns
        incomplete_endings = [" to", " of", " for", " in", " the", " a", " an", " and", " or", " is", " are", " will"]
        for ending in incomplete_endings:
            if name_lower.endswith(ending) and len(name_lower) > 25:
                return False
        
        return True

    def _find_column_index(
        self,
        headers: List[str],
        patterns: List[str],
    ) -> Optional[int]:
        """
        Find the index of a column matching any of the given patterns.
        
        Args:
            headers: List of column header strings
            patterns: List of patterns to match (case-insensitive)
            
        Returns:
            Column index if found, None otherwise
        """
        for idx, header in enumerate(headers):
            header_lower = header.lower().strip()
            for pattern in patterns:
                if pattern.lower() in header_lower:
                    return idx
        return None

    def _extract_from_tables(
        self,
        tables: List[Dict[str, Any]],
        classification: Optional[DocumentClassification] = None,
    ) -> List[RawExtractionRecord]:
        """
        Extract benefit records from table data using adaptive column detection.
        """
        all_records = []
        
        for table_idx, table in enumerate(tables):
            rows = table.get("rows", [])
            
            # Skip tables that are too small
            if len(rows) < self.config.min_table_rows:
                continue
            
            # Analyze table structure to detect column roles
            column_roles = self._detect_column_roles(rows)
            
            if not column_roles.get('has_benefit_data'):
                continue
            
            # Extract records from this table
            table_records = self._extract_from_benefit_table(
                rows=rows,
                column_roles=column_roles,
                table_idx=table_idx,
            )
            all_records.extend(table_records)
        
        return all_records

    def _detect_column_roles(self, rows: List[List[str]]) -> Dict[str, Any]:
        """
        Analyze table content to detect column roles.
        
        Returns dict with:
            - service_col: Index of service/benefit name column
            - in_network_col: Index of in-network value column
            - out_network_col: Index of out-of-network value column
            - has_benefit_data: Whether this appears to be a benefit table
        """
        roles = {
            'service_col': None,
            'in_network_col': None,
            'out_network_col': None,
            'has_benefit_data': False,
        }
        
        if not rows:
            return roles
        
        # Analyze column content across all rows
        max_cols = max(len(row) for row in rows) if rows else 0
        
        if max_cols < self.config.min_cells_benefit_row:
            return roles
        
        # Score each column for different roles
        col_scores = {
            'benefit_value': [0] * max_cols,
            'service_name': [0] * max_cols,
        }
        
        for row in rows:
            for col_idx, cell in enumerate(row):
                if not cell or not isinstance(cell, str):
                    continue
                
                cell_text = str(cell).strip()
                
                # Check for benefit value patterns (%, $, "after deductible", etc.)
                if self._contains_benefit_value(cell_text):
                    col_scores['benefit_value'][col_idx] += 1
                
                # Check for service name characteristics
                if self._looks_like_service_name(cell_text):
                    col_scores['service_name'][col_idx] += 1
        
        # Determine column roles based on scores
        total_rows = len(rows)
        min_threshold = max(1, int(total_rows * 0.1))  # At least 10% of rows
        
        # Find service column (usually first column with text, not values)
        for col_idx in range(max_cols):
            if (col_scores['service_name'][col_idx] >= min_threshold and
                col_scores['benefit_value'][col_idx] < col_scores['service_name'][col_idx]):
                roles['service_col'] = col_idx
                break
        
        # Find value columns (columns with benefit values)
        value_cols = [
            (col_idx, score) 
            for col_idx, score in enumerate(col_scores['benefit_value'])
            if score >= min_threshold and col_idx != roles.get('service_col')
        ]
        value_cols.sort(key=lambda x: x[1], reverse=True)
        
        if len(value_cols) >= 1:
            # First value column is in-network, second is out-of-network
            roles['in_network_col'] = value_cols[0][0]
            roles['has_benefit_data'] = True
            
            if len(value_cols) >= 2:
                roles['out_network_col'] = value_cols[1][0]
        
        logger.debug(f"Detected column roles: {roles}")
        return roles

    def _contains_benefit_value(self, text: str) -> bool:
        """Check if text contains a benefit value pattern."""
        # Check for percentage
        if re.search(r'\d{1,3}\s*%', text):
            return True
        
        # Check for dollar amount
        if re.search(r'\$\s*[\d,]+', text):
            return True
        
        # Check for common benefit phrases
        if self._matches_any_pattern(text, self.patterns.not_covered):
            return True
        if self._matches_any_pattern(text, self.patterns.covered_in_full):
            return True
        
        return False

    def _looks_like_service_name(self, text: str) -> bool:
        """Check if text looks like a service/benefit name."""
        # Must be primarily alphabetic
        alpha_chars = sum(1 for c in text if c.isalpha())
        if len(text) < 3 or alpha_chars < len(text) * 0.5:
            return False
        
        # Should not be primarily a benefit value
        if self._contains_benefit_value(text):
            # Could be service name with embedded value, check if more text than value
            value_match = re.search(r'\d+%|\$[\d,]+', text)
            if value_match:
                value_len = len(value_match.group())
                if value_len > len(text) * 0.5:
                    return False
        
        return True

    def _matches_any_pattern(self, text: str, patterns: List[re.Pattern]) -> bool:
        """Check if text matches any of the compiled patterns."""
        for pattern in patterns:
            if pattern.search(text):
                return True
        return False

    def _extract_from_benefit_table(
        self,
        rows: List[List[str]],
        column_roles: Dict[str, Any],
        table_idx: int,
    ) -> List[RawExtractionRecord]:
        """Extract records from a table identified as containing benefit data."""
        records = []
        current_category = "General Services"
        
        service_col = column_roles.get('service_col', 0)
        in_network_col = column_roles.get('in_network_col')
        out_network_col = column_roles.get('out_network_col')
        
        for row_idx, row in enumerate(rows):
            if not row or len(row) < self.config.min_cells_benefit_row:
                continue
            
            # Check if this is a category header row
            if self._is_category_row(row, column_roles):
                new_category = self._extract_category_name(row)
                if new_category:
                    current_category = new_category
                continue
            
            # Extract service name
            service_name = None
            if service_col is not None and service_col < len(row):
                service_name = self._clean_text(row[service_col])
            
            if not service_name:
                # Try first cell
                if len(row) > 0:
                    service_name = self._clean_text(row[0])
            
            if not service_name:
                continue
            
            # Extract benefit values
            in_network_text = None
            out_network_text = None
            
            if in_network_col is not None and in_network_col < len(row):
                in_network_text = self._clean_text(row[in_network_col])
            
            if out_network_col is not None and out_network_col < len(row):
                out_network_text = self._clean_text(row[out_network_col])
            
            # If no dedicated columns found, try to extract from remaining cells
            if in_network_text is None and out_network_text is None:
                # Try cells after service name
                value_cells = []
                for col_idx, cell in enumerate(row):
                    if col_idx != service_col and cell:
                        cell_text = self._clean_text(cell)
                        if cell_text and self._contains_benefit_value(cell_text):
                            value_cells.append(cell_text)
                
                if len(value_cells) >= 1:
                    in_network_text = value_cells[0]
                if len(value_cells) >= 2:
                    out_network_text = value_cells[1]
            
            # Check for embedded values in service name
            if not in_network_text and not out_network_text:
                embedded = self._extract_embedded_values(service_name)
                if embedded:
                    in_network_text = embedded.get('in_network')
                    out_network_text = embedded.get('out_network')
                    if embedded.get('service'):
                        service_name = embedded.get('service')
            
            # Skip if no benefit values found
            if not in_network_text and not out_network_text:
                continue
            
            # Check for preauth indicators
            preauth_text = self._detect_preauth(row, service_name)
            
            # Calculate confidence based on data quality
            confidence = self._calculate_row_confidence(
                service_name, in_network_text, out_network_text
            )
            
            records.append(RawExtractionRecord(
                service_category=current_category,
                service_name=service_name,
                description_text=None,  # NEW: Add description_text field
                in_network_text=in_network_text,
                out_of_network_text=out_network_text,
                preauth_text=preauth_text,
                page_number=None,
                table_index=table_idx,
                row_index=row_idx,
                extraction_method="table",
                raw_confidence=confidence,
            ))
        
        return records

    def _is_category_row(self, row: List[str], column_roles: Dict[str, Any]) -> bool:
        """Check if a row is a category header."""
        if not row:
            return False
        
        # Category rows usually have content in first cell only
        non_empty = [cell for cell in row if cell and str(cell).strip()]
        if len(non_empty) != 1:
            return False
        
        # The single cell should look like a category name
        cell_text = str(non_empty[0]).strip()
        
        # Should be relatively short
        if len(cell_text) > 100:
            return False
        
        # Should not contain benefit values
        if self._contains_benefit_value(cell_text):
            return False
        
        # Check against category patterns
        for category, patterns in self.patterns.category_patterns.items():
            if self._matches_any_pattern(cell_text, patterns):
                return True
        
        return False

    def _extract_category_name(self, row: List[str]) -> Optional[str]:
        """Extract category name from a category row."""
        for cell in row:
            if cell and str(cell).strip():
                return self._clean_text(cell)
        return None

    def _clean_text(self, text: Any) -> Optional[str]:
        """Clean and normalize text."""
        if text is None:
            return None
        
        text = str(text).strip()
        
        # Remove excessive whitespace
        text = re.sub(r'\s+', ' ', text)
        
        # Remove common artifacts
        text = re.sub(r'^[\*\-•]+\s*', '', text)
        
        if not text:
            return None
        
        return text

    def _extract_embedded_values(self, text: str) -> Optional[Dict[str, str]]:
        """Extract benefit values embedded in service name text."""
        if not text:
            return None
            
        result = {}
        
        # Look for percentage patterns with optional "after deductible"
        pattern = r'(\d{1,3})\s*%\s*(?:after\s+(?:\w+\s+)?deductible)?'
        percentages = re.findall(pattern, text, re.IGNORECASE)
        
        if len(percentages) >= 2:
            result['in_network'] = f"{percentages[0]}% after Deductible"
            result['out_network'] = f"{percentages[1]}% after Deductible"
            # Remove values from service name
            clean_name = re.sub(pattern, '', text, flags=re.IGNORECASE)
            clean_name = re.sub(r'\s+', ' ', clean_name).strip()
            clean_name = re.sub(r'^[\s,;:/]+|[\s,;:/]+$', '', clean_name)
            if clean_name:
                result['service'] = clean_name
        elif len(percentages) == 1:
            result['in_network'] = f"{percentages[0]}% after Deductible"
        
        return result if result else None

    def _detect_preauth(self, row: List[str], service_name: str) -> Optional[str]:
        """Detect pre-authorization requirements."""
        full_text = ' '.join(str(cell) for cell in row if cell)
        full_text += ' ' + (service_name or '')
        
        if self._matches_any_pattern(full_text, self.patterns.preauth):
            return "Required"
        
        return None

    def _calculate_row_confidence(
        self,
        service_name: Optional[str],
        in_network: Optional[str],
        out_network: Optional[str],
    ) -> float:
        """Calculate confidence score for extracted row."""
        confidence = self.config.thresholds.table_extraction
        
        # Penalize missing service name
        if not service_name:
            confidence -= self.config.thresholds.missing_field_penalty * 2
        
        # Penalize missing values
        if not in_network:
            confidence -= self.config.thresholds.missing_field_penalty
        if not out_network:
            confidence -= self.config.thresholds.missing_field_penalty
        
        # Boost for well-formed values
        if in_network and re.match(r'^\d{1,3}%', in_network):
            confidence += 0.02
        if out_network and re.match(r'^\d{1,3}%', out_network):
            confidence += 0.02
        
        return max(0.0, min(1.0, confidence))

    def _extract_from_text_with_parser(self, content: str) -> List[RawExtractionRecord]:
        """
        Extract benefit records using production-grade text parser.
        
        Args:
            content: Full document text
            
        Returns:
            List of RawExtractionRecord objects
        """
        if not content:
            return []
        
        # Use the production-grade parser
        parsed_benefits = self.text_parser.parse_document(content)
        
        # Convert ParsedBenefit to RawExtractionRecord
        records = []
        for pb in parsed_benefits:
            # Build in-network text
            in_network_text = None
            if pb.in_network_value:
                in_network_text = pb.in_network_value
                if pb.in_network_copay:
                    in_network_text = f"{pb.in_network_copay} copay; {in_network_text}"
                if pb.after_deductible_in:
                    in_network_text = f"{in_network_text} after deductible"
            elif pb.in_network_copay:
                in_network_text = f"{pb.in_network_copay} copay"
            
            # Build out-of-network text
            out_network_text = None
            if pb.out_of_network_value:
                out_network_text = pb.out_of_network_value
                if pb.out_of_network_copay:
                    out_network_text = f"{pb.out_of_network_copay} copay; {out_network_text}"
                if pb.after_deductible_out:
                    out_network_text = f"{out_network_text} after deductible"
            elif pb.out_of_network_copay:
                out_network_text = f"{pb.out_of_network_copay} copay"
            
            records.append(RawExtractionRecord(
                service_category=pb.category,
                service_name=pb.service_name,
                description_text=None,  # NEW: Add description_text field
                in_network_text=in_network_text,
                out_of_network_text=out_network_text,
                preauth_text="Required" if pb.preauth_required else None,
                limit_text=pb.limitations,
                extraction_method="text_parser",
                raw_confidence=pb.confidence,
            ))
        
        return records

    def _extract_from_text(self, content: str) -> List[RawExtractionRecord]:
        """Legacy text extraction - uses parser now."""
        return self._extract_from_text_with_parser(content)

    # Legacy methods for backward compatibility
    def extract_benefits_data(self, pdf_document: str) -> Dict[str, Any]:
        """Legacy method - use extract_from_content instead."""
        if not os.path.exists(pdf_document):
            raise FileNotFoundError(f"PDF file not found: {pdf_document}")
        
        return {
            "benefit_name": "Sample Benefit",
            "coverage_amount": 1000,
            "network_type": "Two-Tier",
        }

    def standardize_data(self, extracted_data: Dict[str, Any]) -> Dict[str, Any]:
        """Standardize extracted data into consistent format."""
        return extracted_data

    def validate_extracted_data(self, standardized_data: Dict[str, Any]) -> bool:
        """Validate standardized data."""
        return bool(standardized_data.get("service_name") or standardized_data.get("benefit_name"))

    def process_document(self, pdf_document: str) -> Tuple[Dict[str, Any], bool]:
        """Process a PDF document."""
        extracted_data = self.extract_benefits_data(pdf_document)
        standardized_data = self.standardize_data(extracted_data)
        is_valid = self.validate_extracted_data(standardized_data)
        return standardized_data, is_valid
