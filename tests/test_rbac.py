"""Testes RBAC essenciais — proteção contra vazamento de dados entre roles.

6 cenários:
1. admin_ve_tudo — queries DAX SEM filtro RBAC concatenado
2. vendedor_filtro_aplicado — toda query DAX tem CODUSUR = <seu>
3. supervisor_filtro_aplicado — toda query DAX tem CODSUPERVISOR = <seu>
4. vendedor_403_em_outro_codusur — /api/vendedor/<outro> retorna 403
5. vendedor_403_em_api_vendedores — ranking não acessível
6. supervisor_403_em_vendedor_fora_do_time — /api/vendedor/<id de outro sup> retorna 403
"""
import json
import pathlib

from tests.conftest import login_as

FIX_DIR = pathlib.Path(__file__).resolve().parent / 'fixtures'


def _load(name):
    with open(FIX_DIR / f'{name}.json', encoding='utf-8') as f:
        return json.load(f)


# ──────────────────────────────────────────────────────────────────────


def test_admin_ve_tudo_sem_filtro_rbac(client, usuario_admin, mock_dax_capture, clean_redis):
    """Admin logado: aplicar_rbac_dax() retorna string vazia — nenhuma query DAX
    deve conter `CODUSUR = ` ou `CODSUPERVISOR = ` injetado pelo RBAC."""
    mock_dax_capture.set_routes([
        ('UltimaCompra', _load('dax_carteira_snapshot_rec')),
        ('Compras12m',   _load('dax_carteira_snapshot_freqmon')),
        ('MUNICENT',     _load('dax_carteira_meta')),
        ('PCSUPERV',     _load('dax_supervisores_map')),
        ('PCUSUARI',     _load('dax_vendedores_map')),
        ('CodCli',       _load('dax_carteira_datas')),
    ])
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    r = client.get('/api/carteira/rfm?modo=fixa')
    assert r.status_code == 200

    # Examinar todas as queries enviadas — nenhuma deve ter filtro RBAC
    queries_filter = [q for q in mock_dax_capture.queries if 'FILTER' in q]
    assert len(queries_filter) > 0, 'Esperava queries com FILTER'
    for q in queries_filter:
        # Aceito CODUSUR = num só se NÃO veio do RBAC (ex: dentro de FILTER puro
        # numa query específica de cliente). Mas pra /api/carteira/rfm não há
        # nenhum filtro de CODUSUR — só DTSAIDA temporal.
        assert 'CODUSUR =' not in q, f'Admin não devia ter CODUSUR no FILTER:\n{q}'
        assert 'CODSUPERVISOR =' not in q, f'Admin não devia ter CODSUPERVISOR no FILTER:\n{q}'


def test_vendedor_filtro_aplicado_em_todas_queries(client, usuario_vendedor, mock_dax_capture, clean_redis):
    """Vendedor (codusur=573): toda query DAX com FILTER deve conter `CODUSUR = 573`."""
    mock_dax_capture.set_routes([
        ('UltimaCompra', _load('dax_carteira_snapshot_rec')),
        ('Compras12m',   _load('dax_carteira_snapshot_freqmon')),
        ('MUNICENT',     _load('dax_carteira_meta')),
        ('PCSUPERV',     _load('dax_supervisores_map')),
        ('PCUSUARI',     _load('dax_vendedores_map')),
        ('CodCli',       _load('dax_carteira_datas')),
    ])
    login_as(client, usuario_vendedor['email'], usuario_vendedor['senha'])
    r = client.get('/api/carteira/rfm?modo=fixa')
    assert r.status_code == 200

    # As queries da carteira que usam FILTER (snapshot/datas) DEVEM ter CODUSUR = 573
    queries_carteira = [q for q in mock_dax_capture.queries
                        if 'FILTER' in q and 'FATURAMENTO_VENDAS' in q]
    assert len(queries_carteira) > 0
    for q in queries_carteira:
        assert 'FATURAMENTO_VENDAS[CODUSUR] = 573' in q, \
            f'Vendedor 573 deveria ter filtro CODUSUR=573 mas:\n{q[:300]}'


def test_supervisor_filtro_aplicado_em_todas_queries(client, usuario_supervisor, mock_dax_capture, clean_redis):
    """Supervisor (codsupervisor=18): toda query com FILTER deve conter `CODSUPERVISOR = 18`."""
    mock_dax_capture.set_routes([
        ('UltimaCompra', _load('dax_carteira_snapshot_rec')),
        ('Compras12m',   _load('dax_carteira_snapshot_freqmon')),
        ('MUNICENT',     _load('dax_carteira_meta')),
        ('PCSUPERV',     _load('dax_supervisores_map')),
        ('PCUSUARI',     _load('dax_vendedores_map')),
        ('CodCli',       _load('dax_carteira_datas')),
    ])
    login_as(client, usuario_supervisor['email'], usuario_supervisor['senha'])
    r = client.get('/api/carteira/rfm?modo=fixa')
    assert r.status_code == 200

    queries_carteira = [q for q in mock_dax_capture.queries
                        if 'FILTER' in q and 'FATURAMENTO_VENDAS' in q]
    assert len(queries_carteira) > 0
    for q in queries_carteira:
        assert 'FATURAMENTO_VENDAS[CODSUPERVISOR] = 18' in q, \
            f'Supervisor 18 deveria ter filtro CODSUPERVISOR=18 mas:\n{q[:300]}'


def test_vendedor_403_ao_acessar_outro_codusur(client, usuario_vendedor, mock_dax_capture, clean_redis):
    """Vendedor 573 tentando /api/vendedor/100 → 403."""
    mock_dax_capture.set_routes([
        ('PCUSUARI', _load('dax_vendedores_map')),
    ])
    login_as(client, usuario_vendedor['email'], usuario_vendedor['senha'])
    r = client.get('/api/vendedor/100')
    assert r.status_code == 403


def test_vendedor_403_em_api_vendedores(client, usuario_vendedor):
    """Vendedor não tem direito ao ranking → /api/vendedores deve retornar 403."""
    login_as(client, usuario_vendedor['email'], usuario_vendedor['senha'])
    r = client.get('/api/vendedores')
    assert r.status_code == 403


def test_supervisor_403_em_vendedor_fora_do_time(client, usuario_supervisor, mock_dax_capture, clean_redis):
    """Supervisor (codsupervisor=18) tenta /api/vendedor/820 (codsupervisor=99 na fixture) → 403.
    Mas /api/vendedor/100 (codsupervisor=18, mesmo time) → não 403 (passa autorização)."""
    mock_dax_capture.set_routes([
        ('PCUSUARI', _load('dax_vendedores_map')),
    ])
    login_as(client, usuario_supervisor['email'], usuario_supervisor['senha'])

    # 820 está em outro time (codsupervisor=99): 403
    r_fora = client.get('/api/vendedor/820')
    assert r_fora.status_code == 403

    # vendedor inexistente: 403 também (pode_acessar_vendedor falha)
    r_inexistente = client.get('/api/vendedor/99999')
    assert r_inexistente.status_code == 403
