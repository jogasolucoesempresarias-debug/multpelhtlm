"""Seeder de metas da DEMO — popula `multpel_metas` no banco de AUTH da instância de demonstração
pra o painel /metas mostrar meta × realizado (barras de atingimento), em vez de só o realizado.

Fonte da meta = realizado do `joga_demo` (pcpedc/faturamento, via provider_sql.metas_realizado) × um
fator (default 0.95 → atingimento ~105%, verde e crível). Grão = vendedor (codusur), como o app espera.

────────────────────────────────────────────────────────────────────────────────────────────────────
⚠️  TRAVA DE SEGURANÇA — este script ESCREVE no banco de AUTH (DB_*), não no joga_demo:
    1) exige a env  DEMO_SEED=1  (opt-in explícito);
    2) RECUSA rodar se o auth DB alvo for a produção da Multpel (DB_NAME == 'multpel_db').
    Aponte DB_NAME pro auth DB da instância de DEMO antes de rodar. O nome do alvo é impresso.
────────────────────────────────────────────────────────────────────────────────────────────────────

Uso:
    DEMO_SEED=1 DB_NAME=<auth_db_da_demo> \
      python -X utf8 _seed_demo/seed_metas_demo.py [--ano AAAA] [--mes M] [--fator 0.95]
"""
import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

try:
    import psycopg2 as _pg
except ImportError:
    import psycopg as _pg

# provider_sql está na raiz do app (um nível acima de _seed_demo)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import provider_sql  # noqa: E402  (lê o joga_demo analítico)

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

PROD_AUTH_DB = "multpel_db"   # auth de PRODUÇÃO da Multpel — nunca semear demo aqui

_DDL = """
CREATE TABLE IF NOT EXISTS multpel_metas (
    id                 SERIAL PRIMARY KEY,
    ano                INTEGER NOT NULL,
    mes                INTEGER NOT NULL,
    codusur            INTEGER NOT NULL,
    valor_meta         NUMERIC(14,2) DEFAULT 0,
    clientes_meta      INTEGER       DEFAULT 0,
    mix_meta           INTEGER       DEFAULT 0,
    rentabilidade_meta NUMERIC(14,2) DEFAULT 0,
    atualizado_em      TIMESTAMP     DEFAULT NOW(),
    atualizado_por     INTEGER,
    UNIQUE (ano, mes, codusur)
)
"""

_UPSERT = """
INSERT INTO multpel_metas
    (ano, mes, codusur, valor_meta, clientes_meta, mix_meta, rentabilidade_meta, atualizado_em)
VALUES (%s,%s,%s,%s,%s,%s,%s, NOW())
ON CONFLICT (ano, mes, codusur) DO UPDATE SET
    valor_meta = EXCLUDED.valor_meta,
    clientes_meta = EXCLUDED.clientes_meta,
    mix_meta = EXCLUDED.mix_meta,
    rentabilidade_meta = EXCLUDED.rentabilidade_meta,
    atualizado_em = NOW()
"""


def _auth_conn():
    """Conexão com o banco de AUTH (DB_*) — o mesmo que get_db() do app usa."""
    return _pg.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", PROD_AUTH_DB),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", ""),
    )


def _guardas(alvo):
    if os.getenv("DEMO_SEED") != "1":
        sys.exit("ABORTADO: defina DEMO_SEED=1 pra confirmar que quer semear metas SINTÉTICAS.")
    if alvo == PROD_AUTH_DB:
        sys.exit(f"ABORTADO: DB_NAME='{alvo}' é a AUTH de PRODUÇÃO da Multpel. Aponte DB_NAME pro "
                 f"auth DB da instância de DEMO antes de rodar (nunca semear meta sintética na produção).")


def main():
    ap = argparse.ArgumentParser(description="Semeia multpel_metas (DEMO) a partir do realizado do joga_demo.")
    ap.add_argument("--ano", type=int, default=None)
    ap.add_argument("--mes", type=int, default=None)
    ap.add_argument("--fator", type=float, default=0.95,
                    help="meta = fator × realizado (0.95 → atingimento ~105%%)")
    args = ap.parse_args()

    alvo = os.getenv("DB_NAME", PROD_AUTH_DB)
    _guardas(alvo)

    h = provider_sql.hoje_analitico()          # "hoje" ancorado no dado da demo
    ano = args.ano or h.year
    mes = args.mes or h.month
    print(f"[seed_metas_demo] AUTH DB alvo = '{alvo}'  |  período = {ano}-{mes:02d}  |  fator = {args.fator}")

    # Realizado por vendedor (venda/rentabilidade/clientes/mix) do joga_demo.
    realizado = provider_sql.metas_realizado(ano, mes, None)["por_vendedor"]
    if not realizado:
        sys.exit("ABORTADO: sem realizado no joga_demo pro período — nada a semear (confira o pcpedc).")

    f = args.fator
    linhas = []
    for cu, r in realizado.items():
        venda = float(r.get("venda") or 0)
        if venda <= 0:
            continue   # vendedor sem venda de pedido no mês → não cria meta fantasma
        linhas.append((ano, mes, int(cu),
                       round(venda * f, 2),
                       int(round((r.get("clientes") or 0) * f)),
                       int(round((r.get("mix") or 0) * f)),
                       round(float(r.get("rentabilidade") or 0) * f, 2)))

    conn = _auth_conn()
    cur = conn.cursor()
    try:
        cur.execute(_DDL)                      # garante a tabela na auth de demo
        cur.executemany(_UPSERT, linhas)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    total = sum(l[3] for l in linhas)
    print(f"[seed_metas_demo] OK: {len(linhas)} metas semeadas (soma valor_meta = R$ {total:,.2f}).")


if __name__ == "__main__":
    main()
