"""Carteira em risco × carteira recuperada. Funções puras — sem Flask/DAX/DB.
Testes em tests/test_recuperacao.py. Plano: docs/comercial/PLANO_MELHORIAS_COMERCIAL.md §4.

Réguas (acordadas com o João Victor em 24/09/2026):
- EM RISCO (inativo) = mais de `inativo_dias` (60 → inativo a partir do 61º dia) sem comprar E
  além do próprio ciclo de compra. 60 é a REGRA COMERCIAL da empresa (depois disso qualquer
  vendedor pode atender); a medição sugeria 69 como ciclo ideal e o João preferiu manter 61.
  O ciclo existe para não chamar de "sumido" quem compra naturalmente a cada 3 meses (medido:
  21% dos "reativados" pelo corte fixo eram esses clientes).
- INATIVO NO ERP = 91+ dias: o Winthor marca sozinho. Aqui é só uma MARCA na lista
  (`inativo_erp`), não muda o estado.
- PERDIDO = mais de `perdido_dias` sem comprar (default 365 — a confirmar com o João).
- RECUPERADO no mês = a 1ª compra do mês aconteceu com o cliente EM RISCO ou PERDIDO. O crédito
  vai para QUEM VENDEU (decisão dele); o dono do cadastro aparece ao lado.
- VALOR MENSAL = venda dos 365 dias que terminam na última compra ÷ 12. É a régua da "venda
  perdida": comparável com a venda recuperada. ⚠️ NÃO é o `rfm.receita_perdida_proj`, que
  multiplica pelos meses de atraso e cresce sozinho.

A PONTE do estoque em risco fecha no zero:
    em_risco_fim = em_risco_inicio + entraram − recuperados_do_risco − viraram_perdidos
Cliente que entra em risco e é recuperado dentro do mesmo mês conta como entrada E como
recuperado. Quem volta da base PERDIDA é recuperação também, mas não passa pelo estoque em
risco — sai separado (`resgatados_perdidos`).
"""
from bisect import bisect_left
from datetime import date, timedelta

import rfm

INATIVO_DIAS = 60     # regra comercial: inativo a partir do 61º dia
ERP_INATIVO_DIAS = 91  # o ERP marca inativo sozinho a partir daqui (só sinalização)
PERDIDO_DIAS = 365

ATIVO, RISCO, PERDIDO = 'ativo', 'risco', 'perdido'

# Chance de o cliente EM RISCO comprar no mês, por dias parado — medida no BI de jan a jul/26
# (16.790 cliente-mês). É o que ordena a lista de risco: valor mensal × chance de voltar.
# ⚠️ NÃO usar rfm.prioridade_contato aqui: ela multiplica por dias ÷ ciclo, que explode com
# ciclo curto (1º da lista era um cliente parado há 311 dias com ciclo de 7 — chance de 4,6%).
CHANCE_VOLTA = ((90, 0.325), (180, 0.152), (365, 0.046))


def chance_volta(dias):
    for ate, p in CHANCE_VOLTA:
        if dias <= ate:
            return p
    return 0.0


# ───────────────────────── calendário ─────────────────────────
def inicio_mes(anomes):
    return date(anomes // 100, anomes % 100, 1)


def mes_add(anomes, n):
    i = (anomes // 100) * 12 + (anomes % 100 - 1) + n
    return (i // 12) * 100 + i % 12 + 1


# ───────────────────────── histórico ─────────────────────────
def indexar(eventos):
    """[(codcli, data, codusur, valor)] → {codcli: {'datas': [date…] ordenadas, 'dia': {date:
    {codusur: valor}}}}. Só venda > 0 conta como compra."""
    out = {}
    for codcli, d, u, v in eventos:
        if codcli is None or d is None or not v or v <= 0:
            continue
        if not isinstance(d, date):
            d = date.fromisoformat(str(d)[:10])
        h = out.setdefault(codcli, {'datas': [], 'dia': {}})
        por = h['dia'].setdefault(d, {})
        por[u] = por.get(u, 0.0) + v
    for h in out.values():
        h['datas'] = sorted(h['dia'])
    return out


def marcar_excluidos(historico, excluidos):
    """Grava a data de EXCLUSÃO do cadastro (`PCCLIENT.DTEXCLUSAO`) em cada histórico.
    `excluidos` = {codcli: date}. Ver `estado_em`."""
    for c, d in (excluidos or {}).items():
        h = historico.get(c)
        if h is not None:
            h['excluido'] = d
    return historico


def estado_em(h, t, inativo_dias=INATIVO_DIAS, perdido_dias=PERDIDO_DIAS):
    """Estado do cliente no INÍCIO do dia `t` (só compras anteriores a `t` contam).
    Devolve dict {estado, ultima, dias, ciclo, valor_mensal} ou None se ele nunca comprou.

    Cliente EXCLUÍDO no Winthor (`h['excluido']`, ver `marcar_excluidos`) é PERDIDO a partir do
    dia seguinte à exclusão — a empresa declarou que ele não compra mais (João, 20/10/2026: os 5
    do JULIANO foram excluídos em 26/06 como "CLIENTE NAO COMPRA MAIS"). Antes da data ele segue
    a régua normal, então o passado não se reescreve; e no mês da exclusão quem estava em risco
    sai pelo "viraram perdidos", o que mantém a ponte fechando no zero. Se ele comprar DEPOIS da
    exclusão, a régua normal volta a valer a partir dessa compra."""
    i = bisect_left(h['datas'], t)
    if i == 0:
        return None
    ultima = h['datas'][i - 1]
    dias = (t - ultima).days
    desde = ultima - timedelta(days=365)
    janela = [d for d in h['datas'][:i] if d > desde]
    ciclo = rfm.ciclo_pessoal(janela)
    valor = sum(sum(h['dia'][d].values()) for d in janela) / 12
    excluido = h.get('excluido')
    if dias > perdido_dias or (excluido is not None and excluido < t and ultima <= excluido):
        est = PERDIDO
    elif dias > inativo_dias and (ciclo is None or dias > ciclo):
        est = RISCO
    else:
        est = ATIVO
    return {'estado': est, 'ultima': ultima, 'dias': dias, 'ciclo': ciclo, 'valor_mensal': valor}


def _compras_no_mes(h, t0, t1):
    i, j = bisect_left(h['datas'], t0), bisect_left(h['datas'], t1)
    return h['datas'][i:j]


# ───────────────────────── o mês de um cliente ─────────────────────────
def movimento_cliente(h, anomes, inativo_dias=INATIVO_DIAS, perdido_dias=PERDIDO_DIAS, hoje=None):
    """O que aconteceu com UM cliente no mês. Devolve None se ele não participa da ponte.

    `hoje`: teto do "fim" no mês EM CURSO. Sem ele o fim seria o dia 1º do mês seguinte, uma data no
    futuro em que todo cliente que ainda não comprou já cruzou o limite: em 06/11/2026 a tela dizia
    729 entradas em risco e 2.948 em risco, quando até o dia eram 100 e 2.457 (a mesma conta da lista
    "em risco hoje"). Em mês fechado o teto não muda nada."""
    t0, t1 = inicio_mes(anomes), inicio_mes(mes_add(anomes, 1))
    if hoje is not None:
        t1 = min(t1, hoje + timedelta(days=1))
    ini = estado_em(h, t0, inativo_dias, perdido_dias)
    fim = estado_em(h, t1, inativo_dias, perdido_dias)
    compras = _compras_no_mes(h, t0, t1)
    antes_da_compra = estado_em(h, compras[0], inativo_dias, perdido_dias) if compras else None
    e_ini = ini['estado'] if ini else None
    e_fim = fim['estado'] if fim else None
    e_rec = antes_da_compra['estado'] if antes_da_compra else None
    recuperado = e_rec in (RISCO, PERDIDO)
    # Quem começou o mês em risco e comprou saiu do ESTOQUE em risco, mesmo que tenha cruzado o
    # limite de perdido no meio do mês — senão some da ponte (achado na validação de ago/26).
    origem = RISCO if (recuperado and (e_ini == RISCO or e_rec == RISCO)) else e_rec
    vendas = {}
    for d in compras:
        for u, v in h['dia'][d].items():
            vendas[u] = vendas.get(u, 0.0) + v
    return {
        'ini': e_ini, 'fim': e_fim,
        'valor_ini': ini['valor_mensal'] if ini else 0.0,
        'valor_fim': fim['valor_mensal'] if fim else 0.0,
        'entrou': e_ini == ATIVO and (e_fim == RISCO or (recuperado and e_rec == RISCO)),
        'recuperado': recuperado,
        'recuperado_de': origem if recuperado else None,
        'dias_parado': antes_da_compra['dias'] if recuperado else None,
        'valor_mensal_rec': antes_da_compra['valor_mensal'] if recuperado else 0.0,
        'vendas': vendas,                      # {codusur: R$ no mês} — o crédito é de quem vendeu
        # `not recuperado`: quem saiu do risco por compra já saiu pela linha dos recuperados. Sem
        # exclusão os dois nunca coincidem; com ela sim — comprou e foi excluído no mesmo mês
        # (4 casos no BI, out/25–abr/26) — e contaria duas vezes, quebrando a ponte.
        'virou_perdido': e_ini == RISCO and e_fim == PERDIDO and not recuperado,
    }


def movimentos(historico, anomes, inativo_dias=INATIVO_DIAS, perdido_dias=PERDIDO_DIAS, hoje=None):
    """{codcli: movimento} do mês — só dos clientes que PARTICIPAM de algo (em risco no início
    ou no fim, entrou, recuperado, virou perdido). É o insumo caro: calcule UMA vez por mês e
    agregue por escopo com ponte_de/placar_de."""
    out = {}
    for c, h in historico.items():
        m = movimento_cliente(h, anomes, inativo_dias, perdido_dias, hoje)
        if (m['ini'] == RISCO or m['fim'] == RISCO or m['entrou'] or m['recuperado']
                or m['virou_perdido']):
            out[c] = m
    return out


# ───────────────────────── ponte da empresa ─────────────────────────
def ponte(historico, anomes, clientes=None, inativo_dias=INATIVO_DIAS, perdido_dias=PERDIDO_DIAS):
    """Ponte do estoque em risco no mês (atalho: calcula os movimentos e agrega)."""
    return ponte_de(movimentos(historico, anomes, inativo_dias, perdido_dias), anomes, clientes)


def ponte_de(movs, anomes, clientes=None):
    """Ponte do estoque em risco, em clientes e em R$ mensal. `clientes` restringe o universo
    (ex.: a base de um time). Fecha no zero por construção (ver docstring do módulo)."""
    cs = movs.keys() if clientes is None else [c for c in clientes if c in movs]
    p = {k: 0 for k in ('risco_ini', 'entraram', 'recuperados', 'viraram_perdidos', 'risco_fim',
                        'resgatados_perdidos')}
    v = {k: 0.0 for k in ('risco_ini', 'entraram', 'recuperados', 'viraram_perdidos', 'risco_fim',
                          'resgatados_perdidos')}
    venda_rec = 0.0
    for c in cs:
        m = movs[c]
        if m['ini'] == RISCO:
            p['risco_ini'] += 1; v['risco_ini'] += m['valor_ini']
        if m['entrou']:
            p['entraram'] += 1; v['entraram'] += m['valor_mensal_rec'] or m['valor_fim']
        if m['recuperado']:
            venda_rec += sum(m['vendas'].values())
            if m['recuperado_de'] == RISCO:
                p['recuperados'] += 1; v['recuperados'] += m['valor_mensal_rec']
            else:
                p['resgatados_perdidos'] += 1; v['resgatados_perdidos'] += m['valor_mensal_rec']
        if m['virou_perdido']:
            p['viraram_perdidos'] += 1; v['viraram_perdidos'] += m['valor_ini']
        if m['fim'] == RISCO:
            p['risco_fim'] += 1; v['risco_fim'] += m['valor_fim']
    return {'anomes': anomes, 'clientes': p, 'valor_mensal': {k: round(x, 2) for k, x in v.items()},
            'venda_recuperada': round(venda_rec, 2)}


# ───────────────────────── período (vários meses) ─────────────────────────
# Pedido do João (06/11/2026): "colocar tipo para puxar: mês atual, 3 meses, 6 meses, ano".
PERIODOS = {'mes_atual': 'Mês atual', '3m': '3 meses', '6m': '6 meses', 'ano': 'Ano'}


def meses_do_periodo(periodo, ref, atual):
    """Meses de um período, em ordem. `ref` = último mês fechado; `atual` = mês em curso.
    3 e 6 meses e Ano contam só meses FECHADOS: o mês em curso tem seu botão, e misturá-lo
    enviesaria a comparação entre períodos. Ano = de janeiro até o último mês fechado (em janeiro,
    o ano anterior inteiro). Período desconhecido → None."""
    if periodo == 'mes_atual':
        return [atual]
    if periodo in ('3m', '6m'):
        n = int(periodo[0])
        return [mes_add(ref, -i) for i in range(n - 1, -1, -1)]
    if periodo == 'ano':
        return [(ref // 100) * 100 + m for m in range(1, ref % 100 + 1)]
    return None


def somar_pontes(pontes):
    """Ponte de meses CONSECUTIVOS: início do primeiro, fim do último, fluxos somados. Fecha no zero
    porque o fim de um mês é o início do seguinte (conferido no BI em 3, 6 e 12 meses). Por isso a
    tela não oferece meses salteados: com um buraco, a ponte deixaria de fechar.
    ⚠️ Os fluxos contam EVENTOS: em 12 meses foram 5.572 recuperações de 3.549 clientes distintos."""
    p = {k: sum(x['clientes'][k] for x in pontes) for k in ('entraram', 'recuperados', 'viraram_perdidos',
                                                            'resgatados_perdidos')}
    p['risco_ini'], p['risco_fim'] = pontes[0]['clientes']['risco_ini'], pontes[-1]['clientes']['risco_fim']
    v = {k: round(sum(x['valor_mensal'][k] for x in pontes), 2)
         for k in ('entraram', 'recuperados', 'viraram_perdidos', 'resgatados_perdidos')}
    v['risco_ini'], v['risco_fim'] = pontes[0]['valor_mensal']['risco_ini'], pontes[-1]['valor_mensal']['risco_fim']
    return {'anomes': pontes[-1]['anomes'], 'clientes': p, 'valor_mensal': v,
            'venda_recuperada': round(sum(x['venda_recuperada'] for x in pontes), 2)}


_PLACAR_POSICAO = ('em_risco', 'valor_em_risco')   # estoque: vale o do ÚLTIMO mês, nunca a soma
_PLACAR_CONTA = ('saldo', 'valor_saldo')           # recalculados do acumulado


def somar_placares(placares):
    """Placar de vários meses: fluxos somados, posição do último mês, saldo recalculado."""
    out = {}
    for tab in ('rcas', 'times'):
        acum = {}
        for pl in placares:
            for k, s in pl[tab].items():
                a = acum.setdefault(k, {})
                for c, x in s.items():
                    if c not in _PLACAR_POSICAO and c not in _PLACAR_CONTA:
                        a[c] = a.get(c, 0) + x
        for k, a in acum.items():
            fim = placares[-1][tab].get(k) or {}
            a['em_risco'], a['valor_em_risco'] = fim.get('em_risco', 0), fim.get('valor_em_risco', 0.0)
            a['saldo'] = a['rec_da_base'] - a['entraram']
            a['valor_saldo'] = a['valor_rec_da_base'] - a['valor_entraram']
            for c, x in a.items():
                if isinstance(x, float):
                    a[c] = round(x, 2)
        out[tab] = acum
    return out


# ───────────────────────── placar por RCA / time ─────────────────────────
def placar(historico, anomes, dono_de, time_de, inativo_dias=INATIVO_DIAS, perdido_dias=PERDIDO_DIAS):
    """Atalho: calcula os movimentos do mês e monta o placar (ver placar_de)."""
    return placar_de(movimentos(historico, anomes, inativo_dias, perdido_dias), dono_de, time_de)


def placar_de(movs, dono_de, time_de):
    """Placar do mês por RCA e por time, com as DUAS leituras (decisão de 24/09/2026):
    - `da_base`: a carteira do DONO (cadastro) — em risco no fim do mês e recuperado da base
      por qualquer vendedor;
    - `por_ele`: o que o VENDEDOR recuperou, de qualquer base (é quem leva o crédito).
    Identidades que a tela deixa refazer (05/10/2026):
      rec_da_base = rec_risco_da_base + rec_perdido_da_base
      rec_da_base = (rec_por_ele − rec_de_outra_base) + rec_por_outros
      saldo       = rec_da_base − entraram         (positivo = bom)
    `dono_de` = {codcli: codusur do cadastro}; `time_de` = {codusur: codsupervisor}.
    Venda recuperada de um cliente com dois vendedores no mês é dividida pelo que cada um vendeu.
    """
    rcas, times = {}, {}

    def _slot(tab, k):
        return tab.setdefault(k, {'em_risco': 0, 'valor_em_risco': 0.0, 'rec_da_base': 0,
                                  'venda_rec_da_base': 0.0, 'rec_por_ele': 0,
                                  'venda_rec_por_ele': 0.0, 'rec_de_outra_base': 0,
                                  'entraram': 0, 'valor_entraram': 0.0,
                                  'rec_risco_da_base': 0, 'valor_rec_risco_da_base': 0.0,
                                  'valor_rec_da_base': 0.0,
                                  'rec_perdido_da_base': 0, 'rec_por_outros': 0})

    for c, m in movs.items():
        dono = dono_de.get(c)
        t_dono = time_de.get(dono)
        if m['fim'] == RISCO:
            for tab, k in ((rcas, dono), (times, t_dono)):
                s = _slot(tab, k); s['em_risco'] += 1; s['valor_em_risco'] += m['valor_fim']
        # SALDO do mês (João, 29/09/2026), na carteira do DONO — os mesmos critérios da ponte da
        # empresa, então a soma fecha com ela. Sinal: ver o fim da função.
        if m['entrou']:
            for tab, k in ((rcas, dono), (times, t_dono)):
                s = _slot(tab, k); s['entraram'] += 1; s['valor_entraram'] += m['valor_mensal_rec'] or m['valor_fim']
        if m['recuperado'] and m['recuperado_de'] == RISCO:
            for tab, k in ((rcas, dono), (times, t_dono)):
                s = _slot(tab, k); s['rec_risco_da_base'] += 1; s['valor_rec_risco_da_base'] += m['valor_mensal_rec']
        if not m['recuperado']:
            continue
        total = sum(m['vendas'].values())
        times_que_venderam = {time_de.get(u) for u in m['vendas']}
        for tab, k, vendeu in ((rcas, dono, dono in m['vendas']), (times, t_dono, t_dono in times_que_venderam)):
            s = _slot(tab, k); s['rec_da_base'] += 1; s['venda_rec_da_base'] += total
            s['valor_rec_da_base'] += m['valor_mensal_rec']
            if m['recuperado_de'] != RISCO:
                s['rec_perdido_da_base'] += 1
            # A volta do "de outra base" (05/10/2026): cliente DA BASE recuperado só por quem não é
            # o dono. Fecha a linha: da base = (por ele − de outra base) + por outros.
            if not vendeu:
                s['rec_por_outros'] += 1
        times_creditados = set()
        for u, v in m['vendas'].items():
            s = _slot(rcas, u); s['rec_por_ele'] += 1; s['venda_rec_por_ele'] += v
            if u != dono:
                s['rec_de_outra_base'] += 1
            tu = time_de.get(u)
            st = _slot(times, tu); st['venda_rec_por_ele'] += v
            if tu not in times_creditados:
                st['rec_por_ele'] += 1; times_creditados.add(tu)
                if tu != t_dono:
                    st['rec_de_outra_base'] += 1
    for tab in (rcas, times):
        for s in tab.values():
            # SALDO DE RECUPERAÇÃO = todos os recuperados da base − entraram. Positivo = recuperou
            # mais do que perdeu (sinal invertido em 06/10/2026: "quem fez um bom trabalho fica
            # negativo"). Desde 06/11/2026 conta também quem voltou da base PERDIDA (João: "ele
            # recuperou 24, entraram 20, e fica +1 porque 3 estavam como perdidos"): sem isso, quem
            # entrou em risco, virou perdido e voltou ficava −1 para sempre.
            # ⚠️ NÃO é a variação do estoque em risco, e nunca foi: quem vira perdido sai do risco
            # sem entrar no saldo (out/26: saldo −165 × variação −46). Quem fecha no zero é a ponte.
            s['saldo'] = s['rec_da_base'] - s['entraram']
            s['valor_saldo'] = s['valor_rec_da_base'] - s['valor_entraram']
            for k in ('valor_em_risco', 'venda_rec_da_base', 'venda_rec_por_ele', 'valor_entraram',
                      'valor_rec_risco_da_base', 'valor_rec_da_base', 'valor_saldo'):
                s[k] = round(s[k], 2)
    return {'rcas': rcas, 'times': times}


# ───────────────────────── recuperação que ficou ─────────────────────────
def recuperacao_que_ficou(historico, anomes, inativo_dias=INATIVO_DIAS, perdido_dias=PERDIDO_DIAS):
    """Dos recuperados em `anomes`, quantos voltaram a comprar em algum dos 2 meses seguintes.
    Medido: só 53% — venda recuperada no mês, sozinha, superestima a recuperação.
    Só faz sentido para meses com os 2 seguintes já fechados; quem chama escolhe o mês."""
    movs = movimentos(historico, anomes, inativo_dias, perdido_dias)
    return ficou_de(historico, movs, anomes)


def ficou_de(historico, movs, anomes, clientes=None):
    """Recuperação que ficou a partir dos movimentos já calculados do mês `anomes`."""
    rec = [c for c, m in movs.items() if m['recuperado'] and (clientes is None or c in clientes)]
    t0, t1 = inicio_mes(mes_add(anomes, 1)), inicio_mes(mes_add(anomes, 3))
    ficaram = [c for c in rec if _compras_no_mes(historico[c], t0, t1)]
    return {'anomes': anomes, 'recuperados': len(rec), 'ficaram': len(ficaram),
            'taxa': (len(ficaram) / len(rec)) if rec else None}


# ───────────────────────── listas ─────────────────────────
def em_risco_em(historico, t, inativo_dias=INATIVO_DIAS, perdido_dias=PERDIDO_DIAS):
    """Clientes EM RISCO no dia `t`, com o que a lista precisa (sem cadastro — o chamador junta)."""
    out = []
    for c, h in historico.items():
        e = estado_em(h, t, inativo_dias, perdido_dias)
        if e and e['estado'] == RISCO:
            out.append({'codcli': c, 'ultima_compra': e['ultima'].isoformat(), 'dias': e['dias'],
                        'ciclo': e['ciclo'], 'valor_mensal': round(e['valor_mensal'], 2),
                        'inativo_erp': e['dias'] >= ERP_INATIVO_DIAS,
                        'chance_volta': chance_volta(e['dias']),
                        'prioridade': round(e['valor_mensal'] * chance_volta(e['dias']), 2)})
    out.sort(key=lambda x: -x['prioridade'])
    return out


def recuperados_no_mes(historico, anomes, dono_de, inativo_dias=INATIVO_DIAS, perdido_dias=PERDIDO_DIAS):
    """Atalho de recuperados_de."""
    return recuperados_de(movimentos(historico, anomes, inativo_dias, perdido_dias), dono_de)


def recuperados_de(movs, dono_de):
    """Lista dos recuperados no mês: quem vendeu, dono do cadastro, de outra base, dias parado."""
    out = []
    for c, m in movs.items():
        if not m['recuperado']:
            continue
        dono = dono_de.get(c)
        out.append({'codcli': c, 'dono': dono, 'vendedores': sorted(m['vendas']),
                    'de_outra_base': any(u != dono for u in m['vendas']),
                    'recuperado_de': m['recuperado_de'], 'dias_parado': m['dias_parado'],
                    'valor_mensal': round(m['valor_mensal_rec'], 2),
                    'venda_mes': round(sum(m['vendas'].values()), 2)})
    out.sort(key=lambda x: -x['venda_mes'])
    return out


def juntar_recuperados(listas):
    """Lista de recuperados de vários meses (em ordem cronológica), uma linha por cliente: a venda
    soma, o resto é da recuperação mais recente e `vezes` diz quantas foram."""
    por = {}
    for lista in listas:
        for r in lista:
            a = por.get(r['codcli'])
            if a is None:
                por[r['codcli']] = {**r, 'vezes': 1}
                continue
            por[r['codcli']] = {**r, 'vezes': a['vezes'] + 1,
                                'venda_mes': round(a['venda_mes'] + r['venda_mes'], 2),
                                'vendedores': sorted(set(a['vendedores']) | set(r['vendedores'])),
                                'de_outra_base': a['de_outra_base'] or r['de_outra_base']}
    return sorted(por.values(), key=lambda x: -x['venda_mes'])
