import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
APP_NAME = os.getenv("APP_NAME", "SmartPlan AI")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'smartplan.db'}")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "10"))
