"""
Data utilities for exercise display and manipulation.

This module provides functions for displaying and working with exercise data.
"""

import pandas as pd
from typing import List, Dict, Optional


def display_exercises_summary(exercises: List[Dict], display_count: Optional[int] = None) -> None:
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
