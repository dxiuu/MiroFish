# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MiroFish is a swarm intelligence engine for multi-agent social media simulation. It builds knowledge graphs from uploaded documents, then runs thousands of autonomous agents on simulated Twitter/Reddit environments to predict social dynamics. The 5-step pipeline: **Upload → Graph Build → Simulation Config → Run Simulation → Generate Report → Chat with Agents**.

## Commands

```bash
# Setup
npm run setup:all       # Install all dependencies (frontend + backend)

# Development
npm run dev             # Start both backend (port 5001) and frontend (port 3000) concurrently
npm run backend         # Backend only: cd backend && uv run python run.py
npm run frontend        # Frontend only: cd frontend && npm run dev

# Build
npm run build           # Production frontend build

# Docker
docker compose up -d                              # Start MiroFish app (requires .env)
docker compose -f docker-compose.zep.yml up -d   # Start self-hosted Zep (zep + zep-nlp + zep-postgres)
```

**Backend package manager:** `uv` (not pip). Use `uv run python` to execute scripts.

**Python version:** 3.11 or 3.12 (locked in `pyproject.toml`).

**Required environment:** Copy `.env.example` to `.env` and fill in at minimum `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL_NAME`. Then choose a memory backend:
- **Self-hosted Zep (recommended):** Run `docker compose -f docker-compose.zep.yml up -d`, set `ZEP_API_URL=http://localhost:8000` in `.env`
- **Mem0 cloud:** Set `MEM0_KEY` in `.env`
- **Zep Cloud:** Set `ZEP_API_KEY` in `.env`

## Architecture

### Monorepo Structure

- `backend/` — Flask 3.0 API server on port 5001
- `frontend/` — Vue 3 + Vite SPA on port 3000 (proxies `/api` to backend in dev)

### Backend Layers

**API layer** (`backend/app/api/`): Three Flask blueprints — `graph.py`, `simulation.py`, `report.py`. Long-running operations are dispatched as async tasks tracked in `models/task.py`.

**Service layer** (`backend/app/services/`): Core logic. Key services:
- `ontology_generator.py` — LLM-driven design of entity/relationship types from document domain
- `graph_builder.py` — Extracts entities from text chunks and stores in knowledge graph
- `oasis_profile_generator.py` — Generates agent personas from graph entities
- `simulation_runner.py` — Manages subprocess lifecycle for OASIS simulations (1763 lines)
- `simulation_ipc.py` — IPC queue protocol between main server and simulation subprocesses
- `report_agent.py` — ReACT-pattern report generator with tool use (InsightForge, PanoramaSearch, QuickSearch, Interview tools)
- `zep_tools.py` — Memory search tools for knowledge graph (name is a historical artifact)

**Utils** (`backend/app/utils/`): `llm_client.py` wraps OpenAI SDK for any compatible LLM API; `zep_client.py` factory for Zep connections; `zep_adapter.py` OSS/Cloud compat layer; `mem0_client.py` wraps Mem0; `file_parser.py` handles PDF/TXT/MD parsing.

### External Services

The system depends on an LLM and a knowledge graph backend:
1. **OpenAI-compatible LLM** — Recommended: Alibaba Qwen via Dashscope (`LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1`)
2. **Knowledge graph** — One of:
   - **Self-hosted Zep OSS** (`ZEP_API_URL=http://localhost:8000`) — run via `docker-compose.zep.yml`; no usage limits
   - **Mem0 cloud** (`MEM0_KEY`) — managed service
   - **Zep Cloud** (`ZEP_API_KEY`) — has 5 req/min rate limit and episode cap on free plan

### Simulation Engine

Simulations run via `camel-oasis` (CAMEL-AI's social media simulation framework). Each simulation spawns a subprocess running parallel Twitter + Reddit agents. The subprocess communicates with the Flask server via IPC queues. Actions are logged as JSON and downloadable post-simulation.

### State Persistence

No SQL database. All state is file-based:
- Projects: `backend/uploads/projects/{project_id}/project.json` + uploaded files
- Simulation results: JSON action logs alongside simulation metadata
- ProjectStatus progression: `CREATED → ONTOLOGY_GENERATED → GRAPH_BUILDING → GRAPH_COMPLETED → FAILED`

### Frontend Routes

| Route | View | Purpose |
|-------|------|---------|
| `/` | Home.vue | Landing + project history |
| `/process/:projectId` | MainView.vue | 5-step workflow container |
| `/simulation/:id` | SimulationView.vue | Config preview |
| `/simulation/:id/start` | SimulationRunView.vue | Real-time monitor |
| `/report/:id` | ReportView.vue | Report display |
| `/interaction/:id` | InteractionView.vue | Chat with agents |

Frontend API clients mirror backend blueprints: `src/api/{graph,simulation,report}.js`.

## Key Patterns

**Async task pattern:** Long operations (graph build, simulation, report) are started via POST endpoints that return a `task_id`. Progress is polled via `GET /status?task_id=...` endpoints.

**LLM client:** All LLM calls go through `utils/llm_client.py`. The client supports an optional "boost" model for high-throughput tasks via `LLM_BOOST_*` env vars.

**ReACT report agent:** `report_agent.py` uses a tool-calling loop (default max 5 iterations, 2 reflection rounds). Tools in `zep_tools.py` search the knowledge graph at different granularities (deep/broad/quick). The `zep_` file prefix is a historical artifact.

**Zep client factory:** All services call `make_zep_client()` from `utils/zep_client.py` — never instantiate `Zep` directly. The factory wraps the client in `ZepAdapter` (`utils/zep_adapter.py`), which provides graceful fallbacks for 4 APIs not supported by Zep OSS: `set_ontology`, `add_batch`, `episode.processed` polling, and the `reranker` search parameter.

**Windows UTF-8:** `backend/run.py` configures UTF-8 console encoding. Simulation scripts monkey-patch `open()` to default UTF-8. Do not remove these workarounds.
