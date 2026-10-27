"""Settings, read once from the environment (see deploy/bidcraft.env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml


def _get(name: str, default: str | None = None, *, required: bool = False) -> str | None:
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(f"Missing required setting: {name}")
    return value


@dataclass(frozen=True)
class Settings:
    database_url: str
    files_root: Path
    internal_token: str
    openai_api_key: str | None
    openai_model: str
    openai_base_url: str | None
    vault_key: str
    embed_model: str
    embed_cache: Path
    policy_path: Path
    company_name: str
    company_city: str
    company_cin: str
    ocr_enabled: bool
    ocr_languages: str
    signature_font: str | None
    sign_p12_path: str | None
    sign_p12_password: str | None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    here = Path(__file__).resolve().parent.parent
    return Settings(
        database_url=_get("DATABASE_URL", "dbname=bidcraft user=bidcraft host=/var/run/postgresql"),
        files_root=Path(_get("FILES_ROOT", "/srv/bidcraft/files")),
        internal_token=_get("AI_INTERNAL_TOKEN", required=True),
        openai_api_key=_get("OPENAI_API_KEY"),
        openai_model=_get("OPENAI_MODEL", required=True),
        openai_base_url=_get("OPENAI_BASE_URL") or None,
        vault_key=_get("VAULT_KEY", required=True),
        embed_model=_get("EMBED_MODEL", "BAAI/bge-small-en-v1.5"),
        embed_cache=Path(_get("EMBED_CACHE", "/opt/bidcraft/models")),
        policy_path=Path(_get("POLICY_PATH", str(here / "config" / "redaction_policy.yaml"))),
        company_name=_get("COMPANY_NAME", "Your Company Limited"),
        company_city=_get("COMPANY_CITY", "New Delhi"),
        company_cin=_get("COMPANY_CIN", ""),
        ocr_enabled=_get("OCR_ENABLED", "true").lower() == "true",
        ocr_languages=_get("OCR_LANGUAGES", "eng+hin"),
        signature_font=_get("SIGNATURE_FONT") or None,
        sign_p12_path=_get("SIGN_P12_PATH") or None,
        sign_p12_password=_get("SIGN_P12_PASSWORD") or None,
    )


def load_policy() -> dict:
    """Read the outbound data policy. Re-read on every call so edits apply without code changes."""
    with open(get_settings().policy_path, encoding="utf-8") as f:
        return yaml.safe_load(f)
