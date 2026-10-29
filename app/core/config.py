import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def get_workflow_path() -> Path:
    return ROOT / os.environ.get("WORKFLOW_PATH", "config/workflows/billing_v1.yaml")

def get_model_provider() -> str:
    return os.environ.get("MODEL_PROVIDER", "fake").lower()
