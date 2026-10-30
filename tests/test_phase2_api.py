"""API isolation, trace gating, and Phase 1 regression on the Phase 2 stack."""
from app.services.model import build_model
from tests.conftest import billing_service, headers, make_client, msg, new_conv, to_pin_step, verify
from tests.test_phase2_agent import v2_client

TRACE_KEYS = {"step", "tool", "policy", "duration_ms", "outcome", "source_ids"}


def test_p2_20_sessions_and_trace_are_isolated():
    client, app, _ = v2_client(dev_trace=True)
    first = new_conv(client)
    second = new_conv(client)
    crossed = client.post(
        f"/api/v1/conversations/{first}/messages",
        json={"message": "プラン変更の手数料は？"},
        headers=headers(second),
    )
    assert crossed.status_code == 401
    own = client.get(f"/api/v1/conversations/{first}/trace", headers=headers(first))
    other = client.get(f"/api/v1/conversations/{first}/trace", headers=headers(second))
    assert own.status_code == 200
    assert other.status_code == 401
    assert app.state.service.authorize(first, headers(second)["X-Conversation-Token"]) is False


def test_p2_21_verify_customer_mismatch_blocks_billing():
    client, _, billing = v2_client()
    cid = new_conv(client)
    msg(client, cid, "私の請求額を教えて")
    msg(client, cid, "CUST-1001")
    mismatch = verify(client, cid, customer="CUST-1002", pin="5678")
    assert mismatch.status_code == 400
    assert billing.calls == 0
    done = verify(client, cid, customer="CUST-1001", pin="1234")
    assert done.status_code == 200
    assert "8,800円" in done.json()["message"]
    assert billing.calls == 1


def test_p2_22_pin_in_chat_is_not_stored():
    client, app, _ = v2_client()
    cid = new_conv(client)
    sent = msg(client, cid, "1234")
    assert "専用入力欄" in sent.json()["message"]
    blob = str(app.state.service.checkpoint_values(cid)) + str(sent.json())
    assert "1234" not in blob
    msg(client, cid, "私の請求額を教えて")
    msg(client, cid, "CUST-1001")
    again = msg(client, cid, "1234")
    assert "専用" in again.json()["message"]
    assert "1234" not in str(app.state.service.checkpoint_values(cid))


def test_p2_25_trace_disabled_is_absent():
    client, _, _ = v2_client(dev_trace=False)
    cid = new_conv(client)
    body = msg(client, cid, "プラン変更の手数料は？")
    assert body.status_code == 200
    payload = body.json()
    assert payload["developer_trace_available"] is False
    assert "trace_events" not in payload
    assert client.get(f"/api/v1/conversations/{cid}/trace", headers=headers(cid)).status_code == 404


def test_p2_26_trace_enabled_is_sanitized():
    client, _, _ = v2_client(dev_trace=True)
    cid = new_conv(client)
    msg(client, cid, "今月の料金が高くなった理由と安いプランを教えて")
    msg(client, cid, "CUST-1001")
    verify(client, cid)
    traced = client.get(f"/api/v1/conversations/{cid}/trace", headers=headers(cid))
    assert traced.status_code == 200
    events = traced.json()["events"]
    assert events
    assert any(event["policy"] == "allowed" and event["tool"] == "get_billing_info" for event in events)
    for event in events:
        assert set(event) == TRACE_KEYS
    dumped = traced.text
    assert "1234" not in dumped and "pin" not in dumped.lower() and "system" not in dumped.lower()


def test_p2_27_openai_without_key_is_an_error():
    try:
        build_model("openai", None)
    except ValueError as exc:
        assert "OPENAI_API_KEY" in str(exc)
    else:
        raise AssertionError("missing API key was treated as a successful model")


def test_p2_29_verified_binding_survives_the_next_turn():
    client, app, billing = v2_client()
    cid = new_conv(client)
    msg(client, cid, "一般的なプランを教えて。あと私の請求額も。")
    assert "billing" in app.state.service.checkpoint_values(cid).get("pending_tasks", [])
    msg(client, cid, "CUST-1001")
    first = verify(client, cid).json()
    assert "8,800円" in first["message"]
    follow = msg(client, cid, "私の請求額を教えて").json()
    assert follow["required_input"] is None
    assert "8,800円" in follow["message"]
    assert "お客様IDを入力" not in follow["message"]
    assert app.state.service.bindings.get(cid) == "CUST-1001"
    assert billing.calls >= 1


def test_p2_30_phase1_paths_still_close_with_the_same_facts():
    billing = billing_service()
    client, _ = make_client(billing=billing)
    cid = new_conv(client)
    to_pin_step(client, cid)
    billed = verify(client, cid).json()
    assert billed["status"] == "CLOSED"
    assert "8,800円" in billed["message"] and "6,600円" in billed["message"]
    faq = new_conv(client)
    hours = msg(client, faq, "営業時間は何時ですか？").json()
    assert hours["status"] == "CLOSED" and "9時から18時" in hours["message"]
    hand = new_conv(client)
    ticket = msg(client, hand, "オペレーターに代わってください").json()
    assert ticket["status"] == "HANDOFF" and ticket["handoff_ticket_id"].startswith("DEMO-")
