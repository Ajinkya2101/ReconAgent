"""Tunable settings for the MIPS-WMS-Oracle reconciliation agent."""
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")
# The five daily source extracts live in data/ (one file per source per sales day).
DATA_DIR = Path(os.environ.get("RECON_DATA_DIR", PROJECT_ROOT / "data"))
OUTPUT_DIR = PROJECT_ROOT / "output"

# Source of the data: "excel" reads the five extracts in DATA_DIR; "events" projects the
# simulated event log (recon_agent/events, docs/EVENT_CONTRACT.md). Event mode keeps its log,
# planner decisions and reports in output/sim/, so simulated decisions never shift the
# rule confidence learned on real data.
SOURCE_MODE = os.environ.get("RECON_SOURCE", "excel").strip().lower()
SIM_DIR = OUTPUT_DIR / "sim"
EVENT_DB = Path(os.environ.get("RECON_EVENT_DB", SIM_DIR / "events.db"))
REPORT_DIR = SIM_DIR if SOURCE_MODE == "events" else OUTPUT_DIR
FEEDBACK_FILE = Path(os.environ.get("RECON_FEEDBACK_FILE", REPORT_DIR / "feedback.json"))

# Glob patterns for each source extract (one file per source per sales day).
SOURCES = {
    "mips_orders": "MIPS Order Data*.xlsx",
    "odi_batches": "ODI Batch Data*.xlsx",
    "odi_finished_goods": "ODI Finished Good Data*.xlsx",
    "wms_fill_rate": "WMS - Fill Rate Report*.xlsx",
    "wms_order_summary": "WMS - Order Summary Report by Item*.xlsx",
}

# Detection thresholds.
QTY_TOLERANCE_UNITS = 0          # MIPS vs WMS order qty must match exactly
CARRYOVER_MIN_UNITS = 50         # ignore trivial end-of-run excess
OVERPRODUCTION_MIN_UNITS = 100   # recovery-run over-build worth flagging
ROBUST_Z_THRESHOLD = 3.5         # MAD-based outlier cut-off
ORACLE_POSTING_SLA_MINUTES = 10  # R18: a WMS transaction must post in Oracle within this (plant time)

# Default retail price used for $ exposure when a SKU has none in MIPS.
DEFAULT_UNIT_PRICE = 3.99

# Worklist (web UI) settings.
# Sales-day closure-tag colours, weekday -> (label, fill, ink). PROVISIONAL: confirm the
# Lehigh Valley tag code and sales-day calendar before anyone reads this as the plant's own.
DAY_CODES = {
    0: ("MON", "#1f5fbf", "#ffffff"),
    1: ("TUE", "#23803a", "#ffffff"),
    2: ("WED", "#e0762b", "#16191d"),
    3: ("THU", "#c62f2a", "#ffffff"),
    4: ("FRI", "#ffffff", "#16191d"),
    5: ("SAT", "#f2c230", "#16191d"),
    6: ("SUN", "#6a46a8", "#ffffff"),
}
SALES_WEEKDAYS = (0, 1, 2, 3, 4, 5)   # unconfirmed; drives "next sales day" for carry-forward
CARRY_FORWARD_RULES = ("R05", "R06", "R08")
HIGH_ACK_MINUTES = 15
WEB_POLL_SECONDS = 20        # fallback poll; open screens normally hear of changes by server push
WEB_WATCH_SECONDS = 0.5 if SOURCE_MODE == "events" else 2.0   # how often the server checks the feed

# Source simulator (python -m recon_agent simulate): the operator app that publishes events.
SIM_PASSCODE = os.environ.get("RECON_SIM_PASSCODE", "")   # empty: name-only sign-in (local POC)
WORKLIST_URL = os.environ.get("RECON_WORKLIST_URL", "http://localhost:8000/")
# Write-back (event feed): the worklist sends approved corrections here. In the POC the source
# simulator stands in for Oracle Integration; the token, if set, must match on both sides.
CORRECTIONS_URL = os.environ.get("RECON_CORRECTIONS_URL", "http://127.0.0.1:8100/oic/corrections")
CORRECTIONS_TOKEN = os.environ.get("RECON_CORRECTIONS_TOKEN", "")

# OCI Generative AI (optional AI layer; the engine runs fully without it).
# Credentials and tenancy settings come from the OCI_* variables in .env. RECON_AI=off disables
# every model call (tests, offline demos) even when credentials are present.
AI_ENABLED = os.environ.get("RECON_AI", "on").strip().lower() != "off"
# A changing feed must not call the model once per event: narration waits until the data has been
# quiet this long (but never longer than the max wait after the first change), and only flags the
# model has not explained yet (same ID, units and evidence) are sent.
AI_DEBOUNCE_SECONDS = 0 if SOURCE_MODE == "excel" else 20
AI_MAX_WAIT_SECONDS = 120
AI_TOP_FINDINGS = 40
OCI_MODEL_ID = os.environ.get("OCI_MODEL_ID") or "google.gemini-2.5-pro"
OCI_REGION = os.environ.get("OCI_REGION") or "us-chicago-1"
OCI_COMPARTMENT_ID = os.environ.get("OCI_COMPARTMENT_ID", "")
OCI_SERVICE_ENDPOINT = os.environ.get("OCI_SERVICE_ENDPOINT", "")
OCI_MAX_TOKENS = 32000      # Gemini 2.5 Pro spends part of this on reasoning
OCI_TIMEOUT_SECONDS = 300
