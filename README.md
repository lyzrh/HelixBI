<div align="center">

<img src="docs/logo.svg" alt="Helix BI" width="240"/>

# Helix BI

**Conversational AgentBI for Manufacturing & Retail**

Ask in natural language → the Agent generates analysis code → safe execution in a Docker
sandbox → charts / tables / conclusions. Analysis assets are automatically captured as
Skills, Insights, and Dashboards — **the system gets smarter with every use**.

[Getting Started](#getting-started) · [Core Features](#core-features) · [Architecture](#architecture) · [Tech Stack](#tech-stack) · [Roadmap](#roadmap)

[简体中文](README.zh-CN.md) · **English**

</div>

---

## What is Helix BI?

Helix BI (绎数) is an open-source, enterprise-grade data-analysis agent workbench. It puts
conversational analysis and traditional BI asset accumulation on the same pipeline:

```
Connect data (files / databases) → Ask in chat (optional metric confirmation)
  → SSE streaming analysis (Skill hit: instant replay / miss: LLM codegen + self-repair)
  → Conclusions + charts + tables + follow-ups
  → Captured as Skills / pinned to dashboards
  → Scheduled insight scans → LLM business diagnosis → dashboards → exported reports
```

It ships with two industry semantic packs — **Retail Sales** and **Manufacturing
Production** (metric definitions, derived formulas, synonyms, time conventions) — and you
can always connect your own data.

## Screenshots

| Conversational Analysis (agent streaming) | Workbench |
|:---:|:---:|
| ![Conversational Analysis](screenshots/chat.png) | ![Workbench](screenshots/workbench.png) |

| Self-Service Analytics (drag & drop, zero tokens) | Active Insights (scheduled scans + overview) |
|:---:|:---:|
| ![Self-Service Analytics](screenshots/explore.png) | ![Active Insights](screenshots/insights.png) |

| Datasource Management (upload / database / SQL query) |
|:---:|
| ![Datasources](screenshots/datasources.png) |

## Core Features

| Module | Capabilities |
| --- | --- |
| **Conversational Analysis** | Ask in natural language; SSE streams step progress / code / terminal / charts / tables / conclusions; "confirm query first" mode lets you edit the QuerySpec before execution; automatic failure repair with retries (up to 3); one-click suggested follow-ups |
| **Self-Service Analytics** | Click / drag fields for instant charts (ECharts interactive rendering, fully local — zero tokens); switch freely among bar / line / pie / area / scatter / stacked charts; adjustable aggregation and sorting |
| **Scenario Agents** | Pre-built industry experts for retail sales and manufacturing production: bound semantic packs and datasources, opening messages, suggested questions, start/stop management — ready to chat out of the box |
| **Skill Library** | Verified analysis paths are automatically captured as reusable Skills; similar questions replay stored code instantly (sub-second); when column schemas change, skills serve as few-shot references for regeneration; usage / success-rate statistics |
| **Active Insights** | Scheduled scans across all datasources: metric jumps / sustained trends / outliers / top-share shifts / threshold breaches (partial months auto-excluded to avoid false alarms); new alerts get automatic LLM diagnosis (phenomenon → evidence → cause → recommendation); overview stat cards + status workflow |
| **Dashboards** | Pin charts / tables / conclusions from conversations and insight diagnoses in one click; grid-layout browsing; export to self-contained HTML reports |
| **Datasources** | CSV / Excel / Parquet upload; MySQL / PostgreSQL / SQLite connections (test before saving); DB tables materialized to parquet cache before entering the sandbox; data preview + **read-only SQL query** (executed on local sqlite — zero tokens) |
| **Semantic Layer** | Industry semantic packs define metrics (with derived formulas: yield, attainment rate, average order value, etc.), dimensions, synonyms, time conventions, and chart suggestions — injected into generation prompts to keep definitions consistent |
| **Settings Center** | Hot-reload LLM endpoints (DeepSeek / Zhipu / Qwen / any OpenAI-compatible API — takes effect on save, no restart); preferences (answer style / creativity / follow-up toggle / custom instructions); profile |
| **Reliability** | Network-isolated sandbox + CPU / memory limits + read-only data; `dahelper` JSON contract for returning results; SQLite metadata store in WAL mode |

## Architecture

```
┌───────────── React 18 + AntD 5 frontend (Vite) ─────────────┐
│ Workbench / Conversational Analysis / Self-Service /        │
│ Scenario Agents / Skills / Active Insights / Dashboards /   │
│ Datasources / Settings Center                                │
└───────────────────────────┬──────────────────────────────────┘
                  SSE streaming (spec/code/step/answer/chart…)
┌───────────────────────────▼──────────────────────────────────┐
│                    FastAPI backend (:8000)                    │
│  routers/   analysis(SSE) sessions datasources agents skills │
│             insights dashboards explore(SQL) settings usage  │
│  services/  analysis_runner     # LangGraph wrapper + runs   │
│             skill_engine       # capture / match / replay    │
│             insight_engine     # rule scans + LLM diagnosis  │
│             insight_scheduler  # scheduled scan loop         │
│             datasource         # files + DB + parquet cache  │
│  semantic/  industry packs (retail sales / manufacturing)    │
│  SQLite metadata (WAL): sessions/messages/runs/datasources/  │
│    agents/skills/insights/dashboards/settings/token usage    │
└───────────────────────────┬──────────────────────────────────┘
                            ▼
        LangGraph core (parse_intent → generate_code
          → execute → self-repair loop → summarize → followup)
                            ▼
        Docker sandbox: --network none, CPU/memory limits, read-only /data
        File datasources mounted directly; database data materialized
        to parquet before entering the sandbox
```

## Getting Started

### Prerequisites

- Python 3.11+
- Node.js 18+ (only needed for frontend development; production mode serves the built assets from the backend)
- Docker (Desktop on Windows/macOS, Engine on Linux) — required for sandbox execution; when offline, analysis / Skill replay are unavailable, everything else works

### 1. Backend

```bash
git clone https://github.com/lyzrh/HelixBI.git
cd HelixBI

python -m venv .venv
```

Install dependencies and create `.env`:

```bash
# Windows (CMD / PowerShell)
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env

# Linux / macOS
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and fill in `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `MODEL_NAME`.

`.env` supports any OpenAI-compatible endpoint (DeepSeek / Zhipu GLM / Qwen / local
vLLM…). You can also configure it at runtime in the Settings Center (top-right corner) —
changes take effect on save, no restart needed:

```ini
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_API_KEY=sk-xxx
MODEL_NAME=deepseek-chat
```

### 2. Sandbox image

```bash
# With Docker running; if Docker Hub is unreachable, pull the base image from a mirror first
docker pull docker.m.daocloud.io/library/python:3.11-slim
docker tag docker.m.daocloud.io/library/python:3.11-slim python:3.11-slim
docker build -t helix-sandbox:latest sandbox/
```

### 3. Run

```bash
# Windows (CMD / PowerShell)
.venv\Scripts\python -m uvicorn backend.main:app --port 8000

# Linux / macOS (venv activated)
python -m uvicorn backend.main:app --port 8000
```

Open <http://127.0.0.1:8000> and you're ready to go (sample datasources / scenario
agents / skills are preloaded).

The built-in sample data is generated demo data: retail sales covering ~6 months and
manufacturing ~3 months, both ending recently — "last 30 / 90 days" questions work
out of the box.

Frontend dev mode:

```bash
cd frontend && npm install && npm run dev   # http://localhost:5173 (proxied to :8000)
```

For production, just run `npm run build`; the output is served statically by the backend.

## Project Structure

```
backend/                # FastAPI service
  main.py               # entry point (CORS / static hosting / lifespan)
  models.py schemas.py  # ORM (metadata tables) and API models
  seed.py               # built-in datasources / agents / skills / dashboards
  routers/              # analysis sessions datasources agents skills
                        # insights dashboards explore settings usage misc
  services/             # analysis_runner skill_engine insight_engine
                        # insight_scheduler datasource report_export
semantics/              # industry semantic packs (retail_sales / manufacturing_production yaml)
app/                    # LangGraph core
  graph.py profiler.py sandbox.py prompts.py report.py semantic.py
sandbox/                # sandbox image (pandas/pyarrow/matplotlib/CJK fonts + dahelper)
frontend/               # React + AntD + Zustand + Vite
  src/pages/            # Chat Workbench Explore Agents Skills Insights
                        # Dashboards Datasources Usage
  src/stores/           # chatStore (SSE state machine) appStore
examples/               # sample data (retail / manufacturing CSV, generated demo data)
screenshots/            # README screenshots
uploads/ data/ runs/    # runtime directories (gitignored)
```

## Tech Stack

| Layer | Technology |
| --- | --- |
| Frontend | React 18 · Ant Design 5 · Zustand · ECharts · Vite |
| Backend | FastAPI · SQLAlchemy · SSE |
| Agent core | LangGraph · LangChain (OpenAI-compatible endpoints) |
| Execution | Docker sandbox (no network, resource limits, read-only data) · pandas / pyarrow / matplotlib |
| Metadata | SQLite (WAL mode) |
| Data access | CSV / Excel / Parquet files · MySQL / PostgreSQL / SQLAlchemy (materialized to parquet) |

## Design Philosophy

- **Definitions first**: industry semantic packs + QuerySpec confirmation — align on "what to compute" before writing code, preventing LLM improvisation from drifting metric definitions
- **Sandbox as safety net**: generated code always runs in a network-isolated Docker container with read-only data; results return through the `dahelper` JSON contract
- **Asset accumulation**: a successful analysis becomes a Skill (instant replay); a valuable finding gets pinned to a dashboard — the system grows stronger with use instead of starting from scratch every time
- **Proactive, not passive**: scheduled insight scans surface anomalies before the user even asks
- **Token discipline**: Skill replay skips the LLM entirely; self-service analytics and SQL queries are fully local; follow-up suggestions can be turned off; connection tests send only a max_tokens=1 probe
- **Brand identity**: the "Yizi" purple design system (primary `#5645D4` + deep navy `#0A1530`) with a double-helix logo — the purple strand stands for business data, the teal strand for analytical intelligence, and the nodes for accumulated analysis assets

Inspired by the product shape of FineBI NEXT, and by the practices of open-source
projects such as DB-GPT / PandasAI / Vanna / OpenCodeInterpreter in code-interpreter
execution, self-repair, and follow-up recommendation.

## Roadmap

- **R2**: dashboards rendered client-side (interactive ECharts instead of PNG), insight subscription push, multi-user support & permissions
- **R3**: regression evaluation (fixed question sets for accuracy), visual semantic-pack editor, metric lineage

## License

[MIT](LICENSE) © 2026 Helix BI Contributors
