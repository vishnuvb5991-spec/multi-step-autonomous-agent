# Multi-Step Autonomous Customer Support Agent

**Project 5** — Difficulty 5/5

A **ReAct-style multi-step agent** that reasons, calls domain-specific tools, reads results, and decides again — with tracing, guardrails, and human sign-off before irreversible actions.

---

## Architecture

```
User / τ²-bench harness
        │
        ▼ POST /chat
   FastAPI (api/main.py)
        │
        ▼ ainvoke
   LangGraph ReAct Graph (agent/graph.py)
     ├── Guardrails (agent/guardrails.py)
     ├── Gemini 3.8 Flash (via langchain-google-genai)
     ├── MCP Tool Servers (mcp_servers/*.py)
     │     ├── retail_server.py   — orders, refunds, cancellations
     │     ├── airline_server.py  — bookings, flights, vouchers
     │     └── telecom_server.py  — accounts, network, credits
     ├── Human sign-off (interrupt() + POST /approve)
     └── Arize Phoenix tracing (tracing/setup.py)
```

## Benchmark

- **[τ²-bench](https://github.com/sierra-research/tau2-bench)** — customer support tasks across retail, airline, and telecom

## Tech Stack

| Layer | Technology |
|---|---|
| LLM | Gemini 3.8 Flash (`google-genai` ≥ 2.30.1) |
| Orchestration | LangGraph (`create_react_agent` + `StateGraph`) |
| Tools | MCP Python SDK (`FastMCP`) + `langchain-mcp-adapters` |
| Human approval | LangGraph `interrupt()` + `Command(resume=...)` |
| Tracing | Arize Phoenix (OpenTelemetry / OpenInference) |
| API | FastAPI + Uvicorn |
| Deploy | Docker + Cloud Run |

---

## Quick Start

### 1. Setup

```bash
cd multi-step-agent

# Create .env from template
cp .env.example .env
# Edit .env: set GEMINI_API_KEY=your_key_here

# Install (Python 3.12+)
pip install -e ".[dev]"
```

### 2. Run with Docker Compose (recommended)

```bash
# Starts Phoenix tracing UI + agent API
docker compose up --build
```

- Agent API: http://localhost:8080
- Phoenix UI: http://localhost:6006

### 3. Run locally (development)

```bash
# Terminal 1: Start Phoenix
python -m phoenix.server.main serve

# Terminal 2: Start agent
uvicorn api.main:app --reload --port 8080
```

---

## API Usage

### Start a conversation

```bash
curl -X POST http://localhost:8080/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "I want to return my order ORD-001", "domain": "retail"}'
```

Response:
```json
{
  "thread_id": "abc-123",
  "reply": "I looked up order ORD-001... Would you like a full refund of $89.99?",
  "requires_approval": false,
  "escalated": false,
  "step_count": 2
}
```

### Human approval for irreversible action

When `requires_approval: true`:

```bash
curl -X POST http://localhost:8080/approve \
  -H "Content-Type: application/json" \
  -d '{"thread_id": "abc-123", "approved": true}'
```

### Inspect thread state (debug)

```bash
curl http://localhost:8080/threads/abc-123
```

---

## Guardrails

| Layer | Checks | Action |
|---|---|---|
| Input | Prompt injection, jailbreak patterns, >4000 chars | Block + flag |
| Output | Credit card numbers, SSNs | Redact + flag |
| Policy | Domain rules (refund window, cancellation fees…) | Return policy error |
| Step limit | >10 ReAct iterations | Auto-escalate |
| Irreversible | cancel_order, process_refund, cancel_flight, apply_voucher, apply_bill_credit | interrupt() → human sign-off |

---

## Evaluation (τ²-bench)

```bash
# Ensure the API server is running first, then:
python eval/run_eval.py --domain retail airline telecom --k 3
```

Output:
```json
{
  "task_success_rate": 0.73,
  "tool_accuracy": 0.81,
  "escalation_rate": 0.07,
  "pass_k": 0.89,
  "n_tasks": 13,
  "per_domain": { ... }
}
```

**Metrics:**
- `task_success_rate` — fraction of tasks where agent reached the correct final state
- `tool_accuracy` — fraction of tool calls that matched the reference solution
- `escalation_rate` — fraction of tasks escalated to human (should be low)
- `pass^k` — P(task succeeds at least once in k independent trials)

---

## Running Tests

```bash
pytest tests/ -v
```

Expected: **18+ tests pass** (guardrails, metrics, tool policy checks).

---

## Cloud Run Deployment

```bash
# Build and push
gcloud builds submit --tag gcr.io/YOUR_PROJECT/multi-step-agent

# Deploy
gcloud run deploy multi-step-agent \
  --image gcr.io/YOUR_PROJECT/multi-step-agent \
  --region us-central1 \
  --set-env-vars GEMINI_API_KEY=your_key \
  --allow-unauthenticated
```

---

## Project Layout

```
multi-step-agent/
├── agent/
│   ├── graph.py          # LangGraph ReAct graph
│   ├── state.py          # AgentState TypedDict
│   ├── guardrails.py     # Input/output guardrails
│   └── prompts.py        # Domain system prompts
├── mcp_servers/
│   ├── retail_server.py  # FastMCP: retail tools
│   ├── airline_server.py # FastMCP: airline tools
│   └── telecom_server.py # FastMCP: telecom tools
├── api/
│   ├── main.py           # FastAPI routes
│   └── models.py         # Pydantic schemas
├── tracing/
│   └── setup.py          # Arize Phoenix setup
├── eval/
│   ├── run_eval.py       # τ²-bench harness
│   └── metrics.py        # pass^k, success rate, etc.
├── tests/                # Unit tests
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```
