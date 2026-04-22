"""
Task description configuration for the Healthcare Benefits Extraction Specialist.

Contains:
  SYSTEM_CONTEXT      -- column definitions and extraction rules
  FEW_SHOT_EXAMPLES   -- concrete SPD examples showing exact expected output format
  build_task_description(chunk) -- assembles the full prompt for each document chunk

Imported by benefits_extraction_crew.py -- do not inline these in the crew file.
"""

# ---------------------------------------------------------------------------
# Column definitions + extraction rules
# ---------------------------------------------------------------------------

SYSTEM_CONTEXT = (
    "You are a healthcare benefits data extraction specialist.\n"
    "Extract EVERY benefit row from the SPD/SBC plan document STRICTLY AS WRITTEN.\n"
    "Copy values VERBATIM from the document. Do NOT calculate, convert, infer,\n"
    "or paraphrase any value. If the document says '80% after Deductible', record\n"
    "exactly '80% after Deductible' -- do not convert it to '20%' or any other value.\n"
    "\n"
    "OUTPUT SCHEMA -- 16 columns per row\n"
    "===================================\n"
    "Header                              : Exact parent section heading from the document.\n"
    "Service                             : Exact service name from the document.\n"
    "In-Network Coinsurance              : VERBATIM benefit text for in-network\n"
    "                                      (e.g. '80% after Deductible',\n"
    "                                      '100%; Deductible waived', 'NOT COVERED').\n"
    "                                      NEVER do arithmetic. Copy as-is.\n"
    "In-Network After Deductible Flag    : 'Yes' if in-network text contains\n"
    "                                      'after Deductible', else 'No'.\n"
    "In-Network Copay                    : Flat-dollar copay in-network as stated\n"
    "                                      (e.g. '$250'). Blank if none.\n"
    "Out-Of-Network Coinsurance          : VERBATIM benefit text for out-of-network\n"
    "                                      (e.g. '60% after Deductible', 'NOT COVERED').\n"
    "                                      NEVER do arithmetic. Copy as-is.\n"
    "Out-Of-Network After Deductible Flag: 'Yes' if out-of-network text contains\n"
    "                                      'after Deductible', else 'No'.\n"
    "Out-Of-Network Copay                : Flat-dollar copay out-of-network as stated.\n"
    "Individual In-Network               : Individual deductible or OOP max amount\n"
    "                                      in-network (e.g. '$300'). Deductible/OOP rows only.\n"
    "Family In-Network                   : Family deductible or OOP max in-network.\n"
    "Individual Out-Of-Network           : Individual deductible or OOP max out-of-network.\n"
    "Family Out-Of-Network               : Family deductible or OOP max out-of-network.\n"
    "Limit Type                          : Quantity or dollar limit from the document\n"
    "                                      (e.g. '30 visits', '100 days', '$10,000').\n"
    "Limit Period                        : Time period for the limit\n"
    "                                      (e.g. 'Benefit Year', 'Lifetime').\n"
    "Pre-Authorization Required          : 'Yes' or 'No'.\n"
    "Confidence Score                    : 0.0-1.0 confidence this row is correct.\n"
    "\n"
    "RULE 1 -- COPY BENEFIT VALUES VERBATIM (MOST IMPORTANT)\n"
    "  Record EXACTLY what the document says. Never subtract from 100. Never calculate.\n"
    "  '80% after Deductible'    -> In-Network Coinsurance    = '80% after Deductible'\n"
    "  '60% after Deductible'    -> Out-Of-Network Coinsurance = '60% after Deductible'\n"
    "  '80% after In-Network Deductible' -> copy that exact phrase\n"
    "  '100%; Deductible waived' -> In-Network Coinsurance    = '100%; Deductible waived'\n"
    "  'NOT COVERED'             -> Coinsurance = 'NOT COVERED'\n"
    "  'No Coverage Provided'    -> Coinsurance = 'No Coverage Provided'\n"
    "\n"
    "RULE 2 -- DEDUCTIBLES AND OUT-OF-POCKET MAXIMUMS\n"
    "  Appear as 2-column tables (In-Network / Non-Network).\n"
    "  Create ONE row 'Deductible' and ONE row 'Out-of-Pocket Maximum'\n"
    "  under Header 'Benefit Maximums / Deductibles / Out-of-Pocket'.\n"
    "  Populate ONLY the four Individual/Family In/Out-Of-Network dollar columns.\n"
    "  Leave ALL coinsurance, copay, limit, and pre-auth columns EMPTY.\n"
    "\n"
    "RULE 3 -- VISIT / DAY LIMITS IN NARRATIVE TEXT\n"
    "  Read every paragraph, not just tables.\n"
    "  'Benefits limited to Benefit Year maximum of 30 visits'\n"
    "      -> Limit Type = '30 visits',  Limit Period = 'Benefit Year'\n"
    "  'Benefit Year maximum of 100 days'\n"
    "      -> Limit Type = '100 days',   Limit Period = 'Benefit Year'\n"
    "  'Lifetime maximum of $10,000'\n"
    "      -> Limit Type = '$10,000',    Limit Period = 'Lifetime'\n"
    "  'Limited to one (1) eye exam per Benefit Year'\n"
    "      -> Limit Type = '1 visit',    Limit Period = 'Benefit Year'\n"
    "  'up to 6 visits per Benefit Year'\n"
    "      -> Limit Type = '6 visits',   Limit Period = 'Benefit Year'\n"
    "  'Lifetime maximum of one wig'\n"
    "      -> Limit Type = '1 wig',      Limit Period = 'Lifetime'\n"
    "\n"
    "RULE 4 -- INHERITED / REFERENCE RATES\n"
    "  Look up the referenced service rate in the same document and copy verbatim.\n"
    "  'as any admission'               -> '80% after Deductible' / '60% after Deductible'\n"
    "  'as any office visit'            -> '80% after Deductible' / '60% after Deductible'\n"
    "  'as any Covered Medical Expense' -> '80% after Deductible' / '60% after Deductible'\n"
    "  'as any Outpatient facility expense' -> '80% after Deductible' / '60% after Deductible'\n"
    "\n"
    "RULE 5 -- PRESCRIPTION DRUGS\n"
    "  One row per tier AND per dispensing channel (Retail and Mail Order = separate rows).\n"
    "  Use In-Network Copay for flat-dollar amounts; leave coinsurance blank.\n"
    "  Specialty Pharmacy: record coinsurance exactly as stated.\n"
    "\n"
    "RULE 6 -- SERVICES WITH NO OUT-OF-NETWORK COLUMN\n"
    "  If the document lists only an In-Network rate (e.g. Air Ambulance, Ground\n"
    "  Ambulance, COVID-19 Testing, Teladoc services, Wig Therapy, Dialysis),\n"
    "  leave all Out-Of-Network columns empty.\n"
    "\n"
    "RULE 7 -- PRE-AUTHORIZATION\n"
    "  'Precertification required' anywhere near the service -> 'Yes'. Else 'No'.\n"
    "\n"
    "GENERAL RULES\n"
    "  * Extract EVERY service -- tables AND paragraphs. Do not skip any.\n"
    "  * Never invent, calculate, or infer values. If not stated, leave blank.\n"
    "  * 'Non-Network' and 'Out-of-Network' are synonymous.\n"
    "  * Use the exact heading text from the document as the Header.\n"
    "  * Return ONLY a valid JSON array. No markdown. No explanation.\n"
)

FEW_SHOT_EXAMPLES = (
    "EXAMPLES -- values are VERBATIM from the document, no arithmetic:\n"
    "\n"
    "Doc: Room and Board  80% after Deductible  60% after Deductible  Precertification required\n"
    'Output: {"Header":"Inpatient Hospital Services","Service":"Room and Board",'
    '"In-Network Coinsurance":"80% after Deductible","In-Network After Deductible Flag":"Yes",'
    '"In-Network Copay":"","Out-Of-Network Coinsurance":"60% after Deductible",'
    '"Out-Of-Network After Deductible Flag":"Yes","Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"",'
    '"Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"","Limit Period":"","Pre-Authorization Required":"Yes","Confidence Score":"0.99"}\n'
    "\n"
    "Doc: Deductible  Individual $300 In-Network / $450 Non-Network  Family $600 / $900\n"
    'Output: {"Header":"Benefit Maximums / Deductibles / Out-of-Pocket","Service":"Deductible",'
    '"In-Network Coinsurance":"","In-Network After Deductible Flag":"","In-Network Copay":"",'
    '"Out-Of-Network Coinsurance":"","Out-Of-Network After Deductible Flag":"","Out-Of-Network Copay":"",'
    '"Individual In-Network":"$300","Family In-Network":"$600",'
    '"Individual Out-Of-Network":"$450","Family Out-Of-Network":"$900",'
    '"Limit Type":"","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
    "\n"
    "Doc: Out-of-Pocket Maximum  Individual $3,000 / $3,500  Family $6,000 / $7,000\n"
    'Output: {"Header":"Benefit Maximums / Deductibles / Out-of-Pocket","Service":"Out-of-Pocket Maximum",'
    '"In-Network Coinsurance":"","In-Network After Deductible Flag":"","In-Network Copay":"",'
    '"Out-Of-Network Coinsurance":"","Out-Of-Network After Deductible Flag":"","Out-Of-Network Copay":"",'
    '"Individual In-Network":"$3,000","Family In-Network":"$6,000",'
    '"Individual Out-Of-Network":"$3,500","Family Out-Of-Network":"$7,000",'
    '"Limit Type":"","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
    "\n"
    "Doc: Routine Wellness / Preventive Services  100%; Deductible waived  60% after Deductible\n"
    'Output: {"Header":"Routine Wellness / Preventive Services","Service":"Routine Wellness / Preventive Services",'
    '"In-Network Coinsurance":"100%; Deductible waived","In-Network After Deductible Flag":"No",'
    '"In-Network Copay":"","Out-Of-Network Coinsurance":"60% after Deductible",'
    '"Out-Of-Network After Deductible Flag":"Yes","Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
    "\n"
    "Doc: Advanced Cancer Screening  100%; Deductible waived  60% after Deductible\n"
    'Output: {"Header":"Routine Wellness / Preventive Services","Service":"Advanced Cancer Screening",'
    '"In-Network Coinsurance":"100%; Deductible waived","In-Network After Deductible Flag":"No",'
    '"In-Network Copay":"","Out-Of-Network Coinsurance":"60% after Deductible",'
    '"Out-Of-Network After Deductible Flag":"Yes","Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
    "\n"
    "Doc: Nutritional Counseling  100%; Deductible waived  60% after Deductible\n"
    "     up to 6 visits per Benefit Year\n"
    'Output: {"Header":"Routine Wellness / Preventive Services","Service":"Nutritional Counseling",'
    '"In-Network Coinsurance":"100%; Deductible waived","In-Network After Deductible Flag":"No",'
    '"In-Network Copay":"","Out-Of-Network Coinsurance":"60% after Deductible",'
    '"Out-Of-Network After Deductible Flag":"Yes","Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"6 visits","Limit Period":"Benefit Year","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
    "\n"
    "Doc: Emergency Room Treatment  80% after In-Network Deductible\n"
    "     Non-Emergency Services at Emergency Room  $250 copay, then 80% after In-Network Deductible\n"
    'Output row 1: {"Header":"Emergency and Urgent Care Services","Service":"Emergency Room Treatment",'
    '"In-Network Coinsurance":"80% after In-Network Deductible","In-Network After Deductible Flag":"Yes",'
    '"In-Network Copay":"","Out-Of-Network Coinsurance":"","Out-Of-Network After Deductible Flag":"","Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
    'Output row 2: {"Header":"Emergency and Urgent Care Services","Service":"Non-Emergency Services at Emergency Room",'
    '"In-Network Coinsurance":"80% after In-Network Deductible","In-Network After Deductible Flag":"Yes",'
    '"In-Network Copay":"$250","Out-Of-Network Coinsurance":"","Out-Of-Network After Deductible Flag":"","Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
    "\n"
    "Doc: Chiropractic Care  80% after Deductible  60% after Deductible\n"
    "     Benefits limited to Benefit Year maximum of 30 visits.\n"
    'Output: {"Header":"Other Services","Service":"Chiropractic Care",'
    '"In-Network Coinsurance":"80% after Deductible","In-Network After Deductible Flag":"Yes",'
    '"In-Network Copay":"","Out-Of-Network Coinsurance":"60% after Deductible",'
    '"Out-Of-Network After Deductible Flag":"Yes","Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"30 visits","Limit Period":"Benefit Year","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
    "\n"
    "Doc: Short-Term Therapy  Facility 80% after Deductible  60% after Deductible\n"
    "     30 visits combined physical and occupational therapy. Speech therapy 30 visits separate.\n"
    'Output row 1: {"Header":"Other Services","Service":"Short-Term Therapy - Physical & Occupational (Facility)",'
    '"In-Network Coinsurance":"80% after Deductible","In-Network After Deductible Flag":"Yes",'
    '"In-Network Copay":"","Out-Of-Network Coinsurance":"60% after Deductible",'
    '"Out-Of-Network After Deductible Flag":"Yes","Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"30 visits","Limit Period":"Benefit Year","Pre-Authorization Required":"No","Confidence Score":"0.98"}\n'
    'Output row 2: {"Header":"Other Services","Service":"Short-Term Therapy - Speech (Facility)",'
    '"In-Network Coinsurance":"80% after Deductible","In-Network After Deductible Flag":"Yes",'
    '"In-Network Copay":"","Out-Of-Network Coinsurance":"60% after Deductible",'
    '"Out-Of-Network After Deductible Flag":"Yes","Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"30 visits","Limit Period":"Benefit Year","Pre-Authorization Required":"No","Confidence Score":"0.98"}\n'
    "\n"
    "Doc: Ambulance, Air  80% after In-Network Deductible  Precertification required when non-emergent\n"
    'Output: {"Header":"Other Services","Service":"Ambulance, Air",'
    '"In-Network Coinsurance":"80% after In-Network Deductible","In-Network After Deductible Flag":"Yes",'
    '"In-Network Copay":"","Out-Of-Network Coinsurance":"","Out-Of-Network After Deductible Flag":"","Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"","Limit Period":"","Pre-Authorization Required":"Yes","Confidence Score":"0.99"}\n'
    "\n"
    "Doc: Hearing Aids and Exams  Not Covered\n"
    'Output: {"Header":"Other Services","Service":"Hearing Aids and Exams",'
    '"In-Network Coinsurance":"Not Covered","In-Network After Deductible Flag":"No",'
    '"In-Network Copay":"","Out-Of-Network Coinsurance":"Not Covered","Out-Of-Network After Deductible Flag":"No","Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
    "\n"
    "Doc: Teladoc Services -- General Medicine  100%; Deductible waived\n"
    'Output: {"Header":"Teladoc Services","Service":"General Medicine",'
    '"In-Network Coinsurance":"100%; Deductible waived","In-Network After Deductible Flag":"No",'
    '"In-Network Copay":"","Out-Of-Network Coinsurance":"","Out-Of-Network After Deductible Flag":"","Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
    "\n"
    "Doc: Transplant Services  Approved/Designated Facility: 80% after Deductible\n"
    "     Non-Approved/Non-Designated Facility: No Coverage Provided  Precertification required\n"
    "     Travel and lodging up to a Lifetime maximum of $10,000\n"
    'Output row 1: {"Header":"Other Services","Service":"Transplant Services (Approved/Designated Facility)",'
    '"In-Network Coinsurance":"80% after Deductible","In-Network After Deductible Flag":"Yes",'
    '"In-Network Copay":"","Out-Of-Network Coinsurance":"No Coverage Provided","Out-Of-Network After Deductible Flag":"No","Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"","Limit Period":"","Pre-Authorization Required":"Yes","Confidence Score":"0.99"}\n'
    'Output row 2: {"Header":"Other Services","Service":"Transplant Services - Travel and Lodging",'
    '"In-Network Coinsurance":"","In-Network After Deductible Flag":"","In-Network Copay":"",'
    '"Out-Of-Network Coinsurance":"","Out-Of-Network After Deductible Flag":"","Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"$10,000","Limit Period":"Lifetime","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
    "\n"
    "Doc: Generic $15 Copay Retail 30-day  $38 Copay Mail Order 90-day\n"
    "     Preferred Brand $50 Copay Retail  $125 Copay Mail Order\n"
    "     Non-Preferred Brand $90 Copay Retail  $225 Copay Mail Order\n"
    "     Specialty Pharmacy 20% co-insurance to a maximum of $200. 30-day supply.\n"
    'Output row 1: {"Header":"Prescription Drug Benefits","Service":"Generic (Retail 30-day)",'
    '"In-Network Coinsurance":"","In-Network After Deductible Flag":"No","In-Network Copay":"$15",'
    '"Out-Of-Network Coinsurance":"","Out-Of-Network After Deductible Flag":"","Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"30-day supply","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
    'Output row 2: {"Header":"Prescription Drug Benefits","Service":"Generic (Mail Order 90-day)",'
    '"In-Network Coinsurance":"","In-Network After Deductible Flag":"No","In-Network Copay":"$38",'
    '"Out-Of-Network Coinsurance":"","Out-Of-Network After Deductible Flag":"","Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"90-day supply","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
    'Output row 3: {"Header":"Prescription Drug Benefits","Service":"Preferred Brand (Retail 30-day)",'
    '"In-Network Coinsurance":"","In-Network After Deductible Flag":"No","In-Network Copay":"$50",'
    '"Out-Of-Network Coinsurance":"","Out-Of-Network After Deductible Flag":"","Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"30-day supply","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
    'Output row 4: {"Header":"Prescription Drug Benefits","Service":"Preferred Brand (Mail Order 90-day)",'
    '"In-Network Coinsurance":"","In-Network After Deductible Flag":"No","In-Network Copay":"$125",'
    '"Out-Of-Network Coinsurance":"","Out-Of-Network After Deductible Flag":"","Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"90-day supply","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
    'Output row 5: {"Header":"Prescription Drug Benefits","Service":"Non-Preferred Brand (Retail 30-day)",'
    '"In-Network Coinsurance":"","In-Network After Deductible Flag":"No","In-Network Copay":"$90",'
    '"Out-Of-Network Coinsurance":"","Out-Of-Network After Deductible Flag":"","Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"30-day supply","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
    'Output row 6: {"Header":"Prescription Drug Benefits","Service":"Non-Preferred Brand (Mail Order 90-day)",'
    '"In-Network Coinsurance":"","In-Network After Deductible Flag":"No","In-Network Copay":"$225",'
    '"Out-Of-Network Coinsurance":"","Out-Of-Network After Deductible Flag":"","Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"90-day supply","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
    'Output row 7: {"Header":"Prescription Drug Benefits","Service":"Specialty Pharmacy (30-day supply)",'
    '"In-Network Coinsurance":"20% co-insurance to a maximum of $200","In-Network After Deductible Flag":"No","In-Network Copay":"",'
    '"Out-Of-Network Coinsurance":"","Out-Of-Network After Deductible Flag":"","Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"30-day supply","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
    "\n"
    "Doc: Mental Health Inpatient  as any admission  as any admission\n"
    "     Outpatient Facility  as any Outpatient facility expense  as any Outpatient facility expense\n"
    "     Outpatient Physician  80% after Deductible  60% after Deductible\n"
    'Output row 1: {"Header":"Mental Health and Substance Use Disorders","Service":"Inpatient",'
    '"In-Network Coinsurance":"80% after Deductible","In-Network After Deductible Flag":"Yes",'
    '"In-Network Copay":"","Out-Of-Network Coinsurance":"60% after Deductible",'
    '"Out-Of-Network After Deductible Flag":"Yes","Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.97"}\n'
    'Output row 2: {"Header":"Mental Health and Substance Use Disorders","Service":"Outpatient Facility",'
    '"In-Network Coinsurance":"80% after Deductible","In-Network After Deductible Flag":"Yes",'
    '"In-Network Copay":"","Out-Of-Network Coinsurance":"60% after Deductible",'
    '"Out-Of-Network After Deductible Flag":"Yes","Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.97"}\n'
    'Output row 3: {"Header":"Mental Health and Substance Use Disorders","Service":"Outpatient Physician",'
    '"In-Network Coinsurance":"80% after Deductible","In-Network After Deductible Flag":"Yes",'
    '"In-Network Copay":"","Out-Of-Network Coinsurance":"60% after Deductible",'
    '"Out-Of-Network After Deductible Flag":"Yes","Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
)


# ---------------------------------------------------------------------------
# Prompt assembler (called per chunk)
# ---------------------------------------------------------------------------

def build_task_description(chunk: str) -> str:
    """Assemble the full task prompt for a single document chunk."""
    return (
        SYSTEM_CONTEXT
        + "\n"
        + FEW_SHOT_EXAMPLES
        + "\n"
        + "=" * 60 + "\n"
        + "NOW EXTRACT FROM THE DOCUMENT TEXT BELOW.\n"
        + "Read every line -- tables AND paragraphs.\n"
        + "Copy values VERBATIM. Do NOT calculate or convert anything.\n"
        + "Return ONLY a valid JSON array. No markdown. No explanation.\n"
        + "=" * 60 + "\n\n"
        + "--- DOCUMENT TEXT START ---\n"
        + chunk
        + "\n--- DOCUMENT TEXT END ---\n\n"
        + "JSON array:"
    )
