"""Lógica RFM da carteira. Funções puras, sem dependência de Flask/DAX/DB.
Testes em tests/test_rfm.py validam matematicamente cada função.

Glossário:
- R (Recência): dias desde a última compra. Quanto menor, melhor → quintil 5.
- F (Frequência): número de compras nos últimos 12m. Quanto maior, melhor → quintil 5.
- M (Monetário): lucro nos últimos 12m. Quanto maior, melhor → quintil 5.
- Régua FIXA: thresholds absolutos da planilha original do cliente (10/30/45 dias).
- Régua PERSONALIZADA: dias_sem_comprar / ciclo_pessoal — sensível ao padrão de cada cliente.
"""
import statistics
from datetime import date
from typing import Iterable

REGUA_FIXA = {'ok': 10, 'normal': 30, 'atencao': 45}  # > 45 = URGENTE
CICLO_PESSOAL_FLOOR_DIAS = 7

SEGMENTOS_ORDEM = [
    'champions', 'loyal', 'cant_lose', 'at_risk',
    'new', 'potential_loyalist', 'lost', 'hibernating',
]

STATUS_ORDEM = ['ok', 'normal', 'atencao', 'urgente']


def ciclo_pessoal(datas):
    """Mediana de intervalos entre compras consecutivas. Floor 7.
    Aceita lista de strings ISO (YYYY-MM-DD ou YYYY-MM-DDTHH:MM:SS) ou date objects.
    Retorna None se < 2 datas distintas (sem como medir intervalo)."""
    if not datas or len(datas) < 2:
        return None
    parsed = []
    for d in datas:
        if isinstance(d, date):
            parsed.append(d)
        else:
            parsed.append(date.fromisoformat(str(d)[:10]))
    parsed.sort()
    intervalos = [(parsed[i + 1] - parsed[i]).days for i in range(len(parsed) - 1)]
    intervalos = [i for i in intervalos if i > 0]
    if not intervalos:
        return None
    return max(CICLO_PESSOAL_FLOOR_DIAS, int(statistics.median(intervalos)))


def status_regua_fixa(dias_sem_comprar):
    """4 status conforme planilha original do cliente."""
    if dias_sem_comprar is None or dias_sem_comprar < 0:
        return 'urgente'
    if dias_sem_comprar <= REGUA_FIXA['ok']:
        return 'ok'
    if dias_sem_comprar <= REGUA_FIXA['normal']:
        return 'normal'
    if dias_sem_comprar <= REGUA_FIXA['atencao']:
        return 'atencao'
    return 'urgente'


def status_regua_personalizada(dias_sem_comprar, ciclo):
    """Razão dias/ciclo. Se ciclo=None (cliente com 1 compra), fallback pra fixa."""
    if ciclo is None or ciclo <= 0:
        return status_regua_fixa(dias_sem_comprar)
    if dias_sem_comprar is None or dias_sem_comprar < 0:
        return 'urgente'
    razao = dias_sem_comprar / ciclo
    if razao < 1.0:
        return 'ok'
    if razao < 2.0:
        return 'normal'
    if razao < 3.0:
        return 'atencao'
    return 'urgente'


def lucro_perdido_projetado(lucro_12m, dias_sem_comprar, ciclo):
    """Lucro mensal médio × meses atrasado (não negativo). Floor 0.
    Cliente ainda dentro do ciclo dele = 0. Sem ciclo (1 compra) = 0."""
    if lucro_12m is None or lucro_12m <= 0 or ciclo is None or dias_sem_comprar is None:
        return 0.0
    if dias_sem_comprar < ciclo:
        return 0.0
    meses_atrasado = (dias_sem_comprar - ciclo) / 30
    return (lucro_12m / 12) * max(0.0, meses_atrasado)


def receita_perdida_projetada(venda_12m, dias_sem_comprar, ciclo):
    """Mesma fórmula do lucro_perdido_projetado mas usando venda líquida 12m.
    Diretor da Multpel pensa em receita primeiro (lucro é decisão do comercial)."""
    if venda_12m is None or venda_12m <= 0 or ciclo is None or dias_sem_comprar is None:
        return 0.0
    if dias_sem_comprar < ciclo:
        return 0.0
    meses_atrasado = (dias_sem_comprar - ciclo) / 30
    return (venda_12m / 12) * max(0.0, meses_atrasado)


def quintis(valores):
    """Retorna 4 cutoffs Q1..Q4 (5o quintil é tudo acima de Q4).
    Ignora None. Usado pra mapear cada cliente em quintil 1-5 via quintil_de().
    Pra valores 1..100, retorna [20, 40, 60, 80]."""
    vals = sorted(v for v in valores if v is not None)
    if not vals:
        return [0, 0, 0, 0]
    n = len(vals)
    return [vals[max(0, min(n - 1, n * q // 5 - 1))] for q in (1, 2, 3, 4)]


def quintil_de(valor, cutoffs, invertido=False):
    """Mapeia valor em quintil 1-5.
    invertido=True pra Recência (menor dias = quintil maior = melhor)."""
    if valor is None:
        return 1
    q = 1
    for c in cutoffs:
        if valor > c:
            q += 1
    if invertido:
        q = 6 - q
    return max(1, min(5, q))


def segmento_canonico(r, f, m):
    """8 segmentos. Ordem de prioridade (first match wins) evita overlap."""
    if r == 5 and f == 5 and m == 5:
        return 'champions'
    if r >= 4 and f >= 4 and m >= 3:
        return 'loyal'
    if 2 <= r <= 3 and f >= 4 and m >= 4:
        return 'cant_lose'
    if 2 <= r <= 3 and f >= 3 and m >= 3:
        return 'at_risk'
    if r == 5 and f == 1:
        return 'new'
    if r >= 4 and 1 <= f <= 3:
        return 'potential_loyalist'
    if r <= 2 and f <= 2 and m <= 2:
        return 'lost'
    return 'hibernating'


def calcular_clientes(snapshot, datas_por_cliente, meta_por_cliente):
    """Entrada-saída completa de RFM. Pura — sem I/O.

    Args:
        snapshot: lista de dicts {CODCLI, DiasSemComprar, Compras12m, Lucro12m, Venda12m, UltimaCompra}
        datas_por_cliente: dict {codcli: [datas_iso]}
        meta_por_cliente: dict {codcli: {cliente, fantasia, cidade, uf, telefone, codusur1, bloqueio}}

    Returns:
        lista de dicts com TODOS os campos calculados (R/F/M, segmento, status_fixa,
        status_personalizada, lucro_perdido_proj, ciclo_pessoal, etc).
    """
    # Quintis:
    # - R: sobre toda a base (inativos legitimamente caem em R baixo).
    # - F e M: SÓ sobre clientes ATIVOS (Compras12m >= 1). Antes os inativos
    #   (Compras12m=0, ~20% da base) inflavam o quintil F=1 e impossibilitavam
    #   (R=5, F=1) → segmento NEW sempre zerado. Inativos agora recebem F=1, M=1
    #   atribuídos diretamente — caem em 'lost' pela combinação R baixo + F=1 + M=1.
    cuts_r = quintis(c.get('DiasSemComprar') for c in snapshot)
    ativos = [c for c in snapshot if (c.get('Compras12m') or 0) >= 1]
    cuts_f = quintis(c.get('Compras12m') for c in ativos)
    cuts_m = quintis(c.get('Lucro12m')   for c in ativos)

    resultado = []
    for c in snapshot:
        codcli = c.get('CODCLI')
        dias = c.get('DiasSemComprar') or 0
        compras = c.get('Compras12m') or 0
        lucro = c.get('Lucro12m') or 0.0
        venda = c.get('Venda12m') or 0.0

        ciclo = ciclo_pessoal(datas_por_cliente.get(codcli, []))
        r = quintil_de(dias, cuts_r, invertido=True)
        if compras >= 1:
            f = quintil_de(compras, cuts_f)
            m = quintil_de(lucro,   cuts_m)
        else:
            # Inativo (Compras12m=0): atribui F=1, M=1 sem passar pelo quintil distorcido
            f, m = 1, 1
        seg = segmento_canonico(r, f, m)
        s_fixa = status_regua_fixa(dias)
        s_pers = status_regua_personalizada(dias, ciclo)
        lp = lucro_perdido_projetado(lucro, dias, ciclo)
        rp = receita_perdida_projetada(venda, dias, ciclo)

        meta = meta_por_cliente.get(codcli, {})
        resultado.append({
            'codcli':                codcli,
            'cliente':               meta.get('cliente') or f'Cliente #{codcli}',
            'cidade':                meta.get('cidade'),
            'uf':                    meta.get('uf'),
            'codusur':               meta.get('codusur1'),
            'telefone':              meta.get('telefone'),
            'bloqueio':              meta.get('bloqueio'),
            'recencia_dias':         dias,
            'frequencia_12m':        compras,
            'lucro_12m':             lucro,
            'venda_12m':             venda,
            'ciclo_pessoal':         ciclo,
            'lucro_perdido_proj':    round(lp, 2),
            'receita_perdida_proj':  round(rp, 2),
            'status_fixa':           s_fixa,
            'status_personalizada':  s_pers,
            'segmento':              seg,
            'r':                     r,
            'f':                     f,
            'm':                     m,
            'ultima_compra':         str(c.get('UltimaCompra', ''))[:10] or None,
        })
    return resultado


def agregar_distribuicoes(clientes, modo='personalizada'):
    """Conta clientes por status (régua) e por segmento canônico."""
    regua = {s: 0 for s in STATUS_ORDEM}
    segmentos = {s: 0 for s in SEGMENTOS_ORDEM}
    status_key = 'status_personalizada' if modo == 'personalizada' else 'status_fixa'
    for c in clientes:
        regua[c[status_key]] = regua.get(c[status_key], 0) + 1
        segmentos[c['segmento']] = segmentos.get(c['segmento'], 0) + 1
    total = len(clientes)
    ok_normal = regua['ok'] + regua['normal']
    return {
        'regua':     {**regua, 'pct_ok_normal': (ok_normal / total) if total else 0},
        'segmentos': segmentos,
        'total_clientes': total,
    }


def matriz_rf(clientes):
    """Pra cada célula (r, f) ∈ {1..5}×{1..5}: count + m_avg + segmento_principal.

    segmento_principal = segmento modal (mais comum) dos clientes daquela célula.
    Como segmentos dependem de R/F/M, uma mesma (R,F) pode misturar segmentos diferentes
    conforme o M de cada cliente. O modal dá a melhor cor pra UI (Chart.js bubble color).
    seg_share = fração desse segmento dominante (ex: 0.75 = 75% dos clientes na célula).
    """
    from collections import Counter
    celulas = {}
    for c in clientes:
        key = (c['r'], c['f'])
        bucket = celulas.setdefault(key, {
            'r': c['r'], 'f': c['f'], 'count': 0, 'm_sum': 0.0,
            'segmentos': Counter(),
        })
        bucket['count'] += 1
        bucket['m_sum'] += (c['lucro_12m'] or 0)
        bucket['segmentos'][c['segmento']] += 1
    out = []
    for cell in celulas.values():
        seg_modal, seg_count = cell['segmentos'].most_common(1)[0] if cell['segmentos'] else (None, 0)
        out.append({
            'r':                  cell['r'],
            'f':                  cell['f'],
            'count':              cell['count'],
            'm_avg':              round(cell['m_sum'] / cell['count'], 2) if cell['count'] else 0,
            'segmento_principal': seg_modal,
            'seg_share':          round(seg_count / cell['count'], 4) if cell['count'] else 0,
        })
    return sorted(out, key=lambda x: (x['r'], x['f']))


def histograma_recencia(clientes, bins=None):
    """Distribuição de dias-sem-comprar em bins. Default: 0-7, 8-15, 16-30, 31-60, 61-90, 91-180, 181+."""
    if bins is None:
        bins = [(0, 7), (8, 15), (16, 30), (31, 60), (61, 90), (91, 180), (181, 9999)]
    out = [{'bin': f'{lo}-{hi}' if hi < 9999 else f'{lo}+', 'count': 0} for lo, hi in bins]
    for c in clientes:
        d = c['recencia_dias']
        for i, (lo, hi) in enumerate(bins):
            if lo <= d <= hi:
                out[i]['count'] += 1
                break
    return out
