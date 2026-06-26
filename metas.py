"""Módulo puro de Metas — sem Flask, sem DAX, sem DB. Só matemática.
Espelha o estilo de rfm.py / cohort.py: 100% unit-testável.

Cobre as 4 métricas das telas META do cliente: venda, rentabilidade, clientes, mix.
Projeção e necessidade/dia usam DIAS ÚTEIS (dias de meta), não dias corridos.

Regras decifradas contra as medidas oficiais do dataset META (centavo-a-centavo):
- Projeção  = realizado × DiasMetaMes / DiasMetaDecorridos   (run-rate por dia útil)
- Falta     = max(0, Meta − Realizado)
- Necess/dia= Falta / DiasMetaRestantes
- % Realiz. = Realizado / Meta ; % Proj.Meta = Projeção / Meta

IMPORTANTE: clientes/mix são DISTINCTCOUNT — NÃO somam vendedor→supervisor→total
(um cliente atendido por 2 vendedores conta 1× no total). O realizado de clientes/mix
deve vir medido em cada grão (vendedor, supervisor, total) separadamente; este módulo
nunca soma realizado de clientes/mix. Só `valor`/`rentabilidade` (e as metas) são aditivos.
"""

METRICAS = ('venda', 'clientes', 'mix', 'rentabilidade')

# Métricas cujo realizado/meta podem ser somados em rollups (aditivas).
# clientes/mix são distinct → fora desta lista.
METRICAS_ADITIVAS = ('venda', 'rentabilidade')


def _num(v):
    """Converte pra float seguro (None/'' → 0.0)."""
    if v is None or v == '':
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def projecao(realizado, dias_mes, dias_decorridos):
    """Run-rate por dia útil: realizado × dias_mes / dias_decorridos.
    Se ainda não houve dia decorrido, projeção = realizado (sem extrapolar)."""
    r = _num(realizado)
    dm = _num(dias_mes)
    dd = _num(dias_decorridos)
    if dd <= 0:
        return r
    return r * dm / dd


def falta(meta, realizado):
    """Quanto falta pra meta (nunca negativo)."""
    return max(0.0, _num(meta) - _num(realizado))


def necessidade_dia(meta, realizado, dias_restantes):
    """Quanto precisa por dia útil restante pra fechar a meta.
    Mês encerrado (0 dias restantes) → 0."""
    dr = _num(dias_restantes)
    if dr <= 0:
        return 0.0
    return falta(meta, realizado) / dr


def pct(numerador, denominador):
    """Razão segura. Denominador 0/ausente → None (frontend mostra '—')."""
    d = _num(denominador)
    if d == 0:
        return None
    return _num(numerador) / d


def linha_metrica(meta, realizado, dias_mes, dias_decorridos, dias_restantes, projecao_oficial=None):
    """Monta a linha completa de UMA métrica de UM escopo (vendedor/supervisor/total).
    `projecao_oficial`: se fornecida (medida [Projecao] do dataset, mês corrente), usa ela —
    bate centavo com o BI (que projeta o realizado bruto, com bônus). Senão recalcula (run-rate)."""
    meta_v = _num(meta)
    real_v = _num(realizado)
    proj = _num(projecao_oficial) if projecao_oficial is not None else projecao(real_v, dias_mes, dias_decorridos)
    return {
        'meta':          meta_v,
        'realizado':     real_v,
        'falta':         falta(meta_v, real_v),
        'necessidade_dia': necessidade_dia(meta_v, real_v, dias_restantes),
        'projecao':      proj,
        'pct_realizado': pct(real_v, meta_v),
        'pct_projecao':  pct(proj, meta_v),
    }


def somar_aditivo(linhas, campo):
    """Soma um campo numérico de uma lista de dicts (pra rollups de venda/rentabilidade)."""
    return sum(_num(l.get(campo)) for l in linhas)


# ── Sugestão de meta ───────────────────────────────────────────────────────
def _chave_anomes(ano, mes):
    return ano * 100 + mes


def _mes_anterior(ano, mes, n=1):
    """Retorna (ano, mes) n meses antes."""
    idx = (ano * 12 + (mes - 1)) - n
    return idx // 12, idx % 12 + 1


def sugerir(historico, ano, mes, metodo='ano_anterior', crescimento=0.0):
    """Sugere o valor de meta pra (ano, mes) a partir do histórico de realizados.

    historico: dict {anomes_int: valor_realizado}  (ex.: {202506: 50000.0})
    metodo:
      - 'ano_anterior': mesmo mês do ano anterior × (1 + crescimento)
      - 'media_3m'    : média dos 3 meses imediatamente anteriores × (1 + crescimento)
    crescimento: fração (0.10 = +10%).

    Retorna float arredondado ou None se não houver base suficiente.
    """
    h = {int(k): _num(v) for k, v in (historico or {}).items()}
    fator = 1.0 + _num(crescimento)

    if metodo == 'ano_anterior':
        base = h.get(_chave_anomes(ano - 1, mes))
        if base is None:
            return None
        return round(base * fator, 2)

    if metodo == 'media_3m':
        vals = []
        for n in range(1, 4):
            a, m = _mes_anterior(ano, mes, n)
            v = h.get(_chave_anomes(a, m))
            if v is not None:
                vals.append(v)
        if not vals:
            return None
        return round(sum(vals) / len(vals) * fator, 2)

    return None
