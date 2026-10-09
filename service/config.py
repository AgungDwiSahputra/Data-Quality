# service/config.py
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass
class Settings:
    # SMTP
    smtp_host: str = os.getenv("SMTP_HOST", "")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_user: str = os.getenv("SMTP_USER", "")
    smtp_pass: str = os.getenv("SMTP_PASS", "")
    smtp_tls: bool = os.getenv("SMTP_TLS", "true").lower() == "true"

    mail_from: str = os.getenv("MAIL_FROM", "dq-service@company.local")
    mail_to_default: str = os.getenv("MAIL_TO_DEFAULT", "")   # koma-pisah
    mail_subject_prefix: str = os.getenv("MAIL_SUBJECT_PREFIX", "[DQ]")

    # Path
    project_root: str = os.getenv("PROJECT_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    report_dir: str = os.getenv("REPORT_DIR", "reports")

    # Service
    api_key: str = os.getenv("SERVICE_API_KEY", "")  # header X-API-Key
    max_concurrent_jobs: int = int(os.getenv("MAX_CONCURRENT_JOBS", "2"))

settings = Settings()