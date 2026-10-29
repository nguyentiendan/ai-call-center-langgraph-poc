import hashlib
import hmac
import json
from pathlib import Path

from pydantic import BaseModel


class VerificationResult(BaseModel):
    verified: bool
    verified_customer_id: str | None = None
    reason: str

class VerificationService:
    """Server-side only. PIN is hashed transiently and never stored/logged."""
    def __init__(self, customers_path: Path):
        data = json.loads(Path(customers_path).read_text(encoding="utf-8"))
        self._customers = {c["customer_id"]: c for c in data["customers"]}

    def verify(self, customer_id: str, demo_pin: str) -> VerificationResult:
        rec = self._customers.get(customer_id)
        if rec is None or not demo_pin:
            return VerificationResult(verified=False, reason="not_found")
        digest = hashlib.sha256((rec["salt"] + demo_pin).encode()).hexdigest()
        if hmac.compare_digest(digest, rec["pin_hash"]):
            return VerificationResult(verified=True, verified_customer_id=customer_id, reason="ok")
        return VerificationResult(verified=False, reason="mismatch")
