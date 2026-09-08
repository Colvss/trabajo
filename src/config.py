"""Carga de configuración y secretos.

Los secretos viven en variables de entorno (.env en local, GitHub Secrets en
CI). La configuración de negocio vive en config.yml. Nunca se mezclan.
"""

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env")


def load_config() -> dict:
    with open(ROOT / "config.yml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


CFG = load_config()

OPENAQ_API_KEY = os.environ.get("OPENAQ_API_KEY", "").strip()
MOTHERDUCK_TOKEN = os.environ.get("MOTHERDUCK_TOKEN", "").strip()

# Permite apuntar a una base local sin tocar config.yml:
#   DB_TARGET=airq.duckdb python -m src.run_pipeline
DB_TARGET = os.environ.get("DB_TARGET", CFG["database"]["target"]).strip()

SCHEMA_RAW = CFG["database"]["schemas"]["raw"]
SCHEMA_STG = CFG["database"]["schemas"]["staging"]
SCHEMA_MART = CFG["database"]["schemas"]["mart"]


def require_api_key() -> str:
    if not OPENAQ_API_KEY:
        raise SystemExit(
            "Falta OPENAQ_API_KEY.\n"
            "  1. Registrate gratis en https://explore.openaq.org/register\n"
            "  2. Copiá .env.example a .env y pegá la key ahí."
        )
    return OPENAQ_API_KEY
