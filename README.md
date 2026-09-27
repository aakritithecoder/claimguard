# 🛡️ ClaimGuard — Real-Time Compliance & Fact-Verification Voice Agent

**Built for the [AssemblyAI - Voice Agent Hackathon](https://lablab.ai/ai-hackathons/assemblyai-voice-agent-hackathon) on lablab.ai**

ClaimGuard listens to a live sales/support call, silently flags claims a rep makes that
contradict official company policy — and, unlike a passive dashboard, lets a supervisor
**talk to it out loud** mid-call to ask about risk, get an explanation for a flagged
claim, or escalate the call to a human. It's a genuine two-way voice agent, not just a
transcript viewer.

🔗 **Live demo:** https://claimguard-backend-cea0.onrender.com
🎥 **Demo video:** _[add link once recorded]_
📊 **Pitch deck:** _[add link]_

---

## The problem

Sales and support reps routinely make claims on live calls — refund windows, delivery
times, support hours — that don't match what the company actually promises. By the time
a compliance team reviews a recording, the customer has already been misled, and the
damage (a chargeback, a complaint, a regulatory flag) is already done. Nobody is
listening *live*.

## What ClaimGuard does

1. **Listens** to a live call in real time using AssemblyAI's Streaming STT.
2. **Extracts** factual, checkable claims from the conversation (ignoring small talk,
   opinions, and questions).
3. **Verifies** each claim against the company's own policy document.
4. **Flags** contradicted claims with a risk level, a confidence score, and the exact
   policy passage it conflicts with — all inside 10 seconds of the claim being spoken.
5. **Talks back.** A supervisor can ask ClaimGuard, out loud, mid-call:
   - *"What's the risk on this call right now?"*
   - *"Why was that claim flagged?"*
   - *"Escalate this call."*

   ClaimGuard answers in a synthesized voice, pulling live data from the same claims
   it just flagged — and can escalate to a human supervisor on command.

## Why this matters (and why it's a "voice agent," not just a transcript tool)

Most real-time fact-checking tools are silent dashboards — you have to go look at them.
ClaimGuard is built specifically to showcase what AssemblyAI's **Voice Agent API** adds
on top of plain transcription: a live, spoken, tool-calling conversation layered on top
of the same real-time listening pipeline. The listening half and the talking half share
the exact same backend and the exact same claims data.

---

## Architecture

```
                 ┌────────────────────────┐
  browser mic ──▶│  /ws/audio (FastAPI)   │
                 │  AssemblyAI Streaming  │
                 │  STT (universal-       │
                 │  streaming-english)    │
                 └───────────┬────────────┘
                             │ transcript buffer
                             ▼
                 ┌────────────────────────┐
                 │  Claim extraction      │
                 │  + policy verification │
                 │  (Groq LLM)            │
                 └───────────┬────────────┘
                             │ claim + verdict + risk
                             ▼
                 ┌────────────────────────┐        ┌─────────────────────┐
                 │  FastAPI backend       │◀──HTTP──│ AssemblyAI Voice     │
                 │  claim store + audit   │  tools  │ Agent API           │
                 │  log + tool endpoints  │         │ (Talk to Copilot)   │
                 └───────────┬────────────┘         └─────────────────────┘
                             │ /api/claims (poll)         ▲
                             ▼                             │ spoken Q&A,
                 ┌────────────────────────┐                │ tool calls
                 │  Live dashboard        │────────────────┘
                 │  (Monitor + Copilot    │  same page, one deployment
                 │  tabs, one page)       │
                 └────────────────────────┘
```

Everything — the listening pipeline, the dashboard, and the voice copilot — is served
from a single FastAPI app and a single deployed URL. No separate servers, no local
processes required to use it.

---

## AssemblyAI features used

- **Streaming Speech-to-Text** (`universal-streaming-english` model) — powers the live
  listening pipeline behind the Monitor tab, feeding the claim-extraction step.
- **Voice Agent API** — powers the "Talk to Copilot" tab: a full duplex conversational
  agent with barge-in / turn detection, built using AssemblyAI's official
  [voice-agent-starter-python](https://github.com/AssemblyAI/voice-agent-starter-python).
- **HTTP tool-calling** — the agent calls real backend endpoints
  (`get_risk_summary`, `explain_verdict`, `escalate_to_human`) mid-conversation, so its
  spoken answers are grounded in the actual claims the system flagged, not hallucinated.
- **60-second short-lived tokens** — the browser never sees the AssemblyAI API key; it's
  minted server-side and expires almost immediately, per AssemblyAI's recommended
  security pattern for browser-based voice agents.

## Other tech used

- **FastAPI** (Python) — single backend serving the dashboard, the claim store, the
  audit log, the tool endpoints, and the token-minting endpoint.
- **Groq** (`openai/gpt-oss-120b`) — claim extraction and policy-verification reasoning.
- **Vanilla JS + Web Audio API** (AudioWorklets) — real-time mic capture/playback in the
  browser for both the listening pipeline and the voice agent call, no frontend
  framework or build step.
- **Render** — permanent hosting for the backend + dashboard.

---

## Features

- **Live claim monitor**: real-time feed of every checkable claim made on a call, color
  coded by risk (low / medium / high), with confidence scores and the exact policy
  passage each claim was checked against.
- **One-click escalation**: flag any claim straight to a supervisor from the dashboard.
- **Talk to Copilot**: a full spoken conversation with the agent, live transcript, and a
  raw event log for anyone who wants to see exactly what's happening under the hood.
- **Hash-chained audit log**: every flagged claim and escalation is logged with a
  tamper-evident hash chain, so the compliance trail can't be silently edited after the
  fact.
- **Custom policy knowledge base**: swap in your own company's policy document
  (`policy.txt`) and every claim gets checked against your actual rules, not a generic
  fact-checker.

---

## Running it locally

### 1. Prerequisites
- Python 3.10+
- An [AssemblyAI API key](https://www.assemblyai.com/dashboard/api-keys)
- A [Groq API key](https://console.groq.com) (free, no card required)

### 2. Setup

```bash
git clone https://github.com/YOUR_USERNAME/claimguard.git
cd claimguard
pip install -r requirements.txt
```

Create a `.env` file:
```
ASSEMBLYAI_API_KEY=your_assemblyai_key
GROQ_API_KEY=your_groq_key
CALL_ID=demo-call-1
VOICE_AGENT_ID=your_published_agent_id
```

### 3. Set up the Voice Agent (one-time)

The voice agent itself is published separately using AssemblyAI's
[official starter kit](https://github.com/AssemblyAI/voice-agent-starter-python). Clone
it, point its HTTP tools at your deployed backend's `/tools/*` endpoints (see
`agents/claimguard-copilot.jsonc` in this repo for the exact tool config used), and run
its `publish.py` once to get an agent ID — put that ID in `VOICE_AGENT_ID` above.

### 4. Run

```bash
uvicorn backend.app:app --reload --port 8000
```

Open `http://localhost:8000` — one page, both the Monitor and Talk to Copilot tabs.

---

## Project structure

```
claimguard/
├── backend/
│   ├── app.py          # FastAPI app: claim store, audit log, tool endpoints,
│   │                   #   token minting, and the dashboard route
│   └── knowledge.py     # simple keyword-based policy retrieval
├── frontend/
│   └── index.html       # single-page dashboard (Monitor + Talk to Copilot tabs)
├── agents/
│   └── claimguard-copilot.jsonc   # Voice Agent config (system prompt + HTTP tools)
├── policy.txt            # example company policy the claims are checked against
├── requirements.txt
└── README.md
```

---

## What's next

- Move the claim store from in-memory to persistent storage (Postgres/SQLite), so data
  survives a redeploy.
- Replace keyword-based policy retrieval with embeddings for more accurate matching on
  longer policy documents.
- Real telephony integration (Twilio) so ClaimGuard can monitor an actual live phone
  call, not just a browser mic.
- PII redaction on the transcript before storage, for real-world compliance use.

---

## Team

_[add your team name / members here]_

Built with [AssemblyAI](https://www.assemblyai.com), [Groq](https://groq.com),
[FastAPI](https://fastapi.tiangolo.com), and [Render](https://render.com).
