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
MESES = 12          # janela da curva (casa com o produtos_map do Radar, também 12m)

# Abaixo disto a curva é ruído: E-COMMERCE tinha 266 produtos e R$ 42 mil em 12m no BI real —
# "curva A" de 5 itens não orienta ninguém. A tela avisa, não esconde (mesma política do
# `lead_confiavel` do Compras: número com aviso, nunca número seco).
AMOSTRA_MIN_PRODUTOS = 200
AMOSTRA_MIN_VENDA = 100_000.0


def classificar(itens, chave='venda', corte_a=CORTE_A, corte_b=CORTE_B):
    """Devolve NOVA lista ordenada por `chave` desc, cada item com `rank`, `pct` (% da venda),
    `pct_acum` e `classe`. Não muta a entrada. Venda ≤ 0 → classe C, pct 0.

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
    return ordenados


def resumo(itens, chave='venda'):
    """KPIs da tela: por classe → {qt, venda, pct_venda}; total; concentração (% dos itens que
    fazem `CORTE_A`% da venda). Espera itens já classificados."""
    total_venda = sum(i.get(chave) or 0 for i in itens if (i.get(chave) or 0) > 0)
    por_classe = {}
    for c in ('A', 'B', 'C'):
        sel = [i for i in itens if i.get('classe') == c]
        venda = sum(i.get(chave) or 0 for i in sel if (i.get(chave) or 0) > 0)
        por_classe[c] = {
            'qt': len(sel),
            'venda': round(venda, 2),
            'pct_venda': round(venda / total_venda * 100, 1) if total_venda else 0.0,
        }
    n = len(itens)
    return {
        'total_produtos': n,
        'total_venda': round(total_venda, 2),
        'classes': por_classe,
        # "X% dos itens fazem 80% da venda" — o número que a gerente repete na reunião
        'concentracao_pct_itens': round(por_classe['A']['qt'] / n * 100, 1) if n else 0.0,
    }


def amostra_confiavel(itens, chave='venda'):
    """(ok, motivo). Falso quando o escopo é pequeno demais para a curva significar algo."""
    n = len(itens)
    venda = sum(i.get(chave) or 0 for i in itens if (i.get(chave) or 0) > 0)
    if n < AMOSTRA_MIN_PRODUTOS:
        return False, f'{n} produtos no escopo (mínimo {AMOSTRA_MIN_PRODUTOS})'
    if venda < AMOSTRA_MIN_VENDA:
        return False, f'venda de R$ {venda:,.0f} no escopo (mínimo R$ {AMOSTRA_MIN_VENDA:,.0f})'
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
