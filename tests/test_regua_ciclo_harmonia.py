"""Régua ÚNICA do ciclo (revisão geral do Comercial, 17/11/2026).

Trava o que a revisão harmonizou:
- rfm.faixa_ciclo usa os MESMOS cortes da status_regua_personalizada (1× / 2× / 3×);
- Radar (cliente × produto) e Mix (cliente × departamento) classificam pelo ciclo do cliente
  NAQUELE grão, não pela janela da tela (casos do João, 16/11: 59 dias "Comprando"; ciclo de 90
  dias "Parou" com 61);
- "última compra" = última VENDA (CODOPER 'S') — bonificação não conta;
- a consulta de datas do Mix inteiro vem em blocos mensais e bloco no teto levanta.
"""
import json
import pathlib
from datetime import date, timedelta

import pytest

import rfm
from tests.conftest import login_as

FIX_DIR = pathlib.Path(__file__).resolve().parent / 'fixtures'


def _load(name):
    with open(FIX_DIR / f'{name}.json', encoding='utf-8') as f:
        return json.load(f)


def _payload(rows):
    return {'results': [{'tables': [{'rows': rows}]}]}


def _iso(dias_atras):
    return (date.today() - timedelta(days=dias_atras)).isoformat() + 'T00:00:00'


def _carteira_routes():
    return [
        ('Compras12m',   _load('dax_carteira_snapshot_freqmon')),
        ('UltimaCompra', _load('dax_carteira_snapshot_rec')),
        ('MUNICENT',     _load('dax_carteira_meta')),
        ('CodCli',       _load('dax_carteira_datas')),
        ('PCUSUARI',     _load('dax_vendedores_map')),
        ('PCSUPERV',     _load('dax_supervisores_map')),
    ]


# ───────────────────────── rfm: a régua ─────────────────────────

@pytest.mark.parametrize('dias,ciclo,esperado', [
    (10, 30, 'no_prazo'), (29, 30, 'no_prazo'),
    (30, 30, 'atencao'), (59, 30, 'atencao'),
    (60, 30, 'atrasado'), (89, 30, 'atrasado'),
    (90, 30, 'parou'), (400, 30, 'parou'),
    (61, 90, 'no_prazo'),                 # ciclo longo não "para" no dia 61
    (5, None, 'esporadico'), (300, None, 'esporadico'),
])
def test_faixa_ciclo(dias, ciclo, esperado):
    assert rfm.faixa_ciclo(dias, ciclo) == esperado


@pytest.mark.parametrize('dias,esperado', [(0, 'no_prazo'), (59, 'no_prazo'), (60, 'esporadico'), (200, 'esporadico')])
def test_faixa_ciclo_sem_ciclo_vale_a_janela(dias, esperado):
    """1 compra só: dentro da janela está no prazo (como a Recuperação: sem ciclo = ativo até 60 d)."""
    assert rfm.faixa_ciclo(dias, None, janela_sem_ciclo=60) == esperado
    assert rfm.faixa_ciclo(dias, 30, janela_sem_ciclo=60) == rfm.faixa_ciclo(dias, 30)   # com ciclo, ignora


def test_faixa_ciclo_e_a_mesma_regua_da_carteira():
    """Fonte única: cada faixa corresponde a um status da Carteira, com o nome exibido igual."""
    for dias in range(0, 200, 3):
        for ciclo in (7, 15, 30, 56, 90):
            st = rfm.status_regua_personalizada(dias, ciclo)
            fx = rfm.faixa_ciclo(dias, ciclo)
            assert rfm.STATUS_CICLO_PT[st] == rfm.FAIXA_CICLO_PT[fx]


def test_ciclos_por_chave_mediana_e_dias_distintos():
    d0 = date(2026, 1, 1)
    pares = [('a', d0), ('a', d0), ('a', d0 + timedelta(days=10)), ('a', (d0 + timedelta(days=40)).isoformat()),
             ('b', d0), (None, d0), ('c', None)]
    r = rfm.ciclos_por_chave(pares)
    assert r['a'] == {'ciclo': 20, 'compras': 3}      # intervalos 10 e 30 → mediana 20
    assert r['b'] == {'ciclo': None, 'compras': 1}    # 1 dia só: sem ciclo (esporádico)
    assert set(r) == {'a', 'b'}


# ───────────────────────── Radar: drill do produto ─────────────────────────

def _radar_produto_routes():
    # Clientes da fixture de carteira: 1, 2, 3 e 4.
    cli = [
        {'[CODCLI]': 1, '[Ultima]': _iso(59), '[Venda12m]': 4000.0, '[Qt12m]': 40.0},  # ciclo 30 → Atenção
        {'[CODCLI]': 2, '[Ultima]': _iso(61), '[Venda12m]': 3000.0, '[Qt12m]': 30.0},  # ciclo 90 → No prazo
        {'[CODCLI]': 3, '[Ultima]': _iso(80), '[Venda12m]': 500.0,  '[Qt12m]': 5.0},   # 1 compra, não voltou → Esporádico
        {'[CODCLI]': 4, '[Ultima]': None,     '[Venda12m]': 0.0,    '[Qt12m]': 0.0},   # só bonificação
    ]
    datas = ([{'[CODCLI]': 1, '[DTSAIDA]': _iso(d)} for d in (59, 89, 119, 149)]
             + [{'[CODCLI]': 2, '[DTSAIDA]': _iso(d)} for d in (61, 151, 241)]
             + [{'[CODCLI]': 3, '[DTSAIDA]': _iso(80)}])
    prod_map = [{'[CODPROD]': 900001, '[DESCRICAO]': 'PRODUTO X', '[CODEPTO]': 1, '[CODFORNECPRINC]': 10,
                 '[FORNECPRINC]': 'FORNECEDOR X', '[Venda]': 7500.0}]
    return [
        ('LinhasDiaVenda', _payload(datas)),
        ('Compras12m', _load('dax_carteira_snapshot_freqmon')),
        ('DESCRICAO',  _payload(prod_map)),
        ('Venda12m',   _payload(cli)),
        ('DEPARTAMENTO', _load('dax_deptos_nomes')),
    ] + _carteira_routes()[1:]


def test_radar_produto_situacao_pelo_ciclo_do_cliente(client, usuario_admin, mock_dax_capture, clean_redis):
    mock_dax_capture.set_routes(_radar_produto_routes())
    login_as(client, usuario_admin['email'], usuario_admin['senha'])

    d = client.get('/api/radar/produto/900001?dias=60').get_json()
    st = {r['codcli']: r['status'] for r in d['rows']}
    assert st == {1: 'atencao', 2: 'ativo', 3: 'esporadico'}   # o 4 (sem venda) não é cliente do item
    r1 = next(r for r in d['rows'] if r['codcli'] == 1)
    assert r1['ciclo'] == 30 and r1['compras_12m'] == 4
    k = d['kpis']
    assert k['clientes'] == 3 and k['parados'] == 1 and k['receita_em_risco'] == 4000.0
    assert k['por_status']['atencao'] == 1 and k['por_status']['esporadico'] == 1

    # Última compra = última VENDA; datas do ciclo só de venda e do produto
    q_cli = next(q for q in mock_dax_capture.queries if '"Venda12m"' in q)
    assert 'FATURAMENTO_VENDAS[CODOPER] = "S"' in q_cli
    q_dt = next(q for q in mock_dax_capture.queries if 'LinhasDiaVenda' in q)
    assert 'TREATAS({"S"}, FATURAMENTO_VENDAS[CODOPER])' in q_dt
    assert 'TREATAS({900001}, FATURAMENTO_VENDAS[CODPROD])' in q_dt

    csv = client.get('/api/radar/produto/900001/csv?dias=60').get_data(as_text=True)
    assert 'CicloDias' in csv and 'Atenção' in csv and 'Esporádico' in csv


# ───────────────────────── Mix: cliente × departamento ─────────────────────────

def _mix_routes():
    rows = [
        {'[CODCLI]': 1, '[CODEPTO]': 1, '[UltimaCompra]': _iso(70), '[VendaCat12m]': 900.0, '[LucroCat12m]': 300.0},
        {'[CODCLI]': 3, '[CODEPTO]': 1, '[UltimaCompra]': _iso(70), '[VendaCat12m]': 800.0, '[LucroCat12m]': 200.0},
        {'[CODCLI]': 4, '[CODEPTO]': 1, '[UltimaCompra]': _iso(70), '[VendaCat12m]': 100.0, '[LucroCat12m]': 50.0},
        {'[CODCLI]': 2, '[CODEPTO]': 2, '[UltimaCompra]': _iso(10), '[VendaCat12m]': 500.0, '[LucroCat12m]': 100.0},
    ]
    datas = ([{'[CODCLI]': 1, '[CODEPTO]': 1, '[DTSAIDA]': _iso(d)} for d in (70, 160, 250)]     # ciclo 90
             + [{'[CODCLI]': 3, '[CODEPTO]': 1, '[DTSAIDA]': _iso(d)} for d in (70, 90, 110)]  # ciclo 20
             + [{'[CODCLI]': 4, '[CODEPTO]': 1, '[DTSAIDA]': _iso(70)}])                       # 1 compra
    return [
        ('LinhasDiaVenda', _payload(datas)),
        ('LucroCat12m',  _payload(rows)),
        ('DEPARTAMENTO', _load('dax_deptos_nomes')),
        ('SECAO',        _load('dax_secoes_nomes')),
    ] + _carteira_routes()


def test_mix_entra_so_quem_passou_do_proprio_ciclo(client, usuario_admin, mock_dax_capture, clean_redis):
    mock_dax_capture.set_routes(_mix_routes())
    login_as(client, usuario_admin['email'], usuario_admin['senha'])

    d = client.get('/api/mix/abandonado?dias=60').get_json()
    st = {(r['codcli'], r['codepto']): r['status'] for r in d['rows']}
    # 1: 70 dias com ciclo de 90 → ainda no prazo dele, fica FORA (antes entrava por passar de 60)
    # 2: comprou há 10 dias → fora · 3: 70/20 = 3,5× → Parou · 4: 1 compra → Esporádico
    assert st == {(3, 1): 'parou', (4, 1): 'esporadico'}
    t = d['totais']
    assert t['pares'] == 2 and t['venda'] == 900.0 and t['lucro'] == 250.0
    assert t['por_status']['parou'] == 1 and t['por_status']['esporadico'] == 1

    q_main = next(q for q in mock_dax_capture.queries if 'LucroCat12m' in q)
    assert 'FATURAMENTO_VENDAS[CODOPER] = "S"' in q_main
    # Mix inteiro (sem depto/fornecedor): datas em 12 blocos mensais (teto de 100 mil linhas)
    assert sum(1 for q in mock_dax_capture.queries if 'LinhasDiaVenda' in q) == 12


def test_ciclos_bloco_no_teto_levanta(monkeypatch):
    """Bloco de datas com 99.999+ linhas = resposta cortada pelo Power BI: ciclo calculado sobre
    metade das datas sairia errado sem erro nenhum. Tem que levantar."""
    import server
    cheio = _payload([{'[CODCLI]': i, '[CODEPTO]': 1, '[DTSAIDA]': '2026-01-01T00:00:00'} for i in range(99_999)])
    monkeypatch.setattr(server, '_executar_dax_paralelo_n', lambda qs, max_workers=4: {k: cheio for k in qs})
    monkeypatch.setitem(server.CONFIG, 'data_source', 'powerbi')
    with pytest.raises(RuntimeError, match='teto'):
        server._ciclos_venda_12m(('codcli', 'codepto'), blocos_mensais=True)
