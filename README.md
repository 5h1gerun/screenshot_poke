# OBS Screenshot/Template Tool (GUI Only)

This repository provides the Tkinter-based GUI application combined_app.py for capturing OBS WebSocket scenes, performing template matching, and producing images and text updates for OBS.

## Requirements
- Python 3.10+
- OBS (with WebSocket enabled)
- Windows / macOS / Linux (screen coordinates and template assets depend on your setup)

## Setup
1. Install dependencies:

`
pip install -r requirements.txt
`

2. Configure environment variables (recommended):
   - Copy .env.example to .env.
   - Update the values to match your OBS host/port/password and the base working directory (BASE_DIR).

All of these values can also be supplied from the GUI once the app is running. Secrets such as passwords should stay in .env, which is ignored by git.

## Usage
Run the GUI application with:

`
python combined_app.py
`

After launch, confirm:
- OBS connection information (host, port, password)
- Base directory (BASE_DIR), which should contain/produce handantmp, haisin, and koutiku

## Directory Overview
- handantmp/: Template images and in-progress screenshots
- haisin/: Output images for streaming
- koutiku/: Saved materials for later reuse

The .gitignore keeps volatile/generated images (e.g., scene*.png, screenshot*.png, *cropped*.png) out of version control. Preserve required templates such as anme*.jpg and masu.png.

## Security Notes
- Keep passwords and personal data in environment variables, not in the source code.
- Verify licensing/rights for any template or screenshot assets before sharing publicly.
- Scrub secrets when sharing logs or images in PRs/issues.

## License
Add an appropriate license file (e.g., MIT or Apache-2.0) before publishing.
