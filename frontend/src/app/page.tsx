"use client";
import { useState } from "react";

type Turn = { role: "user" | "assistant"; text: string };
type ApiResp = { conversation_id: string; status: string; workflow_state: string | null; message: string; required_input: string | null; handoff_ticket_id: string | null; intent?: string | null };
type Debug = { workflow_state: string; intent: string | null; intent_confidence: number | null; trace: { state: string; outcome: string }[] } | null;

const API = process.env.NEXT_PUBLIC_API_BASE ?? "";

async function post(path: string, body?: unknown): Promise<ApiResp> {
  const r = await fetch(`${API}${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : "{}" });
  const data = await r.json();
  if (!r.ok) throw new Error(data.detail ?? `HTTP ${r.status}`);
  return data;
}

export default function Home() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [cid, setCid] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [pin, setPin] = useState("");
  const [customerId, setCustomerId] = useState("CUST-1001");
  const [state, setState] = useState<ApiResp | null>(null);
  const [debug, setDebug] = useState<Debug>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function refreshDebug(id: string) {
    try { setDebug(await (await fetch(`${API}/api/v1/conversations/${id}/debug`)).json()); } catch { setDebug(null); }
  }
  async function start() {
    setBusy(true); setError("");
    try {
      const r = await post("/api/v1/conversations");
      setCid(r.conversation_id); setState(r); setTurns([{ role: "assistant", text: r.message }]);
      await refreshDebug(r.conversation_id);
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  async function send() {
    if (!cid || !input.trim()) return;
    const text = input.trim(); setInput(""); setTurns((t) => [...t, { role: "user", text }]); setBusy(true); setError("");
    try { const r = await post(`/api/v1/conversations/${cid}/messages`, { message: text }); setState(r); setTurns((t) => [...t, { role: "assistant", text: r.message }]); await refreshDebug(cid); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  async function verify() {
    if (!cid || !pin) return;
    setBusy(true); setError("");
    try { const r = await post(`/api/v1/conversations/${cid}/verify`, { customer_id: customerId, demo_pin: pin }); setPin(""); setState(r); setTurns((t) => [...t, { role: "assistant", text: r.message }]); await refreshDebug(cid); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  }

  const needsPin = state?.required_input === "demo_pin";
  const closed = state?.status === "CLOSED" || state?.status === "HANDOFF";

  return (
    <main className="shell">
      <header className="topbar">
        <div><strong>Sakura Mobile</strong><span>サポートチャット（デモ）</span></div>
        <button onClick={start} disabled={busy}>新しい問い合わせ</button>
      </header>
      <section className="layout">
        <div className="chat panel">
          <div className="messages">
            {turns.length === 0 && <p className="hint">「新しい問い合わせ」を押して開始してください。例: 今月の携帯料金が先月より高いのですが。</p>}
            {turns.map((t, i) => <div key={i} className={`bubble ${t.role}`}>{t.text}</div>)}
          </div>
          <div className="composer">
            <input value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} placeholder="メッセージを入力（PINは書かないでください）" disabled={!cid || closed || busy} />
            <button onClick={send} disabled={!cid || closed || busy}>送信</button>
          </div>
          {needsPin && (
            <div className="pinbox">
              <label>お客様ID<input value={customerId} onChange={(e) => setCustomerId(e.target.value)} /></label>
              <label>デモ用PIN（専用欄）<input type="password" inputMode="numeric" autoComplete="off" value={pin} onChange={(e) => setPin(e.target.value)} /></label>
              <button onClick={verify} disabled={busy}>確認する</button>
              <small>PINはチャット履歴・ログに表示されません。デモ: CUST-1001 / 1234</small>
            </div>
          )}
          {error && <p className="error">{error}</p>}
        </div>
        <aside className="panel side">
          <h2>状態（サニタイズ済み）</h2>
          <dl>
            <dt>状態</dt><dd>{state?.status ?? "-"}</dd>
            <dt>ワークフロー</dt><dd>{state?.workflow_state ?? "-"}</dd>
            <dt>意図</dt><dd>{debug?.intent ?? state?.intent ?? "-"}</dd>
            <dt>信頼度</dt><dd>{debug?.intent_confidence ?? "-"}</dd>
            <dt>チケット</dt><dd>{state?.handoff_ticket_id ?? "-"}</dd>
          </dl>
          <h3>トレース</h3>
          <ol className="trace">{(debug?.trace ?? []).map((t, i) => <li key={i}>{t.state} → {t.outcome}</li>)}</ol>
        </aside>
      </section>
    </main>
  );
}
