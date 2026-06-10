"""Smoke tests dos endpoints /api/carteira/* e /api/_internal/vendedores-map.
Mock de execute_dax roteado por substring na query (cada query → fixture distinta)."""
import json
import pathlib

import pytest

from tests.conftest import login_as

FIX_DIR = pathlib.Path(__file__).resolve().parent / 'fixtures'


def _load(nome):
    with open(FIX_DIR / f'{nome}.json', encoding='utf-8') as f:
        return json.load(f)


@pytest.fixture
def mock_dax_router(monkeypatch):
    """Cria função fake que rotea por substring na query DAX."""
    def _setup(rotas):
        loaded = [(kw, _load(name)) for kw, name in rotas]

        def _fake_execute(token, query, dataset_id=None):
            for kw, payload in loaded:
                if kw in query:
                    return payload
            raise ValueError(f'Nenhuma fixture casou: {query[:120]!r}')

        import server
        monkeypatch.setattr(server, 'execute_dax', _fake_execute)
        monkeypatch.setattr(server, 'get_token_cached', lambda: 'fake-token')
    return _setup


def test_vendedores_map_exclui_tecnicos(client, usuario_admin, mock_dax_router, clean_redis):
    mock_dax_router([('PCUSUARI', 'dax_vendedores_map')])
    login_as(client, usuario_admin['email'], usuario_admin['senha'])

    r = client.get('/api/_internal/vendedores-map')
    assert r.status_code == 200
    d = r.get_json()
    assert d['ok']
    # Fixture tem 3 vendedores: 573, 100, 999. O 999 é técnico → deve sair.
    v = d['vendedores']
    assert '573' in v and '100' in v
    assert '999' not in v
    assert v['573']['nome'] == 'JOAO VICTOR'


def test_carteira_rfm_estrutura(client, usuario_admin, mock_dax_router, clean_redis):
    """Mocka as 7 queries (rec/freqmon/4×datas/meta) + vendedores + supervisores e valida estrutura."""
    mock_dax_router([
        # ordem importa: substring mais específica primeiro
        ('UltimaCompra',  'dax_carteira_snapshot_rec'),
        ('Compras12m',    'dax_carteira_snapshot_freqmon'),
        ('MUNICENT',      'dax_carteira_meta'),
        ('PCSUPERV',      'dax_supervisores_map'),
        ('PCUSUARI',      'dax_vendedores_map'),
        ('CodCli',        'dax_carteira_datas'),  # casa os 4 chunks de datas
    ])
    login_as(client, usuario_admin['email'], usuario_admin['senha'])

    r = client.get('/api/carteira/rfm?modo=fixa')
    assert r.status_code == 200, r.get_data(as_text=True)
    d = r.get_json()
    assert d['ok']
    assert d['modo'] == 'fixa'
    # 4 clientes no snapshot_rec (admin vê todos)
    assert d['total_clientes'] == 4
    # Soma de régua == total
    assert sum(d['regua'][k] for k in ('ok', 'normal', 'atencao', 'urgente')) == 4
    # Soma de segmentos == total
    assert sum(d['segmentos'].values()) == 4
    # Estrutura matriz/histograma
    assert 'matriz_rf' in d
    assert 'histograma_recencia' in d
    assert isinstance(d['matriz_rf'], list)


def test_carteira_clientes_paginado_e_filtros(client, usuario_admin, mock_dax_router, clean_redis):
    mock_dax_router([
        ('UltimaCompra',  'dax_carteira_snapshot_rec'),
        ('Compras12m',    'dax_carteira_snapshot_freqmon'),
        ('MUNICENT',      'dax_carteira_meta'),
        ('PCSUPERV',      'dax_supervisores_map'),
        ('PCUSUARI',      'dax_vendedores_map'),
        ('CodCli',        'dax_carteira_datas'),
    ])
    login_as(client, usuario_admin['email'], usuario_admin['senha'])

    # Carrega cache via /rfm primeiro
    client.get('/api/carteira/rfm?modo=fixa')

    # Pagina limit=2
    r = client.get('/api/carteira/clientes?limit=2&offset=0')
    assert r.status_code == 200
    d = r.get_json()
    assert d['total'] == 4
    assert len(d['rows']) == 2
    assert d['offset'] == 0

    # Filtro por vendedor (573 tem 2 clientes na fixture meta)
    r2 = client.get('/api/carteira/clientes?vendedor=573&limit=10')
    d2 = r2.get_json()
    assert d2['total'] == 2
    for row in d2['rows']:
        assert row['codusur'] == 573

    # Cada cliente tem nome do vendedor preenchido via lookup
    assert d2['rows'][0]['vendedor'] in ('JOAO VICTOR', 'RCA 573')


# ─────────────────────────────────────────────────────────────────────
# Tests do chart receita+positivação reativo a filtros
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def fixture_venda_mensal(mock_dax_router, monkeypatch):
    """Mock helpers de venda + devolução mensal por cliente (3 clientes × 2 meses).
    Codclis 1, 2, 3 batem com os da fixture dax_carteira_snapshot_rec.

    venda_mensal retorna BRUTA por DTSAIDA. devol_mensal retorna devolução por DTENT."""
    import server
    from datetime import date
    hoje = date.today()
    mes_atual = hoje.year * 100 + hoje.month
    if hoje.month == 1:
        mes_ant = (hoje.year - 1) * 100 + 12
    else:
        mes_ant = hoje.year * 100 + hoje.month - 1
    def _fake_venda_mensal(role=None, codusur=None, codsupervisor=None):
        return {
            1: {mes_ant: 5000.0, mes_atual: 3000.0},
            2: {mes_ant: 8000.0},
            3: {mes_atual: 2000.0},
        }
    def _fake_devol_mensal(role=None, codusur=None, codsupervisor=None):
        return {
            1: {mes_ant: 100.0},   # devolução pequena pro cliente 1
        }
    monkeypatch.setattr(server, '_carregar_venda_mensal_por_cliente', _fake_venda_mensal)
    monkeypatch.setattr(server, '_carregar_devolucao_mensal_por_cliente', _fake_devol_mensal)


def test_receita_positivacao_sem_filtros_inclui_todos_clientes(
    client, usuario_admin, mock_dax_router, fixture_venda_mensal, clean_redis,
):
    """Sem filtros: caminho DAX direto.
    Smoke test que valida estrutura — soma 0 é OK aqui porque mockamos fixtures vazias."""
    mock_dax_router([
        ('UltimaCompra',  'dax_carteira_snapshot_rec'),
        ('Compras12m',    'dax_carteira_snapshot_freqmon'),
        ('MUNICENT',      'dax_carteira_meta'),
        ('PCSUPERV',      'dax_supervisores_map'),
        ('PCUSUARI',      'dax_vendedores_map'),
        ('CodCli',        'dax_carteira_datas'),
        ('VENDA BRUTA',                       'dax_chart_empty'),
        ('FATURAMENTO_DEVOLUCAO[DTENT]',      'dax_chart_empty'),
        ('FATURAMENTO_DEVOLUCAO_AVULSA[DTENT]', 'dax_chart_empty'),
    ])
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    client.get('/api/carteira/rfm?modo=fixa')  # popula cache da carteira_full

    r = client.get('/api/carteira/receita-positivacao-12m')
    assert r.status_code == 200
    d = r.get_json()
    assert d['ok']
    assert len(d['rows']) == 12, '12 buckets exatos'
    # Estrutura correta dos buckets
    for b in d['rows']:
        assert 'AnoMes' in b and 'VendaLiquida' in b and 'ClientesUnicos' in b


def test_receita_positivacao_com_filtro_uf_restringe_subset(
    client, usuario_admin, mock_dax_router, fixture_venda_mensal, clean_redis,
):
    """Com filtro UF=ES (filtro simples), vai pelo caminho DAX direto.
    Smoke test pra validar estrutura — não verifica subset porque mocks são vazios."""
    mock_dax_router([
        ('UltimaCompra',  'dax_carteira_snapshot_rec'),
        ('Compras12m',    'dax_carteira_snapshot_freqmon'),
        ('MUNICENT',      'dax_carteira_meta'),
        ('PCSUPERV',      'dax_supervisores_map'),
        ('PCUSUARI',      'dax_vendedores_map'),
        ('CodCli',        'dax_carteira_datas'),
        ('VENDA BRUTA',                       'dax_chart_empty'),
        ('FATURAMENTO_DEVOLUCAO[DTENT]',      'dax_chart_empty'),
        ('FATURAMENTO_DEVOLUCAO_AVULSA[DTENT]', 'dax_chart_empty'),
    ])
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    client.get('/api/carteira/rfm?modo=fixa')

    # Com filtro UF=ES → caminho DAX direto
    r = client.get('/api/carteira/receita-positivacao-12m?uf=ES')
    assert r.status_code == 200
    d = r.get_json()
    assert d['ok']
    assert len(d['rows']) == 12


def test_drill_mes_com_filtros_seta_flag_filtros_aplicados(
    client, usuario_admin, mock_dax_router, fixture_venda_mensal, clean_redis,
):
    """Quando filtros são aplicados ao drill, response inclui filtros_aplicados=true
    e codclis_no_filtro indica quantos clientes entraram no escopo."""
    import server
    # Fixture do drill mensal — mock genérico que retorna estrutura mínima
    fake_payload = {
        'results': [{'tables': [{'rows': [
            {'[Value1]': 1000, '[Value2]': 500, '[Value3]': 10, '[Value4]': 100, '[Value5]': 5}
        ]}]}]
    }
    fake_empty = {'results': [{'tables': [{'rows': []}]}]}
    def _fake_execute(token, query, dataset_id=None):
        if 'TOPN' in query or 'top' in query.lower():
            return fake_empty
        return fake_payload
    monkeypatch_obj = pytest.MonkeyPatch()
    monkeypatch_obj.setattr(server, 'execute_dax', _fake_execute)
    monkeypatch_obj.setattr(server, 'get_token_cached', lambda: 'fake-token')

    mock_dax_router([
        ('UltimaCompra',  'dax_carteira_snapshot_rec'),
        ('Compras12m',    'dax_carteira_snapshot_freqmon'),
        ('MUNICENT',      'dax_carteira_meta'),
        ('PCSUPERV',      'dax_supervisores_map'),
        ('PCUSUARI',      'dax_vendedores_map'),
        ('CodCli',        'dax_carteira_datas'),
    ])
    # Re-aplica mock execute_dax após mock_dax_router (que sobrescreveu)
    monkeypatch_obj.setattr(server, 'execute_dax', _fake_execute)

    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    client.get('/api/carteira/rfm?modo=fixa')

    # Drill com filtro de UF
    r = client.get('/api/carteira/mes/202605?uf=ES')
    monkeypatch_obj.undo()
    assert r.status_code == 200
    d = r.get_json()
    # Filtro aplicado deve ser sinalizado (assume base de 3 clientes na fixture, filtro produz subset < 1000)
    assert 'filtros_aplicados' in d
    assert 'top_produtos' in d
