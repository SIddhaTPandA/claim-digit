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
    "Service                             : The SHORT subheader label only -- exactly as it\n"
    "                                      appears at the START of the benefit row, before any\n"
    "                                      description, narrative, or explanatory text.\n"
    "                                      Strip everything after the benefit rate value or any\n"
    "                                      explanatory sentence that follows the service label.\n"
    "                                      CORRECT: 'Room and Board'\n"
    "                                      WRONG  : 'Room and Board Precertification required Includes\n"
    "                                               the medical services and supplies furnished by...'\n"
    "                                      CORRECT: 'Emergency Room Treatment including related services'\n"
    "                                      CORRECT: 'Non-Emergency Services at Emergency Room'\n"
    "                                      CORRECT: 'Outpatient / Ambulatory Surgery - Facility'\n"
    "                                      If the STRUCTURED TABLES section is present, use the\n"
    "                                      first cell of the row as the Service name verbatim.\n"
    "                                      If a benefit appears as a section heading with no\n"
    "                                      distinct sub-label, use the heading text as BOTH\n"
    "                                      Header and Service.\n"
    "                                      If a label reads 'Section -- Subservice', use only\n"
    "                                      the subservice part for Service (e.g. 'Teladoc\n"
    "                                      Services -- General Medicine' -> Service = 'General\n"
    "                                      Medicine', Header = 'Teladoc Services').\n"
    "                                      Exception: when a row covers multiple sub-types\n"
    "                                      described in narrative (e.g. physical vs. speech\n"
    "                                      therapy with separate visit limits), split into\n"
    "                                      separate rows and construct Service by combining the\n"
    "                                      original label with the sub-type.\n"
    "In-Network Coinsurance              : VERBATIM benefit text for in-network\n"
    "                                      (e.g. '80% after Deductible',\n"
    "                                      '100%; Deductible waived', 'NOT COVERED').\n"
    "                                      NEVER do arithmetic. Copy as-is.\n"
    "In-Network After Deductible Flag    : 'Yes' if the benefit requires satisfying the\n"
    "                                      deductible first -- text contains 'after deductible',\n"
    "                                      'after the deductible', 'after the plan deductible',\n"
    "                                      'deductible then', or equivalent (case-insensitive).\n"
    "                                      'No' otherwise. Apply the same logic to 'No charge\n"
    "                                      after deductible' (Flag = 'Yes').\n"
    "In-Network Copay                    : Dollar amount ONLY -- strip any unit suffix\n"
    "                                      ('/visit', '/prescription', '/day', '/admission').\n"
    "                                      '$30 copay/visit' -> '$30'. Blank if none.\n"
    "Out-Of-Network Coinsurance          : VERBATIM benefit text for out-of-network\n"
    "                                      (e.g. '60% after Deductible', 'NOT COVERED').\n"
    "                                      NEVER do arithmetic. Copy as-is.\n"
    "Out-Of-Network After Deductible Flag: Same 'Yes'/'No' logic as In-Network flag above.\n"
    "Out-Of-Network Copay                : Dollar amount ONLY -- same stripping rules as\n"
    "                                      In-Network Copay. Blank if none.\n"
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
    "  This applies to BOTH plan-perspective phrasing ('plan pays 80%', 'After the plan\n"
    "  deductible is met, your plan pays 80%') AND member-perspective phrasing\n"
    "  ('80% after Deductible', '20% coinsurance'). Copy whichever the document uses.\n"
    "  '80% after Deductible'    -> In-Network Coinsurance    = '80% after Deductible'\n"
    "  '60% after Deductible'    -> Out-Of-Network Coinsurance = '60% after Deductible'\n"
    "  '80% after In-Network Deductible' -> copy that exact phrase\n"
    "  '100%; Deductible waived' -> In-Network Coinsurance    = '100%; Deductible waived'\n"
    "  'After the plan deductible is met, your plan pays 80%' -> copy that exact phrase\n"
    "  'NOT COVERED'             -> Coinsurance = 'NOT COVERED'\n"
    "  'No Coverage Provided'    -> Coinsurance = 'No Coverage Provided'\n"
    "\n"
    "RULE 2 -- DEDUCTIBLES AND OUT-OF-POCKET MAXIMUMS\n"
    "  Create ONE row 'Deductible' and ONE row 'Out-of-Pocket Maximum'.\n"
    "  ALWAYS use the normalized Header 'Benefit Maximums / Deductibles / Out-of-Pocket'\n"
    "  regardless of the document's actual section heading.\n"
    "  Populate ONLY the four Individual/Family In/Out-Of-Network dollar columns.\n"
    "  Leave ALL coinsurance, copay, and limit columns EMPTY.\n"
    "  Set Pre-Authorization Required = 'No'.\n"
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
    "  Look up the referenced service rate IN THE CURRENT DOCUMENT and copy it verbatim.\n"
    "  NEVER assume a rate -- always read it from the document you are processing.\n"
    "  'as any admission'               -> find the Inpatient/Room-and-Board rate in this\n"
    "                                      document and copy it (e.g. '80% after Deductible')\n"
    "  'as any office visit'            -> find the Office Visit rate in this document\n"
    "  'as any Covered Medical Expense' -> find the general medical benefit rate\n"
    "  'as any Outpatient facility expense' -> find the Outpatient Facility rate\n"
    "\n"
    "RULE 5 -- PRESCRIPTION DRUGS\n"
    "  One row per tier AND per dispensing channel (Retail and Mail Order = separate rows).\n"
    "  Use In-Network Copay for flat-dollar amounts; leave Coinsurance blank.\n"
    "  If a tier uses percentage coinsurance instead of a flat copay, populate\n"
    "  In-Network Coinsurance instead of In-Network Copay.\n"
    "  If both apply (e.g. '$50 copay or 25% coinsurance, whichever is greater'),\n"
    "  populate both columns.\n"
    "  Specialty Pharmacy: record coinsurance exactly as stated.\n"
    "\n"
    "RULE 6 -- SERVICES WITH NO OUT-OF-NETWORK COLUMN\n"
    "  If the document explicitly lists only an In-Network rate with no Out-of-Network\n"
    "  benefit (e.g. Air Ambulance, COVID-19 Testing, Teladoc services, Wig Therapy),\n"
    "  leave all Out-Of-Network columns empty. Do not assume a service is In-Network-only\n"
    "  -- check the document. Ground Ambulance, for example, often has OON coverage.\n"
    "\n"
    "RULE 7 -- PRE-AUTHORIZATION\n"
    "  'Yes' if any of these phrases appear near the service (case-insensitive):\n"
    "  'Precertification required', 'Preauthorization required',\n"
    "  'Pre-authorization required', 'Prior authorization required',\n"
    "  'PA required', 'requires prior approval', 'must obtain approval'.\n"
    "  Otherwise 'No'.\n"
    "\n"
    "RULE 8 -- MERGED CELLS (same value applies to BOTH In-Network and Out-of-Network)\n"
    "  The document text BEGINS WITH a STRUCTURED TABLES section (before the raw paragraph\n"
    "  text) where every merged/spanning cell has already been duplicated into each column\n"
    "  it spans. Always read this section first.\n"
    "  When you see the same value in BOTH the In-Network and Non-Network columns of a\n"
    "  structured table row, populate BOTH In-Network Coinsurance AND Out-Of-Network\n"
    "  Coinsurance with that verbatim value.\n"
    "  Example: | Emergency Room Treatment | 80% after In-Network Deductible | 80% after In-Network Deductible |\n"
    "    -> In-Network Coinsurance    = '80% after In-Network Deductible'\n"
    "    -> Out-Of-Network Coinsurance = '80% after In-Network Deductible'\n"
    "  NEVER leave Out-Of-Network blank just because the raw text showed the value only once.\n"
    "  Always cross-check the STRUCTURED TABLES section for the correct column values.\n"
    "\n"
    "GENERAL RULES\n"
    "  * Extract EVERY service -- tables AND paragraphs. Do not skip any.\n"
    "  * Never invent, calculate, or infer values. If not stated, leave blank.\n"
    "  * 'Non-Network' and 'Out-of-Network' are synonymous.\n"
    "  * Use the exact heading text from the document as the Header (except deductible/OOP\n"
    "    rows which always use 'Benefit Maximums / Deductibles / Out-of-Pocket' per RULE 2).\n"
    "  * Confidence Score is a quoted string ('0.99', not 0.99).\n"
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
    "Doc: Hearing Aids and Exams  Not Covered  Not Covered\n"
    "     (document shows 'Not Covered' in both the In-Network and Out-of-Network columns)\n"
    'Output: {"Header":"Other Services","Service":"Hearing Aids and Exams",'
    '"In-Network Coinsurance":"Not Covered","In-Network After Deductible Flag":"No",'
    '"In-Network Copay":"","Out-Of-Network Coinsurance":"Not Covered","Out-Of-Network After Deductible Flag":"No","Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
    "\n"
    "\n"
    "Doc (merged-cell table): | Emergency Room Treatment | 80% after In-Network Deductible | 80% after In-Network Deductible |\n"
    "  (The value appears in BOTH the In-Network and Non-Network columns because the PDF\n"
    "   used a merged cell spanning both columns -- treat each column independently.)\n"
    'Output: {"Header":"Emergency and Urgent Care Services","Service":"Emergency Room Treatment",'
    '"In-Network Coinsurance":"80% after In-Network Deductible","In-Network After Deductible Flag":"Yes",'
    '"In-Network Copay":"","Out-Of-Network Coinsurance":"80% after In-Network Deductible",'
    '"Out-Of-Network After Deductible Flag":"Yes","Out-Of-Network Copay":"",'
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
    "Doc: Room and Board  80% after Deductible  60% after Deductible\n"
    "     Outpatient Surgery Facility  80% after Deductible  60% after Deductible\n"
    "     Mental Health Inpatient  as any admission  as any admission\n"
    "     Outpatient Facility  as any Outpatient facility expense  as any Outpatient facility expense\n"
    "     Outpatient Physician  80% after Deductible  60% after Deductible\n"
    "     (RULE 4: look up 'as any admission' -> found Room and Board = '80% after Deductible')\n"
    "     (RULE 4: look up 'as any Outpatient facility expense' -> found '80% after Deductible')\n"
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
# Additional rules for plan-pays (Cigna-style) SPD documents
# ---------------------------------------------------------------------------

_PLAN_PAYS_EXTRA_RULES = (
    "DOCUMENT NOTE: This document expresses benefit values from the PLAN's perspective\n"
    "('plan pays 80%', 'After the plan deductible is met, your plan pays 80%').\n"
    "Copy these values VERBATIM -- do NOT convert to member-perspective percentages.\n"
    "'After the plan deductible is met, your plan pays 80%' stays exactly as written.\n"
    "'Plan pays 100%' stays exactly as written.\n"
    "\n"
    "RULE 9 -- PLACE-OF-SERVICE ROWS (from decomposed tables)\n"
    "  Some services appear as multiple rows, one per place of service:\n"
    "  'Laboratory (Physician's Office)', 'Laboratory (Independent Lab)', etc.\n"
    "  Treat each as a separate service row. Use the full label as the Service name.\n"
    "  The In-Network and Out-of-Network columns contain the rate for that location.\n"
    "\n"
    "RULE 10 -- COMBINED DEDUCTIBLE CELLS\n"
    "  When a deductible/OOP cell contains both individual and family amounts:\n"
    "  'Individual: $1,500 Family: $3,000' OR '$1,500/$3,000 (individual/family)'\n"
    "  Split into: Individual In-Network = '$1,500', Family In-Network = '$3,000'.\n"
    "  Apply the same split to the Out-of-Network column.\n"
    "\n"
    "EXTENDED RULE 4 -- CIGNA CROSS-REFERENCES\n"
    "  'Covered same as plan's Physician's Office Services'\n"
    "      -> look up Physician Office Visit rate in this document and copy verbatim\n"
    "  'Covered same as plan's Inpatient Hospital benefit'\n"
    "      -> look up Inpatient Hospital Facility Services rate and copy verbatim\n"
    "  'Covered same as plan's Outpatient Facility Services'\n"
    "      -> look up Outpatient Facility Services rate and copy verbatim\n"
    "  Note: these cross-references may already be resolved in the STRUCTURED TABLES\n"
    "  section. If the value is already a concrete rate, use it directly.\n"
)

_PLAN_PAYS_FEW_SHOT = (
    "CIGNA-STYLE EXAMPLES -- 'plan pays' language, copy VERBATIM:\n"
    "\n"
    "Doc: Physician Office Visit - PCP/Specialist\n"
    "     After the plan deductible is met, your plan pays 80%\n"
    "     After the plan deductible is met, your plan pays 60%\n"
    'Output: {"Header":"Physician Services - Office Visits",'
    '"Service":"Physician Office Visit - Primary Care Physician (PCP)/Specialist",'
    '"In-Network Coinsurance":"After the plan deductible is met, your plan pays 80%",'
    '"In-Network After Deductible Flag":"Yes","In-Network Copay":"",'
    '"Out-Of-Network Coinsurance":"After the plan deductible is met, your plan pays 60%",'
    '"Out-Of-Network After Deductible Flag":"Yes","Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"",'
    '"Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
    "\n"
    "Doc: Preventive Care - Ages 19 and older  Plan pays 100%\n"
    "     After the plan deductible is met, your plan pays 70%\n"
    'Output: {"Header":"Preventive Care","Service":"Preventive Care - Ages 19 and older",'
    '"In-Network Coinsurance":"Plan pays 100%","In-Network After Deductible Flag":"No",'
    '"In-Network Copay":"",'
    '"Out-Of-Network Coinsurance":"After the plan deductible is met, your plan pays 70%",'
    '"Out-Of-Network After Deductible Flag":"Yes","Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"",'
    '"Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
    "\n"
    "Doc: Calendar Year Deductible\n"
    "     Individual: $1,500 Family: $3,000   (In-Network)\n"
    "     Individual: $2,000 Family: $4,000   (Out-of-Network)\n"
    'Output: {"Header":"Benefit Maximums / Deductibles / Out-of-Pocket","Service":"Deductible",'
    '"In-Network Coinsurance":"","In-Network After Deductible Flag":"","In-Network Copay":"",'
    '"Out-Of-Network Coinsurance":"","Out-Of-Network After Deductible Flag":"","Out-Of-Network Copay":"",'
    '"Individual In-Network":"$1,500","Family In-Network":"$3,000",'
    '"Individual Out-Of-Network":"$2,000","Family Out-Of-Network":"$4,000",'
    '"Limit Type":"","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
    "\n"
    "Doc: Calendar Year Out-of-Pocket Maximum\n"
    "     Individual: $3,500 Family: $6,850   (In-Network)\n"
    "     Individual: $6,250 Family: $12,500  (Out-of-Network)\n"
    'Output: {"Header":"Benefit Maximums / Deductibles / Out-of-Pocket","Service":"Out-of-Pocket Maximum",'
    '"In-Network Coinsurance":"","In-Network After Deductible Flag":"","In-Network Copay":"",'
    '"Out-Of-Network Coinsurance":"","Out-Of-Network After Deductible Flag":"","Out-Of-Network Copay":"",'
    '"Individual In-Network":"$3,500","Family In-Network":"$6,850",'
    '"Individual Out-Of-Network":"$6,250","Family Out-Of-Network":"$12,500",'
    '"Limit Type":"","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
    "\n"
    "Doc (place-of-service row from decomposed table):\n"
    "  Laboratory (Physician's Office)  Plan pays 100%  Plan pays 70%\n"
    'Output: {"Header":"Laboratory / Radiology Services","Service":"Laboratory (Physician\'s Office)",'
    '"In-Network Coinsurance":"Plan pays 100%","In-Network After Deductible Flag":"No",'
    '"In-Network Copay":"",'
    '"Out-Of-Network Coinsurance":"Plan pays 70%","Out-Of-Network After Deductible Flag":"No",'
    '"Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"",'
    '"Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.97"}\n'
    "\n"
    "Doc (Pharmacy, member-perspective copays -- copy verbatim):\n"
    "  Generic: You pay $10 (Retail 30-day)   You pay $37 (Home Delivery 90-day)\n"
    "  Preferred Brand: You pay $25 (Retail)  You pay $62 (Home Delivery)\n"
    "  Non-Preferred Brand: You pay $50       You pay $125 (Home Delivery)\n"
    'Output row 1: {"Header":"Pharmacy","Service":"Generic (Retail 30-day)",'
    '"In-Network Coinsurance":"","In-Network After Deductible Flag":"No","In-Network Copay":"$10",'
    '"Out-Of-Network Coinsurance":"","Out-Of-Network After Deductible Flag":"","Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"30-day supply","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
    'Output row 2: {"Header":"Pharmacy","Service":"Generic (Home Delivery 90-day)",'
    '"In-Network Coinsurance":"","In-Network After Deductible Flag":"No","In-Network Copay":"$37",'
    '"Out-Of-Network Coinsurance":"","Out-Of-Network After Deductible Flag":"","Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"90-day supply","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
    'Output row 3: {"Header":"Pharmacy","Service":"Preferred Brand (Retail 30-day)",'
    '"In-Network Coinsurance":"","In-Network After Deductible Flag":"No","In-Network Copay":"$25",'
    '"Out-Of-Network Coinsurance":"","Out-Of-Network After Deductible Flag":"","Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"30-day supply","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
    'Output row 4: {"Header":"Pharmacy","Service":"Preferred Brand (Home Delivery 90-day)",'
    '"In-Network Coinsurance":"","In-Network After Deductible Flag":"No","In-Network Copay":"$62",'
    '"Out-Of-Network Coinsurance":"","Out-Of-Network After Deductible Flag":"","Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"90-day supply","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
    'Output row 5: {"Header":"Pharmacy","Service":"Non-Preferred Brand (Retail 30-day)",'
    '"In-Network Coinsurance":"","In-Network After Deductible Flag":"No","In-Network Copay":"$50",'
    '"Out-Of-Network Coinsurance":"","Out-Of-Network After Deductible Flag":"","Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"30-day supply","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
    'Output row 6: {"Header":"Pharmacy","Service":"Non-Preferred Brand (Home Delivery 90-day)",'
    '"In-Network Coinsurance":"","In-Network After Deductible Flag":"No","In-Network Copay":"$125",'
    '"Out-Of-Network Coinsurance":"","Out-Of-Network After Deductible Flag":"","Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"90-day supply","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
)

# ---------------------------------------------------------------------------
# SBC-specific rules and examples
# ---------------------------------------------------------------------------

_SBC_RULES = (
    "DOCUMENT TYPE: SBC (Summary of Benefits and Coverage)\n"
    "This is a federally standardised 3-column table document.\n"
    "\n"
    "SBC COLUMN MAPPING:\n"
    "  Column 1: Service description\n"
    "  Column 2: 'In-Network' (may be labelled 'Network Provider (You will pay the least)')\n"
    "  Column 3: 'Out-of-Network' (may be labelled 'Out-of-Network Provider...')\n"
    "  Pipeline-injected Limitations annotations (optional, appear after the column values):\n"
    "    '[Pre-auth: Yes/No]' and '[Limit: N visits per Benefit Year]'\n"
    "\n"
    "SBC VALUES ARE ALREADY MEMBER-PERSPECTIVE -- copy them verbatim:\n"
    "  '20% coinsurance'         -> In-Network Coinsurance = '20% coinsurance'\n"
    "  '$30 copay/visit'         -> In-Network Copay = '$30' (strip '/visit' suffix per RULE)\n"
    "  'No charge'               -> In-Network Coinsurance = 'No charge',\n"
    "                               In-Network After Deductible Flag = 'No'\n"
    "  'No charge after deductible' -> In-Network Coinsurance = 'No charge after deductible',\n"
    "                               In-Network After Deductible Flag = 'Yes'\n"
    "  '---' or 'N/A'           -> leave the column empty (not applicable)\n"
    "  'Not applicable'          -> leave the column empty\n"
    "\n"
    "HDHP/HSA SBC NOTE: If the plan is identified as HDHP or HSA-qualified, coinsurance\n"
    "  values (e.g. '20% coinsurance') apply after the deductible even when not explicitly\n"
    "  stated. Set After Deductible Flag = 'Yes' for all coinsurance rows in HDHP plans.\n"
    "  Preventive care 'No charge' in HDHP plans: Flag = 'No' (deductible exempt by law).\n"
    "\n"
    "SBC DEDUCTIBLE CELLS -- may pack individual/family in one cell:\n"
    "  '$1,500 individual / $3,000 family'  or\n"
    "  '$1,500/individual or $3,000/family' or\n"
    "  '$1,500 per individual / $3,000 per family'\n"
    "  Split into: Individual In-Network = '$1,500', Family In-Network = '$3,000'.\n"
    "\n"
    "SBC LIMITATIONS ANNOTATIONS (pipeline-injected tags, when present):\n"
    "  '[Pre-auth: Yes]'  -> Pre-Authorization Required = 'Yes'\n"
    "  '[Pre-auth: No]'   -> Pre-Authorization Required = 'No'\n"
    "  '[Limit: 30 visits per Benefit Year]' -> Limit Type = '30 visits',\n"
    "                                           Limit Period = 'Benefit Year'\n"
    "  If no annotation is present, read the raw limitations text for pre-auth and\n"
    "  visit limits yourself.\n"
    "\n"
    "SBC SERVICE NAMES -- use the short label, strip 'If you...' framing:\n"
    "  'If you visit a doctor for routine preventive care' -> 'Preventive care visit'\n"
    "  'If you need emergency room services'              -> 'Emergency Room Services'\n"
    "  'If you have a test (blood work)'                  -> 'Diagnostic Test (Blood Work)'\n"
    "  'Your share of costs for the covered services...'  -> use the header section name\n"
)

_SBC_FEW_SHOT = (
    "SBC EXAMPLES -- values are member-perspective, copy VERBATIM:\n"
    "\n"
    "Doc (SBC): Primary care visit to treat an injury or illness  $30 copay/visit  40% coinsurance\n"
    "           Limitations: ---\n"
    'Output: {"Header":"Physician Services","Service":"Primary care visit",'
    '"In-Network Coinsurance":"","In-Network After Deductible Flag":"No","In-Network Copay":"$30",'
    '"Out-Of-Network Coinsurance":"40% coinsurance","Out-Of-Network After Deductible Flag":"No",'
    '"Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.97"}\n'
    "\n"
    "Doc (SBC): Specialist visit  20% coinsurance  40% coinsurance\n"
    "           Limitations: Requires preauthorization. 30 visits per year. [Pre-auth: Yes] [Limit: 30 visits per Benefit Year]\n"
    'Output: {"Header":"Physician Services","Service":"Specialist visit",'
    '"In-Network Coinsurance":"20% coinsurance","In-Network After Deductible Flag":"No",'
    '"In-Network Copay":"",'
    '"Out-Of-Network Coinsurance":"40% coinsurance","Out-Of-Network After Deductible Flag":"No",'
    '"Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"30 visits","Limit Period":"Benefit Year","Pre-Authorization Required":"Yes","Confidence Score":"0.99"}\n'
    "\n"
    "Doc (SBC): Preventive care/screening/immunization  No charge  40% coinsurance\n"
    "           Limitations: ---  [Pre-auth: No]\n"
    'Output: {"Header":"Preventive Care","Service":"Preventive care/screening/immunization",'
    '"In-Network Coinsurance":"No charge","In-Network After Deductible Flag":"No","In-Network Copay":"",'
    '"Out-Of-Network Coinsurance":"40% coinsurance","Out-Of-Network After Deductible Flag":"No",'
    '"Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
    "\n"
    "Doc (SBC): Overall Deductible  $1,500/individual or $3,000/family  $3,000/individual or $6,000/family\n"
    'Output: {"Header":"Benefit Maximums / Deductibles / Out-of-Pocket","Service":"Deductible",'
    '"In-Network Coinsurance":"","In-Network After Deductible Flag":"","In-Network Copay":"",'
    '"Out-Of-Network Coinsurance":"","Out-Of-Network After Deductible Flag":"","Out-Of-Network Copay":"",'
    '"Individual In-Network":"$1,500","Family In-Network":"$3,000",'
    '"Individual Out-Of-Network":"$3,000","Family Out-Of-Network":"$6,000",'
    '"Limit Type":"","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
    "\n"
    "Doc (SBC): Out-of-Pocket Maximum  $3,500/individual or $7,000/family  $7,000/individual or $14,000/family\n"
    'Output: {"Header":"Benefit Maximums / Deductibles / Out-of-Pocket","Service":"Out-of-Pocket Maximum",'
    '"In-Network Coinsurance":"","In-Network After Deductible Flag":"","In-Network Copay":"",'
    '"Out-Of-Network Coinsurance":"","Out-Of-Network After Deductible Flag":"","Out-Of-Network Copay":"",'
    '"Individual In-Network":"$3,500","Family In-Network":"$7,000",'
    '"Individual Out-Of-Network":"$7,000","Family Out-Of-Network":"$14,000",'
    '"Limit Type":"","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
    "\n"
    "Doc (SBC): Generic drugs (Retail - 30-day supply)  $10 copay/prescription  Not covered\n"
    "           Limitations: ---  [Pre-auth: No]\n"
    'Output: {"Header":"Prescription Drug Benefits","Service":"Generic drugs (Retail 30-day)",'
    '"In-Network Coinsurance":"","In-Network After Deductible Flag":"No","In-Network Copay":"$10",'
    '"Out-Of-Network Coinsurance":"Not covered","Out-Of-Network After Deductible Flag":"No",'
    '"Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"30-day supply","Limit Period":"","Pre-Authorization Required":"No","Confidence Score":"0.99"}\n'
    "\n"
    "Doc (SBC): Inpatient hospital care  20% coinsurance  40% coinsurance\n"
    "           Limitations: Preauthorization required. [Pre-auth: Yes]\n"
    'Output: {"Header":"Inpatient Hospital Services","Service":"Inpatient hospital care",'
    '"In-Network Coinsurance":"20% coinsurance","In-Network After Deductible Flag":"No",'
    '"In-Network Copay":"",'
    '"Out-Of-Network Coinsurance":"40% coinsurance","Out-Of-Network After Deductible Flag":"No",'
    '"Out-Of-Network Copay":"",'
    '"Individual In-Network":"","Family In-Network":"","Individual Out-Of-Network":"","Family Out-Of-Network":"",'
    '"Limit Type":"","Limit Period":"","Pre-Authorization Required":"Yes","Confidence Score":"0.99"}\n'
)

_EXTRACTION_FOOTER = (
    "=" * 60 + "\n"
    "NOW EXTRACT FROM THE DOCUMENT TEXT BELOW.\n"
    "Read every line -- tables AND paragraphs.\n"
    "Copy values VERBATIM. Do NOT calculate or convert anything.\n"
    "Return ONLY a valid JSON array. No markdown. No explanation.\n"
    "=" * 60 + "\n\n"
    "--- DOCUMENT TEXT START ---\n"
)

# ---------------------------------------------------------------------------
# Prompt assemblers (one per document type)
# ---------------------------------------------------------------------------

def build_task_description(chunk: str) -> str:
    """Standard SPD prompt — used for Dickenson-style documents (default path)."""
    return (
        SYSTEM_CONTEXT
        + "\n"
        + FEW_SHOT_EXAMPLES
        + "\n"
        + _EXTRACTION_FOOTER
        + chunk
        + "\n--- DOCUMENT TEXT END ---\n\n"
        + "JSON array:"
    )


def build_task_description_plan_pays(chunk: str) -> str:
    """
    SPD prompt for plan-pays documents (e.g. Cigna).
    Prepends an explicit note that values are plan-perspective and must be
    copied verbatim, plus Cigna-specific few-shot examples.
    """
    return (
        SYSTEM_CONTEXT
        + "\n"
        + _PLAN_PAYS_EXTRA_RULES
        + "\n"
        + _PLAN_PAYS_FEW_SHOT
        + "\n"
        + FEW_SHOT_EXAMPLES
        + "\n"
        + _EXTRACTION_FOOTER
        + chunk
        + "\n--- DOCUMENT TEXT END ---\n\n"
        + "JSON array:"
    )


def build_task_description_sbc(chunk: str) -> str:
    """
    SBC prompt for CMS-standardised Summary of Benefits and Coverage documents.
    Uses member-perspective examples and SBC-specific extraction rules.
    """
    return (
        SYSTEM_CONTEXT
        + "\n"
        + _SBC_RULES
        + "\n"
        + FEW_SHOT_EXAMPLES
        + "\n"
        + _SBC_FEW_SHOT
        + "\n"
        + _EXTRACTION_FOOTER
        + chunk
        + "\n--- DOCUMENT TEXT END ---\n\n"
        + "JSON array:"
    )
