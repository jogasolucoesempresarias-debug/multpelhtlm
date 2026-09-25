"""Potencial de positivação (potencial.py) — camada b do "dinheiro na mesa"."""
import pytest

import potencial as pot


def _base(dono, classe, n, positivados, ticket=100.0, univ='R', inicio=0):
    return [{'codcli': inicio + i, 'dono': dono, 'universo': univ, 'classe': classe,
             'comprou': i < positivados, 'ticket': ticket} for i in range(n)]


def test_percentil_interpolado():
    assert pot.percentil([0.2, 0.4, 0.6, 0.8], 0.75) == pytest.approx(0.65)
    assert pot.percentil([], 0.75) is None


def test_gap_e_a_distancia_ate_a_referencia_vezes_clientes_vezes_ticket():
    linhas = (_base(1, 'A', 10, 5, inicio=0) + _base(2, 'A', 10, 7, inicio=100)
              + _base(3, 'A', 10, 9, inicio=200) + _base(4, 'A', 10, 9, inicio=300))
    r = pot.gap_positivacao(linhas)
    ref = r['referencia'][('R', 'A')]
    assert ref == pytest.approx(0.9)                       # p75 de [.5,.7,.9,.9]
    assert r['por_rca'][1]['gap'] == pytest.approx((0.9 - 0.5) * 10 * 100)
    assert r['por_rca'][3]['gap'] == 0                     # acima da referência não gera gap negativo


def test_formula_do_pedido_sem_referencia_nao_gera_potencial():
    """Todo mundo igual = referência igual à própria positivação = gap zero. A fórmula
    'clientes × ticket × positivação média' só devolveria a venda que já existe."""
    linhas = _base(1, 'A', 10, 6) + _base(2, 'A', 10, 6, inicio=50)
    assert pot.gap_positivacao(linhas)['total'] == 0


def test_cliente_em_risco_fica_fora_para_nao_contar_duas_vezes():
    linhas = _base(1, 'A', 12, 6) + _base(2, 'A', 10, 9, inicio=100)
    com = pot.gap_positivacao(linhas)
    sem = pot.gap_positivacao(linhas, excluir={6, 7})       # 2 não-compradores do RCA 1 em risco
    assert sem['por_rca'][1]['classes']['A']['n'] == 10
    assert sem['por_rca'][1]['gap'] < com['por_rca'][1]['gap']


def test_universos_nao_se_misturam():
    """Loja não serve de referência para campo."""
    linhas = (_base(1, 'A', 10, 5, univ='R') + _base(2, 'A', 10, 5, univ='R', inicio=50)
              + _base(9, 'A', 10, 10, univ='I', inicio=100))
    r = pot.gap_positivacao(linhas)
    assert r['referencia'][('R', 'A')] == pytest.approx(0.5)
    assert r['por_rca'][1]['gap'] == 0


def test_grupo_pequeno_nao_gera_gap_nem_entra_na_referencia():
    linhas = _base(1, 'A', 3, 0) + _base(2, 'A', 10, 5, inicio=50)
    r = pot.gap_positivacao(linhas)
    assert r['por_rca'][1]['gap'] == 0
    assert r['referencia'][('R', 'A')] == pytest.approx(0.5)
