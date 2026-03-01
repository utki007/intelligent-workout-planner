"""SQLite repository for exercises and per-session user profile state."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class UserProfile:
    """Persistent user state used by the chat planner."""

    session_id: str
    goal: str = "general fitness"
    experience_level: str = "beginner"
    days_per_week: int = 3
    session_minutes: int = 45
    injuries: list[str] = field(default_factory=list)
    excluded_equipment: list[str] = field(default_factory=list)
    focus_muscles: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "goal": self.goal,
            "experience_level": self.experience_level,
            "days_per_week": self.days_per_week,
            "session_minutes": self.session_minutes,
            "injuries": self.injuries,
            "excluded_equipment": self.excluded_equipment,
            "focus_muscles": self.focus_muscles,
        }


class WorkoutRepository:
    """Repository layer backed by SQLite."""

    def __init__(self, db_path: str = "data/workout_planner.db", seed_json_path: str = "data/exercises_db.json"):
        self.db_path = Path(db_path)
        self.seed_json_path = Path(seed_json_path)

    def initialize(self) -> None:
        """Create schema and seed exercises if the table is empty."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            self._create_tables(conn)
            if self._exercise_count(conn) == 0:
                self._seed_exercises(conn)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _create_tables(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS exercises (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                body_part TEXT,
                target TEXT,
                secondary_muscles TEXT,
                equipment TEXT,
                difficulty TEXT,
                category TEXT,
                description TEXT,
                instructions TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_profiles (
                session_id TEXT PRIMARY KEY,
                goal TEXT NOT NULL DEFAULT 'general fitness',
                experience_level TEXT NOT NULL DEFAULT 'beginner',
                days_per_week INTEGER NOT NULL DEFAULT 3,
                session_minutes INTEGER NOT NULL DEFAULT 45,
                injuries TEXT NOT NULL DEFAULT '[]',
                excluded_equipment TEXT NOT NULL DEFAULT '[]',
                focus_muscles TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()

    def _exercise_count(self, conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT COUNT(1) AS c FROM exercises").fetchone()
        return int(row["c"] if row else 0)

    def _seed_exercises(self, conn: sqlite3.Connection) -> None:
        if not self.seed_json_path.exists():
            raise FileNotFoundError(
                f"Exercise seed file not found at {self.seed_json_path}. "
                "Run the data-fetch flow first to generate exercises_db.json."
            )

        raw_data = json.loads(self.seed_json_path.read_text(encoding="utf-8"))
        if not isinstance(raw_data, list):
            raise ValueError(f"Seed file {self.seed_json_path} must contain a list of exercises")

        rows: list[tuple[Any, ...]] = []
        for exercise in raw_data:
            exercise_id = str(exercise.get("id", "")).strip()
            if not exercise_id:
                continue

            rows.append(
                (
                    exercise_id,
                    str(exercise.get("name", "")).strip(),
                    str(exercise.get("bodyPart", "")).strip(),
                    str(exercise.get("target", "")).strip(),
                    json.dumps(exercise.get("secondaryMuscles", []), ensure_ascii=True),
                    str(exercise.get("equipment", "")).strip(),
                    str(exercise.get("difficulty", "")).strip(),
                    str(exercise.get("category", "")).strip(),
                    str(exercise.get("description", "")).strip(),
                    json.dumps(exercise.get("instructions", []), ensure_ascii=True),
                )
            )

        conn.executemany(
            """
            INSERT OR REPLACE INTO exercises (
                id, name, body_part, target, secondary_muscles, equipment,
                difficulty, category, description, instructions
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()

    def get_profile(self, session_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM user_profiles WHERE session_id = ?",
                (session_id,),
            ).fetchone()

            if row is None:
                profile = UserProfile(session_id=session_id)
                self._save_profile(conn, profile)
                return profile.to_dict()

            return self._row_to_profile(row).to_dict()

    def update_profile(self, session_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM user_profiles WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            profile = self._row_to_profile(row) if row else UserProfile(session_id=session_id)

            if "goal" in updates and isinstance(updates["goal"], str):
                profile.goal = updates["goal"].strip() or profile.goal
            if "experience_level" in updates and isinstance(updates["experience_level"], str):
                value = updates["experience_level"].strip().lower()
                if value in {"beginner", "intermediate", "advanced"}:
                    profile.experience_level = value
            if "days_per_week" in updates:
                profile.days_per_week = _clamp_int(updates["days_per_week"], 1, 7, profile.days_per_week)
            if "session_minutes" in updates:
                profile.session_minutes = _clamp_int(updates["session_minutes"], 15, 180, profile.session_minutes)
            if "injuries" in updates:
                profile.injuries = _clean_string_list(updates["injuries"])
            if "excluded_equipment" in updates:
                profile.excluded_equipment = _clean_string_list(updates["excluded_equipment"])
            if "focus_muscles" in updates:
                profile.focus_muscles = _clean_string_list(updates["focus_muscles"])

            self._save_profile(conn, profile)
            return profile.to_dict()

    def _save_profile(self, conn: sqlite3.Connection, profile: UserProfile) -> None:
        conn.execute(
            """
            INSERT OR REPLACE INTO user_profiles (
                session_id, goal, experience_level, days_per_week,
                session_minutes, injuries, excluded_equipment,
                focus_muscles, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile.session_id,
                profile.goal,
                profile.experience_level,
                profile.days_per_week,
                profile.session_minutes,
                json.dumps(profile.injuries, ensure_ascii=True),
                json.dumps(profile.excluded_equipment, ensure_ascii=True),
                json.dumps(profile.focus_muscles, ensure_ascii=True),
                datetime.now(tz=timezone.utc).isoformat(),
            ),
        )
        conn.commit()

    def search_exercises(
        self,
        *,
        body_parts: list[str] | None = None,
        targets: list[str] | None = None,
        equipment: list[str] | None = None,
        difficulty: str | None = None,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []

        if body_parts:
            where.append(_lower_in_clause("body_part", len(body_parts)))
            params.extend(_normalize_filters(body_parts))
        if targets:
            where.append(_lower_in_clause("target", len(targets)))
            params.extend(_normalize_filters(targets))
        if equipment:
            where.append(_lower_in_clause("equipment", len(equipment)))
            params.extend(_normalize_filters(equipment))
        if difficulty:
            where.append("LOWER(difficulty) = ?")
            params.append(str(difficulty).strip().lower())

        sql = (
            "SELECT id, name, body_part, target, secondary_muscles, equipment, "
            "difficulty, category, description, instructions FROM exercises"
        )
        if where:
            sql += " WHERE " + " AND ".join(where)

        sql += (
            " ORDER BY CASE LOWER(difficulty) "
            "WHEN 'beginner' THEN 1 WHEN 'intermediate' THEN 2 WHEN 'advanced' THEN 3 ELSE 4 END, "
            "name ASC LIMIT ?"
        )
        params.append(_clamp_int(limit, 1, 500, 12))

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        return [self._row_to_exercise(row) for row in rows]

    def find_exercise_by_name(self, name: str) -> list[dict[str, Any]]:
        """Find exercises whose name matches (case-insensitive partial match)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, name, body_part, target, secondary_muscles, equipment, "
                "difficulty, category, description, instructions FROM exercises "
                "WHERE LOWER(name) LIKE ? ORDER BY name ASC LIMIT 5",
                (f"%{name.strip().lower()}%",),
            ).fetchall()
        return [self._row_to_exercise(row) for row in rows]

    def get_distinct_values(self) -> dict[str, list[str]]:
        """Return distinct body parts, targets, and equipment in the database."""
        result: dict[str, list[str]] = {}
        with self._connect() as conn:
            for column, key in [
                ("body_part", "body_parts"),
                ("target", "targets"),
                ("equipment", "equipment"),
                ("difficulty", "difficulties"),
            ]:
                rows = conn.execute(
                    f"SELECT DISTINCT LOWER({column}) AS v FROM exercises WHERE {column} IS NOT NULL AND {column} != '' ORDER BY v"
                ).fetchall()
                result[key] = [str(row["v"]) for row in rows]
        return result

    def list_exercises(self, limit: int = 2000) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, name, body_part, target, secondary_muscles, equipment,
                       difficulty, category, description, instructions
                FROM exercises
                ORDER BY name ASC
                LIMIT ?
                """,
                (_clamp_int(limit, 1, 5000, 2000),),
            ).fetchall()
        return [self._row_to_exercise(row) for row in rows]

    def _row_to_profile(self, row: sqlite3.Row | None) -> UserProfile:
        if row is None:
            raise ValueError("Cannot build profile from empty row")

        return UserProfile(
            session_id=str(row["session_id"]),
            goal=str(row["goal"]),
            experience_level=str(row["experience_level"]),
            days_per_week=_clamp_int(row["days_per_week"], 1, 7, 3),
            session_minutes=_clamp_int(row["session_minutes"], 15, 180, 45),
            injuries=_parse_json_list(row["injuries"]),
            excluded_equipment=_parse_json_list(row["excluded_equipment"]),
            focus_muscles=_parse_json_list(row["focus_muscles"]),
        )

    def _row_to_exercise(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "name": str(row["name"]),
            "bodyPart": str(row["body_part"]),
            "target": str(row["target"]),
            "secondaryMuscles": _parse_json_list(row["secondary_muscles"]),
            "equipment": str(row["equipment"]),
            "difficulty": str(row["difficulty"]),
            "category": str(row["category"]),
            "description": str(row["description"]),
            "instructions": _parse_json_list(row["instructions"]),
        }


def _parse_json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return _clean_string_list(value)
    if value is None:
        return []

    text = str(value).strip()
    if not text:
        return []

    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        return []

    if not isinstance(loaded, list):
        return []
    return _clean_string_list(loaded)


def _clean_string_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []

    cleaned: list[str] = []
    for item in values:
        text = str(item).strip()
        if text:
            cleaned.append(text)
    return cleaned


def _clamp_int(value: Any, minimum: int, maximum: int, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _normalize_filters(values: list[str]) -> list[str]:
    return [str(value).strip().lower() for value in values if str(value).strip()]


def _lower_in_clause(column: str, item_count: int) -> str:
    placeholders = ", ".join(["?"] * item_count)
    return f"LOWER({column}) IN ({placeholders})"
