"""
Normalizer Agent for SPD Benefits Extraction.

Transforms raw extraction records into standardized BenefitRecord format
using the SemanticMatcher for data-driven, flexible normalization.
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.models.benefit_record import (
    BenefitRecord,
    RawExtractionRecord,
    validate_coinsurance,
    validate_copay,
    validate_limit,
    validate_monetary_amount,
)
from src.ontology.semantic_matcher import (
    MatchResult,
    create_matcher,
    NetworkTermMatcher,
    ServiceCategoryMatcher,
    LimitPeriodMatcher,
)


class NormalizerAgent:
    """
    Agent responsible for normalizing raw extraction data into the 
    standardized 15-column Excel schema.
    
    Uses SemanticMatcher for data-driven normalization instead of
    hardcoded mappings. All term mappings are loaded from external
    JSON configuration that can be edited without code changes.
    
    Key responsibilities:
    - Parse complex benefit text (e.g., "80% after deductible; $20 copay")
    - Map network terminology variations (e.g., "Non-Network" → "Out-of-Network")
    - Extract limit types and periods from text
    - Apply semantic matching for flexible normalization
    """

    # Patterns for parsing benefit text
    COINSURANCE_PATTERN = re.compile(
        r"(\d{1,3})\s*%\s*(coinsurance|coins|covered)?",
        re.IGNORECASE
    )
    COPAY_PATTERN = re.compile(
        r"\$\s*([\d,]+(?:\.\d{2})?)\s*(copay|co-pay|copayment)?(?:\s+per\s+(\w+))?",
        re.IGNORECASE
    )
    DEDUCTIBLE_PATTERN = re.compile(
        r"(?:after|subject\s+to)\s+(?:the\s+)?deductible",
        re.IGNORECASE
    )
    DEDUCTIBLE_WAIVED_PATTERN = re.compile(
        r"(?:deductible\s+(?:is\s+)?waived|no\s+deductible|waived|not\s+subject)",
        re.IGNORECASE
    )
    NOT_COVERED_PATTERN = re.compile(
        r"not\s+covered|no\s+coverage|excluded|n/a|not\s+applicable",
        re.IGNORECASE
    )
    LIMIT_PATTERN = re.compile(
        r"(\d+)\s*(visits?|days?|hours?|treatments?|sessions?|units?)",
        re.IGNORECASE
    )
    DOLLAR_LIMIT_PATTERN = re.compile(
        r"\$\s*([\d,]+(?:\.\d{2})?)\s*(?:limit|maximum|max)?",
        re.IGNORECASE
    )
    PREAUTH_PATTERN = re.compile(
        r"(?:pre-?auth(?:orization)?|prior\s+auth(?:orization)?)\s*(?:required|needed|necessary)?",
        re.IGNORECASE
    )

    def __init__(
        self,
        config_path: Optional[str] = None,
        ontology: Optional[Dict[str, Any]] = None
    ):
        """
        Initialize the NormalizerAgent with semantic matchers.
        
        Args:
            config_path: Optional path to term_mappings.json config file.
                         If not provided, will look for data/term_mappings.json
            ontology: Optional legacy benefits ontology (for backward compatibility)
        """
        self.ontology = ontology or {}
        
        # Determine config path
        if config_path is None:
            # Try to find config relative to project root
            possible_paths = [
                Path(__file__).parent.parent.parent / "data" / "term_mappings.json",
                Path("data/term_mappings.json"),
                Path("./data/term_mappings.json"),
            ]
            for p in possible_paths:
                if p.exists():
                    config_path = str(p)
                    break
        
        # Initialize semantic matchers (data-driven, not hardcoded)
        self.network_matcher = NetworkTermMatcher(config_path=config_path)
        self.service_matcher = ServiceCategoryMatcher(config_path=config_path)
        self.limit_period_matcher = LimitPeriodMatcher(config_path=config_path)
        
        # Legacy service mappings for backward compatibility
        self.service_mappings = self._load_service_mappings()

    def _load_service_mappings(self) -> Dict[str, str]:
        """Load service name mappings from ontology."""
        mappings = {}
        if "services" in self.ontology:
            for service in self.ontology["services"]:
                canonical_name = service.get("canonical_name", "")
                for alias in service.get("aliases", []):
                    mappings[alias.lower()] = canonical_name
        return mappings

    def normalize(self, extracted_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize extracted data into standardized format.
        
        This is the main entry point supporting dictionary-based input
        for backward compatibility.
        
        Args:
            extracted_data: Dictionary with raw extraction data
            
        Returns:
            Normalized data dictionary
            
        Raises:
            ValueError: If required fields are missing
        """
        if not extracted_data:
            raise ValueError("extracted_data cannot be empty")
            
        # Handle required fields
        service_category = extracted_data.get("service_category") or extracted_data.get("benefit_name")
        if not service_category:
            raise ValueError("service_category or benefit_name is required")
        
        # Parse benefit text
        in_network_text = extracted_data.get("in_network_text", "")
        out_of_network_text = extracted_data.get("out_of_network_text", "")
        
        # Parse in-network benefits
        in_network_parsed = self.parse_benefit_text(in_network_text)
        out_network_parsed = self.parse_benefit_text(out_of_network_text)
        
        # Build normalized result
        normalized = {
            "header": self._normalize_header(service_category),
            "service": self._normalize_service_name(
                extracted_data.get("service_name") or service_category
            ),
            "in_network_coinsurance": in_network_parsed.get("coinsurance"),
            "in_network_after_deductible": in_network_parsed.get("after_deductible"),
            "in_network_copay": in_network_parsed.get("copay"),
            "out_of_network_coinsurance": out_network_parsed.get("coinsurance"),
            "out_of_network_after_deductible": out_network_parsed.get("after_deductible"),
            "out_of_network_copay": out_network_parsed.get("copay"),
        }
        
        # Handle limits
        limit_text = extracted_data.get("limit_text", "")
        if limit_text:
            limit_type, limit_period = self.parse_limit_text(limit_text)
            normalized["limit_type"] = limit_type
            normalized["limit_period"] = limit_period
        
        # Handle pre-authorization
        preauth_text = extracted_data.get("preauth_text", "")
        normalized["preauth_required"] = self.parse_preauth_text(preauth_text)
        
        # Handle coverage amount if present (legacy format)
        if "coverage_amount" in extracted_data:
            amount = extracted_data["coverage_amount"]
            if isinstance(amount, str):
                try:
                    # Try to parse as monetary amount
                    amount = amount.replace("$", "").replace(",", "")
                    normalized["coverage_amount"] = float(amount)
                except ValueError:
                    normalized["coverage_amount"] = amount
            else:
                normalized["coverage_amount"] = amount
        
        # Preserve network type if present
        if "network" in extracted_data or "network_type" in extracted_data:
            network = extracted_data.get("network") or extracted_data.get("network_type")
            normalized["network"] = self._normalize_network(network)
        
        return normalized

    def normalize_raw_record(self, raw_record: RawExtractionRecord) -> BenefitRecord:
        """
        Normalize a RawExtractionRecord into a BenefitRecord.
        
        Args:
            raw_record: Raw extraction record from the Extractor Agent
            
        Returns:
            Normalized BenefitRecord matching the 16-column schema
        """
        # Check if this is a deductible/OOP threshold row
        service_name = raw_record.service_name or raw_record.service_category or ""
        is_deductible_row = self._is_deductible_or_oop_row(service_name)
        
        if is_deductible_row:
            # Handle deductible/OOP rows differently - map to appropriate columns
            return self._normalize_deductible_row(raw_record)
        
        # Parse in-network benefits
        in_network_parsed = self.parse_benefit_text(raw_record.in_network_text or "")
        out_network_parsed = self.parse_benefit_text(raw_record.out_of_network_text or "")
        
        # FIXED: Handle merged cells - if In-Network has value but Out-of-Network is empty,
        # check if they should share the same value (common for Emergency Room, etc.)
        if self._should_propagate_merged_value(raw_record, in_network_parsed, out_network_parsed):
            out_network_parsed = in_network_parsed.copy()
        
        # Apply preventive service defaults if no explicit coinsurance
        if self._is_preventive_service(service_name):
            if not in_network_parsed.get("coinsurance"):
                in_network_parsed["coinsurance"] = "100%"
                in_network_parsed["after_deductible"] = "No"
        
        # Parse limits
        limit_type, limit_period = self.parse_limit_text(raw_record.limit_text or "")
        
        # Parse pre-auth
        preauth = self.parse_preauth_text(raw_record.preauth_text or "")
        
        # Extract and clean description/narrative text
        description = self._extract_description(raw_record)
        
        # Calculate confidence based on parsing success
        confidence = self._calculate_normalization_confidence(
            raw_record, in_network_parsed, out_network_parsed
        )
        
        return BenefitRecord(
            header=self._normalize_header(raw_record.service_category),
            service=self._normalize_service_name(
                raw_record.service_name or raw_record.service_category
            ),
            description=description,  # NEW: Include description
            in_network_coinsurance=in_network_parsed.get("coinsurance"),
            in_network_after_deductible=in_network_parsed.get("after_deductible"),
            in_network_copay=in_network_parsed.get("copay"),
            out_of_network_coinsurance=out_network_parsed.get("coinsurance"),
            out_of_network_after_deductible=out_network_parsed.get("after_deductible"),
            out_of_network_copay=out_network_parsed.get("copay"),
            limit_type=limit_type,
            limit_period=limit_period,
            preauth_required=preauth,
            confidence_score=confidence,
            source_page=raw_record.page_number,
            raw_in_network_text=raw_record.in_network_text,
            raw_out_of_network_text=raw_record.out_of_network_text,
        )
    
    def _is_deductible_or_oop_row(self, service_name: str) -> bool:
        """
        Check if a service name indicates this is a deductible or out-of-pocket maximum row.
        
        These rows should be mapped to the deductible/OOP columns, not benefit columns.
        
        Args:
            service_name: The service name to check
            
        Returns:
            True if this is a deductible/OOP row
        """
        if not service_name:
            return False
        
        name_lower = service_name.lower().strip()
        
        # Deductible indicators
        deductible_keywords = [
            "deductible",
            "annual deductible",
            "calendar year deductible",
            "plan year deductible",
        ]
        
        # OOP maximum indicators
        oop_keywords = [
            "out-of-pocket",
            "out of pocket",
            "oop",
            "maximum out-of-pocket",
            "annual out-of-pocket",
            "out-of-pocket maximum",
            "out-of-pocket limit",
        ]
        
        # Family indicator
        family_keywords = ["family"]
        
        # Check for exact or close matches
        for keyword in deductible_keywords + oop_keywords:
            if keyword in name_lower:
                return True
        
        # Also check if service name is simply "Family" (typically follows deductible)
        if name_lower == "family":
            return True
        
        return False
    
    def _normalize_deductible_row(self, raw_record: RawExtractionRecord) -> BenefitRecord:
        """
        Normalize a deductible or OOP maximum row.
        
        These rows have monetary amounts in the copay fields that should be mapped
        to the Individual/Family In-Network/Out-of-Network columns.
        
        Args:
            raw_record: Raw extraction record
            
        Returns:
            BenefitRecord with amounts in appropriate deductible/OOP columns
        """
        service_name = raw_record.service_name or raw_record.service_category or ""
        name_lower = service_name.lower().strip()
        
        # Determine if this is Individual or Family row
        is_family = "family" in name_lower or name_lower == "family"
        
        # Determine if this is Deductible or OOP
        is_oop = any(keyword in name_lower for keyword in [
            "out-of-pocket", "out of pocket", "oop", "maximum"
        ])
        
        # Extract monetary amounts from in-network and out-of-network copay fields
        in_network_amount = None
        out_network_amount = None
        
        # Parse amounts from the text fields
        if raw_record.in_network_text:
            in_network_parsed = self.parse_benefit_text(raw_record.in_network_text)
            in_network_amount = in_network_parsed.get("copay")
            # If no copay found, try to extract dollar amount directly
            if not in_network_amount:
                dollar_match = re.search(r'\$\s*([\d,]+(?:\.\d{2})?)', raw_record.in_network_text)
                if dollar_match:
                    amount = dollar_match.group(1).replace(",", "")
                    in_network_amount = f"${int(float(amount)):,}"
        
        if raw_record.out_of_network_text:
            out_network_parsed = self.parse_benefit_text(raw_record.out_of_network_text)
            out_network_amount = out_network_parsed.get("copay")
            # If no copay found, try to extract dollar amount directly
            if not out_network_amount:
                dollar_match = re.search(r'\$\s*([\d,]+(?:\.\d{2})?)', raw_record.out_of_network_text)
                if dollar_match:
                    amount = dollar_match.group(1).replace(",", "")
                    out_network_amount = f"${int(float(amount)):,}"
        
        # Map to appropriate columns
        individual_in_network = None
        family_in_network = None
        individual_out_of_network = None
        family_out_of_network = None
        
        if is_family:
            family_in_network = in_network_amount
            family_out_of_network = out_network_amount
        else:
            individual_in_network = in_network_amount
            individual_out_of_network = out_network_amount
        
        # Create normalized service name
        if is_oop:
            service_display = "Out-of-Pocket Maximum" if not is_family else "Family Out-of-Pocket Maximum"
        else:
            service_display = "Deductible" if not is_family else "Family Deductible"
        
        # Extract description if present
        description = self._extract_description(raw_record)
        
        return BenefitRecord(
            header=self._normalize_header(raw_record.service_category),
            service=service_display,
            description=description,
            in_network_coinsurance=None,
            in_network_after_deductible=None,
            in_network_copay=None,
            out_of_network_coinsurance=None,
            out_of_network_after_deductible=None,
            out_of_network_copay=None,
            individual_in_network=individual_in_network,
            family_in_network=family_in_network,
            individual_out_of_network=individual_out_of_network,
            family_out_of_network=family_out_of_network,
            limit_type=None,
            limit_period=None,
            preauth_required=None,
            confidence_score=0.95,  # High confidence for structured threshold data
            source_page=raw_record.page_number,
            raw_in_network_text=raw_record.in_network_text,
            raw_out_of_network_text=raw_record.out_of_network_text,
        )

    def normalize_batch(
        self, raw_records: List[RawExtractionRecord]
    ) -> List[BenefitRecord]:
        """
        Normalize a batch of raw extraction records.
        
        Filters out noise records (informational headers without coverage values)
        and returns only legitimate benefit service records.
        
        Args:
            raw_records: List of raw records to normalize
            
        Returns:
            List of normalized BenefitRecords (filtered for quality)
        """
        normalized = []
        for record in raw_records:
            benefit_record = self.normalize_raw_record(record)
            
            # Filter out noise records - services without any coverage data
            # that are likely informational headers or category titles
            if self._is_valid_benefit_record(benefit_record):
                normalized.append(benefit_record)

        # Merge consecutive Individual + Family deductible/OOP rows into one row
        normalized = self._merge_individual_family_deductible_rows(normalized)
        return normalized

    def _merge_individual_family_deductible_rows(
        self, records
    ):
        """
        Merge consecutive Individual and Family deductible/OOP rows into a single row.

        The document encodes deductible thresholds as two separate rows:
          Row A (Individual): individual_in_network=$300, individual_out_of_network=$450
          Row B (Family):     family_in_network=$600,    family_out_of_network=$900

        Both rows share the same header/category and belong in ONE output row with
        all four amount columns populated. This method finds consecutive pairs,
        folds the Family row values into the Individual row, renames the service
        to the bare threshold label (e.g. "Deductible"), and drops the Family row.
        """
        if not records:
            return records

        _OOP_KEYWORDS = ("out-of-pocket", "out of pocket", "oop")

        def _is_individual_threshold(rec):
            # _normalize_deductible_row labels the individual row as "Deductible"
            # or "Out-of-Pocket Maximum" (no "family" qualifier) and populates
            # only the individual_* columns.
            svc = (rec.service or "").lower()
            is_threshold = "deductible" in svc or any(k in svc for k in _OOP_KEYWORDS)
            is_family_labeled = "family" in svc
            return (
                is_threshold
                and not is_family_labeled
                and (rec.individual_in_network or rec.individual_out_of_network)
                and not rec.family_in_network
                and not rec.family_out_of_network
            )

        def _is_matching_family(ind, fam):
            svc = (fam.service or "").lower()
            is_threshold = "deductible" in svc or any(k in svc for k in _OOP_KEYWORDS)
            return (
                is_threshold
                and "family" in svc
                and (fam.family_in_network or fam.family_out_of_network)
                and fam.header == ind.header
            )

        merged = []
        skip_next = False

        for i, rec in enumerate(records):
            if skip_next:
                skip_next = False
                continue

            if (
                _is_individual_threshold(rec)
                and i + 1 < len(records)
                and _is_matching_family(rec, records[i + 1])
            ):
                family_rec = records[i + 1]
                rec.family_in_network = family_rec.family_in_network
                rec.family_out_of_network = family_rec.family_out_of_network
                svc_lower = rec.service.lower()
                if "deductible" in svc_lower:
                    rec.service = "Deductible"
                elif any(k in svc_lower for k in _OOP_KEYWORDS):
                    rec.service = "Out-of-Pocket Maximum"
                skip_next = True  # drop the now-merged Family row

            merged.append(rec)

        return merged

    def _is_valid_benefit_record(self, record: BenefitRecord) -> bool:
        """
        Check if a benefit record has meaningful data for HealthEdge HRP configuration.
        
        Records without coinsurance values that are not recognized service names
        are filtered out as they are likely informational headers.
        
        Args:
            record: The BenefitRecord to validate
            
        Returns:
            True if record should be included in output
        """
        # Has coinsurance value = definitely valid
        if record.in_network_coinsurance or record.out_of_network_coinsurance:
            return True
        
        # Has copay value = valid
        if record.in_network_copay or record.out_of_network_copay:
            return True
        
        # NEW: Has deductible/OOP threshold values = valid (don't filter these out!)
        if (record.individual_in_network or record.family_in_network or 
            record.individual_out_of_network or record.family_out_of_network):
            return True
        
        # Check if it's a known valid service that might not have coinsurance
        service_lower = (record.service or "").lower()
        
        # Category headers or informational items to filter out
        # REMOVED "deductible" and "out-of-pocket" from noise patterns - these are valid threshold rows
        noise_patterns = [
            "benefit maximum",  # Keep this as it's different from deductible
            "smartstarts", "incentive", "maximum family",
            "second surgical opinion", "other services",
            "additional services covered", "mental conditions for which",
            "procedures. prior authorization",
            # Fragment text patterns
            "check)", "check),", "trimester",
            "non-network limited to", "non-network limited",
            "lifetime maximum", "to a lifetime maximum",
            "participants are", 
            "of infertility", "infertility is",
        ]
        
        for pattern in noise_patterns:
            if pattern in service_lower:
                return False
        
        # If service has "precertification required" in the name but no values,
        # it's likely a note, not a service
        if "precertification required" in service_lower and not record.in_network_coinsurance:
            # Unless it's a legitimate service like "Transplant Services"
            if "transplant" not in service_lower and "diagnostic" not in service_lower:
                return False
        
        # Default: include if we're uncertain
        return True

    def parse_benefit_text(self, text: str) -> Dict[str, Optional[str]]:
        """
        Parse complex benefit text into structured components.
        
        Examples:
            "80% after deductible" → {coinsurance: "80%", after_deductible: "Yes"}
            "$20 copay; 100% after copay" → {copay: "$20", coinsurance: "100%"}
            "Not Covered" → {coinsurance: "NOT COVERED"}
        
        Args:
            text: Raw benefit text from document
            
        Returns:
            Dictionary with parsed components
        """
        result: Dict[str, Optional[str]] = {
            "coinsurance": None,
            "after_deductible": None,
            "copay": None,
        }
        
        if not text:
            return result
            
        text = text.strip()
        
        # Check for "Not Covered"
        if self.NOT_COVERED_PATTERN.search(text):
            result["coinsurance"] = "NOT COVERED"
            return result
        
        # Extract coinsurance
        coinsurance_match = self.COINSURANCE_PATTERN.search(text)
        if coinsurance_match:
            pct = int(coinsurance_match.group(1))
            if 0 <= pct <= 100:
                result["coinsurance"] = f"{pct}%"
        
        # Extract copay
        copay_match = self.COPAY_PATTERN.search(text)
        if copay_match:
            amount = copay_match.group(1).replace(",", "")
            suffix = copay_match.group(3)
            if "." in amount:
                dollars, cents = amount.split(".")
                formatted = f"${int(dollars):,}.{cents}"
            else:
                formatted = f"${int(amount):,}"
            if suffix:
                result["copay"] = f"{formatted} per {suffix}"
            else:
                result["copay"] = formatted
        
        # Check for deductible flags
        if self.DEDUCTIBLE_WAIVED_PATTERN.search(text):
            result["after_deductible"] = "No"
        elif self.DEDUCTIBLE_PATTERN.search(text):
            result["after_deductible"] = "Yes"
        
        return result

    def parse_limit_text(self, text: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Parse limit text into type and period.
        
        Examples:
            "90 visits per calendar year" → ("90 visits", "Calendar Year")
            "$3,000 lifetime maximum" → ("$3,000", "Lifetime")
            "25 days per admission" → ("25 days", "Per Admission")
        
        Args:
            text: Raw limit text from document
            
        Returns:
            Tuple of (limit_type, limit_period)
        """
        if not text:
            return None, None
            
        text = text.strip().lower()
        
        limit_type = None
        limit_period = None
        
        # Try to extract numeric limit with unit
        limit_match = self.LIMIT_PATTERN.search(text)
        if limit_match:
            number = limit_match.group(1)
            unit = limit_match.group(2).lower()
            # Normalize unit
            if int(number) > 1 and not unit.endswith("s"):
                unit += "s"
            elif int(number) == 1 and unit.endswith("s"):
                unit = unit[:-1]
            limit_type = f"{number} {unit}"
        
        # Try to extract dollar limit
        if not limit_type:
            dollar_match = self.DOLLAR_LIMIT_PATTERN.search(text)
            if dollar_match:
                amount = dollar_match.group(1).replace(",", "")
                if "." in amount:
                    dollars, cents = amount.split(".")
                    limit_type = f"${int(dollars):,}.{cents}"
                else:
                    limit_type = f"${int(amount):,}"
        
        # Extract period using semantic matcher (data-driven, not hardcoded)
        result = self.limit_period_matcher.match(text)
        if result.matched:
            limit_period = result.standard_value
        
        return limit_type, limit_period

    def parse_preauth_text(self, text: str) -> Optional[str]:
        """
        Parse pre-authorization requirement from text.
        
        Args:
            text: Raw pre-auth text from document
            
        Returns:
            "Yes" if required, "No" if explicitly not required, None otherwise
        """
        if not text:
            return None
            
        text = text.strip().lower()
        
        if self.PREAUTH_PATTERN.search(text):
            return "Yes"
        
        if "not required" in text or "no pre" in text:
            return "No"
        
        # Check for simple yes/no
        if text in ("yes", "y", "required"):
            return "Yes"
        if text in ("no", "n", "not required"):
            return "No"
        
        return None

    def _normalize_header(self, header: str) -> str:
        """Normalize service category header using semantic matcher."""
        if not header:
            return "Other Services"
        
        # Clean and standardize
        header = header.strip()
        
        # Use semantic matcher (data-driven, not hardcoded)
        result = self.service_matcher.match(header)
        if result.matched:
            return result.standard_value
        
        # Fallback: Check legacy ontology for backward compatibility
        header_lower = header.lower()
        if header_lower in self.service_mappings:
            return self.service_mappings[header_lower]
        
        # Title case fallback
        return header.title()

    def _is_preventive_service(self, service_name: str) -> bool:
        """
        Check if a service is a preventive/wellness service.
        
        Preventive services are typically covered at 100% IN-Network per ACA.
        This method identifies such services to apply default 100% coverage
        when no explicit coinsurance is extracted.
        
        Args:
            service_name: The service name to check
            
        Returns:
            True if service appears to be preventive/wellness
        """
        if not service_name:
            return False
        
        # Normalize dashes and convert to lowercase
        name_lower = service_name.lower()
        # Replace various dash characters with regular dash
        for dash in ['–', '—', '−', '‒']:
            name_lower = name_lower.replace(dash, '-')
        
        # Preventive service indicators
        preventive_keywords = [
            "routine wellness", "preventive", "routine colonoscopy", "routine mammogram",
            "well child", "well woman", "well visit", "wellness exam",
            "immunization", "vaccination", "vaccine",
            "screening", "annual physical", "annual exam",
            "preventative", "routine physical", "routine exam",
            "covid-19 testing", "covid testing",
            "routine wellness / preventive",
            "surgical sterilization procedures for women",  # ACA-mandated preventive
            # Handle reversed format: "Service - Routine"
            "colonoscopy - routine", "mammogram - routine",
            "colonoscopy- routine", "mammogram- routine",
            "colonoscopy -routine", "mammogram -routine",
        ]
        
        for keyword in preventive_keywords:
            if keyword in name_lower:
                return True
        
        # Check for "routine" + service pattern
        if name_lower.startswith("routine "):
            return True
        
        # Check for "Service – Routine" pattern (with any dash variant)
        if "- routine" in name_lower or "-routine" in name_lower:
            return True
        
        return False

    def _normalize_service_name(self, service: str) -> str:
        """
        Normalize specific service name.
        
        IMPORTANT: Unlike category normalization, service names should preserve
        hierarchical structure (e.g., "Urgent Care Physician's Office - Primary Care").
        We only normalize if we have an EXACT match in the master service list,
        not using fuzzy/pattern matching which is designed for categories.
        """
        if not service:
            return "General"
        
        service = service.strip()
        
        # Strategy 1: Check for exact match in master service list (standard_values)
        # This preserves hierarchical names from the master list
        result = self.service_matcher.match(service)
        if result.matched and result.match_method == "exact":
            # Only accept exact matches to preserve specific service names
            return result.standard_value
        
        # Strategy 2: Check legacy ontology for backward compatibility
        service_lower = service.lower()
        if service_lower in self.service_mappings:
            return self.service_mappings[service_lower]
        
        # Strategy 3: Preserve the original hierarchical service name
        # Do NOT use pattern/fuzzy matching here - that's for categories only
        # Apply smart title-casing that handles apostrophes correctly
        return self._smart_title(service)
    
    def _smart_title(self, text: str) -> str:
        """
        Smart title-case that handles apostrophes correctly.
        
        Python's .title() incorrectly capitalizes after apostrophes:
        "Physician's" -> "Physician'S"  (wrong)
        
        This method handles it properly:
        "physician's office" -> "Physician's Office"
        """
        import re
        # First, apply title case
        result = text.title()
        # Fix the apostrophe issue: lowercase any letter after an apostrophe
        result = re.sub(r"'([A-Z])", lambda m: "'" + m.group(1).lower(), result)
        return result

    def _normalize_network(self, network: str) -> str:
        """Normalize network terminology using semantic matcher."""
        if not network:
            return "In-Network"
        
        # Use semantic matcher (data-driven, not hardcoded)
        result = self.network_matcher.match(network)
        if result.matched:
            return result.standard_value
        
        # Default to In-Network if no match
        return "In-Network"

    def _normalize_service_category(self, category: str) -> str:
        """Normalize service category using semantic matcher."""
        if not category:
            return "Other Services"
        
        # Use semantic matcher (data-driven, not hardcoded)
        result = self.service_matcher.match(category)
        if result.matched:
            return result.standard_value
        
        # Fallback to title case
        return category.strip().title()

    def _calculate_normalization_confidence(
        self,
        raw_record: RawExtractionRecord,
        in_network_parsed: Dict[str, Any],
        out_network_parsed: Dict[str, Any],
    ) -> float:
        """
        Calculate confidence score for normalization quality.
        
        Factors:
        - Raw extraction confidence
        - Success of parsing benefit text
        - Presence of expected fields
        """
        base_confidence = raw_record.raw_confidence
        
        # Bonus for successful parsing
        parsing_score = 0.0
        total_checks = 0
        
        # Check in-network parsing
        if raw_record.in_network_text:
            total_checks += 1
            if in_network_parsed.get("coinsurance") or in_network_parsed.get("copay"):
                parsing_score += 1
        
        # Check out-of-network parsing
        if raw_record.out_of_network_text:
            total_checks += 1
            if out_network_parsed.get("coinsurance") or out_network_parsed.get("copay"):
                parsing_score += 1
        
        if total_checks > 0:
            parsing_confidence = parsing_score / total_checks
            # Weight: 70% base confidence, 30% parsing success
            return (base_confidence * 0.7) + (parsing_confidence * 0.3)
        
        return base_confidence

    def _should_propagate_merged_value(
        self,
        raw_record: RawExtractionRecord,
        in_network_parsed: Dict[str, Any],
        out_network_parsed: Dict[str, Any],
    ) -> bool:
        """
        Detect if In-Network and Out-of-Network should have the same value (merged cell).
        
        This handles the common case where a PDF has a merged cell spanning both
        network columns (e.g., "Emergency Room: 80%" applies to both IN and OON).
        
        Detection criteria:
        1. In-Network has a value (coinsurance or copay)
        2. Out-of-Network text is empty or null
        3. Service matches patterns that commonly have merged values:
           - Emergency services
           - Urgent care
           - Services with "both networks" language
        
        Args:
            raw_record: The raw extraction record
            in_network_parsed: Parsed In-Network benefits
            out_network_parsed: Parsed Out-of-Network benefits
            
        Returns:
            True if Out-of-Network should copy In-Network value
        """
        # Criterion 1: IN has value, OON is empty
        has_in_value = bool(
            in_network_parsed.get("coinsurance") or in_network_parsed.get("copay")
        )
        has_out_value = bool(
            out_network_parsed.get("coinsurance") or out_network_parsed.get("copay")
        )
        
        if not has_in_value or has_out_value:
            return False  # Nothing to propagate, or OON already has value
        
        # Criterion 2: OON text is truly empty (not just unparseable)
        out_text = (raw_record.out_of_network_text or "").strip()
        if out_text and out_text.lower() not in ("", "n/a", "na", "same", "same as in-network"):
            return False  # OON has text but we couldn't parse it - don't assume it's merged
        
        # Criterion 3: Service name matches common merged-cell patterns
        service_name = (raw_record.service_name or "").lower()
        
        # Pattern 1: Emergency services (mandated same coverage by law)
        emergency_patterns = [
            "emergency room", "emergency department", "er ", "emergency treatment",
            "emergency care", "emergency services", "emergency medical",
        ]
        if any(pattern in service_name for pattern in emergency_patterns):
            return True
        
        # Pattern 2: Urgent care (often same for both networks)
        if "urgent care" in service_name:
            return True
        
        # Pattern 3: Services with explicit "both networks" language in category
        category = (raw_record.service_category or "").lower()
        if any(phrase in category for phrase in ["both network", "all network", "emergency"]):
            return True
        
        # Pattern 4: Check extraction notes for merged cell indicators
        if hasattr(raw_record, 'extraction_notes'):
            notes = " ".join(raw_record.extraction_notes).lower()
            if "merged" in notes or "spanning" in notes:
                return True
        
        return False
    
    def _extract_description(self, raw_record: RawExtractionRecord) -> Optional[str]:
        """
        Extract and clean narrative description text from raw record.
        
        This extracts inclusion/exclusion criteria, conditions, and qualifications
        that are present in the narrative text but not in the benefit values.
        
        Args:
            raw_record: The raw extraction record
            
        Returns:
            Cleaned description text or None if no description available
        """
        description_text = raw_record.description_text
        
        if not description_text:
            return None
        
        # Clean the description text
        description = description_text.strip()
        
        # Remove redundant whitespace
        description = re.sub(r'\s+', ' ', description)
        
        # Remove common artifacts and formatting issues
        description = re.sub(r'^[\*\-•]+\s*', '', description)
        description = re.sub(r'\s+([.,;:])', r'\1', description)  # Fix spacing before punctuation
        
        # Truncate if too long (max 2000 chars per field definition)
        if len(description) > 2000:
            description = description[:1997] + "..."
        
        # Return None if description is too short to be meaningful
        if len(description) < 10:
            return None
        
        # Check if description is just a repeat of the service name or benefit values
        service_name_lower = (raw_record.service_name or "").lower()
        if description.lower() == service_name_lower:
            return None
        
        # Check if it's just repeating the benefit values
        in_net_text = (raw_record.in_network_text or "").lower()
        out_net_text = (raw_record.out_of_network_text or "").lower()
        desc_lower = description.lower()
        
        if desc_lower == in_net_text or desc_lower == out_net_text:
            return None
        
        return description