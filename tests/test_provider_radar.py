"""Gate Fase 2 — tela Radar (produtos sangrando + drills) no modo DATA_SOURCE=postgres."""
import server
from tests.conftest import login_as


def _postgres(monkeypatch):
    server._R.flushall()
    monkeypatch.setitem(server.CONFIG, 'data_source', 'postgres')


def test_radar_busca_board_produto_cliente(client, usuario_admin, monkeypatch):
    _postgres(monkeypatch)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])

    # busca type-ahead (índice de produtos)
    b = client.get('/api/radar/produtos/busca?q=&limit=10').get_json()
    assert b['ok'] is True and len(b['produtos']) > 0
    p0 = b['produtos'][0]
    assert {'codprod', 'descricao', 'venda_12m'} <= set(p0)

    # board: produtos que perderam receita
    board = client.get('/api/radar/board?dias=60&limit=50').get_json()
    assert board['ok'] is True and board['total'] > 0 and len(board['rows']) <= 50
    prod = board['rows'][0]
    assert {'codprod', 'queda_receita', 'venda_rec', 'venda_ant', 'clientes_perdidos'} <= set(prod)
    # board só mostra quem está sangrando (queda > 0), ordenado por queda desc
    assert all(x['queda_receita'] > 0 for x in board['rows'])
    assert board['rows'] == sorted(board['rows'], key=lambda x: x['queda_receita'], reverse=True)

    # detalhe do produto (clientes com status de recência)
    codprod = prod['codprod']
    det = client.get(f'/api/radar/produto/{codprod}?dias=60').get_json()
    assert det['ok'] is True and det['codprod'] == codprod
    assert 'rows' in det and 'kpis' in det
    assert det['kpis']['clientes'] == len(det['rows'])

    # drill invertido por cliente (usa um codcli que compra o produto)
    if det['rows']:
        codcli = det['rows'][0]['codcli']
        rc = client.get(f'/api/radar/cliente/{codcli}?dias=60').get_json()
        assert rc['ok'] is True and rc['codcli'] == codcli
        assert 'kpis' in rc


def test_radar_default_powerbi_intocado():
    assert server.CONFIG['data_source'] == 'powerbi'
