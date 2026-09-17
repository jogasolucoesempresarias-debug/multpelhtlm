"""Curva ABC por time — motor PURO (sem Flask, sem BI), como rfm.py / cohort.py.

Pareto clássico: ordena por venda desc, acumula o % e classifica A ≤ `corte_a`, B ≤ `corte_b`,
C = cauda. É a MESMA regra do `estoque/core._aplicar_curva` (cortes 80/95) — o gate
`tests/test_curva_abc.py::test_mesma_regua_do_compras` trava a equivalência, para o app não ter
duas definições de "curva A" com o mesmo nome. Não importa `estoque.core` de propósito: o Comercial
não pode depender do pacote de Compras (módulo opcional por instância).

⚠️ Quem VENDEU, não de quem o cliente É. A curva mede o que o time vendeu (régua de VENDA,
`FATURAMENTO_VENDAS[CODSUPERVISOR]`), coerente com Dashboard/Vendedores. Medido no BI real em
15/09/2026 (12m): nos 4 times de campo grandes as duas réguas concordam em 97-99,8% das classes;
em Lojas e Diretoria divergem 40-66% na venda — cliente cadastrado num time, vendido por outro.
A tela declara a régua na 1ª linha.
"""

CORTE_A = 80.0      # % acumulado da venda: até aqui é A
CORTE_B = 95.0      # até aqui é B; o resto é C
MESES = 12          # janela padrão da curva (casa com o produtos_map do Radar, também 12m)

# Janelas do seletor (pedido do João Victor 17/09/2026: "uma janela de 12 meses muda muito a
# performance do produto"). ⚠️ A curva é o Pareto da venda DO PERÍODO: trocar a janela muda QUAIS
# itens são A/B/C — aqui isso é o desejado (é o oposto da nota do Compras, onde a janela foi travada
# de propósito). `meses` é o peso da janela p/ escalar o piso de venda da amostra; o mês atual é
# parcial (1º dia até hoje) e vale 1.
PERIODOS = {
    '12m':       {'meses': 12, 'rotulo': 'últimos 12 meses', 'curto': '12m'},
    '6m':        {'meses': 6,  'rotulo': 'últimos 6 meses',  'curto': '6m'},
    '3m':        {'meses': 3,  'rotulo': 'últimos 3 meses',  'curto': '3m'},
    'mes_atual': {'meses': 1,  'rotulo': 'mês atual',        'curto': 'mes'},
}
PERIODO_PADRAO = '12m'


def normalizar_periodo(tok):
    """Token da querystring → chave de PERIODOS (desconhecido/vazio = padrão 12m)."""
    tok = (str(tok or '')).strip().lower()
    return tok if tok in PERIODOS else PERIODO_PADRAO

# Abaixo disto a curva é ruído: E-COMMERCE tinha 266 produtos e R$ 42 mil em 12m no BI real —
# "curva A" de 5 itens não orienta ninguém. A tela avisa, não esconde (mesma política do
# `lead_confiavel` do Compras: número com aviso, nunca número seco).
AMOSTRA_MIN_PRODUTOS = 200
AMOSTRA_MIN_VENDA = 100_000.0


def margem_pct(lucro, venda):
    """Margem em % na régua do Comercial: LUCRO TOTAL ÷ VENDA LÍQUIDA (Dashboard e Categorias).
    Ponderada no período (lucro ÷ venda), não média de margens mensais. None se não há venda."""
    if not venda or venda <= 0:
        return None
    return round((lucro or 0) / venda * 100, 2)


def classificar(itens, chave='venda', corte_a=CORTE_A, corte_b=CORTE_B):
    """Devolve NOVA lista ordenada por `chave` desc, cada item com `rank`, `pct` (% da venda),
    `pct_acum`, `classe` e `margem` (% — a partir de `lucro`, se o item tiver). Não muta a entrada.
    Venda ≤ 0 → classe C, pct 0.

    Fronteira INCLUSIVA (`<=`), igual ao Compras: o item que pousa exatamente em 80% ainda é A."""
    ordenados = sorted((dict(i) for i in itens), key=lambda x: x.get(chave) or 0, reverse=True)
    total = sum(v for v in (i.get(chave) or 0 for i in ordenados) if v > 0)
    acum = 0.0
    for n, it in enumerate(ordenados, 1):
        v = it.get(chave) or 0
        if total > 0 and v > 0:
            acum += v
            pct = v / total * 100
            pct_acum = acum / total * 100
            classe = 'A' if pct_acum <= corte_a else ('B' if pct_acum <= corte_b else 'C')
        else:
            pct, pct_acum, classe = 0.0, (acum / total * 100 if total > 0 else 0.0), 'C'
        it['rank'] = n
        it['pct'] = round(pct, 4)
        it['pct_acum'] = round(pct_acum, 4)
        it['classe'] = classe
        if 'lucro' in it:
            it['margem'] = margem_pct(it.get('lucro'), v)
    return ordenados


def resumo(itens, chave='venda'):
    """KPIs da tela: por classe → {qt, venda, pct_venda, lucro, margem}; total; concentração (% dos
    itens que fazem `CORTE_A`% da venda). Espera itens já classificados. A margem da classe é a
    ponderada (Σ lucro ÷ Σ venda dos itens com venda > 0), a leitura "meu carro-chefe é 80% da
    venda com X% de margem"."""
    total_venda = sum(i.get(chave) or 0 for i in itens if (i.get(chave) or 0) > 0)
    total_lucro = sum(i.get('lucro') or 0 for i in itens if (i.get(chave) or 0) > 0)
    por_classe = {}
    for c in ('A', 'B', 'C'):
        sel = [i for i in itens if i.get('classe') == c and (i.get(chave) or 0) > 0]
        venda = sum(i.get(chave) or 0 for i in sel)
        lucro = sum(i.get('lucro') or 0 for i in sel)
        por_classe[c] = {
            'qt': sum(1 for i in itens if i.get('classe') == c),
            'venda': round(venda, 2),
            'pct_venda': round(venda / total_venda * 100, 1) if total_venda else 0.0,
            'lucro': round(lucro, 2),
            'margem': margem_pct(lucro, venda),
        }
    n = len(itens)
    return {
        'total_produtos': n,
        'total_venda': round(total_venda, 2),
        'total_lucro': round(total_lucro, 2),
        'margem': margem_pct(total_lucro, total_venda),
        'classes': por_classe,
        # "X% dos itens fazem 80% da venda" — o número que a gerente repete na reunião
        'concentracao_pct_itens': round(por_classe['A']['qt'] / n * 100, 1) if n else 0.0,
    }


def amostra_confiavel(itens, chave='venda', periodo=PERIODO_PADRAO):
    """(ok, motivo). Falso quando o escopo é pequeno demais para a curva significar algo.
    O piso de VENDA é de 12 meses e escala com a janela (6m = metade, mês atual = 1/12) — senão
    todo time cai na faixa amarela no dia 3 do mês. O piso de PRODUTOS não escala: um time de
    verdade vende mais de 200 itens distintos num mês."""
    n = len(itens)
    venda = sum(i.get(chave) or 0 for i in itens if (i.get(chave) or 0) > 0)
    meses = PERIODOS[normalizar_periodo(periodo)]['meses']
    min_venda = AMOSTRA_MIN_VENDA * meses / 12
    if n < AMOSTRA_MIN_PRODUTOS:
        return False, f'{n} produtos no escopo (mínimo {AMOSTRA_MIN_PRODUTOS})'
    if venda < min_venda:
        return False, f'venda de R$ {venda:,.0f} no escopo (mínimo R$ {min_venda:,.0f} para a janela)'
    return True, ''


def filtrar(itens, classe=None, codepto=None, codfornec=None, busca=None):
    """Filtro de TELA — o mesmo que o front aplica, reaplicado no servidor para o export sair com o
    recorte que a pessoa está vendo (armadilha já paga no Compras: tela com 116 itens, PDF com o
    universo). Classe é filtro sobre a curva JÁ calculada — filtrar antes reclassificaria."""
    out = itens
    if classe:
        cls = {c.strip().upper() for c in str(classe).split(',') if c.strip()}
        out = [i for i in out if i.get('classe') in cls]
    if codepto not in (None, ''):
        out = [i for i in out if _cod_str(i.get('codepto')) == _cod_str(codepto)]
    if codfornec not in (None, ''):
        out = [i for i in out if _cod_str(i.get('codfornec')) == _cod_str(codfornec)]
    if busca:
        q = str(busca).strip().lower()
        if q:
            out = [i for i in out if q in str(i.get('descricao') or '').lower()
                   or q == str(i.get('codprod') or '')]
    return out


def _cod_str(v):
    """'7', 7, 7.0 → '7'. O CODEPTO vem float do Power BI e int do Postgres; sem normalizar os dois
    lados, '7' != '7.0' e o filtro esvazia a lista em silêncio (bug real do Compras, 08/2026)."""
    if v is None:
        return ''
    try:
        f = float(v)
        return str(int(f)) if f == int(f) else str(f)
    except (TypeError, ValueError):
        return str(v).strip()
