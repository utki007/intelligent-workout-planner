from src.fetch_db import create_db, fetch_and_save_exercises
from src.data import display_exercises_summary


def main():
    """Main execution function."""
    # Configuration
    EXERCISE_LIMIT = 20
    
    try:
        # Create and initialize ExerciseDB
        db = create_db()
        
        # Fetch exercises and save to files
        exercises = fetch_and_save_exercises(
            db=db,
            limit=EXERCISE_LIMIT,
            json_filename="exercises_db.json",
            excel_filename="exercises_db.xlsx"
        )
        
        # Display summary if exercises were fetched
        if exercises:
            display_exercises_summary(exercises, display_count=None)  # None = show all
    
    except ValueError as e:
        print(f"\n✗ Configuration Error: {e}")
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
