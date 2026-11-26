"""Tratativas — o histórico do CRM leve para o líder (26/11/2026).

Pedido: um possível cliente perguntou se dá para ver o histórico das tratativas; o líder quer
acompanhar um cliente específico e o trabalho de cada vendedor. As regras vieram da análise dos
registros de produção cruzados com as notas fiscais — cada teste diz qual.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

import tratativas as tr

HOJE = date(2026, 11, 26)
UTC = timezone.utc


def _reg(id_, codcli, status, quando, autor=7, nome='Ana', data_acao=None, **kw):
    """`quando` = datetime de Brasília; o banco guarda em UTC (TIMESTAMPTZ)."""
    return {'id': id_, 'codcli': codcli, 'status': status, 'autor_id': autor, 'autor_nome': nome,
            'criado_em': (quando + timedelta(hours=3)).replace(tzinfo=UTC),
            'data_acao': data_acao or quando.date(), 'descricao': '', **kw}


def _idx(*compras):
    return tr.indexar_compras([[c, d, 1, v] for c, d, v in compras])


# ══════════════ regras puras ══════════════
def test_clique_duplo_no_mesmo_minuto_conta_uma_vez():
    """Produção tinha pares iguais no mesmo minuto. Mesmo cliente/status/autor em minutos diferentes
    são dois registros de verdade."""
    t = datetime(2026, 11, 3, 10, 15, 5)
    regs = [_reg(1, 10, 'ligacao_feita', t), _reg(2, 10, 'ligacao_feita', t + timedelta(seconds=20)),
            _reg(3, 10, 'ligacao_feita', t + timedelta(minutes=5)), _reg(4, 10, 'sem_contato', t)]
    assert [r['id'] for r in tr.deduplicar(regs)] == [1, 3, 4]


def test_dia_do_registro_e_o_de_brasilia():
    r = _reg(1, 10, 'ligacao_feita', datetime(2026, 11, 3, 22, 30))   # 01:30 UTC do dia 4
    assert tr.dia_local(r['criado_em']) == date(2026, 11, 3)


def test_faixa_e_compra_na_janela():
    idx = _idx((10, '2026-09-01', 100.0), (10, '2026-11-10', 250.0), (10, '2026-11-10', 50.0))
    r = tr.enriquecer([_reg(1, 10, 'ligacao_feita', datetime(2026, 11, 3, 9))], idx, HOJE)[0]
    assert r['dias_sem_compra'] == 63 and r['faixa'] == '61-90'
    assert r['comprou'] and r['valor_janela'] == 300.0 and r['janela_ok']   # mesmo dia soma
    novo = tr.enriquecer([_reg(2, 10, 'ligacao_feita', datetime(2026, 11, 20, 9))], idx, HOJE)[0]
    assert novo['janela_ok'] is False                                         # janela ainda aberta
    sem = tr.enriquecer([_reg(3, 99, 'ligacao_feita', datetime(2026, 11, 3, 9))], idx, HOJE)[0]
    assert sem['dias_sem_compra'] is None and sem['faixa'] == '90+'


def test_conversao_conta_so_acoes_e_a_primeira_por_cliente():
    """'Pedido feito' é resultado (quase sempre 'converte' sozinho): fica fora da conversão."""
    idx = _idx((10, '2026-08-01', 1.0), (10, '2026-11-05', 1.0), (20, '2026-08-01', 1.0))
    regs = tr.enriquecer([
        _reg(1, 10, 'ligacao_feita', datetime(2026, 11, 2, 9)),
        _reg(2, 10, 'ligacao_feita', datetime(2026, 11, 3, 9)),        # mesmo cliente: não conta de novo
        _reg(3, 20, 'pedido_feito', datetime(2026, 11, 2, 9)),          # resultado: fora
        _reg(4, 20, 'sem_contato', datetime(2026, 11, 4, 9)),
    ], idx, HOJE)
    conv = tr._conversao(regs)
    assert conv['90+'] == [2, 1]                                       # 10 comprou, 20 não
    assert sum(n for n, _ in conv.values()) == 2


def test_retorno_cumprido_vencido_e_pendente():
    idx = _idx((30, '2026-11-12', 10.0))
    regs = tr.enriquecer([
        _reg(1, 10, 'retorno_agendado', datetime(2026, 11, 2, 9), data_acao=date(2026, 11, 6)),
        _reg(2, 10, 'ligacao_feita', datetime(2026, 11, 7, 9)),          # D+1: cumprido
        _reg(3, 20, 'retorno_agendado', datetime(2026, 11, 2, 9), data_acao=date(2026, 11, 6)),
        _reg(4, 20, 'ligacao_feita', datetime(2026, 11, 15, 9)),         # tarde, mas aconteceu algo
        _reg(5, 30, 'retorno_agendado', datetime(2026, 11, 2, 9), data_acao=date(2026, 11, 6)),
        _reg(6, 40, 'retorno_agendado', datetime(2026, 11, 2, 9), data_acao=date(2026, 11, 6)),
        _reg(7, 50, 'retorno_agendado', datetime(2026, 11, 20, 9), data_acao=date(2026, 11, 30)),
    ], idx, HOJE)
    rs = {x['reg']['codcli']: x for x in tr.retornos(regs, idx, HOJE)}
    assert rs[10]['cumprido'] and not rs[10]['pendente']
    assert not rs[20]['cumprido'] and not rs[20]['pendente']
    assert not rs[30]['cumprido'] and not rs[30]['pendente']            # comprou depois
    assert rs[40]['pendente'] and rs[40]['dias_atraso'] == 20           # nada aconteceu
    assert 50 not in rs                                                  # ainda não venceu


def test_transferencia_so_sai_de_pendente_se_outra_pessoa_assumir_ou_o_cliente_comprar():
    """Em produção, as transferências não tinham registro de outra pessoa depois."""
    idx = _idx((30, '2026-11-20', 10.0))
    regs = tr.enriquecer([
        _reg(1, 10, 'transferir', datetime(2026, 11, 2, 9), autor=7),
        _reg(2, 10, 'ligacao_feita', datetime(2026, 11, 3, 9), autor=7),   # o mesmo de novo: pendente
        _reg(3, 20, 'transferir', datetime(2026, 11, 2, 9), autor=7),
        _reg(4, 20, 'ligacao_feita', datetime(2026, 11, 3, 9), autor=8, nome='Bia'),
        _reg(5, 30, 'transferir', datetime(2026, 11, 2, 9), autor=7),
        _reg(6, 10, 'transferir', datetime(2026, 11, 4, 9), autor=7),
    ], idx, HOJE)
    ts = {x['reg']['codcli']: x for x in tr.transferencias(regs, idx, HOJE)}
    assert ts[10]['pendente'] and ts[10]['n'] == 2 and ts[10]['reg']['id'] == 6
    assert not ts[20]['pendente'] and ts[20]['assumido']
    assert not ts[30]['pendente'] and ts[30]['comprou']


def test_por_autor_separa_risco_e_pedido_faturado():
    idx = _idx((10, '2026-06-01', 1.0), (20, '2026-11-01', 1.0), (20, '2026-11-08', 1.0))
    regs = tr.enriquecer([
        _reg(1, 10, 'ligacao_feita', datetime(2026, 11, 2, 9)),          # 10: 154 dias = risco
        _reg(2, 20, 'pedido_feito', datetime(2026, 11, 5, 9)),           # faturou em 3 dias
        _reg(3, 30, 'pedido_feito', datetime(2026, 11, 6, 9)),           # não faturou
        _reg(4, 40, 'pedido_feito', datetime(2026, 11, 24, 9)),          # prazo de 5 dias não acabou
    ], idx, HOJE)
    a = tr.por_autor(regs, [], [], HOJE, idx)[0]
    assert a['registros'] == 4 and a['clientes'] == 4 and a['dias'] == 4
    assert a['pct_risco'] == 75                                         # 10 (154 d), 30 e 40 (sem compra)
    assert a['pedidos'] == {'n': 2, 'faturados': 1}


def test_semanas_e_resumo():
    idx = _idx((10, '2026-11-20', 1.0))
    regs = tr.enriquecer([_reg(1, 10, 'ligacao_feita', datetime(2026, 11, 24, 9)),
                          _reg(2, 11, 'ligacao_feita', datetime(2026, 11, 25, 9), autor=8, nome='Bia'),
                          _reg(3, 12, 'transferir', datetime(2026, 10, 1, 9))], idx, HOJE)
    sem = tr.semanas(regs, HOJE)
    assert len(sem) == 12 and sem[-1] == {'inicio': '2026-11-23', 'registros': 2, 'pessoas': 2}
    res = tr.resumo([r for r in regs if r['dia'] >= date(2026, 11, 1)], regs, idx, HOJE)
    assert res['kpis']['registros'] == 2 and res['kpis']['pessoas'] == 2
    assert res['kpis']['transferencias_pendentes'] == 1                 # a de outubro segue pendente


def test_linha_do_tempo_junta_tratativa_compra_e_troca_de_vendedor():
    idx = _idx((10, '2026-11-10', 300.0), (10, '2025-01-10', 9.0))
    regs = [_reg(1, 10, 'ligacao_feita', datetime(2026, 11, 3, 9)),
            _reg(2, 10, 'sem_contato', datetime(2026, 11, 4, 9), excluido_em=datetime(2026, 11, 4, tzinfo=UTC))]
    ev = tr.linha_do_tempo(regs, idx, 10, [(202609, 1), (202610, 1), (202611, 2)], {'1': 'Ana', '2': 'Bia'},
                           date(2025, 11, 26))
    assert [e['tipo'] for e in ev] == ['compra', 'tratativa', 'carteira']   # a excluída não aparece
    assert ev[2]['de'] == 'Ana' and ev[2]['para'] == 'Bia'
    adm = tr.linha_do_tempo(regs, idx, 10, [], {}, date(2025, 11, 26), mostrar_excluidos=True)
    assert any(e.get('excluido') for e in adm)


# ══════════════ servidor (modo postgres = base da demo) ══════════════
@pytest.fixture
def pg(monkeypatch):
    import server
    server._R.flushall()
    monkeypatch.setitem(server.CONFIG, 'data_source', 'postgres')
    return server


def _clientes(server, n=3):
    from flask import session
    with server.app.test_request_context('/'):
        session.update({'user_id': 1, 'role': 'admin'})
        cs = [c for c in server._carteira_no_escopo() if c.get('codsupervisor') and c.get('codusur')]
    sup = cs[0]['codsupervisor']
    do_time = [c for c in cs if c['codsupervisor'] == sup][:n]
    fora = next(c for c in cs if c['codsupervisor'] != sup)
    return sup, do_time, fora


def _semear(server, regs):
    conn = server.get_db()
    with conn, conn.cursor() as cur:
        cur.execute(server._PLANO_DDL)
        for codcli, status, dias_atras in regs:
            cur.execute("INSERT INTO cliente_plano (codcli, data_acao, status, autor_nome, criado_em) "
                        "VALUES (%s, CURRENT_DATE - %s, %s, 'Teste Trat', NOW() - make_interval(days => %s))",
                        (codcli, dias_atras, status, dias_atras))
    conn.close()


def _limpar(server, codclis):
    conn = server.get_db()
    with conn, conn.cursor() as cur:
        cur.execute(server._PLANO_DDL)
        cur.execute("DELETE FROM cliente_plano WHERE codcli = ANY(%s)", (list(codclis),))
    conn.close()


@pytest.fixture
def cenario(client, pg):
    from tests.conftest import _criar_usuario, _remover_usuario, login_as
    sup, do_time, fora = _clientes(pg)
    cods = [c['codcli'] for c in do_time] + [fora['codcli']]
    _limpar(pg, cods)
    _semear(pg, [(do_time[0]['codcli'], 'ligacao_feita', 20), (do_time[1]['codcli'], 'transferir', 10),
                 (fora['codcli'], 'ligacao_feita', 5)])
    email = 'sup-trat@teste.local'
    _criar_usuario(email, 'senha123', role='supervisor', codsupervisor=sup, codsupervisores=[sup])
    login_as(client, email, 'senha123')
    yield {'sup': sup, 'do_time': do_time, 'fora': fora}
    _remover_usuario(email)
    _limpar(pg, cods)


def test_supervisor_ve_so_o_proprio_time(client, cenario):
    j = client.get('/api/tratativas?dias=90').get_json()
    assert j['ok']
    vistos = {x['codcli'] for x in j['feed']}
    assert cenario['do_time'][0]['codcli'] in vistos and cenario['do_time'][1]['codcli'] in vistos
    assert cenario['fora']['codcli'] not in vistos
    assert {x['codcli'] for x in j['transferencias_pendentes']} >= {cenario['do_time'][1]['codcli']}
    assert set(j) >= {'kpis', 'conversao', 'autores', 'semanas', 'retornos_pendentes', 'opcoes', 'faixas'}


def test_linha_do_tempo_respeita_o_escopo(client, cenario):
    ok = client.get(f"/api/tratativas/cliente/{cenario['do_time'][0]['codcli']}")
    assert ok.status_code == 200 and any(e['tipo'] == 'tratativa' for e in ok.get_json()['eventos'])
    assert client.get(f"/api/tratativas/cliente/{cenario['fora']['codcli']}").status_code == 404


def test_periodo_invalido_volta_ao_padrao(client, cenario):
    assert client.get('/api/tratativas?dias=7').get_json()['dias'] == tr.PERIODO_PADRAO


def test_pagina_e_menu():
    from pathlib import Path
    assert "{ href: '/tratativas', rotulo: 'Tratativas' }" in Path('static/joga-header.js').read_text(encoding='utf-8')
    html = Path('tratativas.html').read_text(encoding='utf-8')
    assert '/api/tratativas' in html and 'data-joga-header' in html
