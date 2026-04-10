"""gruenbeck2lox – FastAPI application entry point."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes_devices import router as devices_router
from backend.api.routes_loxone import router as loxone_router
from backend.api.routes_values import router as values_router
from backend.api.routes_ui import get_static_files
from backend.config import get_settings
from backend.db.database import Database
from backend.db.migrations import run_migrations
from backend.scheduler import Scheduler


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def _load_or_create_fernet(settings) -> Fernet:
    """Return a Fernet instance from env, secret file, or auto-generated key."""
    # 1. Explicit key from environment
    if settings.secret_key:
        try:
            return Fernet(settings.secret_key.encode())
        except (ValueError, InvalidToken) as exc:
            raise RuntimeError(f"GRUENBECK2LOX_SECRET_KEY is invalid: {exc}") from exc

    # 2. Persisted secret file
    secret_file: Path = settings.secret_file
    if secret_file.exists():
        key = secret_file.read_bytes().strip()
    else:
        key = Fernet.generate_key()
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        secret_file.write_bytes(key)
        # Restrict permissions on POSIX systems
        try:
            os.chmod(secret_file, 0o600)
        except AttributeError:
            pass  # Windows – no chmod needed

    return Fernet(key)


def _import_yaml_config(settings, db: Database):
    """Coroutine: import devices/loxone from config.yaml into SQLite if not already present."""
    import asyncio

    async def _do_import() -> None:
        yaml_cfg = settings.load_yaml_config()
        if not yaml_cfg:
            return

        fernet = _load_or_create_fernet(settings)

        for dev in yaml_cfg.get("devices", []):
            existing = await db.fetchone(
                "SELECT id FROM devices WHERE name = ?", (dev["name"],)
            )
            if existing:
                continue
            await db.execute(
                "INSERT INTO devices (name, type, host, port, poll_interval, enabled) VALUES (?,?,?,?,?,1)",
                (dev["name"], dev.get("type", "sc"), dev["host"], dev.get("port", 80), dev.get("poll_interval", 30)),
            )
            logging.getLogger(__name__).info("Imported device from config.yaml: %s", dev["name"])

        for srv in yaml_cfg.get("loxone", []):
            existing = await db.fetchone(
                "SELECT id FROM loxone_servers WHERE name = ?", (srv["name"],)
            )
            if existing:
                srv_id = existing["id"]
            else:
                enc_pw = fernet.encrypt(srv["password"].encode()).decode()
                cursor = await db.execute(
                    "INSERT INTO loxone_servers (name, host, port, user, password_enc, push_mode) VALUES (?,?,?,?,?,?)",
                    (srv["name"], srv["host"], srv.get("port", 80), srv["user"], enc_pw, srv.get("push_mode", "http")),
                )
                srv_id = cursor.lastrowid
                logging.getLogger(__name__).info("Imported Loxone server from config.yaml: %s", srv["name"])

            for mapping in srv.get("mappings", []):
                dev_row = await db.fetchone(
                    "SELECT id FROM devices WHERE name = ?", (mapping["gruenbeck_device"],)
                )
                if not dev_row:
                    continue
                await db.execute(
                    "INSERT OR IGNORE INTO mappings (loxone_server_id, device_id, gruenbeck_key, loxone_input) "
                    "VALUES (?,?,?,?)",
                    (srv_id, dev_row["id"], mapping["gruenbeck_key"], mapping["loxone_input"]),
                )

    return _do_import


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    _setup_logging(settings.log_level)
    logger = logging.getLogger(__name__)
    logger.info("gruenbeck2lox starting up …")

    # Database
    db = Database(settings.db_path)
    await db.connect()
    await run_migrations(db)
    app.state.db = db

    # Encryption
    fernet = _load_or_create_fernet(settings)
    app.state.fernet = fernet

    # Import config.yaml on first run
    importer = _import_yaml_config(settings, db)
    await importer()

    # Scheduler
    scheduler = Scheduler(db=db, fernet=fernet)
    app.state.scheduler = scheduler
    await scheduler.start_all()

    yield

    # Shutdown
    logger.info("gruenbeck2lox shutting down …")
    await scheduler.stop_all()
    await db.close()


app = FastAPI(
    title="gruenbeck2lox",
    description="Grünbeck water softener → Loxone Miniserver bridge",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
_PREFIX = "/api/v1"
app.include_router(devices_router, prefix=_PREFIX)
app.include_router(loxone_router, prefix=_PREFIX)
app.include_router(values_router, prefix=_PREFIX)


@app.get("/api/v1/health", tags=["health"])
async def health() -> dict:
    return {"status": "ok", "service": "gruenbeck2lox"}


# Serve frontend as static files at /
app.mount("/", get_static_files(), name="frontend")
