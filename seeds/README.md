# Kalshi Cabinet Departure Simulation — Upload Guide

## Files

| File | MiroFish Field | Purpose |
|------|---------------|---------|
| `trump_cabinet_departure_kalshi.md` | `files` (drag-drop) | Reality seed — entity-dense document |
| `simulation_requirement.txt` | `simulation_requirement` text field | Guides ontology + includes market structure, strategy, agent distribution |

(`additional_context.txt` is kept for reference but its content is merged into `simulation_requirement.txt` since the UI has no additional_context field.)

## Option A: Upload via UI

1. Start the app: `npm run dev` (from repo root)
2. Open `http://localhost:3000`
3. Drag-drop `trump_cabinet_departure_kalshi.md` into the upload zone
4. Paste the full contents of `simulation_requirement.txt` into the "Simulation Requirement" field
5. Set project name: `Kalshi Cabinet Departure v1`
6. Click generate — this runs ontology generation, then proceed through the 5-step pipeline

## Option B: Upload via API

```bash
cd seeds/

# Step 1: Generate ontology + create project
curl -X POST http://localhost:5001/api/graph/ontology/generate \
  -F "files=@trump_cabinet_departure_kalshi.md" \
  -F "project_name=Kalshi Cabinet Departure v1" \
  -F "simulation_requirement=$(cat simulation_requirement.txt)"

# Response contains project_id and ontology. Save the project_id.
# Example: {"success": true, "data": {"project_id": "proj_abc123def456", ...}}

# Step 2: Build knowledge graph
curl -X POST http://localhost:5001/api/graph/build \
  -H "Content-Type: application/json" \
  -d '{"project_id": "proj_abc123def456"}'

# Response contains task_id. Poll for completion:
curl http://localhost:5001/api/graph/task/TASK_ID_HERE

# Step 3: Continue through UI for simulation config + run
# Open http://localhost:3000/process/proj_abc123def456
```

## Expected Ontology Types

The seed document is designed to produce these 10 entity types:

1. **CabinetOfficial** — Trump Cabinet members (Chavez-DeRemer, Hegseth, Bondi, Gabbard, etc.)
2. **PoliticalJournalist** — DC insider reporters (Haberman, Swan, Collins, Baker, etc.)
3. **PoliticalAnalyst** — Forecasters and commentators (Silver, Sabato, Bitecofer, etc.)
4. **PredictionTrader** — Market participants and analysts (Theo4, Domer, Lott, Alexander, etc.)
5. **CongressionalLeader** — Senators and committee chairs (Thune, Ernst, Durbin, etc.)
6. **MediaPersonality** — MAGA media and commentators (Carlson, Hannity, Bannon, Kirk, etc.)
7. **GovernmentAgency** — DOL, Pentagon, DOJ, ODNI, NLRB, White House, etc.
8. **MediaOutlet** — NYT, WaPo, Reuters, AP, CNN, Fox News, Politico, etc.
9. **Person** — Fallback for individuals not fitting above types
10. **Organization** — Fallback for orgs not fitting above types

## Expected Agent Count

The seed contains ~60-70 named entities, which should produce 60-70 agents. Distribution roughly maps to:
- Cabinet officials: ~10 agents
- Journalists: ~12 agents
- Political analysts: ~8 agents
- Prediction traders: ~8 agents
- Congressional leaders: ~8 agents
- MAGA media: ~8 agents
- Government agencies: ~8 agents (institutional accounts)
- Media outlets: ~8 agents (institutional accounts)

## Simulation Parameters

- **Rounds:** Start with 20-30 (under 40 for compute cost)
- **Platforms:** Both Twitter and Reddit enabled
- **LLM:** Qwen2.5:32b via Ollama, or any OpenAI-compatible API (Claude, GPT-4)
