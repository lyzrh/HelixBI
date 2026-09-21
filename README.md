<div align="center">

<img src="https://cdn.jsdelivr.net/gh/lyzrh/HelixBI@main/docs/logo.svg" alt="Helix BI" width="240"/>

# Helix BI

**Conversational AgentBI for Manufacturing & Retail**

Ask in natural language → the Agent generates analysis code → safe execution in a Docker
sandbox → charts / tables / conclusions. Analysis assets are automatically captured as
Skills, Insights, and Dashboards — **the system gets smarter with every use**.

[Getting Started](#getting-started) · [Core Features](#core-features) · [Auth & RBAC](#auth--rbac) · [Architecture](#architecture) · [API](#api-overview) · [Evaluation](#evaluation) · [Tech Stack](#tech-stack) · [Roadmap](#roadmap)

[简体中文](README.zh-CN.md) · **English**

</div>

---

## What is Helix BI?

Helix BI (绎数) is an open-source, enterprise-grade data-analysis agent workbench. It puts
conversational analysis and traditional BI asset accumulation on the same pipeline:

```
Sign in → pick a workspace (role / permissions / data scope follow)
  → Connect data (files / databases, owned by the workspace)
  → Ask in chat (optional metric confirmation)
  → SSE streaming analysis (Skill hit: instant replay / miss: LLM codegen + self-repair)
  → Conclusions + charts + tables + follow-ups
  → Captured as Skills / pinned to dashboards
  → Scheduled insight scans → LLM business diagnosis → dashboards → exported reports
```

It ships with two industry semantic packs — **Retail Sales** and **Manufacturing
Production** (metric definitions, derived formulas, synonyms, time conventions) — and you
can always connect your own data.

## Screenshots

| Login (Premium SaaS split view) | Workbench (brand gradient hero) |
|:---:|:---:|
| ![Login](https://cdn.jsdelivr.net/gh/lyzrh/HelixBI@main/screenshots/login.png) | ![Workbench](https://cdn.jsdelivr.net/gh/lyzrh/HelixBI@main/screenshots/workbench.png) |

| Conversational Analysis (agent streaming) | Self-Service Analytics (drag & drop, zero tokens) |
|:---:|:---:|
| ![Conversational Analysis](https://cdn.jsdelivr.net/gh/lyzrh/HelixBI@main/screenshots/chat.png) | ![Self-Service Analytics](https://cdn.jsdelivr.net/gh/lyzrh/HelixBI@main/screenshots/explore.png) |

| Active Insights (scheduled scans + overview) | Workspace Members (RBAC) |
|:---:|:---:|
| ![Active Insights](https://cdn.jsdelivr.net/gh/lyzrh/HelixBI@main/screenshots/insights.png) | ![Members](https://cdn.jsdelivr.net/gh/lyzrh/HelixBI@main/screenshots/members.png) |

| Datasource Management (upload / database / SQL query) |
|:---:|
| ![Datasources](https://cdn.jsdelivr.net/gh/lyzrh/HelixBI@main/screenshots/datasources.png) |

## Core Features

| Module | Capabilities |
| --- | --- |
| **Auth & RBAC** | Username / email + password sign-in (JWT) + self-registration (grants no roles); UserContext resolved from user → workspace membership → role → permissions, so the *same person can be an analyst in one workspace and a viewer in another*; 11 permission points across datasources / SQL / analysis / dashboards / skills / membership; admins manage members visually (add / re-role / remove, last-admin protection); authorization always enforced server-side — bypassing the UI still gets rejected |
| **Conversational Analysis** | Ask in natural language; SSE streams step progress / code / terminal / charts / tables / conclusions; "confirm query first" mode lets you edit the QuerySpec before execution; automatic failure repair with retries (up to 3); one-click suggested follow-ups |
| **Self-Service Analytics** | Click / drag fields for instant charts (ECharts interactive rendering, fully local — zero tokens); switch freely among bar / line / pie / area / scatter / stacked charts; adjustable aggregation and sorting |
| **Scenario Agents** | Pre-built industry experts for retail sales and manufacturing production: bound semantic packs and datasources, opening messages, suggested questions, start/stop management — ready to chat out of the box |
| **Skill Library** | Verified analysis paths are automatically captured as reusable Skills; **retrieval V2** scores candidates with 8 explainable signals and gates replay on metrics / dimensions / analysis type / sort direction / Top-N / time window / datasource fingerprint — a hit replays in sub-seconds with **zero LLM calls**, a miss or a failed replay safely returns to the full Agent; `global / workspace / user` scopes prevent cross-workspace leakage; usage / success-rate statistics |
| **Active Insights** | Scheduled scans across all datasources: metric jumps / sustained trends / outliers / top-share shifts / threshold breaches (partial months auto-excluded to avoid false alarms); new alerts get automatic LLM diagnosis (phenomenon → evidence → cause → recommendation); overview stat cards + status workflow |
| **Dashboards** | Pin charts / tables / conclusions from conversations and insight diagnoses in one click; grid-layout browsing; export to self-contained HTML reports |
| **Datasources** | CSV / Excel / Parquet upload; MySQL / PostgreSQL / SQLite connections (test before saving); **datasources belong to a workspace** — invisible to and unusable by other workspaces; DB tables materialized to parquet cache before entering the sandbox; data preview + **read-only SQL query** (executed on local sqlite — zero tokens) |
| **Semantic Layer** | Industry semantic packs define metrics (with derived formulas: yield, attainment rate, average order value, etc.), dimensions, synonyms, time conventions, and chart suggestions — injected into generation prompts to keep definitions consistent |
| **Settings Center** | Hot-reload LLM endpoints (DeepSeek / Zhipu / Qwen / any OpenAI-compatible API — takes effect on save, no restart); preferences (answer style / creativity / follow-up toggle / custom instructions); **UI language toggle (中文 / English)** — applies instantly to navigation / workbench / settings, persisted locally; profile |
| **Reliability** | Network-isolated sandbox + CPU / memory limits + read-only data; `dahelper` JSON contract for returning results; SQLite metadata store in WAL mode; authorization enforced three times: API layer, runtime entry, before tool execution |
| **Evaluation** | 65 fixed questions (retail / manufacturing / colloquial adversarial cases) + staged metric reports (semantic resolution / context injection / skill matching / replay admission); metric thresholds wired into pytest as CI gates |
| **Observability** | Every analysis run persists a `Run.trace`: per-stage latencies, LLM calls & tokens, skill hit mode, six result-acceptance checks, and **the acting user / workspace / role**; the frontend "run timeline" panel exposes it — skill replay's `LLM calls == 0` is data, not copy |

## Auth & RBAC

Login only *authenticates*. What you can see and do is decided entirely server-side from trusted data:

```
Login (username / email + password)
  ↓ authenticate: issue a JWT (identity only — no roles or permissions inside)
user_id
  ↓ User Resolver
current workspace (X-Workspace-Id header, defaults to the user's first workspace)
  ↓ Role / Permission / Data Scope
UserContext { user_id, username, workspace_id, role, permissions, data_scope }
  ↓
LangGraph Agent → LLM → permission check → Tools (sandbox execution) → DataSource → result
```

- **Roles are never self-selected**: they come from `user → workspace membership → role → permissions`, so the same person can be an analyst in the sales workspace and only a viewer in the marketing one.
- **Self-registration**: the "Register" entry on the login page creates an account (username / email / password, stored as pbkdf2 hashes); registration only establishes an identity and **grants no workspace and no role** — role fields in the request body are ignored. An admin then adds the user to a workspace via the Members page.
- **Membership management**: admins can add (by username) / re-role / remove members; the last admin of a workspace cannot be demoted or removed; member management only works on the admin's own workspace.
- **Workspace isolation everywhere**: datasources / sessions / runs / dashboards / insights / skills are filtered by workspace — cross-workspace reads get 404 and writes get 403; run artifacts, uploaded files and materialized caches are served through an authenticated file-download route (workspace-checked) instead of anonymous static mounts.
- **Permission points fully wired**: system-level writes (LLM settings, scenario agents, insight scheduling) require `workspace:manage`; insights distinguish read (`datasource:read`) / generate & diagnose (`analysis:execute`) / status change (`datasource:write`).
- **Permission points**: `datasource:read/write`, `sql:execute`, `analysis:create/execute`, `dashboard:read/write`, `skill:read/write`, `workspace:manage`, `member:manage`.
- **One single check entry**: everything goes through `permission_checker.has_permission(user_context, "datasource:write")` — no hard-coded role checks, and the LLM never participates in authorization.
- **The UI is not a security boundary**: hiding buttons is a UX nicety; a viewer calling the API directly still gets 403.
- **Data isolation**: datasources / skills / conversations / dashboards all carry a workspace owner and cross-workspace access is rejected; skills additionally have `global / workspace / user` scopes; legacy global dashboards (`workspace_id=NULL`) stay visible to all workspaces for compatibility.
- **Default account**: built-in administrator `admin / admin123` (**change it and create real accounts right after the first deployment**).
- **Local demo accounts**: run `python -m backend.devseed` to create `analyst1` / `viewer1` (password `Dev-12345678`; idempotent, manual-only, refused when `HELIX_ENV=production`).

Built-in roles and their permission sets:

| Role | Permissions |
| --- | --- |
| `admin` | all 11 |
| `analyst` | datasource read/write, SQL execution, analysis create/execute, dashboard read/write, skill read/write |
| `viewer` | datasource read, dashboard read, skill read |

## Why HelixBI?

| Approach | Pain point |
| --- | --- |
| **Traditional BI** | Modelling, definitions and dashboards are all pre-built by hand; unmodelled questions simply go unanswered |
| **LLM-only analytics** | Flexible but unreliable: definitions drift, code runs uncontrolled, one failure means starting over |

HelixBI adds four layers of constraint so that "flexible" and "trustworthy" hold at the
same time:

```
Semantic layer (semantic_packs)  definitions aligned first — no LLM improvisation
   ↓
QuerySpec confirmation           agree on "what to compute" before writing code
   ↓
Docker sandbox execution         no network + resource limits + read-only data
   ↓
Result acceptance + Skill capture  trusted conclusions become Skills — next time:
                                   instant replay, zero tokens
```

## Evaluation

`python -m backend.evaluation` prints a staged evaluation report (runs fully offline —
no Docker / LLM needed; sandbox-dependent stages honestly report "not collected" when
resources are missing instead of inventing numbers).

Current real metrics (65 fixed questions: retail 25 + manufacturing 25 + colloquial
adversarial 15):

```
Evaluation Report — HelixBI Agent Pipeline
Semantic resolution accuracy (strict)   90.8%   (59/65)
Semantic resolution accuracy (lenient)  93.8%   (61/65)
  Metric-set hit                        93.8%
  Dimension-set hit                     100.0%
  YoY / QoQ detection                   100.0%
  Metric-level P / R                    100.0% / 94.0%   F1 96.9%
  Dimension-level P / R                 100.0% / 100.0%  F1 100.0%
  Resolution latency p50 / p95          0.015 / 0.029 ms (deterministic, zero tokens)
Context-injection completeness          96.6%   (113/117 definitions)
  Rendered fidelity of resolved defs    100.0%  (113/113)
  Derived-formula injection             100.0%  (19/19)
Skill retrieval Top1 accuracy          78.5%   (65 queries / 33 captured paths; baseline 72.3%)
Skill retrieval Recall@3                90.8%   Recall@2 89.2%
Replay-admission correctness            100.0%  (65/65)  structural guard only
Skill Retrieval V2 (full path-signature grain: pack / metrics / dimensions / type /
                   ranking / time window)
  Retrieval Top1                       100.0%  (baseline 95.4%)
  Replay Precision / Recall            100.0% / 96.9%
  False Replay Rate                    0.0%    (baseline 4.6% — 3 cases → 0)
  Admission accuracy (adversarial)     100.0%  (20 cases; baseline 50.0%, false accepts 10 → 0)
  LLM calls / query                    0.09    (0.09 counting mis-replays; baseline 0.14)
Execution / self-repair / end-to-end    needs the Docker sandbox; "not collected"
                                        when unavailable
```

The evaluation is not decoration — it has already driven three real fixes: the colloquial
adversarial subset (60%) exposed missing synonyms ("地区") and ranking words ("最长") in
the packs; after fixing, standard phrasing reached 100%. Adding the semantic-resolution
skeleton to skill matching lifted Top1 from 64.6% to 72.3%, and Skill Retrieval V2 raised it
to 78.5% while driving false replays from 4.6% to 0. Metric thresholds also gate pytest
(`tests/evaluation/`) and CI.

### Skill Retrieval V2 / Replay Admission

Before this change a Skill was only a few-shot reference inside the analysis path (real
replay existed solely behind "run this Skill manually"), and the replay guard only checked
column subsets plus the pandas reader — so a Skill with **different metrics, dimensions,
sort direction or time window would still be replayed**, returning a "looks right, wrong
definition" answer. V2 turns this into an evaluable, explainable and safe retrieval +
admission system:

```
Query → Semantic Resolution → Candidate Retrieval → Top-K scoring (8 explainable signals)
      → Replay Admission (hard blockers + High/Medium/Low tiers) → Replay (0 LLM calls) or full Agent
```

- **Rather give up a replay than replay wrongly**: any mismatch in metrics / dimensions /
  analysis type / sort direction / Top-N / time window / datasource fingerprint / columns /
  reader sends the query to the Agent, and the exact reason is written to the trace;
- **No extra LLM calls**: every signal comes from deterministic semantic resolution and Skill
  metadata; weights live in `backend/config.py` and are calibrated with
  `--tune-weights --sensitivity` against the eval set;
- **Safe fallback**: a failed replay automatically falls back to the full Agent instead of
  returning an error;
- **Answers "why"**: `Run.trace.skill.retrieval` records candidates, per-signal scores, the
  admission verdict, rejection reason and fallback reason.

Full baseline-vs-V2 table, weight calibration and open issues:
[`docs/skill-retrieval-v2.md`](docs/skill-retrieval-v2.md)
(reproduce with `python -m backend.evaluation --benchmark`).

## Observability

Every run (including skill replays) persists a `Run.trace`:

- **Per-stage latencies**: intent → semantic resolution → skill matching → codegen → sandbox execution → summary
- **LLM usage**: call count (by node), input / output tokens, cost — always 0 for skill replays
- **Skill retrieval record**: candidate Skills with their 8 signal scores, the selected Skill,
  the admission verdict plus rejection reason, and whether it replayed or fell back to the
  Agent — "why was this not replayed / why was this Skill chosen" is answerable from the trace
- **Result acceptance**: execution ok / has artifacts / answer present / chart files really exist / well-formed tables / clean stderr — six checks decoupled from the boolean `ok`
- **Actor**: the user / workspace / role behind the run, for per-person and per-workspace auditing
- **Failures leave traces too**: failed runs write the same trace — that's the round you most want to inspect

The "run timeline" panel on historical chat messages exposes all of the above.

## Architecture

The backend is organised **by business domain**, not by technical layer: every directory
under `backend/` owns one capability, and `routers/` is a thin API layer while the real
logic lives in the domain modules.

```
┌───────────── React 18 + AntD 5 frontend (Vite) ─────────────┐
│ Workbench / Conversational Analysis / Self-Service /        │
│ Scenario Agents / Skills / Active Insights / Dashboards /   │
│ Datasources / Settings Center                                │
└───────────────────────────┬──────────────────────────────────┘
                  SSE streaming (spec/code/step/answer/chart…)
┌───────────────────────────▼──────────────────────────────────┐
│                    FastAPI backend (:8000)                    │
│  routers/    API layer: auth analysis(SSE) sessions           │
│              datasources agents skills insights dashboards    │
│              explore(SQL) settings usage misc                 │
│              (carries the sign-in and permission gates)       │
│  auth/       authentication & RBAC: UserContext resolution +  │
│              the single permission-check entry                │
│  agent/      Agent core: graph (LangGraph) + sandbox client   │
│  analysis/   Analysis Runtime (driving + persistence) +       │
│              self-service analytics (zero tokens)             │
│  skills/     Skill capture / retrieval / replay / few-shot    │
│  insights/   rule scans + scheduler + LLM diagnosis           │
│  datasource/ files + DB connections + parquet cache           │
│  semantic/   semantic-pack runtime (reads semantic_packs/)    │
│  report/     self-contained HTML export                       │
│  SQLite metadata (WAL): users/workspaces/members/roles/       │
│    permissions, sessions/messages/runs/datasources/agents/    │
│    skills/insights/dashboards/settings/token usage            │
└───────────────────────────┬──────────────────────────────────┘
                            ▼
        LangGraph core (parse_intent → generate_code
          → execute → self-repair loop → summarize → followup)
                            ▼
        Docker sandbox: --network none, CPU/memory limits, read-only /data
        File datasources mounted directly; database data materialized
        to parquet before entering the sandbox
```

Configuration vs runtime boundary: `semantic_packs/` is **configuration** (the single
source of truth for business semantics) while `backend/semantic/` is the **runtime**
(loading and rendering them into prompts).

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

# Token signing secret (when unset, a random one is generated per process —
# fine for single-process local dev only; multi-process / production
# deployments MUST set it, otherwise every restart invalidates logins)
HELIX_JWT_SECRET=replace-with-a-long-random-string
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

Open <http://127.0.0.1:8000> and sign in with the built-in administrator
`admin / admin123` (**change the password and create real accounts immediately**); the
sample datasources / scenario agents / skills are preloaded.

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
backend/                # FastAPI service (organised by business domain)
  main.py               # entry point (CORS / static hosting / lifespan)
  config.py             # single config entry (paths / LLM / sandbox / data / runtime dirs)
  db.py                 # SQLite engine (WAL) + startup migrations (new columns / RBAC tables)
  models.py schemas.py  # ORM (metadata tables + users/workspaces/roles/permissions) and API models
  seed.py               # RBAC baseline + built-in datasources / agents / skills / dashboards
  routers/              # API layer: auth analysis sessions datasources agents skills
                        #   insights dashboards explore settings usage misc
  auth/                 # authentication & RBAC: security (password + JWT), context
                        #   (UserContext + permission_checker), deps (sign-in / permission gates)
  agent/                # Agent core: graph prompts profiler sandbox
  analysis/             # Analysis Runtime (runtime) + self-service (explore)
  skills/               # Skill capture / retrieval (score+admit) / replay (scope-isolated)
  insights/             # rule scans (engine) + scheduler
  datasource/           # file / DB access + parquet materialization
  semantic/             # semantic-pack runtime (registry + render + resolver)
  evaluation/           # evaluation pipeline: datasets / metrics / runner (python -m backend.evaluation)
  report/               # export (builder for runs / exporter for dashboards)
semantic_packs/         # industry semantic packs (retail_sales / manufacturing_production yaml)
sandbox/                # standalone execution environment: sandbox image
                        #   (pandas/pyarrow/matplotlib/CJK fonts + dahelper contract)
frontend/               # React + AntD + Zustand + Vite
  src/pages/            # Login Chat Workbench Explore Agents Skills Insights
                        # Dashboards Datasources Usage
  src/stores/           # chatStore (SSE state machine) authStore (auth state) appStore
  src/api/              # client (token injection / 401 redirect) sse (stream reading)
tests/                  # pytest suite (evaluation/ metric gates + test_auth_rbac.py permission cases)
docs/                   # api.md (endpoint reference) + README images
examples/               # sample data (retail / manufacturing CSV, generated demo data)
screenshots/            # README screenshots
AGENTS.md               # universal AI-assistant rules (AGENTS standard)
.agents/                # agent collaboration docs: rules/ (on-demand) + plans/ (design decisions)
.claude/                # Claude Code config: settings + slash commands + subagents
uploads/ data/ runs/    # runtime directories (gitignored, created automatically on startup)
```

## API Overview

The full endpoint reference — including the permission each route requires, status-code
conventions and the SSE event protocol — lives in **[docs/api.md](docs/api.md)**; a live
interactive version is available at `/docs` once the server is running.

```bash
# Sign in (username or email + password)
curl -X POST http://127.0.0.1:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'
# → { "token": "eyJ...", "user": {...}, "workspaces": [...] }

# Call business endpoints with the token (X-Workspace-Id selects the workspace,
# defaults to the user's first one)
curl http://127.0.0.1:8000/api/datasources \
  -H "Authorization: Bearer $TOKEN" -H "X-Workspace-Id: 1"
```

| Group | Endpoints | Permission |
| --- | --- | --- |
| Auth / workspaces | `/api/auth/register` (public, identity only — no grants) `/login` (public) `/me` `/switch-workspace` `/roles` `/permissions` `/users` `/workspaces/{id}/members` (GET/POST/PATCH/DELETE) | signed in; user & membership management needs `workspace:manage` / `member:manage` |
| Chat & analysis | `/api/sessions...`, `/api/sessions/{id}/analyze`, `/api/runs/{id}/rerun`, `/api/runs/{id}/export` | `analysis:create` / `analysis:execute` (analysis endpoints are **SSE**) |
| Datasources & self-service | `/api/datasources...`, `/api/explore/schema` `/profile` `/query` `/sql` | `datasource:read` / `datasource:write` / `sql:execute` / `analysis:execute` |
| Skills / dashboards / insights | `/api/skills...`, `/api/dashboards...`, `/api/insights...` | `skill:read/write`, `dashboard:read/write`; insights require sign-in |
| Agents / settings / usage / utils | `/api/agents...`, `/api/settings...`, `/api/usage...`, `/api/utils/export_table` | signed in |
| Public | `/api/health`, `/api/semantic/packs`, `/api/stats` | none |

## Tech Stack

| Layer | Technology |
| --- | --- |
| Frontend | React 18 · Ant Design 5 · Zustand · ECharts · Vite |
| Backend | FastAPI · SQLAlchemy · SSE |
| Auth & authorization | JWT (HS256, stdlib implementation) · pbkdf2-sha256 password hashing · built-in RBAC (roles / permissions / workspaces) |
| Agent core | LangGraph · LangChain (OpenAI-compatible endpoints) |
| Execution | Docker sandbox (no network, resource limits, read-only data) · pandas / pyarrow / matplotlib |
| Metadata | SQLite (WAL mode) |
| Data access | CSV / Excel / Parquet files · MySQL / PostgreSQL / SQLAlchemy (materialized to parquet) |

## Design Philosophy

- **Definitions first**: industry semantic packs + QuerySpec confirmation — align on "what to compute" before writing code, preventing LLM improvisation from drifting metric definitions
- **Authorization first**: authentication only answers "who are you"; authorization comes from the server-resolved UserContext, checked through one entry and enforced at three points (API → runtime → tool) — button visibility in the UI is cosmetic
- **Sandbox as safety net**: generated code always runs in a network-isolated Docker container with read-only data; results return through the `dahelper` JSON contract
- **Asset accumulation**: a successful analysis becomes a Skill (instant replay); a valuable finding gets pinned to a dashboard — the system grows stronger with use instead of starting from scratch every time
- **Proactive, not passive**: scheduled insight scans surface anomalies before the user even asks
- **Token discipline**: Skill replay skips the LLM entirely; self-service analytics and SQL queries are fully local; follow-up suggestions can be turned off; connection tests send only a max_tokens=1 probe
- **Brand identity**: the "Yizi" purple design system (primary `#5645D4` + deep navy `#0A1530`) with a double-helix logo — the purple strand stands for business data, the teal strand for analytical intelligence, and the nodes for accumulated analysis assets

Inspired by the product shape of FineBI NEXT, and by the practices of open-source
projects such as DB-GPT / PandasAI / Vanna / OpenCodeInterpreter in code-interpreter
execution, self-repair, and follow-up recommendation.

## Roadmap

- **R2**: dashboards rendered client-side (interactive ECharts instead of PNG), insight subscription push, i18n for the remaining pages (the zh/en toggle already covers navigation / workbench / settings; the sign-in page and workspace selector are still Chinese-only); ~~multi-user support & permissions~~ (✅ shipped: sign-in / workspaces / UserContext / RBAC — see "Auth & RBAC")
- **R3**: ~~regression evaluation~~ (✅ shipped: `backend/evaluation/` + `tests/evaluation/` gates; next: grow the question set and collect sandbox-execution / self-repair metrics), visual semantic-pack editor, metric lineage
- **Security hardening (P1)**: authenticate the static artifact mounts (`/runs`, `/uploads`, `/data` are currently open), encrypt stored database passwords, refresh tokens & a revocation list, finer-grained permissions for agent / settings endpoints
- **Architecture (P2)**: `backend/routers/` → `api/` and `config/db/models/schemas` → `core/`; introduce `features/` domains in the frontend (see [.agents/rules/architecture.md](.agents/rules/architecture.md))

## License

[MIT](LICENSE) © 2026 Helix BI Contributors
