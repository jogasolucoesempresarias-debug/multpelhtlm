"""Testes do supervisor multi-área (RBAC IN {...} + autorização granular).

Garante isolamento: supervisor com áreas [18,19] vê a união das duas e NÃO acessa a área 99.
"""
import json
import pathlib

from tests.conftest import login_as

FIX_DIR = pathlib.Path(__file__).resolve().parent / 'fixtures'


def _load(name):
    with open(FIX_DIR / f'{name}.json', encoding='utf-8') as f:
        return json.load(f)


def _rotear_carteira(cap):
    cap.set_routes([
        ('UltimaCompra', _load('dax_carteira_snapshot_rec')),
        ('Compras12m',   _load('dax_carteira_snapshot_freqmon')),
        ('MUNICENT',     _load('dax_carteira_meta')),
        ('PCSUPERV',     _load('dax_supervisores_map')),
        ('PCUSUARI',     _load('dax_vendedores_map')),
        ('CodCli',       _load('dax_carteira_datas')),
    ])


def test_carteira_multi_area_por_cadastro(client, usuario_supervisor_multi, mock_dax_capture, clean_redis):
    """Login multi-área [18,19] → carteira (por cadastro) traz clientes das áreas 18/19
    (clientes 1,2,3), nunca o cliente 4 (área 99). Isolamento por resposta."""
    _rotear_carteira(mock_dax_capture)
    login_as(client, usuario_supervisor_multi['email'], usuario_supervisor_multi['senha'])
    r = client.get('/api/carteira/clientes?limit=100')
    assert r.status_code == 200, r.get_data(as_text=True)
    codclis = {row['codcli'] for row in r.get_json()['rows']}
    assert codclis == {1, 2, 3}, f'multi-área deveria ver {{1,2,3}}, viu {codclis}'
    assert 4 not in codclis, 'não pode vazar cliente da área 99'


def test_supervisor_multi_acessa_area_propria_nao_alheia(client, usuario_supervisor_multi, mock_dax_capture, clean_redis):
    """pode_acessar_vendedor: vendedor da área 18 ou 19 ok; vendedor da área 99 → 403."""
    mock_dax_capture.set_routes([
        ('PCUSUARI', _load('dax_vendedores_map')),  # 573/100 → sup 18, 820 → sup 99
    ])
    login_as(client, usuario_supervisor_multi['email'], usuario_supervisor_multi['senha'])

    # 820 está na área 99 (fora de [18,19]) → 403
    assert client.get('/api/vendedor/820').status_code == 403
    # 100 está na área 18 (dentro) → autorização passa (não 403)
    assert client.get('/api/vendedor/100').status_code != 403


def test_drill_360_guarda_escopo(client, usuario_supervisor, mock_dax_capture, clean_redis):
    """Drill 360°: supervisor 18 não pode abrir cliente 4 (área 99) → 404; cliente do escopo → 200."""
    _rotear_carteira(mock_dax_capture)
    login_as(client, usuario_supervisor['email'], usuario_supervisor['senha'])
    client.get('/api/carteira/clientes?limit=100')  # popula cache da carteira
    assert client.get('/api/carteira/cliente/4').status_code == 404  # fora do cadastro dele
    assert client.get('/api/carteira/cliente/1').status_code == 200  # cliente do escopo


# ── Categorias / Mix / Tendências pela régua de cadastro ──

def test_mix_isola_por_cadastro(client, usuario_supervisor, mock_dax_capture, clean_redis):
    """Mix: supervisor 18 vê só seus clientes de cadastro (1,2,3); nunca o 4 (área 99)."""
    mock_dax_capture.set_routes([
        ('UltimaCompra', _load('dax_mix_abandonado')),   # também alimenta o snapshot da carteira
        ('DEPARTAMENTO', _load('dax_deptos_nomes')),
        ('SECAO',        _load('dax_secoes_nomes')),
        ('Compras12m',   _load('dax_carteira_snapshot_freqmon')),
        ('MUNICENT',     _load('dax_carteira_meta')),
        ('PCSUPERV',     _load('dax_supervisores_map')),
        ('PCUSUARI',     _load('dax_vendedores_map')),
        ('CodCli',       _load('dax_carteira_datas')),
    ])
    login_as(client, usuario_supervisor['email'], usuario_supervisor['senha'])
    r = client.get('/api/mix/abandonado?dias=60')
    assert r.status_code == 200, r.get_data(as_text=True)
    codclis = {row['codcli'] for row in r.get_json()['rows']}
    assert 4 not in codclis, 'não pode vazar cliente da área 99'
    assert codclis <= {1, 2, 3}


def _rotear_mix(cap):
    cap.set_routes([
        ('UltimaCompra', _load('dax_mix_abandonado')),
        ('DEPARTAMENTO', _load('dax_deptos_nomes')),
        ('SECAO',        _load('dax_secoes_nomes')),
        ('Compras12m',   _load('dax_carteira_snapshot_freqmon')),
        ('MUNICENT',     _load('dax_carteira_meta')),
        ('PCSUPERV',     _load('dax_supervisores_map')),
        ('PCUSUARI',     _load('dax_vendedores_map')),
        ('CodCli',       _load('dax_carteira_datas')),
    ])


def test_mix_drill_guarda_escopo(client, usuario_supervisor, mock_dax_capture, clean_redis):
    """Drill de deptos: cliente fora do cadastro do supervisor (área 99) → 404."""
    _rotear_mix(mock_dax_capture)
    login_as(client, usuario_supervisor['email'], usuario_supervisor['senha'])
    assert client.get('/api/mix/abandonado/4/deptos').status_code == 404


def test_mix_csv_export(client, usuario_admin, mock_dax_capture, clean_redis):
    """CSV do mix: 200, text/csv, nome de arquivo e cabeçalho corretos."""
    _rotear_mix(mock_dax_capture)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    r = client.get('/api/mix/abandonado/csv?dias=60')
    assert r.status_code == 200
    assert 'text/csv' in r.headers['Content-Type']
    assert 'mix_abandonado' in r.headers['Content-Disposition']
    assert 'CodCli' in r.get_data(as_text=True)


def test_categorias_supervisor_restringe_por_codcli(client, usuario_supervisor, mock_dax_capture, clean_redis):
    """Categorias (supervisor): a query agrega restrita aos codclis do cadastro (IN {1,2,3})."""
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
    login_as(client, usuario_supervisor['email'], usuario_supervisor['senha'])
    r = client.get('/api/categorias')
    assert r.status_code == 200, r.get_data(as_text=True)
    qcat = [q for q in mock_dax_capture.queries if 'CODEPTO' in q and 'SUMMARIZECOLUMNS' in q]
    assert qcat, 'esperava a query de categorias'
    assert any('FATURAMENTO_VENDAS[CODCLI] IN {1, 2, 3}' in q for q in qcat), \
        'categorias do supervisor deve restringir pelos codclis do cadastro'


def test_categorias_admin_sem_restricao_codcli(client, usuario_admin, mock_dax_capture, clean_redis):
    """Categorias (admin): agrega tudo, sem CODCLI IN (não estoura)."""
    mock_dax_capture.set_routes([
        ('DEPARTAMENTO', _load('dax_deptos_nomes')),
        ('SECAO',        _load('dax_secoes_nomes')),
        ('CODEPTO',      _load('dax_categorias')),
    ])
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    r = client.get('/api/categorias')
    assert r.status_code == 200
    assert not any('FATURAMENTO_VENDAS[CODCLI] IN' in q for q in mock_dax_capture.queries)


def test_tendencias_cohort_global_sem_rbac_venda(client, usuario_supervisor, mock_dax_capture, clean_redis):
    """Tendências: a query de compras é GLOBAL (sem RBAC de venda); o recorte é por cadastro."""
    mock_dax_capture.set_routes([
        ('AnoMes',       _load('dax_cohort_compras')),
        ('UltimaCompra', _load('dax_carteira_snapshot_rec')),
        ('Compras12m',   _load('dax_carteira_snapshot_freqmon')),
        ('MUNICENT',     _load('dax_carteira_meta')),
        ('PCSUPERV',     _load('dax_supervisores_map')),
        ('PCUSUARI',     _load('dax_vendedores_map')),
        ('CodCli',       _load('dax_carteira_datas')),
    ])
    login_as(client, usuario_supervisor['email'], usuario_supervisor['senha'])
    r = client.get('/api/tendencias/cohort?periodo=12m')
    assert r.status_code == 200, r.get_data(as_text=True)
    qcohort = [q for q in mock_dax_capture.queries if 'AnoMes' in q and 'CODCLI' in q]
    assert qcohort, 'esperava a query global de compras do cohort'
    for q in qcohort:
        assert 'CODSUPERVISOR' not in q, 'cohort deve ser global (recorte é por cadastro em Python)'


def test_supervisor_single_legado_continua_isolado(client, usuario_supervisor, mock_dax_capture, clean_redis):
    """Supervisor legado (só codsupervisor=18, sem codsupervisores) segue isolado por cadastro:
    vê clientes 1,2,3 (área 18) e não o cliente 4 (área 99)."""
    _rotear_carteira(mock_dax_capture)
    login_as(client, usuario_supervisor['email'], usuario_supervisor['senha'])
    r = client.get('/api/carteira/clientes?limit=100')
    assert r.status_code == 200
    codclis = {row['codcli'] for row in r.get_json()['rows']}
    assert codclis == {1, 2, 3}
    assert 4 not in codclis
