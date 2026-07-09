"""Testes do endpoint /api/gerencial/cobertura + RBAC por cadastro (CODUSUR1 → time).

Fixtures (tests/fixtures): 4 clientes.
  cli1→codusur1 573 (sup 18) · cli2→100 (sup 18) · cli3→573 (sup 18) · cli4→820 (sup 99)
Escopos esperados:
  admin       → 4 clientes
  vendedor573 → 2 clientes (cli1, cli3)
  supervisor18→ 3 clientes (cli1, cli2, cli3)
"""
import json
import pathlib

import pytest

from tests.conftest import login_as

FIX_DIR = pathlib.Path(__file__).resolve().parent / 'fixtures'


def _load(nome):
    with open(FIX_DIR / f'{nome}.json', encoding='utf-8') as f:
        return json.load(f)


@pytest.fixture
def mock_carteira(monkeypatch):
    """Rotea as 6 queries DAX da carteira full pras fixtures existentes."""
    rotas = [
        ('UltimaCompra', 'dax_carteira_snapshot_rec'),
        ('Compras12m',   'dax_carteira_snapshot_freqmon'),
        ('MUNICENT',     'dax_carteira_meta'),
        ('PCSUPERV',     'dax_supervisores_map'),
        ('PCUSUARI',     'dax_vendedores_map'),
        ('CodCli',       'dax_carteira_datas'),
    ]
    loaded = [(kw, _load(name)) for kw, name in rotas]

    def _fake_execute(token, query, dataset_id=None):
        for kw, payload in loaded:
            if kw in query:
                return payload
        raise ValueError(f'Nenhuma fixture casou: {query[:120]!r}')

    import server
    monkeypatch.setattr(server, 'execute_dax', _fake_execute)
    monkeypatch.setattr(server, 'get_token_cached', lambda: 'fake-token')


def _get_cobertura(client, **params):
    q = '&'.join(f'{k}={v}' for k, v in params.items())
    return client.get('/api/gerencial/cobertura' + ('?' + q if q else ''))


def _soma(itens):
    return sum(g['total_clientes'] for g in itens)


def test_admin_ve_toda_a_empresa(client, usuario_admin, mock_carteira, clean_redis):
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    r = _get_cobertura(client)
    assert r.status_code == 200, r.get_data(as_text=True)
    d = r.get_json()
    assert d['ok']
    # Estrutura
    for k in ('empresa', 'times', 'vendedores', 'limiar_pct', 'abaixo_do_limiar', 'coberto_dias'):
        assert k in d
    # Empresa == 4 clientes; níveis reconciliam
    assert d['empresa']['total_clientes'] == 4
    assert _soma(d['times']) == 4
    assert _soma(d['vendedores']) == 4
    # 2 times (sup 18 com 3, sup 99 com 1)
    times_por_id = {t['id']: t['total_clientes'] for t in d['times']}
    assert times_por_id.get(18) == 3
    assert times_por_id.get(99) == 1


def test_ranking_pior_primeiro_e_flags(client, usuario_admin, mock_carteira, clean_redis):
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    d = _get_cobertura(client).get_json()
    # Lista ordenada pior→melhor por cobertura_clientes
    covs = [t['cobertura_clientes'] for t in d['times']]
    assert covs == sorted(covs)
    # Cada grupo carrega os campos do placar
    for g in d['times'] + d['vendedores']:
        for campo in ('cobertura_clientes', 'cobertura_valor', 'cobertura_ciclo',
                      'receita_em_risco', 'base_morta', 'buckets', 'rollup_0_30'):
            assert campo in g


def test_vendedor_so_ve_sua_carteira(client, usuario_vendedor, mock_carteira, clean_redis):
    login_as(client, usuario_vendedor['email'], usuario_vendedor['senha'])
    d = _get_cobertura(client).get_json()
    assert d['ok']
    assert d['empresa']['total_clientes'] == 2       # cli1 + cli3
    # Só o próprio codusur aparece nos vendedores
    ids = {v['id'] for v in d['vendedores']}
    assert ids == {573}


def test_supervisor_ve_apenas_suas_areas(client, usuario_supervisor, mock_carteira, clean_redis):
    login_as(client, usuario_supervisor['email'], usuario_supervisor['senha'])
    d = _get_cobertura(client).get_json()
    assert d['ok']
    assert d['empresa']['total_clientes'] == 3       # sup 18: cli1, cli2, cli3
    # Todos os times retornados são a área do supervisor (18)
    assert {t['id'] for t in d['times']} == {18}
    # Vendedores da área: 573 e 100
    assert {v['id'] for v in d['vendedores']} == {573, 100}


def test_coberto_dias_toggle_e_fallback(client, usuario_admin, mock_carteira, clean_redis):
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    assert _get_cobertura(client, coberto_dias=60).get_json()['coberto_dias'] == 60
    # valor inválido cai no default (30)
    assert _get_cobertura(client, coberto_dias=999).get_json()['coberto_dias'] == 30


def test_csv_export(client, usuario_admin, mock_carteira, clean_redis):
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    r = client.get('/api/gerencial/cobertura/csv')
    assert r.status_code == 200
    txt = r.get_data(as_text=True)
    assert 'EMPRESA' in txt and 'TIME' in txt
    assert 'attachment' in r.headers.get('Content-Disposition', '')
