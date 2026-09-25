"""Performance Comercial — nota 0–10 por vendedor (item 6 do pedido de 09/2026).
Funções puras — sem Flask/DAX/DB. Testes em tests/test_performance_comercial.py.
Plano: docs/comercial/PLANO_MELHORIAS_COMERCIAL.md §3.4. Padrão copiado (não importado) do
estoque/nota.py (Performance de Compras): o Compras é módulo opcional por instância.

Pesos do cliente (editáveis no Admin, gravados por COMPETÊNCIA): Rentabilidade 35 · Cobertura 25
· Mix 20 · Receita 10 · Frequência 10.

Como cada indicador é medido — e por quê (medido no BI em 24/09/2026):
- COBERTURA: cobertura da base (positivacao.py) AJUSTADA PELA CURVA ABC = positivados ÷
  esperados, onde esperado = base de cada classe × positivação da classe no universo. Sem o
  ajuste, carteira cheia de clientes C (positivação ~25%) perde para carteira de A (~82%) sem
  diferença de trabalho. 1,0 = a média do universo.
- RENTABILIDADE, RECEITA, MIX: % de ATINGIMENTO da meta do Metas (lucro, venda, mix). Valores
  absolutos medem território: a margem da BA roda ~4 p.p. acima e a meta de rentabilidade já
  embute isso (BA 24% × ES 19,5% da meta de venda). Margem das Metas = pelo BRUTO (a régua delas).
  Sem meta cadastrada → indicador ausente e a nota sai PARCIAL e RENORMALIZADA (padrão Compras).
- FREQUÊNCIA: pedidos por cliente positivado no mês.
- Receita YoY mês a mês NÃO serve: a ordem dos vendedores de um mês para o outro concorda só
  0,24 (ruído). Por isso receita = atingimento de meta.

Escalas LINEARES p10 → 0 e p90 → 10, calibradas no histórico de 12 meses POR UNIVERSO (campo,
lojas, telemarketing não se comparam: lojas atendem 300–600 clientes de balcão) e CONGELADAS aqui
com versão — recalibrar é mudar NOTA_VERSAO, senão a nota muda sem ninguém mexer na operação.
Nada de percentil (jogo de soma zero: um sobe só se outro cai).

A nota NUNCA é gravada: recalcula do ingrediente (mês fechado).
"""

NOTA_VERSAO = 1

# Nota medida sobre menos que isso do peso não ranqueia (ex.: só cobertura = 25%). 35% é
# cobertura + frequência, o que existe mesmo sem meta cadastrada no Metas.
PESO_MINIMO = 0.35

PESOS_PADRAO = {'rentabilidade': 35, 'cobertura': 25, 'mix': 20, 'receita': 10, 'frequencia': 10}
INDICADORES = ('rentabilidade', 'cobertura', 'mix', 'receita', 'frequencia')

CAMPO, LOJAS, TELEMARKETING = 'campo', 'lojas', 'telemarketing'
UNIVERSOS = (CAMPO, LOJAS, TELEMARKETING)

# Atingimento de meta: PROVISÓRIO (70% → 0, 110% → 10) até medir a distribuição real no banco
# de produção (o local só tem jun/26). A tela declara "escala provisória".
_ATING = (0.70, 1.10)

# (p10, p90) medidos em 12 meses fechados (set/25–ago/26). Telemarketing não tinha amostra
# suficiente: usa a do campo, marcada como provisória.
ESCALAS = {
    CAMPO:         {'cobertura': (0.76, 1.34), 'frequencia': (1.50, 3.51),
                    'rentabilidade': _ATING, 'receita': _ATING, 'mix': _ATING},
    LOJAS:         {'cobertura': (0.92, 1.54), 'frequencia': (1.80, 6.80),
                    'rentabilidade': _ATING, 'receita': _ATING, 'mix': _ATING},
    TELEMARKETING: {'cobertura': (0.76, 1.34), 'frequencia': (1.50, 3.51),
                    'rentabilidade': _ATING, 'receita': _ATING, 'mix': _ATING},
}
ESCALAS_PROVISORIAS = {(u, k) for u in UNIVERSOS for k in ('rentabilidade', 'receita', 'mix')} | \
                      {(TELEMARKETING, 'cobertura'), (TELEMARKETING, 'frequencia')}


def universo_de(tipovend):
    """TIPOVEND do Winthor → universo. I = interno/loja, E = externo de telemarketing."""
    t = (tipovend or '').upper()
    if t == 'I':
        return LOJAS
    if t == 'E':
        return TELEMARKETING
    return CAMPO


def escala(valor, lo, hi):
    """Linear lo → 0, hi → 10, com trava nas pontas. None se não medido."""
    if valor is None:
        return None
    if hi <= lo:
        return 10.0 if valor >= hi else 0.0
    return max(0.0, min(10.0, (valor - lo) / (hi - lo) * 10))


def atingimento(realizado, meta):
    """realizado ÷ meta; None sem meta (> 0)."""
    if meta is None or meta <= 0 or realizado is None:
        return None
    return realizado / meta


def cobertura_ajustada(bases, atendidos, classes, universo_rca):
    """{codusur: {obs, esperado, indice}} — cobertura da base ajustada pela curva ABC.

    bases:        {codusur: set(codcli)} — base ativa (positivacao.base_ativa)
    atendidos:    {codusur: set(codcli)} — clientes que ELE atendeu no mês
    classes:      {codcli: 'A'|'B'|'C'} — classe do INÍCIO do mês (positivacao.classes_abc)
    universo_rca: {codusur: universo}
    A taxa esperada de cada classe é a do UNIVERSO (Σ cobertos ÷ Σ base, só clientes com classe).
    """
    tot = {}
    for u, base in bases.items():
        un = universo_rca.get(u, CAMPO)
        meus = atendidos.get(u, set())
        for c in base:
            k = classes.get(c)
            if k is None:
                continue
            t = tot.setdefault((un, k), [0, 0])
            t[0] += 1
            t[1] += 1 if c in meus else 0
    taxa = {key: (p / n) if n else None for key, (n, p) in tot.items()}
    out = {}
    for u, base in bases.items():
        un = universo_rca.get(u, CAMPO)
        meus = atendidos.get(u, set())
        obs = esp = 0.0
        for c in base:
            k = classes.get(c)
            if k is None or taxa.get((un, k)) is None:
                continue
            esp += taxa[(un, k)]
            obs += 1 if c in meus else 0
        out[u] = {'obs': int(obs), 'esperado': round(esp, 2), 'indice': (obs / esp) if esp else None}
    return out


def pesos_vigentes(historico, anomes):
    """Pesos da competência: o registro mais recente com competência <= anomes; senão o padrão.
    historico = {'AAAAMM': {indicador: peso}}. Mudar o foco em novembro NÃO reescreve setembro."""
    validas = sorted(int(k) for k in (historico or {}) if int(k) <= anomes)
    if not validas:
        return dict(PESOS_PADRAO), None
    comp = validas[-1]
    p = historico[str(comp)] if str(comp) in historico else historico[comp]
    return {k: float(p.get(k, 0)) for k in INDICADORES}, comp


def validar_pesos(pesos):
    """Erro (str) ou None. Soma 100, cada um entre 0 e 100, só os 5 indicadores."""
    if set(pesos) - set(INDICADORES):
        return 'indicador desconhecido'
    try:
        vals = [float(pesos.get(k, 0)) for k in INDICADORES]
    except (TypeError, ValueError):
        return 'peso inválido'
    if any(v < 0 or v > 100 for v in vals):
        return 'cada peso entre 0 e 100'
    if abs(sum(vals) - 100) > 0.01:
        return 'os pesos precisam somar 100'
    return None


def nota(valores, universo, pesos):
    """Nota 0–10 PARCIAL e RENORMALIZADA: divide pelo peso efetivamente medido.
    Indicador sem valor (sem meta, sem base) sai da conta e da tela como pendente.
    Sem nenhum indicador medido → nota None."""
    esc = ESCALAS.get(universo, ESCALAS[CAMPO])
    notas, num, den = {}, 0.0, 0.0
    for k in INDICADORES:
        s = escala(valores.get(k), *esc[k])
        notas[k] = None if s is None else round(s, 2)
        w = float(pesos.get(k, 0))
        if s is not None and w > 0:
            num += s * w
            den += w
    total = sum(float(pesos.get(k, 0)) for k in INDICADORES) or 100.0
    suficiente = den / total >= PESO_MINIMO - 1e-9
    return {
        'nota': round(num / den, 2) if den and suficiente else None,
        'parcial': den < total - 1e-9,
        'peso_medido': round(den / total, 4),
        'faltando': [k for k in INDICADORES if notas[k] is None and float(pesos.get(k, 0)) > 0],
        'notas': notas,
    }


def ranquear(linhas):
    """Ordena por universo e nota desc (None por último) e grava `posicao` dentro do universo."""
    out = sorted(linhas, key=lambda l: (UNIVERSOS.index(l['universo']) if l['universo'] in UNIVERSOS else 9,
                                        l['nota'] is None, -(l['nota'] or 0)))
    pos = {}
    for l in out:
        if l['nota'] is None:
            l['posicao'] = None
            continue
        pos[l['universo']] = pos.get(l['universo'], 0) + 1
        l['posicao'] = pos[l['universo']]
    return out
