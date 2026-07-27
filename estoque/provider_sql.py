"""
Provider SQL do módulo Compras (/estoque) — modo DATA_SOURCE=postgres.

Espelha os builders DAX de `estoque/queries.py` lendo o Postgres analítico (`joga_demo`), devolvendo as
MESMAS chaves que o `estoque/core.py` consome. Assim o `core.py` roda intacto (a matemática de cobertura/
sugestão/ruptura é agnóstica de fonte).

⚠️ CONTRATO loader→core (onde mora o bug silencioso): o caminho DAX passa por `clean_rows`, que encurta
`PCEST[QTBLOQUEADA]→qtbloq`, `PCEST[QTVENDMES1]→giro_m1`, etc. Aqui os SELECTs reproduzem EXATAMENTE esses
apelidos — se um nome divergir, o core lê None e a tela zera em silêncio. Datas saem como ISO string e
numéricos como float, espelhando o `clean_rows` (o `core._parse_dt`/`_n` esperam esse formato).

Conexão reusa `provider_sql.analytics_conn()` da raiz (import defensivo p/ teste isolado do subpacote).
"""
import sys
from pathlib import Path

try:
    from provider_sql import analytics_conn, VB, CT, DEV, CDEV
except ImportError:  # subpacote testado isolado: põe a raiz do app no path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from provider_sql import analytics_conn, VB, CT, DEV, CDEV


def _fil(filiais):
    """Fragmento ' AND codfilial IN (...)' (CHAR). Vazio/None → '' (sem recorte de filial)."""
    if not filiais:
        return ""
    itens = ",".join("'" + str(f).strip() + "'" for f in filiais if str(f).strip())
    return f" AND codfilial IN ({itens})" if itens else ""


def _iso(d):
    return d.isoformat() if d is not None else None


def _f(v):
    return float(v) if v is not None else None


# ───────────────────────── snapshot PCEST (giro/gerencial/custo/datas) ─────────────────────────
def snapshot_estoque(filiais=None):
    """Espelha q_snapshot_estoque: PCEST agregado por CODPROD. Aliases IDÊNTICOS aos do clean_rows
    (qtbloqueada→qtbloq, qtpendente→qtpend, qtvendmes1..3→giro_m1..3)."""
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"""
            SELECT codprod,
                   sum(qtestger), sum(qtreserv), sum(qtbloqueada), sum(qtpendente), sum(qttransito),
                   max(custofin), sum(qtvendmes1), sum(qtvendmes2), sum(qtvendmes3),
                   max(dtultsaida), max(dtultent)
            FROM pcest WHERE 1=1{_fil(filiais)}
            GROUP BY codprod
            HAVING sum(qtestger)<>0 OR sum(qtvendmes1)<>0 OR sum(qtvendmes2)<>0 OR sum(qtvendmes3)<>0
        """)
        return [{"CODPROD": cod, "qtestger": _f(qeg), "qtreserv": _f(qr), "qtbloq": _f(qb),
                 "qtpend": _f(qp), "qttransito": _f(qt), "custofin": _f(cf),
                 "giro_m1": _f(g1), "giro_m2": _f(g2), "giro_m3": _f(g3),
                 "dtultsaida": _iso(dts), "dtultent": _iso(dte)}
                for (cod, qeg, qr, qb, qp, qt, cf, g1, g2, g3, dts, dte) in cur.fetchall()]


# ───────────────────────── estoque endereçado (QTDISP oficial) ─────────────────────────
def estoque_endereco(filiais=None):
    """Espelha q_estoque_endereco: SUM(PCESTENDERECO[QT]) por CODPROD, RUA<>99, filiais."""
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"""
            SELECT e.codprod, sum(e.qt)
            FROM pcestendereco e JOIN pcendereco d ON d.codendereco = e.codendereco
            WHERE d.rua <> 99{_fil_alias(filiais, 'd')}
            GROUP BY e.codprod HAVING sum(e.qt) <> 0
        """)
        return [{"CODPROD": cod, "qt_end": _f(qt)} for cod, qt in cur.fetchall()]


def _fil_alias(filiais, alias):
    """Como _fil, mas qualificando a coluna com um alias de tabela (ex.: 'd.codfilial')."""
    frag = _fil(filiais)
    return frag.replace(" codfilial IN", f" {alias}.codfilial IN") if frag else ""


# ───────────────────────── cadastro produto / fornecedor / comprador ─────────────────────────
def cadastro_produto():
    """Espelha q_cadastro_produto (REVENDA='S' AND OBS2<>'FL'). Dict {CODPROD: row}. NCM=classificfiscal,
    CTRL_VALIDADE=controlavalidadedolote."""
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute("""
            SELECT codprod, descricao, codfab, percipi, codfornec, codepto, codsec, embalagem,
                   qtunitcx, classificfiscal, marca, prazoval, controlavalidadedolote, volume,
                   alturam3, larguram3, comprimentom3, pesobruto
            FROM pcprodut WHERE revenda = 'S' AND coalesce(obs2, '') <> 'FL'
        """)
        out = {}
        for r in cur.fetchall():
            out[int(r[0])] = {
                "CODPROD": r[0], "DESCRICAO": r[1], "CODFAB": r[2], "PERCIPI": _f(r[3]),
                "CODFORNEC": r[4], "CODEPTO": r[5], "CODSEC": r[6], "EMBALAGEM": r[7],
                "QTUNITCX": _f(r[8]), "NCM": r[9], "MARCA": r[10], "PRAZOVAL": r[11],
                "CTRL_VALIDADE": r[12], "VOLUME": _f(r[13]), "ALTURAM3": _f(r[14]),
                "LARGURAM3": _f(r[15]), "COMPRIMENTOM3": _f(r[16]), "PESOBRUTO": _f(r[17]),
            }
        return out


def cadastro_fornecedor():
    """Espelha q_cadastro_fornecedor. Dict {CODFORNEC: row}."""
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute("""
            SELECT codfornec, fornecedor, fantasia, codcomprador, prazoentrega, vlminpedcompra,
                   cgc, ie, numeroend, bairro, cep, cidade, estado, email
            FROM pcfornec
        """)
        out = {}
        for r in cur.fetchall():
            out[int(r[0])] = {
                "CODFORNEC": r[0], "FORNECEDOR": r[1], "FANTASIA": r[2], "CODCOMPRADOR": r[3],
                "PRAZOENTREGA": r[4], "VLMINPEDCOMPRA": _f(r[5]), "CGC": r[6], "IE": r[7],
                "NUMEROEND": r[8], "BAIRRO": r[9], "CEP": r[10], "CIDADE": r[11],
                "ESTADO": r[12], "EMAIL": r[13],
            }
        return out


def compradores():
    """Espelha q_compradores_estoque: [{MATRICULA, NOME}] do PCEMPR."""
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute("SELECT matricula, nome FROM pcempr")
        return [{"MATRICULA": m, "NOME": n} for m, n in cur.fetchall()]


# ───────────────────────── embalagem / cubagem (PCEMBALAGEM) ─────────────────────────
def embalagem():
    """Espelha q_embalagem: por CODPROD, caixa (MAX qtunit) + a embalagem de maior qtunit + cubagem/peso."""
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute("""
            SELECT codprod, max(qtunit), (array_agg(embalagem ORDER BY qtunit DESC NULLS LAST))[1],
                   max(volume), max(altura), max(largura), max(comprimento), max(pesobruto)
            FROM pcembalagem GROUP BY codprod
        """)
        return [{"CODPROD": cod, "qtunit": _f(qu), "embalagem": emb, "volume": _f(vol),
                 "altura": _f(al), "largura": _f(la), "comprimento": _f(co), "pesobruto": _f(pb)}
                for (cod, qu, emb, vol, al, la, co, pb) in cur.fetchall()]


# ───────────────────────── pedido de compra real (PCPEDIDO / PCITEM) ─────────────────────────
def pedido_cab(desde, filiais=None):
    """Espelha q_pedido_cab: cabeçalho dos pedidos emitidos a partir de `desde` (date)."""
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"""
            SELECT numped, dtemissao, codfilial, codfornec, codcomprador, vltotal, vlentregue,
                   dtvenc, dtentradaestoque, dtprevent
            FROM pcpedido WHERE dtemissao >= %s{_fil(filiais)}
        """, (desde,))
        return [{"NUMPED": np, "DTEMISSAO": _iso(dte), "CODFILIAL": cfil, "CODFORNEC": cfor,
                 "CODCOMPRADOR": ccomp, "VLTOTAL": _f(vt), "VLENTREGUE": _f(ve),
                 "DTVENC": _iso(dv), "DTENTRADAESTOQUE": _iso(dee), "DTPREVENT": _iso(dp)}
                for (np, dte, cfil, cfor, ccomp, vt, ve, dv, dee, dp) in cur.fetchall()]


_TRIB_COLS = ("periipi", "vlipi", "percst", "vlst")


def _pcitem_tem_tributacao(cur):
    """`pcitem` do joga_demo só ganhou as colunas de IPI/ST na v4 — uma base semeada antes
    não as tem. Sem este probe, o SELECT quebraria e derrubaria TAMBÉM o já-pedido (que vem
    da mesma query). Ausente → devolve sem tributação e o core cai no fallback do cadastro."""
    cur.execute("""SELECT count(*) FROM information_schema.columns
                   WHERE table_name = 'pcitem' AND column_name = ANY(%s)""", (list(_TRIB_COLS),))
    return cur.fetchone()[0] == len(_TRIB_COLS)


def pedido_itens(numped_min):
    """Espelha q_pedido_itens: itens dos pedidos com NUMPED >= numped_min, agregados por
    (NUMPED, CODPROD) — inclusive a tributação praticada na linha (MAX, atributo não somável)."""
    with analytics_conn() as c:
        cur = c.cursor()
        trib = _pcitem_tem_tributacao(cur)
        extra = ", max(periipi), max(vlipi), max(percst), max(vlst)" if trib else ""
        cur.execute(f"""
            SELECT numped, codprod, sum(qtpedida), sum(qtentregue){extra}
            FROM pcitem WHERE numped >= %s GROUP BY numped, codprod
        """, (int(numped_min),))
        out = []
        for row in cur.fetchall():
            np, cod, qp, qe = row[:4]
            d = {"NUMPED": np, "CODPROD": cod, "qtped": _f(qp), "qtentregue": _f(qe)}
            if trib:
                d.update(dict(zip(_TRIB_COLS, (_f(v) for v in row[4:8]))))
            out.append(d)
        return out


def pedido_itens_um(numped):
    """Espelha q_pedido_itens_um: itens de UM pedido (drill 'ver itens comprados' do Orçamento)."""
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute("""SELECT numped, codprod, sum(qtpedida), sum(qtentregue)
            FROM pcitem WHERE numped = %s GROUP BY numped, codprod""", (int(numped),))
        return [{"NUMPED": np, "CODPROD": cod, "qtped": _f(qp), "qtentregue": _f(qe)}
                for (np, cod, qp, qe) in cur.fetchall()]


# ───────────────────────── ocupação WMS: posições por produto ─────────────────────────
def posicoes_por_produto(filiais=None):
    """Espelha q_posicoes_por_produto: {CODPROD, pos} = nº de endereços ocupados (QT>0, RUA<>99)."""
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"""
            SELECT e.codprod, count(DISTINCT e.codendereco)
            FROM pcestendereco e JOIN pcendereco d ON d.codendereco = e.codendereco
            WHERE e.qt > 0 AND d.rua <> 99{_fil_alias(filiais, 'd')}
            GROUP BY e.codprod HAVING count(DISTINCT e.codendereco) > 0
        """)
        return [{"CODPROD": cod, "pos": int(pos)} for cod, pos in cur.fetchall()]


# ───────────────────────── filiais disponíveis ─────────────────────────
def filiais():
    """Espelha q_filiais: [{CODFILIAL}] distintas do PCEST."""
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute("SELECT DISTINCT codfilial FROM pcest")
        return [{"CODFILIAL": f} for (f,) in cur.fetchall()]


# ───────────────────────── Inc.2: venda por produto (RCA→joga_demo) ─────────────────────────
def vendas_por_produto(ini, fim, filiais=None):
    """{cod: {venda, custo, qtd}} LÍQUIDA (venda − devoluções − devol. avulsa) por CODPROD no
    intervalo [ini,fim] (DTSAIDA p/ venda, DTENT p/ devolução), escopado por filiais de VENDA.
    Reproduz o `_vendas_liquidas` do routes.py (medidas VENDA BRUTA/CUSTO TOTAL da raiz), pro modo
    postgres — sem tocar o RCA do cliente. Alimenta venda/lucro/margem/ABC do Cockpit + crescimento."""
    fv = _fil(filiais)
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"""SELECT codprod, {VB}, {CT}, coalesce(sum(qt) FILTER (WHERE codoper='S'),0)
            FROM faturamento_vendas WHERE dtsaida BETWEEN %s AND %s{fv} GROUP BY codprod""",
                    (ini, fim))
        m = {int(cod): {"venda": _f(v) or 0.0, "custo": _f(ct) or 0.0, "qtd": _f(q) or 0.0}
             for cod, v, ct, q in cur.fetchall() if cod is not None}
        cur.execute(f"""SELECT codprod, {DEV}, {CDEV} FROM faturamento_devolucao
            WHERE dtent BETWEEN %s AND %s{fv} GROUP BY codprod""", (ini, fim))
        for cod, dev, cdev in cur.fetchall():
            if cod is not None and int(cod) in m:
                m[int(cod)]["venda"] -= float(dev or 0); m[int(cod)]["custo"] -= float(cdev or 0)
        cur.execute(f"""SELECT codprod, coalesce(sum(vldevolucao),0), coalesce(sum(vlcusto),0)
            FROM faturamento_devolucao_avulsa WHERE dtent BETWEEN %s AND %s{fv} GROUP BY codprod""",
                    (ini, fim))
        for cod, dav, cdav in cur.fetchall():
            if cod is not None and int(cod) in m:
                m[int(cod)]["venda"] -= float(dav or 0); m[int(cod)]["custo"] -= float(cdav or 0)
    return m


# ───────────────────────── Inc.3: Desempenho por comprador (RECEITA COMPRADOR) ─────────────────────────
def receita_comprador(ini, fim, filiais=None):
    """Espelha q_receita_comprador_rca: [{CODCOMPRADOR, venda, custo, qtd, clientes_pos, fornecedores}]."""
    fv = _fil(filiais)
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"""SELECT codcomprador, {VB}, {CT}, coalesce(sum(qt) FILTER (WHERE codoper='S'),0),
                count(DISTINCT codcli), count(DISTINCT codfornec)
            FROM faturamento_vendas WHERE dtsaida BETWEEN %s AND %s{fv}
            GROUP BY codcomprador HAVING {VB} <> 0 OR coalesce(sum(qt) FILTER (WHERE codoper='S'),0) <> 0""",
                    (ini, fim))
        return [{"CODCOMPRADOR": cc, "venda": _f(v) or 0.0, "custo": _f(ct) or 0.0, "qtd": _f(q) or 0.0,
                 "clientes_pos": int(cli or 0), "fornecedores": int(forn or 0)}
                for cc, v, ct, q, cli, forn in cur.fetchall()]


def devol_comprador(ini, fim, filiais=None):
    """Espelha q_devol_comprador_rca: [{CODCOMPRADOR, dev, cdev}] (por DTENT)."""
    fv = _fil(filiais)
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"""SELECT codcomprador, {DEV}, {CDEV} FROM faturamento_devolucao
            WHERE dtent BETWEEN %s AND %s{fv} GROUP BY codcomprador HAVING {DEV} <> 0""", (ini, fim))
        return [{"CODCOMPRADOR": cc, "dev": _f(d) or 0.0, "cdev": _f(cd) or 0.0}
                for cc, d, cd in cur.fetchall()]


def venda_comprador_periodo(ini, fim, filiais=None):
    """Espelha q_venda_comprador_periodo_rca: [{CODCOMPRADOR, venda, custo}] (comparativo ano×ano)."""
    fv = _fil(filiais)
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"""SELECT codcomprador, {VB}, {CT} FROM faturamento_vendas
            WHERE dtsaida BETWEEN %s AND %s{fv} GROUP BY codcomprador HAVING {VB} <> 0""", (ini, fim))
        return [{"CODCOMPRADOR": cc, "venda": _f(v) or 0.0, "custo": _f(ct) or 0.0}
                for cc, v, ct in cur.fetchall()]


# ───────────────────────── Inc.3: séries mensais por produto (360° / forecast) ─────────────────────────
_AM = "extract(year FROM dtsaida)::int*100 + extract(month FROM dtsaida)::int"
_AM_DEV = "extract(year FROM dtent)::int*100 + extract(month FROM dtent)::int"


def vendas_mensal_qt(ini, filiais=None):
    """Espelha q_vendas_mensal_rca: [{CODPROD, AM, qtd}] — QT por produto×mês (base do forecast).
    Mirror do DAX: sem filtro de codoper (soma todas as operações)."""
    fv = _fil(filiais)
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"""SELECT codprod, {_AM} am, sum(qt) FROM faturamento_vendas
            WHERE dtsaida >= %s{fv} GROUP BY codprod, am""", (ini,))
        return [{"CODPROD": cod, "AM": am, "qtd": _f(q) or 0.0} for cod, am, q in cur.fetchall()]


def venda_produto_mensal(ini, filiais=None):
    """Espelha q_venda_produto_mensal_rca: [{CODPROD, AnoMes, venda}] — VENDA BRUTA por produto×mês."""
    fv = _fil(filiais)
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"""SELECT codprod, {_AM} am, {VB} FROM faturamento_vendas
            WHERE dtsaida >= %s{fv} GROUP BY codprod, am HAVING {VB} <> 0""", (ini,))
        return [{"CODPROD": cod, "AnoMes": am, "venda": _f(v) or 0.0} for cod, am, v in cur.fetchall()]


def devol_produto_mensal(ini, filiais=None):
    """Espelha q_devol_produto_mensal_rca: [{CODPROD, AnoMes, dev}] — devolução por produto×mês (DTENT)."""
    fv = _fil(filiais)
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"""SELECT codprod, {_AM_DEV} am, {DEV} FROM faturamento_devolucao
            WHERE dtent >= %s{fv} GROUP BY codprod, am HAVING {DEV} <> 0""", (ini,))
        return [{"CODPROD": cod, "AnoMes": am, "dev": _f(d) or 0.0} for cod, am, d in cur.fetchall()]


def venda_comprador_mensal(ini, filiais=None):
    """Espelha q_venda_comprador_mensal_rca: [{CODCOMPRADOR, AnoMes, venda}] — VENDA BRUTA por comprador×mês
    (base do % da aba Vencidos)."""
    fv = _fil(filiais)
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"""SELECT codcomprador, {_AM} am, {VB} FROM faturamento_vendas
            WHERE dtsaida >= %s{fv} GROUP BY codcomprador, am HAVING {VB} <> 0""", (ini,))
        return [{"CODCOMPRADOR": cc, "AnoMes": am, "venda": _f(v) or 0.0} for cc, am, v in cur.fetchall()]


def devol_comprador_mensal(ini, filiais=None):
    """Espelha q_devol_comprador_mensal_rca: [{CODCOMPRADOR, AnoMes, dev}] — devolução por comprador×mês."""
    fv = _fil(filiais)
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"""SELECT codcomprador, {_AM_DEV} am, {DEV} FROM faturamento_devolucao
            WHERE dtent >= %s{fv} GROUP BY codcomprador, am HAVING {DEV} <> 0""", (ini,))
        return [{"CODCOMPRADOR": cc, "AnoMes": am, "dev": _f(d) or 0.0} for cc, am, d in cur.fetchall()]


# ═════════════════════════ Inc.4: island tables (Validade/Vencidos/Ocupação/Lead time/Verbas) ═════════════════════════
def _in_ints(vals):
    return "(" + ",".join(str(int(v)) for v in vals) + ")" if vals else "(-1)"


# ── Lead time: ponte NUMPED→entrada; Verbas: PCVERBA/PCAPLICVERBA ──
def pedido_entrada():
    """Espelha q_pedido_entrada: [{NUMPED, DTENTRADA}] (ponte NUMPED→1ª entrada da NF)."""
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute("SELECT numped, dtentrada FROM pedido_entrada")
        return [{"NUMPED": np, "DTENTRADA": _iso(dt)} for np, dt in cur.fetchall()]


def verbas():
    """Espelha q_verbas: PCVERBA cru (regra de cancelamento fica no core)."""
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute("""SELECT numverba, codfilial, codfornec, codcomprador, valor, tipo, formapgto,
                              referencia, codconta, dtemissao, dtvenc, dtcancel FROM pcverba""")
        return [{"NUMVERBA": nv, "CODFILIAL": cf, "CODFORNEC": cfor, "CODCOMPRADOR": cc, "VALOR": _f(v),
                 "TIPO": t, "FORMAPGTO": fp, "REFERENCIA": ref, "CODCONTA": cco,
                 "DTEMISSAO": _iso(dte), "DTVENC": _iso(dv), "DTCANCEL": _iso(dc)}
                for (nv, cf, cfor, cc, v, t, fp, ref, cco, dte, dv, dc) in cur.fetchall()]


def verba_aplic():
    """Espelha q_verba_aplic: PCAPLICVERBA cru."""
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute("SELECT numverba, vlaplic, dtaplic, dtestorno FROM pcaplicverba")
        return [{"NUMVERBA": nv, "VLAPLIC": _f(va), "DTAPLIC": _iso(da), "DTESTORNO": _iso(de)}
                for nv, va, da, de in cur.fetchall()]


# ── Validade / FEFO (PCESTENDERECO × PCENDERECO, RUA<>99) ──
def validade(data_ini, data_fim, filiais=None):
    """Espelha q_validade: lotes vencendo na janela. [{CODPROD, NUMLOTE, DTVAL, qt, DESCRICAO}]."""
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"""SELECT e.codprod, e.numlote, e.dtval, sum(e.qt), max(pr.descricao)
            FROM pcestendereco e JOIN pcendereco d ON d.codendereco = e.codendereco
            LEFT JOIN pcprodut pr ON pr.codprod = e.codprod
            WHERE d.rua <> 99{_fil_alias(filiais, 'd')} AND e.dtval BETWEEN %s AND %s
            GROUP BY e.codprod, e.numlote, e.dtval HAVING sum(e.qt) > 0""", (data_ini, data_fim))
        return [{"CODPROD": cod, "NUMLOTE": lo, "DTVAL": _iso(dv), "qt": _f(qt), "DESCRICAO": desc}
                for cod, lo, dv, qt, desc in cur.fetchall()]


def prox_venc(codprods, hoje, filiais=None):
    """Espelha q_prox_venc: menor DTVAL futuro c/ estoque, por CODPROD. [{CODPROD, prox_venc}]."""
    if not codprods:
        return []
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"""SELECT e.codprod, min(e.dtval)
            FROM pcestendereco e JOIN pcendereco d ON d.codendereco = e.codendereco
            WHERE d.rua <> 99{_fil_alias(filiais, 'd')} AND e.codprod IN {_in_ints(codprods)}
              AND e.qt > 0 AND e.dtval >= %s
            GROUP BY e.codprod""", (hoje,))
        return [{"CODPROD": cod, "prox_venc": _iso(pv)} for cod, pv in cur.fetchall()]


def lotes_produto(codprod, filiais=None):
    """Espelha q_lotes_produto: lotes/validades de 1 produto. [{CODPROD, NUMLOTE, DTVAL, qt}]."""
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"""SELECT e.codprod, e.numlote, e.dtval, sum(e.qt)
            FROM pcestendereco e JOIN pcendereco d ON d.codendereco = e.codendereco
            WHERE d.rua <> 99{_fil_alias(filiais, 'd')} AND e.codprod = %s
            GROUP BY e.codprod, e.numlote, e.dtval HAVING sum(e.qt) > 0""", (int(codprod),))
        return [{"CODPROD": cod, "NUMLOTE": lo, "DTVAL": _iso(dv), "qt": _f(qt)}
                for cod, lo, dv, qt in cur.fetchall()]


def desc_de(codprods):
    """Espelha q_desc_de: DESCRICAO de uma lista de códigos (sem filtro revenda/FL)."""
    if not codprods:
        return []
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"SELECT codprod, descricao FROM pcprodut WHERE codprod IN {_in_ints(codprods)}")
        return [{"CODPROD": cod, "DESCRICAO": d} for cod, d in cur.fetchall()]


# ── Vencidos (perda de validade, conta 200042 — PCMOV é o fato) ──
def vencidos(filiais=None):
    """Espelha q_vencidos: itens baixados por perda de validade (join por NUMTRANSVENDA)."""
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"""SELECT n.dtsaida, m.numnota, m.codprod, pr.descricao, m.qt, m.punit,
                m.qt * m.punit, pr.codfornec, f.fornecedor, f.codcomprador, em.nome, m.codfilial
            FROM pcmov m
            LEFT JOIN pcnfsaid n ON n.numtransvenda = m.numtransvenda
            LEFT JOIN pcprodut pr ON pr.codprod = m.codprod
            LEFT JOIN pcfornec f ON f.codfornec = pr.codfornec
            LEFT JOIN pcempr em ON em.matricula = f.codcomprador
            WHERE 1=1{_fil_alias(filiais, 'm')}""")
        return [{"dtsaida": _iso(ds), "numnota": nn, "codprod": cod, "descricao": desc, "qt": _f(qt),
                 "punit": _f(pu), "total": _f(tot), "codfornec": cfor, "fornecedor": fnome,
                 "codcomprador": cc, "comprador": cnome, "codfilial": cfil}
                for (ds, nn, cod, desc, qt, pu, tot, cfor, fnome, cc, cnome, cfil) in cur.fetchall()]


# ── Ocupação / WMS (PCENDERECO × PCESTENDERECO) ──
def produto_enderecos(codprod, filiais=None):
    """Espelha q_produto_enderecos: posições WMS de 1 produto. [{rua,predio,nivel,apto,tipo,q,dtval,numlote}]."""
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"""SELECT d.rua, d.predio, d.nivel, d.apto, d.tipoender, e.qt, e.dtval, e.numlote
            FROM pcestendereco e JOIN pcendereco d ON d.codendereco = e.codendereco
            WHERE e.codprod = %s AND e.qt > 0 AND d.rua <> 99{_fil_alias(filiais, 'd')}""", (int(codprod),))
        return [{"rua": ru, "predio": pr, "nivel": ni, "apto": ap, "tipo": ti, "q": _f(q),
                 "dtval": _iso(dv), "numlote": lo}
                for (ru, pr, ni, ap, ti, q, dv, lo) in cur.fetchall()]


def ocupacao_kpis(filiais=None):
    """Espelha q_ocupacao_kpis: 1 linha {posicoes, ocupadas, bloqueados, com_estoque, produtos, pares}."""
    fp = _fil_alias(filiais, 'd')            # ' AND d.codfilial IN (...)' (join)
    fp_pe = _fil(filiais)                     # ' AND codfilial IN (...)' (pcendereco puro)
    with analytics_conn() as c:
        cur = c.cursor()
        # posições/ocupadas/bloqueados: PCENDERECO puro
        cur.execute(f"""SELECT
            count(*) FILTER (WHERE ativo='S' AND bloqueio='N'),
            count(*) FILTER (WHERE ativo='S' AND bloqueio='N' AND situacao='O'),
            count(*) FILTER (WHERE ativo='S' AND bloqueio='S')
            FROM pcendereco WHERE 1=1{fp_pe}""")
        pos, ocu, bloq = cur.fetchone()
        # com_estoque/produtos/pares: PCESTENDERECO(qt>0) JOIN PCENDERECO(bloqueio='N')
        cur.execute(f"""SELECT count(DISTINCT e.codendereco), count(DISTINCT e.codprod),
                count(DISTINCT (e.codprod, e.codendereco))
            FROM pcestendereco e JOIN pcendereco d ON d.codendereco = e.codendereco
            WHERE e.qt > 0 AND d.bloqueio='N'{fp}""")
        cest, prods, pares = cur.fetchone()
        return [{"posicoes": int(pos or 0), "ocupadas": int(ocu or 0), "bloqueados": int(bloq or 0),
                 "com_estoque": int(cest or 0), "produtos": int(prods or 0), "pares": int(pares or 0)}]


def ocupacao_por_rua(filiais=None):
    """Espelha q_ocupacao_por_rua: [{RUA, posicoes, ocupadas}] (régua WMS: ativo=S, bloqueio=N)."""
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"""SELECT rua, count(*), count(*) FILTER (WHERE situacao='O')
            FROM pcendereco WHERE ativo='S' AND bloqueio='N'{_fil(filiais)} GROUP BY rua""")
        return [{"RUA": ru, "posicoes": int(p or 0), "ocupadas": int(o or 0)} for ru, p, o in cur.fetchall()]


def ocupacao_por_tipo(filiais=None):
    """Espelha q_ocupacao_por_tipo: [{TIPOENDER, posicoes, ocupadas}]."""
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"""SELECT tipoender, count(*), count(*) FILTER (WHERE situacao='O')
            FROM pcendereco WHERE ativo='S' AND bloqueio='N'{_fil(filiais)} GROUP BY tipoender""")
        return [{"TIPOENDER": t, "posicoes": int(p or 0), "ocupadas": int(o or 0)} for t, p, o in cur.fetchall()]


def ocupacao_vazias(filiais=None, rua=None):
    """Espelha q_ocupacao_vazias: posições marcadas Ocupada (SITUACAO='O') SEM estoque físico.
    [{rua,predio,nivel,apto,tipo,codprod,nprod}]."""
    extra = f" AND d.rua = {int(rua)}" if rua is not None else ""
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"""SELECT d.rua, d.predio, d.nivel, d.apto, d.tipoender,
                (SELECT max(x.codprod) FROM pcestendereco x WHERE x.codendereco = d.codendereco),
                (SELECT count(DISTINCT x.codprod) FROM pcestendereco x WHERE x.codendereco = d.codendereco)
            FROM pcendereco d
            WHERE d.situacao='O' AND d.rua <> 99 AND d.ativo='S' AND d.bloqueio='N'{extra}{_fil_alias(filiais, 'd')}
              AND NOT EXISTS (SELECT 1 FROM pcestendereco x WHERE x.codendereco = d.codendereco AND x.qt > 0)""")
        return [{"rua": ru, "predio": pr, "nivel": ni, "apto": ap, "tipo": ti,
                 "codprod": cod, "nprod": int(npd or 0)}
                for (ru, pr, ni, ap, ti, cod, npd) in cur.fetchall()]


def rua_itens(rua, filiais=None):
    """Espelha q_rua_itens: posições COM estoque de uma RUA. [{predio,nivel,apto,tipo,codprod,qt,dtval}]."""
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"""SELECT d.predio, d.nivel, d.apto, d.tipoender, e.codprod, e.qt, e.dtval
            FROM pcestendereco e JOIN pcendereco d ON d.codendereco = e.codendereco
            WHERE e.qt > 0 AND d.rua = %s{_fil_alias(filiais, 'd')}""", (int(rua),))
        return [{"predio": pr, "nivel": ni, "apto": ap, "tipo": ti, "codprod": cod, "qt": _f(q), "dtval": _iso(dv)}
                for (pr, ni, ap, ti, cod, q, dv) in cur.fetchall()]
