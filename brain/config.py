import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

COMPANY = "Ridgeline Outfitters"
DATA_DIR = ROOT / "data"
DOCS_DIR = DATA_DIR / "docs"
PL_PATH = DATA_DIR / "financials" / "pl_monthly.csv"
KPI_PATH = DATA_DIR / "financials" / "kpi_drivers.csv"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
LLM_MODEL = os.getenv("BRAIN_MODEL", "claude-opus-5-5")

HYDRA_DB_API_KEY = os.getenv("HYDRA_DB_API_KEY")
HYDRA_DATABASE = os.getenv("HYDRA_DATABASE", "ridgeline_company_brain")
HYDRA_COLLECTION = os.getenv("HYDRA_COLLECTION", "finance_docs")
HYDRA_STATE_PATH = ROOT / ".hydra_state.json"

# A variance is a "driver" worth explaining if it moves profit by at least this much.
MATERIALITY_USD = 10_000
DEFAULT_QUESTION = "Why was profit below budget, and what should management watch next month?"
