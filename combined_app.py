"""Refactored entrypoint for the GUI app.

This file now defers to the modular implementation under app/.
Run with: python combined_app.py
"""

# Load environment variables from .env if present
try:
    import os, sys
    from pathlib import Path
    from dotenv import load_dotenv  # type: ignore
    # Prefer .env next to the executable (frozen) or this file
    if getattr(sys, "frozen", False):
        env_dir = Path(sys.executable).resolve().parent
    else:
        env_dir = Path(__file__).resolve().parent
    dotenv_path = env_dir / ".env"
    load_dotenv(dotenv_path=str(dotenv_path))
except Exception:
    # If python-dotenv is not installed, fallback silently
    pass

from app.ui.app import main


if __name__ == "__main__":
    main()
