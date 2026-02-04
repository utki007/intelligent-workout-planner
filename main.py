"""
Main entry point for Exercise Database fetcher.

This script fetches exercises from ExerciseDB API and saves them
in both JSON and Excel formats.
"""

import pandas as pd
from src.db import ExerciseDB

def display_exercises_summary(exercises, display_count=None):
    """
    Display exercises in DataFrame view with ID, name, target, and body part.
    
    Args:
        exercises: List of exercise dictionaries
        display_count: Number of exercises to display (None = show all)
    """
    if not exercises:
        return
    
    # Convert to DataFrame
    df = pd.DataFrame(exercises)
    
    # Select only the columns we want to display
    display_columns = ['id', 'name', 'target', 'bodyPart']
    
    # Check which columns exist
    available_columns = [col for col in display_columns if col in df.columns]
    
    if not available_columns:
        print("⚠️  No displayable columns found in exercises")
        return
    
    # Select and rename columns for better display
    display_df = df[available_columns].copy()
    
    # Rename columns for better readability
    column_rename = {
        'id': 'ID',
        'name': 'Name',
        'target': 'Target',
        'bodyPart': 'Body Part'
    }
    display_df = display_df.rename(columns=column_rename)
    
    # Limit display if specified
    if display_count is not None:
        display_df = display_df.head(display_count)
        print(f"\nExercises (showing first {display_count} of {len(df)}):")
    else:
        print(f"\nAll exercises ({len(df)} total):")
    
    print("=" * 80)
    
    # Display DataFrame with proper formatting
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', None)
    pd.set_option('display.max_colwidth', 50)
    
    print(display_df.to_string(index=False))
    
    if display_count is not None and len(df) > display_count:
        print(f"\n... and {len(df) - display_count} more exercises (see Excel file for full list)")


def main():
    """Main execution function."""
    # Configuration
    EXERCISE_LIMIT = 20
    
    try:
        # Initialize ExerciseDB
        db = ExerciseDB()
        
        # Show current offset
        current_offset = db.get_current_offset()
        print(f"📍 Current offset: {current_offset}")
        print(f"   (Will fetch exercises starting from position {current_offset})")
        
        # Show rate limit status
        rate_status = db.get_rate_limit_status()
        print(f"📊 Rate limit status: {rate_status['requests_used']}/{rate_status['max_requests']} requests used this hour")
        print(f"   Remaining: {rate_status['requests_remaining']} requests")
        if rate_status['wait_seconds']:
            wait_min = rate_status['wait_seconds'] / 60
            print(f"   ⚠️  Will need to wait {wait_min:.1f} minutes before next request")
        
        # Fetch exercises
        exercises = db.fetch_exercises(limit=EXERCISE_LIMIT)
        
        if exercises:
            print(f"\n✓ Fetched {len(exercises)} exercises")
            
            # Save to JSON file
            if db.save_to_json(exercises, "exercises_db.json"):
                print("\n" + "=" * 60)
                print(f"SUCCESS! {len(exercises)} exercises saved to data/exercises_db.json")
                print("=" * 60)
            
            # Save to Excel file with serial number
            if db.save_to_excel(exercises, "exercises_db.xlsx"):
                print("\n" + "=" * 60)
                print(f"SUCCESS! {len(exercises)} exercises saved to data/exercises_db.xlsx")
                print("Excel file includes serial numbers for each exercise.")
                print("=" * 60)
            
            # Display summary in DataFrame view
            display_exercises_summary(exercises, display_count=None)  # None = show all
            
        else:
            print("\n✗ Failed to fetch exercises. Please check:")
            print("  1. Your API key is correct (EXERCISE_API_KEY in .env)")
            print("  2. BASE_URL is set correctly in .env")
            print("  3. You're subscribed to ExerciseDB on RapidAPI")
            print("  4. Your internet connection is working")
    
    except ValueError as e:
        print(f"\n✗ Configuration Error: {e}")
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
