"""Pilares do Agente de IA — os rankings que respondem pergunta ESPECÍFICA.

**Por que existe** (08/2026, teste do João Victor + do Gabriel no navegador): a 1ª versão do
contexto tinha só o placar e duas listas de 8 itens. O agente ficou honesto e inútil — recusou
**8 de 9** perguntas reais com "consulte a aba do produto". É o mesmo "duro e picado" que a
decisão do contexto consolidado queria evitar, chegando por outra porta: não por aba, por
agregado. E recusa demais mata a adoção tão rápido quanto invenção.

O desenho veio do João Victor: *"temos que dividir em pilares — Estoque parado, Cobertura de
Estoque, Validade, Itens vencidos, Performance de fornecedores… e dentro da visão gerencial ter
essa análise por comprador é fundamental"*. E do Gabriel: *"para perguntas muito específicas,
ter um top 10 dos melhores aos piores"*.

⚠️ **Custo zero de query.** Tudo aqui sai do `produtos` que o `_build_produtos()` já montou —
os campos (`valor`, `dias_sem_venda`, `cobertura_dias`, `lucro`, `venda_perdida`, `fornecedor`,
`comprador`…) já viajam no dicionário de cada item. A ÚNICA exceção é a validade, que depende
dos lotes e chega pronta de fora.

⚠️ **Nada é recalculado.** Ordenar e cortar não é calcular: os números que entram nas listas são
os mesmos que a tela mostra. Somar/derivar aqui faria o agente divergir das abas, que é a ferida
recorrente do módulo.
"""

from . import core

TOP = 10          # "top 10 dos melhores aos piores" (Gabriel). Curto o bastante p/ não diluir.
_DESC = 42        # descrição truncada: nome longo come o contexto sem acrescentar informação


def _n(v):
    return core._n(v)


def _item(p, campos):
    """Uma linha compacta de item. `campos` = [(rótulo, chave, formato)]."""
    d = {"cod": p.get("codprod"),
         "desc": (p.get("descricao") or "").strip()[:_DESC],
         "fornecedor": (p.get("fornecedor") or "").strip()[:28],
         "comprador": (p.get("comprador") or "").strip()[:24]}
    for rot, chave, _fmt in campos:
        d[rot] = p.get(chave)
    return d


def rank(produtos, chave, n=TOP, filtro=None, maior=True, campos=()):
    """Os N maiores (ou menores) por `chave`, já filtrados.

    ⚠️ `maior=False` NÃO é só inverter: itens com a chave nula/zero entrariam em primeiro e
    entupiriam a lista de "piores" com quem não tem o dado. Por isso o nulo é sempre descartado."""
    itens = [p for p in produtos if (filtro(p) if filtro else True) and p.get(chave) is not None]
    if not maior:
        itens = [p for p in itens if _n(p.get(chave)) > 0]
    itens.sort(key=lambda p: _n(p.get(chave)), reverse=maior)
    return [_item(p, campos) for p in itens[:n]]


# ── pilar: estoque parado ──────────────────────────────────────────────────────────────────
_C_PARADO = [("valor", "valor", "brl"), ("dias_sem_venda", "dias_sem_venda", "int"),
             ("cobertura_dias", "cobertura_dias", "int")]


def parado(produtos):
    """Capital parado (régua do Cockpit: 60+ dias sem venda) e a lista dos maiores ofensores.

    ⚠️ A pergunta real do teste foi *"o item com mais de 30 dias sem vendas com maior estoque
    financeiro"*. Por isso `dias_sem_venda` viaja em CADA linha: sem ele o agente só conseguia
    responder por faixa, e a pessoa queria o item."""
    par = [p for p in produtos if core.eh_parado(p)]
    return {
        "criterio": "sem venda há 60+ dias, com estoque (mesma régua do Cockpit)",
        "itens": len(par),
        "valor": core._round(sum(_n(p.get("valor")) for p in par)),
        "maiores": rank(par, "valor", campos=_C_PARADO),
        "mais_tempo_sem_vender": rank(par, "dias_sem_venda", campos=_C_PARADO),
    }


# ── pilar: cobertura ───────────────────────────────────────────────────────────────────────
_C_COB = [("cobertura_dias", "cobertura_dias", "int"), ("valor", "valor", "brl"),
          ("giro_mes", "giro_mes", "num")]


def cobertura(produtos):
    """Os dois extremos, que são perguntas diferentes: excesso (capital preso em cobertura alta)
    e risco (vai faltar). Só itens COM GIRO — sem giro a cobertura não é calculável e entraria
    como 9999, entupindo o topo com item morto."""
    com_giro = [p for p in produtos if _n(p.get("giro_dia")) > 0 and _n(p.get("qtdisp")) > 0]
    return {
        "base": "somente itens com giro > 0 e estoque > 0",
        "maior_cobertura": rank(com_giro, "cobertura_dias", campos=_C_COB),
        "menor_cobertura": rank(com_giro, "cobertura_dias", maior=False, campos=_C_COB),
        "maior_valor_parado_em_cobertura_alta": rank(
            com_giro, "valor", filtro=lambda p: _n(p.get("cobertura_dias")) >= 120,
            campos=_C_COB),
    }


# ── pilar: ruptura ─────────────────────────────────────────────────────────────────────────
_C_RUP = [("venda_perdida_mes", "venda_perdida", "brl"), ("giro_mes", "giro_mes", "num"),
          ("curva", "curva_abc", "txt"), ("ja_pedido", "qtd_ja_pedida", "num")]


def _em_ruptura(p):
    return _n(p.get("qtdisp")) <= 0 and _n(p.get("giro_dia")) > 0


def ruptura(produtos):
    """Ruptura REAL + o recorte por comprador, que foi pedido no teste e o agente não tinha."""
    rup = [p for p in produtos if _em_ruptura(p)]
    por_curva = {}
    for c in ("A", "B", "C"):
        univ = [p for p in produtos if p.get("curva_abc") == c]
        n = sum(1 for p in univ if _em_ruptura(p))
        por_curva[c] = {"em_ruptura": n, "universo": len(univ),
                        "pct": core._round(n / len(univ) * 100, 1) if univ else None}
    return {
        "por_curva": por_curva,
        "por_comprador": core.ruptura_por_comprador(produtos),
        "mais_custosas": rank(rup, "venda_perdida", campos=_C_RUP),
        "sem_providencia": rank([p for p in rup if core._sem_providencia(p)],
                                "venda_perdida", campos=_C_RUP),
    }


# ── pilar: fornecedores ────────────────────────────────────────────────────────────────────
def fornecedores(produtos, n=TOP):
    """Agregado por fornecedor a partir dos MESMOS produtos da tela.

    ⚠️ No teste o agente respondeu "GALVANOTEK lidera" INFERINDO da lista de maiores ofensores —
    ele não tinha o total por fornecedor e mesmo assim concluiu. Inferência sobre dado ausente é
    a porta de entrada da invenção; a partir daqui o total existe e não há o que inferir.

    ⚠️ O `lucro` já vem agregado por produto (é o mesmo da aba Fornecedores). NÃO se recalcula
    como `venda × margem`: a margem é arredondada a 1 casa e o caminho de volta reintroduz erro
    (é a armadilha registrada no README)."""
    agg = {}
    for p in produtos:
        cod = p.get("codfornec")
        if cod is None:
            continue
        g = agg.setdefault(cod, {"codfornec": cod, "fornecedor": (p.get("fornecedor") or "").strip()[:32],
                                 "skus": 0, "estoque_valor": 0.0, "venda": 0.0, "lucro": 0.0,
                                 "parado_valor": 0.0, "em_ruptura": 0, "a_comprar_nf": 0.0})
        g["skus"] += 1
        g["estoque_valor"] += _n(p.get("valor"))
        g["venda"] += _n(p.get("venda"))
        g["lucro"] += _n(p.get("lucro"))
        if core.eh_parado(p):
            g["parado_valor"] += _n(p.get("valor"))
        if _em_ruptura(p):
            g["em_ruptura"] += 1
        if _n(p.get("sugestao_compra")) > 0:
            g["a_comprar_nf"] += _n(p.get("valor_sugerido_nf"))
    linhas = []
    for g in agg.values():
        for k in ("estoque_valor", "venda", "lucro", "parado_valor", "a_comprar_nf"):
            g[k] = core._round(g[k])
        g["margem_pct"] = core._round(g["lucro"] / g["venda"] * 100, 1) if g["venda"] else None
        linhas.append(g)

    def _top(chave, maior=True, filtro=None):
        base = [x for x in linhas if (filtro(x) if filtro else True)]
        base.sort(key=lambda x: _n(x.get(chave)), reverse=maior)
        return base[:n]

    return {
        "total_fornecedores": len(linhas),
        "maior_estoque": _top("estoque_valor"),
        "maior_lucro": _top("lucro"),
        "menor_lucro": _top("lucro", maior=False),
        "maior_capital_parado": _top("parado_valor"),
        "maior_compra_sugerida": _top("a_comprar_nf"),
    }


# ── pilar: compradores ─────────────────────────────────────────────────────────────────────
def compradores(produtos):
    """Visão gerencial por comprador — *"é fundamental"* (João Victor).

    O orçamento por comprador vem de fora (depende do pedido do Winthor); aqui entra o que sai
    da posição de estoque: ruptura, capital parado e compra sugerida de cada um."""
    agg = {}
    for p in produtos:
        nome = (p.get("comprador") or "").strip() or "(sem comprador)"
        g = agg.setdefault(nome, {"comprador": nome[:28], "skus": 0, "estoque_valor": 0.0,
                                  "parado_valor": 0.0, "em_ruptura": 0, "venda_perdida": 0.0,
                                  "a_comprar_nf": 0.0})
        g["skus"] += 1
        g["estoque_valor"] += _n(p.get("valor"))
        if core.eh_parado(p):
            g["parado_valor"] += _n(p.get("valor"))
        if _em_ruptura(p):
            g["em_ruptura"] += 1
            g["venda_perdida"] += _n(p.get("venda_perdida"))
        if _n(p.get("sugestao_compra")) > 0:
            g["a_comprar_nf"] += _n(p.get("valor_sugerido_nf"))
    out = []
    for g in agg.values():
        for k in ("estoque_valor", "parado_valor", "venda_perdida", "a_comprar_nf"):
            g[k] = core._round(g[k])
        g["pct_parado"] = (core._round(g["parado_valor"] / g["estoque_valor"] * 100, 1)
                           if g["estoque_valor"] else None)
        out.append(g)
    out.sort(key=lambda x: _n(x.get("estoque_valor")), reverse=True)
    return out


# ── pilar: validade ────────────────────────────────────────────────────────────────────────
def validade(resumo, lotes_proximos=None):
    """Faixas de validade + os lotes que vencem primeiro.

    ⚠️ *"quais itens vencem nos próximos 10 dias"* foi respondido com "consulte o sistema
    específico de controle de validade" — o painel TEM a aba Validade/FEFO, o contexto é que não
    carregava nada dela. Recusar o que a ferramenta faz é pior que não ter a ferramenta."""
    if not resumo and not lotes_proximos:
        return None
    return {"faixas": (resumo or {}).get("faixas") or resumo,
            "proximos_vencimentos": lotes_proximos or []}


# ── render ─────────────────────────────────────────────────────────────────────────────────
def _brl(v):
    if v is None:
        return "—"
    try:
        return "R$ " + f"{float(v):,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")
    except (TypeError, ValueError):
        return str(v)


def _v(v):
    if v is None:
        return "—"
    if isinstance(v, float) and v == int(v):
        v = int(v)
    return str(v).replace(".", ",")


def _metrica(rot, valor):
    """Compoe "rotulo valor", menos quando o rotulo seria REDUNDANTE.

    ⚠️ O rotulo "R$" com formato 'brl' produzia "R$ R$ 118.506,19": o `_brl` ja emite o simbolo.
    Eram 70 linhas do prompt em 4 pilares (parado, cobertura, fornecedores e ocupacao) — os mais
    consultados. Achado lendo o prompt gravado pelo rastro de auditoria; ninguem nunca tinha
    olhado o artefato que o modelo recebe, so o codigo que o monta."""
    return valor if rot == "R$" else f"{rot} {valor}"


def _linhas_itens(titulo, itens, metricas):
    """`metricas` = [(rótulo curto, chave, 'brl'|'num')]."""
    if not itens:
        return []
    out = [titulo]
    for i in itens:
        m = " · ".join(_metrica(rot, _brl(i.get(k)) if f == 'brl' else _v(i.get(k)))
                       for rot, k, f in metricas)
        quem = i.get("fornecedor") or i.get("comprador") or ""
        out.append(f"  {i.get('cod')} {i.get('desc')}" + (f" [{quem}]" if quem else "") + f" → {m}")
    return out


# Rótulo humano de cada pilar — alimenta o ÍNDICE. Sem índice, com 11 blocos o modelo passa a
# responder "não tenho" sobre coisa que está três telas abaixo no próprio prompt.
_INDICE = [("parado", "estoque parado / capital parado / dead stock"),
           ("cobertura", "cobertura de estoque, excesso e o que vai faltar"),
           ("ruptura", "ruptura real, por curva, por comprador e as mais caras"),
           ("meta_ruptura", "placar da META de ruptura por curva (régua sem-providência)"),
           ("fornecedores", "performance de fornecedores: estoque, lucro, parado, a comprar"),
           ("compradores", "visão por comprador (posição de estoque)"),
           ("desempenho", "venda, lucro, margem e crescimento por comprador"),
           ("validade", "o que vence primeiro (FEFO)"),
           ("vencidos", "perda JÁ consumada por validade, mês a mês"),
           ("abc_xyz", "matriz ABC x XYZ"),
           ("ocupacao", "ocupação do armazém e espaço morto"),
           ("leadtime", "lead time por fornecedor"),
           ("verbas", "verba negociada por fornecedor"),
           ("qualidade", "qualidade do cadastro")]


# Trecho DISTINTIVO do cabeçalho de cada pilar, usado para descobrir quem realmente renderizou.
# ⚠️ Se um rótulo divergir do render, o pilar apenas deixa de ser ANUNCIADO — nunca o contrário.
# A direção da falha é de propósito: melhor o agente não saber que tem, do que prometer e faltar.
_TITULO = {
    "parado": "ESTOQUE PARADO",
    "cobertura": "COBERTURA DE ESTOQUE",
    "ruptura": "RUPTURA (real",
    "meta_ruptura": "META DE RUPTURA",
    "fornecedores": "PERFORMANCE DE FORNECEDORES",
    "compradores": "VISÃO POR COMPRADOR",
    "desempenho": "DESEMPENHO COMERCIAL POR COMPRADOR",
    "validade": "VALIDADE (FEFO)",
    "vencidos": "VENCIDOS",
    "abc_xyz": "MATRIZ ABC",
    "ocupacao": "OCUPAÇÃO DO ARMAZÉM",
    "leadtime": "LEAD TIME POR FORNECEDOR",
    "verbas": "VERBAS",
    "qualidade": "QUALIDADE DO CADASTRO",
}


def renderizar(pil):
    """Os pilares viram texto rotulado. Cabeçalho explícito por pilar porque é assim que o modelo
    encontra o bloco certo — lista longa sem hierarquia dilui em vez de enriquecer."""
    p = []

    par = pil.get("parado")
    if par:
        p.append(f"=== PILAR: ESTOQUE PARADO ({par['criterio']}) ===")
        p.append(f"Total: {_v(par['itens'])} itens · {_brl(par['valor'])}")
        p += _linhas_itens("Maiores valores parados:", par["maiores"],
                           [("R$", "valor", "brl"), ("s/ vender há", "dias_sem_venda", "num"),
                            ("cob.", "cobertura_dias", "num")])
        p += _linhas_itens("Há mais tempo sem vender:", par["mais_tempo_sem_vender"],
                           [("dias s/ venda", "dias_sem_venda", "num"), ("R$", "valor", "brl")])
        p.append("")

    cob = pil.get("cobertura")
    if cob:
        p.append(f"=== PILAR: COBERTURA DE ESTOQUE ({cob['base']}) ===")
        p.append("⚠️ Cobertura altíssima (milhares de dias) NÃO é excesso de compra: é giro "
                 "RESIDUAL — o item vende uma fração por mês e a divisão explode. Leia o giro "
                 "junto. Para 'onde está o capital preso' use a lista por VALOR abaixo, não esta.")
        p += _linhas_itens("MAIOR cobertura em dias (ver o giro antes de concluir):",
                           cob["maior_cobertura"],
                           [("cobertura", "cobertura_dias", "num"), ("R$", "valor", "brl"),
                            ("giro/mês", "giro_mes", "num")])
        p += _linhas_itens("MENOR cobertura (vai faltar primeiro):", cob["menor_cobertura"],
                           [("cobertura", "cobertura_dias", "num"), ("R$", "valor", "brl"),
                            ("giro/mês", "giro_mes", "num")])
        p += _linhas_itens("Mais dinheiro em cobertura acima de 120 dias:",
                           cob["maior_valor_parado_em_cobertura_alta"],
                           [("R$", "valor", "brl"), ("cobertura", "cobertura_dias", "num")])
        p.append("")

    rup = pil.get("ruptura")
    if rup:
        p.append("=== PILAR: RUPTURA (real: estoque <= 0 e giro > 0) ===")
        pc = rup.get("por_curva") or {}
        p.append("Por curva: " + " · ".join(
            f"{c}: {_v(pc[c]['em_ruptura'])} de {_v(pc[c]['universo'])} ({_v(pc[c]['pct'])}%)"
            for c in ("A", "B", "C") if c in pc))
        if rup.get("por_comprador"):
            p.append("Por comprador:")
            for c in rup["por_comprador"][:TOP]:
                p.append(f"  {c.get('comprador')}: {_v(c.get('n_ruptura'))} em ruptura "
                         f"({_v(c.get('pct_ruptura'))}%) · {_v(c.get('n_sem_pedido'))} sem pedido "
                         f"· venda perdida {_brl(c.get('venda_perdida'))}")
        p += _linhas_itens("Rupturas que mais custam:", rup["mais_custosas"],
                           [("venda perdida/mês", "venda_perdida_mes", "brl"),
                            ("curva", "curva", "num")])
        p += _linhas_itens("Em ruptura e SEM nenhuma providência:", rup["sem_providencia"],
                           [("venda perdida/mês", "venda_perdida_mes", "brl")])
        p.append("")

    fo = pil.get("fornecedores")
    if fo:
        p.append(f"=== PILAR: PERFORMANCE DE FORNECEDORES ({_v(fo['total_fornecedores'])} no recorte) ===")
        for titulo, chave, met in (
                ("Maior estoque financeiro:", "maior_estoque", [("R$", "estoque_valor", "brl"), ("SKUs", "skus", "num")]),
                ("Maior lucro no período:", "maior_lucro", [("lucro", "lucro", "brl"), ("venda", "venda", "brl"), ("margem", "margem_pct", "num")]),
                ("Menor lucro no período:", "menor_lucro", [("lucro", "lucro", "brl"), ("venda", "venda", "brl")]),
                ("Maior capital parado:", "maior_capital_parado", [("parado", "parado_valor", "brl")]),
                ("Maior compra sugerida (régua NF):", "maior_compra_sugerida", [("a comprar", "a_comprar_nf", "brl")])):
            if fo.get(chave):
                p.append(titulo)
                for g in fo[chave]:
                    m = " · ".join(_metrica(r, _brl(g.get(k)) if f == 'brl' else _v(g.get(k)))
                                   for r, k, f in met)
                    p.append(f"  {g.get('codfornec')} {g.get('fornecedor')} → {m}")
        p.append("")

    comp = pil.get("compradores")
    if comp:
        p.append("=== PILAR: VISÃO POR COMPRADOR (posição de estoque) ===")
        for c in comp[:TOP]:
            p.append(f"  {c.get('comprador')}: estoque {_brl(c.get('estoque_valor'))} · "
                     f"parado {_brl(c.get('parado_valor'))} ({_v(c.get('pct_parado'))}%) · "
                     f"{_v(c.get('em_ruptura'))} em ruptura · "
                     f"a comprar {_brl(c.get('a_comprar_nf'))}")
        p.append("")

    val = pil.get("validade")
    if val:
        p.append("=== PILAR: VALIDADE (FEFO) ===")
        fx = val.get("faixas") or []
        if isinstance(fx, list):
            for f in fx:
                p.append(f"  {f.get('faixa')}: {_v(f.get('itens') or f.get('qt'))} lotes · "
                         f"{_brl(f.get('valor'))}")
        for l in (val.get("proximos_vencimentos") or [])[:TOP]:
            p.append(f"  vence {l.get('dt_validade')} ({_v(l.get('dias'))}d): "
                     f"{l.get('cod')} {l.get('desc')} → {_v(l.get('qt'))} un · {_brl(l.get('valor'))}")
        p.append("")

    extras = _render_extras(pil)
    if extras:
        p.append(extras)
    return "\n".join(p)


# ═══════════════ pilares acrescentados no 2º ciclo (auditoria das 22 abas) ═══════════════
# Inventário feito aba a aba: o agente cobria 12 das 22. As de baixo fecham as que dão para
# responder por AGREGADO. Ficou de fora só o Plano de reposição (é simulação semana a semana de
# UM produto — não é pergunta de panorama, é de item, e o caminho ali é a própria aba).

def meta_ruptura(produtos, params=None):
    """O placar da Meta de ruptura (aba Visão) — régua DIFERENTE da ruptura real.

    ⚠️ Aqui o numerador é a ruptura SEM PROVIDÊNCIA (`core._sem_providencia`), não a ruptura
    real: é risco de OMISSÃO. O agente já tinha as duas contagens no placar, mas não tinha as
    METAS por curva, então não conseguia dizer se estava dentro ou fora — que é a pergunta."""
    p = params or {}
    metas = {"A": _n(p.get("meta_ruptura_a") or p.get("metaA") or 2),
             "B": _n(p.get("meta_ruptura_b") or p.get("metaB") or 5),
             "C": _n(p.get("meta_ruptura_c") or p.get("metaC") or 10)}
    out = {}
    for c in ("A", "B", "C"):
        univ = [x for x in produtos if x.get("curva_abc") == c]
        n = sum(1 for x in univ if _em_ruptura(x) and core._sem_providencia(x))
        pct = core._round(n / len(univ) * 100, 1) if univ else None
        out[c] = {"sem_providencia": n, "universo": len(univ), "pct": pct,
                  "meta_pct": metas[c],
                  "dentro_da_meta": (pct is not None and pct <= metas[c])}
    return out


def abc_xyz(produtos):
    """Matriz ABC (valor de venda) × XYZ (previsibilidade da demanda). AX = essencial e previsível;
    CZ = cauda imprevisível. Sai de graça: as duas classificações já viajam no produto."""
    cel = {}
    for p in produtos:
        k = p.get("abc_xyz") or ((p.get("curva_abc") or "?") + (p.get("xyz") or "?"))
        g = cel.setdefault(k, {"celula": k, "skus": 0, "estoque_valor": 0.0, "venda": 0.0,
                               "em_ruptura": 0})
        g["skus"] += 1
        g["estoque_valor"] += _n(p.get("valor"))
        g["venda"] += _n(p.get("venda"))
        if _em_ruptura(p):
            g["em_ruptura"] += 1
    for g in cel.values():
        g["estoque_valor"] = core._round(g["estoque_valor"])
        g["venda"] = core._round(g["venda"])
    return sorted(cel.values(), key=lambda x: -x["estoque_valor"])


def ocupacao(produtos):
    """Ocupação do armazém (WMS) e ESPAÇO MORTO — item que ocupa posição e praticamente não gira.
    `pos_end`, `m3_end` e `espaco_morto` já são calculados no `_build_produtos`."""
    com_pos = [p for p in produtos if _n(p.get("pos_end")) > 0]
    mortos = [p for p in produtos if p.get("espaco_morto")]
    if not com_pos and not mortos:
        return None                      # instância sem WMS endereçado
    return {
        "skus_endereçados": len(com_pos),
        "posicoes_ocupadas": int(sum(_n(p.get("pos_end")) for p in com_pos)),
        "m3_endereçado": core._round(sum(_n(p.get("m3_end")) for p in com_pos), 2),
        "espaco_morto_skus": len(mortos),
        "espaco_morto_valor": core._round(sum(_n(p.get("valor")) for p in mortos)),
        "espaco_morto_posicoes": int(sum(_n(p.get("pos_end")) for p in mortos)),
        "maiores_espacos_mortos": rank(
            mortos, "pos_end",
            campos=[("posicoes", "pos_end", "num"), ("valor", "valor", "brl"),
                    ("giro_mes", "giro_mes", "num")]),
    }


def desempenho(produtos):
    """Venda, lucro e margem por COMPRADOR (aba Desempenho comercial), a partir da mesma posição.

    ⚠️ A margem é `lucro ÷ venda` calculada aqui sobre os DOIS agregados — não é média das
    margens dos itens, que daria número diferente e divergiria da tela."""
    agg = {}
    for p in produtos:
        nome = (p.get("comprador") or "").strip() or "(sem comprador)"
        g = agg.setdefault(nome, {"comprador": nome[:28], "venda": 0.0, "lucro": 0.0,
                                  "venda_ano_ant": 0.0})
        g["venda"] += _n(p.get("venda"))
        g["lucro"] += _n(p.get("lucro"))
        g["venda_ano_ant"] += _n(p.get("venda_ano_ant"))
    out = []
    for g in agg.values():
        v, la = g["venda"], g["venda_ano_ant"]
        g["margem_pct"] = core._round(g["lucro"] / v * 100, 1) if v else None
        g["cresc_aa_pct"] = core._round((v - la) / la * 100, 1) if la else None
        for k in ("venda", "lucro", "venda_ano_ant"):
            g[k] = core._round(g[k])
        out.append(g)
    return sorted(out, key=lambda x: -_n(x.get("venda")))


def qualidade(produtos, cadastro=None):
    """Qualidade do cadastro. Dois universos, como na aba — e a diferença é declarada.

    `cadastro` = saída de `core.qualidade_cadastro` (BASE INTEIRA). O bloco do snapshot mede só
    o que está no recorte da tela."""
    sem_medida = [p for p in produtos if p.get("medidas_confiaveis") is False]
    d = {
        "no_recorte": {
            "skus": len(produtos),
            "sem_medidas_confiaveis": len(sem_medida),
            "sem_fator_de_caixa": sum(1 for p in produtos if _n(p.get("qtunitcx")) <= 1),
        }
    }
    if cadastro:
        r = cadastro.get("resumo") or {}
        d["base_inteira"] = {"total_com_problema": r.get("total"),
                             "base": r.get("base"),
                             "por_categoria": r.get("contagem")}
    return d


def vencidos(res):
    """Perda REALIZADA por validade (conta 200042), mês a mês — a aba Vencidos.

    ⚠️ É a única métrica de PERDA CONSUMADA do módulo: validade/FEFO fala do que ainda pode ser
    salvo, esta fala do dinheiro que já foi. O João Victor listou as duas como pilares separados
    justamente por isso, e confundi-las trocaria "risco" por "prejuízo"."""
    if not res:
        return None
    meses = (res.get("meses") or [])[-12:]
    if not meses and not (res.get("compradores") or []):
        return None
    return {"por_mes": [{"mes": m.get("mes"), "valor": m.get("valor"),
                         "pct_venda": m.get("pct_venda")} for m in meses],
            "total": res.get("total"),
            "por_comprador": (res.get("compradores") or [])[:TOP],
            "ainda_em_estoque": (res.get("em_estoque") or [])[:TOP]}


def leadtime(res):
    """Lead time por fornecedor (aba Lead time) — quanto tempo entre emitir e receber."""
    if not res:
        return None
    # ⚠️ As chaves são `lead_real` / `n_reais` / `confiavel` (conferidas no
    # `core.leadtime_fornecedores`). A 1ª versão chutou `lead_mediano`/`n_pedidos`: o filtro
    # descartava TODAS as linhas, o pilar saía vazio e o índice o anunciava assim mesmo — o
    # agente então dizia "o painel não tem lead time" sobre uma aba que existe. Foi o que o
    # `smoke_ia_real` pegou. É a razão de o índice hoje sair do que RENDERIZOU, não do que existe.
    linhas = [l for l in (res.get("fornecedores") or []) if l.get("lead_real") is not None]
    linhas.sort(key=lambda x: -_n(x.get("lead_real")))
    if not linhas:
        return None
    _fmt = lambda l: {"fornecedor": l.get("fornecedor"), "lead": l.get("lead_real"),
                      "pedidos": l.get("n_reais"), "confiavel": l.get("confiavel")}
    return {"piores": [_fmt(l) for l in linhas[:TOP]],
            "melhores": [_fmt(l) for l in linhas[::-1][:TOP]]}


def verbas(res):
    """Verba NEGOCIADA por fornecedor (aba Verbas) — 12 meses; saldo é posição."""
    if not res:
        return None
    linhas = list(res.get("fornecedores") or [])
    if not linhas and not (res.get("resumo") or {}).get("negociado_12m"):
        return None
    linhas.sort(key=lambda x: -_n(x.get("negociado")))
    # o resumo da aba chama de `negociado_12m`/`saldo_aberto` — sem o de-para o agente dizia
    # "total exato não está disponível" com o total na mão
    _r = res.get("resumo") or {}
    return {"resumo": {"negociado": _r.get("negociado_12m"), "aplicado": _r.get("aplicado_12m"),
                       "saldo": _r.get("saldo_aberto"), "fornecedores": _r.get("n_fornec")},
            "maiores": [{"fornecedor": l.get("fornecedor"), "negociado": l.get("negociado"),
                         "saldo": l.get("saldo"), "pct_vc": l.get("pct_vc")}
                        for l in linhas[:TOP]]}


def _render_extras(pil):
    """Render dos pilares do 2º ciclo. Separado do `renderizar` só por tamanho — o `renderizar`
    já estava longo e um arquivo que ninguém consegue ler inteiro é onde bug se esconde."""
    p = []

    mr = pil.get("meta_ruptura")
    if mr:
        p.append("=== PILAR: META DE RUPTURA (régua SEM-PROVIDÊNCIA, não a ruptura real) ===")
        p.append("⚠️ Este % é MENOR que o da ruptura real: conta só o que está zerado E sem "
                 "nenhum pedido/pré-entrada. É esta régua — e só esta — que se compara à meta.")
        for c in ("A", "B", "C"):
            g = mr.get(c) or {}
            ok = "DENTRO" if g.get("dentro_da_meta") else "ACIMA"
            p.append(f"  Curva {c}: {_v(g.get('pct'))}% (meta {_v(g.get('meta_pct'))}%) → {ok} "
                     f"· {_v(g.get('sem_providencia'))} de {_v(g.get('universo'))} SKUs")
        p.append("")

    de = pil.get("desempenho")
    if de:
        p.append("=== PILAR: DESEMPENHO COMERCIAL POR COMPRADOR ===")
        for g in de[:TOP]:
            p.append(f"  {g.get('comprador')}: venda {_brl(g.get('venda'))} · "
                     f"lucro {_brl(g.get('lucro'))} · margem {_v(g.get('margem_pct'))}% · "
                     f"cresc. AA {_v(g.get('cresc_aa_pct'))}%")
        p.append("")

    ve = pil.get("vencidos")
    if ve and (ve.get("por_mes") or ve.get("por_comprador")):
        p.append("=== PILAR: VENCIDOS — perda JÁ consumada por validade ===")
        p.append("⚠️ Diferente do pilar VALIDADE: aquele é risco (ainda dá para vender), este é "
                 "prejuízo realizado. Não troque um pelo outro.")
        for m in (ve.get("por_mes") or []):
            p.append(f"  {m.get('mes')}: {_brl(m.get('valor'))}"
                     + (f" ({_v(m.get('pct_venda'))}% da venda)" if m.get("pct_venda") is not None else ""))
        for c in (ve.get("por_comprador") or []):
            p.append(f"  comprador {c.get('comprador')}: {_brl(c.get('valor'))}")
        p.append("")

    ax = pil.get("abc_xyz")
    if ax:
        p.append("=== PILAR: MATRIZ ABC x XYZ (A/B/C = peso na venda · X/Y/Z = previsibilidade) ===")
        for g in ax[:12]:
            p.append(f"  {g.get('celula')}: {_v(g.get('skus'))} SKUs · "
                     f"estoque {_brl(g.get('estoque_valor'))} · venda {_brl(g.get('venda'))} · "
                     f"{_v(g.get('em_ruptura'))} em ruptura")
        p.append("")

    oc = pil.get("ocupacao")
    if oc and (_n(oc.get("posicoes_ocupadas")) or _n(oc.get("espaco_morto_skus"))):
        p.append("=== PILAR: OCUPAÇÃO DO ARMAZÉM (WMS) ===")
        p.append(f"  {_v(oc.get('skus_endereçados'))} SKUs endereçados · "
                 f"{_v(oc.get('posicoes_ocupadas'))} posições · {_v(oc.get('m3_endereçado'))} m³")
        p.append(f"  ESPAÇO MORTO (ocupa posição e quase não gira): "
                 f"{_v(oc.get('espaco_morto_skus'))} SKUs · {_v(oc.get('espaco_morto_posicoes'))} "
                 f"posições · {_brl(oc.get('espaco_morto_valor'))}")
        p += _linhas_itens("Maiores espaços mortos:", oc.get("maiores_espacos_mortos") or [],
                           [("posições", "posicoes", "num"), ("R$", "valor", "brl"),
                            ("giro/mês", "giro_mes", "num")])
        p.append("")

    lt = pil.get("leadtime")
    if lt and (lt.get("piores") or lt.get("melhores")):
        p.append("=== PILAR: LEAD TIME POR FORNECEDOR (dias entre emitir e receber) ===")
        for l in (lt.get("piores") or []):
            aviso = "" if l.get("confiavel") else " (amostra fraca)"
            p.append(f"  MAIS LENTO {l.get('fornecedor')}: {_v(l.get('lead'))}d "
                     f"· {_v(l.get('pedidos'))} pedidos{aviso}")
        for l in (lt.get("melhores") or [])[:5]:
            aviso = "" if l.get("confiavel") else " (amostra fraca)"
            p.append(f"  MAIS RÁPIDO {l.get('fornecedor')}: {_v(l.get('lead'))}d{aviso}")
        p.append("")

    vb = pil.get("verbas")
    if vb and (vb.get("maiores") or (vb.get("resumo") or {}).get("negociado") is not None):
        p.append("=== PILAR: VERBAS (negociado nos últimos 12 meses; saldo é POSIÇÃO atual) ===")
        r = vb.get("resumo") or {}
        if r.get("negociado") is not None:
            p.append(f"  Negociado 12m: {_brl(r.get('negociado'))} · "
                     f"aplicado {_brl(r.get('aplicado'))} · "
                     f"saldo em aberto {_brl(r.get('saldo'))} · "
                     f"{_v(r.get('fornecedores'))} fornecedores")
        for l in (vb.get("maiores") or []):
            p.append(f"  {l.get('fornecedor')}: negociado {_brl(l.get('negociado'))} · "
                     f"saldo {_brl(l.get('saldo'))} · V/C {_v(l.get('pct_vc'))}%")
        p.append("")

    qa = pil.get("qualidade")
    if qa:
        p.append("=== PILAR: QUALIDADE DO CADASTRO ===")
        nr = qa.get("no_recorte") or {}
        p.append(f"  No recorte da tela: {_v(nr.get('sem_medidas_confiaveis'))} sem medidas "
                 f"confiáveis · {_v(nr.get('sem_fator_de_caixa'))} sem fator de caixa "
                 f"(de {_v(nr.get('skus'))} SKUs)")
        bi = qa.get("base_inteira") or {}
        if bi:
            p.append(f"  ⚠️ Na BASE INTEIRA (universo diferente, como na aba): "
                     f"{_v(bi.get('total_com_problema'))} de {_v(bi.get('base'))} produtos")
            for k, v in (bi.get("por_categoria") or {}).items():
                p.append(f"    - {k}: {_v(v)}")
        p.append("")

    corpo = "\n".join(p)
    # ⚠️ O ÍNDICE sai do que REALMENTE foi renderizado, varrendo os cabeçalhos "=== PILAR:"
    # do texto — não da lista de pilares que existem no dicionário. Motivo: um pilar cujo
    # payload veio com chave errada produz bloco VAZIO, e um índice montado da estrutura o
    # anunciaria assim mesmo. Foi o que aconteceu com o lead time: o índice prometia e o
    # agente respondia "o painel não tem lead time" sobre uma aba que existe. Assim, chave
    # errada degrada para "não anunciado" em vez de "prometido e ausente".
    rotulos = dict(_INDICE)
    presentes = [k for k, _ in _INDICE
                 if ("=== PILAR: " + _TITULO.get(k, "").upper()) in corpo.upper()]
    if not presentes:
        return corpo
    cab = ["=== ÍNDICE DOS PILARES DISPONÍVEIS (procure aqui ANTES de dizer que não tem) ==="]
    cab += [f"  · {k}: {rotulos[k]}" for k in presentes]
    cab.append("")
    return "\n".join(cab) + "\n" + corpo
