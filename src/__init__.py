"""
Exercise Database Package

This package provides functionality to fetch exercises from ExerciseDB API
and save them in various formats.
"""

from .db import ExerciseDB
from .data import display_exercises_summary
from .fetch_db import create_db, fetch_and_save_exercises

__all__ = ['ExerciseDB', 'display_exercises_summary', 'create_db', 'fetch_and_save_exercises']
__version__ = '1.0.0'
