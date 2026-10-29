
import pytest
from fastapi.testclient import TestClient

from app.core.config import ROOT
from app.main import create_app
from app.services.billing import BillingService, TrustedBindings
from app.services.model import FakeModel


def make_client(model=None, billing=None, workflow: str = "billing_v1.yaml"):
    app = create_app(workflow_path=ROOT / f"config/workflows/{workflow}",
                     model=model or FakeModel(), billing=billing)
    return TestClient(app, raise_server_exceptions=False), app

def new_conv(client) -> str:
    r = client.post("/api/v1/conversations")
    assert r.status_code == 200
    return r.json()["conversation_id"]

def msg(client, cid, text):
    return client.post(f"/api/v1/conversations/{cid}/messages", json={"message": text})

def verify(client, cid, customer="CUST-1001", pin="1234"):
    return client.post(f"/api/v1/conversations/{cid}/verify", json={"customer_id": customer, "demo_pin": pin})

def to_pin_step(client, cid, customer="CUST-1001"):
    msg(client, cid, "今月の携帯料金が先月より高いのですが。")
    return msg(client, cid, customer)

def billing_service(fail_mode=None):
    return BillingService(ROOT / "data/demo_invoices.json", TrustedBindings(), retries=1, fail_mode=fail_mode)

@pytest.fixture()
def client():
    c, _ = make_client()
    return c
