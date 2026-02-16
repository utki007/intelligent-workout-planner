import pandas as pd
from pyDatalog import pyDatalog
import json
import os
from typing import Dict, List, Tuple
from datetime import datetime, timedelta

# ============================================================================
# USER STATE MANAGEMENT
# ============================================================================

USER_STATE_FILE = "user_state.json"
WEEKLY_PLANS_FILE = "weekly_plans.json"

def get_current_week_identifier() -> str:
    """Get current week identifier based on date (YYYY-WW format)."""
    today = datetime.now()
    year, week_num, _ = today.isocalendar()
    return f"{year}-W{week_num:02d}"

def get_next_week_number(user_id: str) -> int:
    """Get the next week number for a user."""
    if os.path.exists(WEEKLY_PLANS_FILE):
        with open(WEEKLY_PLANS_FILE, 'r') as f:
            plans = json.load(f)
            user_plans = plans.get(user_id, {})
            if user_plans:
                max_week = max([int(k) for k in user_plans.keys() if k.isdigit()], default=0)
                return max_week + 1
    return 1

def week_plan_exists(user_id: str, week_number: int) -> bool:
    """Check if a plan exists for a specific week."""
    if os.path.exists(WEEKLY_PLANS_FILE):
        with open(WEEKLY_PLANS_FILE, 'r') as f:
            plans = json.load(f)
            user_plans = plans.get(user_id, {})
            return str(week_number) in user_plans
    return False

def get_week_by_temporal_id(user_id: str, temporal_id: str) -> int:
    """Get week number for a temporal identifier, or create new week if not found."""
    if os.path.exists(WEEKLY_PLANS_FILE):
        with open(WEEKLY_PLANS_FILE, 'r') as f:
            plans = json.load(f)
            user_plans = plans.get(user_id, {})
            # Check if any week has this temporal_id
            for week_str, plan_data in user_plans.items():
                if plan_data.get("temporal_id") == temporal_id:
                    return int(week_str)
    # Return next week number if temporal_id not found
    return get_next_week_number(user_id)

def save_weekly_plan(user_id: str, week_number: int, plan_data: Dict):
    """Save weekly workout plan to file."""
    plans = {}
    if os.path.exists(WEEKLY_PLANS_FILE):
        with open(WEEKLY_PLANS_FILE, 'r') as f:
            plans = json.load(f)
    
    if user_id not in plans:
        plans[user_id] = {}
    
    plans[user_id][str(week_number)] = plan_data
    
    with open(WEEKLY_PLANS_FILE, 'w') as f:
        json.dump(plans, f, indent=2)

def load_weekly_plan(user_id: str, week_number: int) -> Dict:
    """Load a specific weekly plan."""
    if os.path.exists(WEEKLY_PLANS_FILE):
        with open(WEEKLY_PLANS_FILE, 'r') as f:
            plans = json.load(f)
            user_plans = plans.get(user_id, {})
            return user_plans.get(str(week_number), {})
    return {}

def get_all_weeks(user_id: str) -> List[int]:
    """Get list of all week numbers for a user."""
    if os.path.exists(WEEKLY_PLANS_FILE):
        with open(WEEKLY_PLANS_FILE, 'r') as f:
            plans = json.load(f)
            user_plans = plans.get(user_id, {})
            return sorted([int(k) for k in user_plans.keys() if k.isdigit()])
    return []

def display_weekly_plan(plan_data: Dict, week_number: int):
    """Display a weekly plan in a formatted way."""
    day_names = {
        1: "Chest",
        2: "Back", 
        3: "Legs",
        4: "Shoulders",
        5: "Arms"
    }
    
    print("\n" + "=" * 80)
    print(f"EXISTING WEEK {week_number} WORKOUT PLAN")
    print(f"Temporal Period: {plan_data.get('temporal_id', 'Unknown')}")
    print(f"Generated: {plan_data.get('generated_at', 'Unknown')}")
    print(f"Intensity Level: {plan_data.get('intensity_level', 'N/A')}/5")
    print("=" * 80)
    
    for day_num in range(1, 6):
        day_data = plan_data.get("days", {}).get(day_num, {})
        day_name = day_data.get("day_name", day_names.get(day_num, f"Day {day_num}"))
        exercises = day_data.get("exercises", [])
        
        print(f"\n{'='*80}")
        print(f"WEEK {week_number} - DAY {day_num} - {day_name.upper()}")
        print(f"{'='*80}")
        
        if exercises:
            print(f"\n📋 {len(exercises)} exercises:\n")
            for idx, ex in enumerate(exercises, 1):
                print(f"{'─'*80}")
                print(f"Exercise {idx}: {ex.get('exercise_name', 'Unknown')}")
                print(f"{'─'*80}")
                print(f"  📊 Prescription: {ex.get('sets', 'N/A')} sets × {ex.get('reps', 'N/A')} reps")
                weight = ex.get('weight_kg', 0)
                if weight > 0:
                    print(f"  ⚖️  Weight: {weight} kg")
                else:
                    print(f"  ⚖️  Weight: Bodyweight")
                print(f"  🎯 Target: {ex.get('target_muscle', 'N/A')}")
                if ex.get('secondary_muscles'):
                    print(f"  🔗 Secondary: {ex.get('secondary_muscles', '')}")
                print()
        else:
            print(f"\n⚠️  No exercises available for this day")
            if day_num == 4:
                print(f"   (Shoulder exercises filtered due to injury)")
        print()

def load_user_state(user_id: str) -> Dict:
    """Load user state from file."""
    if os.path.exists(USER_STATE_FILE):
        with open(USER_STATE_FILE, 'r') as f:
            states = json.load(f)
            return states.get(user_id, {"intensity_level": 1})
    return {"intensity_level": 1}

def save_user_state(user_id: str, state: Dict):
    """Save user state to file."""
    states = {}
    if os.path.exists(USER_STATE_FILE):
        with open(USER_STATE_FILE, 'r') as f:
            states = json.load(f)
    states[user_id] = state
    with open(USER_STATE_FILE, 'w') as f:
        json.dump(states, f, indent=2)

# ============================================================================
# EXERCISE INTENSITY CALCULATION
# ============================================================================

def calculate_weight_reps(exercise_name: str, intensity_level: int, base_weight: float = None) -> Tuple[float, int]:
    """
    Calculate weight and reps based on intensity level.
    Intensity 1 = beginner (lower weight, higher reps)
    Intensity 5 = advanced (higher weight, lower reps)
    """
    # Base values for beginner (intensity 1)
    if base_weight is None:
        # Estimate base weight from exercise name (simplified)
        base_weight = estimate_base_weight(exercise_name)
    
    base_reps = 12  # Base reps for beginner
    
    # Intensity multipliers
    weight_multiplier = 0.6 + (intensity_level - 1) * 0.1  # 0.6 to 1.0
    reps_adjustment = -(intensity_level - 1) * 2  # -8 to 0
    
    weight = round(base_weight * weight_multiplier, 1)
    reps = max(6, min(20, base_reps + reps_adjustment))
    
    return weight, reps

def estimate_base_weight(exercise_name: str) -> float:
    """Estimate base weight for an exercise (simplified estimation)."""
    name_lower = exercise_name.lower()
    
    # Bodyweight exercises
    if any(word in name_lower for word in ['push-up', 'pull-up', 'dip', 'squat', 'lunge', 'plank']):
        return 0.0
    
    # Weight estimates (in kg)
    weight_map = {
        'bench': 60.0, 'press': 50.0, 'row': 40.0, 'curl': 10.0,
        'deadlift': 80.0, 'squat': 70.0, 'shoulder': 20.0,
        'fly': 15.0, 'extension': 8.0, 'raise': 5.0
    }
    
    for key, weight in weight_map.items():
        if key in name_lower:
            return weight
    
    return 20.0  # Default

# ============================================================================
# DYNAMIC EXPLAINABILITY
# ============================================================================

class ExplainabilityEngine:
    """Generates dynamic explanations for exercise selection/rejection."""
    
    def __init__(self, df: pd.DataFrame):
        self.df = df
        self.explanations = []
    
    def explain_exercise_selection(self, exercise_name: str, user_id: str, day: int, 
                                   intensity_level: int) -> Dict:
        """Generate explanation for why an exercise was selected."""
        exercise_row = self.df[self.df['name'] == exercise_name]
        if exercise_row.empty:
            return {"status": "not_found", "explanation": f"Exercise '{exercise_name}' not found in database"}
        
        row = exercise_row.iloc[0]
        target = str(row.get('target', '')).lower()
        difficulty = str(row.get('difficulty', '')).lower()
        secondary = str(row.get('secondaryMuscles', ''))
        
        # Check if forbidden
        is_forbidden = False
        forbidden_reason = ""
        try:
            forbidden_result = forbidden(user_id, exercise_name)  # type: ignore
            is_forbidden = len(forbidden_result) > 0
            if is_forbidden:
                # Find which injured muscle causes the ban
                injured_muscles = []
                try:
                    injured_result = injured(user_id, "M")  # type: ignore
                    for item in injured_result:
                        if isinstance(item, tuple) and len(item) > 1:
                            injured_muscles.append(item[1])
                except:
                    pass
                
                # Check which muscle this exercise trains that's injured
                trains_result = trains(exercise_name, "M")  # type: ignore
                for item in trains_result:
                    if isinstance(item, tuple) and len(item) > 1:
                        muscle = item[1]
                        if muscle in injured_muscles:
                            forbidden_reason = f"trains injured muscle '{muscle}'"
                            break
        except:
            pass
        
        # Check if matches day theme
        matches_day_result = False
        theme_muscles = []
        try:
            matches_result = matches_day(day, exercise_name)  # type: ignore
            matches_day_result = len(matches_result) > 0
            
            # Get theme muscles for this day
            theme_result = theme(day, "M")  # type: ignore
            for item in theme_result:
                if isinstance(item, tuple) and len(item) > 1:
                    theme_muscles.append(item[1])
                elif isinstance(item, str):
                    theme_muscles.append(item)
        except Exception as e:
            # Fallback: use predefined day muscle map
            day_muscle_map = {
                1: ["pectorals", "serratus anterior"],
                2: ["lats", "traps", "upper back", "spine"],
                3: ["quads", "hamstrings", "glutes", "calves", "adductors", "abductors"],
                4: ["delts"],
                5: ["biceps", "triceps", "forearms"]
            }
            theme_muscles = day_muscle_map.get(day, [])
        
        # Check if valid (beginner + not forbidden)
        is_valid = False
        try:
            valid_result = valid(user_id, exercise_name)  # type: ignore
            is_valid = len(valid_result) > 0
        except:
            pass
        
        # Calculate weight and reps
        weight, reps = calculate_weight_reps(exercise_name, intensity_level)
        
        explanation = {
            "exercise": exercise_name,
            "selected": is_valid and matches_day_result and not is_forbidden,
            "difficulty": difficulty,
            "target_muscle": target,
            "secondary_muscles": secondary,
            "intensity_level": intensity_level,
            "weight_kg": weight,
            "reps": reps,
            "sets": 3 if intensity_level <= 2 else 4,
            "reasoning": []
        }
        
        # Build simplified KRR-style reasoning chain
        # Get injured muscles list for reasoning
        injured_muscles_list = []
        try:
            injured_result = injured(user_id, "M")  # type: ignore
            for item in injured_result:
                if isinstance(item, tuple) and len(item) > 1:
                    injured_muscles_list.append(item[1])
                elif isinstance(item, str):
                    injured_muscles_list.append(item)
        except:
            pass
        
        # Build concise reasoning
        if is_forbidden:
            # Extract muscle name from forbidden_reason
            injured_muscle_name = injured_muscles_list[0] if injured_muscles_list else 'muscle'
            if "'" in forbidden_reason:
                injured_muscle_name = forbidden_reason.split("'")[1]
            
            explanation["reasoning"].append(f"forbidden({user_id}, {exercise_name}) = TRUE")
            explanation["reasoning"].append(f"  → injured({user_id}, {injured_muscle_name}) & trains({exercise_name}, {injured_muscle_name})")
            explanation["selected"] = False
        else:
            explanation["reasoning"].append(f"forbidden({user_id}, {exercise_name}) = FALSE")
            explanation["reasoning"].append(f"  → No conflict with injured muscles")
        
        if difficulty == 'beginner':
            explanation["reasoning"].append(f"difficulty({exercise_name}, 'beginner') = TRUE")
        else:
            explanation["reasoning"].append(f"difficulty({exercise_name}, 'beginner') = FALSE (actual: {difficulty})")
        
        if is_valid:
            explanation["reasoning"].append(f"valid({user_id}, {exercise_name}) = TRUE")
            explanation["reasoning"].append(f"  → difficulty('beginner') & ~forbidden")
        else:
            explanation["reasoning"].append(f"valid({user_id}, {exercise_name}) = FALSE")
            if difficulty != 'beginner':
                explanation["reasoning"].append(f"  → difficulty ≠ 'beginner'")
            if is_forbidden:
                explanation["reasoning"].append(f"  → exercise is forbidden")
            explanation["selected"] = False
        
        if matches_day_result:
            theme_str = ', '.join(theme_muscles[:2]) if theme_muscles else "target"
            explanation["reasoning"].append(f"matches_day({day}, {exercise_name}) = TRUE")
            explanation["reasoning"].append(f"  → trains({target}) matches theme({day}, {theme_str})")
        else:
            explanation["reasoning"].append(f"matches_day({day}, {exercise_name}) = FALSE")
            theme_str = ', '.join(theme_muscles[:2]) if theme_muscles else "target muscles"
            explanation["reasoning"].append(f"  → {target} does not match Day {day} theme ({theme_str})")
            explanation["selected"] = False
        
        # Final conclusion
        if explanation["selected"]:
            explanation["reasoning"].append(f"plan({user_id}, {day}, {exercise_name}) = TRUE ✓")
            explanation["reasoning"].append(f"  → valid & matches_day")
        else:
            explanation["reasoning"].append(f"plan({user_id}, {day}, {exercise_name}) = FALSE ✗")
            if not is_valid:
                explanation["reasoning"].append(f"  → fails valid check")
            if not matches_day_result:
                explanation["reasoning"].append(f"  → fails theme match")
        
        return explanation
    
    def explain_no_exercises_found(self, user_id: str, day: int) -> List[str]:
        """Generate KRR-style reasoning when no valid exercises are found for a day."""
        reasoning = []
        
        # Get injured muscles
        injured_muscles_list = []
        try:
            injured_result = injured(user_id, "M")  # type: ignore
            for item in injured_result:
                if isinstance(item, tuple) and len(item) > 1:
                    injured_muscles_list.append(item[1])
                elif isinstance(item, str):
                    injured_muscles_list.append(item)
        except:
            pass
        
        # Get theme muscles for this day
        theme_muscles = []
        try:
            theme_result = theme(day, "M")  # type: ignore
            for item in theme_result:
                if isinstance(item, tuple) and len(item) > 1:
                    theme_muscles.append(item[1])
                elif isinstance(item, str):
                    theme_muscles.append(item)
        except:
            # Fallback: use predefined day muscle map
            day_muscle_map = {
                1: ["pectorals", "serratus anterior"],
                2: ["lats", "traps", "upper back", "spine"],
                3: ["quads", "hamstrings", "glutes", "calves", "adductors", "abductors"],
                4: ["delts"],
                5: ["biceps", "triceps", "forearms"]
            }
            theme_muscles = day_muscle_map.get(day, [])
        
        reasoning.append(f"Query: plan({user_id}, {day}, E)")
        reasoning.append(f"Result: ∅ (empty set)")
        reasoning.append("")
        reasoning.append("Analysis:")
        
        # Check if theme muscles are injured
        injured_theme_muscles = []
        if injured_muscles_list and theme_muscles:
            injured_theme_muscles = [m for m in theme_muscles if m in injured_muscles_list]
        
        if injured_theme_muscles:
            reasoning.append(f"  All theme muscles for Day {day} are injured:")
            for muscle in injured_theme_muscles:
                reasoning.append(f"    injured({user_id}, {muscle})")
            reasoning.append(f"  → theme({day}, M) & injured({user_id}, M) for all M")
            reasoning.append(f"  → All exercises matching Day {day} theme are forbidden")
            reasoning.append(f"  → No valid exercises can match Day {day} theme")
        else:
            reasoning.append(f"  Checking constraints:")
            reasoning.append(f"    Rule: plan(U, D, E) <= valid(U, E) & matches_day(D, E)")
            reasoning.append(f"    Rule: valid(U, E) <= difficulty(E, 'beginner') & ~forbidden(U, E)")
            reasoning.append(f"    Rule: matches_day(D, E) <= theme(D, M) & trains(E, M)")
            reasoning.append("")
            reasoning.append(f"  Possible reasons for empty result:")
            reasoning.append(f"    1. No exercises train theme muscles: {', '.join(theme_muscles[:3])}")
            reasoning.append(f"    2. All matching exercises are non-beginner difficulty")
            reasoning.append(f"    3. All matching exercises are forbidden (train injured muscles)")
        
        if injured_muscles_list:
            reasoning.append("")
            reasoning.append(f"  Injured muscles: {', '.join(injured_muscles_list)}")
            reasoning.append(f"  → Exercises training these muscles are forbidden")
        
        reasoning.append("")
        reasoning.append(f"Conclusion: plan({user_id}, {day}, E) = ∅")
        reasoning.append(f"  → No exercise E satisfies: valid({user_id}, E) & matches_day({day}, E)")
        
        return reasoning

# ============================================================================
# MAIN SYSTEM
# ============================================================================

# Load exercise data
df = pd.read_excel('data/exercises_db.xlsx', engine='openpyxl')
print("=" * 80)
print("KRR-BASED WORKOUT PLANNER WITH DYNAMIC EXPLAINABILITY")
print("=" * 80)
print(f"\nLoaded {len(df)} exercises from database")
print(f"Columns: {', '.join(df.columns.tolist())}")

# Clear any previous state
pyDatalog.clear()

# -----------------------------
# 1️⃣ Create Logical Terms
# -----------------------------
pyDatalog.create_terms('User, Exercise, Muscle, Day')
pyDatalog.create_terms('trains, difficulty, injured, theme')
pyDatalog.create_terms('forbidden, valid, matches_day, plan')
pyDatalog.create_terms('U, E, M, D, Level')

# -----------------------------
# 2️⃣ Define Rules
# -----------------------------

# Injury rule: exercise is forbidden if it trains an injured muscle
forbidden(U, E) <= injured(U, M) & trains(E, M)  # type: ignore

# Valid exercise rule (beginner + not forbidden)
valid(U, E) <= difficulty(E, 'beginner') & ~forbidden(U, E)  # type: ignore

# Match exercise to day theme
matches_day(D, E) <= theme(D, M) & trains(E, M)  # type: ignore

# Final workout plan rule
plan(U, D, E) <= valid(U, E) & matches_day(D, E)  # type: ignore

# -----------------------------
# 3️⃣ Load Exercise Data
# -----------------------------

print("\nLoading exercise data into knowledge base...")
exercise_count = 0
for _, row in df.iterrows():
    exercise_name = str(row["name"]).strip()
    target_muscle = str(row["target"]).strip().lower() if pd.notna(row["target"]) else ""
    difficulty_level = str(row["difficulty"]).strip().lower() if pd.notna(row["difficulty"]) else ""
    
    # Assert primary target muscle
    if target_muscle:
        pyDatalog.assert_fact("trains", exercise_name, target_muscle)
        exercise_count += 1
    
    # Assert secondary muscles if they exist
    if pd.notna(row.get("secondaryMuscles")):
        secondary = str(row["secondaryMuscles"]).strip()
        if secondary:
            for sec_muscle in secondary.split(','):
                sec_muscle = sec_muscle.strip().lower()
                if sec_muscle:
                    pyDatalog.assert_fact("trains", exercise_name, sec_muscle)
    
    # Assert difficulty
    if difficulty_level:
        pyDatalog.assert_fact("difficulty", exercise_name, difficulty_level)

print(f"✓ Loaded {exercise_count} exercise-muscle relationships")

# -----------------------------
# 4️⃣ Define 5-Day Split
# -----------------------------
day_muscle_map = {
    1: ["pectorals", "serratus anterior"],
    2: ["lats", "traps", "upper back", "spine"],
    3: ["quads", "hamstrings", "glutes", "calves", "adductors", "abductors"],
    4: ["delts"],
    5: ["biceps", "triceps", "forearms"]
}

for day, muscles in day_muscle_map.items():
    for muscle in muscles:
        pyDatalog.assert_fact("theme", day, muscle)

# -----------------------------
# 5️⃣ Define User and Injuries
# -----------------------------
USER_ID = "john"
pyDatalog.assert_fact("User", USER_ID)

# Example injury
pyDatalog.assert_fact("injured", USER_ID, "delts")  # User has injured shoulders

# Load user state
user_state = load_user_state(USER_ID)
intensity_level = user_state["intensity_level"]

print(f"\n✓ User: {USER_ID}")
print(f"✓ Current Intensity Level: {intensity_level}/5")
print(f"✓ Injured Muscles: delts")

# -----------------------------
# 6️⃣ Determine Week Number with Temporal Tracking
# -----------------------------

# Get current temporal identifier (year-week)
temporal_id = get_current_week_identifier()
print(f"✓ Current Week Identifier: {temporal_id}")

# Check if plan already exists for this temporal period
existing_week = get_week_by_temporal_id(USER_ID, temporal_id)
plan_exists = False

if existing_week > 0 and week_plan_exists(USER_ID, existing_week):
    existing_plan = load_weekly_plan(USER_ID, existing_week)
    plan_exists = True
    
    print(f"\n⚠️  PLAN ALREADY EXISTS FOR THIS WEEK!")
    print(f"   Week {existing_week} plan was generated on: {existing_plan.get('generated_at', 'Unknown')}")
    print(f"   Temporal ID: {existing_plan.get('temporal_id', 'Unknown')}")
    
    # Display the existing plan
    display_weekly_plan(existing_plan, existing_week)
    
    print("\n" + "=" * 80)
    print("SYSTEM READY")
    print("=" * 80)
    print("\n💡 To generate next week's plan, run the system again.")
    print("💡 The system will automatically prevent duplicate plans for the same week.")
    print("=" * 80)
    exit(0)

# Get next week number
week_number = get_next_week_number(USER_ID)
print(f"✓ Generating Week {week_number} workout plan")
print(f"✓ Temporal ID: {temporal_id}")

# -----------------------------
# 7️⃣ Generate Plan with Explainability
# -----------------------------

explainer = ExplainabilityEngine(df)

day_names = {
    1: "Chest",
    2: "Back", 
    3: "Legs",
    4: "Shoulders",
    5: "Arms"
}

print("\n" + "=" * 80)
print(f"GENERATING WEEK {week_number} - 5-DAY WORKOUT PLAN")
print(f"Temporal Period: {temporal_id}")
print("=" * 80)

# Store plan data for this week
weekly_plan_data = {
    "week_number": week_number,
    "user_id": USER_ID,
    "intensity_level": intensity_level,
    "temporal_id": temporal_id,
    "generated_at": datetime.now().isoformat(),
    "days": {}
}

for day in range(1, 6):
    print(f"\n{'='*80}")
    print(f"WEEK {week_number} - DAY {day} - {day_names[day].upper()}")
    print(f"{'='*80}")
    
    # Store exercises for this day
    day_exercises = []
    
    try:
        # Query pyDatalog for exercises
        exercises = plan(USER_ID, day, E)  # type: ignore
        
        if exercises:
            # Remove duplicates
            seen = set()
            unique_exercises = []
            for e in exercises:
                ex_name = e[0] if isinstance(e, tuple) else e
                if ex_name not in seen:
                    seen.add(ex_name)
                    unique_exercises.append(ex_name)
            
            # Limit to top 5 exercises
            selected_exercises = unique_exercises[:5]
            
            print(f"\n📋 Found {len(exercises)} valid exercises, selecting top {len(selected_exercises)}:\n")
            
            for idx, ex_name in enumerate(selected_exercises, 1):
                explanation = explainer.explain_exercise_selection(ex_name, USER_ID, day, intensity_level)
                
                print(f"{'─'*80}")
                print(f"Exercise {idx}: {ex_name}")
                print(f"{'─'*80}")
                
                if explanation["selected"]:
                    print(f"  ✅ SELECTED")
                    print(f"  📊 Prescription: {explanation['sets']} sets × {explanation['reps']} reps")
                    if explanation['weight_kg'] > 0:
                        print(f"  ⚖️  Weight: {explanation['weight_kg']} kg")
                    else:
                        print(f"  ⚖️  Weight: Bodyweight")
                    print(f"  🎯 Target: {explanation['target_muscle']}")
                    if explanation['secondary_muscles']:
                        print(f"  🔗 Secondary: {explanation['secondary_muscles']}")
                    
                    # Store exercise data
                    day_exercises.append({
                        "exercise_name": ex_name,
                        "sets": explanation['sets'],
                        "reps": explanation['reps'],
                        "weight_kg": explanation['weight_kg'],
                        "target_muscle": explanation['target_muscle'],
                        "secondary_muscles": explanation['secondary_muscles'],
                        "difficulty": explanation['difficulty'],
                        "reasoning": explanation['reasoning']
                    })
                else:
                    print(f"  ❌ NOT SELECTED")
                
                print(f"\n  KRR:")
                for reason in explanation["reasoning"]:
                    print(f"    {reason}")
                print()
            
            # Store day data
            weekly_plan_data["days"][day] = {
                "day_name": day_names[day],
                "exercises": day_exercises,
                "total_exercises": len(day_exercises)
            }
        else:
            print(f"\n⚠️  No valid exercises available for Day {day}")
            
            # Generate KRR-style reasoning for why no exercises found
            no_exercises_reasoning = explainer.explain_no_exercises_found(USER_ID, day)
            
            print(f"\n  KRR:")
            for reason in no_exercises_reasoning:
                print(f"    {reason}")
            
            # Store empty day with reasoning
            weekly_plan_data["days"][day] = {
                "day_name": day_names[day],
                "exercises": [],
                "total_exercises": 0,
                "reasoning": no_exercises_reasoning
            }
            
    except Exception as e:
        print(f"  ❌ Error querying exercises: {e}")
        import traceback
        traceback.print_exc()
        # Store empty day if error
        weekly_plan_data["days"][day] = {
            "day_name": day_names[day],
            "exercises": [],
            "total_exercises": 0,
            "error": str(e)
        }

# Save weekly plan to file
save_weekly_plan(USER_ID, week_number, weekly_plan_data)
print(f"\n💾 Week {week_number} plan saved to {WEEKLY_PLANS_FILE}")
print(f"   Temporal ID: {temporal_id}")
print(f"   Generated at: {weekly_plan_data['generated_at']}")

# -----------------------------
# 8️⃣ Display Storage Information
# -----------------------------

all_weeks = get_all_weeks(USER_ID)
if all_weeks:
    print(f"\n📚 Stored Weekly Plans for {USER_ID}:")
    for w in all_weeks:
        plan = load_weekly_plan(USER_ID, w)
        total_exercises = sum([len(day.get("exercises", [])) for day in plan.get("days", {}).values()])
        temporal = plan.get("temporal_id", "N/A")
        print(f"   Week {w}: {total_exercises} exercises across {len(plan.get('days', {}))} days")
        print(f"            Intensity: {plan.get('intensity_level', 'N/A')}/5 | Temporal ID: {temporal}")

print("\n" + "=" * 80)
print("SYSTEM READY FOR NEXT WEEK GENERATION")
print("=" * 80)
print("\n💡 To generate next week's plan, run the system again.")
print("💡 The system will automatically show existing plans and prevent duplicates.")
print("=" * 80)