"""
Classifier Agent for SPD Benefits Extraction.

Responsible for detecting document type (SPD, SBC, SOB), 
identifying two-tier network structure, and mapping document sections.
"""

import re
from typing import Any, Dict, List, Optional

from src.models.benefit_record import (
    DocumentClassification,
    DocumentSection,
    DocumentType,
    NetworkColumnMapping,
    NetworkTier,
    ServiceCategoryType,
)


class ClassifierAgent:
    """
    Agent responsible for classifying SPD/SBC documents and detecting structure.
    
    Key responsibilities:
    - Detect document type (SPD, SBC, SOB, COC)
    - Identify two-tier network column structure
    - Map sections to service categories
    - Detect if OCR is needed
    """

    # Keywords for document type detection
    DOCUMENT_TYPE_KEYWORDS = {
        DocumentType.SPD: [
            "summary plan description",
            "spd",
            "plan description",
            "erisa",
        ],
        DocumentType.SBC: [
            "summary of benefits and coverage",
            "sbc",
            "what this plan covers",
            "coverage examples",
        ],
        DocumentType.SOB: [
            "schedule of benefits",
            "sob",
            "benefit schedule",
        ],
        DocumentType.COC: [
            "certificate of coverage",
            "coc",
            "certificate of insurance",
        ],
    }

    # Keywords for network column detection
    IN_NETWORK_KEYWORDS = [
        "in-network", "in network", "participating", "preferred",
        "tier 1", "par", "network", "contracted",
    ]
    OUT_NETWORK_KEYWORDS = [
        "out-of-network", "out of network", "non-participating",
        "non-par", "tier 2", "non-network", "oon", "non-preferred",
    ]

    # Section category keywords
    SECTION_KEYWORDS = {
        ServiceCategoryType.INPATIENT: [
            "inpatient", "hospital admission", "hospitalization",
            "room and board", "facility services",
        ],
        ServiceCategoryType.OUTPATIENT: [
            "outpatient", "ambulatory", "same day", "day surgery",
        ],
        ServiceCategoryType.PHYSICIAN: [
            "physician", "doctor visit", "office visit", "specialist",
            "primary care", "pcp",
        ],
        ServiceCategoryType.PREVENTIVE: [
            "preventive", "wellness", "routine", "annual physical",
            "immunization", "screening",
        ],
        ServiceCategoryType.EMERGENCY: [
            "emergency", "urgent care", "er", "emergency room",
            "911", "ambulance",
        ],
        ServiceCategoryType.MENTAL_HEALTH: [
            "mental health", "behavioral health", "psychiatric",
            "substance abuse", "counseling",
        ],
        ServiceCategoryType.PRESCRIPTION: [
            "prescription", "pharmacy", "drug", "rx",
            "generic", "brand", "specialty",
        ],
        ServiceCategoryType.MATERNITY: [
            "maternity", "pregnancy", "prenatal", "delivery",
            "newborn", "obstetric",
        ],
        ServiceCategoryType.REHABILITATION: [
            "rehabilitation", "physical therapy", "occupational therapy",
            "speech therapy", "pt", "ot",
        ],
        ServiceCategoryType.DURABLE_MEDICAL: [
            "durable medical", "dme", "equipment", "prosthetic",
            "orthotics",
        ],
    }

    def __init__(self, openai_client: Optional[Any] = None):
        """
        Initialize the ClassifierAgent.
        
        Args:
            openai_client: Optional Azure OpenAI client for GPT-based classification
        """
        self.openai_client = openai_client

    def classify(self, document: Any) -> str:
        """
        Classify a document and return the document type.
        
        This is a simple classification method for basic usage.
        
        Args:
            document: Document content (string or document object)
            
        Returns:
            Document type string ("Type A", "Type B", "Type C" for SPD, SBC, SOB)
            
        Raises:
            ValueError: If document is empty
            TypeError: If document is None
        """
        if document is None:
            raise TypeError("Document cannot be None")
        
        if isinstance(document, str) and not document.strip():
            raise ValueError("Document cannot be empty")
        
        # Get classification
        doc_type = self._detect_document_type(str(document))
        
        # Map to expected return values
        type_mapping = {
            DocumentType.SPD: "Type A",
            DocumentType.SBC: "Type B",
            DocumentType.SOB: "Type C",
            DocumentType.COC: "Type C",
            DocumentType.UNKNOWN: "Type A",  # Default
        }
        
        return type_mapping.get(doc_type, "Type A")

    def classify_document(
        self,
        document_id: str,
        document_content: str,
        total_pages: int = 1,
        table_data: Optional[List[Dict[str, Any]]] = None,
    ) -> DocumentClassification:
        """
        Perform full classification of a document.
        
        Args:
            document_id: Unique identifier for the document
            document_content: Full text content of the document
            total_pages: Total number of pages in document
            table_data: Optional extracted table data for column analysis
            
        Returns:
            DocumentClassification with full structure analysis
        """
        # Detect document type
        doc_type = self._detect_document_type(document_content)
        doc_type_confidence = self._calculate_type_confidence(document_content, doc_type)
        
        # Detect plan information
        plan_name = self._extract_plan_name(document_content)
        plan_year = self._extract_plan_year(document_content)
        
        # Detect network structure
        network_columns = self._detect_network_columns(table_data or [])
        detected_tiers = self._detect_network_tiers(document_content)
        is_two_tier = len(detected_tiers) == 2
        
        # Detect sections
        sections = self._detect_sections(document_content, total_pages)
        
        # Detect if has benefit tables
        has_benefit_tables = self._has_benefit_tables(table_data or [])
        table_pages = self._get_table_pages(table_data or [])
        
        return DocumentClassification(
            document_id=document_id,
            document_type=doc_type,
            document_type_confidence=doc_type_confidence,
            plan_name=plan_name,
            plan_year=plan_year,
            is_two_tier=is_two_tier,
            network_columns=network_columns,
            detected_tiers=detected_tiers,
            total_pages=total_pages,
            sections=sections,
            has_benefit_tables=has_benefit_tables,
            table_pages=table_pages,
        )

    def load_model(self, model_path: str) -> None:
        """
        Load classification model from path.
        
        Args:
            model_path: Path to the model file
        """
        # For rule-based classification, no model loading needed
        # This would be used if using a trained ML model
        pass

    def preprocess_document(self, document: str) -> str:
        """
        Preprocess document text for classification.
        
        Args:
            document: Raw document text
            
        Returns:
            Preprocessed text
        """
        if not document:
            return ""
        
        # Normalize whitespace
        text = re.sub(r'\s+', ' ', document)
        # Convert to lowercase for matching
        text = text.lower()
        # Remove special characters but keep alphanumeric and spaces
        text = re.sub(r'[^a-z0-9\s]', ' ', text)
        
        return text.strip()

    def postprocess_result(self, classification_result: DocumentType) -> str:
        """
        Postprocess classification result.
        
        Args:
            classification_result: Raw classification result
            
        Returns:
            Human-readable classification string
        """
        return classification_result.value

    def _detect_document_type(self, content: str) -> DocumentType:
        """Detect document type based on keyword matching."""
        content_lower = content.lower()
        
        scores = {}
        for doc_type, keywords in self.DOCUMENT_TYPE_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in content_lower)
            scores[doc_type] = score
        
        # Return type with highest score, default to SPD
        if not any(scores.values()):
            return DocumentType.UNKNOWN
        
        return max(scores.keys(), key=lambda k: scores[k])

    def _calculate_type_confidence(self, content: str, doc_type: DocumentType) -> float:
        """Calculate confidence score for document type classification."""
        if doc_type == DocumentType.UNKNOWN:
            return 0.5
        
        content_lower = content.lower()
        keywords = self.DOCUMENT_TYPE_KEYWORDS.get(doc_type, [])
        
        matches = sum(1 for kw in keywords if kw in content_lower)
        confidence = min(1.0, 0.6 + (matches * 0.1))
        
        return round(confidence, 2)

    def _extract_plan_name(self, content: str) -> Optional[str]:
        """Extract plan name from document content."""
        # Try common patterns
        patterns = [
            r"plan\s+name[:\s]+([A-Za-z0-9\s]+?)(?:\n|$)",
            r"([A-Za-z]+\s+(?:health|medical|insurance)\s+plan)",
            r"welcome\s+to\s+([A-Za-z0-9\s]+?)(?:\n|\.|$)",
        ]
        
        for pattern in patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                return match.group(1).strip()[:100]  # Limit length
        
        return None

    def _extract_plan_year(self, content: str) -> Optional[str]:
        """Extract plan year from document content.
        
        Supports years from 2000-2099 to handle historical and future documents.
        """
        # Look for year patterns - support 2000-2099 for broader compatibility
        year_pattern = r"(20[0-9]{2})[-–]?(20[0-9]{2})?"
        match = re.search(year_pattern, content)
        if match:
            if match.group(2):
                return f"{match.group(1)}-{match.group(2)}"
            return match.group(1)
        return None

    def _detect_network_columns(
        self, table_data: List[Dict[str, Any]]
    ) -> List[NetworkColumnMapping]:
        """Detect network column mappings from table headers."""
        mappings = []
        
        for table in table_data:
            headers = table.get("headers", [])
            for idx, header in enumerate(headers):
                header_lower = header.lower() if header else ""
                
                if any(kw in header_lower for kw in self.IN_NETWORK_KEYWORDS):
                    mappings.append(NetworkColumnMapping(
                        column_index=idx,
                        column_header=header,
                        network_tier=NetworkTier.IN_NETWORK,
                    ))
                elif any(kw in header_lower for kw in self.OUT_NETWORK_KEYWORDS):
                    mappings.append(NetworkColumnMapping(
                        column_index=idx,
                        column_header=header,
                        network_tier=NetworkTier.OUT_OF_NETWORK,
                    ))
        
        return mappings

    def _detect_network_tiers(self, content: str) -> List[str]:
        """Detect which network tiers are present in document."""
        content_lower = content.lower()
        tiers = []
        
        if any(kw in content_lower for kw in self.IN_NETWORK_KEYWORDS):
            tiers.append("In-Network")
        if any(kw in content_lower for kw in self.OUT_NETWORK_KEYWORDS):
            tiers.append("Out-of-Network")
        
        # Default to both if keywords found or none found
        if not tiers:
            return ["In-Network", "Out-of-Network"]
        
        return tiers

    def _detect_sections(
        self, content: str, total_pages: int
    ) -> List[DocumentSection]:
        """Detect document sections and their categories."""
        sections = []
        content_lower = content.lower()
        
        for section_type, keywords in self.SECTION_KEYWORDS.items():
            for keyword in keywords:
                if keyword in content_lower:
                    # Found a section
                    sections.append(DocumentSection(
                        section_name=section_type.value,
                        section_type=section_type,
                        start_page=1,  # Would need page info for accurate detection
                        has_table=True,  # Assume has table
                        confidence=0.8,
                    ))
                    break  # Only add once per section type
        
        return sections

    def _has_benefit_tables(self, table_data: List[Dict[str, Any]]) -> bool:
        """Check if document has benefit tables."""
        return len(table_data) > 0

    def _get_table_pages(self, table_data: List[Dict[str, Any]]) -> List[int]:
        """Get list of pages containing tables."""
        pages = set()
        for table in table_data:
            if "page" in table:
                pages.add(table["page"])
        return sorted(pages) if pages else []