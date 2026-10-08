"""Evolução da carteira — histórico de performance do Comercial. Funções PURAS (sem Flask/DAX/DB).
Testes em tests/test_evolucao_carteira.py.

Pedido do Gabriel (05/10/2026): o histórico que a Gestão de Estoque tem, para a carteira — cobertura
da empresa, do time e do vendedor ao longo do tempo, cruzando com QUEM USA a plataforma.

⚠️ Diferente da foto do estoque, quase nada aqui precisa ser gravado. Venda é EVENTO datado: a
cobertura de março se recalcula hoje, pela mesma régua, a partir do fato. Gravar "cobertura = 62%"
congelaria a régua do dia — e mudar a régua viraria degrau no gráfico. Só o que o sistema
SOBRESCREVE vai para foto (o servidor cuida disso): dono do cliente (`carteira_foto`), vendedor →
time (`vendedor_foto`) e uso por pessoa (`uso_mensal`; o `multpel_log` é expurgado aos 12 meses).

Réguas:
- COBERTURA = `cobertura.cobertura_por_dono` no último dia de cada mês FECHADO (carteira ativa =
  cadastrados no RCA com compra em 365 d; positivado = comprou em 60 d, de qualquer vendedor). É a
  régua única do Gerencial/Performance/Vendedores: o último mês da série tem de bater com elas.
- RECUPERAÇÃO = `recuperacao.placar_de` do mês (em risco no fim, entraram, recuperados do risco),
  na carteira do DONO. SALDO DE RECUPERAÇÃO = recuperados − entraram: positivo = recuperou mais do
  que perdeu (06/10/2026 — antes era o inverso e o João lia o bom trabalho como negativo).
- Time agrega CONTAGENS (Σ positivados ÷ Σ base). Média de percentuais daria ao RCA de 5 clientes
  o mesmo peso do de 300.
- USO é do TIME: o time "usa" a partir do 1º mês (≥ marco) em que algum usuário ligado a ele teve
  ≥ `limiar` dias ativos. Supervisor liga pelas áreas (`codsupervisores`); vendedor, pelo time dele
  naquele mês. Diretoria (viewer/admin) não é de time nenhum. Só 4 vendedores têm login (10/2026),
  por isso a unidade é o time e não o vendedor.
- ANTES × DEPOIS = média do PERÍODO (contagens somadas nos meses), nunca o dia de início contra o
  dia de fim — o relatório de performance do estoque mostrou como o dia escolhido fabrica −46%.
"""
import calendar
from datetime import date

import cobertura as cob
import recuperacao as recup


# ───────────────────────── calendário ─────────────────────────
def mes_add(anomes, n):
    i = (anomes // 100) * 12 + (anomes % 100 - 1) + n
    return (i // 12) * 100 + i % 12 + 1


def meses_fechados(ref, n=12):
    """Os `n` meses que terminam em `ref` (inclusive), do mais antigo para o mais novo."""
    return [mes_add(ref, -i) for i in range(n - 1, -1, -1)]


def fim_do_mes(anomes):
    a, m = anomes // 100, anomes % 100
    return date(a, m, calendar.monthrange(a, m)[1])


# ───────────────────────── cobertura ─────────────────────────
def cobertura_mes(ultima, dono_de, time_de, data_ref):
    """{'rcas': {u: {base, positivados}}, 'times': {t: {base, positivados}}} na data. Time de
    vendedor desconhecido (fictício, desligado) cai em None — "Sem time", como na Recuperação."""
    por_rca = cob.cobertura_por_dono(ultima, dono_de, data_ref)
    rcas, times = {}, {}
    for u, g in por_rca.items():
        rcas[u] = {'base': g['base'], 'positivados': g['positivados']}
        t = times.setdefault(time_de.get(u), {'base': 0, 'positivados': 0})
        t['base'] += g['base']
        t['positivados'] += g['positivados']
    return {'rcas': rcas, 'times': times}


def recuperacao_mes(movs, dono_de, time_de):
    """Placar da Recuperação do mês, por RCA (dono) e por time do dono."""
    return recup.placar_de(movs, dono_de, time_de)


# ───────────────────────── linha do mês ─────────────────────────
def _div(a, b):
    return (a / b) if b else None


def linha(anomes, base, positivados, em_risco, valor_em_risco, entraram, recuperados, valor_saldo):
    """Um mês de um grupo (empresa, time ou vendedor). Percentuais saem das CONTAGENS."""
    return {'anomes': anomes, 'base': base, 'positivados': positivados,
            'cobertura': _div(positivados, base),
            'em_risco': em_risco, 'valor_em_risco': round(valor_em_risco or 0.0, 2),
            'pct_risco': _div(em_risco, base),
            'entraram': entraram, 'recuperados': recuperados, 'saldo': recuperados - entraram,
            'valor_saldo': round(valor_saldo or 0.0, 2)}


def linha_de(anomes, cob_g, rec_g):
    cob_g, rec_g = cob_g or {}, rec_g or {}
    return linha(anomes, cob_g.get('base', 0), cob_g.get('positivados', 0), rec_g.get('em_risco', 0),
                 rec_g.get('valor_em_risco', 0.0), rec_g.get('entraram', 0),
                 rec_g.get('rec_risco_da_base', 0), rec_g.get('valor_saldo', 0.0))


def somar(linhas, anomes):
    """Soma linhas do mesmo mês (grupo "usa", "não usa", empresa) recalculando os percentuais."""
    s = {k: 0 for k in ('base', 'positivados', 'em_risco', 'entraram', 'recuperados')}
    vr = vv = 0.0
    for ln in linhas:
        for k in s:
            s[k] += ln.get(k) or 0
        vr += ln.get('valor_em_risco') or 0.0
        vv += ln.get('valor_saldo') or 0.0
    return linha(anomes, s['base'], s['positivados'], s['em_risco'], vr, s['entraram'],
                 s['recuperados'], vv)


# ───────────────────────── uso → time ─────────────────────────
def uso_por_time(uso_rows, time_por_mes):
    """{anomes: {codsupervisor: {'dias_ativos': máx entre os usuários do time, 'usuarios': n}}}.
    `time_por_mes` = {anomes: {codusur: codsupervisor}} — o time do vendedor NAQUELE mês."""
    out = {}
    for r in uso_rows or []:
        am, dias = r.get('anomes'), r.get('dias_ativos') or 0
        if r.get('role') == 'supervisor':
            times = [int(t) for t in (r.get('codsupervisores') or []) if t is not None]
        elif r.get('role') == 'vendedor' and r.get('codusur') is not None:
            t = (time_por_mes.get(am) or {}).get(int(r['codusur']))
            times = [t] if t is not None else []
        else:
            times = []                           # diretoria/admin: não é de time
        for t in times:
            g = out.setdefault(am, {}).setdefault(t, {'dias_ativos': 0, 'usuarios': 0})
            g['dias_ativos'] = max(g['dias_ativos'], dias)
            g['usuarios'] += 1
    return out


def classificar_uso(uso_time, times, marco, limiar):
    """{time: {'usa', 'comecou_em'}} — começa a usar no 1º mês ≥ marco com ≥ limiar dias ativos.
    Todo time entra, com ou sem login (decisão do Gabriel: medir tudo e filtrar na tela)."""
    out = {}
    for t in times:
        meses = sorted(am for am, por in (uso_time or {}).items()
                       if am >= marco and (por.get(t) or {}).get('dias_ativos', 0) >= limiar)
        out[t] = {'usa': bool(meses), 'comecou_em': meses[0] if meses else None}
    return out


# ───────────────────────── antes × depois ─────────────────────────
def _periodo(linhas):
    base = sum(ln['base'] for ln in linhas)
    pos = sum(ln['positivados'] for ln in linhas)
    risco = sum(ln['em_risco'] for ln in linhas)
    return {'meses': [ln['anomes'] for ln in linhas],
            'cobertura': _div(pos, base), 'pct_risco': _div(risco, base),
            'saldo_medio': (sum(ln['saldo'] for ln in linhas) / len(linhas)) if linhas else None,
            # a mesma média em R$/mês (botão Clientes | R$/mês da tela, João 06/10/2026)
            'saldo_medio_valor': (sum(ln.get('valor_saldo') or 0.0 for ln in linhas) / len(linhas))
            if linhas else None}


def antes_depois(serie, marco, n_antes=3):
    """Médias de período: os `n_antes` meses imediatamente antes do marco × do marco em diante.
    Sem os `n_antes` meses completos, o "antes" sai vazio (não compara com meio período)."""
    antes = [ln for ln in serie if mes_add(marco, -n_antes) <= ln['anomes'] < marco]
    if len(antes) < n_antes:
        antes = []
    depois = [ln for ln in serie if ln['anomes'] >= marco]
    a, d = _periodo(antes), _periodo(depois)

    def _pp(x, y):
        return (y - x) * 100 if x is not None and y is not None else None
    return {'antes': a, 'depois': d,
            'delta': {'cobertura_pp': _pp(a['cobertura'], d['cobertura']),
                      'pct_risco_pp': _pp(a['pct_risco'], d['pct_risco']),
                      'saldo_medio': (d['saldo_medio'] - a['saldo_medio'])
                      if a['saldo_medio'] is not None and d['saldo_medio'] is not None else None,
                      'saldo_medio_valor': (d['saldo_medio_valor'] - a['saldo_medio_valor'])
                      if a['saldo_medio_valor'] is not None and d['saldo_medio_valor'] is not None else None}}
