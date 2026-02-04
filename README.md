# Personal Workout Trainer with Agentic Reasoning

A Python application to fetch exercise data from ExerciseDB API, manage workout planning with constraint-based reasoning, and analyze exercise data through comprehensive visualizations.

## Project Structure

```
Workout builder/
├── src/                           # Source code
│   ├── __init__.py               # Package initialization
│   └── db.py                     # ExerciseDB class for API interactions
├── data/                          # Output directory for JSON and Excel files
│   ├── exercises_db.json         # Raw exercise data in JSON format
│   └── exercises_db.xlsx         # Exercise data in Excel with serial numbers
├── config/                        # Configuration files
│   ├── offset_config.json        # Persistent offset tracking
│   └── rate_limit.json           # Rate limit request history
├── .env                           # Environment variables (create from .env.example)
├── .env.example                  # Example environment file
├── .gitignore                    # Git ignore rules
├── requirements.txt              # Python dependencies
├── main.py                       # Main entry point
├── exercise_analysis.ipynb      # Jupyter notebook for data analysis
└── README.md                     # This file
```

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment

1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and add your API credentials:
   ```
   EXERCISE_API_KEY=your-actual-api-key-here
   BASE_URL=https://exercisedb.p.rapidapi.com/exercises
   ```

3. Get your API key from [ExerciseDB on RapidAPI](https://rapidapi.com/justin-WFnsXH_t6/api/exercisedb)

## Usage

### Fetch Exercises

Run the main script from the project root:

```bash
python main.py
```

This will:
- Fetch exercises from ExerciseDB API (default: 2000 exercises, configurable in `main.py`)
- Automatically handle pagination (API limit: 10 exercises per request)
- Track persistent offset to avoid refetching duplicate data
- Enforce rate limiting (1000 requests/hour hard limit)
- Save to `data/exercises_db.json` (raw JSON format)
- Save to `data/exercises_db.xlsx` (Excel with serial numbers)
- Display summary in DataFrame format with ID, name, target, and body part

### Use as a Module

```python
from src.db import ExerciseDB

# Initialize
db = ExerciseDB()

# Check current offset
current_offset = db.get_current_offset()
print(f"Current offset: {current_offset}")

# Check rate limit status
rate_status = db.get_rate_limit_status()
print(f"Requests used: {rate_status['requests_used']}/1000")

# Fetch exercises
exercises = db.fetch_exercises(limit=50)

# Save to files (automatically appends and removes duplicates)
db.save_to_json(exercises, "my_exercises.json")
db.save_to_excel(exercises, "my_exercises.xlsx")

# Reset offset if needed
db.reset_offset(0)  # Start from beginning
```

### Analyze Exercise Data

Open the Jupyter notebook for comprehensive data analysis:

```bash
jupyter notebook exercise_analysis.ipynb
```

The notebook includes:
- Dataset overview and statistics
- Exercises by primary target muscle
- Exercises by body part
- **Exercises per muscle group (including secondary muscles)** - Key analysis
- Comparison: Primary target vs all muscles (primary + secondary)
- Exercises by equipment type
- Exercises by difficulty level
- Exercises by category
- Muscle group coverage analysis
- Summary statistics with visualizations

## Features

### ✅ Core Functionality
- **API Integration**: Fetches exercises from ExerciseDB API via RapidAPI
- **Pagination**: Automatically handles pagination (10 exercises per API request)
- **Persistent Offset Tracking**: Remembers last fetched position to avoid duplicates across runs
- **Rate Limiting**: Enforces 1000 requests/hour limit with automatic cooldown
- **Duplicate Prevention**: Removes duplicates based on exercise `id` primary key
- **Data Appending**: Appends new exercises to existing files without overwriting

### ✅ Data Storage
- **JSON Format**: Saves raw exercise data in JSON format
- **Excel Format**: Saves data in Excel with serial numbers for easy reference
- **Column Flattening**: Automatically flattens nested arrays (e.g., `secondaryMuscles`) for Excel readability
- **Serial Numbers**: Automatically generates and maintains serial numbers in Excel

### ✅ Data Analysis
- **Jupyter Notebook**: Comprehensive analysis and visualization notebook
- **Muscle Group Analysis**: Counts exercises per muscle group including secondary muscles
- **Multiple Visualizations**: Bar charts, pie charts, and comparison charts
- **Statistical Summary**: Detailed statistics and insights

### ✅ Error Handling
- **API Error Handling**: Handles 401 (unauthorized), 429 (rate limit), and other HTTP errors
- **Connection Timeouts**: 60-second timeout with retry logic
- **Data Validation**: Validates exercise data before saving
- **Configuration Validation**: Checks for required environment variables

### ✅ User Experience
- **Progress Indicators**: Shows current offset and rate limit status
- **DataFrame Display**: Beautiful DataFrame view of fetched exercises
- **Clear Error Messages**: Helpful error messages with troubleshooting tips
- **Configuration Management**: Easy-to-use config files for offset and rate limiting

## Output Files

All output files are saved in the `data/` directory:
- `exercises_db.json` - Raw exercise data in JSON format
- `exercises_db.xlsx` - Exercise data in Excel with serial numbers

Configuration files in `config/` directory:
- `offset_config.json` - Tracks last fetched offset and timestamp
- `rate_limit.json` - Tracks API request timestamps for rate limiting

## Configuration

### Main Script Configuration

Edit `main.py` to change:
- `EXERCISE_LIMIT` - Number of exercises to fetch (default: 2000)

### API Configuration

Edit `.env` file:
- `EXERCISE_API_KEY` - Your RapidAPI key
- `BASE_URL` - ExerciseDB API base URL

### Offset Management

The system automatically tracks the last fetched offset. To reset:
```python
from src.db import ExerciseDB
db = ExerciseDB()
db.reset_offset(0)  # Start from beginning
```

## ExerciseDB Class Methods

### Core Methods
- `fetch_exercises(limit: int)` - Fetches exercises with pagination and rate limiting
- `save_to_json(exercises, filename)` - Saves exercises to JSON file (appends, removes duplicates)
- `save_to_excel(exercises, filename)` - Saves exercises to Excel file (appends, removes duplicates, adds serial numbers)

### Configuration Methods
- `get_current_offset()` - Returns current offset position
- `reset_offset(offset: int)` - Resets offset to specified value
- `get_rate_limit_status()` - Returns rate limit status and remaining requests

### Helper Methods
- `_remove_duplicates_by_id(exercises)` - Removes duplicate exercises by ID
- `_check_rate_limit()` - Calculates wait time based on rate limit
- `_record_request()` - Records API request timestamp

## Data Analysis Features

The `exercise_analysis.ipynb` notebook provides:

1. **Dataset Overview**: Total exercises, unique body parts, muscles, equipment, difficulty levels
2. **Primary Target Analysis**: Exercises grouped by primary target muscle
3. **Body Part Analysis**: Distribution across body parts with visualizations
4. **Muscle Group Analysis (Key Feature)**: Counts exercises per muscle group including:
   - Primary target muscles
   - Secondary muscles
   - Combined counts showing total exercise coverage per muscle
5. **Comparison Analysis**: Primary target vs all muscles (primary + secondary)
6. **Equipment Analysis**: Distribution by equipment type
7. **Difficulty Analysis**: Distribution by difficulty level (beginner, intermediate, advanced)
8. **Category Analysis**: Distribution by exercise category
9. **Coverage Analysis**: Secondary muscle count per exercise
10. **Summary Statistics**: Comprehensive overview with top rankings

## Requirements

- Python 3.7+
- requests
- pandas
- openpyxl
- python-dotenv
- matplotlib (for notebook)
- seaborn (for notebook)
- jupyter (for notebook)

## API Rate Limits

- **Hard Limit**: 1000 requests per hour
- **Automatic Cooldown**: System automatically calculates and enforces wait times
- **Request Tracking**: Tracks request history to prevent exceeding limits
- **Status Monitoring**: Real-time rate limit status display

## Troubleshooting

### API Key Issues
- Ensure `EXERCISE_API_KEY` is set in `.env` file
- Verify your RapidAPI subscription is active
- Check that the API key has access to ExerciseDB

### Rate Limit Issues
- The system automatically handles rate limiting
- If you hit the limit, wait for the cooldown period (displayed in console)
- Request history is automatically cleaned (removes requests older than 1 hour)

### Duplicate Data
- The system automatically prevents duplicates based on exercise `id`
- If you see duplicates, check that the `id` field is present in all exercises
- Use `reset_offset(0)` to start fresh if needed

### File Not Found Errors
- Ensure `data/` and `config/` directories exist (created automatically)
- Check file permissions for write access

## Future Enhancements

Based on the project proposal, future features may include:
- User constraint-based workout planning
- Weekly workout plan generation
- Post-workout feedback system
- Agentic decision-making for workout recommendations
- Integration with pyDatalog for constraint reasoning

## License

This project is part of a coursework assignment.
