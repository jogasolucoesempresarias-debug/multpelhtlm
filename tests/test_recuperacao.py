"""Carteira em risco × recuperada (recuperacao.py) — réguas acordadas com o João em 24/09/2026:
inativo a partir do 61º dia E além do próprio ciclo; crédito para quem vendeu; ponte que fecha."""
from datetime import date, timedelta

import pytest

import recuperacao as rec


def _h(*compras):
    """compras = (data, codusur, valor)."""
    return rec.indexar([(1, d, u, v) for d, u, v in compras])[1]


def _mensal(inicio, n, u=10, v=100.0, passo=30):
    return [(inicio + timedelta(days=passo * i), u, v) for i in range(n)]


# ───────────────────────── estado ─────────────────────────
def test_inativo_so_depois_de_60_dias_e_do_ciclo():
    h = _h(*_mensal(date(2025, 1, 1), 10))          # compra a cada 30 dias; última 2025-09-28
    ult = date(2025, 9, 28)
    assert rec.estado_em(h, ult + timedelta(days=60))['estado'] == rec.ATIVO    # 60 = ainda ativo
    assert rec.estado_em(h, ult + timedelta(days=61))['estado'] == rec.RISCO    # 61º dia


def test_ciclo_longo_NAO_e_risco_so_por_passar_de_60():
    """Quem compra a cada ~90 dias não sumiu no dia 61 (21% dos 'reativados' eram esses)."""
    h = _h(*_mensal(date(2024, 1, 1), 5, passo=90))
    ult = date(2024, 1, 1) + timedelta(days=360)
    assert rec.estado_em(h, ult + timedelta(days=80))['estado'] == rec.ATIVO
    assert rec.estado_em(h, ult + timedelta(days=95))['estado'] == rec.RISCO


def test_perdido_depois_de_365():
    h = _h((date(2024, 1, 10), 10, 50.0))
    assert rec.estado_em(h, date(2025, 1, 11))['estado'] == rec.PERDIDO


def test_valor_mensal_e_a_venda_12m_ate_a_ultima_compra_dividida_por_12():
    h = _h(*_mensal(date(2025, 1, 1), 12, v=120.0))
    e = rec.estado_em(h, date(2026, 6, 1))
    assert e['valor_mensal'] == pytest.approx(120.0)     # 12 × 120 / 12 — não cresce com o atraso


# ───────────────────────── recuperação ─────────────────────────
def test_recuperado_vai_para_QUEM_VENDEU_e_mostra_o_dono():
    hist = rec.indexar([
        *[(1, d, 10, v) for d, _, v in _mensal(date(2025, 1, 5), 6)],   # dono 10, para em jun
        (1, date(2025, 10, 20), 20, 300.0),                               # 20 traz de volta em out
    ])
    m = rec.movimento_cliente(hist[1], 202510)
    assert m['recuperado'] and m['recuperado_de'] == rec.RISCO and m['vendas'] == {20: 300.0}
    pl = rec.placar(hist, 202510, dono_de={1: 10}, time_de={10: 'A', 20: 'B'})
    assert pl['rcas'][20]['rec_por_ele'] == 1 and pl['rcas'][20]['rec_de_outra_base'] == 1
    assert pl['rcas'][10]['rec_da_base'] == 1 and pl['rcas'][10]['rec_por_ele'] == 0
    assert pl['times']['B']['venda_rec_por_ele'] == 300.0
    assert pl['times']['A']['venda_rec_da_base'] == 300.0


def test_cliente_ativo_comprando_nao_e_recuperacao():
    hist = rec.indexar([(1, d, 10, v) for d, _, v in _mensal(date(2025, 1, 5), 12)])
    assert rec.movimento_cliente(hist[1], 202510)['recuperado'] is False


def test_volta_da_base_perdida_sai_separado_da_ponte():
    hist = rec.indexar([(1, date(2024, 1, 10), 10, 50.0), (1, date(2025, 6, 10), 10, 80.0)])
    p = rec.ponte(hist, 202506)
    assert p['clientes']['resgatados_perdidos'] == 1 and p['clientes']['recuperados'] == 0
    assert p['venda_recuperada'] == 80.0


# ───────────────────────── a ponte fecha ─────────────────────────
def test_ponte_fecha_no_zero_com_entrada_e_recuperacao_no_mesmo_mes():
    base = date(2025, 1, 5)
    eventos = []
    # c1: para em abr → em risco em jul, fica em risco
    eventos += [(1, d, 10, v) for d, _, v in _mensal(base, 4)]
    # c2: para em mai, entra em risco em ago e é recuperado em ago
    eventos += [(2, d, 10, v) for d, _, v in _mensal(base, 5)] + [(2, date(2025, 8, 25), 10, 90.0)]
    # c3: ativo o ano todo
    eventos += [(3, d, 10, v) for d, _, v in _mensal(base, 12)]
    # c4: em risco desde 2024, vira perdido em ago/25
    eventos += [(4, date(2024, 8, 10), 10, 40.0), (4, date(2024, 7, 10), 10, 40.0)]
    hist = rec.indexar(eventos)
    for am in (202506, 202507, 202508, 202509):
        p = rec.ponte(hist, am)['clientes']
        assert p['risco_fim'] == p['risco_ini'] + p['entraram'] - p['recuperados'] - p['viraram_perdidos'], am
    ago = rec.ponte(hist, 202508)['clientes']
    assert ago['recuperados'] == 1 and ago['viraram_perdidos'] == 1


# ───────────────────────── recuperação que ficou ─────────────────────────
def test_recuperacao_que_ficou_olha_os_dois_meses_seguintes():
    hist = rec.indexar([
        *[(1, d, 10, v) for d, _, v in _mensal(date(2025, 1, 5), 4)],
        (1, date(2025, 8, 10), 10, 100.0), (1, date(2025, 10, 2), 10, 100.0),   # ficou
        *[(2, d, 10, v) for d, _, v in _mensal(date(2025, 1, 5), 4)],
        (2, date(2025, 8, 12), 10, 100.0),                                       # compra isolada
    ])
    r = rec.recuperacao_que_ficou(hist, 202508)
    assert (r['recuperados'], r['ficaram']) == (2, 1) and r['taxa'] == 0.5


def test_lista_em_risco_marca_inativo_no_erp_a_partir_de_91():
    hist = rec.indexar([(1, d, 10, v) for d, _, v in _mensal(date(2025, 1, 5), 6)] +
                       [(2, d, 10, v) for d, _, v in _mensal(date(2025, 2, 5), 6)])
    t = date(2025, 6, 4) + timedelta(days=95)            # c1 parado há 95 d; c2 há 65 d
    lst = {x['codcli']: x for x in rec.em_risco_em(hist, t)}
    assert lst[1]['inativo_erp'] is True and lst[2]['inativo_erp'] is False


def test_quem_comeca_em_risco_e_cruza_o_limite_de_perdido_no_mes_ainda_sai_do_risco():
    """Achado na validação de ago/26: a ponte errava por 1 cliente que estava em risco no dia 1,
    passou de 365 dias no meio do mês e comprou."""
    hist = rec.indexar([(1, date(2024, 8, 10), 10, 50.0), (1, date(2025, 8, 20), 10, 70.0)])
    m = rec.movimento_cliente(hist[1], 202508)
    assert m['ini'] == rec.RISCO and m['recuperado_de'] == rec.RISCO
    p = rec.ponte(hist, 202508)['clientes']
    assert p['risco_fim'] == p['risco_ini'] + p['entraram'] - p['recuperados'] - p['viraram_perdidos']


def test_lista_de_risco_prioriza_valor_vezes_chance_de_voltar():
    """Parado há 300 dias vale menos que parado há 70 com metade do valor (4,6% × 32,5%)."""
    hist = rec.indexar([(1, date(2024, 12, 1), 10, 1200.0), (1, date(2025, 1, 1), 10, 1200.0),
                        (2, date(2025, 8, 1), 10, 600.0), (2, date(2025, 9, 1), 10, 600.0)])
    lst = rec.em_risco_em(hist, date(2025, 11, 10))
    assert [x['codcli'] for x in lst] == [2, 1]
    assert lst[0]['chance_volta'] == 0.325 and lst[1]['chance_volta'] == 0.046
