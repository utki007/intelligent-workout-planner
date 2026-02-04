"""
Exercise Database Module

This module provides functionality to fetch exercises from ExerciseDB API
and save them in various formats (JSON, Excel).
"""

import requests
import json
import os
import pandas as pd
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
from typing import List, Dict, Optional

# Load environment variables
load_dotenv()


class ExerciseDB:
    """Class to handle exercise data fetching and storage."""
    
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, 
                 data_dir: str = "data", config_dir: str = "config"):
        """
        Initialize ExerciseDB with API credentials.
        
        Args:
            api_key: RapidAPI key (defaults to EXERCISE_API_KEY from .env)
            base_url: Base URL for API (defaults to BASE_URL from .env)
            data_dir: Directory to save output files (defaults to "data")
            config_dir: Directory for config files (defaults to "config")
        """
        self.api_key = api_key or os.getenv("EXERCISE_API_KEY")
        self.base_url = base_url or os.getenv("BASE_URL")
        self.data_dir = data_dir
        self.config_dir = config_dir
        self.config_file = os.path.join(config_dir, "offset_config.json")
        self.rate_limit_file = os.path.join(config_dir, "rate_limit.json")
        
        # Rate limiting configuration
        self.max_requests_per_hour = 1000
        self.request_window_hours = 1
        
        # Create directories if they don't exist
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.config_dir, exist_ok=True)
        
        # Validate configuration
        if not self.api_key:
            raise ValueError(
                "API key not found! Please set EXERCISE_API_KEY in your .env file.\n"
                "Create a .env file with: EXERCISE_API_KEY=your-api-key-here"
            )
        if not self.base_url:
            raise ValueError(
                "BASE URL not found! Please set BASE_URL in your .env file.\n"
                "Create a .env file with: BASE_URL=your-base-url-here"
            )
        
        self.headers = {
            "X-RapidAPI-Key": self.api_key,
            "X-RapidAPI-Host": "exercisedb.p.rapidapi.com"
        }
        
        # Load current offset from config file
        self.current_offset = self._load_offset()
        
        # Load rate limit tracking
        self.request_history = self._load_request_history()
    
    def _get_file_path(self, filename: str) -> str:
        """Get full file path in data directory."""
        return os.path.join(self.data_dir, filename)
    
    def _remove_duplicates_by_id(self, exercises: List[Dict]) -> List[Dict]:
        """
        Remove duplicates from exercises list based on 'id' primary key.
        Keeps the first occurrence of each unique ID.
        
        Args:
            exercises: List of exercise dictionaries
        
        Returns:
            List of exercises with duplicates removed
        """
        if not exercises:
            return []
        
        seen_ids = set()
        unique_exercises = []
        duplicates_count = 0
        
        for exercise in exercises:
            # Get ID and validate it
            ex_id = exercise.get('id')
            
            # Skip exercises without valid ID
            if ex_id is None or ex_id == '' or (isinstance(ex_id, float) and pd.isna(ex_id)):
                print(f"⚠️  Skipping exercise without valid ID: {exercise.get('name', 'Unknown')}")
                continue
            
            # Convert ID to string for consistent comparison
            ex_id_str = str(ex_id).strip()
            
            # Check if we've seen this ID before
            if ex_id_str in seen_ids:
                duplicates_count += 1
                continue
            
            # Add to seen set and unique list
            seen_ids.add(ex_id_str)
            unique_exercises.append(exercise)
        
        if duplicates_count > 0:
            print(f"⚠️  Removed {duplicates_count} duplicate exercise(s) from new data (same ID)")
        
        return unique_exercises
    
    def _load_offset(self) -> int:
        """
        Load the current offset from config file.
        Returns 0 if config file doesn't exist or is invalid.
        """
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                    offset = config.get('offset', 0)
                    print(f"📖 Loaded offset from config: {offset}")
                    return offset
        except (json.JSONDecodeError, IOError) as e:
            print(f"⚠️  Could not load offset config: {e}. Starting from offset 0.")
        
        return 0
    
    def _save_offset(self, offset: int) -> bool:
        """
        Save the current offset to config file.
        
        Args:
            offset: The offset value to save
        
        Returns:
            True if successful, False otherwise
        """
        try:
            config = {
                'offset': offset,
                'last_updated': pd.Timestamp.now().isoformat()
            }
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
            return True
        except Exception as e:
            print(f"⚠️  Could not save offset config: {e}")
            return False
    
    def reset_offset(self, offset: int = 0) -> bool:
        """
        Reset the offset to a specific value (default: 0).
        
        Args:
            offset: The offset value to reset to (default: 0)
        
        Returns:
            True if successful, False otherwise
        """
        self.current_offset = offset
        success = self._save_offset(offset)
        if success:
            print(f"✓ Offset reset to {offset}")
        return success
    
    def get_current_offset(self) -> int:
        """
        Get the current offset value.
        
        Returns:
            Current offset value
        """
        return self.current_offset
    
    def _load_request_history(self) -> List[str]:
        """
        Load request history from config file.
        Returns list of ISO timestamp strings.
        """
        try:
            if os.path.exists(self.rate_limit_file):
                with open(self.rate_limit_file, 'r') as f:
                    data = json.load(f)
                    return data.get('request_timestamps', [])
        except (json.JSONDecodeError, IOError):
            pass
        return []
    
    def _save_request_history(self) -> bool:
        """Save request history to config file."""
        try:
            data = {
                'request_timestamps': self.request_history,
                'last_updated': datetime.now().isoformat()
            }
            with open(self.rate_limit_file, 'w') as f:
                json.dump(data, f, indent=2)
            return True
        except Exception:
            return False
    
    def _clean_old_requests(self):
        """Remove request timestamps older than the rate limit window."""
        if not self.request_history:
            return
        
        cutoff_time = datetime.now() - timedelta(hours=self.request_window_hours)
        cutoff_iso = cutoff_time.isoformat()
        
        initial_count = len(self.request_history)
        self.request_history = [
            ts for ts in self.request_history 
            if ts >= cutoff_iso
        ]
        
        removed = initial_count - len(self.request_history)
        if removed > 0:
            print(f"🧹 Cleaned {removed} old request timestamp(s)")
    
    def _check_rate_limit(self) -> Optional[float]:
        """
        Check if we can make a request based on rate limits.
        
        Returns:
            None if request can be made immediately
            Number of seconds to wait if rate limit would be exceeded
        """
        # Clean old requests first
        self._clean_old_requests()
        
        # Count requests in the last hour
        current_count = len(self.request_history)
        
        # Check if we're at or near the limit
        if current_count >= self.max_requests_per_hour:
            # Find the oldest request in the current window
            if self.request_history:
                oldest_request = min(self.request_history)
                oldest_dt = datetime.fromisoformat(oldest_request)
                wait_until = oldest_dt + timedelta(hours=self.request_window_hours)
                wait_seconds = (wait_until - datetime.now()).total_seconds()
                
                if wait_seconds > 0:
                    return wait_seconds
        
        # Check if we're close to the limit (within 10 requests)
        if current_count >= self.max_requests_per_hour - 10:
            print(f"⚠️  Rate limit warning: {current_count}/{self.max_requests_per_hour} requests used this hour")
        
        return None
    
    def _wait_for_rate_limit(self, wait_seconds: float):
        """
        Wait for the specified number of seconds, showing progress.
        
        Args:
            wait_seconds: Number of seconds to wait
        """
        if wait_seconds <= 0:
            return
        
        wait_minutes = wait_seconds / 60
        print(f"⏳ Rate limit reached. Waiting {wait_minutes:.1f} minutes ({wait_seconds:.0f} seconds)...")
        print(f"   ({self.max_requests_per_hour} requests/hour limit)")
        
        # Wait in chunks to show progress
        chunk_size = 10  # seconds
        remaining = wait_seconds
        
        while remaining > 0:
            sleep_time = min(chunk_size, remaining)
            time.sleep(sleep_time)
            remaining -= sleep_time
            if remaining > 0:
                print(f"   ⏱️  {remaining:.0f} seconds remaining...")
        
        print("✓ Rate limit cooldown complete. Resuming requests...")
    
    def _record_request(self):
        """Record a request timestamp."""
        current_time = datetime.now().isoformat()
        self.request_history.append(current_time)
        self._save_request_history()
    
    def get_rate_limit_status(self) -> Dict:
        """
        Get current rate limit status.
        
        Returns:
            Dictionary with rate limit information
        """
        self._clean_old_requests()
        current_count = len(self.request_history)
        remaining = self.max_requests_per_hour - current_count
        
        # Calculate time until oldest request expires
        wait_time = None
        if self.request_history:
            oldest_request = min(self.request_history)
            oldest_dt = datetime.fromisoformat(oldest_request)
            wait_until = oldest_dt + timedelta(hours=self.request_window_hours)
            wait_seconds = (wait_until - datetime.now()).total_seconds()
            if wait_seconds > 0:
                wait_time = wait_seconds
        
        return {
            'requests_used': current_count,
            'requests_remaining': max(0, remaining),
            'max_requests': self.max_requests_per_hour,
            'window_hours': self.request_window_hours,
            'wait_seconds': wait_time,
            'can_make_request': remaining > 0
        }
    
    def fetch_exercises(self, limit: int) -> Optional[List[Dict]]:
        """
        Safely fetch a specified number of exercises from ExerciseDB API.
        Uses pagination to fetch more than 10 exercises (API default limit).
        
        Args:
            limit: Number of exercises to fetch
        
        Returns:
            List of exercise dictionaries, or None if error occurred
        """
        all_exercises = []
        batch_size = 10  # API seems to limit to 10 per request
        offset = self.current_offset  # Start from saved offset
        
        print(f"Fetching {limit} exercises from ExerciseDB API...")
        print(f"Starting from offset: {offset}")
        print(f"(Using pagination: {batch_size} exercises per request)")
        
        while len(all_exercises) < limit:
            # Calculate how many we still need
            remaining = limit - len(all_exercises)
            current_batch_size = min(batch_size, remaining)
            
            params = {
                "limit": current_batch_size,
                "offset": offset,
                "sortMethod": "id",
                "sortOrder": "ascending"
            }
            
            try:
                # Check rate limit before making request
                wait_time = self._check_rate_limit()
                if wait_time is not None:
                    self._wait_for_rate_limit(wait_time)
                    # Re-check after waiting
                    wait_time = self._check_rate_limit()
                    if wait_time is not None:
                        print(f"✗ Still rate limited. Please try again later.")
                        return None
                
                print(f"  Fetching batch: offset={offset}, limit={current_batch_size}...")
                print(f"  📊 Rate limit: {len(self.request_history)}/{self.max_requests_per_hour} requests this hour")
                
                # Record request before making it
                self._record_request()
                
                response = requests.get(
                    self.base_url, 
                    headers=self.headers, 
                    params=params, 
                    timeout=60
                )
                
                # Check if the request was successful
                if response.status_code == 200:
                    exercises = response.json()
                    
                    # Validate response is a list
                    if not isinstance(exercises, list):
                        print(f"✗ Unexpected response format: {type(exercises)}")
                        return None
                    
                    # If we got fewer exercises than requested, we've reached the end
                    if len(exercises) == 0:
                        print(f"  No more exercises available. Got {len(all_exercises)} total.")
                        break
                    
                    all_exercises.extend(exercises)
                    print(f"  ✓ Got {len(exercises)} exercises (total: {len(all_exercises)})")
                    
                    # If we got fewer than requested, we've reached the end
                    if len(exercises) < current_batch_size:
                        print(f"  Reached end of available exercises.")
                        break
                    
                    offset += len(exercises)
                    # Update the current offset
                    self.current_offset = offset
                    
                elif response.status_code == 401:
                    print("✗ Authentication failed. Check your API key.")
                    print("  Make sure EXERCISE_API_KEY is set correctly in your .env file")
                    return None
                elif response.status_code == 429:
                    print("✗ Rate limit exceeded. Please wait a moment and try again.")
                    return None
                else:
                    print(f"✗ Error: {response.status_code}")
                    print(f"Response: {response.text[:200]}")
                    return None
                    
            except requests.exceptions.Timeout:
                print("✗ Request timed out. Check your internet connection.")
                return None
            except requests.exceptions.ConnectionError:
                print("✗ Connection error. Check your internet connection.")
                return None
            except requests.exceptions.RequestException as e:
                print(f"✗ Network error: {e}")
                return None
            except json.JSONDecodeError as e:
                print(f"✗ Invalid JSON response: {e}")
                return None
            except Exception as e:
                print(f"✗ An unexpected error occurred: {e}")
                return None
        
        print(f"\n✓ Successfully fetched {len(all_exercises)} exercises")
        
        # Save the updated offset for next run
        if all_exercises:
            self._save_offset(self.current_offset)
            print(f"💾 Saved offset {self.current_offset} for next run")
        
        return all_exercises
    
    def save_to_json(self, exercises: List[Dict], filename: str = "exercises_db.json") -> bool:
        """
        Save exercises to a JSON file in raw form.
        Appends to existing file if it exists, otherwise creates new file.
        
        Args:
            exercises: List of exercise dictionaries
            filename: Output JSON filename (will be saved in data directory)
        
        Returns:
            True if successful, False otherwise
        """
        if exercises is None or not exercises:
            print("✗ No exercises to save.")
            return False
        
        try:
            filepath = self._get_file_path(filename)
            existing_exercises = []
            
            # Read existing data if file exists
            if os.path.exists(filepath):
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        existing_exercises = json.load(f)
                    if not isinstance(existing_exercises, list):
                        existing_exercises = []
                    print(f"📖 Read {len(existing_exercises)} existing exercises from {filename}")
                except (json.JSONDecodeError, IOError) as e:
                    print(f"⚠️  Could not read existing file: {e}. Creating new file.")
                    existing_exercises = []
            
            # Remove duplicates from existing data (in case file has duplicates)
            existing_exercises = self._remove_duplicates_by_id(existing_exercises)
            
            # Remove duplicates within new exercises list
            new_exercises = self._remove_duplicates_by_id(exercises)
            
            # Get existing IDs (convert to strings for consistent comparison)
            existing_ids = {
                str(ex.get('id')).strip() 
                for ex in existing_exercises 
                if ex.get('id') is not None and ex.get('id') != ''
            }
            
            # Filter out exercises that already exist
            filtered_exercises = []
            for ex in new_exercises:
                ex_id = ex.get('id')
                if ex_id is None or ex_id == '':
                    continue
                ex_id_str = str(ex_id).strip()
                if ex_id_str not in existing_ids:
                    filtered_exercises.append(ex)
                    existing_ids.add(ex_id_str)  # Add to set to prevent duplicates in same batch
            
            skipped_count = len(exercises) - len(filtered_exercises)
            if skipped_count > 0:
                print(f"⚠️  Skipped {skipped_count} exercise(s) that already exist in file")
            
            # Combine existing and new exercises
            all_exercises = existing_exercises + filtered_exercises
            
            # Final check: ensure no duplicates in final list
            all_exercises = self._remove_duplicates_by_id(all_exercises)
            
            # Save combined data
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(all_exercises, f, indent=2, ensure_ascii=False)
            
            file_size_kb = os.path.getsize(filepath) / 1024
            print(f"✓ Saved {len(filtered_exercises)} new exercises to {filepath}")
            print(f"  Total unique exercises in file: {len(all_exercises)}")
            print(f"  File size: {file_size_kb:.2f} KB")
            return True
        except Exception as e:
            print(f"✗ Error saving file: {e}")
            return False
    
    def save_to_excel(self, exercises: List[Dict], filename: str = "exercises_db.xlsx") -> bool:
        """
        Convert exercises list to pandas DataFrame and save to Excel with serial number.
        Appends to existing file if it exists, otherwise creates new file.
        
        Args:
            exercises: List of exercise dictionaries
            filename: Output Excel filename (will be saved in data directory)
        
        Returns:
            True if successful, False otherwise
        """
        if exercises is None or not exercises:
            print("✗ No exercises to save.")
            return False
        
        try:
            filepath = self._get_file_path(filename)
            existing_df = None
            
            # Read existing data if file exists
            if os.path.exists(filepath):
                try:
                    existing_df = pd.read_excel(filepath, engine='openpyxl')
                    print(f"📖 Read {len(existing_df)} existing exercises from {filename}")
                    
                    # Remove 'Serial Number' column if it exists (we'll recalculate it)
                    if 'Serial Number' in existing_df.columns:
                        existing_df = existing_df.drop(columns=['Serial Number'])
                except Exception as e:
                    print(f"⚠️  Could not read existing Excel file: {e}. Creating new file.")
                    existing_df = None
            
            # Remove duplicates from new exercises list first
            unique_exercises = self._remove_duplicates_by_id(exercises)
            
            # Convert new exercises to DataFrame
            print(f"Converting {len(unique_exercises)} new exercises to DataFrame...")
            new_df = pd.DataFrame(unique_exercises)
            
            if len(new_df) == 0:
                print("ℹ️  No valid exercises to add after duplicate removal")
                if existing_df is not None:
                    # Just update serial numbers and save existing data
                    combined_df = existing_df.copy()
                    combined_df.insert(0, 'Serial Number', range(1, len(combined_df) + 1))
                    combined_df.to_excel(filepath, index=False, engine='openpyxl')
                    print(f"✓ Updated existing file with {len(combined_df)} exercises")
                return True
            
            # Flatten nested columns (like secondaryMuscles and instructions which are lists)
            # Convert lists to strings for better Excel display
            for col in new_df.columns:
                if new_df[col].dtype == 'object':
                    # Check if column contains lists
                    if new_df[col].apply(lambda x: isinstance(x, list)).any():
                        new_df[col] = new_df[col].apply(
                            lambda x: ', '.join(map(str, x)) if isinstance(x, list) else x
                        )
            
            # Remove duplicates from existing data if present
            if existing_df is not None:
                if 'id' in existing_df.columns:
                    # Remove duplicates from existing data
                    existing_df = existing_df.drop_duplicates(subset=['id'], keep='first')
                    print(f"  Cleaned existing data: {len(existing_df)} unique exercises")
                
                # Remove new exercises that already exist
                if 'id' in new_df.columns and 'id' in existing_df.columns:
                    # Convert IDs to strings for consistent comparison
                    existing_df['id'] = existing_df['id'].astype(str).str.strip()
                    new_df['id'] = new_df['id'].astype(str).str.strip()
                    
                    # Filter out duplicates
                    existing_ids = set(existing_df['id'].dropna())
                    initial_count = len(new_df)
                    new_df = new_df[~new_df['id'].isin(existing_ids)]
                    
                    skipped_count = initial_count - len(new_df)
                    if skipped_count > 0:
                        print(f"⚠️  Skipped {skipped_count} exercise(s) that already exist in file")
                    
                    # Also check for duplicates within new_df itself
                    duplicates_in_new = new_df.duplicated(subset=['id'], keep='first')
                    if duplicates_in_new.any():
                        dup_count = duplicates_in_new.sum()
                        new_df = new_df.drop_duplicates(subset=['id'], keep='first')
                        print(f"⚠️  Removed {dup_count} duplicate(s) within new data")
            
            # Combine existing and new data
            if existing_df is not None and len(new_df) > 0:
                # Ensure columns match (add missing columns with NaN)
                all_columns = set(existing_df.columns) | set(new_df.columns)
                for col in all_columns:
                    if col not in existing_df.columns:
                        existing_df[col] = None
                    if col not in new_df.columns:
                        new_df[col] = None
                
                # Reorder columns to match
                column_order = list(existing_df.columns)
                new_df = new_df[column_order]
                
                # Concatenate
                combined_df = pd.concat([existing_df, new_df], ignore_index=True)
                
                # Final duplicate check on combined data
                if 'id' in combined_df.columns:
                    initial_combined_count = len(combined_df)
                    combined_df = combined_df.drop_duplicates(subset=['id'], keep='first')
                    if len(combined_df) < initial_combined_count:
                        print(f"⚠️  Removed {initial_combined_count - len(combined_df)} duplicate(s) from combined data")
            elif existing_df is not None:
                combined_df = existing_df
                print("ℹ️  No new exercises to add (all duplicates)")
            else:
                combined_df = new_df
                # Final duplicate check on new data only
                if 'id' in combined_df.columns:
                    initial_count = len(combined_df)
                    combined_df = combined_df.drop_duplicates(subset=['id'], keep='first')
                    if len(combined_df) < initial_count:
                        print(f"⚠️  Removed {initial_count - len(combined_df)} duplicate(s) from new data")
            
            # Add serial number as the first column (continuous numbering)
            combined_df.insert(0, 'Serial Number', range(1, len(combined_df) + 1))
            
            # Save to Excel
            print(f"Saving to Excel file: {filepath}...")
            combined_df.to_excel(filepath, index=False, engine='openpyxl')
            
            # Get file size
            file_size_kb = os.path.getsize(filepath) / 1024
            file_size_mb = file_size_kb / 1024
            
            new_count = len(new_df) if 'new_df' in locals() and len(new_df) > 0 else 0
            print(f"✓ Saved {new_count} new exercises to {filepath}")
            print(f"  Total exercises in file: {len(combined_df)}")
            print(f"  File size: {file_size_kb:.2f} KB ({file_size_mb:.2f} MB)")
            print(f"  Columns: {len(combined_df.columns)}")
            print(f"  Rows: {len(combined_df)}")
            
            return True
            
        except ImportError:
            print("✗ Error: openpyxl is required for Excel export.")
            print("  Install it with: pip install openpyxl")
            return False
        except PermissionError:
            print(f"✗ Permission denied. Cannot write to {filename}")
            print("  Make sure the file is not open in Excel or another program.")
            return False
        except Exception as e:
            print(f"✗ Error saving to Excel: {e}")
            import traceback
            traceback.print_exc()
            return False
