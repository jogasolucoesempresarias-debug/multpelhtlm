"""CRM leve: plano de ação por cliente (pedido do João, 29/09/2026 — PLANO_MELHORIAS §12).

Decisões dele:
- status: Ligação feita · Sem contato · Retorno agendado · Pedido prometido · Não compra mais · Transferir;
- data pode ser FUTURA (retorno agendado) e o retorno de hoje aparece EM DESTAQUE na lista do dia;
- quem tem acesso à lista pode registrar (mesma porta de escopo das telas), com o autor gravado;
- o acompanhamento ZERA quando o cliente compra (nada é apagado: vira "acompanhamento anterior");
- começa pela lista do dia (Próximo Pedido), Recuperação e ficha do cliente.
"""
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

import plano_cliente as pc

HOJE = date(2026, 9, 29)


# ══════════════ motor puro ══════════════
def test_os_seis_status_do_joao():
    assert [s for s, _ in pc.STATUS] == ['ligacao_feita', 'sem_contato', 'retorno_agendado',
                                          'pedido_prometido', 'nao_compra_mais', 'transferir']
    assert dict(pc.STATUS)['retorno_agendado'] == 'Retorno agendado'


def test_validacao_status_data_e_descricao():
    ok, erro = pc.validar({'status': 'ligacao_feita'}, HOJE)
    assert erro is None and ok == {'status': 'ligacao_feita', 'data_acao': HOJE, 'descricao': ''}
    assert pc.validar({'status': 'xyz'}, HOJE)[1]
    # só o retorno agendado aceita data futura — e ele EXIGE data de hoje em diante
    assert pc.validar({'status': 'ligacao_feita', 'data_acao': '2026-10-02'}, HOJE)[1]
    assert pc.validar({'status': 'retorno_agendado'}, HOJE)[1]
    assert pc.validar({'status': 'retorno_agendado', 'data_acao': '2026-09-28'}, HOJE)[1]
    ok, erro = pc.validar({'status': 'retorno_agendado', 'data_acao': '2026-10-02', 'descricao': ' ligar às 9h '}, HOJE)
    assert erro is None and ok['data_acao'] == date(2026, 10, 2) and ok['descricao'] == 'ligar às 9h'
    assert pc.validar({'status': 'sem_contato', 'data_acao': 'ontem'}, HOJE)[1]
    assert pc.validar({'status': 'sem_contato', 'descricao': 'x' * 501}, HOJE)[1]
    assert pc.validar({'status': 'sem_contato', 'data_acao': '2026-06-01'}, HOJE)[1]      # mais de 60 d atrás


def _reg(dia, status='ligacao_feita', data_acao=None, id_=None):
    return {'id': id_ or dia, 'status': status, 'data_acao': data_acao or date(2026, 9, dia), 'descricao': '',
            'autor_nome': 'Ana', 'criado_em': datetime(2026, 9, dia, 10, 0)}


def test_acompanhamento_zera_quando_o_cliente_compra():
    """Registro feito ATÉ o dia da última compra é do acompanhamento anterior; depois, é o atual."""
    regs = [_reg(10), _reg(20), _reg(25), _reg(27)]
    atual, anteriores = pc.separar(regs, ultima_compra=date(2026, 9, 20))
    assert [r['id'] for r in atual] == [27, 25]                       # mais recente primeiro
    assert [r['id'] for r in anteriores] == [20, 10]
    atual, anteriores = pc.separar(regs, ultima_compra=None)            # nunca comprou: tudo atual
    assert len(atual) == 4 and anteriores == []
    atual, _ = pc.separar(regs, ultima_compra='2026-09-26')            # aceita texto AAAA-MM-DD
    assert [r['id'] for r in atual] == [27]


def test_resumo_da_linha_e_o_retorno_em_destaque():
    r = pc.resumo([], HOJE)
    assert r == {'n': 0, 'ultimo': None, 'retorno': None, 'retorno_situacao': None, 'destaque': False}
    hoje = pc.resumo([_reg(29, 'retorno_agendado', HOJE)], HOJE)
    assert hoje['retorno'] == '2026-09-29' and hoje['retorno_situacao'] == 'hoje' and hoje['destaque']
    atras = pc.resumo([_reg(20, 'retorno_agendado', date(2026, 9, 25))], HOJE)
    assert atras['retorno_situacao'] == 'atrasado' and atras['destaque']
    fut = pc.resumo([_reg(28, 'retorno_agendado', date(2026, 10, 3))], HOJE)
    assert fut['retorno_situacao'] == 'futuro' and not fut['destaque']
    # registro DEPOIS do retorno resolve o agendamento
    feito = pc.resumo([_reg(29, 'ligacao_feita'), _reg(20, 'retorno_agendado', date(2026, 9, 25))], HOJE)
    assert feito['retorno'] is None and not feito['destaque'] and feito['n'] == 2
    assert feito['ultimo']['status'] == 'ligacao_feita' and feito['ultimo']['rotulo'] == 'Ligação feita'


# ══════════════ servidor (modo postgres = base da demo) ══════════════
@pytest.fixture
def pg(monkeypatch):
    import server
    server._R.flushall()
    monkeypatch.setitem(server.CONFIG, 'data_source', 'postgres')
    return server


def _limpar(server, codclis):
    conn = server.get_db()
    with conn, conn.cursor() as cur:
        cur.execute(server._PLANO_DDL)
        cur.execute("DELETE FROM cliente_plano WHERE codcli = ANY(%s)", (list(codclis),))
    conn.close()


def _cliente_do_time(server, sup):
    from flask import session
    with server.app.test_request_context('/'):
        session.update({'user_id': 1, 'role': 'admin'})
        return next(c for c in server._carteira_no_escopo()
                    if c.get('codsupervisor') == sup and c.get('ultima_compra'))


@pytest.fixture
def supervisor12(client, pg):
    from tests.conftest import _criar_usuario, _remover_usuario, login_as
    email = 'sup-plano@teste.local'
    _criar_usuario(email, 'senha123', role='supervisor', codsupervisor=12, codsupervisores=[12])
    login_as(client, email, 'senha123')
    yield email
    _remover_usuario(email)


def test_registrar_e_ler_o_plano(client, supervisor12, pg):
    server = pg
    c = _cliente_do_time(server, 12)
    _limpar(server, [c['codcli']])
    try:
        r = client.post(f"/api/plano/{c['codcli']}", json={'status': 'sem_contato', 'descricao': 'caixa postal'})
        assert r.status_code == 200 and r.get_json()['resumo']['n'] == 1
        j = client.get(f"/api/plano/{c['codcli']}").get_json()
        assert j['ok'] and [x['status'] for x in j['atual']] == ['sem_contato']
        assert j['atual'][0]['autor_nome'] and j['atual'][0]['descricao'] == 'caixa postal'
        assert [s['codigo'] for s in j['status_opcoes']][0] == 'ligacao_feita'
        assert client.post(f"/api/plano/{c['codcli']}", json={'status': 'xyz'}).status_code == 400
    finally:
        _limpar(server, [c['codcli']])


def test_fora_do_escopo_nao_le_nem_registra(client, supervisor12, pg):
    server = pg
    c = _cliente_do_time(server, 14)
    assert client.get(f"/api/plano/{c['codcli']}").status_code == 404
    assert client.post(f"/api/plano/{c['codcli']}", json={'status': 'sem_contato'}).status_code == 404
    j = client.post('/api/plano/resumo', json={'codclis': [c['codcli']]}).get_json()
    assert j['ok'] and j['resumos'] == {}


def test_registro_anterior_a_compra_vai_para_o_historico_anterior(client, supervisor12, pg):
    server = pg
    c = _cliente_do_time(server, 12)
    _limpar(server, [c['codcli']])
    ult = date.fromisoformat(c['ultima_compra'])
    conn = server.get_db()
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO cliente_plano (codcli, data_acao, status, descricao, autor_id, autor_nome, criado_em) "
                    "VALUES (%s,%s,'pedido_prometido','',NULL,'teste',%s)", (c['codcli'], ult, datetime.combine(ult, datetime.min.time())))
    conn.close()
    try:
        j = client.get(f"/api/plano/{c['codcli']}").get_json()
        assert j['atual'] == [] and [x['status'] for x in j['anteriores']] == ['pedido_prometido']
        assert j['resumo']['n'] == 0
    finally:
        _limpar(server, [c['codcli']])


def test_lista_do_dia_traz_o_plano_o_card_o_filtro_e_o_retorno_em_destaque(client, supervisor12, pg):
    server = pg
    hoje = server._hoje_ref()
    # um cliente do time FORA da janela "hoje" (atraso > 0) — o retorno de hoje tem de trazê-lo
    from flask import session
    with server.app.test_request_context('/'):
        session.update({'user_id': 1, 'role': 'admin'})
        fora = next(c for c in server._carteira_no_escopo()
                    if c.get('codsupervisor') == 12 and (c.get('dias_atraso') or 0) > 3 and c.get('ultima_compra'))
    _limpar(server, [fora['codcli']])
    try:
        assert client.post(f"/api/plano/{fora['codcli']}",
                           json={'status': 'retorno_agendado', 'data_acao': hoje.isoformat()}).status_code == 200
        j = client.get('/api/carteira/proximo-pedido?janela=hoje&limit=500').get_json()
        assert j['rows'][0]['codcli'] == fora['codcli']                       # retorno de hoje no TOPO
        assert j['rows'][0]['plano']['destaque'] and j['rows'][0]['plano']['retorno_situacao'] == 'hoje'
        assert all('plano' in r for r in j['rows'])
        assert j['cards']['com_tratativa'] >= 1 and j['cards']['retornos_hoje'] >= 1
        assert j['cards']['na_janela'] == j['total']
        sem = client.get('/api/carteira/proximo-pedido?janela=hoje&limit=500&tratativa=sem').get_json()
        assert fora['codcli'] not in {r['codcli'] for r in sem['rows']}
        com = client.get('/api/carteira/proximo-pedido?janela=hoje&limit=500&tratativa=com').get_json()
        assert {r['codcli'] for r in com['rows']} == {r['codcli'] for r in j['rows'] if r['plano']['n']}
    finally:
        _limpar(server, [fora['codcli']])


def test_listas_da_recuperacao_trazem_o_plano(client, supervisor12, pg):
    j = client.get('/api/recuperacao/listas?tipo=risco&limit=20').get_json()
    assert j['rows'] and all('plano' in r for r in j['rows'])


def test_resumo_em_lote_so_do_escopo(client, supervisor12, pg):
    server = pg
    c = _cliente_do_time(server, 12)
    j = client.post('/api/plano/resumo', json={'codclis': [c['codcli'], 'x', None]}).get_json()
    assert j['ok'] and str(c['codcli']) in j['resumos']


def test_telas_carregam_o_componente():
    for arq in ('carteira.html', 'recuperacao.html', 'index.html', 'vendedor.html'):
        assert '/static/plano-cliente.js' in Path(arq).read_text(encoding='utf-8'), arq
    js = Path('static/plano-cliente.js').read_text(encoding='utf-8')
    assert 'window.PlanoCliente' in js and "'/api/plano/'" in js and 'stopPropagation' in js
    assert 'PlanoCliente' in Path('static/drill-cliente.js').read_text(encoding='utf-8')
    cart = Path('carteira.html').read_text(encoding='utf-8')
    assert 'tratativa' in cart and 'com_tratativa' in cart
    assert 'CREATE TABLE IF NOT EXISTS cliente_plano' in Path('init_db.py').read_text(encoding='utf-8')
