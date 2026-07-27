"""Gate Fase 2 — tela Mix abandonado no modo DATA_SOURCE=postgres (lê do joga_demo)."""
import server
from tests.conftest import login_as


def _postgres(monkeypatch):
    server._R.flushall()
    monkeypatch.setitem(server.CONFIG, 'data_source', 'postgres')


def test_mix_abandonado_e_drills_modo_postgres(client, usuario_admin, monkeypatch):
    _postgres(monkeypatch)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])

    r = client.get('/api/mix/abandonado?dias=60&limit=50').get_json()
    assert r['ok'] is True
    assert r['total'] > 0 and 0 < len(r['rows']) <= 50
    row = r['rows'][0]
    assert {'codcli', 'codepto', 'ultima_compra', 'dias_sem_comprar_categoria',
            'venda_cat_12m', 'lucro_cat_12m'} <= set(row)
    # ordenado por lucro do depto desc; todos parados há >= 60 dias
    assert r['rows'] == sorted(r['rows'], key=lambda x: x['lucro_cat_12m'], reverse=True)
    assert all(x['dias_sem_comprar_categoria'] >= 60 for x in r['rows'])

    codcli = row['codcli']
    d = client.get(f'/api/mix/abandonado/{codcli}/deptos?dias=60').get_json()
    assert d['ok'] is True and d['codcli'] == codcli
    assert len(d['rows']) <= 5

    f = client.get(f'/api/mix/cliente/{codcli}/fornecedores?dias=60').get_json()
    assert f['ok'] is True
    assert 'rows' in f


def test_mix_default_powerbi_intocado():
    assert server.CONFIG['data_source'] == 'powerbi'
