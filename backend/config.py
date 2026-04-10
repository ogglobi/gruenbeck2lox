"""Application configuration – reads from environment variables and YAML file."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """All runtime settings for gruenbeck2lox.

    Values are read from environment variables with prefix ``GRUENBECK2LOX_``.
    A ``config.yaml`` file in the data directory provides initial device /
    Loxone server definitions that are imported into SQLite on first run.
    """

    model_config = SettingsConfigDict(
        env_prefix="GRUENBECK2LOX_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    data_dir: Path = Field(default=Path("/app/data"), description="Persistent data directory")
    log_level: str = Field(default="INFO", description="Logging level")
    secret_key: str = Field(
        default="",
        description=(
            "Fernet key for encrypting Loxone passwords. "
            "Auto-generated and stored in DATA_DIR/.secret if empty."
        ),
    )
    host: str = Field(default="0.0.0.0", description="Bind host for the HTTP server")
    port: int = Field(default=8080, description="Bind port for the HTTP server")

    @property
    def db_path(self) -> Path:
        """Absolute path to the SQLite database file."""
        return self.data_dir / "gruenbeck2lox.db"

    @property
    def secret_file(self) -> Path:
        """Path to the auto-generated Fernet key file."""
        return self.data_dir / ".secret"

    @property
    def config_yaml(self) -> Path:
        """Path to the optional YAML configuration file."""
        return self.data_dir / "config.yaml"

    def load_yaml_config(self) -> dict:
        """Load raw YAML config dict; return empty dict if file is missing."""
        if self.config_yaml.exists():
            with self.config_yaml.open(encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {}
        return {}


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the singleton settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
