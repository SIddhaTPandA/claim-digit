"""
Agent goal string for the Healthcare Benefits Extraction Specialist.
Kept separate so it can be referenced independently from the full backstory.
"""

AGENT_GOAL = (
    "Extract every benefit service, deductible, copay, coinsurance rate, visit limit, "
    "and out-of-pocket maximum that is explicitly stated in the SPD/SBC plan document. "
    "Map each item precisely to the 16-column output schema. "
    "Never invent or infer values that are not present in the source text. "
    "Return a strictly valid JSON array — no markdown, no explanation."
)
