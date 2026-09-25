"""Potencial de positivação ("dinheiro na mesa", camada b). Funções puras — sem Flask/DAX/DB.
Testes em tests/test_potencial.py. Plano: docs/comercial/PLANO_MELHORIAS_COMERCIAL.md §3.2.

Por que não é a fórmula do pedido: "clientes × ticket médio × % positivação média" é IDENTIDADE
— medido em ago/26: 6.935 × 43,2% × R$ 2.164 = R$ 6.476.352 = exatamente a venda. Potencial só
existe contra uma REFERÊNCIA. Aqui ela é o percentil 75 (default) da positivação dos RCAs do
MESMO universo (campo ≠ loja ≠ telemarketing) em cada classe ABC de cliente — "o que 1 em cada
4 vendedores já faz". Medido em ago/26: média R$ 0,54 mi · p75 R$ 1,06 mi · p90 R$ 1,46 mi/mês.

Sem dupla contagem: o cliente EM RISCO/PERDIDO já está na camada a (recuperacao.py) e fica
FORA desta conta — denominador e referência incluídos. Cada real entra numa camada só.
Grupo RCA×classe com menos de `min_clientes` não gera gap (é ruído) nem entra na referência.
"""

PERCENTIL_REF = 0.75
MIN_CLIENTES = 10


def percentil(valores, p):
    """Percentil com interpolação linear (p em 0..1). None se vazio."""
    xs = sorted(valores)
    if not xs:
        return None
    k = (len(xs) - 1) * p
    i = int(k)
    j = min(i + 1, len(xs) - 1)
    return xs[i] + (xs[j] - xs[i]) * (k - i)


def gap_positivacao(linhas, excluir=(), p_ref=PERCENTIL_REF, min_clientes=MIN_CLIENTES):
    """Gap de positivação por RCA (dono do cadastro) no mês.

    Args:
        linhas: [{codcli, dono, universo, classe, comprou, ticket}] — a base ativa do mês.
                `ticket` = venda média do cliente nos meses em que comprou (R$/mês).
        excluir: codclis já contados na camada a (em risco/perdido).

    Returns:
        {'referencia': {(universo, classe): p_ref}, 'por_rca': {dono: {'gap': R$, 'classes':
         {classe: {n, positivados, positivacao, referencia, gap}}}}, 'total': R$}
    """
    excluir = set(excluir)
    grupos = {}
    for l in linhas:
        if l['codcli'] in excluir or l.get('dono') is None:
            continue
        g = grupos.setdefault((l['dono'], l.get('universo'), l['classe']),
                              {'n': 0, 'pos': 0, 'ticket': 0.0})
        g['n'] += 1
        g['pos'] += 1 if l.get('comprou') else 0
        g['ticket'] += l.get('ticket') or 0.0

    amostras = {}
    for (dono, univ, classe), g in grupos.items():
        if g['n'] >= min_clientes:
            amostras.setdefault((univ, classe), []).append(g['pos'] / g['n'])
    referencia = {k: percentil(v, p_ref) for k, v in amostras.items()}

    por_rca, total = {}, 0.0
    for (dono, univ, classe), g in grupos.items():
        n = g['n']
        p = g['pos'] / n
        ref = referencia.get((univ, classe))
        gap = 0.0
        if n >= min_clientes and ref is not None and ref > p:
            gap = (ref - p) * n * (g['ticket'] / n)
        r = por_rca.setdefault(dono, {'gap': 0.0, 'classes': {}})
        r['classes'][classe] = {'n': n, 'positivados': g['pos'], 'positivacao': p,
                                'referencia': ref, 'gap': round(gap, 2)}
        r['gap'] += gap
        total += gap
    for r in por_rca.values():
        r['gap'] = round(r['gap'], 2)
    return {'referencia': referencia, 'por_rca': por_rca, 'total': round(total, 2)}
