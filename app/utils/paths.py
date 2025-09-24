from __future__ import annotations

import os


def get_koutiku_dir(base_dir: str) -> str:
    """Return the path to the screenshots output directory (koutiku).

    Directory name is configurable via env `OUTPUT_KOUTIKU_DIR` (default: "koutiku").
    """
    name = os.getenv("OUTPUT_KOUTIKU_DIR", "koutiku").strip() or "koutiku"
    return os.path.join(base_dir, name)


def get_haisin_dir(base_dir: str) -> str:
    """Return the path to the broadcast/distribution directory (haisin).

    Directory name is configurable via env `OUTPUT_HAISIN_DIR` (default: "haisin").
    """
    name = os.getenv("OUTPUT_HAISIN_DIR", "haisin").strip() or "haisin"
    return os.path.join(base_dir, name)


def get_output_format_ext() -> str:
    """Return the configured image format extension (lowercase, without dot).

    Controlled by env `OUTPUT_IMAGE_FORMAT` with values like PNG/JPG/WEBP.
    Defaults to PNG.
    """
    fmt = (os.getenv("OUTPUT_IMAGE_FORMAT", "PNG") or "PNG").strip().lower()
    if fmt in ("jpg", "jpeg"):
        return "jpg"
    if fmt in ("webp",):
        return "webp"
    # default png for unknown values
    return "png"


def get_broadcast_output_path(base_dir: str) -> str:
    """Return full path for the broadcast image file.

    Uses `OUTPUT_HAISIN_BASENAME` (default: "haisinyou") and `OUTPUT_IMAGE_FORMAT`.
    """
    base = os.getenv("OUTPUT_HAISIN_BASENAME", "haisinyou").strip() or "haisinyou"
    ext = get_output_format_ext()
    return os.path.join(get_haisin_dir(base_dir), f"{base}.{ext}")


def get_results_csv_path(base_dir: str) -> str:
    """Return path to results CSV within koutiku directory."""
    return os.path.join(get_koutiku_dir(base_dir), "_results.csv")


def get_tags_json_path(base_dir: str) -> str:
    """Return path to tags JSON within koutiku directory."""
    return os.path.join(get_koutiku_dir(base_dir), "_tags.json")


def get_pairs_json_path(base_dir: str) -> str:
    """Return path to pairs JSON within koutiku directory."""
    return os.path.join(get_koutiku_dir(base_dir), "_pairs.json")

