"""
Confidence Scorer for SPD Benefits Extraction.

Calculates extraction confidence scores based on multiple factors
to determine if human review is needed.
"""

from typing import Any, Dict, List, Optional

from src.models.benefit_record import BenefitRecord, ValidationIssue


class ConfidenceScorer:
    """
    Calculates confidence scores for extracted benefit data.
    
    Scoring factors:
    - Document Intelligence extraction confidence
    - Field completeness
    - Format validation success
    - Pattern matching quality
    - Cross-field consistency
    """

    # Weights for different scoring components
    DEFAULT_WEIGHTS = {
        "extraction_confidence": 0.30,  # Base DI confidence
        "field_completeness": 0.25,     # Presence of expected fields
        "format_validation": 0.20,      # Correct formatting
        "pattern_matching": 0.15,       # Pattern recognition success
        "consistency": 0.10,            # Cross-field consistency
    }

    # Confidence threshold for auto-approval
    AUTO_APPROVAL_THRESHOLD = 0.70

    def __init__(
        self,
        weights: Optional[Dict[str, float]] = None,
        threshold: float = 0.70
    ):
        """
        Initialize the confidence scorer.
        
        Args:
            weights: Optional custom weights for scoring components
            threshold: Confidence threshold for auto-approval
        """
        self.weights = weights or self.DEFAULT_WEIGHTS
        self.threshold = threshold
        
        # Normalize weights to sum to 1.0
        total = sum(self.weights.values())
        if total != 1.0:
            self.weights = {k: v / total for k, v in self.weights.items()}

    def calculate_score(
        self,
        extracted_data: Dict[str, Any],
        ground_truth: Optional[Dict[str, Any]] = None
    ) -> float:
        """
        Calculate confidence score for extracted data.
        
        Args:
            extracted_data: Dictionary of extracted field values
            ground_truth: Optional ground truth for comparison (testing)
            
        Returns:
            Confidence score between 0.0 and 1.0
        """
        if not extracted_data:
            return 0.0
        
        # If ground truth provided, calculate match score
        if ground_truth:
            return self._calculate_match_score(extracted_data, ground_truth)
        
        # Otherwise calculate based on extraction quality
        scores = {}
        
        # Field completeness score
        scores["field_completeness"] = self._calculate_completeness_score(extracted_data)
        
        # Format validation score
        scores["format_validation"] = self._calculate_format_score(extracted_data)
        
        # Extraction confidence (if available)
        scores["extraction_confidence"] = extracted_data.get(
            "raw_confidence", 
            extracted_data.get("confidence_score", 0.85)
        )
        
        # Pattern matching quality
        scores["pattern_matching"] = self._calculate_pattern_score(extracted_data)
        
        # Consistency score
        scores["consistency"] = self._calculate_consistency_score(extracted_data)
        
        # Calculate weighted average
        total_score = sum(
            scores.get(key, 0.5) * weight
            for key, weight in self.weights.items()
        )
        
        return round(min(1.0, max(0.0, total_score)), 4)

    def score_benefit_record(self, record: BenefitRecord) -> float:
        """
        Calculate confidence score for a BenefitRecord.
        
        Args:
            record: BenefitRecord to score
            
        Returns:
            Confidence score between 0.0 and 1.0
        """
        scores = {}
        
        # Base extraction confidence
        scores["extraction_confidence"] = record.confidence_score
        
        # Field completeness
        scores["field_completeness"] = self._score_record_completeness(record)
        
        # Format validation
        scores["format_validation"] = self._score_record_format(record)
        
        # Pattern matching (based on raw text availability)
        scores["pattern_matching"] = self._score_pattern_matching(record)
        
        # Internal consistency
        scores["consistency"] = self._score_record_consistency(record)
        
        # Calculate weighted average
        total_score = sum(
            scores.get(key, 0.5) * weight
            for key, weight in self.weights.items()
        )
        
        return round(min(1.0, max(0.0, total_score)), 4)

    def score_batch(
        self,
        records: List[BenefitRecord],
        validation_issues: Optional[List[ValidationIssue]] = None
    ) -> Dict[str, Any]:
        """
        Score a batch of records and provide summary statistics.
        
        Args:
            records: List of BenefitRecords to score
            validation_issues: Optional validation issues to factor in
            
        Returns:
            Dictionary with individual scores and summary
        """
        if not records:
            return {
                "individual_scores": [],
                "average_score": 0.0,
                "min_score": 0.0,
                "max_score": 0.0,
                "below_threshold_count": 0,
                "requires_review": True,
            }
        
        # Calculate individual scores
        individual_scores = []
        for idx, record in enumerate(records):
            score = self.score_benefit_record(record)
            
            # Reduce score for validation issues
            if validation_issues:
                record_issues = [i for i in validation_issues if i.record_index == idx]
                error_count = sum(1 for i in record_issues if i.severity == "error")
                warning_count = sum(1 for i in record_issues if i.severity == "warning")
                
                # Deduct for issues
                score -= (error_count * 0.15 + warning_count * 0.05)
                score = max(0.0, score)
            
            individual_scores.append({
                "index": idx,
                "service": record.service,
                "score": round(score, 4),
                "below_threshold": score < self.threshold,
            })
        
        scores_list = [s["score"] for s in individual_scores]
        below_threshold = sum(1 for s in scores_list if s < self.threshold)
        
        return {
            "individual_scores": individual_scores,
            "average_score": round(sum(scores_list) / len(scores_list), 4),
            "min_score": round(min(scores_list), 4),
            "max_score": round(max(scores_list), 4),
            "below_threshold_count": below_threshold,
            "requires_review": below_threshold > 0 or min(scores_list) < self.threshold,
        }

    def requires_human_review(
        self,
        overall_confidence: float,
        validation_issues: Optional[List[ValidationIssue]] = None,
        low_confidence_record_count: int = 0
    ) -> tuple[bool, List[str]]:
        """
        Determine if human review is needed.
        
        Args:
            overall_confidence: Overall extraction confidence
            validation_issues: List of validation issues
            low_confidence_record_count: Number of records below threshold
            
        Returns:
            Tuple of (requires_review, list_of_reasons)
        """
        requires_review = False
        reasons = []
        
        # Check overall confidence
        if overall_confidence < self.threshold:
            requires_review = True
            reasons.append(f"Overall confidence ({overall_confidence:.1%}) below threshold ({self.threshold:.1%})")
        
        # Check for error-severity validation issues
        if validation_issues:
            error_count = sum(1 for i in validation_issues if i.severity == "error")
            if error_count > 0:
                requires_review = True
                reasons.append(f"{error_count} validation error(s) found")
        
        # Check for multiple low-confidence records
        if low_confidence_record_count > 0:
            requires_review = True
            reasons.append(f"{low_confidence_record_count} record(s) below confidence threshold")
        
        return requires_review, reasons

    def _calculate_match_score(
        self,
        extracted: Dict[str, Any],
        ground_truth: Dict[str, Any]
    ) -> float:
        """Calculate match score against ground truth."""
        if not ground_truth:
            return 0.0
        
        score = 0.0
        total_fields = len(ground_truth)
        
        for key in ground_truth:
            if key in extracted:
                if extracted[key] == ground_truth[key]:
                    score += 1.0
                elif self._is_partial_match(extracted[key], ground_truth[key]):
                    score += 0.5
        
        return score / total_fields if total_fields > 0 else 0.0

    def _is_partial_match(self, extracted: Any, expected: Any) -> bool:
        """Check for partial match between values."""
        if extracted is None or expected is None:
            return False
        
        # String comparison (case-insensitive, trimmed)
        if isinstance(extracted, str) and isinstance(expected, str):
            return extracted.strip().lower() == expected.strip().lower()
        
        return False

    def _calculate_completeness_score(self, data: Dict[str, Any]) -> float:
        """Calculate field completeness score."""
        # Key fields that should be present
        key_fields = [
            "header", "service", "service_category", "service_name",
            "in_network_coinsurance", "out_of_network_coinsurance",
            "in_network_text", "out_of_network_text",
        ]
        
        present = sum(1 for f in key_fields if data.get(f))
        return present / len(key_fields)

    def _calculate_format_score(self, data: Dict[str, Any]) -> float:
        """Calculate format validation score."""
        format_checks = 0
        format_passes = 0
        
        # Check coinsurance format
        for field in ["in_network_coinsurance", "out_of_network_coinsurance"]:
            if field in data and data[field]:
                format_checks += 1
                value = str(data[field]).upper()
                if value in ("NOT COVERED", "N/A") or (
                    value.endswith("%") and value[:-1].isdigit()
                ):
                    format_passes += 1
        
        # Check copay format
        for field in ["in_network_copay", "out_of_network_copay"]:
            if field in data and data[field]:
                format_checks += 1
                if str(data[field]).startswith("$"):
                    format_passes += 1
        
        return format_passes / format_checks if format_checks > 0 else 1.0

    def _calculate_pattern_score(self, data: Dict[str, Any]) -> float:
        """Calculate pattern matching quality score."""
        # Check if we have raw text that was successfully parsed
        has_raw = any(data.get(f) for f in ["in_network_text", "out_of_network_text", "raw_in_network_text"])
        has_parsed = any(data.get(f) for f in ["in_network_coinsurance", "in_network_copay"])
        
        if has_raw and has_parsed:
            return 1.0
        elif has_raw or has_parsed:
            return 0.7
        else:
            return 0.5

    def _calculate_consistency_score(self, data: Dict[str, Any]) -> float:
        """Calculate cross-field consistency score."""
        score = 1.0
        
        # If we have after_deductible flag, we should also have coinsurance
        for network in ["in_network", "out_of_network"]:
            has_deductible_flag = data.get(f"{network}_after_deductible")
            has_coinsurance = data.get(f"{network}_coinsurance")
            
            if has_deductible_flag and not has_coinsurance:
                score -= 0.2
        
        return max(0.0, score)

    def _score_record_completeness(self, record: BenefitRecord) -> float:
        """Score completeness of a BenefitRecord."""
        # Required fields
        required_score = 1.0 if record.header and record.service else 0.5
        
        # Benefit fields
        benefit_fields = [
            record.in_network_coinsurance,
            record.in_network_copay,
            record.out_of_network_coinsurance,
            record.out_of_network_copay,
        ]
        benefit_count = sum(1 for f in benefit_fields if f)
        benefit_score = benefit_count / 4.0
        
        return (required_score * 0.4) + (benefit_score * 0.6)

    def _score_record_format(self, record: BenefitRecord) -> float:
        """Score format correctness of a BenefitRecord."""
        checks = 0
        passes = 0
        
        # Coinsurance format
        for value in [record.in_network_coinsurance, record.out_of_network_coinsurance]:
            if value:
                checks += 1
                if value.upper() in ("NOT COVERED", "N/A") or (
                    value.endswith("%") and value[:-1].isdigit()
                ):
                    passes += 1
        
        # Copay format
        for value in [record.in_network_copay, record.out_of_network_copay]:
            if value:
                checks += 1
                if value.startswith("$"):
                    passes += 1
        
        # After deductible format
        for value in [record.in_network_after_deductible, record.out_of_network_after_deductible]:
            if value:
                checks += 1
                if value in ("Yes", "No"):
                    passes += 1
        
        return passes / checks if checks > 0 else 1.0

    def _score_pattern_matching(self, record: BenefitRecord) -> float:
        """Score pattern matching quality."""
        # Higher score if we have raw text AND parsed values
        has_raw = bool(record.raw_in_network_text or record.raw_out_of_network_text)
        has_parsed = bool(record.in_network_coinsurance or record.in_network_copay)
        
        if has_raw and has_parsed:
            return 1.0
        elif has_parsed:
            return 0.85
        elif has_raw:
            return 0.6
        else:
            return 0.5

    def _score_record_consistency(self, record: BenefitRecord) -> float:
        """Score internal consistency of a record."""
        score = 1.0
        
        # If coinsurance is NOT COVERED, copay should also be absent
        if record.in_network_coinsurance == "NOT COVERED":
            if record.in_network_copay:
                score -= 0.2
        
        if record.out_of_network_coinsurance == "NOT COVERED":
            if record.out_of_network_copay:
                score -= 0.2
        
        # If we have deductible amounts, service should be related to deductibles
        if record.individual_in_network or record.family_in_network:
            if "deductible" not in record.service.lower() and "oop" not in record.service.lower():
                score -= 0.1
        
        return max(0.0, score)