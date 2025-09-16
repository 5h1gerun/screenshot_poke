"""App package for OBS screenshot/template GUI.

This package contains:
- obs_client: thin, thread-safe wrapper around obs-websocket-py
- utils: image utilities (crop, match template)
- threads: worker threads for each feature
- ui: CustomTkinter GUI wiring the pieces together

Keep runtime dependencies minimal and avoid side effects on import.
"""

