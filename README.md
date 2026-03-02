# Agentic Workout Planner

A **Knowledge Representation & Reasoning (KRR)** workout planner with **agentic behavior**. It generates personalized **3–7 day weekly plans** using pyDatalog, respects user injuries, and adapts from end-of-week soreness feedback. Optional **Azure OpenAI** provides natural-language explanations per day and chat responses.

## Requirements

- **Python 3.8+**
- **pandas** — data handling and Excel
- **openpyxl** — read/write `.xlsx`
- **pyDatalog** — logical rules and querying
- **fastapi**, **uvicorn** — HTTP API
- **openai** — Azure OpenAI API for LLM explanations/chat
- **Node.js 18+** — React/Vite chat frontend

## Setup

```bash
pip install -r requirements.txt
```

Create the exercise database (if not present). All recommendations use `data/exercises_db.xlsx`:

```bash
python scripts/create_exercises_xlsx.py
```

### Azure OpenAI (optional)

To connect the LLM for daily explanations, add your Azure OpenAI settings in **one** of these ways:

**Option A – `.env` file (recommended)**  
1. Copy the example: `cp .env.example .env`  
2. Edit `.env` in the project root and set:
   - `AZURE_OPENAI_KEY` – your API key (Azure Portal → your resource → Keys and Endpoint)
   - `AZURE_OPENAI_ENDPOINT` – e.g. `https://your-resource.openai.azure.com`
   - `AZURE_OPENAI_MODEL` – your **deployment name** (e.g. `gpt-4o` or the name you gave in Azure)

**Option B – environment variables**  
Export before running (same names as above):

```bash
export AZURE_OPENAI_KEY="your-key"
export AZURE_OPENAI_ENDPOINT="https://your-resource.openai.azure.com"
export AZURE_OPENAI_MODEL="your-deployment-name"
```

The code that reads these is in **`agent/llm.py`** (it uses `os.environ`; with `python-dotenv` installed it also loads from `.env`). The app runs without them; explanations will show a fallback message until configured.

## Run

### 1. CLI (from project root)

```bash
python main.py
```

This creates/loads a default user (with one injury), generates a weekly plan (3–7 days depending on config/user), prints KRR reasoning per day and LLM explanations, then prompts for end-of-week soreness to update state for next week.

### 2. FastAPI + React chat frontend (recommended)

1. Start the Python API (from project root):

```bash
uvicorn api.app:app --reload --port 8000
```

2. In another terminal, install and run the React chat frontend:

```bash
cd web
npm install
npm run dev
```

3. Open **http://localhost:5173** in your browser.
   - Enter a **User ID**.
   - Answer the **onboarding questions in chat** (goals, days/week, injuries, intensity).
   - Say **"Generate my plan"** or click **Generate plan**.
   - Expand any day card to see exercises and use **See Thinking** to view KRR + LLM reasoning.
   - Use the chat panel to ask follow-up questions (e.g. why a day is rest, how to adjust for injuries).

## Project Structure

```
├── data/
│   ├── exercises_db.xlsx    # Exercise DB for all recommendations (name, target, difficulty)
│   ├── agent_state_*.json   # Per-user state (soreness, volume, missed sessions)
│   ├── profile_*.json       # Per-user profile (goals, days/week, injuries, intensity, feedback history)
│   └── plan_*.json          # Last generated plan + KRR reasoning per user
├── agent/
│   ├── agent_state.py       # AgentState, AgentGoals, persistence
│   ├── decision_policy.py   # DecisionPolicy, PlanEvaluation
│   ├── mcp.py               # Meta-control: state → goals → plan → evaluate
│   ├── krr.py               # pyDatalog KB: forbidden/recommended, reasoning
│   └── llm.py               # Azure OpenAI wrapper (explain_plan)
├── planner/
│   ├── weekly_planner.py    # Weekly planner, uses KRR + DecisionPolicy and dynamic splits (3–7 days)
│   └── splits.json          # JSON presets: for each days_per_week, which body parts to target per day
├── api/
│   └── app.py               # FastAPI: /api/plan, /api/profile, /api/explanation, /api/feedback, /api/chat
├── frontend/                # (legacy; Streamlit UI removed)
├── web/                     # Node.js (React + Vite) chat frontend
│   ├── src/App.jsx
│   └── package.json
├── scripts/
│   └── create_exercises_xlsx.py
├── main.py                  # Entry point
├── requirements.txt
└── README.md
```

## Dynamic Splits (3–7 days)

The planner does **not** hardcode a fixed number of days. Instead, it:

- reads presets from `planner/splits.json`, keyed by `days_per_week` (3–7)
- for each day defines:
  - a human‑readable theme (e.g. `"Chest"`, `"Upper Push"`, `"Core + Grip"`)
  - a list of **target muscles** that must match the `target` values in `exercises_db.xlsx`
- generates days labeled `Day1`…`DayN` using those presets.

Only **beginner** exercises are recommended; any exercise that trains a muscle in the user’s **injury list** is **forbidden** (KRR).

## Agentic Loop

1. **Memory** — AgentState (soreness per muscle, weekly volume, missed sessions).
2. **Goals** — AgentGoals (target soreness range 1–3, max sets per muscle per week).
3. **Decision** — DecisionPolicy adjusts sets/volume and prioritizes muscles.
4. **Plan** — Weekly planner uses KRR + policy → **N days (3–7)** from `splits.json`, up to 6 exercises per day.
5. **Evaluate** — PlanEvaluation flags overtraining and suggests modifications.
6. **Feedback** — User reports soreness → state updated → next week adapts.

## KRR (Knowledge Base)

- **Facts:** `is_exercise(E)`, `trains_muscle(E, M)`, `has_difficulty(E, D)`, `injured_muscle(User, M)`.
- **Rules:**  
  - `forbidden(User, E)` ← exercise trains an injured muscle.  
  - `recommended(User, E)` ← beginner and not forbidden.

Query by muscle or filter by injuries via `agent/krr.py`; reasoning text is shown per day in CLI and Streamlit.
