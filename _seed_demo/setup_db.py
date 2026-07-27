"""
Passo 3b — Cria o banco da demo e aplica o schema (idempotente).
1) conecta no banco de manutenção (o do app) e cria 'joga_demo' se não existir;
2) aplica schema.sql em 'joga_demo'.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import db  # noqa: E402


def main():
    # 0) testa conexão + versão / 1) cria o banco da demo (autocommit; SEM 'with' p/ não abrir transação)
    maint = db.conn(dbname=db.MAINT_DB, autocommit=True)
    try:
        cur = maint.cursor()
        cur.execute("SELECT version()")
        print("Postgres:", cur.fetchone()[0].split(",")[0])
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db.DEMO_DB,))
        if cur.fetchone():
            print(f"banco '{db.DEMO_DB}' já existe.")
        else:
            cur.execute(f'CREATE DATABASE "{db.DEMO_DB}"')
            print(f"banco '{db.DEMO_DB}' criado.")
        cur.close()
    finally:
        maint.close()

    # 2) aplica o schema
    sql = (HERE / "schema.sql").read_text(encoding="utf-8")
    with db.conn(dbname=db.DEMO_DB) as c:
        with c.cursor() as cur:
            cur.execute(sql)
        c.commit()
        with c.cursor() as cur:
            cur.execute("""SELECT count(*) FROM information_schema.tables
                           WHERE table_schema='public'""")
            print(f"schema aplicado — {cur.fetchone()[0]} tabelas em '{db.DEMO_DB}'.")


if __name__ == "__main__":
    main()
