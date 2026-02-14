"""
Main entry point for the Intelligent Workout Planner.

This script queries the workout recommender to get safe exercise recommendations
based on user injuries and muscle recovery status.
"""

import json
from src.workout_recommender import WorkoutRecommender


# Theme label -> (bodyPart filter, target filter) for main exercises
_THEMES = [
    ("Chest", ["chest"], ["pectorals", "serratus_anterior"]),
    ("Biceps & Triceps", ["upper arms"], ["biceps", "triceps"]),
    ("Back", ["back"], ["upper_back", "lats", "spine", "traps"]),
    ("Legs", ["upper legs", "lower legs"], ["glutes", "calves", "quads", "hamstrings", "adductors", "abductors"]),
    ("Shoulders", ["shoulders"], ["delts"]),
    ("Cardio / Full-body", ["cardio", "waist"], ["cardiovascular_system", "abs", "obliques", "core"]),
]


def _normalize(s: str) -> str:
    if not s or str(s).strip() == "":
        return ""
    return str(s).strip().lower().replace(" ", "_")


def _row_targets(row) -> set:
    """Primary + secondary muscles for a row (normalized)."""
    out = set()
    t = row.get("target")
    if t is not None and str(t).strip():
        out.add(_normalize(str(t)))
    sec = row.get("secondaryMuscles")
    if sec is not None:
        if isinstance(sec, list):
            for s in sec:
                out.add(_normalize(str(s)))
        else:
            for s in str(sec).split(","):
                out.add(_normalize(s.strip()))
    return out


def _count_main_candidates(df, body_parts, targets, cat_col, include_secondary: bool) -> list:
    """Return sorted list of main exercise names matching body part, primary target, or (if include_secondary) secondary targets."""
    if cat_col:
        strength_df = df[~df[cat_col].isin(["stretching", "mobility"])]
    else:
        strength_df = df
    target_set = set(targets)
    names = []
    seen = set()
    for _, row in strength_df.iterrows():
        name = row.get("name")
        if not name or name in seen:
            continue
        if row.get("bodyPart") in body_parts or (row.get("target") in target_set):
            seen.add(name)
            names.append(name)
            continue
        if include_secondary:
            row_tgts = _row_targets(row)
            if row_tgts & target_set:
                seen.add(name)
                names.append(name)
    return sorted(names)


def _reorder_themes_for_cooldown(recommender: WorkoutRecommender) -> list:
    """Return list of (theme_label, body_parts, targets) with under-supplied theme moved to last (Day 6)."""
    beginner = recommender.get_recommended_beginner_exercises()
    beginner_set = set(beginner)
    df = recommender.get_exercise_dataframe()
    df = df[df["name"].isin(beginner_set)].copy()
    cat_col = "category" if "category" in df.columns else None

    counts = []
    for label, body_parts, targets in _THEMES:
        cands = _count_main_candidates(df, body_parts, targets, cat_col, include_secondary=True)
        counts.append((len(cands), (label, body_parts, targets)))
    # Move theme with fewest main exercises to the end
    counts.sort(key=lambda x: (x[0], x[1][0]))
    # So counts[0] is the theme with fewest (or tied); put it last
    order = [t for _, t in counts]
    # Rotate so the smallest is last: e.g. [B,T, Back, Legs, Shoulders, Cardio] -> [Back, Legs, Shoulders, Cardio, B&T] if B&T is empty
    fewest_theme = order[0]
    rest = order[1:]
    reordered = rest + [fewest_theme]
    return reordered


def build_6day_plan(recommender: WorkoutRecommender) -> dict:
    """
    Build a deterministic 6-day beginner workout plan from the KB and exercise dataframe.
    Uses only recommended (non-forbidden) beginner exercises.
    If a theme (e.g. Biceps/Triceps) is on cooldown and has no/few exercises, that theme
    is moved to Day 6 so other days get full main exercises.
    Main exercises include primary and secondary target muscles so e.g. Day 1 has more chest options.
    """
    beginner = recommender.get_recommended_beginner_exercises()
    beginner_set = set(beginner)
    df = recommender.get_exercise_dataframe()
    df = df[df["name"].isin(beginner_set)].copy()

    cat_col = "category" if "category" in df.columns else None
    # Reorder themes: put theme with fewest main exercises (e.g. on cooldown) last
    ordered_themes = _reorder_themes_for_cooldown(recommender)

    # Warmup/cooldown pool: stretching or mobility, sorted for determinism
    if cat_col:
        warmup_pool = df[df[cat_col].isin(["stretching", "mobility"])]["name"].drop_duplicates().sort_values().tolist()
    else:
        warmup_pool = []
    while len(warmup_pool) < 18:
        warmup_pool = warmup_pool + warmup_pool[: max(1, 18 - len(warmup_pool))]
    warmup_pool = warmup_pool[: 18]

    plan = {}
    used_prev_day = set()

    for i, day_name in enumerate([f"Day {d}" for d in range(1, 7)]):
        _, body_parts, targets = ordered_themes[i]
        # Main: include primary and secondary target match (more exercises for e.g. chest)
        main_candidates = _count_main_candidates(df, body_parts, targets, cat_col, include_secondary=True)
        main_candidates = [x for x in main_candidates if x not in used_prev_day]
        main_exercises = main_candidates[:5]
        if len(main_exercises) < 3 and main_candidates:
            main_exercises = main_candidates[: max(3, len(main_candidates))]
        # If still fewer than 3 (e.g. day focus on cooldown), pad with stretching/mobility for this focus, then any
        if len(main_exercises) < 3 and cat_col:
            target_set = set(targets)
            pad_candidates = df[
                df[cat_col].isin(["stretching", "mobility"])
                & (df["bodyPart"].isin(body_parts) | df["target"].isin(target_set))
            ]["name"].drop_duplicates().sort_values().tolist()
            pad_candidates = [x for x in pad_candidates if x not in used_prev_day and x not in main_exercises]
            need = 3 - len(main_exercises)
            main_exercises = main_exercises + pad_candidates[: need]
            if len(main_exercises) < 3:
                any_stretch = df[df[cat_col].isin(["stretching", "mobility"])]["name"].drop_duplicates().sort_values().tolist()
                any_stretch = [x for x in any_stretch if x not in used_prev_day and x not in main_exercises]
                main_exercises = main_exercises + any_stretch[: 3 - len(main_exercises)]

        warmup_exercises = warmup_pool[i * 2 : i * 2 + 2]
        if len(warmup_exercises) < 2 and warmup_pool:
            warmup_exercises = warmup_pool[:2]
        cooldown_exercises = warmup_pool[12 + i : 12 + i + 1]
        if not cooldown_exercises and warmup_pool:
            cooldown_exercises = warmup_pool[12 : 13]

        plan[day_name] = {
            "focus": ordered_themes[i][0],
            "warmup": warmup_exercises,
            "main": main_exercises,
            "cooldown": cooldown_exercises,
        }
        used_prev_day = set(main_exercises + warmup_exercises + cooldown_exercises)

    return plan


def _format_exercise_name(name: str) -> str:
    """Turn normalized name into readable title (e.g. lever_seated_fly -> Lever seated fly)."""
    if not name:
        return name
    return name.replace("_", " ").replace("  ", " ").strip().title()


def print_plan(plan: dict) -> None:
    """Print the 6-day plan in a clear, easy-to-read format."""
    print()
    print("=" * 60)
    print("  6-DAY BEGINNER WORKOUT PLAN")
    print("=" * 60)
    for day_name in [f"Day {d}" for d in range(1, 7)]:
        day = plan[day_name]
        focus = day.get("focus", "")
        warmup = day.get("warmup", [])
        main = day.get("main", [])
        cooldown = day.get("cooldown", [])
        print()
        print(f"  {day_name} — {focus}")
        print("  " + "-" * (len(day_name) + len(focus) + 5))
        print("  Warmup (1–2 exercises):")
        for ex in warmup:
            print(f"    • {_format_exercise_name(ex)}")
        print("  Main workout (3–5 exercises):")
        for ex in main:
            print(f"    • {_format_exercise_name(ex)}")
        if not main:
            print("    (Rest or light movement — this focus is on cooldown)")
        print("  Cooldown:")
        for ex in cooldown:
            print(f"    • {_format_exercise_name(ex)}")
        print()
    print("=" * 60)
    print()


def main():
    """Main execution function."""
    
    # User injury and cooldown configuration
    # Update these based on user's current status
    INJURED_MUSCLES = ["biceps"]  # List of injured muscles
    MUSCLES_ON_COOLDOWN = ["biceps", "triceps"]  # Muscles in recovery period
    
    try:
        # Initialize workout recommender
        print("Initializing Workout Recommender...")
        recommender = WorkoutRecommender(
            injured_muscles=INJURED_MUSCLES,
            muscles_on_cooldown=MUSCLES_ON_COOLDOWN
        )
        
        # Build and print 6-day beginner workout plan (readable format)
        plan = build_6day_plan(recommender)
        print_plan(plan)
        
    except FileNotFoundError as e:
        print(f"\n✗ File Error: {e}")
        print("\nPlease ensure the exercise database exists.")
        print("You may need to run the data fetching script first.")
    except ValueError as e:
        print(f"\n✗ Configuration Error: {e}")
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
