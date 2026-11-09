"""Tunable settings for the MIPS-WMS-Oracle reconciliation agent."""
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")
# The source workbook (the business team's 9 Nov mapping, one sheet per source) is found anywhere
# under data/ (see docs/DATA_MODEL_9NOV.md).
DATA_DIR = Path(os.environ.get("RECON_DATA_DIR", PROJECT_ROOT / "data"))
OUTPUT_DIR = PROJECT_ROOT / "output"
WORKBOOK_GLOB = os.environ.get("RECON_WORKBOOK_GLOB", "**/BIMBO_MIPS_WMS_ODI_Data*.xlsx")

# Source of the data: "excel" reads the workbook in DATA_DIR; "events" projects the simulated
# event log (recon_agent/events, docs/EVENT_CONTRACT.md). Event mode keeps its log, planner
# decisions and reports in output/sim/, so simulated decisions never shift the rule confidence
# learned on real data.
SOURCE_MODE = os.environ.get("RECON_SOURCE", "excel").strip().lower()
SIM_DIR = OUTPUT_DIR / "sim"
EVENT_DB = Path(os.environ.get("RECON_EVENT_DB", SIM_DIR / "events.db"))
REPORT_DIR = SIM_DIR if SOURCE_MODE == "events" else OUTPUT_DIR
FEEDBACK_FILE = Path(os.environ.get("RECON_FEEDBACK_FILE", REPORT_DIR / "feedback.json"))

# Detection thresholds.
QTY_TOLERANCE_UNITS = 0          # quantities must match exactly between systems
HIGH_UNITS = 1000                # a quantity rule is High at this many units or more

# Retail price used for $ exposure when a SKU's sales orders carry none.
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
SALES_WEEKDAYS = (0, 1, 2, 3, 4, 5)   # unconfirmed; drives "next sales day" on the worklist
# Surplus stays in WMS (all on-hand stock is kept there), so no rule carries units forward any more.
CARRY_FORWARD_RULES: tuple[str, ...] = ()
HIGH_ACK_MINUTES = 15
WEB_POLL_SECONDS = 20        # fallback poll; open screens normally hear of changes by server push
WEB_WATCH_SECONDS = 0.5 if SOURCE_MODE == "events" else 2.0   # how often the server checks the feed

# Sign-in (username and password) and sessions. See recon_agent/users.py and docs/DEPLOYMENT.md.
USERS_FILE = Path(os.environ.get("RECON_USERS_FILE", OUTPUT_DIR / "users.json"))
SECRET_KEY = os.environ.get("RECON_SECRET_KEY", "")   # signs session cookies; random per process if empty
SESSION_HOURS = float(os.environ.get("RECON_SESSION_HOURS") or 12)
# Secure cookies travel over HTTPS only (browsers also send them to http://localhost). Turn off only
# for a plain-HTTP test server reached by another name.
COOKIE_SECURE = os.environ.get("RECON_COOKIE_SECURE", "on").strip().lower() != "off"
# Origins allowed to POST, comma-separated (e.g. https://recon.example.com). Empty: the request's own host.
ALLOWED_ORIGINS = {o.strip().rstrip("/") for o in os.environ.get("RECON_ALLOWED_ORIGINS", "").split(",") if o.strip()}
# Proxies whose X-Forwarded-For / X-Forwarded-Proto uvicorn trusts (the TLS terminator in front).
FORWARDED_ALLOW_IPS = os.environ.get("RECON_FORWARDED_ALLOW_IPS", "127.0.0.1")
API_DOCS = os.environ.get("RECON_API_DOCS", "off").strip().lower() == "on"   # Swagger UI at /api/docs
# Failed sign-ins allowed per username, and per network address, in SIGNIN_WINDOW. The address limit
# is higher: a plant floor often reaches the server through one shared address.
SIGNIN_ATTEMPTS = 5
SIGNIN_ATTEMPTS_PER_ADDRESS = 50
SIGNIN_WINDOW_SECONDS = 15 * 60

# Source simulator (python -m recon_agent simulate): the operator app that publishes events.
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
# Every model call (narration and the Ask assistant) is appended here with its input, output and the
# checks on it; each explanation shows the id of its entry. Kept next to the planner decisions, so
# simulated runs log separately from real ones. A full file is renamed with a timestamp, never cut.
AI_AUDIT_FILE = Path(os.environ.get("RECON_AI_AUDIT_FILE", FEEDBACK_FILE.with_name("ai_audit.jsonl")))
AI_AUDIT_MAX_BYTES = 50 * 1024 * 1024
OCI_MODEL_ID = os.environ.get("OCI_MODEL_ID") or "google.gemini-2.5-pro"
OCI_REGION = os.environ.get("OCI_REGION") or "us-chicago-1"
OCI_COMPARTMENT_ID = os.environ.get("OCI_COMPARTMENT_ID", "")
OCI_SERVICE_ENDPOINT = os.environ.get("OCI_SERVICE_ENDPOINT", "")
OCI_MAX_TOKENS = 32000      # Gemini 2.5 Pro spends part of this on reasoning
OCI_TIMEOUT_SECONDS = 300
