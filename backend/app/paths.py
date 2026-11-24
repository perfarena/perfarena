"""The repo root and the data folder, for the app and its scripts.

In the source tree the data folder is <repo>/data. In the Docker image, /data is
the database volume, so the Dockerfile copies the data folder to /app/data and
sets ARENA_DATA_DIR to it.
"""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = Path(os.environ.get("ARENA_DATA_DIR") or REPO_ROOT / "data")
