"""
Passo 4d — Módulo Compras no modo BD (SQL no joga_demo). Fórmulas oficiais do core.py:
  QTDISP = qtestger-qtbloqueada-qtreserv ; giro_dia = (m1+m2+m3)/3/30
  cobertura_dias = CEIL(QTDISP/giro_dia) ; ruptura = qtdisp<=0 E giro>0
  sugestão = giro_dia*(lead+45) - (qtdisp + pedido_aberto) , em caixas
  orçamento = 65% da venda líq 30d por comprador × realizado (pedidos, exclui transferência)
Telas: Cockpit/Cobertura, Abastecimento (sugestão), Ruptura, Validade/Vencidos, Orçamento.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import db  # noqa: E402

REF = "DATE '2026-07-24'"

BASE = f"""
  WITH aberto AS (
    SELECT i.codprod, sum(i.qtpedida - i.qtentregue) qt_aberto
    FROM pcitem i JOIN pcpedido p ON p.numped=i.numped
    WHERE p.dtentradaestoque IS NULL AND p.dtemissao >= {REF} - 180
    GROUP BY i.codprod),
  base AS (
    SELECT e.codprod, e.codfilial,
      (e.qtestger-e.qtbloqueada-e.qtreserv) qtdisp,
      (e.qtvendmes1+e.qtvendmes2+e.qtvendmes3)/3.0/30 giro_dia,
      e.custofin, pr.qtunitcx, coalesce(f.prazoentrega,10) lead,
      coalesce(a.qt_aberto,0) qt_aberto
    FROM pcest e
    JOIN pcprodut pr ON pr.codprod=e.codprod
    LEFT JOIN pcfornec f ON f.codfornec=pr.codfornec
    LEFT JOIN aberto a ON a.codprod=e.codprod)
"""


def brl(v):
    return f"R$ {float(v or 0):,.0f}"


def run():
    with db.conn() as c:
        cur = c.cursor()

        print("===== COCKPIT / COBERTURA (filiais 3+5) =====")
        cur.execute(BASE + """
          SELECT count(*) FILTER (WHERE qtdisp<=0 AND giro_dia>0)                         ruptura,
                 count(*) FILTER (WHERE giro_dia>0 AND qtdisp>0 AND ceil(qtdisp/giro_dia)<=30)  critico,
                 count(*) FILTER (WHERE giro_dia>0 AND qtdisp>0 AND ceil(qtdisp/giro_dia) BETWEEN 31 AND 120) saudavel,
                 count(*) FILTER (WHERE giro_dia>0 AND ceil(qtdisp/giro_dia)>120)          excesso,
                 count(*) FILTER (WHERE giro_dia=0 AND qtdisp>0)                           parado_sem_giro,
                 round(sum(qtdisp*custofin))                                               valor_estoque
          FROM base""")
        cols = [d[0] for d in cur.description]
        r = cur.fetchone()
        for k, v in zip(cols, r):
            print(f"  {k:18} {v:>12,}" if k != "valor_estoque" else f"  {k:18} {brl(v):>12}")

        print("\n===== ABASTECIMENTO — sugestão de compra (top 10 por valor) =====")
        cur.execute(BASE + """
          SELECT codprod, codfilial, round(giro_dia*30) giro_mes, round(qtdisp) qtdisp,
                 round(qt_aberto) aberto,
                 ceil((giro_dia*(lead+45) - (qtdisp+qt_aberto))/nullif(qtunitcx,0)) caixas,
                 round((giro_dia*(lead+45) - (qtdisp+qt_aberto))*custofin) valor
          FROM base
          WHERE giro_dia>0 AND (giro_dia*(lead+45) - (qtdisp+qt_aberto)) > 0
          ORDER BY valor DESC LIMIT 10""")
        print(f"  {'codprod':>8} {'fil':>3} {'giro/mês':>8} {'disp':>7} {'aberto':>7} {'caixas':>7} {'valor':>12}")
        for cod, fil, gm, disp, ab, cx, val in cur.fetchall():
            print(f"  {cod:>8} {fil:>3} {gm:>8,} {disp:>7,} {ab:>7,} {cx:>7,} {brl(val):>12}")

        print("\n===== RUPTURA (estoque<=0 E giro>0) — por comprador =====")
        cur.execute(BASE + """
          SELECT f.codcomprador, em.nome, count(*) itens_ruptura,
                 round(sum(b.giro_dia*30*b.custofin)) venda_mes_risco
          FROM base b JOIN pcprodut pr ON pr.codprod=b.codprod
          LEFT JOIN pcfornec f ON f.codfornec=pr.codfornec
          LEFT JOIN pcempr em ON em.matricula=f.codcomprador
          WHERE b.qtdisp<=0 AND b.giro_dia>0
          GROUP BY f.codcomprador, em.nome ORDER BY itens_ruptura DESC LIMIT 8""")
        for cc, nome, n, risco in cur.fetchall():
            print(f"  comprador {str(cc):>3} {str(nome)[:20]:20}  {n:>4} itens  risco/mês {brl(risco)}")

        print("\n===== VALIDADE / FEFO + VENCIDOS =====")
        cur.execute(f"""SELECT count(*), count(DISTINCT codprod), round(sum(qt))
                        FROM pcestendereco WHERE dtval BETWEEN {REF} AND {REF}+60""")
        lotes, prods, un = cur.fetchone()
        print(f"  vencendo em 60d: {lotes} lotes, {prods} produtos, {un:,.0f} unidades")
        cur.execute(f"""SELECT count(*), round(sum(m.qt*m.punit))
                        FROM pcmov m JOIN pcnfsaid n USING(numtransvenda)
                        WHERE n.dtsaida >= {REF}-365""")
        vl, valor = cur.fetchone()
        print(f"  vencidos (perda validade, 12m): {vl} baixas, {brl(valor)}")

        print("\n===== ORÇAMENTO DE COMPRAS — meta 65% × realizado (30d, por comprador) =====")
        cur.execute(f"""
          WITH venda AS (
            SELECT codcomprador, sum(vlvenda-icmsretido-vlfecp) v30
            FROM faturamento_vendas WHERE codoper='S' AND dtsaida >= {REF}-30
            GROUP BY codcomprador),
          compras AS (
            SELECT codcomprador, sum(vltotal) realizado
            FROM pcpedido WHERE dtemissao >= {REF}-30 GROUP BY codcomprador)
          SELECT em.matricula, em.nome, coalesce(v.v30,0)*0.65 meta, coalesce(c.realizado,0) realizado
          FROM pcempr em
          LEFT JOIN venda v ON v.codcomprador=em.matricula
          LEFT JOIN compras c ON c.codcomprador=em.matricula
          ORDER BY meta DESC""")
        print(f"  {'comprador':22} {'meta (65%)':>14} {'realizado':>14} {'atingido':>9}")
        for mat, nome, meta, real in cur.fetchall():
            meta, real = float(meta or 0), float(real or 0)
            pct = real / meta * 100 if meta else 0
            print(f"  {str(nome)[:22]:22} {brl(meta):>14} {brl(real):>14} {pct:>7.0f}%")


if __name__ == "__main__":
    run()
