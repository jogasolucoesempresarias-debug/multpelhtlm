"""
Passo 4c — Mais telas do Comercial no modo BD (SQL no joga_demo):
  - Vendedores (ranking 12m, lucro, positivação, YoY)
  - Categorias (treemap deptos: venda × margem + top fornecedores)
  - Tendências (cohort retention — reusa cohort.py do app, intacto)
"""
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import db      # noqa: E402
import cohort  # noqa: E402  (módulo puro do app)
import gerar as G  # noqa: E402
import random
import perfil as P  # noqa: E402

REF, D12, D24 = date(2026, 7, 24), date(2025, 7, 24), date(2024, 7, 24)
VB = "sum(vlvenda-icmsretido-vlfecp) FILTER (WHERE codoper='S')"
LUCRO = "sum((vlvenda-icmsretido-vlfecp)-vlcustofin) FILTER (WHERE codoper='S')"

# nomes de depto (reconstrói o catálogo determinístico)
G.rng = random.Random(P.SEED)
DEPTO_NOME = {d[0]: d[1] for d in G.gen_deptos()}


def brl(v):
    return f"R$ {v:,.0f}"


def vendedores(cur):
    cur.execute("""
      SELECT fv.codusur, u.nome,
        sum(vlvenda-icmsretido-vlfecp) FILTER (WHERE codoper='S' AND dtsaida>=%s) v12,
        sum((vlvenda-icmsretido-vlfecp)-vlcustofin) FILTER (WHERE codoper='S' AND dtsaida>=%s) l12,
        count(DISTINCT codcli) FILTER (WHERE codoper='S' AND dtsaida>=%s) pos,
        sum(vlvenda-icmsretido-vlfecp) FILTER (WHERE codoper='S' AND dtsaida>=%s AND dtsaida<%s) vant
      FROM faturamento_vendas fv LEFT JOIN pcusuari u ON u.codusur=fv.codusur
      GROUP BY fv.codusur, u.nome ORDER BY v12 DESC NULLS LAST LIMIT 10""",
                (D12, D12, D12, D24, D12))
    print("\n===== VENDEDORES (ranking 12m) =====")
    print(f"  {'RCA':>5} {'nome':22} {'venda 12m':>14} {'lucro':>13} {'posit.':>7} {'YoY':>7}")
    for cod, nome, v12, l12, pos, vant in cur.fetchall():
        v12, l12, vant = float(v12 or 0), float(l12 or 0), float(vant or 0)
        yoy = (v12 / vant - 1) * 100 if vant else 0
        print(f"  {cod:>5} {str(nome)[:22]:22} {brl(v12):>14} {brl(l12):>13} {pos:>7,} {yoy:>+6.1f}%")


def categorias(cur):
    cur.execute(f"""
      SELECT codepto, {VB} venda,
        (1 - sum(vlcustofin) FILTER (WHERE codoper='S')/nullif({VB},0))*100 margem
      FROM faturamento_vendas WHERE dtsaida>=%s GROUP BY codepto ORDER BY venda DESC NULLS LAST LIMIT 12""",
                (D12,))
    print("\n===== CATEGORIAS (treemap: tamanho=venda, cor=margem) =====")
    print(f"  {'depto':28} {'venda 12m':>14} {'margem':>8}")
    for cod, venda, margem in cur.fetchall():
        nome = DEPTO_NOME.get(cod, f"DEPTO {cod}")
        print(f"  {nome[:28]:28} {brl(float(venda or 0)):>14} {float(margem or 0):>6.1f}%")

    cur.execute(f"""
      SELECT fornecprinc, {VB} venda FROM faturamento_vendas
      WHERE dtsaida>=%s AND fornecprinc IS NOT NULL GROUP BY fornecprinc ORDER BY venda DESC NULLS LAST LIMIT 8""",
                (D12,))
    print("\n  --- Top 8 fornecedores ---")
    for nome, venda in cur.fetchall():
        print(f"  {str(nome)[:28]:28} {brl(float(venda or 0)):>14}")


def tendencias(cur):
    cur.execute("""SELECT codcli, date_trunc('month', dtsaida)::date
                   FROM faturamento_vendas WHERE codoper='S' GROUP BY codcli, 2""")
    compras = {}
    for codcli, m in cur.fetchall():
        compras.setdefault(codcli, []).append(m)
    matriz = cohort.matriz_cohort(cohort.cohort_de_compras(compras), meses_max=6)
    print("\n===== TENDÊNCIAS — cohort retention (reusa cohort.py do app) =====")
    print(f"  {'aquisição':10} {'tam.':>6}  M+0   M+1   M+2   M+3   M+4   M+5   M+6")
    for row in matriz[:10]:
        ret = "  ".join(f"{int(r*100):>3}%" for r in row["retencao"])
        print(f"  {row['aquisicao']:10} {row['tamanho']:>6,}  {ret}")
    print(f"  ...({len(matriz)} coortes no total)")


if __name__ == "__main__":
    with db.conn() as c:
        cur = c.cursor()
        vendedores(cur)
        categorias(cur)
        tendencias(cur)
