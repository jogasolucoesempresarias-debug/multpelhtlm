"""
Passo 3f — Gerador das tabelas de COMPRAS/ESTOQUE. Coerente com as vendas já geradas.
- giro (QTVENDMES1..3) vem do FATO real (últimos 3 meses), por produto×filial (3 e 5);
- cobertura ganha spread (saudável/parado/risco/zerado) p/ as telas terem contraste;
- FEFO: lotes com validade espalhada (alguns vencendo); WMS: posições ocupadas/vazias;
- pedido de compra em aberto (desconta da sugestão); verbas (1801); vencidos (conta 200042).
Reprodutível (perfil.SEED, continua a sequência de gerar/gerar_fato).
"""
import io
import random
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import db          # noqa: E402
import perfil as P  # noqa: E402
import gerar as G   # noqa: E402

# reconstrói o mundo (mesmo SEED)
G.rng = random.Random(P.SEED)
deptos = G.gen_deptos()
secoes = G.gen_secoes(deptos)
compradores = G.gen_compradores()
supervisores = G.gen_supervisores()
vendedores = G.gen_vendedores(supervisores)
fornecedores = G.gen_fornecedores(compradores)
produtos = G.gen_produtos(deptos, secoes, fornecedores)
prod_by_forn = {}
for p in produtos:
    prod_by_forn.setdefault(p["codfornec"], []).append(p)
rng = G.rng
FILIAIS = ["3", "5"]
HOJE = P.DATA_FIM


def copy_stream(cur, table, columns, rows, chunk=100_000):
    sql = f"COPY {table} ({', '.join(columns)}) FROM STDIN WITH (FORMAT text)"
    buf, n, tot = io.StringIO(), 0, 0
    for r in rows:
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


def carregar_giro(cur):
    """{(codprod,filial): (m1,m2,m3,dtultsaida)} dos últimos 3 meses cheios.

    ⚠️ Os três meses saem de `perfil.meses_giro()`, não de datas cravadas. Estavam fixos em
    abr/mai/jun de 2026 — com a janela da base virando deslizante, aquilo passaria a somar
    quantidade de meses fora do período gerado, o giro sairia ZERO em massa e a demo abriria com
    quase todo o catálogo classificado como "sem giro"."""
    (a0, a1), (b0, b1), (c0, c1) = P.meses_giro()
    cur.execute("""
        SELECT codprod, codfilial,
          coalesce(sum(qt) FILTER (WHERE dtsaida >= %s AND dtsaida < %s),0),
          coalesce(sum(qt) FILTER (WHERE dtsaida >= %s AND dtsaida < %s),0),
          coalesce(sum(qt) FILTER (WHERE dtsaida >= %s AND dtsaida < %s),0),
          max(dtsaida)
        FROM faturamento_vendas
        WHERE codoper='S' AND codfilial IN ('3','5')
        GROUP BY codprod, codfilial""", (a0, a1, b0, b1, c0, c1))
    return {(r[0], r[1]): (float(r[2]), float(r[3]), float(r[4]), r[5]) for r in cur.fetchall()}


def situacao_do_produto(cod):
    """Sorteia a SITUAÇÃO de estoque do produto (mesma para as duas filiais).

    ⚠️ É por PRODUTO, não por linha produto×filial: um item "parado" na matriz e "saudável" no
    depósito não é dead stock nenhum, e o app somaria metade dele no capital parado. Determinístico
    pelo código para a base ser reprodutível com o mesmo SEED."""
    r = random.Random(P.SEED * 31 + cod).random()
    acc = 0.0
    for nome in ("sem_giro", "parado", "ruptura", "excesso"):
        acc += P.ESTOQUE_MIX[nome]
        if r < acc:
            return nome
    return "saudavel"


def _cobertura_qt(giro_mes, situacao):
    """Quantidade em estoque a partir do giro e da situação sorteada.

    ⚠️ A distribuição antiga colocava 55% dos itens entre 20 e 45 dias de cobertura — ou seja,
    ABAIXO do limiar do Estoque ideal (45). O painel gerencial da demo abria com 26% de cobertura
    ideal contra 59,7% do real, e o gráfico parecia uma empresa em colapso."""
    if giro_mes <= 0:
        # ⚠️ Dead stock guarda POUCO volume. Com `uniform(1,60)` o capital parado da demo saiu
        # 6,9% contra 2,9% do real — item parado vira o maior problema da tela e desequilibra a
        # leitura. Na base real o item parado carrega ~1/5 do valor médio por SKU.
        return round(rng.uniform(1, 22)) if situacao != "ruptura" else 0
    if situacao == "ruptura":
        return 0
    faixa = ("excesso" if situacao == "excesso"
             else "risco" if rng.random() < P.RISCO_FRAC else "saudavel")
    lo, hi = P.COBERTURA_FAIXAS[faixa]
    return max(1, round(giro_mes / 30 * rng.uniform(lo, hi)))


def gen_pcest(giro):
    for p in produtos:
        sit = situacao_do_produto(p["codprod"])
        for f in FILIAIS:
            m1, m2, m3, dtult = giro.get((p["codprod"], f), (0, 0, 0, None))
            # SEM GIRO e PARADO não vendem: o giro dos 3 meses vai a zero. Sem isto o item
            # continuava com `QTVENDMES` cheio e o app o classificava como saudável, por mais
            # antiga que fosse a última saída.
            if sit in ("sem_giro", "parado"):
                m1 = m2 = m3 = 0
            gmes = (m1 + m2 + m3) / 3
            qtger = _cobertura_qt(gmes, sit)
            qtreserv = round(qtger * rng.uniform(0, 0.05))
            qtbloq = round(qtger * rng.uniform(0, 0.03))
            # ⚠️ `dias_sem_venda` (e portanto o CAPITAL PARADO) sai daqui. Antes era sempre a
            # última venda REAL do fato — e como todo produto vendia até o fim do período, quase
            # nada passava dos 60 dias e o capital parado da demo era 0,1%.
            if sit == "parado":
                dtults = (HOJE - timedelta(days=rng.randint(*P.PARADO_DIAS))).isoformat()
            elif sit == "sem_giro":
                dtults = (HOJE - timedelta(days=rng.randint(35, 90))).isoformat()
            else:
                dtults = dtult.isoformat() if dtult else None
            dtulte = (HOJE - timedelta(days=rng.randint(1, 120))).isoformat()
            yield (p["codprod"], f, qtger, qtreserv, qtbloq, round(qtger * rng.uniform(0, 0.02)),
                   round(qtger * rng.uniform(0, 0.02)), m1, m2, m3, p["custo"], dtults, dtulte)


def gen_pcembalagem():
    for p in produtos:
        if rng.random() < 0.05:      # ~5% sem fator de caixa (itens_sem_fator_caixa)
            continue
        yield (p["codprod"], f"CX/{p['qtunitcx']:04d}/UN", p["qtunitcx"], p["volume"],
               round(rng.uniform(.1, .5), 4), round(rng.uniform(.1, .5), 4),
               round(rng.uniform(.1, .5), 4), p["peso"])


def gen_wms(giro):
    """pcendereco (posições) + pcestendereco (estoque endereçado c/ lote/validade)."""
    enderecos, estend = [], []
    cod_end = 1
    for f in FILIAIS:
        n_pos = 2000 if f == "3" else 600
        pool = []
        for _ in range(n_pos):
            rua = rng.choice(list(range(1, 41)) + [99])   # 99 = pulmão/virtual
            tipo = "AP" if rng.random() < 0.6 else "AE"
            ocup = rng.random() < 0.7
            enderecos.append((cod_end, f, rua, str(rng.randint(1, 20)), str(rng.randint(1, 5)),
                              str(rng.randint(1, 4)), tipo, "S", "N", "O" if ocup else "L"))
            if ocup and rua != 99:
                pool.append((cod_end, rua))
            cod_end += 1
        # distribui estoque endereçado nas posições ocupadas (rua<>99)
        prods_com_estoque = [p for p in produtos if giro.get((p["codprod"], f), (0, 0, 0, 0))[0] > 0]
        rng.shuffle(prods_com_estoque)
        for (ce, rua), p in zip(pool, prods_com_estoque):
            controla = p["controla_val"]
            # validade: perecível vence mais perto; alguns na janela de risco (60d)
            if controla:
                dias = rng.choice([rng.randint(10, 60), rng.randint(60, 200), rng.randint(200, 540)])
            else:
                dias = rng.randint(200, 900)
            dtval = (HOJE + timedelta(days=dias)).isoformat()
            estend.append((p["codprod"], ce, f"L{rng.randint(1000,9999)}", dtval,
                           round(rng.uniform(1, 200))))
    return enderecos, estend


def gen_compras():
    """pcpedido/pcitem (alguns em aberto = dtentradaestoque null) + pedido_entrada + lead time."""
    peds, itens, entradas = [], [], []
    nump = 500000
    for _ in range(P.COMPRAS_N_PEDIDOS):
        nump += 1
        forn = rng.choice(fornecedores)
        f = rng.choice(FILIAIS)
        dias_atras = rng.randint(1, 360)
        dtemis = HOJE - timedelta(days=dias_atras)
        aberto = dias_atras < 180 and rng.random() < 0.35
        prazo = forn[4]
        dtprev = dtemis + timedelta(days=prazo)
        prods = prod_by_forn.get(forn[0], [])
        if not prods:
            continue
        sel = rng.sample(prods, min(len(prods), rng.randint(*P.COMPRAS_ITENS_PED)))
        # Perfil tributário do FORNECEDOR (não do produto) — é assim na base real: medindo 155
        # fornecedores, 44 cobram IPI em todas as linhas, 78 em nenhuma e 33 são mistos; ST
        # aparece em ~7% deles. Determinístico pelo código p/ a base continuar reprodutível.
        perfil = forn[0] % 10
        ipi_forn = 0.0 if perfil < 5 else (15.0 if perfil < 8 else 6.5)
        st_forn = 20.0 if perfil == 9 else 0.0
        vtot = 0.0
        for p in sel:
            qtped = round(rng.uniform(*P.COMPRAS_QTD_ITEM))
            qtent = 0 if aberto else qtped
            # fornecedor "misto" (perfil 7): parte dos itens sai isenta
            ipi = 0.0 if (perfil == 7 and p["codprod"] % 3 == 0) else ipi_forn
            vl_ipi = round(p["custo"] * ipi / 100, 6)
            vl_st = round(p["custo"] * st_forn / 100, 6)
            # VLTOTAL do Winthor é a NF CHEIA (mercadoria + IPI + ST) — a demo precisa espelhar
            # isso, senão o Orçamento dela mede numa régua e a sugestão em outra (o bug real).
            vtot += qtped * (p["custo"] + vl_ipi + vl_st)
            itens.append((nump, p["codprod"], qtped, qtent, ipi, vl_ipi, st_forn, vl_st))
        dtent = None if aberto else (dtemis + timedelta(days=rng.randint(2, prazo + 10)))
        peds.append((nump, dtemis.isoformat(), f, forn[0], forn[3], round(vtot, 2),
                     0.0 if aberto else round(vtot, 2), (dtemis + timedelta(days=30)).isoformat(),
                     dtent.isoformat() if dtent else None, dtprev.isoformat()))
        if dtent:
            entradas.append((nump, dtent.isoformat()))
    return peds, itens, entradas


def gen_verbas():
    verbas, aplic = [], []
    nv = 700000
    for _ in range(400):
        nv += 1
        forn = rng.choice(fornecedores)
        conta = rng.choice([250009, 250008, 200013])
        valor = round(rng.uniform(500, 30000), 2)
        dtemis = HOJE - timedelta(days=rng.randint(1, 500))
        cancel = (dtemis + timedelta(days=10)).isoformat() if rng.random() < 0.08 else None
        verbas.append((nv, rng.choice(FILIAIS), forn[0], forn[3], valor,
                       "REBAIXA" if conta == 250009 else "CC", "DESCONTO",
                       f"CAMPANHA {rng.randint(1,99)}", conta, dtemis.isoformat(),
                       (dtemis + timedelta(days=60)).isoformat(), cancel))
        if not cancel and rng.random() < 0.6:
            aplic.append((nv, round(valor * rng.uniform(0.3, 1.0), 2),
                          (dtemis + timedelta(days=rng.randint(5, 50))).isoformat(),
                          None))
    return verbas, aplic


def gen_vencidos():
    """pcmov (perda validade, conta 200042) + pcnfsaid (join por NUMTRANSVENDA)."""
    mov, nfs = [], []
    ntv = 30000000
    perec = [p for p in produtos if p["controla_val"]]
    for _ in range(600):
        ntv += 1
        p = rng.choice(perec)
        qt = round(rng.uniform(1, 30))
        dt = HOJE - timedelta(days=rng.randint(1, 400))
        mov.append((ntv, rng.randint(1000, 99999), p["codprod"], rng.choice(FILIAIS), qt,
                    round(p["custo"], 4)))
        nfs.append((ntv, dt.isoformat()))
    return mov, nfs


def gen_devol_avulsa():
    for _ in range(150):
        p = rng.choice(produtos)
        f = rng.choice(FILIAIS)
        uf = "ES" if f == "3" else "BA"
        dt = HOJE - timedelta(days=rng.randint(1, 700))
        qt = rng.choice([1, 2, 3, 5])
        val = round(qt * p["custo"] * (1 + p["margem"]), 2)
        yield (dt.isoformat(), rng.randint(100, 7000), uf, None, None, p["codprod"], f, qt, val,
               round(qt * p["custo"], 2))


if __name__ == "__main__":
    with db.conn() as c:
        cur = c.cursor()
        cur.execute("""TRUNCATE pcest, pcembalagem, pcendereco, pcestendereco, pcpedido, pcitem,
                       pedido_entrada, pcverba, pcaplicverba, pcmov, pcnfsaid,
                       faturamento_devolucao_avulsa RESTART IDENTITY""")
        giro = carregar_giro(cur)
        print(f"giro carregado: {len(giro):,} pares produto×filial")

        cnt = {}
        cnt["pcest"] = copy_stream(cur, "pcest",
            ["codprod", "codfilial", "qtestger", "qtreserv", "qtbloqueada", "qtpendente",
             "qttransito", "qtvendmes1", "qtvendmes2", "qtvendmes3", "custofin", "dtultsaida", "dtultent"],
            gen_pcest(giro))
        cnt["pcembalagem"] = copy_stream(cur, "pcembalagem",
            ["codprod", "embalagem", "qtunit", "volume", "altura", "largura", "comprimento", "pesobruto"],
            gen_pcembalagem())

        enderecos, estend = gen_wms(giro)
        cnt["pcendereco"] = copy_stream(cur, "pcendereco",
            ["codendereco", "codfilial", "rua", "predio", "nivel", "apto", "tipoender", "ativo", "bloqueio", "situacao"],
            enderecos)
        cnt["pcestendereco"] = copy_stream(cur, "pcestendereco",
            ["codprod", "codendereco", "numlote", "dtval", "qt"], estend)

        peds, itens, entradas = gen_compras()
        cnt["pcpedido"] = copy_stream(cur, "pcpedido",
            ["numped", "dtemissao", "codfilial", "codfornec", "codcomprador", "vltotal",
             "vlentregue", "dtvenc", "dtentradaestoque", "dtprevent"], peds)
        cnt["pcitem"] = copy_stream(cur, "pcitem", ["numped", "codprod", "qtpedida", "qtentregue",
                                                   "periipi", "vlipi", "percst", "vlst"], itens)
        cnt["pedido_entrada"] = copy_stream(cur, "pedido_entrada", ["numped", "dtentrada"], entradas)

        verbas, aplic = gen_verbas()
        cnt["pcverba"] = copy_stream(cur, "pcverba",
            ["numverba", "codfilial", "codfornec", "codcomprador", "valor", "tipo", "formapgto",
             "referencia", "codconta", "dtemissao", "dtvenc", "dtcancel"], verbas)
        cnt["pcaplicverba"] = copy_stream(cur, "pcaplicverba",
            ["numverba", "vlaplic", "dtaplic", "dtestorno"], aplic)

        mov, nfs = gen_vencidos()
        cnt["pcmov"] = copy_stream(cur, "pcmov",
            ["numtransvenda", "numnota", "codprod", "codfilial", "qt", "punit"], mov)
        cnt["pcnfsaid"] = copy_stream(cur, "pcnfsaid", ["numtransvenda", "dtsaida"], nfs)

        cnt["faturamento_devolucao_avulsa"] = copy_stream(cur, "faturamento_devolucao_avulsa",
            ["dtent", "codcli", "uf", "codusur", "codsupervisor", "codprod", "codfilial", "qt",
             "vldevolucao", "vlcusto"], gen_devol_avulsa())
        c.commit()

    for k, v in cnt.items():
        print(f"  {k:26s} {v:>8,}")
    print("estoque/compras OK.")
