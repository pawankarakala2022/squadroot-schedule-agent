"""Start the demo: `.venv\\Scripts\\python -m app`, then open http://127.0.0.1:8000."""

import uvicorn


def main() -> None:
    """Serve the demo app on loopback (a temporary local server, not hosting)."""
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
