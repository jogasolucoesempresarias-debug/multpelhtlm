"""Testes das visões de abandono CENTRADAS NO CLIENTE (Patch: Mix fornecedores + Radar produtos).

Cobrem:
- /api/_internal/clientes-busca  → busca por escopo (admin vê tudo; vendedor só o cadastro dele)
- /api/mix/cliente/<id>/fornecedores → agrupa por fornecedor, filtra por dias, 404 fora do escopo
- /api/radar/cliente/<id>         → produtos que o cliente parou (só parou/perdido), 404 fora do escopo
"""
import json
import pathlib
from datetime import date, timedelta

import pytest

from tests.conftest import login_as

FIX_DIR = pathlib.Path(__file__).resolve().parent / 'fixtures'


def _load(name):
    with open(FIX_DIR / f'{name}.json', encoding='utf-8') as f:
        return json.load(f)


def _payload(rows):
    return {'results': [{'tables': [{'rows': rows}]}]}


def _iso(dias_atras):
    return (date.today() - timedelta(days=dias_atras)).isoformat() + 'T00:00:00'


# Rotas que carregam a carteira GLOBAL (usada por _carteira_no_escopo em todos os endpoints).
# 'Compras12m' precisa vir ANTES de qualquer rota 'Venda12m' (a query freqmon também tem esse alias).
def _carteira_routes():
    return [
        ('Compras12m',   _load('dax_carteira_snapshot_freqmon')),
        ('UltimaCompra', _load('dax_carteira_snapshot_rec')),
        ('MUNICENT',     _load('dax_carteira_meta')),
        ('CodCli',       _load('dax_carteira_datas')),
        ('PCUSUARI',     _load('dax_vendedores_map')),
        ('PCSUPERV',     _load('dax_supervisores_map')),
    ]


# ───────────────────────── clientes-busca ─────────────────────────

def test_clientes_busca_admin_ve_tudo(client, usuario_admin, mock_dax_capture, clean_redis):
    mock_dax_capture.set_routes(_carteira_routes())
    login_as(client, usuario_admin['email'], usuario_admin['senha'])

    r = client.get('/api/_internal/clientes-busca?q=')
    assert r.status_code == 200, r.get_data(as_text=True)
    d = r.get_json()
    assert d['ok']
    codclis = {c['codcli'] for c in d['clientes']}
    assert {1, 2, 3, 4}.issubset(codclis)  # admin vê os 4 da fixture


def test_clientes_busca_por_nome(client, usuario_admin, mock_dax_capture, clean_redis):
    mock_dax_capture.set_routes(_carteira_routes())
    login_as(client, usuario_admin['email'], usuario_admin['senha'])

    r = client.get('/api/_internal/clientes-busca?q=padaria')
    d = r.get_json()
    nomes = [c['cliente'] for c in d['clientes']]
    assert nomes == ['PADARIA TOP']  # casa só pelo nome


def test_clientes_busca_vendedor_so_seu_cadastro(client, usuario_vendedor, mock_dax_capture, clean_redis):
    """Vendedor 573 é o CODUSUR1 dos clientes 1 e 3. Não deve ver 2 (usur 100) nem 4 (usur 820)."""
    mock_dax_capture.set_routes(_carteira_routes())
    login_as(client, usuario_vendedor['email'], usuario_vendedor['senha'])

    r = client.get('/api/_internal/clientes-busca?q=')
    d = r.get_json()
    codclis = {c['codcli'] for c in d['clientes']}
    assert codclis == {1, 3}


# ───────────────────────── mix/cliente/<id>/fornecedores ─────────────────────────

def test_mix_cliente_fornecedores_agrupa_e_filtra_dias(client, usuario_admin, mock_dax_capture, clean_redis):
    """2 fornecedores: BOMBRIL parado (200d) e GALVANOTEK ativo (8d). Com dias=60 → só BOMBRIL."""
    fornec_rows = [
        {'[CODFORNECPRINC]': 10, '[FORNECPRINC]': 'BOMBRIL',    '[UltimaCompra]': _iso(200), '[VendaCat12m]': 12000.0, '[LucroCat12m]': 3000.0},
        {'[CODFORNECPRINC]': 20, '[FORNECPRINC]': 'GALVANOTEK', '[UltimaCompra]': _iso(8),   '[VendaCat12m]': 5000.0,  '[LucroCat12m]': 900.0},
    ]
    # 'CODFORNECPRINC' ANTES de 'UltimaCompra' pra a query do drill não roubar a rota da carteira.
    mock_dax_capture.set_routes([('CODFORNECPRINC', _payload(fornec_rows))] + _carteira_routes())
    login_as(client, usuario_admin['email'], usuario_admin['senha'])

    r = client.get('/api/mix/cliente/1/fornecedores?dias=60')
    assert r.status_code == 200, r.get_data(as_text=True)
    d = r.get_json()
    assert d['ok']
    assert d['codcli'] == 1
    assert d['total'] == 1
    assert d['rows'][0]['fornec_nome'] == 'BOMBRIL'
    assert d['rows'][0]['dias_parado'] >= 60
    assert d['rows'][0]['lucro_cat_12m'] == 3000.0


def test_mix_board_busca_filtra_por_cliente(client, usuario_admin, mock_dax_capture, clean_redis):
    """Board com ?busca= filtra a lista COMPLETA por cliente, no MESMO formato dos pares.
    'padaria' casa só o cliente 1 (PADARIA TOP) — os demais (bar/mercado/atacado) somem."""
    mock_dax_capture.set_routes([
        ('UltimaCompra', _load('dax_mix_abandonado')),   # board (e snapshot_rec da carteira)
        ('DEPARTAMENTO', _load('dax_deptos_nomes')),
        ('Compras12m',   _load('dax_carteira_snapshot_freqmon')),
        ('MUNICENT',     _load('dax_carteira_meta')),
        ('CodCli',       _load('dax_carteira_datas')),
        ('PCUSUARI',     _load('dax_vendedores_map')),
        ('PCSUPERV',     _load('dax_supervisores_map')),
    ])
    login_as(client, usuario_admin['email'], usuario_admin['senha'])

    r = client.get('/api/mix/abandonado?dias=60&busca=padaria')
    assert r.status_code == 200, r.get_data(as_text=True)
    d = r.get_json()
    assert d['ok']
    assert d['total'] == 4                       # total absoluto do board (não muda com a busca)
    codclis = {row['codcli'] for row in d['rows']}
    assert codclis == {1}                          # só PADARIA TOP casa "padaria"
    # Mesmo formato dos 200 (chaves de par cliente×departamento)
    assert 'depto_nome' in d['rows'][0]
    assert 'dias_sem_comprar_categoria' in d['rows'][0]
    assert 'lucro_cat_12m' in d['rows'][0]


def test_mix_cliente_fornecedores_404_fora_escopo(client, usuario_vendedor, mock_dax_capture, clean_redis):
    """Vendedor 573 não tem o cliente 2 (usur 100) no cadastro → 404 (não vaza dados)."""
    mock_dax_capture.set_routes([('CODFORNECPRINC', _payload([]))] + _carteira_routes())
    login_as(client, usuario_vendedor['email'], usuario_vendedor['senha'])

    r = client.get('/api/mix/cliente/2/fornecedores?dias=60')
    assert r.status_code == 404


# ───────────────────────── radar/cliente/<id> ─────────────────────────

def _radar_routes():
    prod_rows = [
        {'[CODPROD]': 100, '[Ultima]': _iso(200), '[Venda12m]': 5000.0, '[Qt12m]': 50.0},   # perdido
        {'[CODPROD]': 200, '[Ultima]': _iso(5),   '[Venda12m]': 3000.0, '[Qt12m]': 30.0},   # ativo
    ]
    rec_rows = [
        {'[CODPROD]': 200, '[VendaRec]': 3000.0, '[QtRec]': 30.0},
    ]
    ant_rows = [
        {'[CODPROD]': 100, '[VendaAnt]': 1000.0, '[QtAnt]': 10.0},
        {'[CODPROD]': 200, '[VendaAnt]': 2000.0, '[QtAnt]': 20.0},
    ]
    prod_map_rows = [
        {'[CODPROD]': 100, '[DESCRICAO]': 'DETERGENTE 500ML', '[CODEPTO]': 1, '[CODFORNECPRINC]': 10, '[FORNECPRINC]': 'BOMBRIL', '[Venda]': 5000.0},
        {'[CODPROD]': 200, '[DESCRICAO]': 'SACOLA 40X50',     '[CODEPTO]': 2, '[CODFORNECPRINC]': 20, '[FORNECPRINC]': 'RELIX',   '[Venda]': 3000.0},
    ]
    # Ordem: 'Compras12m' (freqmon) antes de 'Venda12m' (query prod). 'DESCRICAO' antes de tudo p/ produtos_map.
    return [
        ('Compras12m', _load('dax_carteira_snapshot_freqmon')),
        ('DESCRICAO',  _payload(prod_map_rows)),
        ('VendaRec',   _payload(rec_rows)),
        ('VendaAnt',   _payload(ant_rows)),
        ('Venda12m',   _payload(prod_rows)),
        ('DEPARTAMENTO', _load('dax_deptos_nomes')),
        ('UltimaCompra', _load('dax_carteira_snapshot_rec')),
        ('MUNICENT',   _load('dax_carteira_meta')),
        ('CodCli',     _load('dax_carteira_datas')),
        ('PCUSUARI',   _load('dax_vendedores_map')),
        ('PCSUPERV',   _load('dax_supervisores_map')),
    ]


def test_radar_cliente_so_produtos_parados(client, usuario_admin, mock_dax_capture, clean_redis):
    """Produto 100 perdido (200d) aparece; produto 200 ativo (5d, volume mantido) é filtrado."""
    mock_dax_capture.set_routes(_radar_routes())
    login_as(client, usuario_admin['email'], usuario_admin['senha'])

    r = client.get('/api/radar/cliente/1?dias=60')
    assert r.status_code == 200, r.get_data(as_text=True)
    d = r.get_json()
    assert d['ok']
    assert d['cliente']['codcli'] == 1
    codprods = [row['codprod'] for row in d['rows']]
    assert codprods == [100]                      # só o parado/perdido
    assert d['rows'][0]['status'] in ('parou', 'perdido')
    assert d['rows'][0]['descricao'] == 'DETERGENTE 500ML'
    assert d['rows'][0]['fornec_nome'] == 'BOMBRIL'
    assert d['kpis']['produtos_parados'] == 1
    assert d['kpis']['receita_em_risco'] == 5000.0


def test_radar_cliente_404_fora_escopo(client, usuario_vendedor, mock_dax_capture, clean_redis):
    """Vendedor 573 não tem o cliente 4 (usur 820) → 404."""
    mock_dax_capture.set_routes(_radar_routes())
    login_as(client, usuario_vendedor['email'], usuario_vendedor['senha'])

    r = client.get('/api/radar/cliente/4?dias=60')
    assert r.status_code == 404


# ───────────────────────── radar: filtro por fornecedor (board) ─────────────────────────

def _radar_board_routes():
    prod_map = [
        {'[CODPROD]': 100, '[DESCRICAO]': 'DETERGENTE', '[CODEPTO]': 1, '[CODFORNECPRINC]': 113, '[FORNECPRINC]': 'BOMBRIL SA', '[Venda]': 5000.0},
        {'[CODPROD]': 200, '[DESCRICAO]': 'SACOLA',     '[CODEPTO]': 2, '[CODFORNECPRINC]': 999, '[FORNECPRINC]': 'OUTRO LTDA', '[Venda]': 3000.0},
    ]
    rec = [{'[CODPROD]': 100, '[VendaRec]': 100.0, '[CliRec]': 2}, {'[CODPROD]': 200, '[VendaRec]': 100.0, '[CliRec]': 2}]
    ant = [{'[CODPROD]': 100, '[VendaAnt]': 500.0, '[CliAnt]': 5}, {'[CODPROD]': 200, '[VendaAnt]': 300.0, '[CliAnt]': 4}]
    return [
        ('DESCRICAO',    _payload(prod_map)),
        ('VendaRec',     _payload(rec)),
        ('VendaAnt',     _payload(ant)),
        ('DEPARTAMENTO', _load('dax_deptos_nomes')),
    ]


def test_radar_fornecedores_lista_int(client, usuario_admin, mock_dax_capture, clean_redis):
    """/api/radar/fornecedores deriva do catálogo, ordena por nome e devolve codfornec INT."""
    mock_dax_capture.set_routes(_radar_board_routes())
    login_as(client, usuario_admin['email'], usuario_admin['senha'])

    r = client.get('/api/radar/fornecedores')
    assert r.status_code == 200, r.get_data(as_text=True)
    d = r.get_json()
    nomes = [f['nome'] for f in d['fornecedores']]
    assert nomes == ['BOMBRIL SA', 'OUTRO LTDA']          # ordenado por nome
    assert all(isinstance(f['codfornec'], int) for f in d['fornecedores'])  # não float


def test_radar_board_filtra_por_fornecedor(client, usuario_admin, mock_dax_capture, clean_redis):
    """Board sem filtro traz os 2 produtos sangrando; com fornecedor=113 sobra só o da BOMBRIL."""
    mock_dax_capture.set_routes(_radar_board_routes())
    login_as(client, usuario_admin['email'], usuario_admin['senha'])

    full = client.get('/api/radar/board?dias=60').get_json()
    assert full['total'] == 2                              # 100 e 200 (ambos com queda)

    filt = client.get('/api/radar/board?dias=60&fornecedor=113').get_json()
    assert filt['total'] == 1
    assert filt['rows'][0]['codprod'] == 100
    assert filt['rows'][0]['fornec_nome'] == 'BOMBRIL SA'
