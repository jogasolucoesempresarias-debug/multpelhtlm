"""Gate Fase 4 — RBAC SQL por papel no modo DATA_SOURCE=postgres.
Prova que o recorte por venda (escopo_where) funciona end-to-end via sessão para vendedor (codusur) e
supervisor (codsupervisor), não só admin. Valores reais do joga_demo (codusur 213, codsupervisor 14 têm
volume). Os codusur de teste do conftest (573) não existem no joga_demo — por isso injetamos um real.
"""
import provider_sql
import server
from tests.conftest import login_as

REAL_CODUSUR = 213   # vendedor com muita venda no joga_demo
REAL_CODSUP = 14     # supervisor com muita venda no joga_demo


def _postgres(monkeypatch):
    server._R.flushall()
    monkeypatch.setitem(server.CONFIG, 'data_source', 'postgres')


def _venda(client):
    return client.get('/api/dashboard/kpis').get_json()['primarios']['venda_liquida']


def test_rbac_vendedor_recorta_por_codusur(client, usuario_admin, usuario_vendedor, monkeypatch):
    _postgres(monkeypatch)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    total = _venda(client)
    assert total > 1_000_000

    server._R.flushall()
    login_as(client, usuario_vendedor['email'], usuario_vendedor['senha'])
    with client.session_transaction() as s:
        s['codusur'] = REAL_CODUSUR          # substitui o 573 do conftest por um real
    v = _venda(client)
    # recorta: subconjunto do total, não vazio
    assert 0 < v < total
    # bate centavo com o provider chamado direto (mesmo RBAC)
    ref = provider_sql.dashboard_kpis(
        {'role': 'vendedor', 'codusur': REAL_CODUSUR, 'supervisores': []})['primarios']['venda_liquida']
    assert abs(v - ref) < 0.01


def test_rbac_supervisor_recorta_por_codsupervisor(client, usuario_admin, usuario_supervisor, monkeypatch):
    _postgres(monkeypatch)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    total = _venda(client)

    server._R.flushall()
    login_as(client, usuario_supervisor['email'], usuario_supervisor['senha'])
    with client.session_transaction() as s:
        s['codusur'] = None
        s['codsupervisor'] = REAL_CODSUP
        s['codsupervisores'] = [REAL_CODSUP]
    v = _venda(client)
    assert 0 < v < total
    ref = provider_sql.dashboard_kpis(
        {'role': 'supervisor', 'codusur': None, 'supervisores': [REAL_CODSUP]})['primarios']['venda_liquida']
    assert abs(v - ref) < 0.01


def test_rbac_supervisor_sem_area_nao_ve_nada(monkeypatch):
    """escopo_where com supervisor sem áreas → recorte impossível (1=0), venda 0."""
    _postgres(monkeypatch)
    r = provider_sql.dashboard_kpis({'role': 'supervisor', 'codusur': None, 'supervisores': []})
    assert r['primarios']['venda_liquida'] == 0
