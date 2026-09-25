"""Curva ABC de clientes na Carteira (item 1) — classe do INÍCIO do mês, card por classe e filtro.
A classe NUNCA inclui o próprio mês medido (medido: incluir infla a positivação ~2,5–3 p.p.)."""
from datetime import date

import pytest

from tests.conftest import login_as


def _cli(codcli, codusur=573, sup=18):
    return {'codcli': codcli, 'codusur': codusur, 'codsupervisor': sup, 'cliente': f'C{codcli}',
            'cidade': 'X', 'uf': 'ES', 'segmento': 'loyal', 'status_personalizada': 'ok',
            'status_fixa': 'ok', 'recencia_dias': 5, 'frequencia_12m': 10, 'venda_12m': 100.0,
            'lucro_12m': 20.0, 'receita_perdida_proj': 0.0, 'lucro_perdido_proj': 0.0,
            'vendedor': 'V', 'time': 'T'}


@pytest.fixture
def cenario(monkeypatch):
    import server
    monkeypatch.setattr(server, '_hoje_ref', lambda: date(2026, 9, 10))
    # 1 cliente grande (A) e 9 pequenos; o grande só "vira A" se o mês de ago entrar na conta
    vm = {1: {m: 10.0 for m in range(202509, 202513)} | {m: 10.0 for m in range(202601, 202608)} | {202608: 5000.0}}
    for c in range(2, 11):
        vm[c] = {m: 100.0 for m in range(202509, 202513)} | {m: 100.0 for m in range(202601, 202609)}
    vm[2][202609] = 50.0                     # comprou no mês corrente (dia 5)
    monkeypatch.setattr(server, '_carregar_venda_mensal_por_cliente', lambda: vm)
    monkeypatch.setattr(server, '_carregar_devolucao_mensal_por_cliente', lambda: {})
    monkeypatch.setattr(server, '_carregar_carteira_full', lambda: [_cli(c) for c in range(1, 11)])
    monkeypatch.setattr(server, '_carregar_compras_dia', lambda: [
        [2, '2026-09-05', 573, 50.0], [3, '2026-08-04', 573, 100.0], [4, '2026-08-20', 573, 100.0]])
    return server


def test_card_mede_o_mes_fechado_com_a_classe_do_inicio_dele(client, usuario_admin, cenario, clean_redis):
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    d = client.get('/api/carteira/positivacao-abc').get_json()
    assert d['ok'] and d['mes_fechado'] == 202608
    # Cliente 1 comprou R$ 5.000 em ago: no INÍCIO de ago ele era pequeno (C), não A
    tot = {k: d['fechado'][k]['base'] for k in 'ABC'}
    assert sum(tot.values()) == 10
    assert d['fechado']['C']['positivados'] >= 1       # o cliente 1 conta na classe do início (C)
    # parcial: até o dia 10 de set × até o dia 10 de ago (só o cliente 3 comprou até dia 10 em ago)
    p = d['parcial']
    assert p['dia'] == 10
    assert sum(p['atual'][k]['positivados'] for k in 'ABC') == 1          # cliente 2 (dia 5)
    assert sum(p['anterior_mesmo_dia'][k]['positivados'] for k in 'ABC') == 1   # cliente 3 (dia 4)


def test_coluna_e_filtro_de_classe_na_tabela(client, usuario_admin, cenario, clean_redis):
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    todos = client.get('/api/carteira/clientes?limit=50').get_json()['rows']
    assert all(r.get('classe_abc') in ('A', 'B', 'C') for r in todos)
    # classe do início de SET inclui agosto: agora o cliente 1 (R$ 5.000 em ago) é A
    assert next(r for r in todos if r['codcli'] == 1)['classe_abc'] == 'A'
    so_a = client.get('/api/carteira/clientes?classe=A&limit=50').get_json()
    assert {r['classe_abc'] for r in so_a['rows']} == {'A'}
