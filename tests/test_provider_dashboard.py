"""Gate Fase 2 — /api/dashboard/kpis no modo DATA_SOURCE=postgres (lê do joga_demo)."""
import server
import provider_sql
from tests.conftest import login_as


def test_dashboard_kpis_modo_postgres(client, usuario_admin, monkeypatch):
    server._R.flushall()
    monkeypatch.setitem(server.CONFIG, 'data_source', 'postgres')

    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    resp = client.get('/api/dashboard/kpis')

    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True
    p = data['primarios']
    # estrutura idêntica ao contrato da rota DAX
    assert set(p) == {'venda_liquida', 'lucro_total', 'margem', 'ticket_medio'}
    assert set(data['secundarios']) == {'total_mix', 'clientes_novos',
                                         'valor_medio_peso', 'clientes_positivados'}
    assert set(data) >= {'ok', 'primarios', 'secundarios', 'yoy', 'yoy_mes'}
    # yoy e yoy_mes têm o shape que o front lê (_yoy_parse): 4 percentuais, NÃO {atual,anterior,variacao}.
    # (o front lê d.yoy_mes.receita_liquida/lucro_bruto/positivacao_cliente nos badges dos cards)
    yoy_keys = {'receita_liquida', 'lucro_bruto', 'positivacao_cliente', 'positivacao_mix'}
    assert set(data['yoy']) == yoy_keys
    assert data['yoy_mes'] is None or set(data['yoy_mes']) == yoy_keys
    if data['yoy_mes'] is not None:
        assert set(data['yoy_mes_info']) == {'rotulo', 'periodo', 'dias_uteis', 'dias_uteis_anterior'}
    # números coerentes (base cheia da demo)
    assert p['venda_liquida'] > 1_000_000

    # bate com o provider chamado direto (admin = sem RBAC)
    ref = provider_sql.dashboard_kpis({'role': 'admin', 'codusur': None, 'supervisores': []})
    assert abs(p['venda_liquida'] - ref['primarios']['venda_liquida']) < 0.01


def test_dashboard_subendpoints_modo_postgres(client, usuario_admin, monkeypatch):
    server._R.flushall()
    monkeypatch.setitem(server.CONFIG, 'data_source', 'postgres')
    login_as(client, usuario_admin['email'], usuario_admin['senha'])

    r = client.get('/api/dashboard/serie?periodo=12m').get_json()
    assert r['ok'] and len(r['rows']) > 0 and 'VendaLiquida' in r['rows'][0]

    r = client.get('/api/dashboard/sazonalidade').get_json()
    assert r['ok'] and len(r['rows']) > 0 and {'Ano', 'MES', 'VendaLiquida'} <= set(r['rows'][0])

    r = client.get('/api/dashboard/pareto?top=20').get_json()
    assert r['ok'] and 0 < len(r['rows']) <= 20
    assert r['rows'][0]['Venda12m'] >= r['rows'][-1]['Venda12m']

    r = client.get('/api/dashboard/yoy').get_json()
    assert r['ok'] and set(r['yoy']) == {'receita_liquida', 'lucro_bruto',
                                         'positivacao_cliente', 'positivacao_mix'}


def test_dashboard_top_clientes_modo_postgres(client, usuario_admin, monkeypatch):
    server._R.flushall()
    monkeypatch.setitem(server.CONFIG, 'data_source', 'postgres')
    login_as(client, usuario_admin['email'], usuario_admin['senha'])

    r = client.get('/api/dashboard/top-clientes?metrica=lucro&limit=10').get_json()
    assert r['ok'] is True and r['metrica'] == 'lucro'
    assert 0 < len(r['rows']) <= 10
    row = r['rows'][0]
    assert {'CODCLI', 'CLIENTE', 'UF', 'CODUSUR', 'Venda12m', 'Lucro12m'} <= set(row)
    # ordenado por lucro desc
    assert [x['Lucro12m'] for x in r['rows']] == sorted((x['Lucro12m'] for x in r['rows']), reverse=True)

    server._R.flushall()
    rv = client.get('/api/dashboard/top-clientes?metrica=venda&limit=5').get_json()
    assert rv['ok'] is True and rv['metrica'] == 'venda' and 0 < len(rv['rows']) <= 5
    assert [x['Venda12m'] for x in rv['rows']] == sorted((x['Venda12m'] for x in rv['rows']), reverse=True)


def test_dashboard_kpis_default_powerbi_intocado(monkeypatch):
    """Garantia: o default segue powerbi (modo BD não liga sozinho)."""
    assert server.CONFIG['data_source'] == 'powerbi'
