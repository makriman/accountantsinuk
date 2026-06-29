"""
HubSpot CRM configuration — edit this one file to fit your market.
================================================================================
Everything product/industry-specific lives here so the importer stays generic.
Out of the box this is configured for a B2B lead list (companies + the people
who work at them). Rename the properties, relabel the pipeline stages, and swap
the generic-email patterns to match how YOUR market addresses its mailboxes.

The custom properties below are created automatically on first run
(`push_to_hubspot.py --phase props`). They are prefixed so they group neatly in
HubSpot and never clash with standard fields — change PREFIX to your own slug.
"""

# Prefix for every custom property created in your portal (lowercase, no spaces).
PREFIX = "lead"

# Where the dataset lives (output of ../lead-gen/build_dataset.py).
DATA_FILE = "leads.js"

# ── Custom COMPANY properties: (internal_name, label, type, fieldType) ─────────
COMPANY_PROPS = [
    (f"{PREFIX}_reg_number",    "Registration Number",          "string", "text"),
    (f"{PREFIX}_category",      "Category / Industry",           "string", "text"),
    (f"{PREFIX}_entity_type",   "Entity Type",                   "string", "text"),
    (f"{PREFIX}_data_source",   "Data Source",                   "string", "text"),
    (f"{PREFIX}_tags",          "Lead Tags",                     "string", "text"),
    (f"{PREFIX}_area",          "Region / Area",                 "string", "text"),
    (f"{PREFIX}_directory_url", "Directory Profile URL",         "string", "text"),
    (f"{PREFIX}_other_phones",  "Other Phone Numbers",           "string", "text"),
    (f"{PREFIX}_founded",       "Founded / Incorporation Date",  "date",   "date"),
    (f"{PREFIX}_rating",        "Maps Rating",                   "number", "number"),
    (f"{PREFIX}_reviews",       "Maps Review Count",             "number", "number"),
]

# Boolean (Yes/No) company properties: (internal_name, label)
COMPANY_BOOL_PROPS = [
    (f"{PREFIX}_directory_member", "Directory Member"),
]

# Custom CONTACT properties: (internal_name, label, type, fieldType)
CONTACT_PROPS = [
    (f"{PREFIX}_data_source",     "Data Source",                  "string", "text"),
    (f"{PREFIX}_source_company",  "Source Company (Reg. Number)", "string", "text"),
    (f"{PREFIX}_email_type",      "Email Type (named / generic)", "string", "text"),
]

# ── Deal pipeline seeded by seed_pipeline.py ───────────────────────────────────
# (stage label, win probability 0–1, is-closed)
PIPELINE_LABEL = "Sales Pipeline"
STAGES = [
    ("New Lead",        0.10, False),
    ("Qualifying",      0.20, False),
    ("Contacted",       0.30, False),
    ("Meeting Booked",  0.50, False),
    ("Proposal / Demo", 0.60, False),
    ("Negotiation",     0.80, False),
    ("Won",             1.00, True),
    ("Lost",            0.00, True),
]

# ── Email classification ───────────────────────────────────────────────────────
# Local-parts that mean "a shared mailbox", not a person. A contact whose email
# matches this is tagged "generic"; everything else is "named" and the importer
# tries to infer a first/last name from the local-part. Tune for your market.
GENERIC_LOCALPART = (
    r"^(info|admin|enquir|hello|contact|office|mail|accounts?|team|support|sales|"
    r"reception|post|payroll|hr|careers|jobs|finance|help|general|partners?)"
)
