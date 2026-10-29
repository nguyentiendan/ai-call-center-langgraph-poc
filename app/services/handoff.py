import uuid


class HandoffService:
    """Demo queue only — no live operator. Idempotent per conversation."""
    def __init__(self):
        self._by_key: dict[str, dict] = {}
        self.created = 0

    def create(self, conversation_id: str, reason: str, summary: str, idempotency_key: str) -> dict:
        if idempotency_key in self._by_key:
            return self._by_key[idempotency_key]
        ticket = {"ticket_id": f"DEMO-{uuid.uuid4().hex[:8].upper()}", "status": "queued_demo",
                  "reason": reason, "summary": summary[:300]}
        self._by_key[idempotency_key] = ticket
        self.created += 1
        return ticket
