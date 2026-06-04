"""Smoke test do endpoint /api/dashboard/kpis com DAX mockado."""
from tests.conftest import login_as


def test_kpis_estrutura(client, usuario_admin, mock_dax, clean_redis):
    """Mocka execute_dax pra retornar dax_kpis.json e valida que endpoint:
    - exige login
    - retorna 200
    - tem chaves esperadas (primarios + secundarios + yoy)
    """
    # 401 sem login
    r0 = client.get('/api/dashboard/kpis')
    assert r0.status_code == 401

    # Login admin
    login_as(client, usuario_admin['email'], usuario_admin['senha'])

    # Mocka DAX
    mock_dax('dax_kpis')

    # Chama endpoint
    r = client.get('/api/dashboard/kpis')
    assert r.status_code == 200, r.get_data(as_text=True)
    data = r.get_json()
    assert data['ok'] is True
    assert 'primarios' in data
    assert 'secundarios' in data
    assert 'yoy' in data
    # Estrutura de cada bloco
    assert 'venda_liquida' in data['primarios']
    assert 'lucro_total' in data['primarios']
    assert 'margem' in data['primarios']
    assert 'ticket_medio' in data['primarios']
