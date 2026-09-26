import os, json, time, threading, uuid, hashlib
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
load_dotenv()

import assemblyai as aai
from assemblyai.streaming.v3 import (
    StreamingClient, StreamingClientOptions, StreamingEvents,
    StreamingParameters, TurnEvent,
)
from groq import Groq
from backend.knowledge import load_policy, search_policy

AAI_KEY = os.environ["ASSEMBLYAI_API_KEY"]
GROQ_KEY = os.environ["GROQ_API_KEY"]
CALL_ID = os.environ.get("CALL_ID", "demo-call-1")

llm = Groq(api_key=GROQ_KEY)
GROQ_MODEL = "openai/gpt-oss-120b"
policy_chunks = load_policy(os.path.join(os.path.dirname(__file__), "..", "policy.txt"))

app = FastAPI(title="ClaimGuard Backend")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

CLAIMS: list[dict] = []
AUDIT_LOG: list[dict] = []
buffer: list[str] = []
buffer_lock = threading.Lock()


# ---------- claim store helpers ----------

def _chain_hash(entry: dict) -> str:
    prev = AUDIT_LOG[-1]["hash"] if AUDIT_LOG else "genesis"
    payload = prev + json.dumps(entry, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()

def _log(event: dict):
    event["hash"] = _chain_hash(event)
    AUDIT_LOG.append(event)

def _store_claim(speaker, text, verdict, risk, confidence, evidence):
    record = {
        "call_id": CALL_ID, "speaker": speaker, "text": text,
        "verdict": verdict, "risk": risk, "confidence": confidence, "evidence": evidence,
        "id": str(uuid.uuid4()), "ts": datetime.now(timezone.utc).isoformat(),
    }
    CLAIMS.append(record)
    _log({"event": "claim_flagged", "claim_id": record["id"], "risk": risk, "ts": record["ts"]})


# ---------- Groq claim extraction + verification ----------

EXTRACT_PROMPT = """You are monitoring a live sales/support call for factual, checkable
claims about refunds, pricing, support hours, delivery times, or cancellation terms.
Ignore greetings, small talk, opinions, and questions.

Transcript:
{chunk}

Return ONLY a JSON list like [{{"speaker":"rep","claim":"..."}}]. Return [] if none.
No markdown, no explanation, just the JSON."""

VERIFY_PROMPT = """You are a compliance verifier. Compare this claim to the official
policy excerpts below.

Claim: "{claim}"

Policy excerpts:
{evidence}

Return ONLY JSON: {{"verdict":"supported|contradicted|insufficient","risk":"low|medium|high",
"confidence":0.0-1.0,"explanation":"one sentence"}}
risk="high" only when the claim is contradicted and could mislead the customer.
No markdown, no explanation, just the JSON."""


def extract_claims(chunk_text: str) -> list[dict]:
    try:
        resp = llm.chat.completions.create(
            model=GROQ_MODEL, max_tokens=400,
            messages=[{"role": "user", "content": EXTRACT_PROMPT.format(chunk=chunk_text)}],
        )
    except Exception as e:
        print(f"[extract] GROQ CALL FAILED: {e}")
        return []
    text = resp.choices[0].message.content.strip().replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(text)
    except Exception:
        return []


def verify_claim(claim_text: str):
    evidence_chunks = search_policy(claim_text, policy_chunks)
    evidence = "\n---\n".join(evidence_chunks) or "No matching policy excerpt found."
    try:
        resp = llm.chat.completions.create(
            model=GROQ_MODEL, max_tokens=250,
            messages=[{"role": "user", "content": VERIFY_PROMPT.format(claim=claim_text, evidence=evidence)}],
        )
    except Exception as e:
        print(f"[verify] GROQ CALL FAILED: {e}")
        return {"verdict": "insufficient", "risk": "low", "confidence": 0.0, "explanation": "groq error"}, evidence
    text = resp.choices[0].message.content.strip().replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(text), evidence
    except Exception:
        return {"verdict": "insufficient", "risk": "low", "confidence": 0.0, "explanation": "parse error"}, evidence


def process_buffer_loop():
    while True:
        time.sleep(8)
        with buffer_lock:
            if not buffer:
                continue
            chunk_text = "\n".join(buffer)
            buffer.clear()
        print(f"[buffer] flushing: {chunk_text[:80]!r}")
        for item in extract_claims(chunk_text):
            verdict, evidence = verify_claim(item["claim"])
            _store_claim(item.get("speaker", "rep"), item["claim"],
                         verdict["verdict"], verdict["risk"], verdict["confidence"], verdict["explanation"])
            print(f"[buffer] stored claim: {verdict}")

threading.Thread(target=process_buffer_loop, daemon=True).start()


# ---------- API routes ----------

class Claim(BaseModel):
    call_id: str; speaker: str; text: str
    verdict: str; risk: str; confidence: float; evidence: str

@app.get("/api/claims")
def list_claims(call_id: str | None = None):
    return [c for c in CLAIMS if (call_id is None or c["call_id"] == call_id)]

@app.get("/api/audit-log")
def audit_log():
    return AUDIT_LOG

@app.get("/tools/get_risk_summary")
def get_risk_summary(call_id: str):
    calls = [c for c in CLAIMS if c["call_id"] == call_id]
    high = [c for c in calls if c["risk"] == "high"]
    if not high:
        return {"summary": f"No high risk claims yet. {len(calls)} claims checked so far."}
    latest = high[-1]
    return {
        "summary": f"{len(high)} high risk claim(s). Most recent: '{latest['text']}', verdict {latest['verdict']}.",
        "most_recent_claim_id": latest["id"],
    }

@app.get("/tools/explain_verdict")
def explain_verdict(claim_id: str):
    match = next((c for c in CLAIMS if c["id"] == claim_id), None)
    if not match:
        raise HTTPException(404, "claim not found")
    return {"explanation": f"Claim: '{match['text']}'. Verdict: {match['verdict']} at {round(match['confidence']*100)}% confidence. {match['evidence']}"}

class EscalateRequest(BaseModel):
    call_id: str
    reason: str

@app.post("/tools/escalate")
def escalate(body: EscalateRequest):
    _log({"event": "escalation", "call_id": body.call_id, "reason": body.reason, "ts": datetime.now(timezone.utc).isoformat()})
    return {"ok": True, "message": "Supervisor notified."}


# ---------- live mic audio over WebSocket ----------

@app.websocket("/ws/audio")
async def ws_audio(websocket: WebSocket):
    await websocket.accept()
    print("[ws] browser connected, starting AssemblyAI session")

    def on_turn(self, event: TurnEvent):
        if event.end_of_turn and event.transcript.strip():
            print(f"[heard] {event.transcript}")
            with buffer_lock:
                buffer.append(event.transcript)

    client = StreamingClient(StreamingClientOptions(api_key=AAI_KEY, api_host="streaming.assemblyai.com"))
    client.on(StreamingEvents.Turn, on_turn)
    client.connect(StreamingParameters(speech_model="universal-streaming-english", sample_rate=16000))

    try:
        while True:
            data = await websocket.receive_bytes()
            client.stream(data)
    except WebSocketDisconnect:
        print("[ws] browser disconnected")
    finally:
        client.disconnect(terminate=True)


# ---------- serve the dashboard itself ----------
app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "..", "frontend"), html=True), name="static")