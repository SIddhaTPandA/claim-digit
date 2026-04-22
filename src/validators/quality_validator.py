"""
Generic Quality Validation Framework

Validates extracted Excel output without plan-specific assumptions.
This validator is designed to work with ANY benefit plan document type
(SPD, SBC, SOB, COC, PPO, HMO, HDHP, etc.) without hardcoded expectations.
"""

import pandas as pd
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
import re
import logging

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of a validation check."""
    rule_name: str
    passed: bool
    message: str
    severity: str = "warning"  # "error", "warning", "info"
    affected_rows: List[int] = field(default_factory=list)


@dataclass
class QualityReport:
    """Complete quality validation report."""
    file_path: str
    total_records: int
    schema_valid: bool
    validations: List[ValidationResult] = field(default_factory=list)
    
    @property
    def error_count(self) -> int:
        return sum(1 for v in self.validations if not v.passed and v.severity == "error")
    
    @property
    def warning_count(self) -> int:
        return sum(1 for v in self.validations if not v.passed and v.severity == "warning")
    
    @property
    def info_count(self) -> int:
        return sum(1 for v in self.validations if v.severity == "info" and not v.passed)
    
    @property
    def is_production_ready(self) -> bool:
        return self.schema_valid and self.error_count == 0
    
    def summary(self) -> str:
        """Generate a text summary of the report."""
        lines = [
            "=" * 60,
            "QUALITY VALIDATION REPORT",
            "=" * 60,
            f"File: {self.file_path}",
            f"Total Records: {self.total_records}",
            f"Schema Valid: {'Yes' if self.schema_valid else 'No'}",
            f"Production Ready: {'Yes' if self.is_production_ready else 'No'}",
            "",
            f"Errors: {self.error_count}",
            f"Warnings: {self.warning_count}",
            f"Info: {self.info_count}",
            "",
            "-" * 60,
            "VALIDATION DETAILS",
            "-" * 60,
        ]
        
        for v in self.validations:
            status = "PASS" if v.passed else v.severity.upper()
            lines.append(f"[{status}] {v.rule_name}: {v.message}")
            if v.affected_rows and len(v.affected_rows) <= 5:
                lines.append(f"       Affected rows: {v.affected_rows}")
            elif v.affected_rows:
                lines.append(f"       Affected rows: {v.affected_rows[:5]}... (+{len(v.affected_rows)-5} more)")
        
        lines.append("=" * 60)
        return "\n".join(lines)


class QualityValidator:
    """
    Generic quality validator for extracted benefit data.
    
    Does NOT make plan-specific assumptions about coinsurance values.
    Validates data format, consistency, and completeness without
    assuming what specific percentages or values should be.
    """
    
    REQUIRED_COLUMNS = [
        "Header",
        "Service",
        "In-Network Coinsurance",
        "In-Network After Deductible Flag",
        "In-Network Copay",
        "Out-Of-Network Coinsurance",
        "Out-Of-Network After Deductible Flag",
        "Out-Of-Network Copay",
        "Individual In-Network",
        "Family In-Network",
        "Individual Out-Of-Network",
        "Family Out-Of-Network",
        "Limit Type",
        "Limit Period",
        "Pre-Authorization Required",
    ]
    
    # Confidence Score is optional (may not be in all outputs)
    OPTIONAL_COLUMNS = ["Confidence Score"]
    
    def __init__(self, confidence_threshold: float = 0.70):
        """
        Initialize the validator.
        
        Args:
            confidence_threshold: Minimum confidence score for records (0.0-1.0)
        """
        self.confidence_threshold = confidence_threshold
    
    def validate(self, excel_path: Path) -> QualityReport:
        """
        Run all validations on an Excel file.
        
        Args:
            excel_path: Path to the Excel file to validate
            
        Returns:
            QualityReport with all validation results
        """
        df = pd.read_excel(excel_path)
        
        report = QualityReport(
            file_path=str(excel_path),
            total_records=len(df),
            schema_valid=True
        )
        
        # Schema validation
        schema_result = self._validate_schema(df)
        report.validations.append(schema_result)
        report.schema_valid = schema_result.passed
        
        if not report.schema_valid:
            return report  # Can't continue without valid schema
        
        # Data quality validations
        report.validations.extend([
            self._validate_service_names(df),
            self._validate_header_values(df),
            self._validate_coinsurance_format(df),
            self._validate_copay_format(df),
            self._validate_deductible_flags(df),
            self._validate_network_consistency(df),
            self._validate_confidence_scores(df),
            self._validate_limit_consistency(df),
            self._validate_preauth_values(df),
            self._validate_no_empty_rows(df),
            self._validate_no_duplicate_services(df),
        ])
        
        return report
    
    def _validate_schema(self, df: pd.DataFrame) -> ValidationResult:
        """Validate that all required columns are present."""
        missing = [col for col in self.REQUIRED_COLUMNS if col not in df.columns]
        
        if missing:
            return ValidationResult(
                rule_name="schema_validation",
                passed=False,
                message=f"Missing columns: {missing}",
                severity="error"
            )
        return ValidationResult(
            rule_name="schema_validation",
            passed=True,
            message=f"All {len(self.REQUIRED_COLUMNS)} required columns present"
        )
    
    def _validate_service_names(self, df: pd.DataFrame) -> ValidationResult:
        """Validate that all records have service names."""
        empty_services = df["Service"].isna() | (df["Service"] == "")
        empty_indices = df[empty_services].index.tolist()
        
        if empty_indices:
            return ValidationResult(
                rule_name="service_names_present",
                passed=False,
                message=f"{len(empty_indices)} records missing service names",
                severity="error",
                affected_rows=empty_indices
            )
        return ValidationResult(
            rule_name="service_names_present",
            passed=True,
            message="All records have service names"
        )
    
    def _validate_header_values(self, df: pd.DataFrame) -> ValidationResult:
        """Validate that all records have header/category values."""
        empty_headers = df["Header"].isna() | (df["Header"] == "")
        empty_indices = df[empty_headers].index.tolist()
        
        if empty_indices:
            return ValidationResult(
                rule_name="header_values_present",
                passed=False,
                message=f"{len(empty_indices)} records missing header/category",
                severity="warning",
                affected_rows=empty_indices
            )
        return ValidationResult(
            rule_name="header_values_present",
            passed=True,
            message="All records have header/category values"
        )
    
    def _validate_coinsurance_format(self, df: pd.DataFrame) -> ValidationResult:
        """Validate coinsurance values are valid percentages or special values."""
        valid_special = {"NOT COVERED", "N/A", "VARIES", "SEE NOTES", ""}
        invalid_rows = []
        
        for col in ["In-Network Coinsurance", "Out-Of-Network Coinsurance"]:
            if col not in df.columns:
                continue
            for idx, val in df[col].items():
                if pd.isna(val):
                    continue
                val_str = str(val).strip().upper()
                if val_str in valid_special:
                    continue
                # Check percentage format
                if not re.match(r"^\d{1,3}%?$", val_str.replace("%", "")):
                    invalid_rows.append(idx)
                else:
                    # Validate range
                    pct_match = re.search(r"(\d+)", val_str)
                    if pct_match:
                        pct = int(pct_match.group(1))
                        if pct > 100:
                            invalid_rows.append(idx)
        
        if invalid_rows:
            return ValidationResult(
                rule_name="coinsurance_format",
                passed=False,
                message=f"{len(set(invalid_rows))} records have invalid coinsurance format",
                severity="warning",
                affected_rows=list(set(invalid_rows))
            )
        return ValidationResult(
            rule_name="coinsurance_format",
            passed=True,
            message="All coinsurance values have valid format"
        )
    
    def _validate_copay_format(self, df: pd.DataFrame) -> ValidationResult:
        """Validate copay values are valid dollar amounts or special values."""
        valid_special = {"N/A", "NOT COVERED", "VARIES", "SEE NOTES", "", "NONE"}
        invalid_rows = []
        
        for col in ["In-Network Copay", "Out-Of-Network Copay"]:
            if col not in df.columns:
                continue
            for idx, val in df[col].items():
                if pd.isna(val):
                    continue
                val_str = str(val).strip().upper()
                if val_str in valid_special:
                    continue
                # Check dollar format: $X, $X.XX, $X,XXX, or with "per day" etc.
                # Also accept plain numbers
                if not re.match(r"^\$?[\d,]+(\.\d{2})?\s*(per\s+\w+)?$", val_str, re.IGNORECASE):
                    # Check if it's a more complex format like "$50 per visit"
                    if not re.search(r"\$[\d,]+", val_str):
                        invalid_rows.append(idx)
        
        if invalid_rows:
            return ValidationResult(
                rule_name="copay_format",
                passed=False,
                message=f"{len(set(invalid_rows))} records have invalid copay format",
                severity="warning",
                affected_rows=list(set(invalid_rows))
            )
        return ValidationResult(
            rule_name="copay_format",
            passed=True,
            message="All copay values have valid format"
        )
    
    def _validate_deductible_flags(self, df: pd.DataFrame) -> ValidationResult:
        """Validate deductible flags are Yes/No/None."""
        valid_values = {"YES", "NO", "Y", "N", "TRUE", "FALSE", ""}
        invalid_rows = []
        
        for col in ["In-Network After Deductible Flag", "Out-Of-Network After Deductible Flag"]:
            if col not in df.columns:
                continue
            for idx, val in df[col].items():
                if pd.isna(val):
                    continue
                val_str = str(val).strip().upper()
                if val_str not in valid_values:
                    invalid_rows.append(idx)
        
        if invalid_rows:
            return ValidationResult(
                rule_name="deductible_flag_format",
                passed=False,
                message=f"{len(set(invalid_rows))} records have invalid deductible flag values",
                severity="warning",
                affected_rows=list(set(invalid_rows))
            )
        return ValidationResult(
            rule_name="deductible_flag_format",
            passed=True,
            message="All deductible flags have valid format"
        )
    
    def _validate_network_consistency(self, df: pd.DataFrame) -> ValidationResult:
        """
        Validate network value consistency.
        
        NOTE: We do NOT assume IN >= OON because:
        1. Document might use "you pay" perspective
        2. Some plans have unusual structures
        
        Instead, we flag records where IN and OON are identical (potential extraction issue).
        """
        identical_rows = []
        
        in_col = "In-Network Coinsurance"
        out_col = "Out-Of-Network Coinsurance"
        
        if in_col not in df.columns or out_col not in df.columns:
            return ValidationResult(
                rule_name="network_consistency",
                passed=True,
                message="Network columns not present, skipping check"
            )
        
        for idx, row in df.iterrows():
            in_val = str(row.get(in_col, "")).strip()
            out_val = str(row.get(out_col, "")).strip()
            
            # Skip if either is empty or special value
            if not in_val or not out_val:
                continue
            if in_val.upper() in {"NOT COVERED", "N/A", "VARIES"}:
                continue
            if out_val.upper() in {"NOT COVERED", "N/A", "VARIES"}:
                continue
            
            # Flag if identical (unusual for most plans - worth review)
            if in_val == out_val:
                identical_rows.append(idx)
        
        if identical_rows:
            return ValidationResult(
                rule_name="network_consistency",
                passed=True,  # Not a failure, just informational
                message=f"{len(identical_rows)} records have identical IN/OON values (review recommended)",
                severity="info",
                affected_rows=identical_rows
            )
        return ValidationResult(
            rule_name="network_consistency",
            passed=True,
            message="Network values show expected variation"
        )
    
    def _validate_confidence_scores(self, df: pd.DataFrame) -> ValidationResult:
        """Validate confidence scores are within range and above threshold."""
        if "Confidence Score" not in df.columns:
            return ValidationResult(
                rule_name="confidence_scores",
                passed=True,
                message="Confidence Score column not present (optional)"
            )
        
        low_confidence_rows = []
        invalid_rows = []
        
        for idx, val in df["Confidence Score"].items():
            if pd.isna(val):
                continue
            try:
                score = float(val)
                if score < 0 or score > 1:
                    invalid_rows.append(idx)
                elif score < self.confidence_threshold:
                    low_confidence_rows.append(idx)
            except (ValueError, TypeError):
                invalid_rows.append(idx)
        
        if invalid_rows:
            return ValidationResult(
                rule_name="confidence_scores",
                passed=False,
                message=f"{len(invalid_rows)} records have invalid confidence scores",
                severity="error",
                affected_rows=invalid_rows
            )
        
        if low_confidence_rows:
            return ValidationResult(
                rule_name="confidence_scores",
                passed=True,
                message=f"{len(low_confidence_rows)} records below {self.confidence_threshold:.0%} threshold (review recommended)",
                severity="info",
                affected_rows=low_confidence_rows
            )
        
        return ValidationResult(
            rule_name="confidence_scores",
            passed=True,
            message="All confidence scores valid and above threshold"
        )
    
    def _validate_limit_consistency(self, df: pd.DataFrame) -> ValidationResult:
        """Validate that family limits >= individual limits when both present."""
        violations = []
        
        limit_pairs = [
            ("Individual In-Network", "Family In-Network"),
            ("Individual Out-Of-Network", "Family Out-Of-Network"),
        ]
        
        for ind_col, fam_col in limit_pairs:
            if ind_col not in df.columns or fam_col not in df.columns:
                continue
            
            for idx, row in df.iterrows():
                ind_val = self._extract_dollar_amount(row.get(ind_col, ""))
                fam_val = self._extract_dollar_amount(row.get(fam_col, ""))
                
                if ind_val is not None and fam_val is not None:
                    if fam_val < ind_val:
                        violations.append(idx)
        
        if violations:
            return ValidationResult(
                rule_name="limit_consistency",
                passed=False,
                message=f"{len(set(violations))} records have family limit < individual limit",
                severity="warning",
                affected_rows=list(set(violations))
            )
        return ValidationResult(
            rule_name="limit_consistency",
            passed=True,
            message="All limit values are consistent"
        )
    
    def _validate_preauth_values(self, df: pd.DataFrame) -> ValidationResult:
        """Validate pre-authorization values are Yes/No/None."""
        if "Pre-Authorization Required" not in df.columns:
            return ValidationResult(
                rule_name="preauth_values",
                passed=True,
                message="Pre-Authorization Required column not present"
            )
        
        valid_values = {"YES", "NO", "Y", "N", "TRUE", "FALSE", "REQUIRED", "NOT REQUIRED", ""}
        invalid_rows = []
        
        for idx, val in df["Pre-Authorization Required"].items():
            if pd.isna(val):
                continue
            val_str = str(val).strip().upper()
            if val_str not in valid_values:
                invalid_rows.append(idx)
        
        if invalid_rows:
            return ValidationResult(
                rule_name="preauth_values",
                passed=False,
                message=f"{len(invalid_rows)} records have invalid pre-auth values",
                severity="warning",
                affected_rows=invalid_rows
            )
        return ValidationResult(
            rule_name="preauth_values",
            passed=True,
            message="All pre-authorization values have valid format"
        )
    
    def _validate_no_empty_rows(self, df: pd.DataFrame) -> ValidationResult:
        """Validate that no rows are completely empty (except header/service)."""
        value_columns = [
            "In-Network Coinsurance", "Out-Of-Network Coinsurance",
            "In-Network Copay", "Out-Of-Network Copay"
        ]
        
        empty_rows = []
        for idx, row in df.iterrows():
            all_empty = True
            for col in value_columns:
                if col in df.columns:
                    val = row.get(col)
                    if pd.notna(val) and str(val).strip():
                        all_empty = False
                        break
            if all_empty:
                empty_rows.append(idx)
        
        if empty_rows:
            return ValidationResult(
                rule_name="no_empty_rows",
                passed=False,
                message=f"{len(empty_rows)} records have no benefit values",
                severity="warning",
                affected_rows=empty_rows
            )
        return ValidationResult(
            rule_name="no_empty_rows",
            passed=True,
            message="All records have at least one benefit value"
        )
    
    def _validate_no_duplicate_services(self, df: pd.DataFrame) -> ValidationResult:
        """Validate that there are no exact duplicate service names."""
        if "Service" not in df.columns:
            return ValidationResult(
                rule_name="no_duplicate_services",
                passed=True,
                message="Service column not present"
            )
        
        # Group by service name and find duplicates
        service_counts = df["Service"].value_counts()
        duplicates = service_counts[service_counts > 1]
        
        if len(duplicates) > 0:
            # Get indices of duplicates
            duplicate_indices = []
            for service in duplicates.index:
                indices = df[df["Service"] == service].index.tolist()
                duplicate_indices.extend(indices)
            
            return ValidationResult(
                rule_name="no_duplicate_services",
                passed=True,  # Not necessarily an error - some plans have multiple entries
                message=f"{len(duplicates)} services appear multiple times (may be intentional)",
                severity="info",
                affected_rows=duplicate_indices
            )
        return ValidationResult(
            rule_name="no_duplicate_services",
            passed=True,
            message="No duplicate service names found"
        )
    
    @staticmethod
    def _extract_dollar_amount(value: Any) -> Optional[float]:
        """Extract numeric dollar amount from a string."""
        if pd.isna(value):
            return None
        val_str = str(value).strip()
        match = re.search(r"\$?([\d,]+(?:\.\d{2})?)", val_str)
        if match:
            try:
                return float(match.group(1).replace(",", ""))
            except ValueError:
                return None
        return None


def validate_excel(excel_path: str, confidence_threshold: float = 0.70) -> QualityReport:
    """
    Convenience function to validate an Excel file.
    
    Args:
        excel_path: Path to the Excel file
        confidence_threshold: Minimum confidence threshold (0.0-1.0)
        
    Returns:
        QualityReport with validation results
    """
    validator = QualityValidator(confidence_threshold=confidence_threshold)
    return validator.validate(Path(excel_path))


def validate_all_in_directory(
    directory: str, 
    confidence_threshold: float = 0.70,
    print_summary: bool = True
) -> List[QualityReport]:
    """
    Validate all Excel files in a directory.
    
    Args:
        directory: Path to directory containing Excel files
        confidence_threshold: Minimum confidence threshold (0.0-1.0)
        print_summary: Whether to print a summary to stdout
        
    Returns:
        List of QualityReport objects
    """
    dir_path = Path(directory)
    excel_files = list(dir_path.glob("*.xlsx"))
    
    if print_summary:
        print(f"Found {len(excel_files)} Excel files in {directory}")
        print("=" * 60)
    
    reports = []
    for excel_file in sorted(excel_files):
        report = validate_excel(str(excel_file), confidence_threshold)
        reports.append(report)
        
        if print_summary:
            status = "READY" if report.is_production_ready else "REVIEW"
            print(f"[{status}] {excel_file.name}: {report.total_records} records, "
                  f"{report.error_count} errors, {report.warning_count} warnings")
    
    if print_summary:
        print("=" * 60)
        ready_count = sum(1 for r in reports if r.is_production_ready)
        print(f"Production Ready: {ready_count}/{len(reports)}")
    
    return reports


# CLI usage
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python quality_validator.py <excel_file_or_directory>")
        print("       python quality_validator.py ../OutputExcel/test_run/")
        print("       python quality_validator.py ../OutputExcel/test_run/document.xlsx")
        sys.exit(1)
    
    path = Path(sys.argv[1])
    
    if path.is_dir():
        reports = validate_all_in_directory(str(path))
    elif path.is_file():
        report = validate_excel(str(path))
        print(report.summary())
    else:
        print(f"Error: Path not found: {path}")
        sys.exit(1)
