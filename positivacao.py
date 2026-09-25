"""Positivação de carteira por RCA. Funções puras — sem Flask/DAX/DB.
Testes em tests/test_positivacao.py.

Réguas (acordadas com o João Victor em 24/09/2026), todas no último mês FECHADO:
- BASE ATIVA do RCA = clientes CADASTRADOS nele (PCCLIENT.CODUSUR1) que compraram em algum
  dos 12 meses que terminam no mês de referência. NÃO é o cadastro inteiro e NÃO usa o
  BLOQUEIO (965 bloqueados compraram em 12m — bloqueio é de crédito, não inatividade).
- COBERTURA DA BASE = clientes da base atendidos POR ELE no mês ÷ base ativa. Fica entre 0 e 1.
  É a que entra em comparação/nota.
- FORA DA BASE = clientes de OUTRAS bases que ele atendeu no mês, em 4 tipos (nesta ordem):
  · código fictício (transferência/prospecção/caixa…) e · RCA inativo → CADASTRO a transferir,
    não desempenho (ago/26: o VALDELI atendia 90 fora da base, 64 do MATEUS, parado há 3m);
  · liberado → o cliente estava há mais de REGRA_LIBERADO_DIAS (60) sem comprar quando ele
    vendeu: pela REGRA COMERCIAL da empresa qualquer vendedor pode atender (não é do ERP);
  · colega → cliente ATIVO de um RCA ativo. É o único tipo que merece conversa.
- ALCANCE = tudo que ele atendeu no mês ÷ base ativa. Pode passar de 100%. É INFORMATIVO:
  somar "fora da base" na cobertura esconde a base descoberta (IGOR, loja: alcance 93% com a
  própria base coberta em 48%) e premiaria vender para cliente de colega.
- Base ativa < MIN_AMOSTRA → sem cobertura (None): caixa/balcão que vende para carteira alheia
  não é parâmetro (LARISSA, base 0; WAGNER, base 1).

Por que existe: até 09/2026 a taxa era "clientes que compraram DO RCA em 12m ÷ cadastro INTEIRO
dele (42 mil, com 33,9 mil bloqueados)" — 37 de 97 RCAs passavam de 100% (máx. 8.600%).
"""

MESES_CARTEIRA = 12
MIN_AMOSTRA = 5
REGRA_LIBERADO_DIAS = 60   # regra comercial: > 60 dias sem comprar, qualquer vendedor atende

DONO_FICTICIO = 'ficticio'
DONO_INATIVO = 'rca_inativo'
DONO_LIBERADO = 'liberado'
DONO_COLEGA = 'colega'
TIPOS_DONO = (DONO_COLEGA, DONO_LIBERADO, DONO_FICTICIO, DONO_INATIVO)


def anomes_de(d):
    """date → AAAAMM (int)."""
    return d.year * 100 + d.month


def mes_add(anomes, n):
    """Soma n meses (n pode ser negativo) a um AAAAMM."""
    i = (anomes // 100) * 12 + (anomes % 100 - 1) + n
    return (i // 12) * 100 + i % 12 + 1


def mes_fechado(hoje):
    """Último mês fechado em relação a `hoje` (date) → AAAAMM."""
    return mes_add(anomes_de(hoje), -1)


def base_ativa(venda_mensal, codusur_de, anomes_ref, meses=MESES_CARTEIRA):
    """{codusur: set(codcli)} — clientes do CADASTRO de cada RCA com compra (venda > 0) em
    algum dos `meses` meses que terminam em `anomes_ref`. Cliente sem RCA fica fora."""
    janela = {mes_add(anomes_ref, -i) for i in range(meses)}
    out = {}
    for codcli, por_mes in (venda_mensal or {}).items():
        u = codusur_de.get(codcli)
        if u is None:
            continue
        if any((v or 0) > 0 and am in janela for am, v in por_mes.items()):
            out.setdefault(u, set()).add(codcli)
    return out


def liberados_no_mes(primeira_venda, ultima_antes, dias=REGRA_LIBERADO_DIAS):
    """{codusur: set(codcli)} — clientes que CADA RCA atendeu no mês estando LIBERADOS pela
    regra comercial (mais de `dias` sem comprar no momento da venda dele).

    primeira_venda: {codusur: {codcli: date}} — 1ª venda de cada RCA a cada cliente no mês.
    ultima_antes:   {codcli: date} — última compra do cliente ANTES do mês (None/ausente = nenhuma).
    A compra anterior à venda dele é a mais recente entre a última antes do mês e a 1ª venda de
    OUTRO vendedor no mês que tenha vindo antes — se o dono vendeu dia 3, quem vende dia 20 não
    pegou cliente liberado."""
    por_cli = {}
    for u, cs in primeira_venda.items():
        for c, d in cs.items():
            por_cli.setdefault(c, []).append((d, u))
    out = {}
    for c, vendas in por_cli.items():
        for d, u in vendas:
            antes = [x for x, v in vendas if v != u and x < d]
            ult = ultima_antes.get(c)
            prev = max(antes + ([ult] if ult else []), default=None)
            if prev is None or (d - prev).days > dias:
                out.setdefault(u, set()).add(c)
    return out


def tipo_dono(dono, ficticios, rcas_ativos, liberado=False):
    """Classifica um cliente atendido fora da base. Cadastro errado vem antes da regra dos 60
    dias: cliente de fictício/RCA inativo é cadastro a transferir mesmo que esteja liberado."""
    if dono in ficticios:
        return DONO_FICTICIO
    if dono is None or dono not in rcas_ativos:
        return DONO_INATIVO
    if liberado:
        return DONO_LIBERADO
    return DONO_COLEGA


def positivacao_por_rca(bases, atendidos, codusur_de, ficticios=(), rcas_ativos=(), liberados=None):
    """Placar por RCA no mês de referência.

    Args:
        bases:       {codusur: set(codcli)} — saída de base_ativa().
        atendidos:   {codusur: set(codcli)} — clientes que CADA RCA atendeu (vendeu) no mês.
        codusur_de:  {codcli: codusur} — dono do cadastro, p/ classificar o "fora da base".
        ficticios:   códigos que não são pessoa (transferência, prospecção, caixa…).
        rcas_ativos: RCAs que venderam recentemente e não estão bloqueados.
        liberados:   {codusur: set(codcli)} — saída de liberados_no_mes().

    Returns:
        {codusur: {base_ativa, cobertos, cobertura, fora_base, fora_por_tipo{...}, atendidos,
                   alcance, amostra_pequena}}. `cobertura`/`alcance` são None sem base mínima.
    """
    ficticios, rcas_ativos = set(ficticios), set(rcas_ativos)
    liberados = liberados or {}
    out = {}
    for u in set(bases) | set(atendidos):
        base = bases.get(u, set())
        meus = atendidos.get(u, set())
        fora = meus - base
        por_tipo = {t: 0 for t in TIPOS_DONO}
        lib = liberados.get(u, set())
        for c in fora:
            por_tipo[tipo_dono(codusur_de.get(c), ficticios, rcas_ativos, c in lib)] += 1
        n = len(base)
        pequena = n < MIN_AMOSTRA
        cobertos = len(base & meus)
        out[u] = {
            'base_ativa':      n,
            'cobertos':        cobertos,
            'cobertura':       None if pequena else cobertos / n,
            'fora_base':       len(fora),
            'fora_por_tipo':   por_tipo,
            'atendidos':       len(meus),
            'alcance':         None if pequena else len(meus) / n,
            'amostra_pequena': pequena,
        }
    return out


# ───────────────────────── positivação por curva ABC de CLIENTES (item 1) ─────────────────────────
# Regra ÚNICA de classe: a do INÍCIO do mês medido = Pareto (80/95, curva_abc) da venda líquida
# dos 12 meses FECHADOS anteriores. Medido em jun–ago/26: calcular a classe incluindo o próprio
# mês infla a positivação ~2,5–3 p.p. em todas as classes (quem comprou forte no mês sobe de
# classe) e muda ~550 clientes de classe. A coluna da Carteira mostra a classe do início do mês
# corrente; o mês fechado é medido com a classe do início DELE.
# Medido (classe sem olhar o futuro): A ≈ 82% · B ≈ 59% · C ≈ 25% de positivação mensal.

def classes_abc(venda_por_cliente):
    """{codcli: 'A'|'B'|'C'} — só clientes com venda > 0 (os demais não têm classe)."""
    import curva_abc
    itens = [{'codcli': c, 'venda': v} for c, v in venda_por_cliente.items() if (v or 0) > 0]
    return {i['codcli']: i['classe'] for i in curva_abc.classificar(itens)}


def venda_12m_ate(venda_mensal, devolucao_mensal, anomes_fim, meses=MESES_CARTEIRA):
    """{codcli: venda líquida} dos `meses` meses que terminam em `anomes_fim` (inclusive)."""
    janela = {mes_add(anomes_fim, -i) for i in range(meses)}
    out = {}
    for c, por_mes in (venda_mensal or {}).items():
        v = sum(x or 0 for m, x in por_mes.items() if m in janela)
        if v:
            out[c] = v
    for c, por_mes in (devolucao_mensal or {}).items():
        d = sum(x or 0 for m, x in por_mes.items() if m in janela)
        if d:
            out[c] = out.get(c, 0) - d
    return out


def positivacao_por_classe(classes, escopo, compraram):
    """{classe: {base, positivados, taxa}} — base = clientes do escopo COM classe (compraram
    nos 12m antes do mês); positivado = está em `compraram` (de qualquer vendedor: é visão de
    carteira, não de vendedor)."""
    out = {k: {'base': 0, 'positivados': 0} for k in ('A', 'B', 'C')}
    for c in escopo:
        k = classes.get(c)
        if k is None:
            continue
        out[k]['base'] += 1
        if c in compraram:
            out[k]['positivados'] += 1
    for v in out.values():
        v['taxa'] = (v['positivados'] / v['base']) if v['base'] else None
    return out


# ───────────────────────── taxas de atendimento por RCA (item 4) ─────────────────────────
# Por CLIENTE POSITIVADO no mês, régua de VENDA (quem vendeu): mix = SKUs distintos,
# cross-selling = departamentos distintos, marca = marcas distintas, frequência = pedidos.
# Medianas no campo (ago/26): 11 SKUs · 4,7 deptos · 3,2 marcas. As três primeiras correlacionam
# 0,87–0,94 entre si: na nota do ranking entra só o mix; as outras são diagnóstico.
# ⚠️ Contagem distinta NÃO se soma de vendedor para time — cada grão é medido no seu nível.

def taxas_atendimento(atend_mes):
    """{codusur: {clientes, mix, cross, marca, frequencia}} a partir de
    {codusur: {codcli: {'skus', 'deptos', 'marcas', 'peds'}}} (um mês)."""
    out = {}
    for u, clis in (atend_mes or {}).items():
        n = len(clis)
        if not n:
            continue
        soma = lambda k: sum((m.get(k) or 0) for m in clis.values())
        out[u] = {'clientes': n, 'mix': soma('skus') / n, 'cross': soma('deptos') / n,
                  'marca': soma('marcas') / n, 'frequencia': soma('peds') / n}
    return out
