"""Route to serve the frontend static files."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

FRONTEND_DIR = Path(__file__).parent.parent.parent / "frontend"

router = APIRouter(tags=["ui"])


def get_static_files() -> StaticFiles:
    return StaticFiles(directory=FRONTEND_DIR, html=True)
