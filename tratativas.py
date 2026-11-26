"""Tratativas — o histórico do CRM leve visto pelo LÍDER (pedido de 26/11/2026).

Um possível cliente perguntou se dá para ver o histórico das tratativas. A tabela `cliente_plano`
já guarda tudo desde 05/10/2026 (só INSERT; exclusão é lógica), mas a tela só mostrava cliente por
cliente. Esta página responde "como está o trabalho de cada vendedor" e "o que aconteceu com esse
cliente". As regras abaixo saíram da análise dos registros de produção cruzados com as notas fiscais
(26/11/2026), e cada uma tem o seu porquê:

- **Conversão só das AÇÕES** (ligação, retorno, sem contato). "Pedido feito" e "pedido prometido" são
  RESULTADO — são registrados depois do pedido e "convertem" sozinhos (quase todos viram nota). Somá-los
  inflaria a conversão de quem registra mais resultado do que esforço.
- **Conversão sempre por FAIXA de dias sem compra.** Medido: quem só trata cliente ativo converte
  quase tudo e quem trata cliente em risco converte muito menos. Um número bruto por vendedor faz o segundo parecer
  pior sendo o que faz o trabalho mais difícil.
- **Registro duplicado no mesmo minuto conta uma vez** (clique duplo, visto em produção).
- **Retorno agendado** é cumprido quando, até 1 dia depois da data marcada, alguém registrou algo no
  cliente ou o cliente comprou. Pendente = venceu e nada aconteceu desde então.
- **Transferir** não tem destino: pendente = ninguém além de quem transferiu registrou nada depois e
  o cliente não comprou. Em produção, as transferências não tinham registro de outra pessoa depois.
- Correlação não é causa: o vendedor tende a ligar para quem já ia comprar. A tela diz isso.

Funções PURAS — sem Flask nem banco. Testes em tests/test_tratativas.py.
"""
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import date, datetime, timedelta

from plano_cliente import ROTULO, hora_local

ACOES = ('ligacao_feita', 'retorno_agendado', 'sem_contato')
RESULTADOS = ('pedido_feito', 'pedido_prometido')
RETORNO, TRANSFERIR, PEDIDO_FEITO = 'retorno_agendado', 'transferir', 'pedido_feito'

JANELA_CONV = 15          # dias depois do registro para contar "comprou" (resposta rápida o bastante
                          # para uma tela de acompanhamento; com 30 dias o último mês não teria número)
PRAZO_FATURAR = 5         # mesmo prazo do plano_cliente: pedido feito que não fatura em 5 dias travou
TOLERANCIA_RETORNO = 1    # dia a mais para o retorno contar como cumprido
DIAS_RISCO = 61           # 61+ dias sem compra = cliente em risco (faixas da Recuperação)
FAIXAS = ('0-30', '31-60', '61-90', '90+')
PERIODOS = (30, 60, 90, 0)  # 0 = desde o primeiro registro
PERIODO_PADRAO = 60


def _data(v):
    if v is None or v == '':
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def dia_local(criado_em):
    """Dia (Brasília) em que o registro foi FEITO — a data que vale para tudo aqui. A `data_acao`
    pode ser até 60 dias para trás; medir pela data informada deixaria inflar o mês com registro
    retroativo."""
    h = hora_local(criado_em)
    return h.date() if isinstance(h, datetime) else _data(h)


def faixa_de(dias):
    """Faixa de dias sem compra no momento do registro. Sem compra no histórico (25 meses) = 90+."""
    if dias is None or dias > 90:
        return '90+'
    if dias <= 30:
        return '0-30'
    if dias <= 60:
        return '31-60'
    return '61-90'


def deduplicar(regs):
    """Tira o clique duplo: mesmo cliente, status e autor no mesmo minuto fica uma vez (o de menor id)."""
    vistos, out = set(), []
    for r in sorted(regs, key=lambda r: r.get('id') or 0):
        h = hora_local(r['criado_em'])
        minuto = h.replace(second=0, microsecond=0) if isinstance(h, datetime) else h
        k = (r['codcli'], r['status'], r.get('autor_id') or r.get('autor_nome'), minuto)
        if k in vistos:
            continue
        vistos.add(k)
        out.append(r)
    return out


def indexar_compras(compras_dia, codclis=None):
    """[[codcli, 'AAAA-MM-DD', codusur, valor]] → {codcli: ([datas ordenadas], [valores])}.
    Soma o dia (o mesmo cliente pode comprar de dois vendedores no mesmo dia)."""
    alvo = set(codclis) if codclis is not None else None
    dia = defaultdict(float)
    for c, d, _u, v in compras_dia or []:
        if alvo is not None and c not in alvo:
            continue
        dt = _data(d)
        if dt:
            dia[(c, dt)] += float(v or 0)
    por = defaultdict(list)
    for (c, dt), v in dia.items():
        por[c].append((dt, v))
    return {c: ([d for d, _ in sorted(xs)], [v for _, v in sorted(xs)]) for c, xs in por.items()}


def _ult_antes(idx, c, d):
    ds = (idx.get(c) or ([], []))[0]
    i = bisect_left(ds, d)
    return ds[i - 1] if i else None


def compras_entre(idx, c, a, b):
    """[(data, valor)] do cliente com a ≤ data ≤ b."""
    ds, vs = idx.get(c) or ([], [])
    i, j = bisect_left(ds, a), bisect_right(ds, b)
    return list(zip(ds[i:j], vs[i:j]))


def enriquecer(regs, idx, hoje, janela=JANELA_CONV):
    """Acrescenta a cada registro: `dia`, `dias_sem_compra` e `faixa` no momento do registro, e se o
    cliente comprou na janela (`comprou`, `valor_janela`, `janela_ok` = a janela já terminou)."""
    for r in regs:
        d = dia_local(r['criado_em'])
        u = _ult_antes(idx, r['codcli'], d)
        dias = (d - u).days if u else None
        depois = compras_entre(idx, r['codcli'], d, d + timedelta(days=janela))
        r.update(dia=d, dias_sem_compra=dias, faixa=faixa_de(dias),
                 janela_ok=d + timedelta(days=janela) <= hoje,
                 comprou=bool(depois), valor_janela=round(sum(v for _, v in depois), 2))
    return regs


def _seguintes(regs_cliente, r):
    return [s for s in regs_cliente if (s['criado_em'], s.get('id') or 0) > (r['criado_em'], r.get('id') or 0)]


def retornos(regs, idx, hoje):
    """Retornos agendados já vencidos (data marcada < hoje), um por registro."""
    por_cli = defaultdict(list)
    for r in regs:
        por_cli[r['codcli']].append(r)
    out = []
    for r in regs:
        marcado = _data(r.get('data_acao'))
        if r['status'] != RETORNO or not marcado or marcado >= hoje:
            continue
        lim = marcado + timedelta(days=TOLERANCIA_RETORNO)
        seg = _seguintes(por_cli[r['codcli']], r)
        reg_prazo = any(s['dia'] <= lim for s in seg)
        compra_prazo = bool(compras_entre(idx, r['codcli'], r['dia'], lim))
        nada_desde = not seg and not compras_entre(idx, r['codcli'], r['dia'], hoje)
        out.append({'reg': r, 'marcado': marcado, 'cumprido': reg_prazo or compra_prazo,
                    'pendente': nada_desde, 'dias_atraso': (hoje - marcado).days})
    return out


def transferencias(regs, idx, hoje):
    """Uma por cliente (a última transferência dele). Pendente = ninguém além de quem transferiu
    registrou nada depois e o cliente não comprou desde então."""
    por_cli = defaultdict(list)
    for r in regs:
        por_cli[r['codcli']].append(r)
    out = []
    for c, rs in por_cli.items():
        tr = [r for r in rs if r['status'] == TRANSFERIR]
        if not tr:
            continue
        r = max(tr, key=lambda x: (x['criado_em'], x.get('id') or 0))
        seg = _seguintes(rs, r)
        outro = any((s.get('autor_id') or s.get('autor_nome')) != (r.get('autor_id') or r.get('autor_nome')) for s in seg)
        comprou = bool(compras_entre(idx, c, r['dia'], hoje))
        out.append({'reg': r, 'n': len(tr), 'assumido': outro, 'comprou': comprou,
                    'pendente': not outro and not comprou, 'dias': (hoje - r['dia']).days})
    return out


def _conv_vazia():
    return {f: [0, 0] for f in FAIXAS}


def _conversao(regs):
    """{faixa: [clientes elegíveis, compraram]} — 1ª AÇÃO de cada cliente, só com janela completa."""
    prim = {}
    for r in sorted(regs, key=lambda r: (r['criado_em'], r.get('id') or 0)):
        if r['status'] in ACOES and r['codcli'] not in prim:
            prim[r['codcli']] = r
    conv = _conv_vazia()
    for r in prim.values():
        if r['janela_ok']:
            conv[r['faixa']][0] += 1
            conv[r['faixa']][1] += 1 if r['comprou'] else 0
    return conv


def _chave_autor(r):
    return r.get('autor_id') if r.get('autor_id') is not None else ('nome', r.get('autor_nome'))


def por_autor(regs, rets, trs, hoje, idx):
    """Uma linha por pessoa que registrou no período."""
    grupos = defaultdict(list)
    for r in regs:
        grupos[_chave_autor(r)].append(r)
    ret_g, tr_g = defaultdict(list), defaultdict(list)
    for x in rets:
        ret_g[_chave_autor(x['reg'])].append(x)
    for x in trs:
        tr_g[_chave_autor(x['reg'])].append(x)
    out = []
    for k, rs in grupos.items():
        prim = {}
        for r in sorted(rs, key=lambda r: (r['criado_em'], r.get('id') or 0)):
            prim.setdefault(r['codcli'], r)
        risco = sum(1 for r in prim.values() if r['dias_sem_compra'] is None or r['dias_sem_compra'] >= DIAS_RISCO)
        ped = [r for r in rs if r['status'] == PEDIDO_FEITO and r['dia'] + timedelta(days=PRAZO_FATURAR) <= hoje]
        fat = sum(1 for r in ped if compras_entre(idx, r['codcli'], r['dia'], r['dia'] + timedelta(days=PRAZO_FATURAR)))
        rr, tt = ret_g.get(k, []), tr_g.get(k, [])
        u = rs[0]
        out.append({
            'autor_id': u.get('autor_id'), 'autor': u.get('autor_nome') or '—',
            'role': u.get('autor_role'), 'codusur': u.get('autor_codusur'),
            'registros': len(rs), 'clientes': len(prim), 'dias': len({r['dia'] for r in rs}),
            'ultimo': max(r['dia'] for r in rs).isoformat(),
            'pct_risco': round(100 * risco / len(prim)) if prim else 0,
            'acoes': sum(1 for r in rs if r['status'] in ACOES),
            'conversao': _conversao(rs),
            'pedidos': {'n': len(ped), 'faturados': fat},
            'retornos': {'vencidos': len(rr), 'cumpridos': sum(1 for x in rr if x['cumprido']),
                         'pendentes': sum(1 for x in rr if x['pendente'])},
            'transferencias': {'n': len(tt), 'pendentes': sum(1 for x in tt if x['pendente'])},
            'status': {s: sum(1 for r in rs if r['status'] == s) for s in ROTULO},
        })
    out.sort(key=lambda a: -a['registros'])
    return out


def semanas(regs, hoje, n=12):
    """[{inicio, registros, pessoas}] das últimas `n` semanas (segunda a domingo), a mais antiga 1º."""
    seg = hoje - timedelta(days=hoje.weekday())
    inicios = [seg - timedelta(weeks=i) for i in range(n - 1, -1, -1)]
    g = {i: [0, set()] for i in inicios}
    for r in regs:
        i = r['dia'] - timedelta(days=r['dia'].weekday())
        if i in g:
            g[i][0] += 1
            g[i][1].add(_chave_autor(r))
    return [{'inicio': i.isoformat(), 'registros': g[i][0], 'pessoas': len(g[i][1])} for i in inicios]


def resumo(regs_periodo, regs_todos, idx, hoje):
    """Tudo o que a página mostra, a partir dos registros já ENRIQUECIDOS.

    `regs_todos` (todo o histórico no escopo) serve às pendências e à série semanal — um retorno
    marcado antes do período e ainda pendente continua sendo problema de hoje. `regs_periodo` serve
    aos números do período."""
    rets_all = retornos(regs_todos, idx, hoje)
    trs_all = transferencias(regs_todos, idx, hoje)
    ids = {r['id'] for r in regs_periodo}
    rets = [x for x in rets_all if x['reg']['id'] in ids]
    trs = [x for x in trs_all if x['reg']['id'] in ids]
    return {
        'kpis': {
            'registros': len(regs_periodo),
            'clientes': len({r['codcli'] for r in regs_periodo}),
            'pessoas': len({_chave_autor(r) for r in regs_periodo}),
            'dias': len({r['dia'] for r in regs_periodo}),
            'pct_risco': (round(100 * sum(1 for r in regs_periodo if r['faixa'] in ('61-90', '90+'))
                                / len(regs_periodo)) if regs_periodo else 0),
            'retornos_vencidos': len(rets), 'retornos_cumpridos': sum(1 for x in rets if x['cumprido']),
            'retornos_pendentes': sum(1 for x in rets_all if x['pendente']),
            'transferencias_pendentes': sum(1 for x in trs_all if x['pendente']),
        },
        'conversao': _conversao(regs_periodo),
        'status': {s: sum(1 for r in regs_periodo if r['status'] == s) for s in ROTULO},
        'autores': por_autor(regs_periodo, rets, trs, hoje, idx),
        'semanas': semanas(regs_todos, hoje),
        'retornos_pendentes': sorted((x for x in rets_all if x['pendente']), key=lambda x: -x['dias_atraso']),
        'transferencias_pendentes': sorted((x for x in trs_all if x['pendente']), key=lambda x: -x['dias']),
    }


def linha_do_tempo(regs, idx, codcli, carteira_hist, nomes_vend, desde, mostrar_excluidos=False):
    """Eventos do cliente, do mais recente para o mais antigo: tratativas (as excluídas só para o
    admin, marcadas), notas fiscais desde `desde` e trocas de vendedor (`carteira_foto`, mensal)."""
    ev = []
    for r in regs:
        if r.get('excluido_em') and not mostrar_excluidos:
            continue
        h = hora_local(r['criado_em'])
        ev.append({'tipo': 'tratativa', 'quando': h.isoformat(timespec='minutes') if isinstance(h, datetime) else str(h),
                   'dia': dia_local(r['criado_em']).isoformat(), 'status': r['status'],
                   'rotulo': ROTULO.get(r['status'], r['status']), 'data_acao': str(_data(r.get('data_acao')) or ''),
                   'descricao': r.get('descricao') or '', 'autor': r.get('autor_nome') or '—',
                   'excluido': bool(r.get('excluido_em'))})
    for d, v in compras_entre(idx, codcli, desde, date.max):
        ev.append({'tipo': 'compra', 'quando': d.isoformat(), 'dia': d.isoformat(), 'valor': round(v, 2)})
    ant = None
    for anomes, cu in sorted(carteira_hist or []):
        if ant is not None and cu != ant:
            d = date(anomes // 100, anomes % 100, 1)
            ev.append({'tipo': 'carteira', 'quando': d.isoformat(), 'dia': d.isoformat(),
                       'de': nomes_vend.get(str(ant)) or (f'RCA {ant}' if ant is not None else '—'),
                       'para': nomes_vend.get(str(cu)) or (f'RCA {cu}' if cu is not None else '—')})
        ant = cu
    ev.sort(key=lambda e: e['quando'], reverse=True)
    return ev
