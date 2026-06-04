"""Smoke tests dos endpoints /api/vendedores e /api/vendedor/<codusur>.
Mock 3 queries do ranking + 1 perfil + 1 vendedores-map por substring routing."""
import json
import pathlib

import pytest

from tests.conftest import login_as

FIX_DIR = pathlib.Path(__file__).resolve().parent / 'fixtures'


def _load(name):
    with open(FIX_DIR / f'{name}.json', encoding='utf-8') as f:
        return json.load(f)


def test_ranking_estrutura(client, usuario_admin, mock_dax_capture, clean_redis):
    """Mock 4 queries (vendedores_map, ranking vendas atual/anterior, métricas),
    valida estrutura da resposta e que YoY é calculado manualmente."""
    mock_dax_capture.set_routes([
        ('PCUSUARI[CODUSUR]', _load('dax_vendedores_map')),
        ('VendaLiqAnt',       _load('dax_vendedores_anterior')),
        ('TicketMedio',       _load('dax_vendedores_metricas')),
        ('VendaLiq',          _load('dax_vendedores_ranking')),  # último (mais genérico)
    ])
    login_as(client, usuario_admin['email'], usuario_admin['senha'])

    r = client.get('/api/vendedores?tipovend=R')
    assert r.status_code == 200, r.get_data(as_text=True)
    d = r.get_json()
    assert d['ok']
    # 3 vendedores na fixture: 573 (sup 18), 100 (sup 18), 820 (sup 99). Todos TIPOVEND=R.
    assert d['total'] == 3
    vendedores = d['vendedores']
    # Ordenado por lucro desc
    assert vendedores[0]['codusur'] == 573  # maior lucro
    assert vendedores[0]['rank'] == 1
    assert vendedores[0]['nome'] == 'JOAO VICTOR'
    assert vendedores[0]['venda_liq'] == 850000
    # YoY = (850000 - 720000) / 720000 ≈ 0.181
    assert vendedores[0]['yoy_receita'] == pytest.approx(0.18, abs=0.01)
    # Métricas populadas
    assert vendedores[0]['ticket_medio'] == 178.5
    assert vendedores[0]['taxa_positivacao'] == 0.62


def test_ranking_filtro_supervisor(client, usuario_admin, mock_dax_capture, clean_redis):
    """Filtro ?supervisor=18 retorna só vendedores do time."""
    mock_dax_capture.set_routes([
        ('PCUSUARI[CODUSUR]', _load('dax_vendedores_map')),
        ('VendaLiqAnt',       _load('dax_vendedores_anterior')),
        ('TicketMedio',       _load('dax_vendedores_metricas')),
        ('VendaLiq',          _load('dax_vendedores_ranking')),
    ])
    login_as(client, usuario_admin['email'], usuario_admin['senha'])

    r = client.get('/api/vendedores?tipovend=R&supervisor=18')
    d = r.get_json()
    assert d['total'] == 2  # 573 e 100 (não 820 que está em sup=99)
    for v in d['vendedores']:
        assert v['codsupervisor'] == 18
