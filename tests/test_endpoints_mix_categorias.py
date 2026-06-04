"""Smoke tests dos endpoints da Onda D: /api/categorias, /api/mix/abandonado, /api/tendencias/cohort."""
import json
import pathlib

import pytest

from tests.conftest import login_as

FIX_DIR = pathlib.Path(__file__).resolve().parent / 'fixtures'


def _load(name):
    with open(FIX_DIR / f'{name}.json', encoding='utf-8') as f:
        return json.load(f)


def test_categorias_estrutura_e_share(client, usuario_admin, mock_dax_capture, clean_redis):
    """Mock query categorias + deptos_map + secoes, valida que response tem share calculado e tratamento NULL."""
    mock_dax_capture.set_routes([
        ('DEPARTAMENTO', _load('dax_deptos_nomes')),
        ('SECAO',        _load('dax_secoes_nomes')),
        ('CODEPTO',      _load('dax_categorias')),  # genérico — captura a query principal
    ])
    login_as(client, usuario_admin['email'], usuario_admin['senha'])

    r = client.get('/api/categorias')
    assert r.status_code == 200, r.get_data(as_text=True)
    d = r.get_json()
    assert d['ok']
    assert d['total_venda'] == 900000.0  # 500k + 300k + 100k
    cats = d['categorias']
    assert len(cats) == 3
    # Ordenado por venda desc
    assert cats[0]['codepto'] == 1
    assert cats[0]['nome'] == 'ALIMENTICIOS'
    assert cats[0]['tem_nome'] is True
    assert cats[0]['share'] == pytest.approx(500/900, abs=0.001)
    assert cats[0]['margem'] == pytest.approx(0.20, abs=0.001)
    # Cliente NULL aparece com nome especial
    null_cat = next(c for c in cats if c['codepto'] is None)
    assert null_cat['nome'] == '(sem departamento)'
    assert null_cat['tem_nome'] is False


def test_mix_abandonado_filtra_dias(client, usuario_admin, mock_dax_capture, clean_redis):
    """Mock retorna 3 clientes; 2 com última compra antiga (>60d), 1 recente. Filtro 60 dias → 2 rows."""
    # Pre-popular cache de carteira (vazio é ok pra teste)
    mock_dax_capture.set_routes([
        # Mix abandonado é a query primary
        ('UltimaCompra', _load('dax_mix_abandonado')),
        # Deptos map
        ('DEPARTAMENTO', _load('dax_deptos_nomes')),
        ('SECAO',        _load('dax_secoes_nomes')),
        # carteira_full carrega varias outras
        ('Compras12m',   _load('dax_carteira_snapshot_freqmon')),
        ('MUNICENT',     _load('dax_carteira_meta')),
        ('PCSUPERV',     _load('dax_supervisores_map')),
        ('PCUSUARI',     _load('dax_vendedores_map')),
        ('CodCli',       _load('dax_carteira_datas')),
    ])
    login_as(client, usuario_admin['email'], usuario_admin['senha'])

    r = client.get('/api/mix/abandonado?dias=60')
    assert r.status_code == 200, r.get_data(as_text=True)
    d = r.get_json()
    assert d['ok']
    assert d['dias'] == 60
    # Cliente 2 tem ultima_compra 2026-05-01 (recente) → NÃO deve aparecer
    # Clientes 1 e 3 são antigos → devem aparecer
    codclis_resposta = [r['codcli'] for r in d['rows']]
    assert 2 not in codclis_resposta
    assert 1 in codclis_resposta or 3 in codclis_resposta
    # Cada row tem depto_nome resolvido
    for row in d['rows']:
        assert 'depto_nome' in row
        assert 'dias_sem_comprar_categoria' in row
        assert row['dias_sem_comprar_categoria'] >= 60


def test_tendencias_cohort_estrutura(client, usuario_admin, mock_dax_capture, clean_redis):
    """Cohort: 4 clientes com aquisições e retenções variadas."""
    mock_dax_capture.set_routes([
        ('AnoMes', _load('dax_cohort_compras')),
    ])
    login_as(client, usuario_admin['email'], usuario_admin['senha'])

    r = client.get('/api/tendencias/cohort?periodo=12m')
    assert r.status_code == 200, r.get_data(as_text=True)
    d = r.get_json()
    assert d['ok']
    cohorts = d['cohorts']
    assert len(cohorts) >= 1
    # Cada cohort tem M+0 = 1.0
    for c in cohorts:
        assert c['retencao'][0] == 1.0
        assert len(c['retencao']) == 13  # 0..12
    # Cohort 2025-05: clientes 1, 2, 3 (tamanho 3)
    cohort_maio = next((c for c in cohorts if c['aquisicao'] == '2025-05'), None)
    assert cohort_maio is not None
    assert cohort_maio['tamanho'] == 3
    # M+1: clientes 1 e 2 voltaram em jun = 2/3
    assert cohort_maio['retencao'][1] == pytest.approx(2/3, abs=0.001)
    # M+2: só cliente 1 voltou em jul = 1/3
    assert cohort_maio['retencao'][2] == pytest.approx(1/3, abs=0.001)
