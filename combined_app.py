"""Refactored entrypoint for the GUI app.

This file now defers to the modular implementation under app/.
Run with: python combined_app.py
"""

# Load environment variables from .env if present
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    # If python-dotenv is not installed, fallback silently
    pass

from app.ui.app import main


if __name__ == "__main__":
    main()
