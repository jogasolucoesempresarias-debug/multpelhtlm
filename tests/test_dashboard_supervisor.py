"""Testes do filtro de supervisores no Dashboard + YoY recalculado +
tabelas Top 10 (departamentos/vendedores) respeitando o filtro de supervisor.
"""
import json
import pathlib

from tests.conftest import login_as

FIX_DIR = pathlib.Path(__file__).resolve().parent / 'fixtures'


def _load(name):
    with open(FIX_DIR / f'{name}.json', encoding='utf-8') as f:
        return json.load(f)


def test_admin_supervisor_injeta_in_nas_queries(client, usuario_admin, mock_dax_capture, clean_redis):
    """Admin com ?supervisor=18,19 → queries do KPI contêm CODSUPERVISOR IN {18, 19}."""
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    r = client.get('/api/dashboard/kpis?supervisor=18,19')
    assert r.status_code == 200

    assert any('FATURAMENTO_VENDAS[CODSUPERVISOR] IN {18, 19}' in q for q in mock_dax_capture.queries), \
        'Esperava CODSUPERVISOR IN {18, 19} nas queries do dashboard'
    # E nas tabelas de devolução também (alinhamento RCA)
    assert any('FATURAMENTO_DEVOLUCAO[CODSUPERVISOR] IN {18, 19}' in q for q in mock_dax_capture.queries)


def test_admin_sem_filtro_nao_injeta_supervisor(client, usuario_admin, mock_dax_capture, clean_redis):
    """Admin sem ?supervisor → nenhum fragmento CODSUPERVISOR IN."""
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    r = client.get('/api/dashboard/kpis')
    assert r.status_code == 200
    assert not any('CODSUPERVISOR] IN' in q for q in mock_dax_capture.queries)


def test_vendedor_ignora_param_supervisor(client, usuario_vendedor, mock_dax_capture, clean_redis):
    """Vendedor (573) passando ?supervisor=18 NÃO deve ampliar escopo:
    queries seguem só com CODUSUR = 573 e SEM CODSUPERVISOR IN."""
    login_as(client, usuario_vendedor['email'], usuario_vendedor['senha'])
    r = client.get('/api/dashboard/kpis?supervisor=18')
    assert r.status_code == 200

    queries_filter = [q for q in mock_dax_capture.queries if 'FILTER' in q and 'FATURAMENTO_VENDAS' in q]
    assert len(queries_filter) > 0
    for q in queries_filter:
        assert 'FATURAMENTO_VENDAS[CODUSUR] = 573' in q, f'esperava lock do vendedor:\n{q[:300]}'
    assert not any('CODSUPERVISOR] IN' in q for q in mock_dax_capture.queries), \
        'param supervisor NÃO pode ampliar escopo de vendedor'


def test_yoy_sem_filtro_tambem_recalcula_rca(client, usuario_admin, mock_dax_capture, clean_redis):
    """Sem supervisor → YoY também é recalculado RCA (12m vs 12m_anterior), NÃO medida nativa.
    Garante 'a mesma conta' global e por-supervisor (só muda o escopo)."""
    mock_dax_capture.set_routes([
        ('TOTAL MIX', {'results': [{'tables': [{'rows': [{
            '[Value1]': 1200, '[Value2]': 1000,   # receita +20%
            '[Value3]': 300,  '[Value4]': 250,    # lucro +20%
            '[Value5]': 110,  '[Value6]': 100,    # cliente +10%
            '[Value7]': 55,   '[Value8]': 50,     # mix +10%
        }]}]}]}),
    ])
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    r = client.get('/api/dashboard/yoy')
    assert r.status_code == 200
    # NÃO usa mais a medida nativa de crescimento
    assert not any('Crescimento Ano a Ano' in q for q in mock_dax_capture.queries)
    # usa a janela anterior (12-24m) e NÃO tem filtro de supervisor
    assert any('EDATE(TODAY(), -24)' in q and '< EDATE(TODAY(), -12)' in q for q in mock_dax_capture.queries)
    assert not any('CODSUPERVISOR] IN' in q for q in mock_dax_capture.queries)
    yoy = r.get_json()['yoy']
    assert abs(yoy['receita_liquida'] - 0.20) < 1e-9


def test_yoy_com_supervisor_recalcula(client, usuario_admin, mock_dax_capture, clean_redis):
    """Com supervisor → recalcula janela 12m vs 12m_anterior e faz (atual-ant)/ant.
    Payload: VL 1200/1000(+20%), Lucro 300/250(+20%), Cli 110/100(+10%), Mix 55/50(+10%)."""
    mock_dax_capture.set_routes([
        # query de recalc tem [TOTAL MIX] (a nativa de Crescimento não tem)
        ('TOTAL MIX', {'results': [{'tables': [{'rows': [{
            '[Value1]': 1200, '[Value2]': 1000,
            '[Value3]': 300,  '[Value4]': 250,
            '[Value5]': 110,  '[Value6]': 100,
            '[Value7]': 55,   '[Value8]': 50,
        }]}]}]}),
    ])
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    r = client.get('/api/dashboard/yoy?supervisor=18')
    assert r.status_code == 200

    # Query de recalc deve referenciar a janela anterior (12-24m)
    assert any('EDATE(TODAY(), -24)' in q and '< EDATE(TODAY(), -12)' in q
               for q in mock_dax_capture.queries), 'esperava janela 12m_anterior na query'
    assert any('FATURAMENTO_VENDAS[CODSUPERVISOR] IN {18}' in q for q in mock_dax_capture.queries)

    yoy = r.get_json()['yoy']
    assert abs(yoy['receita_liquida'] - 0.20) < 1e-9
    assert abs(yoy['lucro_bruto'] - 0.20) < 1e-9
    assert abs(yoy['positivacao_cliente'] - 0.10) < 1e-9
    assert abs(yoy['positivacao_mix'] - 0.10) < 1e-9


# ── Tabelas Top 10 do Dashboard respeitando o filtro de supervisor ──


def test_categorias_supervisor_injeta_in(client, usuario_admin, mock_dax_capture, clean_redis):
    """Top 10 departamentos: /api/categorias?supervisor=18,19 injeta CODSUPERVISOR IN {18, 19}."""
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    r = client.get('/api/categorias?supervisor=18,19')
    assert r.status_code == 200
    assert any('FATURAMENTO_VENDAS[CODSUPERVISOR] IN {18, 19}' in q for q in mock_dax_capture.queries)


def test_categoria_drill_supervisor_injeta_in(client, usuario_admin, mock_dax_capture, clean_redis):
    """Drill de departamento: /api/categorias/<id>/clientes?supervisor=18 injeta o filtro no FILTER."""
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    r = client.get('/api/categorias/5/clientes?supervisor=18')
    assert r.status_code == 200
    assert any('FATURAMENTO_VENDAS[CODSUPERVISOR] IN {18}' in q for q in mock_dax_capture.queries)


def test_categorias_vendedor_ignora_param(client, usuario_vendedor, mock_dax_capture, clean_redis):
    """Categorias por CADASTRO: vendedor 573 restringe por CODCLI IN {1,3} (clientes registrados
    nele), e o ?supervisor=18 é ignorado (nada de CODSUPERVISOR IN)."""
    mock_dax_capture.set_routes([
        ('DEPARTAMENTO', _load('dax_deptos_nomes')),
        ('SECAO',        _load('dax_secoes_nomes')),
        ('CODEPTO',      _load('dax_categorias')),
        ('UltimaCompra', _load('dax_carteira_snapshot_rec')),
        ('Compras12m',   _load('dax_carteira_snapshot_freqmon')),
        ('MUNICENT',     _load('dax_carteira_meta')),
        ('PCSUPERV',     _load('dax_supervisores_map')),
        ('PCUSUARI',     _load('dax_vendedores_map')),
        ('CodCli',       _load('dax_carteira_datas')),
    ])
    login_as(client, usuario_vendedor['email'], usuario_vendedor['senha'])
    r = client.get('/api/categorias?supervisor=18')
    assert r.status_code == 200
    assert not any('CODSUPERVISOR] IN' in q for q in mock_dax_capture.queries)
    qcat = [q for q in mock_dax_capture.queries if 'CODEPTO' in q and 'SUMMARIZECOLUMNS' in q]
    assert qcat and any('FATURAMENTO_VENDAS[CODCLI] IN {1, 3}' in q for q in qcat)


def test_vendedores_multi_supervisor(client, usuario_admin, mock_dax_capture, clean_redis):
    """Top 10 vendedores: ?supervisor=18,99 (CSV) retorna vendedores dos dois times."""
    mock_dax_capture.set_routes([
        ('PCUSUARI[CODUSUR]', _load('dax_vendedores_map')),
        ('VendaLiqAnt',       _load('dax_vendedores_anterior')),
        ('TicketMedio',       _load('dax_vendedores_metricas')),
        ('CarteiraOficial',   _load('dax_vendedores_carteira24m')),
        ('VendaLiq',          _load('dax_vendedores_ranking')),
    ])
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    r = client.get('/api/vendedores?tipovend=R&supervisor=18,99')
    assert r.status_code == 200
    d = r.get_json()
    assert d['total'] == 3  # 573 e 100 (sup 18) + 820 (sup 99)
    assert {v['codsupervisor'] for v in d['vendedores']} == {18, 99}
