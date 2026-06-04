"""Cohort retention puro. Sem Flask/DAX/DB.

Modelo "clássico": cliente está retido em M+N se fez ao menos 1 compra NESSE mês
específico (não cumulative). Atacado B2B com ciclo 30-60d cabe bem em bucket mensal.

Testes em tests/test_cohort.py validam matematicamente.
"""
from datetime import date


def mes_iso(d):
    """Recebe string ISO ou date. Retorna 'YYYY-MM'."""
    if isinstance(d, date):
        return d.strftime('%Y-%m')
    return str(d)[:7]


def cohort_de_compras(compras_por_cliente):
    """Recebe {codcli: [datas_iso]}.
    Retorna dict {mes_aquisicao: {mes_relativo: set_de_codclis}}.

    mes_aquisicao = mês da PRIMEIRA compra do cliente (formato YYYY-MM).
    mes_relativo = 0 (mês da aquisição), 1 (mês seguinte), etc.

    Cliente retido em M+N <=> tem compra em mês_aquisicao + N meses.
    """
    if not compras_por_cliente:
        return {}

    cohorts = {}  # {mes_aquisicao: {mes_relativo: set(codcli)}}
    for codcli, datas in compras_por_cliente.items():
        if not datas:
            continue
        meses = sorted(set(mes_iso(d) for d in datas))
        if not meses:
            continue
        aq = meses[0]
        aq_y, aq_m = int(aq[:4]), int(aq[5:7])
        for m in meses:
            y, mo = int(m[:4]), int(m[5:7])
            rel = (y - aq_y) * 12 + (mo - aq_m)
            if rel < 0:
                continue
            bucket = cohorts.setdefault(aq, {})
            ids = bucket.setdefault(rel, set())
            ids.add(codcli)
    return cohorts


def matriz_cohort(cohorts_data, meses_max=12):
    """Transforma cohorts_data em lista de dicts prontos pro frontend.

    Args:
        cohorts_data: saída de cohort_de_compras
        meses_max: até M+N a calcular (default 12)

    Returns: lista ordenada por mes_aquisicao asc:
        [{"aquisicao": "2025-05", "tamanho": 340,
          "retencao": [1.0, 0.78, 0.62, ...],  # comprimento meses_max+1
          "absolutos": [340, 265, 211, ...]},
         ...]
    """
    if not cohorts_data:
        return []

    matriz = []
    for aq in sorted(cohorts_data.keys()):
        bucket = cohorts_data[aq]
        tamanho = len(bucket.get(0, set()))
        if tamanho == 0:
            continue
        retencao = []
        absolutos = []
        for n in range(meses_max + 1):
            count = len(bucket.get(n, set()))
            absolutos.append(count)
            retencao.append(round(count / tamanho, 4) if tamanho else 0.0)
        matriz.append({
            'aquisicao': aq,
            'tamanho':   tamanho,
            'retencao':  retencao,
            'absolutos': absolutos,
        })
    return matriz


def clientes_no_bucket(cohorts_data, aquisicao, mes_relativo):
    """Retorna list[codcli] dos clientes que estão NO bucket especificado.
    Útil pra drill por coorte."""
    if not cohorts_data:
        return []
    bucket = cohorts_data.get(aquisicao, {})
    return sorted(bucket.get(mes_relativo, set()))
