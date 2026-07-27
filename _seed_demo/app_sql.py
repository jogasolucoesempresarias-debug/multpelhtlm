"""
Passo 4a — Camada SQL do modo BD (prova de conceito): Dashboard executivo lido do joga_demo.
Usa as fórmulas RCA reconstruídas/validadas. Espelha o alinhamento do README:
  Receita Líquida = VENDA BRUTA(dtsaida) − TOTAL DEVOLUCAO(dtent) − TOTAL DEVOL AVULSA(dtent)
  Lucro           = Líquida − (CUSTO TOTAL − CUSTO DEVOLUCAO − CUSTO DEVOL AVULSA)
Ainda NÃO toca no app — é o embrião do adaptador (modo DATA_SOURCE=db).
"""
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import db  # noqa: E402

# expressões das medidas (as regras que decodificamos)
VB   = "sum(vlvenda - icmsretido - vlfecp) FILTER (WHERE codoper='S')"
CT   = "sum(vlcustofin + vlcustofinbonif) FILTER (WHERE codoper IN ('S','SB'))"
DEV  = "sum(vldevolucao) FILTER (WHERE NOT (codativ=37 AND coddevol<>9))"
CDEV = "sum(vlcustofin)  FILTER (WHERE NOT (codativ=37 AND coddevol<>9))"


def _scalar(cur, sql, args):
    cur.execute(sql, args)
    v = cur.fetchone()[0]
    return float(v) if v is not None else 0.0


def resultado(cur, d0, d1):
    vb = _scalar(cur, f"SELECT {VB} FROM faturamento_vendas WHERE dtsaida BETWEEN %s AND %s", (d0, d1))
    ct = _scalar(cur, f"SELECT {CT} FROM faturamento_vendas WHERE dtsaida BETWEEN %s AND %s", (d0, d1))
    dev = _scalar(cur, f"SELECT {DEV} FROM faturamento_devolucao WHERE dtent BETWEEN %s AND %s", (d0, d1))
    cdev = _scalar(cur, f"SELECT {CDEV} FROM faturamento_devolucao WHERE dtent BETWEEN %s AND %s", (d0, d1))
    dav = _scalar(cur, "SELECT sum(vldevolucao) FROM faturamento_devolucao_avulsa WHERE dtent BETWEEN %s AND %s", (d0, d1))
    cdav = _scalar(cur, "SELECT sum(vlcusto) FROM faturamento_devolucao_avulsa WHERE dtent BETWEEN %s AND %s", (d0, d1))
    liquida = vb - dev - dav
    lucro = liquida - (ct - cdev - cdav)
    return {"vb": vb, "liquida": liquida, "lucro": lucro,
            "margem": (lucro / liquida * 100) if liquida else 0}


def serie_12m(cur, fim):
    out = []
    y, m = fim.year, fim.month
    meses = []
    for _ in range(12):
        meses.append((y, m))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    for (yy, mm) in reversed(meses):
        d0 = date(yy, mm, 1)
        d1 = date(yy + (mm == 12), (mm % 12) + 1, 1)  # 1º do mês seguinte
        r = resultado(cur, d0.isoformat(), (d1).isoformat())
        out.append((f"{yy}-{mm:02d}", r["liquida"]))
    return out


def top(cur, campo, label_join, d0, d1, n=10):
    sql = f"""
      SELECT {campo} k, {label_join} rotulo,
             {VB} venda,
             (1 - sum(vlcustofin) FILTER (WHERE codoper='S')
                / nullif({VB},0)) * 100 margem
      FROM faturamento_vendas fv
      WHERE dtsaida BETWEEN %s AND %s
      GROUP BY {campo} ORDER BY venda DESC NULLS LAST LIMIT {n}"""
    cur.execute(sql, (d0, d1))
    return cur.fetchall()


def brl(v):
    return f"R$ {v:,.0f}"


if __name__ == "__main__":
    fim = date(2026, 7, 24)
    mes0, mes1 = date(2026, 7, 1), date(2026, 7, 31)
    ano_ant0, ano_ant1 = date(2025, 7, 1), date(2025, 7, 31)
    ytd0 = date(2026, 1, 1)

    with db.conn() as c:
        cur = c.cursor()
        print("========== DASHBOARD EXECUTIVO (lido do joga_demo via SQL) ==========\n")
        rm = resultado(cur, mes0.isoformat(), mes1.isoformat())
        ra = resultado(cur, ano_ant0.isoformat(), ano_ant1.isoformat())
        ry = resultado(cur, ytd0.isoformat(), fim.isoformat())
        yoy = (rm["liquida"] / ra["liquida"] - 1) * 100 if ra["liquida"] else 0

        print(f"MÊS ATUAL (jul/26):   receita líq {brl(rm['liquida'])}  |  lucro {brl(rm['lucro'])}"
              f"  |  margem {rm['margem']:.1f}%")
        print(f"MESMO MÊS 2025:       receita líq {brl(ra['liquida'])}   ->  YoY {yoy:+.1f}%")
        print(f"ACUMULADO 2026 (YTD): receita líq {brl(ry['liquida'])}  |  lucro {brl(ry['lucro'])}"
              f"  |  margem {ry['margem']:.1f}%")

        print("\n--- Série 12 meses (receita líquida) ---")
        for mes, v in serie_12m(cur, fim):
            barra = "█" * int(v / 120000)
            print(f"  {mes}  {brl(v):>14}  {barra}")

        print("\n--- Top 8 departamentos (12m) ---")
        for k, rot, venda, marg in top(cur, "codepto", "max(descricao)", ytd0.isoformat(), fim.isoformat(), 8):
            print(f"  depto {str(k):>5}  {brl(venda or 0):>14}  margem {float(marg or 0):.1f}%")

        print("\n--- Top 8 vendedores (12m) ---")
        for k, rot, venda, marg in top(cur, "fv.codusur", "'RCA '||fv.codusur", ytd0.isoformat(), fim.isoformat(), 8):
            print(f"  {str(rot):>10}  {brl(venda or 0):>14}")

        print("\n--- Top 8 clientes (12m) ---")
        for k, rot, venda, marg in top(cur, "codcli", "max(cliente)", ytd0.isoformat(), fim.isoformat(), 8):
            print(f"  {str(rot)[:28]:<28}  {brl(venda or 0):>14}")
