"""
Passo 3d — Gerador do FATO (vendas + devolução + metas). Streaming COPY.
Reconstrói as dimensões deterministicamente (mesmo SEED) p/ usar os atributos internos
(custo, margem, ST, perfil RFM, peso ABC) e gera:
  - faturamento_vendas  (CODOPER S/SB/ST/SR; ICMS-ST na filial 5; bonificação)
  - faturamento_devolucao (~1,55% da venda, comercial)
  - pcpedc (metas: 1 pedido por nota, vlatend = venda líquida)
O comportamento por perfil de cliente faz emergir os 8 segmentos RFM e o cohort.
"""
import io
import math
import random
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import db          # noqa: E402
import perfil as P  # noqa: E402
import gerar as G   # noqa: E402

# ── comportamento por perfil (janela de atividade + frequência + ticket) ──
BEHAVIOR = {
    "campeao":    dict(start=0.00, end=1.00, freq=3.5, ticket=2.0),
    "leal":       dict(start=0.00, end=1.00, freq=2.0, ticket=1.3),
    "promissor":  dict(start=0.10, end=1.00, freq=1.2, ticket=1.0),
    "novo":       dict(start=0.85, end=1.00, freq=1.2, ticket=0.8),
    "atencao":    dict(start=0.00, end=0.92, freq=1.0, ticket=1.1),
    "em_risco":   dict(start=0.00, end=0.60, freq=1.0, ticket=1.4),
    "hibernando": dict(start=0.00, end=0.40, freq=0.8, ticket=0.9),
    "perdido":    dict(start=0.00, end=0.30, freq=0.7, ticket=0.7),
}


def poisson(lam, rng):
    if lam <= 0:
        return 0
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        k += 1
        p *= rng.random()
        if p <= L:
            return k - 1


def month_list(d0, d1):
    out, y, m = [], d0.year, d0.month
    while (y, m) <= (d1.year, d1.month):
        out.append((y, m))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def dia_do_mes(y, m, rng):
    import calendar
    last = calendar.monthrange(y, m)[1]
    if (y, m) == (P.DATA_FIM.year, P.DATA_FIM.month):
        last = min(last, P.DATA_FIM.day)
    return date(y, m, rng.randint(1, last))


def copy_stream(cur, table, columns, row_iter, chunk=200_000):
    sql = f"COPY {table} ({', '.join(columns)}) FROM STDIN WITH (FORMAT text)"
    buf, n, tot = io.StringIO(), 0, 0
    for r in row_iter:
        buf.write("\t".join(G._fmt(v) for v in r) + "\n")
        n += 1
        tot += 1
        if n >= chunk:
            buf.seek(0)
            cur.copy_expert(sql, buf)
            buf, n = io.StringIO(), 0
    if n:
        buf.seek(0)
        cur.copy_expert(sql, buf)
    return tot


# ── reconstrói o mundo (mesmo SEED → mesmos códigos já carregados) ──
G.rng = random.Random(P.SEED)
deptos = G.gen_deptos()
secoes = G.gen_secoes(deptos)
compradores = G.gen_compradores()
supervisores = G.gen_supervisores()
vendedores = G.gen_vendedores(supervisores)
fornecedores = G.gen_fornecedores(compradores)
produtos = G.gen_produtos(deptos, secoes, fornecedores)
clientes = G.gen_clientes(vendedores)

rng = G.rng
sup_de = {v[0]: v[2] for v in vendedores}
forn_nome = {f[0]: f[1] for f in fornecedores}
prod_pesos = [p["peso_venda"] for p in produtos]
MESES = month_list(P.DATA_INICIO, P.DATA_FIM)
NM = len(MESES)

VCOLS = ["numtransvenda", "numnota", "dtsaida", "codcli", "cliente", "uf", "codusur",
         "codsupervisor", "codprod", "descricao", "codepto", "codsec", "codmarca",
         "codfornecprinc", "fornecprinc", "codfornec", "codcomprador", "codfilial",
         "codoper", "bonific", "dtcancel", "qt", "vlvenda", "icmsretido", "vlfecp",
         "vlcustofin", "vlcustofinbonif"]

_estado = {"ntv": 15_000_000, "nnota": 1_000_000, "total_vb": 0.0}
pcpedc_rows = []


def _linha_venda(nota, dt, cli, prod, qt, coper):
    """Monta uma tupla de faturamento_vendas + acumula venda bruta."""
    custo_unit = prod["custo"]
    punit = round(custo_unit * (1 + prod["margem"]) * rng.uniform(0.96, 1.06), 4)
    filial = cli["filial"]
    if coper == "SB":                      # bonificação: sem receita, só custo
        vlvenda = 0.0
        vlcustofin = 0.0
        vlcustofinbonif = round(qt * custo_unit, 2)
        icms = fecp = 0.0
        bonific = "S"
    else:
        vlvenda = round(qt * punit, 2)
        vlcustofin = round(qt * custo_unit, 2)
        vlcustofinbonif = 0.0
        bonific = "N"
        # ICMS-ST + FECP só na filial com ST e produto sujeito
        if P.FILIAIS[filial]["st"] and prod["st"]:
            icms = round(vlvenda * P.ST_ALIQUOTA, 2)
            fecp = round(vlvenda * 0.002, 2)
        else:
            icms = fecp = 0.0
        if coper == "S":
            _estado["total_vb"] += vlvenda - icms - fecp   # = VENDA BRUTA da nossa fórmula
    return (nota, _estado["nnota"], dt.isoformat(), cli["codcli"], cli["cliente"], cli["uf"],
            cli["codusur1"], sup_de.get(cli["codusur1"]), prod["codprod"], prod["descricao"],
            prod["codepto"], prod["codsec"], prod["codmarca"], prod["codfornec"],
            forn_nome.get(prod["codfornec"]), prod["codfornec"], prod["codcomprador"], filial,
            coper, bonific, None, qt, vlvenda, icms, fecp, vlcustofin, vlcustofinbonif)


def gen_vendas():
    for cli in clientes:
        beh = BEHAVIOR[cli["perfil"]]
        m0, m1 = int(beh["start"] * NM), max(1, int(beh["end"] * NM))
        for mi in range(m0, m1):
            y, m = MESES[mi]
            seas = P.SAZONALIDADE[m - 1] * 12
            for _ in range(poisson(beh["freq"] * seas, rng)):
                _estado["ntv"] += 1
                _estado["nnota"] += 1
                nota = _estado["ntv"]
                dt = dia_do_mes(y, m, rng)
                r = rng.random()
                coper = "SR" if r < 0.0057 else ("ST" if r < 0.0687 else "S")
                nlin = max(1, poisson(4.35, rng) + 1)
                vb_nota = 0.0
                for _l in range(nlin):
                    prod = rng.choices(produtos, weights=prod_pesos)[0]
                    qt = max(1, int(rng.choice([1, 2, 3, 6, 12]) * beh["ticket"] * rng.uniform(0.6, 1.6)))
                    linha = _linha_venda(nota, dt, cli, prod, qt, coper)
                    if coper == "S":
                        vb_nota += linha[22] - linha[23] - linha[24]
                    yield linha
                if coper == "S" and rng.random() < 0.025:      # bonificação ocasional
                    prod = rng.choices(produtos, weights=prod_pesos)[0]
                    yield _linha_venda(nota, dt, cli, prod, rng.choice([1, 2, 3]), "SB")
                if coper == "S":
                    pcpedc_rows.append((nota, dt.isoformat(), cli["codusur1"], cli["codcli"],
                                        "F", round(vb_nota, 2), None))


def gen_devolucoes():
    alvo = P.DEVOL_FRAC_VENDA * _estado["total_vb"]
    acum, nd = 0.0, 0
    ndev = 20_000_000
    while acum < alvo and nd < 60_000:
        cli = rng.choice(clientes)
        prod = rng.choices(produtos, weights=prod_pesos)[0]
        y, m = MESES[rng.randint(0, NM - 1)]
        dt = dia_do_mes(y, m, rng)
        qt = rng.choice([1, 2, 3, 4])
        val = round(qt * prod["custo"] * (1 + prod["margem"]), 2)
        custo = round(qt * prod["custo"], 2)
        acum += val
        nd += 1
        ndev += 1
        yield (dt.isoformat(), cli["codcli"], cli["codusur1"], sup_de.get(cli["codusur1"]),
               prod["codepto"], "DEVOLUCAO", prod["codsec"], "DEVOLUCAO", prod["codprod"],
               prod["codcomprador"], 1, rng.choice(P.DEVOL_CODDEVOL_COMERCIAIS),
               cli["filial"], qt, val, custo)


DCOLS = ["dtent", "codcli", "codusur", "codsupervisor", "codepto", "departamento", "codsec",
         "secao", "codprod", "codcomprador", "codativ", "coddevol", "codfilial", "qt",
         "vldevolucao", "vlcustofin"]
PCOLS = ["numped", "data", "codusur", "codcli", "posicao", "vlatend", "vlcustofin"]


if __name__ == "__main__":
    with db.conn() as c:
        cur = c.cursor()
        cur.execute("TRUNCATE faturamento_vendas, faturamento_devolucao, pcpedc RESTART IDENTITY")
        print("gerando faturamento_vendas (streaming)...")
        nv = copy_stream(cur, "faturamento_vendas", VCOLS, gen_vendas())
        c.commit()
        print(f"  vendas: {nv:,} linhas | venda bruta acumulada R$ {_estado['total_vb']:,.2f}")
        np_ = copy_stream(cur, "pcpedc", PCOLS, iter(pcpedc_rows))
        c.commit()
        print(f"  pcpedc (metas): {np_:,} pedidos")
        nd = copy_stream(cur, "faturamento_devolucao", DCOLS, gen_devolucoes())
        c.commit()
        print(f"  devolucoes: {nd:,} linhas")
    print("fato OK.")
