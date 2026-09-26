import os, json, time, threading, requests
from dotenv import load_dotenv
load_dotenv()

import assemblyai as aai
from assemblyai.streaming.v3 import (
    StreamingClient, StreamingClientOptions, StreamingEvents,
    StreamingParameters, TurnEvent,
)
from groq import Groq
import sounddevice as sd
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from backend.knowledge import load_policy, search_policy

AAI_KEY = os.environ["ASSEMBLYAI_API_KEY"]
GROQ_KEY = os.environ["GROQ_API_KEY"]
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
CALL_ID = os.environ.get("CALL_ID", "demo-call-1")

llm = Groq(api_key=GROQ_KEY)
GROQ_MODEL = "openai/gpt-oss-120b"
policy_chunks = load_policy(os.path.join(os.path.dirname(__file__), "..", "policy.txt"))

buffer: list[str] = []
buffer_lock = threading.Lock()

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
    print(f"[extract] sending to Groq: {chunk_text[:80]!r}")
    try:
        resp = llm.chat.completions.create(
            model=GROQ_MODEL,
            max_tokens=400,
            messages=[{"role": "user", "content": EXTRACT_PROMPT.format(chunk=chunk_text)}],
        )
    except Exception as e:
        print(f"[extract] GROQ CALL FAILED: {e}")
        return []
    text = resp.choices[0].message.content.strip()
    text = text.replace("```json", "").replace("```", "").strip()
    print(f"[extract] raw Groq reply: {text!r}")
    try:
        return json.loads(text)
    except Exception as e:
        print(f"[extract] JSON parse failed: {e}")
        return []


def verify_claim(claim_text: str):
    evidence_chunks = search_policy(claim_text, policy_chunks)
    evidence = "\n---\n".join(evidence_chunks) or "No matching policy excerpt found."
    try:
        resp = llm.chat.completions.create(
            model=GROQ_MODEL,
            max_tokens=250,
            messages=[{"role": "user", "content": VERIFY_PROMPT.format(claim=claim_text, evidence=evidence)}],
        )
    except Exception as e:
        print(f"[verify] GROQ CALL FAILED: {e}")
        return {"verdict": "insufficient", "risk": "low", "confidence": 0.0, "explanation": "groq error"}, evidence
    text = resp.choices[0].message.content.strip()
    text = text.replace("```json", "").replace("```", "").strip()
    print(f"[verify] raw Groq reply: {text!r}")
    try:
        return json.loads(text), evidence
    except Exception as e:
        print(f"[verify] JSON parse failed: {e}")
        return {"verdict": "insufficient", "risk": "low", "confidence": 0.0, "explanation": "parse error"}, evidence


def process_buffer_loop():
    while True:
        time.sleep(8)
        with buffer_lock:
            if not buffer:
                continue
            chunk_text = "\n".join(buffer)
            buffer.clear()
        print(f"[buffer] flushing chunk of {len(chunk_text)} chars")
        claims = extract_claims(chunk_text)
        print(f"[buffer] extracted {len(claims)} claim(s): {claims}")
        for item in claims:
            verdict, evidence = verify_claim(item["claim"])
            print(f"[buffer] verdict: {verdict}")
            try:
                r = requests.post(f"{BACKEND_URL}/api/claims", json={
                    "call_id": CALL_ID,
                    "speaker": item.get("speaker", "rep"),
                    "text": item["claim"],
                    "verdict": verdict["verdict"],
                    "risk": verdict["risk"],
                    "confidence": verdict["confidence"],
                    "evidence": verdict["explanation"],
                })
                print(f"[buffer] POST status: {r.status_code}")
            except Exception as e:
                print(f"[buffer] POST FAILED: {e}")


def on_turn(self, event: TurnEvent):
    if event.end_of_turn and event.transcript.strip():
        print(f"[heard] {event.transcript}")
        with buffer_lock:
            buffer.append(event.transcript)


def main():
    threading.Thread(target=process_buffer_loop, daemon=True).start()

    client = StreamingClient(StreamingClientOptions(api_key=AAI_KEY, api_host="streaming.assemblyai.com"))
    client.on(StreamingEvents.Turn, on_turn)
    client.connect(StreamingParameters(speech_model="universal-streaming-english", sample_rate=16000))

    print("Listening... press Ctrl+C to stop.")

    def audio_callback(indata, frames, time_info, status):
        client.stream(indata.tobytes())

    try:
        with sd.InputStream(channels=1, samplerate=16000, blocksize=800, dtype="int16", callback=audio_callback):
            while True:
                time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        client.disconnect(terminate=True)


if __name__ == "__main__":
    main()