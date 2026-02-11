"""Run the FastAPI workout planner web app."""

from __future__ import annotations

import os

import uvicorn

from src.webapp.app import app


if __name__ == "__main__":
    host = os.getenv("APP_HOST", "127.0.0.1")
    port = int(os.getenv("APP_PORT", "8000"))
    uvicorn.run("web_main:app", host=host, port=port, reload=True)
