"""
User model for the KRR Workout Planning System.
Stored in data/users.xlsx.
"""

from dataclasses import dataclass, asdict
from typing import List
import pandas as pd


@dataclass
class User:
    """User with profile and workout-related state."""
    user_id: str
    name: str
    age: int
    weight: float  # kg
    experience_level: str  # beginner | intermediate | advanced
    injury_muscles: List[str]
    current_intensity_level: int  # 1-5

    def __post_init__(self):
        if not 1 <= self.current_intensity_level <= 5:
            raise ValueError("current_intensity_level must be between 1 and 5")
        if self.experience_level not in ("beginner", "intermediate", "advanced"):
            raise ValueError("experience_level must be beginner, intermediate, or advanced")

    def to_row(self) -> dict:
        """Convert to dict for Excel row (injury_muscles as comma-separated)."""
        d = asdict(self)
        d["injury_muscles"] = ",".join(self.injury_muscles) if self.injury_muscles else ""
        return d

    @classmethod
    def from_row(cls, row: dict) -> "User":
        """Create User from Excel row."""
        injury = row.get("injury_muscles", "") or ""
        muscles = [m.strip() for m in str(injury).split(",") if m.strip()]
        return cls(
            user_id=str(row["user_id"]),
            name=str(row.get("name", "User")),
            age=int(float(row["age"])),
            weight=float(row["weight"]),
            experience_level=str(row.get("experience_level", "beginner")).strip().lower(),
            injury_muscles=muscles,
            current_intensity_level=int(float(row["current_intensity_level"])),
        )
