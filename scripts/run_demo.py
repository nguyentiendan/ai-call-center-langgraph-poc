"""Print sanitized traces for the 5 required paths. Usage: uv run python scripts/run_demo.py"""
from fastapi.testclient import TestClient

from app.core.config import ROOT
from app.main import create_app
from app.services.billing import BillingService, TrustedBindings


def run(label, steps, billing=None):
    app = create_app(billing=billing) if billing else create_app()
    c = TestClient(app)
    cid = c.post("/api/v1/conversations").json()["conversation_id"]
    print(f"\n== {label} ==")
    for kind, payload in steps:
        if kind == "msg":
            r = c.post(f"/api/v1/conversations/{cid}/messages", json={"message": payload})
        else:
            r = c.post(f"/api/v1/conversations/{cid}/verify", json=payload)
        print(f"[{kind}] -> {r.status_code} {r.json()}")
    print("trace:", c.get(f"/api/v1/conversations/{cid}/debug").json()["trace"])

run("happy_path", [("msg", "今月の携帯料金が先月より高いのですが。"), ("msg", "CUST-1001"),
                   ("verify", {"customer_id": "CUST-1001", "demo_pin": "1234"})])
run("faq", [("msg", "営業時間は何時ですか？")])
run("handoff", [("msg", "オペレーターに代わってください")])
run("lockout_3_failures", [("msg", "料金が高い"), ("msg", "CUST-1001"), ("verify", {"customer_id": "CUST-1001", "demo_pin": "9999"}),
                           ("msg", "CUST-1001"), ("verify", {"customer_id": "CUST-1001", "demo_pin": "9999"}),
                           ("msg", "CUST-1001"), ("verify", {"customer_id": "CUST-1001", "demo_pin": "9999"})])
timeout_billing = BillingService(ROOT / "data/demo_invoices.json", TrustedBindings(), retries=1, fail_mode="timeout")
run("billing_timeout", [("msg", "料金が高い"), ("msg", "CUST-1001"),
                        ("verify", {"customer_id": "CUST-1001", "demo_pin": "1234"})], billing=timeout_billing)
print("\nbilling attempts on timeout path (initial+1 retry expected=2):", len(timeout_billing.attempts_log))
