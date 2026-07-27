"""
Passo 3a — Extrair o MOLDE estatístico da base real (SÓ LEITURA, zero registro copiado).
Colhe distribuições p/ calibrar o gerador: contagens, sazonalidade, mix de operação,
curva de margem por depto, distribuição por UF, taxa de devolução. Base: ano 2025 (RCA).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from estoque import pbi  # noqa: E402

FV = "FATURAMENTO_VENDAS"
Y = f"{FV}[DTSAIDA] >= DATE(2025,1,1), {FV}[DTSAIDA] <= DATE(2025,12,31)"
YS = Y + f', {FV}[CODOPER] = "S"'

Q_COUNTS = f"""EVALUATE ROW(
    "clientes",     CALCULATE(DISTINCTCOUNT({FV}[CODCLI]), {Y}),
    "produtos",     CALCULATE(DISTINCTCOUNT({FV}[CODPROD]), {Y}),
    "vendedores",   CALCULATE(DISTINCTCOUNT({FV}[CODUSUR]), {Y}),
    "supervisores", CALCULATE(DISTINCTCOUNT({FV}[CODSUPERVISOR]), {Y}),
    "fornecedores", CALCULATE(DISTINCTCOUNT({FV}[CODFORNEC]), {Y}),
    "compradores",  CALCULATE(DISTINCTCOUNT({FV}[CODCOMPRADOR]), {Y}),
    "deptos",       CALCULATE(DISTINCTCOUNT({FV}[CODEPTO]), {Y}),
    "secoes",       CALCULATE(DISTINCTCOUNT({FV}[CODSEC]), {Y}),
    "filiais",      CALCULATE(DISTINCTCOUNT({FV}[CODFILIAL]), {Y}),
    "notas",        CALCULATE(DISTINCTCOUNT({FV}[NUMTRANSVENDA]), {Y}),
    "linhas",       CALCULATE(COUNTROWS({FV}), {Y})
)"""

Q_COPER = f"""EVALUATE
SUMMARIZECOLUMNS({FV}[CODOPER], FILTER({FV}, {FV}[DTSAIDA] >= DATE(2025,1,1) && {FV}[DTSAIDA] <= DATE(2025,12,31)),
    "vlvenda", SUM({FV}[VLVENDA]), "linhas", COUNTROWS({FV}))"""

Q_MES = f"""EVALUATE
GROUPBY(
    ADDCOLUMNS(FILTER({FV}, {FV}[DTSAIDA] >= DATE(2025,1,1) && {FV}[DTSAIDA] <= DATE(2025,12,31) && {FV}[CODOPER] = "S"),
        "M", MONTH({FV}[DTSAIDA])),
    [M], "venda", SUMX(CURRENTGROUP(), {FV}[VLVENDA]))"""

Q_DEPTO = f"""EVALUATE
SUMMARIZECOLUMNS({FV}[CODEPTO], FILTER({FV}, {FV}[DTSAIDA] >= DATE(2025,1,1) && {FV}[DTSAIDA] <= DATE(2025,12,31) && {FV}[CODOPER] = "S"),
    "venda", SUM({FV}[VLVENDA]), "custo", SUM({FV}[VLCUSTOFIN]))"""

Q_UF = f"""EVALUATE
SUMMARIZECOLUMNS({FV}[UF], FILTER({FV}, {FV}[DTSAIDA] >= DATE(2025,1,1) && {FV}[DTSAIDA] <= DATE(2025,12,31) && {FV}[CODOPER] = "S"),
    "venda", SUM({FV}[VLVENDA]), "clientes", DISTINCTCOUNT({FV}[CODCLI]))"""


def linha(query):
    return pbi.run_dax_rca(query)


def num(v):
    return f"{v:,.2f}" if isinstance(v, float) else (f"{v:,}" if isinstance(v, int) else str(v))


if __name__ == "__main__":
    print("===== CONTAGENS 2025 =====")
    for k, v in linha(Q_COUNTS)[0].items():
        print(f"  {k:14s} {num(v):>14}")
    c = linha(Q_COUNTS)[0]
    print(f"  linhas/nota   {c['linhas']/c['notas']:>14.2f}")

    print("\n===== MIX DE OPERAÇÃO (CODOPER) =====")
    tot = sum((r.get('vlvenda') or 0) for r in linha(Q_COPER))
    for r in sorted(linha(Q_COPER), key=lambda x: -(x.get('vlvenda') or 0)):
        v = r.get('vlvenda') or 0
        print(f"  {str(r['CODOPER']):>4}  venda={num(v):>16}  linhas={num(r['linhas']):>10}  ({v/tot*100:5.2f}%)")

    print("\n===== SAZONALIDADE (venda S por mês) =====")
    mes = sorted(linha(Q_MES), key=lambda x: x['M'])
    vt = sum(r['venda'] for r in mes)
    for r in mes:
        print(f"  mês {r['M']:>2}  {num(r['venda']):>16}  ({r['venda']/vt*100:5.2f}%)")

    print("\n===== MARGEM POR DEPTO (top 12 por venda) =====")
    dep = sorted(linha(Q_DEPTO), key=lambda x: -(x.get('venda') or 0))[:12]
    for r in dep:
        v, cu = r.get('venda') or 0, r.get('custo') or 0
        marg = (v - cu) / v * 100 if v else 0
        print(f"  depto {str(r['CODEPTO']):>4}  venda={num(v):>15}  margem={marg:5.2f}%")

    print("\n===== DISTRIBUIÇÃO POR UF =====")
    uf = sorted(linha(Q_UF), key=lambda x: -(x.get('venda') or 0))
    vtu = sum((r.get('venda') or 0) for r in uf)
    for r in uf:
        v = r.get('venda') or 0
        print(f"  {str(r['UF']):>4}  venda={num(v):>16}  clientes={num(r['clientes']):>8}  ({v/vtu*100:5.2f}%)")
