"""Helpers shared across API route modules."""

from __future__ import annotations

from fastapi import Request

from backend.db.database import Database


def get_db(request: Request) -> Database:
    """FastAPI dependency: return the Database from app state."""
    return request.app.state.db


def get_crypto(request: Request):
    """FastAPI dependency: return the Fernet instance from app state."""
    return request.app.state.fernet
