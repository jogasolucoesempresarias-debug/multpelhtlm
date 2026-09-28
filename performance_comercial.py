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
- RENTABILIDADE, RECEITA, MIX: % de ATINGIMENTO da meta do Metas (lucro, venda, mix), pontuado
  nas FAIXAS do cliente (ver ATINGIMENTO abaixo — não é a escala linear p10/p90). Valores
  absolutos medem território: a margem da BA roda ~4 p.p. acima e a meta de rentabilidade já
  embute isso (BA 24% × ES 19,5% da meta de venda). Margem das Metas = pelo BRUTO (a régua delas).
  Sem meta cadastrada → indicador ausente e a nota sai PARCIAL e RENORMALIZADA (padrão Compras).
- FREQUÊNCIA: pedidos por cliente positivado no mês.
- Receita YoY mês a mês NÃO serve: a ordem dos vendedores de um mês para o outro concorda só
  0,24 (ruído). Por isso receita = atingimento de meta.

Cobertura e frequência: escalas LINEARES p10 → 0 e p90 → 10, calibradas no histórico de 12 meses POR UNIVERSO (campo,
lojas, telemarketing não se comparam: lojas atendem 300–600 clientes de balcão) e CONGELADAS aqui
com versão — recalibrar é mudar NOTA_VERSAO, senão a nota muda sem ninguém mexer na operação.
Nada de percentil (jogo de soma zero: um sobe só se outro cai).

A nota NUNCA é gravada: recalcula do ingrediente (mês fechado).
"""

# v2 (28/09/2026): escala de atingimento de meta em FAIXAS, definida pelo João (era linear 70→110).
NOTA_VERSAO = 2

# Nota medida sobre menos que isso do peso não ranqueia (ex.: só cobertura = 25%). 35% é
# cobertura + frequência, o que existe mesmo sem meta cadastrada no Metas.
PESO_MINIMO = 0.35

PESOS_PADRAO = {'rentabilidade': 35, 'cobertura': 25, 'mix': 20, 'receita': 10, 'frequencia': 10}
INDICADORES = ('rentabilidade', 'cobertura', 'mix', 'receita', 'frequencia')

CAMPO, LOJAS, TELEMARKETING = 'campo', 'lojas', 'telemarketing'
UNIVERSOS = (CAMPO, LOJAS, TELEMARKETING)

# Atingimento de meta (rentabilidade, receita e mix) — escala em FAIXAS do cliente (João,
# 28/09/2026), a mesma para os três indicadores e os três universos:
#   < 85% → 0 · 85% a 89,99% → rampa 6 → 7 · 90% a 99,99% → 9 · ≥ 100% → 10
# Substituiu a linear provisória 70% → 0 / 110% → 10, em que bater a meta valia 7,5 ("tem
# vendedor com 100% da meta de rentabilidade e nota 7,6"). É regra de META (bateu / quase /
# não bateu), não de dispersão: 90% e 99% valem o mesmo de propósito. Os degraus não oscilam
# sozinhos porque a meta é cadastrada e o mês é fechado (≠ Compras, cuja meta anda com a venda).
# ⚠️ O salto em 85% (0 → 6) é decisão dele, com o custo declarado: rentabilidade pesa 35, então
# 84,9% × 85% são ~2,1 pontos na nota final.
ATINGIMENTO = ('rentabilidade', 'receita', 'mix')
ATING_PISO, ATING_QUASE, ATING_META = 0.85, 0.90, 1.00
ATING_RAMPA = (6.0, 7.0)          # nota em 85% → nota "morrendo" em 89,99%
ESCALA_ATINGIMENTO_PUBLICA = [    # para a tela/IA descreverem a régua sem reimplementá-la
    {'de': None, 'ate': ATING_PISO, 'nota': 0},
    {'de': ATING_PISO, 'ate': ATING_QUASE, 'nota': list(ATING_RAMPA)},
    {'de': ATING_QUASE, 'ate': ATING_META, 'nota': 9},
    {'de': ATING_META, 'ate': None, 'nota': 10},
]

# (p10, p90) medidos em 12 meses fechados (set/25–ago/26). Telemarketing não tinha amostra
# suficiente: usa a do campo, marcada como provisória.
ESCALAS = {
    CAMPO:         {'cobertura': (0.76, 1.34), 'frequencia': (1.50, 3.51)},
    LOJAS:         {'cobertura': (0.92, 1.54), 'frequencia': (1.80, 6.80)},
    TELEMARKETING: {'cobertura': (0.76, 1.34), 'frequencia': (1.50, 3.51)},
}
ESCALAS_PROVISORIAS = {(TELEMARKETING, 'cobertura'), (TELEMARKETING, 'frequencia')}


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


def nota_atingimento(a):
    """Nota 0–10 do % de atingimento de meta, nas faixas do cliente. None se não medido.
    Arredonda a 6 casas antes de comparar: 180.000 ÷ 200.000 tem de cair em "90%", não em 89,99…
    por resíduo de ponto flutuante — a tela trunca na mesma casa."""
    if a is None:
        return None
    a = round(a, 6)
    if a >= ATING_META:
        return 10.0
    if a >= ATING_QUASE:
        return 9.0
    if a >= ATING_PISO:
        lo, hi = ATING_RAMPA
        return lo + (a - ATING_PISO) / (ATING_QUASE - ATING_PISO) * (hi - lo)
    return 0.0


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
        # `por_classe` é só EXPLICAÇÃO do índice (a tela ignora; a nota não usa): a pergunta real
        # "por que o JULIANO tem 10 se a cobertura dele é baixa?" (João, 25/09/2026) só se responde
        # mostrando o mix A/B/C da base e quantos de cada ele atendeu contra a taxa do universo.
        por_classe = {}
        for c in base:
            k = classes.get(c)
            if k is None or taxa.get((un, k)) is None:
                continue
            esp += taxa[(un, k)]
            obs += 1 if c in meus else 0
            pc = por_classe.setdefault(k, {'base': 0, 'atendidos': 0, 'taxa_universo': round(taxa[(un, k)], 4)})
            pc['base'] += 1
            pc['atendidos'] += 1 if c in meus else 0
        out[u] = {'obs': int(obs), 'esperado': round(esp, 2), 'indice': (obs / esp) if esp else None,
                  'por_classe': por_classe}
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
        s = nota_atingimento(valores.get(k)) if k in ATINGIMENTO else escala(valores.get(k), *esc[k])
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


def classificavel(l):
    """Só nota COMPLETA disputa posição (João, 28/09/2026: "quando faltar nota, deixar sem
    classificação — vale para todos"). A parcial segue calculada e visível, mas fora do ranking:
    a Ellen, com só cobertura + frequência (35% do peso), estava em 7º de 46."""
    return l['nota'] is not None and not l.get('parcial')


def ranquear(linhas):
    """Ordena por universo, classificáveis antes, nota desc; grava `posicao` dentro do universo
    (None para nota parcial ou ausente — esses vão para o fim, parciais antes dos sem nota)."""
    out = sorted(linhas, key=lambda l: (UNIVERSOS.index(l['universo']) if l['universo'] in UNIVERSOS else 9,
                                        not classificavel(l), l['nota'] is None, -(l['nota'] or 0)))
    pos = {}
    for l in out:
        if not classificavel(l):
            l['posicao'] = None
            continue
        pos[l['universo']] = pos.get(l['universo'], 0) + 1
        l['posicao'] = pos[l['universo']]
    return out
