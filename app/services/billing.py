import json
from pathlib import Path


class BillingAccessDenied(PermissionError):
    pass

class BillingNotFound(LookupError):
    pass

class BillingUnavailable(RuntimeError):
    pass

class TrustedBindings:
    """Server-side verified identity bindings, keyed by conversation. Client cannot set this."""
    def __init__(self):
        self._map: dict[str, str] = {}

    def bind(self, conversation_id: str, customer_id: str) -> None:
        self._map[conversation_id] = customer_id

    def get(self, conversation_id: str) -> str | None:
        return self._map.get(conversation_id)

    def clear(self, conversation_id: str) -> None:
        self._map.pop(conversation_id, None)

class BillingService:
    """Read-only mock billing. Checks trusted binding inside the service on every call."""
    def __init__(self, invoices_path: Path, bindings: TrustedBindings, retries: int = 1, fail_mode: str | None = None):
        data = json.loads(Path(invoices_path).read_text(encoding="utf-8"))
        self._invoices: dict = data["invoices"]
        self._bindings = bindings
        self.bindings = bindings
        self._retries = max(0, min(retries, 2))
        self.fail_mode = fail_mode  # test injection: "timeout" always fails
        self.calls = 0
        self.attempts_log: list[int] = []

    def get_billing(self, conversation_id: str, requested_customer_id: str | None = None) -> dict:
        verified = self._bindings.get(conversation_id)
        if not verified:
            raise BillingAccessDenied("billing requires a trusted verified identity")
        if requested_customer_id is not None and requested_customer_id != verified:
            raise BillingAccessDenied("customer binding mismatch")
        last_exc: Exception | None = None
        for attempt in range(self._retries + 1):
            self.attempts_log.append(attempt)
            try:
                return self._fetch(verified)
            except BillingUnavailable as e:
                last_exc = e
                continue
        raise last_exc or BillingUnavailable("billing unavailable")

    def _fetch(self, verified_customer_id: str) -> dict:
        self.calls += 1
        if self.fail_mode == "timeout":
            raise BillingUnavailable("simulated timeout")
        inv = self._invoices.get(verified_customer_id)
        if inv is None:
            raise BillingNotFound(verified_customer_id)
        self._validate_math(inv)
        return inv

    @staticmethod
    def _validate_math(inv: dict) -> None:
        for key in ("current", "previous"):
            i = inv[key]
            if i["base_fee"] + i["data_overage"] + i["options"] != i["total"]:
                raise BillingUnavailable(f"invoice math invalid for {key}")
        if inv["current"]["total"] - inv["previous"]["total"] != inv["delta"]:
            raise BillingUnavailable("invoice delta invalid")
        if inv["currency"] != "JPY":
            raise BillingUnavailable("unexpected currency")
