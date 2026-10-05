"""Plano de ação — horário de registro e exclusão (pedido do Gabriel, 05/10/2026).

1. "Registrado em 13:38" quando eram 10:38. É a MESMA armadilha do `multpel_log` (08/2026): a coluna
   nasceu TIMESTAMP sem fuso, o NOW() roda no servidor do BANCO (outra stack, UTC) e o valor viajava
   cru — 3h adiantado. Além da hora, a DATA erra das 21h à meia-noite, e é pela data do registro que
   o acompanhamento separa "atual" de "anterior" à compra.
   Correção: coluna vira TIMESTAMPTZ (o histórico é reinterpretado no fuso do banco, que é o que o
   NOW() usou) e a tela recebe a hora em America/Sao_Paulo.

2. "Um x para excluir caso preencham errado". Regras:
   - quem registrou exclui o PRÓPRIO registro em até 24 h (o erro de digitação aparece na hora; um
     "não compra mais" de semanas atrás não some porque o vendedor mudou de ideia);
   - admin exclui qualquer um, a qualquer tempo;
   - exclusão é LÓGICA (excluido_em/excluido_por): some da tela e do selo, mas o rastro fica — o
     plano existe justamente para o gestor ver o que foi feito.
"""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import plano_cliente as pc

BRT = ZoneInfo('America/Sao_Paulo')


# ══════════════ motor puro ══════════════
def test_hora_do_banco_em_utc_vira_hora_de_brasilia():
    utc = datetime(2026, 10, 5, 13, 38, tzinfo=timezone.utc)
    assert pc.hora_local(utc) == datetime(2026, 10, 5, 10, 38)
    r = {'id': 1, 'status': 'transferir', 'data_acao': date(2026, 10, 5), 'descricao': '',
         'autor_nome': 'Ana', 'criado_em': utc}
    assert pc.serializar(r)['criado_em'] == '2026-10-05T10:38'


def test_registro_das_22h_e_do_dia_de_brasilia_e_nao_do_dia_seguinte_em_utc():
    """20/09 22:00 em Brasília = 21/09 01:00 UTC. Cliente comprou em 20/09: o registro é do
    acompanhamento que a compra encerrou, não do atual."""
    r = {'id': 1, 'status': 'ligacao_feita', 'data_acao': date(2026, 9, 20), 'descricao': '',
         'autor_nome': 'Ana', 'criado_em': datetime(2026, 9, 21, 1, 0, tzinfo=timezone.utc)}
    atual, anteriores = pc.separar([r], ultima_compra=date(2026, 9, 20))
    assert atual == [] and [x['id'] for x in anteriores] == [1]


def test_quem_pode_excluir():
    agora = datetime(2026, 10, 5, 13, 38, tzinfo=timezone.utc)
    reg = {'autor_id': 7, 'criado_em': agora - timedelta(hours=2)}
    assert pc.pode_excluir(reg, user_id=7, admin=False, agora=agora)          # autor, recente
    assert not pc.pode_excluir(reg, user_id=8, admin=False, agora=agora)      # outra pessoa
    assert pc.pode_excluir(reg, user_id=8, admin=True, agora=agora)           # admin
    velho = {'autor_id': 7, 'criado_em': agora - timedelta(hours=25)}
    assert not pc.pode_excluir(velho, user_id=7, admin=False, agora=agora)    # autor, passou de 24 h
    assert pc.pode_excluir(velho, user_id=7, admin=True, agora=agora)
    assert not pc.pode_excluir({'autor_id': None, 'criado_em': agora}, user_id=None, admin=False, agora=agora)


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
def dois_supervisores(client, pg):
    from tests.conftest import _criar_usuario, _remover_usuario, login_as
    a, b = 'sup-plano-a@teste.local', 'sup-plano-b@teste.local'
    ida = _criar_usuario(a, 'senha123', role='supervisor', codsupervisor=12, codsupervisores=[12])
    idb = _criar_usuario(b, 'senha123', role='supervisor', codsupervisor=12, codsupervisores=[12])
    login_as(client, a, 'senha123')
    yield {'a': (a, ida), 'b': (b, idb)}
    _remover_usuario(a)
    _remover_usuario(b)


def test_coluna_guarda_o_fuso(pg):
    _limpar(pg, [])
    conn = pg.get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT data_type FROM information_schema.columns "
                    "WHERE table_name = 'cliente_plano' AND column_name = 'criado_em'")
        assert cur.fetchone()[0] == 'timestamp with time zone'
    conn.close()
    assert "ALTER COLUMN criado_em TYPE TIMESTAMPTZ" in Path('init_db.py').read_text(encoding='utf-8')


def test_registro_sai_com_a_hora_de_brasilia(client, dois_supervisores, pg):
    c = _cliente_do_time(pg, 12)
    _limpar(pg, [c['codcli']])
    try:
        antes = datetime.now(BRT).replace(tzinfo=None)
        j = client.post(f"/api/plano/{c['codcli']}", json={'status': 'transferir'}).get_json()
        registrado = datetime.fromisoformat(j['atual'][0]['criado_em'])
        assert abs((registrado - antes).total_seconds()) < 180      # não 3 h adiantado
    finally:
        _limpar(pg, [c['codcli']])


def test_autor_exclui_o_proprio_e_o_rastro_fica(client, dois_supervisores, pg):
    c = _cliente_do_time(pg, 12)
    _limpar(pg, [c['codcli']])
    try:
        j = client.post(f"/api/plano/{c['codcli']}", json={'status': 'nao_compra_mais'}).get_json()
        reg = j['atual'][0]
        assert reg['pode_excluir'] is True
        r = client.delete(f"/api/plano/{c['codcli']}/{reg['id']}")
        assert r.status_code == 200
        j = r.get_json()
        assert j['atual'] == [] and j['resumo']['n'] == 0
        assert client.post('/api/plano/resumo', json={'codclis': [c['codcli']]}).get_json()[
            'resumos'][str(c['codcli'])]['n'] == 0
        conn = pg.get_db()
        with conn.cursor() as cur:                                     # exclusão lógica: o rastro fica
            cur.execute("SELECT excluido_em IS NOT NULL, excluido_por FROM cliente_plano WHERE id = %s",
                        (reg['id'],))
            assert cur.fetchone() == (True, dois_supervisores['a'][1])
        conn.close()
        assert client.delete(f"/api/plano/{c['codcli']}/{reg['id']}").status_code == 404   # já excluído
    finally:
        _limpar(pg, [c['codcli']])


def test_outra_pessoa_nao_exclui(client, dois_supervisores, pg):
    from tests.conftest import login_as
    c = _cliente_do_time(pg, 12)
    _limpar(pg, [c['codcli']])
    try:
        reg = client.post(f"/api/plano/{c['codcli']}", json={'status': 'sem_contato'}).get_json()['atual'][0]
        login_as(client, dois_supervisores['b'][0], 'senha123')
        j = client.get(f"/api/plano/{c['codcli']}").get_json()
        assert j['atual'][0]['pode_excluir'] is False
        assert client.delete(f"/api/plano/{c['codcli']}/{reg['id']}").status_code == 403
        assert client.get(f"/api/plano/{c['codcli']}").get_json()['resumo']['n'] == 1
    finally:
        _limpar(pg, [c['codcli']])


def test_autor_nao_exclui_depois_de_24h_mas_o_admin_sim(client, dois_supervisores, pg, usuario_admin):
    from tests.conftest import login_as
    c = _cliente_do_time(pg, 12)
    _limpar(pg, [c['codcli']])
    conn = pg.get_db()
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO cliente_plano (codcli, data_acao, status, autor_id, autor_nome, criado_em) "
                    "VALUES (%s, CURRENT_DATE, 'sem_contato', %s, 'a', NOW() - INTERVAL '25 hours') RETURNING id",
                    (c['codcli'], dois_supervisores['a'][1]))
        rid = cur.fetchone()[0]
    conn.close()
    try:
        assert client.delete(f"/api/plano/{c['codcli']}/{rid}").status_code == 403
        login_as(client, usuario_admin['email'], usuario_admin['senha'])
        assert client.delete(f"/api/plano/{c['codcli']}/{rid}").status_code == 200
    finally:
        _limpar(pg, [c['codcli']])


def test_fora_do_escopo_nao_exclui(client, dois_supervisores, pg):
    c = _cliente_do_time(pg, 14)
    assert client.delete(f"/api/plano/{c['codcli']}/1").status_code == 404


def test_tela_tem_o_x_de_excluir():
    js = Path('static/plano-cliente.js').read_text(encoding='utf-8')
    assert 'pode_excluir' in js and "method: 'DELETE'" in js and 'confirm(' in js
