"""Conexão Postgres da demo — mesmo servidor do app (.env), banco SEPARADO 'joga_demo'."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

try:
    import psycopg2 as _pg
except ImportError:              # fallback p/ psycopg (v3) se for o caso
    import psycopg as _pg

DEMO_DB   = os.getenv("DEMO_DB_NAME", "joga_demo")
MAINT_DB  = os.getenv("DB_NAME", "postgres")


def _params(dbname):
    return dict(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=dbname,
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def conn(dbname=None, autocommit=False):
    c = _pg.connect(**_params(dbname or DEMO_DB))
    c.autocommit = autocommit
    return c
