from __future__ import annotations

import argparse

import uvicorn


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the workout planner FastAPI backend")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind (default: 8000)")
    parser.add_argument("--reload", action="store_true", default=True, help="Enable auto-reload")
    parser.add_argument("--no-reload", action="store_false", dest="reload", help="Disable auto-reload")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    uvicorn.run("api.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
