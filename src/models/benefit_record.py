"""
Pydantic models for SPD Benefits Extraction.

Defines the 15-column Excel schema and supporting models for the
complete extraction pipeline: Classification -> Extraction -> Normalization -> Output.
"""

import re
from datetime import datetime
from enum import Enum
from typing import Any, Optional, List

from pydantic import BaseModel, Field, field_validator, model_validator


# =============================================================================
# Enums for standardized values
# =============================================================================


class DocumentType(str, Enum):
    """Type of benefits document being processed."""

    SPD = "SPD"  # Summary Plan Description
    SBC = "SBC"  # Summary of Benefits and Coverage
    SOB = "SOB"  # Schedule of Benefits
    COC = "COC"  # Certificate of Coverage
    UNKNOWN = "UNKNOWN"


class NetworkTier(str, Enum):
    """Network tier types for two-tier network plans."""

    IN_NETWORK = "In-Network"
    OUT_OF_NETWORK = "Out-of-Network"
    TIER_1 = "Tier 1"
    TIER_2 = "Tier 2"
    PREFERRED = "Preferred"
    NON_PREFERRED = "Non-Preferred"


class CoverageStatus(str, Enum):
    """Coverage status indicators."""

    COVERED = "COVERED"
    NOT_COVERED = "NOT COVERED"
    SUBJECT_TO_DEDUCTIBLE = "Subject to Deductible"
    DEDUCTIBLE_WAIVED = "Deductible Waived"
    NOT_APPLICABLE = "N/A"


class LimitPeriod(str, Enum):
    """Time periods for benefit limits."""

    BENEFIT_YEAR = "Benefit Year"
    CALENDAR_YEAR = "Calendar Year"
    PLAN_YEAR = "Plan Year"
    LIFETIME = "Lifetime"
    PER_VISIT = "Per Visit"
    PER_ADMISSION = "Per Admission"
    PER_OCCURRENCE = "Per Occurrence"
    PER_CONFINEMENT = "Per Confinement"
    ROLLING_12_MONTHS = "Rolling 12 Months"
    THREE_YEARS = "3 Years"


class ServiceCategoryType(str, Enum):
    """High-level service category types."""

    INPATIENT = "Inpatient Services"
    OUTPATIENT = "Outpatient Services"
    PHYSICIAN = "Physician Services"
    PREVENTIVE = "Preventive Care"
    EMERGENCY = "Emergency Services"
    MENTAL_HEALTH = "Mental Health"
    PRESCRIPTION = "Prescription Drugs"
    MATERNITY = "Maternity Care"
    REHABILITATION = "Rehabilitation"
    DURABLE_MEDICAL = "Durable Medical Equipment"
    OTHER = "Other Services"


# =============================================================================
# Validation utilities
# =============================================================================


def validate_coinsurance(value: str | None) -> str | None:
    """
    Validate coinsurance format: 0-100%, 'NOT COVERED', 'N/A', or None.

    Valid formats:
    - "80%", "100%", "0%"
    - "NOT COVERED"
    - "N/A"
    - None
    """
    if value is None or value == "":
        return None

    value = str(value).strip().upper()

    # Check for NOT COVERED or N/A
    if value in ("NOT COVERED", "N/A", "NA", "NONE"):
        return "NOT COVERED" if value in ("NOT COVERED", "NONE") else "N/A"

    # Check for percentage format
    percentage_match = re.match(r"^(\d{1,3})%?$", value.replace(" ", ""))
    if percentage_match:
        pct = int(percentage_match.group(1))
        if 0 <= pct <= 100:
            return f"{pct}%"
        raise ValueError(f"Coinsurance percentage must be 0-100, got {pct}")

    # Check for "X% coinsurance" format
    coinsurance_match = re.match(
        r"^(\d{1,3})%?\s*(coinsurance|coins)?$", value.replace(" ", ""), re.IGNORECASE
    )
    if coinsurance_match:
        pct = int(coinsurance_match.group(1))
        if 0 <= pct <= 100:
            return f"{pct}%"

    raise ValueError(
        f"Invalid coinsurance format: {value}. Expected 0-100%, 'NOT COVERED', or 'N/A'"
    )


def validate_copay(value: str | None) -> str | None:
    """
    Validate copay format: $X, $X.XX, or None.

    Valid formats:
    - "$20", "$350", "$1,500"
    - "$20.00", "$350.50"
    - "None", "N/A"
    - None
    """
    if value is None or value == "":
        return None

    value = str(value).strip()

    # Check for None/N/A
    if value.upper() in ("NONE", "N/A", "NA", "-"):
        return None

    # Check for dollar amount
    # Matches: $20, $20.00, $1,500, $1,500.00
    dollar_match = re.match(r"^\$?([\d,]+(?:\.\d{2})?)$", value.replace(" ", ""))
    if dollar_match:
        amount = dollar_match.group(1).replace(",", "")
        # Format with commas for thousands
        if "." in amount:
            dollars, cents = amount.split(".")
            formatted = f"${int(dollars):,}.{cents}"
        else:
            formatted = f"${int(amount):,}"
        return formatted

    # Check for "per day/visit/etc" suffix
    per_match = re.match(
        r"^\$?([\d,]+(?:\.\d{2})?)\s*(per\s+\w+)?$", value, re.IGNORECASE
    )
    if per_match:
        amount = per_match.group(1).replace(",", "")
        suffix = per_match.group(2) or ""
        if "." in amount:
            dollars, cents = amount.split(".")
            formatted = f"${int(dollars):,}.{cents}"
        else:
            formatted = f"${int(amount):,}"
        return f"{formatted} {suffix}".strip() if suffix else formatted

    raise ValueError(f"Invalid copay format: {value}. Expected $X or $X.XX format")


def validate_monetary_amount(value: str | None) -> str | None:
    """
    Validate monetary amount format for deductibles/OOP limits.

    Valid formats:
    - "$500", "$1,500", "$6,000"
    - "Unlimited"
    - "N/A"
    - None
    """
    if value is None or value == "":
        return None

    value = str(value).strip()

    # Check for special values
    if value.upper() in ("UNLIMITED", "NONE", "N/A", "NA", "-"):
        if value.upper() == "UNLIMITED":
            return "Unlimited"
        return None

    # Check for dollar amount
    dollar_match = re.match(r"^\$?([\d,]+(?:\.\d{2})?)$", value.replace(" ", ""))
    if dollar_match:
        amount = dollar_match.group(1).replace(",", "")
        if "." in amount:
            dollars, cents = amount.split(".")
            return f"${int(dollars):,}.{cents}"
        return f"${int(amount):,}"

    raise ValueError(f"Invalid monetary amount: {value}. Expected $X format")


def validate_limit(value: str | None) -> str | None:
    """
    Validate limit format: numeric with unit or dollar amount.

    Valid formats:
    - "90 visits", "25 days", "60 days"
    - "$3,000", "$50,000"
    - "Unlimited"
    - None
    """
    if value is None or value == "":
        return None

    value = str(value).strip()

    # Check for unlimited
    if value.upper() in ("UNLIMITED", "NONE", "NO LIMIT", "N/A"):
        return "Unlimited" if value.upper() in ("UNLIMITED", "NO LIMIT") else None

    # Check for numeric limit with unit
    limit_match = re.match(
        r"^(\d+)\s*(visits?|days?|hours?|treatments?|sessions?|units?)$",
        value,
        re.IGNORECASE,
    )
    if limit_match:
        number = limit_match.group(1)
        unit = limit_match.group(2).lower()
        # Normalize unit to plural if > 1
        if int(number) > 1 and not unit.endswith("s"):
            unit += "s"
        elif int(number) == 1 and unit.endswith("s"):
            unit = unit[:-1]
        return f"{number} {unit}"

    # Check for dollar limit
    dollar_match = re.match(r"^\$?([\d,]+(?:\.\d{2})?)$", value.replace(" ", ""))
    if dollar_match:
        amount = dollar_match.group(1).replace(",", "")
        if "." in amount:
            dollars, cents = amount.split(".")
            return f"${int(dollars):,}.{cents}"
        return f"${int(amount):,}"

    # Return as-is if no standard format matched (may need human review)
    return value


# =============================================================================
# Core data models
# =============================================================================


class BenefitRecord(BaseModel):
    """
    Single benefit service row matching the 15-column output Excel schema.

    This is the primary output format for each benefit service extracted
    from SPD/SBC documents.
    """

    # -------------------------------------------------------------------------
    # Column 1-2: Identification
    # -------------------------------------------------------------------------
    header: str = Field(
        ...,
        description="Service category header (e.g., 'Inpatient Hospital Services')",
        min_length=1,
        max_length=500,
    )
    service: str = Field(
        ...,
        description="Specific service name (e.g., 'Room and Board')",
        min_length=1,
        max_length=500,
    )

    # -------------------------------------------------------------------------
    # Column 3-5: In-Network Benefits
    # -------------------------------------------------------------------------
    in_network_coinsurance: Optional[str] = Field(
        default=None,
        description="In-network coinsurance (e.g., '80%', '100%', 'NOT COVERED')",
    )
    in_network_after_deductible: Optional[str] = Field(
        default=None,
        description="Whether in-network benefit is after deductible ('Yes', 'No')",
    )
    in_network_copay: Optional[str] = Field(
        default=None,
        description="In-network copay amount (e.g., '$20', '$350')",
    )

    # -------------------------------------------------------------------------
    # Column 6-8: Out-of-Network Benefits
    # -------------------------------------------------------------------------
    out_of_network_coinsurance: Optional[str] = Field(
        default=None,
        description="Out-of-network coinsurance (e.g., '60%', 'NOT COVERED')",
    )
    out_of_network_after_deductible: Optional[str] = Field(
        default=None,
        description="Whether out-of-network benefit is after deductible ('Yes', 'No')",
    )
    out_of_network_copay: Optional[str] = Field(
        default=None,
        description="Out-of-network copay amount (e.g., '$50', '$500')",
    )

    # -------------------------------------------------------------------------
    # Column 9-12: Deductible/OOP amounts (only for deductible/OOP max rows)
    # -------------------------------------------------------------------------
    individual_in_network: Optional[str] = Field(
        default=None,
        description="Individual in-network deductible/OOP (e.g., '$500', '$1,500')",
    )
    family_in_network: Optional[str] = Field(
        default=None,
        description="Family in-network deductible/OOP (e.g., '$1,000', '$3,000')",
    )
    individual_out_of_network: Optional[str] = Field(
        default=None,
        description="Individual out-of-network deductible/OOP",
    )
    family_out_of_network: Optional[str] = Field(
        default=None,
        description="Family out-of-network deductible/OOP",
    )

    # -------------------------------------------------------------------------
    # Column 13-14: Limits
    # -------------------------------------------------------------------------
    limit_type: Optional[str] = Field(
        default=None,
        description="Benefit limit (e.g., '90 visits', '$3,000', '25 days')",
    )
    limit_period: Optional[str] = Field(
        default=None,
        description="Limit time period (e.g., 'Benefit Year', 'Lifetime')",
    )

    # -------------------------------------------------------------------------
    # Column 15: Pre-authorization
    # -------------------------------------------------------------------------
    preauth_required: Optional[str] = Field(
        default=None,
        description="Whether pre-authorization is required ('Yes', 'No')",
    )

    # -------------------------------------------------------------------------
    # Metadata (not in Excel output, used for processing and audit)
    # -------------------------------------------------------------------------
    confidence_score: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Extraction confidence score (0.0-1.0)",
    )
    source_page: Optional[int] = Field(
        default=None,
        ge=1,
        description="Source page number in the PDF document",
    )
    raw_in_network_text: Optional[str] = Field(
        default=None,
        description="Original extracted in-network text before normalization",
    )
    raw_out_of_network_text: Optional[str] = Field(
        default=None,
        description="Original extracted out-of-network text before normalization",
    )
    extraction_notes: Optional[str] = Field(
        default=None,
        description="Notes about extraction issues or uncertainties",
    )

    # -------------------------------------------------------------------------
    # Validators
    # -------------------------------------------------------------------------

    @field_validator("in_network_coinsurance", "out_of_network_coinsurance", mode="before")
    @classmethod
    def validate_coinsurance_field(cls, v: str | None) -> str | None:
        """Validate coinsurance values."""
        if v is None:
            return None
        try:
            return validate_coinsurance(v)
        except ValueError:
            # Return original value if validation fails (may need human review)
            return str(v)

    @field_validator("in_network_copay", "out_of_network_copay", mode="before")
    @classmethod
    def validate_copay_field(cls, v: str | None) -> str | None:
        """Validate copay values."""
        if v is None:
            return None
        try:
            return validate_copay(v)
        except ValueError:
            # Return original value if validation fails
            return str(v)

    @field_validator(
        "individual_in_network",
        "family_in_network",
        "individual_out_of_network",
        "family_out_of_network",
        mode="before"
    )
    @classmethod
    def validate_deductible_oop(cls, v: str | None) -> str | None:
        """Validate deductible/OOP monetary amounts."""
        if v is None:
            return None
        try:
            return validate_monetary_amount(v)
        except ValueError:
            return str(v)

    @field_validator("limit_type", mode="before")
    @classmethod
    def validate_limit_type_field(cls, v: str | None) -> str | None:
        """Validate limit type values."""
        if v is None:
            return None
        try:
            return validate_limit(v)
        except ValueError:
            return str(v)

    @field_validator("in_network_after_deductible", "out_of_network_after_deductible", mode="before")
    @classmethod
    def validate_after_deductible(cls, v: str | None) -> str | None:
        """Normalize after deductible values to Yes/No."""
        if v is None:
            return None
        v_upper = str(v).strip().upper()
        if v_upper in ("YES", "Y", "TRUE", "1"):
            return "Yes"
        if v_upper in ("NO", "N", "FALSE", "0", "WAIVED", "DEDUCTIBLE WAIVED"):
            return "No"
        return str(v)

    @field_validator("preauth_required", mode="before")
    @classmethod
    def validate_preauth(cls, v: str | None) -> str | None:
        """Normalize pre-authorization values to Yes/No."""
        if v is None:
            return None
        v_upper = str(v).strip().upper()
        if v_upper in ("YES", "Y", "TRUE", "1", "REQUIRED"):
            return "Yes"
        if v_upper in ("NO", "N", "FALSE", "0", "NOT REQUIRED"):
            return "No"
        return str(v)

    def to_excel_row(self) -> List[Any]:
        """Convert to a list matching the 15-column Excel schema."""
        return [
            self.header,
            self.service,
            self.in_network_coinsurance,
            self.in_network_after_deductible,
            self.in_network_copay,
            self.out_of_network_coinsurance,
            self.out_of_network_after_deductible,
            self.out_of_network_copay,
            self.individual_in_network,
            self.family_in_network,
            self.individual_out_of_network,
            self.family_out_of_network,
            self.limit_type,
            self.limit_period,
            self.preauth_required,
        ]

    @classmethod
    def get_excel_headers(cls) -> List[str]:
        """Get the 15-column Excel headers."""
        return [
            "Header",
            "Service",
            "In-Network Coinsurance",
            "In-Network After Deductible",
            "In-Network Copay",
            "Out-of-Network Coinsurance",
            "Out-of-Network After Deductible",
            "Out-of-Network Copay",
            "Individual In-Network",
            "Family In-Network",
            "Individual Out-of-Network",
            "Family Out-of-Network",
            "Limit Type",
            "Limit Period",
            "Pre-Authorization Required",
        ]


class RawExtractionRecord(BaseModel):
    """
    Raw extraction output before normalization.

    Used by the Extractor Agent to capture raw text extracted from
    the document before the Normalizer Agent processes it.
    """

    # Identification
    service_category: str = Field(
        ...,
        description="Raw service category text from document",
    )
    service_name: Optional[str] = Field(
        default=None,
        description="Raw service name text from document",
    )

    # NEW: Raw description/narrative text
    description_text: Optional[str] = Field(
        default=None,
        description="Raw narrative description including inclusion/exclusion criteria and conditions",
    )

    # Raw benefit text (before parsing into coinsurance/copay)
    in_network_text: Optional[str] = Field(
        default=None,
        description="Raw in-network benefit text (e.g., '80% after deductible; $20 copay')",
    )
    out_of_network_text: Optional[str] = Field(
        default=None,
        description="Raw out-of-network benefit text",
    )

    # Raw limit text
    limit_text: Optional[str] = Field(
        default=None,
        description="Raw limit text (e.g., '90 visits per calendar year')",
    )

    # Raw pre-auth text
    preauth_text: Optional[str] = Field(
        default=None,
        description="Raw pre-authorization text",
    )

    # Location in document
    page_number: Optional[int] = Field(
        default=None,
        ge=1,
        description="Page number where this record was found",
    )
    table_index: Optional[int] = Field(
        default=None,
        ge=0,
        description="Index of the table on the page",
    )
    row_index: Optional[int] = Field(
        default=None,
        ge=0,
        description="Row index within the table",
    )

    # Extraction metadata
    bounding_box: Optional[List[float]] = Field(
        default=None,
        description="Bounding box coordinates [x1, y1, x2, y2]",
    )
    extraction_method: Optional[str] = Field(
        default=None,
        description="Method used for extraction (e.g., 'table', 'text', 'ocr')",
    )
    raw_confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Document Intelligence confidence score",
    )

    @model_validator(mode="after")
    def validate_has_content(self) -> "RawExtractionRecord":
        """Ensure at least service category or some benefit text is present."""
        if not self.service_category:
            raise ValueError("service_category is required")
        return self


class DocumentSection(BaseModel):
    """
    Represents a detected section in the SPD/SBC document.

    Used by the Classifier Agent to identify document structure.
    """

    section_name: str = Field(
        ...,
        description="Name of the section (e.g., 'Deductibles', 'Inpatient Services')",
    )
    section_type: Optional[ServiceCategoryType] = Field(
        default=None,
        description="Categorized section type",
    )
    start_page: int = Field(
        ...,
        ge=1,
        description="Starting page of the section",
    )
    end_page: Optional[int] = Field(
        default=None,
        ge=1,
        description="Ending page of the section",
    )
    has_table: bool = Field(
        default=False,
        description="Whether section contains benefit tables",
    )
    table_columns: Optional[List[str]] = Field(
        default=None,
        description="Detected column headers if table present",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Classification confidence",
    )


class NetworkColumnMapping(BaseModel):
    """
    Mapping of network columns detected in the document.

    Two-tier network documents may have different column structures.
    """

    column_index: int = Field(
        ...,
        ge=0,
        description="Column index in the table",
    )
    column_header: str = Field(
        ...,
        description="Original column header text",
    )
    network_tier: NetworkTier = Field(
        ...,
        description="Mapped network tier",
    )
    column_type: str = Field(
        default="benefit",
        description="Type of data in column (benefit, limit, preauth)",
    )


class DocumentClassification(BaseModel):
    """
    Classification output from the Classifier Agent.

    Contains document type identification, network structure,
    and detected sections for downstream processing.
    """

    # Document identification
    document_id: str = Field(
        ...,
        description="Unique identifier for the document",
    )
    document_type: DocumentType = Field(
        ...,
        description="Classified document type (SPD, SBC, etc.)",
    )
    document_type_confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence in document type classification",
    )

    # Plan information
    plan_name: Optional[str] = Field(
        default=None,
        description="Detected plan name from document",
    )
    plan_year: Optional[str] = Field(
        default=None,
        description="Plan year (e.g., '2024', '2024-2025')",
    )
    effective_date: Optional[str] = Field(
        default=None,
        description="Plan effective date",
    )

    # Network structure
    is_two_tier: bool = Field(
        default=True,
        description="Whether document is two-tier network format",
    )
    network_columns: List[NetworkColumnMapping] = Field(
        default_factory=list,
        description="Detected network column mappings",
    )
    detected_tiers: List[str] = Field(
        default_factory=lambda: ["In-Network", "Out-of-Network"],
        description="List of detected network tiers",
    )

    # Document structure
    total_pages: int = Field(
        ...,
        ge=1,
        description="Total number of pages in document",
    )
    sections: List[DocumentSection] = Field(
        default_factory=list,
        description="Detected document sections",
    )
    has_benefit_tables: bool = Field(
        default=False,
        description="Whether document contains benefit tables",
    )
    table_pages: List[int] = Field(
        default_factory=list,
        description="Page numbers containing benefit tables",
    )

    # Processing hints
    requires_ocr: bool = Field(
        default=False,
        description="Whether OCR is needed (scanned document)",
    )
    language: str = Field(
        default="en",
        description="Detected document language",
    )
    processing_notes: Optional[str] = Field(
        default=None,
        description="Notes for downstream processing",
    )


class ExtractionMetadata(BaseModel):
    """Metadata about the extraction process."""

    extraction_timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="UTC timestamp of extraction",
    )
    extractor_version: str = Field(
        default="1.0.0",
        description="Version of the extraction pipeline",
    )
    document_intelligence_model: str = Field(
        default="prebuilt-layout",
        description="Azure Document Intelligence model used",
    )
    gpt_model: str = Field(
        default="gpt-4o",
        description="GPT model used for agent processing",
    )
    processing_time_seconds: Optional[float] = Field(
        default=None,
        ge=0,
        description="Total processing time in seconds",
    )
    pages_processed: int = Field(
        default=0,
        ge=0,
        description="Number of pages processed",
    )
    tables_extracted: int = Field(
        default=0,
        ge=0,
        description="Number of tables extracted",
    )


class ValidationIssue(BaseModel):
    """Represents a validation issue found during extraction."""

    record_index: int = Field(
        ...,
        ge=0,
        description="Index of the record with the issue",
    )
    field_name: str = Field(
        ...,
        description="Name of the field with the issue",
    )
    issue_type: str = Field(
        ...,
        description="Type of validation issue (e.g., 'format_error', 'missing_value')",
    )
    message: str = Field(
        ...,
        description="Human-readable description of the issue",
    )
    severity: str = Field(
        default="warning",
        description="Severity level: 'error', 'warning', 'info'",
    )
    suggested_value: Optional[str] = Field(
        default=None,
        description="Suggested corrected value if available",
    )


class ExtractionResult(BaseModel):
    """
    Complete extraction result container.

    Contains all extracted benefit records, classification information,
    and processing metadata for a single document.
    """

    # Document identification
    document_id: str = Field(
        ...,
        description="Unique identifier for the document",
    )
    source_filename: str = Field(
        ...,
        description="Original filename of the source document",
    )
    source_blob_url: Optional[str] = Field(
        default=None,
        description="Azure Blob Storage URL of source document",
    )

    # Classification
    classification: DocumentClassification = Field(
        ...,
        description="Document classification results",
    )

    # Extracted records
    benefit_records: List[BenefitRecord] = Field(
        default_factory=list,
        description="List of normalized benefit records",
    )
    raw_records: List[RawExtractionRecord] = Field(
        default_factory=list,
        description="List of raw extraction records (before normalization)",
    )

    # Validation
    validation_issues: List[ValidationIssue] = Field(
        default_factory=list,
        description="List of validation issues found",
    )
    is_valid: bool = Field(
        default=True,
        description="Whether extraction passed validation",
    )

    # Confidence and quality
    overall_confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Overall extraction confidence score",
    )
    requires_human_review: bool = Field(
        default=False,
        description="Whether human review is recommended",
    )
    human_review_reasons: List[str] = Field(
        default_factory=list,
        description="Reasons why human review is needed",
    )

    # Metadata
    metadata: ExtractionMetadata = Field(
        default_factory=ExtractionMetadata,
        description="Extraction process metadata",
    )

    # Output
    output_filename: Optional[str] = Field(
        default=None,
        description="Generated output Excel filename",
    )
    output_blob_url: Optional[str] = Field(
        default=None,
        description="Azure Blob Storage URL of output Excel",
    )

    def get_confidence_summary(self) -> dict[str, Any]:
        """Get a summary of confidence scores across all records."""
        if not self.benefit_records:
            return {
                "total_records": 0,
                "average_confidence": 0.0,
                "min_confidence": 0.0,
                "max_confidence": 0.0,
                "low_confidence_count": 0,
            }

        confidences = [r.confidence_score for r in self.benefit_records]
        return {
            "total_records": len(confidences),
            "average_confidence": sum(confidences) / len(confidences),
            "min_confidence": min(confidences),
            "max_confidence": max(confidences),
            "low_confidence_count": sum(1 for c in confidences if c < 0.70),
        }

    def to_excel_data(self) -> List[List[Any]]:
        """
        Convert all benefit records to Excel-ready data.

        Returns:
            List of rows with headers as first row.
        """
        headers = BenefitRecord.get_excel_headers()
        rows = [headers]
        for record in self.benefit_records:
            rows.append(record.to_excel_row())
        return rows
