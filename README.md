# Intelligent Workout Planner (KRR-Based)

A **Knowledge Representation & Reasoning (KRR)** workout planner that generates personalized weekly plans using pyDatalog. It respects user injuries, uses a 5-day split, and adjusts intensity from soreness feedback. Data is stored in Excel (users, plans, feedback).

## Requirements

- **Python 3.8+**
- **pandas** — Excel and data handling  
- **openpyxl** — read/write `.xlsx`  
- **pyDatalog** — logical rules and querying

## Setup

```bash
pip install -r requirements.txt
```

## Run

From the project root:

```bash
python main.py
```

This creates/loads a user, generates the next week’s plan, prints it with KRR reasoning per day, and optionally prompts for feedback (how was the week 1–5, soreness 1–5). Intensity for the next week is updated from **soreness** (high soreness → lower intensity, low soreness → higher intensity).

## Project Structure

```
intelligent-workout-planner/
├── data/
│   ├── exercises_db.xlsx   # Exercise KB (name, target, difficulty, etc.) — required
│   ├── users.xlsx          # Users (created on first run)
│   ├── weekly_plans.xlsx   # Saved plans (one row per exercise per day)
│   ├── feedback.xlsx      # Feedback rows (created when user submits feedback)
│   └── excel_storage.py   # Read/write for all Excel files
├── models/
│   ├── user.py            # User (user_id, name, age, weight, experience_level, injury_muscles, current_intensity_level)
│   ├── exercise.py        # Exercise prescription (sets, reps, weight from DB + intensity scaling)
│   └── weekly_plan.py     # WeeklyPlanEntry, day_to_focus (5-day split mapping)
├── krr/
│   └── knowledge_base.py  # pyDatalog facts/rules, load exercises, forbidden/recommended queries, KRR reasoning text
├── planner/
│   └── weekly_planner.py  # WeeklyPlanner: generate plan, filter by day focus, write to Excel
├── main.py                # Entry point: user setup, generate_next_week, print plan, optional feedback
├── requirements.txt
└── README.md
```

## 5-Day Split

| Day | Focus           |
|-----|-----------------|
| 1   | Chest           |
| 2   | Back            |
| 3   | Legs            |
| 4   | Biceps/Triceps  |
| 5   | Shoulders       |

Each day has warmup, 4–6 exercises (filtered by that day’s target muscles), and cooldown. Only **beginner** exercises are recommended; any exercise that trains a muscle in the user’s `injury_muscles` is **forbidden**.

## Intensity (1–5)

- Stored per user as `current_intensity_level` (1–5).
- **Updated from soreness only** (not from “how was this week?” or completion):
  - **Soreness 4–5** → decrease level by 1 (min 1).
  - **Soreness 1–2** → increase level by 1 (max 5).
  - **Soreness 3** → no change.
- Intensity affects each exercise in the plan:
  - **Weight:** base weight × multiplier (0.5 at L1 → 1.0 at L5).
  - **Sets:** base sets + extra at higher levels (up to 5 sets).
  - **Reps:** base reps minus 2 per level (e.g. 12 → 4 from L1 to L5), min 4.

Base weights come from the exercise DB when present; otherwise they are estimated from exercise name and target muscle (see `models/exercise.py`).

## KRR (Knowledge Base)

**Facts (from data):**

- `is_exercise(E)` — E is in the exercise DB.
- `trains_muscle(E, M)` — exercise E targets muscle M (primary or secondary).
- `has_difficulty(E, D)` — E has difficulty D (e.g. `beginner`).
- `injured_muscle(User, M)` — user has M in their injury list.

**Rules:**

- `forbidden(User, E)` ← `trains_muscle(E, M)` ∧ `injured_muscle(User, M)`  
  (exercise is forbidden if it trains any injured muscle).
- `recommended(User, E)` ← `is_exercise(E)` ∧ `has_difficulty(E, beginner)` ∧ ¬`forbidden(User, E)`.

**Usage:** The planner queries `recommended(user_id, E)`, then filters results by the day’s theme (e.g. chest, back). For each day, a short **KRR reasoning** block is printed: Facts, Rules, and Inference (query + result, or ∅ and explanation if rest day).

## API (main usage)

- **User:**  
  `get_or_create_user(user_id, name=..., age=..., weight=..., experience_level="beginner", injury_muscles=[], current_intensity_level=1)`  
  `get_user(user_id)`
- **Plan:**  
  `generate_next_week(user_id)` — returns plan dict, writes to `weekly_plans.xlsx`. Uses `user.current_intensity_level` and KRR to choose exercises.
- **Feedback:**  
  `save_feedback(user_id, week_number, feedback_score, soreness_level)`  
  `update_intensity_from_feedback(user_id, soreness_level)` — adjusts intensity from soreness (called after saving feedback).

## Example Output (sketch)

```
══════════════════════════════════════════════════════════
  WEEK 1  ·  WORKOUT PLAN
══════════════════════════════════════════════════════════

  Day 1  │  Chest
──────────────────────────────────────────────────────────
  Warmup   5 min light cardio; dynamic stretches for target muscle.
  Exercise                        Sets × Reps       Weight
  ────────────────────────────────────────────────────────
  Barbell bench press                    3 × 12       24.0 kg
  Push-up                                3 × 12   bodyweight
  ...
  Cooldown 5 min light stretch; focus on worked muscle groups.
  ┌─ KRR Reasoning ─────────────────────────────────────
  │  Facts:
  │    injured_muscle(john_doe, delts)
  │  Rules:
  │    forbidden(U, E) ← trains_muscle(E, M) ∧ injured_muscle(U, M)
  │    recommended(U, E) ← is_exercise(E) ∧ difficulty(E, beginner) ∧ ¬forbidden(U, E)
  │  Inference (Day 1 — Chest):
  │    Query: recommended(john_doe, E) where E matches theme ['pectorals', 'chest', ...]
  │    Result: 5 exercise(s)
  │      ✓ recommended(john_doe, "Barbell bench press")
  │      ...
  └──────────────────────────────────────────────────────
...
══════════════════════════════════════════════════════════
```

## Data Files

- **users.xlsx:** `user_id`, `name`, `age`, `weight`, `experience_level`, `injury_muscles` (comma-separated), `current_intensity_level`.
- **weekly_plans.xlsx:** `user_id`, `week_number`, `day`, `exercise_name`, `sets`, `reps`, `weight`, `timestamp`.
- **feedback.xlsx:** `user_id`, `week_number`, `feedback_score`, `soreness_level`, `timestamp`.

`data/exercises_db.xlsx` must exist and contain at least columns such as `name`, `target`, `difficulty` (and optionally `secondaryMuscles`, warmup/cooldown, etc.). The planner only recommends exercises with difficulty `beginner`.
