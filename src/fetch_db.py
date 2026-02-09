"""
Database fetching operations.

This module handles the creation and initialization of ExerciseDB instances
and provides functions for fetching and saving exercise data.
"""

from typing import Optional, List, Dict
from .db import ExerciseDB


def create_db(api_key: Optional[str] = None, base_url: Optional[str] = None,
              data_dir: str = "data", config_dir: str = "config") -> ExerciseDB:
    """
    Create and initialize an ExerciseDB instance.
    
    Args:
        api_key: RapidAPI key (defaults to EXERCISE_API_KEY from .env)
        base_url: Base URL for API (defaults to BASE_URL from .env)
        data_dir: Directory to save output files (defaults to "data")
        config_dir: Directory for config files (defaults to "config")
    
    Returns:
        Initialized ExerciseDB instance
    
    Raises:
        ValueError: If API key or base URL is not configured
    """
    return ExerciseDB(api_key=api_key, base_url=base_url, 
                      data_dir=data_dir, config_dir=config_dir)


def fetch_and_save_exercises(db: ExerciseDB, limit: int = 20,
                             json_filename: str = "exercises_db.json",
                             excel_filename: str = "exercises_db.xlsx") -> Optional[List[Dict]]:
    """
    Fetch exercises from the API and save them to JSON and Excel files.
    
    Args:
        db: ExerciseDB instance
        limit: Number of exercises to fetch
        json_filename: Name of the JSON output file
        excel_filename: Name of the Excel output file
    
    Returns:
        List of exercise dictionaries if successful, None otherwise
    """
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
    exercises = db.fetch_exercises(limit=limit)
    
    if exercises:
        print(f"\n✓ Fetched {len(exercises)} exercises")
        
        # Save to JSON file
        if db.save_to_json(exercises, json_filename):
            print("\n" + "=" * 60)
            print(f"SUCCESS! {len(exercises)} exercises saved to data/{json_filename}")
            print("=" * 60)
        
        # Save to Excel file with serial number
        if db.save_to_excel(exercises, excel_filename):
            print("\n" + "=" * 60)
            print(f"SUCCESS! {len(exercises)} exercises saved to data/{excel_filename}")
            print("Excel file includes serial numbers for each exercise.")
            print("=" * 60)
    else:
        print("\n✗ Failed to fetch exercises. Please check:")
        print("  1. Your API key is correct (EXERCISE_API_KEY in .env)")
        print("  2. BASE_URL is set correctly in .env")
        print("  3. You're subscribed to ExerciseDB on RapidAPI")
        print("  4. Your internet connection is working")
    
    return exercises
