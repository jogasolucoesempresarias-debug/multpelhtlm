"""
Passo 3c — Gerador sintético: DIMENSÕES + CALENDARIO. Mundo coerente calibrado no perfil.
Nenhum registro real: nomes/códigos inventados; só as FORMAS vêm do molde.
Produtos e clientes carregam atributos "internos" (custo, margem, ST, perfil RFM, ABC)
consumidos pelo gerador do FATO (gerar_fato.py). Reprodutível (perfil.SEED).
"""
import io
import random
import sys
from datetime import timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import db          # noqa: E402
import perfil as P  # noqa: E402

rng = random.Random(P.SEED)

# ───────────────────────── catálogos ─────────────────────────
DEPTO_NOMES = [
    "MERCEARIA DOCE", "MERCEARIA SALGADA", "LIMPEZA", "HIGIENE/PERFUMARIA", "BEBIDAS",
    "BISCOITOS", "MATINAIS", "LATICINIOS", "CONSERVAS", "DIVERSOS", "DESCARTAVEIS",
    "PET", "BAZAR", "CONFEITARIA", "CEREAIS", "TEMPEROS", "CAFE", "OLEOS E AZEITES",
    "MASSAS", "ENLATADOS",
]
PRIMEIROS = ["ANA", "BRUNO", "CARLA", "DIEGO", "ELISA", "FABIO", "GISELE", "HUGO", "IARA",
             "JOAO", "KAREN", "LUCAS", "MARIA", "NELSON", "OLGA", "PAULO", "RITA", "SERGIO",
             "TANIA", "VITOR", "WAGNER", "YARA", "ANDRE", "BEATRIZ", "CESAR", "DANIELA"]
SOBRENOMES = ["SILVA", "SOUZA", "COSTA", "PEREIRA", "OLIVEIRA", "SANTOS", "LIMA", "GOMES",
              "RIBEIRO", "ALVES", "MARTINS", "ROCHA", "BARBOSA", "TEIXEIRA", "MOURA", "PINTO"]
RAZAO_TERMOS = ["COMERCIAL", "DISTRIBUIDORA", "MERCADO", "SUPERMERCADO", "ATACADO", "EMPORIO",
                "MINIMERCADO", "PADARIA", "CONVENIENCIA", "VAREJO", "ARMAZEM", "MERCEARIA"]
RAZAO_NOMES = ["PROGRESSO", "CENTRAL", "UNIAO", "BOA VISTA", "SANTA LUZIA", "HORIZONTE",
               "PRIMAVERA", "ALIANCA", "ESTRELA", "MODELO", "POPULAR", "IDEAL", "REAL",
               "NOVO TEMPO", "SAO JORGE", "BOM PRECO", "ECONOMIA", "FAMILIA", "VITORIA"]
MARCAS = ["NIVEA", "OMO", "YPE", "NESTLE", "SADIA", "COLA", "AMBEV", "BOMBRIL", "COLGATE",
          "3CORACOES", "ITALAC", "PIRACANJUBA", "HEINZ", "UNILEVER", "PG", "DOVE", "SEDA"]
CIDADES = {"ES": ["VITORIA", "VILA VELHA", "CACHOEIRO", "LINHARES", "COLATINA", "SERRA",
                  "GUARAPARI", "ARACRUZ", "SAO MATEUS"],
           "BA": ["SALVADOR", "ILHEUS", "ITABUNA", "PORTO SEGURO", "TEIXEIRA DE FREITAS"],
           "RJ": ["CAMPOS", "MACAE", "RIO DE JANEIRO", "NITEROI"]}


def _cnpj():
    r = f"{rng.randint(10,99)}{rng.randint(100,999)}{rng.randint(100,999)}"
    return f"{r[:2]}.{r[2:5]}.{r[5:8]}/0001-{rng.randint(10,99)}"


def pessoa():
    return f"{rng.choice(PRIMEIROS)} {rng.choice(SOBRENOMES)} {rng.choice(SOBRENOMES)}"


def empresa():
    return f"{rng.choice(RAZAO_NOMES)} {rng.choice(RAZAO_TERMOS)} LTDA"


# ───────────────────────── loader COPY ─────────────────────────
def _fmt(v):
    if v is None:
        return r"\N"
    if isinstance(v, bool):
        return "t" if v else "f"
    if isinstance(v, str):
        return v.replace("\\", "\\\\").replace("\t", " ").replace("\n", " ")
    return str(v)


def copy_load(cur, table, columns, rows):
    buf, n = io.StringIO(), 0
    for r in rows:
        buf.write("\t".join(_fmt(v) for v in r) + "\n")
        n += 1
    buf.seek(0)
    cur.copy_expert(f"COPY {table} ({', '.join(columns)}) FROM STDIN WITH (FORMAT text)", buf)
    return n


# ───────────────────────── dimensões ─────────────────────────
def gen_calendario():
    d = P.DATA_INICIO
    while d <= P.DATA_FIM:
        yield (d.isoformat(), d.year * 100 + d.month, d.year, d.month,
               "S" if d.weekday() < 6 else "N")
        d += timedelta(days=1)


def gen_deptos():
    out = []
    for i in range(P.N_DEPTOS):
        cod = [2, 3, 5, 8, 11, 12, 50][i] if i < 7 else 100 + i
        nome = DEPTO_NOMES[i] if i < len(DEPTO_NOMES) else f"DEPTO {cod}"
        out.append((cod, nome, round(rng.uniform(P.MARGEM_MIN, P.MARGEM_MAX), 4)))
    return out


def gen_secoes(deptos):
    return {200 + s: (rng.choice(deptos)[0], f"SECAO {200+s}") for s in range(P.N_SECOES)}


def gen_compradores():
    return [(40 + i, pessoa()) for i in range(P.N_COMPRADORES)]


def gen_supervisores():
    tipos = ["VENDA EXTERNA", "VENDA INTERNA", "SUPERVISOR LOJAS"]
    return [(i + 1, f"{rng.choice(['ES','BA','RJ'])} {rng.choice(PRIMEIROS)}", rng.choice(tipos))
            for i in range(P.N_SUPERVISORES)]


def gen_vendedores(supervisores):
    out = []
    for i in range(P.N_VENDEDORES):
        uf = "ES" if i < P.N_VENDEDORES * 0.75 else ("BA" if i % 2 else "RJ")
        # tipovend 'R' = Rota (externo), como no Winthor real e no filtro DEFAULT da tela Vendedores
        # (antes era '1', o que zerava a tela até trocar o filtro manualmente).
        out.append((100 + i, pessoa(), rng.choice(supervisores)[0], "R",
                    rng.choice(CIDADES[uf]), uf, "N"))
    return out


def gen_fornecedores(compradores):
    out = []
    for i in range(P.N_FORNECEDORES):
        uf = rng.choice(["ES", "ES", "ES", "SP", "MG", "RJ", "BA"])
        cidade = rng.choice(CIDADES.get(uf, ["SAO PAULO"]))
        out.append((100 + i, empresa().replace("LTDA", "IND LTDA"), rng.choice(RAZAO_NOMES),
                    rng.choice(compradores)[0], rng.randint(3, 30), rng.choice([500, 1000, 2000]),
                    _cnpj(), "ISENTO", str(rng.randint(1, 999)), "CENTRO",
                    f"{rng.randint(10000,99999)}-000", cidade, uf, "compras@forn.com"))
    return out


def gen_produtos(deptos, secoes, fornecedores):
    sec_ids = list(secoes.keys())
    depto_w = [3 if d[0] in (2, 3, 5, 50) else 1 for d in deptos]
    prods = []
    for i in range(P.N_PRODUTOS):
        cod = 40000 + i
        dp = rng.choices(deptos, weights=depto_w)[0]
        cand = [s for s in sec_ids if secoes[s][0] == dp[0]] or sec_ids
        forn = rng.choice(fornecedores)
        marca = rng.choice(MARCAS)
        prods.append({
            "codprod": cod, "descricao": f"PRODUTO {cod} {marca}", "codfornec": forn[0],
            "codcomprador": forn[3], "codepto": dp[0], "codsec": rng.choice(cand), "marca": marca,
            "codmarca": rng.randint(1, 40), "qtunitcx": rng.choice([6, 12, 20, 24, 30, 48]),
            "prazoval": rng.choice([90, 180, 365, 720]), "controla_val": rng.random() < 0.35,
            "volume": round(rng.uniform(0.2, 5), 4), "peso": round(rng.uniform(0.2, 8), 4),
            "custo": round(rng.lognormvariate(1.6, 0.7) + 0.5, 4),
            "margem": min(0.45, max(0.03, rng.gauss(dp[2], 0.04))),
            "st": rng.random() < P.ST_FRAC_PRODUTOS, "pop": rng.random(),
        })
    prods.sort(key=lambda p: p["pop"], reverse=True)   # ABC / Pareto
    n = len(prods)
    for idx, p in enumerate(prods):
        p["peso_venda"] = 5.0 if idx < n * 0.2 else (1.5 if idx < n * 0.5 else 0.4)
        p["abc"] = "A" if idx < n * 0.2 else ("B" if idx < n * 0.5 else "C")
    return prods


def prod_rows(prods):
    for p in prods:
        yield (p["codprod"], p["descricao"], f"CF{p['codprod']}", 0.0, p["codfornec"],
               p["codepto"], p["codsec"], "UN", p["qtunitcx"], f"{rng.randint(10**7, 10**8)}",
               p["marca"], p["codmarca"], p["prazoval"], "S" if p["controla_val"] else "N",
               p["volume"], round(rng.uniform(.05, .4), 4), round(rng.uniform(.05, .4), 4),
               # líquido = bruto menos a embalagem (~3%), como no cadastro real
               round(rng.uniform(.05, .4), 4), p["peso"], round(p["peso"] * 0.97, 4),
               "S", None)


def gen_clientes(vendedores):
    perfis = []
    for nome, cfg in P.CLIENTE_PERFIS.items():
        perfis += [nome] * int(P.N_CLIENTES * cfg["frac"])
    while len(perfis) < P.N_CLIENTES:
        perfis.append("leal")
    rng.shuffle(perfis)
    ufs = ["ES"] * 75 + ["BA"] * 16 + ["RJ"] * 9
    ven_por_uf = {}
    for v in vendedores:
        ven_por_uf.setdefault(v[5], []).append(v[0])
    clientes = []
    for i in range(P.N_CLIENTES):
        uf = rng.choice(ufs)
        clientes.append({
            "codcli": 100 + i, "cliente": empresa(), "uf": uf, "cidade": rng.choice(CIDADES[uf]),
            "codusur1": rng.choice(ven_por_uf.get(uf, [v[0] for v in vendedores])),
            "filial": "3" if uf == "ES" else "5", "perfil": perfis[i],
        })
    return clientes


def cli_rows(clientes):
    for c in clientes:
        yield (c["codcli"], c["cliente"], c["cliente"].split()[0], c["cidade"], c["cidade"],
               c["uf"], f"(2{rng.randint(1,8)}){rng.randint(90000,99999)}-{rng.randint(1000,9999)}",
               f"(2{rng.randint(1,8)}){rng.randint(3000,3999)}-{rng.randint(1000,9999)}",
               c["codusur1"], "N")


if __name__ == "__main__":
    print("gerando dimensões...")
    deptos = gen_deptos()
    secoes = gen_secoes(deptos)
    compradores = gen_compradores()
    supervisores = gen_supervisores()
    vendedores = gen_vendedores(supervisores)
    fornecedores = gen_fornecedores(compradores)
    produtos = gen_produtos(deptos, secoes, fornecedores)
    clientes = gen_clientes(vendedores)

    with db.conn() as c:
        cur = c.cursor()
        cur.execute("TRUNCATE calendario, pcempr, pcsuperv, pcusuari, pcfornec, pcprodut, "
                    "pcclient RESTART IDENTITY CASCADE")
        counts = {
            "calendario": copy_load(cur, "calendario", ["data", "anomes", "ano", "mes", "ehdiameta"], gen_calendario()),
            "pcempr": copy_load(cur, "pcempr", ["matricula", "nome"], compradores),
            "pcsuperv": copy_load(cur, "pcsuperv", ["codsupervisor", "nome", "tiposupervisor"], supervisores),
            "pcusuari": copy_load(cur, "pcusuari", ["codusur", "nome", "codsupervisor", "tipovend", "cidade", "estado", "bloqueio"], vendedores),
            "pcfornec": copy_load(cur, "pcfornec", ["codfornec", "fornecedor", "fantasia", "codcomprador", "prazoentrega", "vlminpedcompra", "cgc", "ie", "numeroend", "bairro", "cep", "cidade", "estado", "email"], fornecedores),
            "pcprodut": copy_load(cur, "pcprodut", ["codprod", "descricao", "codfab", "percipi", "codfornec", "codepto", "codsec", "embalagem", "qtunitcx", "classificfiscal", "marca", "codmarca", "prazoval", "controlavalidadedolote", "volume", "alturam3", "larguram3", "comprimentom3", "pesobruto", "pesoliq", "revenda", "obs2"], prod_rows(produtos)),
            "pcclient": copy_load(cur, "pcclient", ["codcli", "cliente", "fantasia", "municent", "municcob", "estent", "telcelent", "telent", "codusur1", "bloqueio"], cli_rows(clientes)),
        }
        c.commit()
    for k, v in counts.items():
        print(f"  {k:12s} {v:>8,}")
    print("dimensões OK.")
