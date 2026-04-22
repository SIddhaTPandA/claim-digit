"""
Deterministic Validator for SPD Benefits Extraction.

Applies rule-based validation to ensure extracted benefit data
meets quality and consistency requirements.
"""

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.models.benefit_record import BenefitRecord, ValidationIssue


class DeterministicValidator:
    """
    Validates extracted benefit data using deterministic rules.
    
    Key validation rules:
    - IN/OON coinsurance percentages must be 0-100%
    - Family deductible >= Individual deductible
    - Required fields must be present
    - Format consistency checks
    """

    def __init__(self, rules: Optional[List[Callable[[Any], bool]]] = None):
        """
        Initialize validator with optional custom rules.
        
        Args:
            rules: Optional list of custom validation functions
        """
        self.rules = rules or []
        self._setup_default_rules()

    def _setup_default_rules(self) -> None:
        """Set up default validation rules."""
        # Rules are added via specific validation methods
        pass

    def validate(self, data: Any) -> Tuple[bool, List[str]]:
        """
        Validate data against all registered rules.
        
        Args:
            data: Data to validate (can be dict or BenefitRecord)
            
        Returns:
            Tuple of (is_valid, list_of_error_messages)
        """
        errors = []
        
        # Apply custom rules
        for rule in self.rules:
            try:
                if not rule(data):
                    errors.append(f"Validation failed for rule: {rule.__name__}")
            except Exception as e:
                errors.append(f"Rule {rule.__name__} raised exception: {str(e)}")
        
        # Apply built-in validations based on data type
        if isinstance(data, dict):
            dict_errors = self._validate_dict(data)
            errors.extend(dict_errors)
        elif isinstance(data, BenefitRecord):
            record_errors = self._validate_benefit_record(data)
            errors.extend(record_errors)
        
        is_valid = len(errors) == 0
        return is_valid, errors

    def validate_benefit_record(
        self, record: BenefitRecord
    ) -> List[ValidationIssue]:
        """
        Validate a single BenefitRecord with detailed issue reporting.
        
        Args:
            record: BenefitRecord to validate
            
        Returns:
            List of ValidationIssue objects for any problems found
        """
        issues = []
        
        # Validate required fields
        if not record.header or not record.header.strip():
            issues.append(ValidationIssue(
                record_index=0,
                field_name="header",
                issue_type="missing_value",
                message="Header (service category) is required",
                severity="error",
            ))
        
        if not record.service or not record.service.strip():
            issues.append(ValidationIssue(
                record_index=0,
                field_name="service",
                issue_type="missing_value",
                message="Service name is required",
                severity="error",
            ))
        
        # Validate coinsurance format
        for field_name in ["in_network_coinsurance", "out_of_network_coinsurance"]:
            value = getattr(record, field_name)
            if value:
                issue = self._validate_coinsurance_format(field_name, value)
                if issue:
                    issues.append(issue)
        
        # Validate copay format
        for field_name in ["in_network_copay", "out_of_network_copay"]:
            value = getattr(record, field_name)
            if value:
                issue = self._validate_copay_format(field_name, value)
                if issue:
                    issues.append(issue)
        
        # Validate deductible amounts
        issues.extend(self._validate_deductible_consistency(record))
        
        # Validate after_deductible flags
        for field_name in ["in_network_after_deductible", "out_of_network_after_deductible"]:
            value = getattr(record, field_name)
            if value and value not in ("Yes", "No"):
                issues.append(ValidationIssue(
                    record_index=0,
                    field_name=field_name,
                    issue_type="format_error",
                    message=f"{field_name} must be 'Yes' or 'No', got '{value}'",
                    severity="warning",
                    suggested_value="Yes" if value.lower() in ("y", "true", "1") else "No",
                ))
        
        # Validate preauth_required
        if record.preauth_required and record.preauth_required not in ("Yes", "No"):
            issues.append(ValidationIssue(
                record_index=0,
                field_name="preauth_required",
                issue_type="format_error",
                message=f"preauth_required must be 'Yes' or 'No', got '{record.preauth_required}'",
                severity="warning",
            ))
        
        # Validate confidence score
        if record.confidence_score < 0 or record.confidence_score > 1:
            issues.append(ValidationIssue(
                record_index=0,
                field_name="confidence_score",
                issue_type="out_of_range",
                message=f"Confidence score must be 0-1, got {record.confidence_score}",
                severity="error",
            ))
        
        return issues

    def validate_batch(
        self, records: List[BenefitRecord]
    ) -> Tuple[bool, List[ValidationIssue]]:
        """
        Validate a batch of benefit records.
        
        Args:
            records: List of BenefitRecords to validate
            
        Returns:
            Tuple of (all_valid, list_of_issues)
        """
        all_issues = []
        
        for idx, record in enumerate(records):
            issues = self.validate_benefit_record(record)
            # Update record_index for each issue
            for issue in issues:
                issue.record_index = idx
            all_issues.extend(issues)
        
        # Cross-record validations
        all_issues.extend(self._validate_cross_record_consistency(records))
        
        all_valid = not any(i.severity == "error" for i in all_issues)
        return all_valid, all_issues

    def add_rule(self, rule: Callable[[Any], bool]) -> None:
        """
        Add a custom validation rule.
        
        Args:
            rule: Function that takes data and returns True if valid
        """
        self.rules.append(rule)

    def _validate_dict(self, data: Dict[str, Any]) -> List[str]:
        """Validate dictionary data format."""
        errors = []
        
        # Check for required fields
        if "header" not in data and "service_category" not in data and "benefit_name" not in data:
            errors.append("Missing required field: header/service_category/benefit_name")
        
        return errors

    def _validate_benefit_record(self, record: BenefitRecord) -> List[str]:
        """Convert BenefitRecord validation to simple error strings."""
        issues = self.validate_benefit_record(record)
        return [issue.message for issue in issues if issue.severity == "error"]

    def _validate_coinsurance_format(
        self, field_name: str, value: str
    ) -> Optional[ValidationIssue]:
        """Validate coinsurance format."""
        # Valid formats: 0-100%, "NOT COVERED", "N/A"
        if value.upper() in ("NOT COVERED", "N/A"):
            return None
        
        # Check percentage format
        match = re.match(r"^(\d{1,3})%$", value)
        if match:
            pct = int(match.group(1))
            if 0 <= pct <= 100:
                return None
            return ValidationIssue(
                record_index=0,
                field_name=field_name,
                issue_type="out_of_range",
                message=f"Coinsurance percentage must be 0-100%, got {pct}%",
                severity="error",
                suggested_value=f"{min(100, max(0, pct))}%",
            )
        
        return ValidationIssue(
            record_index=0,
            field_name=field_name,
            issue_type="format_error",
            message=f"Invalid coinsurance format: '{value}'. Expected X%, 'NOT COVERED', or 'N/A'",
            severity="warning",
        )

    def _validate_copay_format(
        self, field_name: str, value: str
    ) -> Optional[ValidationIssue]:
        """Validate copay format."""
        # Valid formats: $X, $X.XX, $X per visit, etc.
        pattern = r"^\$[\d,]+(?:\.\d{2})?(?:\s+per\s+\w+)?$"
        if re.match(pattern, value):
            return None
        
        return ValidationIssue(
            record_index=0,
            field_name=field_name,
            issue_type="format_error",
            message=f"Invalid copay format: '{value}'. Expected $X or $X.XX format",
            severity="warning",
        )

    def _validate_deductible_consistency(
        self, record: BenefitRecord
    ) -> List[ValidationIssue]:
        """Validate deductible/OOP amount consistency."""
        issues = []
        
        # Extract numeric values
        def extract_amount(value: Optional[str]) -> Optional[float]:
            if not value or value.upper() in ("UNLIMITED", "N/A"):
                return None
            match = re.search(r"\$?([\d,]+(?:\.\d{2})?)", value)
            if match:
                return float(match.group(1).replace(",", ""))
            return None
        
        ind_in = extract_amount(record.individual_in_network)
        fam_in = extract_amount(record.family_in_network)
        ind_out = extract_amount(record.individual_out_of_network)
        fam_out = extract_amount(record.family_out_of_network)
        
        # Family >= Individual (in-network)
        if ind_in is not None and fam_in is not None:
            if fam_in < ind_in:
                issues.append(ValidationIssue(
                    record_index=0,
                    field_name="family_in_network",
                    issue_type="logical_error",
                    message=f"Family in-network (${fam_in:,.0f}) should be >= Individual (${ind_in:,.0f})",
                    severity="warning",
                ))
        
        # Family >= Individual (out-of-network)
        if ind_out is not None and fam_out is not None:
            if fam_out < ind_out:
                issues.append(ValidationIssue(
                    record_index=0,
                    field_name="family_out_of_network",
                    issue_type="logical_error",
                    message=f"Family out-of-network (${fam_out:,.0f}) should be >= Individual (${ind_out:,.0f})",
                    severity="warning",
                ))
        
        # Out-of-network >= In-network (typically)
        if ind_in is not None and ind_out is not None:
            if ind_out < ind_in:
                issues.append(ValidationIssue(
                    record_index=0,
                    field_name="individual_out_of_network",
                    issue_type="logical_warning",
                    message=f"OON individual (${ind_out:,.0f}) is less than in-network (${ind_in:,.0f}) - unusual",
                    severity="info",
                ))
        
        return issues

    def _validate_cross_record_consistency(
        self, records: List[BenefitRecord]
    ) -> List[ValidationIssue]:
        """Validate consistency across multiple records."""
        issues = []
        
        # Check for duplicate services
        seen_services = {}
        for idx, record in enumerate(records):
            key = (record.header.lower(), record.service.lower())
            if key in seen_services:
                issues.append(ValidationIssue(
                    record_index=idx,
                    field_name="service",
                    issue_type="duplicate",
                    message=f"Duplicate service '{record.service}' under '{record.header}' (first at index {seen_services[key]})",
                    severity="warning",
                ))
            else:
                seen_services[key] = idx
        
        return issues