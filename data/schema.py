# data/schema.py
# ─────────────────────────────────────────────────────────────────────────────
# Central definitions for columns, rows, NA matrix and deadline schedule.
# All other modules import from here — never hardcode these elsewhere.
# ─────────────────────────────────────────────────────────────────────────────

COLS_FRAMES = [
    {"id": "inbound",     "label": "Inbound"},
    {"id": "rop_std",     "label": "ROP STD"},
    {"id": "rop_samples", "label": "ROP Smp"},
    {"id": "whls_gross",  "label": "WHLS Gross"},
    {"id": "whls_net",    "label": "WHLS Net"},
    {"id": "retail",      "label": "Retail"},
    {"id": "gvi",         "label": "GVI"},
    {"id": "ds_na",       "label": "DS NA"},
    {"id": "ecom",        "label": "eCom"},
    {"id": "sample",      "label": "Sample"},
]

COLS_WEARABLES = [
    {"id": "inb_gtk",    "label": "Inb GTK"},
    {"id": "inb_tri",    "label": "Inb Tri"},
    {"id": "rop_labs",   "label": "ROP/Labs"},
    {"id": "whls_gross", "label": "WHLS Gross"},
    {"id": "whls_net",   "label": "WHLS Net"},
    {"id": "retail",     "label": "Retail"},
    {"id": "ds_na",      "label": "DS NA"},
    {"id": "ecom",       "label": "eCom"},
    {"id": "dummy",      "label": "Dummy"},
]

COLS_BY_PL = {
    "FRAMES":    COLS_FRAMES,
    "WEARABLES": COLS_WEARABLES,
}

# Rows shared across both product lines
ROWS = [
    {"id": "py",      "label": "PY",           "is_ref": False, "is_fri": False},
    {"id": "siop",    "label": "SIOP",          "is_ref": False, "is_fri": False},
    {"id": "mon_frc", "label": "Monday FRC",    "is_ref": False, "is_fri": False},
    {"id": "thu_frc", "label": "Thursday FRC",  "is_ref": False, "is_fri": False},
    {"id": "fri_frc", "label": "Friday FRC",    "is_ref": False, "is_fri": True},
    {"id": "actual",  "label": "Actual",        "is_ref": False, "is_fri": False},
    {"id": "eow_wip", "label": "EoW WIP",       "is_ref": False, "is_fri": False},
    {"id": "wip_ot",  "label": "WIP OT %",      "is_ref": False, "is_fri": False},
]

ROW_IDS   = [r["id"] for r in ROWS]
ROW_BY_ID = {r["id"]: r for r in ROWS}

# N/A matrix — columns that are not applicable for a given row
# (cells will be locked/hidden in the UI and stored as NULL in the DB)
NA_FRAMES: dict[str, list[str]] = {
    "py":      ["rop_samples"],
    "siop":    ["rop_samples", "whls_net"],
    "mon_frc": [],
    "thu_frc": ["rop_samples"],
    "fri_frc": ["rop_samples"],
    "actual":  ["rop_samples", "rop_std"],
    "eow_wip": ["rop_samples", "rop_std", "whls_gross", "whls_net", "retail", "gvi", "ds_na", "ecom", "sample"],
    "wip_ot":  ["rop_samples", "rop_std", "whls_gross", "whls_net", "retail", "gvi", "ds_na", "ecom", "sample"],
}

NA_WEARABLES: dict[str, list[str]] = {
    "py":      ["inb_gtk", "inb_tri"],
    "siop":    ["inb_gtk", "inb_tri"],
    "mon_frc": [],
    "thu_frc": [],
    "fri_frc": [],
    "actual":  [],
    "eow_wip": ["inb_gtk", "inb_tri", "whls_gross", "whls_net", "retail", "ds_na", "ecom", "dummy"],
    "wip_ot":  ["inb_gtk", "inb_tri", "whls_gross", "whls_net", "retail", "ds_na", "ecom", "dummy"],
}

NA_BY_PL = {
    "FRAMES":    NA_FRAMES,
    "WEARABLES": NA_WEARABLES,
}

# Deadline schedule — local time per site, per submission type
DEADLINES: dict[str, dict[str, str]] = {
    "SEDICO":   {"py": "Thu 11:00", "siop": "Thu 11:00", "mon_frc": "Tue 18:00", "thu_frc": "Thu 11:00", "fri_frc": "Fri 15:00", "actual": "Next Mon 16:00", "eow_wip": "Next Mon 16:00", "wip_ot": "Next Mon 16:00"},
    "ATLANTA":  {"py": "Wed EOD",   "siop": "Wed EOD",   "mon_frc": "Tue 11:00", "thu_frc": "Wed EOD",   "fri_frc": "Fri 09:00", "actual": "Next Mon 10:00", "eow_wip": "Next Mon 10:00", "wip_ot": "Next Mon 10:00"},
    "TIJUANA":  {"py": "Wed EOD",   "siop": "Wed EOD",   "mon_frc": "Tue 11:00", "thu_frc": "Wed EOD",   "fri_frc": "Fri 09:00", "actual": "Next Mon 10:00", "eow_wip": "Next Mon 10:00", "wip_ot": "Next Mon 10:00"},
    "DONGGUAN": {"py": "Thu 18:00", "siop": "Thu 18:00", "mon_frc": "Thu 18:00", "thu_frc": "Thu 18:00", "fri_frc": "Fri EOD",   "actual": "Next Mon 18:00", "eow_wip": "Next Mon 18:00", "wip_ot": "Next Mon 18:00"},
    "RAYONG":   {"py": "Thu 18:00", "siop": "Thu 18:00", "mon_frc": "Thu 18:00", "thu_frc": "Thu 18:00", "fri_frc": "Fri EOD",   "actual": "Next Mon 18:00", "eow_wip": "Next Mon 18:00", "wip_ot": "Next Mon 18:00"},
    "SUMARE":   {"py": "Wed EOD",   "siop": "Wed EOD",   "mon_frc": "Wed EOD",   "thu_frc": "Wed EOD",   "fri_frc": "Fri 10:00", "actual": "Next Mon 11:00", "eow_wip": "Next Mon 11:00", "wip_ot": "Next Mon 11:00"},
}

SITES = list(DEADLINES.keys())

SITE_OWNERS = {
    "SEDICO":   "Ongaro",
    "ATLANTA":  "Brittany",
    "TIJUANA":  "Brittany",
    "DONGGUAN": "Li Niu",
    "RAYONG":   "Jirawat",
    "SUMARE":   "Eduarda",
}

# Comment pre-sets for Friday FRC / Actual variance
COMMENT_PRESETS = [
    {"id": "logistics",   "label": "Logistics reasons"},
    {"id": "stock",       "label": "Stock availability"},
    {"id": "orders",      "label": "Order reasons (market)"},
    {"id": "prioritization", "label": "Prioritization"},
]

# Threshold rule: comment is mandatory if abs diff >= THRESHOLD_ABS
# OR relative diff >= THRESHOLD_REL (as a fraction)
THRESHOLD_ABS = 10       # Kpcs
THRESHOLD_REL = 0.10     # 10%

# Comparison pairs that trigger the comment rule
# (submitted_row_id, reference_row_id)
COMMENT_TRIGGER_PAIRS = [
    ("fri_frc", "mon_frc"),
    ("actual",  "mon_frc"),
]
