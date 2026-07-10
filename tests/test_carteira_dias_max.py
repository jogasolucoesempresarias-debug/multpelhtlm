"""Testa o filtro dias_min/dias_max de _filtrar_carteira (deep-link de faixa do Gerencial).
Função pura — clientes sintéticos, independente da data de hoje."""
import server


def _cli(codcli, recencia):
    return {'codcli': codcli, 'recencia_dias': recencia, 'venda_12m': 0, 'segmento': 'loyal',
            'codusur': 1, 'codsupervisor': 10}


CLIENTES = [_cli(1, 5), _cli(2, 20), _cli(3, 40), _cli(4, 75), _cli(5, 200)]


def test_faixa_31_45_isola_intervalo():
    r = server._filtrar_carteira(CLIENTES, {'dias_min': 31, 'dias_max': 45, 'limit': 100})
    assert {c['codcli'] for c in r['rows']} == {3}      # só o de 40 dias


def test_faixa_0_15():
    r = server._filtrar_carteira(CLIENTES, {'dias_min': 0, 'dias_max': 15, 'limit': 100})
    assert {c['codcli'] for c in r['rows']} == {1}      # só o de 5 dias


def test_faixa_91_mais_so_min():
    r = server._filtrar_carteira(CLIENTES, {'dias_min': 91, 'limit': 100})
    assert {c['codcli'] for c in r['rows']} == {5}      # 200 dias


def test_rollup_0_30():
    r = server._filtrar_carteira(CLIENTES, {'dias_min': 0, 'dias_max': 30, 'limit': 100})
    assert {c['codcli'] for c in r['rows']} == {1, 2}   # 5 e 20 dias


def test_sem_dias_nao_filtra():
    r = server._filtrar_carteira(CLIENTES, {'limit': 100})
    assert r['total'] == 5
