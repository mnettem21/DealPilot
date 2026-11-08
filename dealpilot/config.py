from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "dealpilot.db"
WEB_DIR = ROOT / "web"
OUTPUT_DIR = ROOT / "output"
SHARED_FOLDER = ROOT / "shared_folder"
ARRIVALS_PATH = ROOT / "sim" / "arrivals.jsonl"
ASSIGNMENTS_PATH = ROOT / "sim" / "assignments.json"
CONTACTS_PATH = ROOT / "contacts" / "teams.json"
STAGES_PATH = ROOT / "rules" / "stages.json"
HISTORY_DEALS_PATH = ROOT / "history" / "deals.csv"
HISTORY_DEPOSITS_PATH = ROOT / "history" / "monthly_deposits.csv"

SIM_START = "2026-11-02T08:00:00-06:00"

STAGES = ["Intake", "Credit", "Committee", "Product", "Committed", "Closed"]

