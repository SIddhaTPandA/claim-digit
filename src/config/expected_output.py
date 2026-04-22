"""
Expected output description for the CrewAI Task used in benefits extraction.
Imported by benefits_extraction_crew.py — do not inline this string there.
"""

TASK_EXPECTED_OUTPUT = (
    "A valid JSON array where every element is a benefit record object with exactly "
    "these 16 keys: "
    "'Header', 'Service', "
    "'In-Network Coinsurance', 'In-Network After Deductible Flag', 'In-Network Copay', "
    "'Out-Of-Network Coinsurance', 'Out-Of-Network After Deductible Flag', 'Out-Of-Network Copay', "
    "'Individual In-Network', 'Family In-Network', "
    "'Individual Out-Of-Network', 'Family Out-Of-Network', "
    "'Limit Type', 'Limit Period', "
    "'Pre-Authorization Required', 'Confidence Score'.\n\n"
    "Accuracy requirements:\n"
    "  - Every value must be taken verbatim from the plan document — no invented data.\n"
    "  - Coinsurance values reflect the MEMBER's share (e.g. plan pays 80% → record '20%').\n"
    "  - Deductible and OOP maximum rows populate only the Individual/Family columns.\n"
    "  - Visit/day limits found in narrative paragraphs must be captured in Limit Type "
    "and Limit Period.\n"
    "  - Prescription drug tiers produce separate rows for Retail and Mail Order channels.\n"
    "  - No markdown, no explanation — pure JSON array only."
)
