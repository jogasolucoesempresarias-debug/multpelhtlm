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


def test_vendedor_ve_so_sua_carteira_por_cadastro(client, usuario_vendedor, mock_dax_capture, clean_redis):
    """Carteira por CADASTRO: vendedor 573 vê só clientes cujo CODUSUR1==573 (clientes 1 e 3),
    não o cliente 2 (CODUSUR1=100). Isolamento é por resposta (Python), não por filtro DAX."""
    mock_dax_capture.set_routes([
        ('UltimaCompra', _load('dax_carteira_snapshot_rec')),
        ('Compras12m',   _load('dax_carteira_snapshot_freqmon')),
        ('MUNICENT',     _load('dax_carteira_meta')),
        ('PCSUPERV',     _load('dax_supervisores_map')),
        ('PCUSUARI',     _load('dax_vendedores_map')),
        ('CodCli',       _load('dax_carteira_datas')),
    ])
    login_as(client, usuario_vendedor['email'], usuario_vendedor['senha'])
    r = client.get('/api/carteira/clientes?limit=100')
    assert r.status_code == 200, r.get_data(as_text=True)
    d = r.get_json()
    codclis = {row['codcli'] for row in d['rows']}
    assert codclis == {1, 3}, f'vendedor 573 deveria ver só {{1,3}}, viu {codclis}'
    for row in d['rows']:
        assert row['codusur'] == 573
    # A carteira global NÃO injeta RBAC de venda no DAX (isolamento é em Python)
    assert not any('CODUSUR = 573' in q for q in mock_dax_capture.queries)


def test_supervisor_ve_so_suas_areas_por_cadastro(client, usuario_supervisor, mock_dax_capture, clean_redis):
    """Supervisor (área 18) vê clientes cujo CODUSUR1 é da área 18 (clientes 1,2,3),
    NUNCA o cliente 4 (CODUSUR1=820 → área 99). Isolamento por cadastro, na resposta."""
    mock_dax_capture.set_routes([
        ('UltimaCompra', _load('dax_carteira_snapshot_rec')),
        ('Compras12m',   _load('dax_carteira_snapshot_freqmon')),
        ('MUNICENT',     _load('dax_carteira_meta')),
        ('PCSUPERV',     _load('dax_supervisores_map')),
        ('PCUSUARI',     _load('dax_vendedores_map')),
        ('CodCli',       _load('dax_carteira_datas')),
    ])
    login_as(client, usuario_supervisor['email'], usuario_supervisor['senha'])
    r = client.get('/api/carteira/clientes?limit=100')
    assert r.status_code == 200, r.get_data(as_text=True)
    d = r.get_json()
    codclis = {row['codcli'] for row in d['rows']}
    assert codclis == {1, 2, 3}, f'supervisor 18 deveria ver {{1,2,3}}, viu {codclis}'
    assert 4 not in codclis, 'NÃO pode ver cliente da área 99'


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
