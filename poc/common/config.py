"""Shared settings. Every value can be overridden with an environment variable."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("POC_DATA_DIR", ROOT / "var"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "lending.db"          # synthetic LOS/LMS/bureau data
AUDIT_DB_PATH = DATA_DIR / "records.db"    # decision record + audit log

STUBS_URL = os.getenv("POC_STUBS_URL", "http://127.0.0.1:8101")
GATEWAY_URL = os.getenv("POC_GATEWAY_URL", "http://127.0.0.1:8100")
BFF_URL = os.getenv("POC_BFF_URL", "http://127.0.0.1:8000")

# Dev-only signing keys. In AWS these are replaced by the IdP (inbound auth)
# and by the LOS approval service (approval tokens).
IDP_SECRET = os.getenv("POC_IDP_SECRET", "dev-idp-secret-change-me-0123456789abcdef")
APPROVAL_SECRET = os.getenv("POC_APPROVAL_SECRET", "dev-approval-secret-change-me-0123456789abcd")
SERVICE_TOKEN = os.getenv("POC_SERVICE_TOKEN", "dev-service-token")

REGISTRY_PATH = ROOT / "registry.yaml"
POLICY_DIR = ROOT / "policies"

MCP_PROTOCOL_VERSION = "2026-07-28"
