"""Testes puros do módulo cohort. Sem mock — rodam em ms."""
import pytest

from cohort import (
    mes_iso,
    cohort_de_compras,
    matriz_cohort,
    clientes_no_bucket,
)


def test_mes_iso_string_e_date():
    from datetime import date
    assert mes_iso('2026-05-15') == '2026-05'
    assert mes_iso('2026-05-15T10:30:00') == '2026-05'
    assert mes_iso(date(2026, 5, 15)) == '2026-05'


def test_cliente_unico_uma_compra():
    """Cliente único com 1 compra → cohort 1 célula M+0=1."""
    cohorts = cohort_de_compras({1: ['2025-05-10']})
    assert '2025-05' in cohorts
    assert cohorts['2025-05'][0] == {1}
    assert len(cohorts['2025-05']) == 1  # só M+0


def test_diagonal_cheia():
    """12 compras mensais consecutivas → diagonal completa M+0..M+11."""
    datas = [f'2025-{m:02d}-15' for m in range(1, 13)]
    cohorts = cohort_de_compras({1: datas})
    assert cohorts['2025-01'][0] == {1}
    assert cohorts['2025-01'][5] == {1}  # jun
    assert cohorts['2025-01'][11] == {1}  # dez
    # Todos os 12 buckets devem ter o cliente
    for n in range(12):
        assert cohorts['2025-01'].get(n) == {1}


def test_2_clientes_mesmo_cohort_1_churn_em_M3():
    """2 clientes adquiridos no mesmo mês. Um churna em M+3 → célula M+3 = {cli_que_voltou}."""
    cohorts = cohort_de_compras({
        1: ['2025-01-10', '2025-02-10', '2025-03-10', '2025-04-10', '2025-05-10'],  # 5 meses consecutivos
        2: ['2025-01-15', '2025-02-15', '2025-03-15'],  # churnou em M+3 (sem abril/maio)
    })
    assert cohorts['2025-01'][0] == {1, 2}
    assert cohorts['2025-01'][1] == {1, 2}
    assert cohorts['2025-01'][2] == {1, 2}
    assert cohorts['2025-01'][3] == {1}  # só cliente 1
    assert cohorts['2025-01'][4] == {1}


def test_cliente_pulou_meses():
    """Cliente comprou em ago/2025 (M+0) + nov/2025 (M+3). Buckets M+1/M+2 SEM esse cliente."""
    cohorts = cohort_de_compras({1: ['2025-08-10', '2025-11-10']})
    assert cohorts['2025-08'][0] == {1}
    assert 1 not in cohorts['2025-08'].get(1, set())
    assert 1 not in cohorts['2025-08'].get(2, set())
    assert cohorts['2025-08'][3] == {1}


def test_edge_vazio():
    assert cohort_de_compras({}) == {}
    assert cohort_de_compras({1: []}) == {}


def test_compras_no_mesmo_mes_contam_uma_vez_por_bucket():
    """3 compras em jan/2025 → cliente está só 1x em M+0 (set deduplica)."""
    cohorts = cohort_de_compras({1: ['2025-01-05', '2025-01-15', '2025-01-25']})
    assert cohorts['2025-01'][0] == {1}
    assert len(cohorts['2025-01'][0]) == 1


def test_matriz_cohort_retencao_percentual():
    """3 clientes, 2 sobrevivem em M+1, 1 em M+2."""
    cohorts = cohort_de_compras({
        1: ['2025-05-01', '2025-06-01', '2025-07-01'],
        2: ['2025-05-01', '2025-06-01'],
        3: ['2025-05-01'],
    })
    matriz = matriz_cohort(cohorts, meses_max=5)
    assert len(matriz) == 1  # 1 cohort só (2025-05)
    c = matriz[0]
    assert c['aquisicao'] == '2025-05'
    assert c['tamanho'] == 3
    assert c['retencao'][0] == 1.0
    assert c['retencao'][1] == pytest.approx(2/3, abs=0.001)
    assert c['retencao'][2] == pytest.approx(1/3, abs=0.001)
    assert c['retencao'][3] == 0.0
    assert c['absolutos'] == [3, 2, 1, 0, 0, 0]


def test_clientes_no_bucket_para_drill():
    cohorts = cohort_de_compras({
        1: ['2025-05-01', '2025-06-01'],
        2: ['2025-05-01'],
        3: ['2025-05-01', '2025-06-01', '2025-07-01'],
    })
    # M+1 do cohort 2025-05: clientes 1 e 3 (cliente 2 não voltou)
    assert clientes_no_bucket(cohorts, '2025-05', 1) == [1, 3]
    # M+2 do cohort 2025-05: só cliente 3
    assert clientes_no_bucket(cohorts, '2025-05', 2) == [3]
    # M+0: todos os 3
    assert clientes_no_bucket(cohorts, '2025-05', 0) == [1, 2, 3]
