"""
Passo 3e — Validar coerência da base sintética (SQL no joga_demo).
Confirma: fórmulas reconstruídas, mix de operação, sazonalidade/YoY, spread RFM,
curva ABC, margem por depto. Se tudo 'acende', a demo está pronta pra plugar.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import db  # noqa: E402

QUERIES = {
"MIX DE OPERAÇÃO (CODOPER)": """
  SELECT codoper, count(*) linhas, round(sum(vlvenda)::numeric,2) venda,
         round(sum(vlcustofin+vlcustofinbonif)::numeric,2) custo
  FROM faturamento_vendas GROUP BY codoper ORDER BY venda DESC NULLS LAST;""",

"MEDIDAS RECONSTRUÍDAS (nossas fórmulas)": """
  SELECT
    round(sum(vlvenda-icmsretido-vlfecp) FILTER (WHERE codoper='S')::numeric,2)      venda_bruta,
    round(sum(vlcustofin+vlcustofinbonif) FILTER (WHERE codoper IN ('S','SB'))::numeric,2) custo_total,
    (SELECT round(sum(vldevolucao)::numeric,2) FROM faturamento_devolucao
        WHERE NOT (codativ=37 AND coddevol<>9))                                       total_devolucao
  FROM faturamento_vendas;""",

"SÉRIE MENSAL / SAZONALIDADE + YoY": """
  SELECT to_char(dtsaida,'YYYY-MM') mes, round(sum(vlvenda-icmsretido-vlfecp)::numeric,0) venda_bruta
  FROM faturamento_vendas WHERE codoper='S' GROUP BY 1 ORDER BY 1;""",

"RFM — spread de recência/frequência/valor": """
  WITH rfm AS (
    SELECT codcli, (DATE '2026-07-24' - max(dtsaida)) rec,
           count(DISTINCT numtransvenda) freq, sum(vlvenda) valor
    FROM faturamento_vendas WHERE codoper='S' GROUP BY codcli)
  SELECT count(*) clientes, round(avg(rec)) rec_media,
         percentile_cont(0.5) WITHIN GROUP (ORDER BY rec)::int rec_mediana,
         min(rec) rec_min, max(rec) rec_max,
         round(avg(freq),1) freq_media, max(freq) freq_max FROM rfm;""",

"RFM — histograma de recência (dias sem comprar)": """
  WITH rfm AS (SELECT codcli,(DATE '2026-07-24'-max(dtsaida)) rec
               FROM faturamento_vendas WHERE codoper='S' GROUP BY codcli)
  SELECT width_bucket(rec,0,900,9)*100 ate_dias, count(*) clientes FROM rfm GROUP BY 1 ORDER BY 1;""",

"ABC — concentração (quintis de venda por produto)": """
  WITH p AS (SELECT codprod, sum(vlvenda) v FROM faturamento_vendas WHERE codoper='S' GROUP BY codprod),
       r AS (SELECT v, ntile(5) OVER (ORDER BY v DESC) q FROM p)
  SELECT q quintil, count(*) produtos, round(sum(v)::numeric,0) venda,
         round((100*sum(v)/sum(sum(v)) OVER ())::numeric,1) pct FROM r GROUP BY q ORDER BY q;""",

"MARGEM POR DEPTO (top 10)": """
  SELECT codepto, round(sum(vlvenda)::numeric,0) venda,
         round((100*(1-sum(vlcustofin)/sum(vlvenda)))::numeric,1) margem_pct
  FROM faturamento_vendas WHERE codoper='S' GROUP BY codepto ORDER BY venda DESC LIMIT 10;""",
}


def run():
    with db.conn() as c:
        cur = c.cursor()
        for titulo, sql in QUERIES.items():
            print(f"\n===== {titulo} =====")
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            print("  " + " | ".join(f"{c:>14}" for c in cols))
            for r in rows:
                print("  " + " | ".join(f"{str(v):>14}" for v in r))


if __name__ == "__main__":
    run()
