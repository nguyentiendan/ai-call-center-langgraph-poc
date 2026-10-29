"""Docker smoke test (stdlib only). Assumes `docker compose up -d --build` is running.

Usage: python3 scripts/docker_smoke.py [base_url]   (default http://localhost:8080)
Covers: frontend page, /health, conversation creation (+token), auth rejection,
billing message, customer ID, PIN verification, grounded billing response, 409 after close.
Exits non-zero on any failure.
"""
import json
import sys
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080").rstrip("/")
FAILURES = []

def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")
    if not ok:
        FAILURES.append(name)

def req(method: str, path: str, body=None, token: str | None = None):
    data = json.dumps(body).encode() if body is not None else b"{}"
    r = urllib.request.Request(BASE + path, data=data if method == "POST" else None, method=method,
                               headers={"Content-Type": "application/json",
                                        **({"X-Conversation-Token": token} if token else {})})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

def main() -> int:
    status, html = req("GET", "/")
    check("frontend serves page", status == 200 and "Sakura Mobile" in html, f"status={status}")
    status, body = req("GET", "/health")
    health = json.loads(body) if status == 200 else {}
    check("api health via nginx", status == 200 and health.get("status") == "ok", body[:120])
    status, body = req("POST", "/api/v1/conversations")
    conv = json.loads(body) if status == 200 else {}
    cid, token = conv.get("conversation_id"), conv.get("conversation_token")
    check("conversation creation returns id+token", bool(cid and token), f"status={status}")
    status, _ = req("POST", f"/api/v1/conversations/{cid}/messages", {"message": "料金"})
    check("missing token rejected 401", status == 401, f"status={status}")
    status, body = req("POST", f"/api/v1/conversations/{cid}/messages",
                       {"message": "今月の携帯料金が先月より高いのですが。"}, token)
    r = json.loads(body)
    check("billing message asks customer_id", r.get("required_input") == "customer_id", body[:160])
    status, body = req("POST", f"/api/v1/conversations/{cid}/messages", {"message": "CUST-1001"}, token)
    r = json.loads(body)
    check("customer id asks demo_pin", r.get("required_input") == "demo_pin", body[:160])
    status, body = req("POST", f"/api/v1/conversations/{cid}/verify",
                       {"customer_id": "CUST-1001", "demo_pin": "1234"}, token)
    r = json.loads(body)
    msg_text = r.get("message", "")
    check("verification + grounded billing response",
          status == 200 and r.get("status") == "CLOSED" and "8,800" in msg_text and "6,600" in msg_text,
          body[:200])
    check("PIN not echoed", "1234" not in body)
    status, _ = req("POST", f"/api/v1/conversations/{cid}/messages", {"message": "ありがとう"}, token)
    check("message after close is 409", status == 409, f"status={status}")
    print(f"\n{'SMOKE OK' if not FAILURES else 'SMOKE FAILED: ' + ', '.join(FAILURES)}")
    return 1 if FAILURES else 0

if __name__ == "__main__":
    sys.exit(main())
