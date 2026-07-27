"""Passo 3g — Validar coerência das tabelas de Compras/estoque (SQL no joga_demo)."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import db  # noqa: E402

QUERIES = {
"COBERTURA — spread (QTDISP = qtestger-qtbloqueada-qtreserv; giro = média 3m)": """
  WITH e AS (SELECT (qtestger-qtbloqueada-qtreserv) qtdisp,
                    (qtvendmes1+qtvendmes2+qtvendmes3)/3.0 gmes FROM pcest)
  SELECT
    count(*) FILTER (WHERE qtdisp<=0)                                              ruptura,
    count(*) FILTER (WHERE qtdisp>0 AND gmes>0 AND qtdisp/(gmes/30)<15)            risco,
    count(*) FILTER (WHERE qtdisp>0 AND gmes>0 AND qtdisp/(gmes/30) BETWEEN 15 AND 45) saudavel,
    count(*) FILTER (WHERE qtdisp>0 AND gmes>0 AND qtdisp/(gmes/30)>45)            parado,
    count(*) FILTER (WHERE gmes=0 AND qtdisp>0)                                    parado_s_giro
  FROM e;""",

"VALIDADE / FEFO — lotes vencendo em 60 dias": """
  SELECT count(*) lotes, count(DISTINCT codprod) produtos, round(sum(qt)) unidades
  FROM pcestendereco WHERE dtval <= DATE '2026-07-24' + 60 AND dtval >= DATE '2026-07-24';""",

"OCUPAÇÃO WMS — posições": """
  SELECT codfilial, count(*) posicoes, count(*) FILTER (WHERE situacao='O') ocupadas,
         count(*) FILTER (WHERE rua=99) pulmao FROM pcendereco GROUP BY codfilial ORDER BY codfilial;""",

"PEDIDO DE COMPRA — abertos (descontam da sugestão)": """
  SELECT count(*) FILTER (WHERE dtentradaestoque IS NULL) abertos,
         round(sum(vltotal) FILTER (WHERE dtentradaestoque IS NULL)) valor_aberto,
         count(*) total FROM pcpedido;""",

"VENCIDOS — pcmov × pcnfsaid (join por NUMTRANSVENDA)": """
  SELECT count(*) linhas, count(DISTINCT m.codprod) produtos, round(sum(m.qt*m.punit)) valor
  FROM pcmov m JOIN pcnfsaid n USING (numtransvenda);""",

"VERBAS — por conta (não canceladas)": """
  SELECT codconta, count(*) qt, round(sum(valor)) total
  FROM pcverba WHERE dtcancel IS NULL GROUP BY codconta ORDER BY total DESC;""",
}


def run():
    with db.conn() as c:
        cur = c.cursor()
        for titulo, sql in QUERIES.items():
            print(f"\n===== {titulo} =====")
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            print("  " + " | ".join(f"{x:>14}" for x in cols))
            for r in cur.fetchall():
                print("  " + " | ".join(f"{str(v):>14}" for v in r))


if __name__ == "__main__":
    run()
