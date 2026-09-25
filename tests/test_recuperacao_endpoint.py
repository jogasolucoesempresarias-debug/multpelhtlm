"""Página Carteira em risco × recuperada — endpoints, RBAC e contrato com a tela.
Dados sintéticos: os loaders pesados são trocados por fixtures em memória (a matemática está
em tests/test_recuperacao.py e tests/test_potencial.py)."""
from datetime import date, timedelta

import pytest

from tests.conftest import login_as


def _compras():
    """Cliente 1 (dono 573, time 18): compra todo mês até abr/26, some, volta em ago/26 pelo 100.
    Cliente 2 (dono 100, time 18): para em mai/26 → em risco no fim de ago.
    Cliente 3 (dono 820, time 99): ativo o ano todo."""
    out = []
    for i in range(16):
        d = date(2025, 1, 10) + timedelta(days=30 * i)
        if d < date(2026, 5, 1):
            out.append([1, d.isoformat(), 573, 500.0])
        if d < date(2026, 6, 1):
            out.append([2, d.isoformat(), 100, 300.0])
        out.append([3, d.isoformat(), 820, 200.0])
    out.append([1, '2026-08-20', 100, 900.0])
    out += [[3, f'2026-{m:02d}-15', 820, 200.0] for m in (6, 7, 8, 9)]
    return out


@pytest.fixture
def cenario(monkeypatch):
    import server
    server._RECUP_MEMO.update(chave=None, base=None, em=0.0)
    monkeypatch.setattr(server, '_hoje_ref', lambda: date(2026, 9, 24))
    monkeypatch.setattr(server, '_carregar_compras_dia', _compras)
    carteira = [
        {'codcli': 1, 'codusur': 573, 'codsupervisor': 18, 'cliente': 'CLIENTE UM', 'time': 'T18'},
        {'codcli': 2, 'codusur': 100, 'codsupervisor': 18, 'cliente': 'CLIENTE DOIS', 'time': 'T18'},
        {'codcli': 3, 'codusur': 820, 'codsupervisor': 99, 'cliente': 'CLIENTE TRES', 'time': 'T99'},
    ]
    monkeypatch.setattr(server, '_carregar_carteira_full', lambda: carteira)
    monkeypatch.setattr(server, '_carregar_vendedores_map', lambda: {
        '573': {'nome': 'JOAO', 'codsupervisor': 18, 'tipo': 'R'},
        '100': {'nome': 'MARIA', 'codsupervisor': 18, 'tipo': 'R'},
        '820': {'nome': 'OUTRO', 'codsupervisor': 99, 'tipo': 'R'},
    })
    monkeypatch.setattr(server, '_carregar_supervisores_map', lambda: {
        '18': {'nome': 'TIME 18'}, '99': {'nome': 'TIME 99'}})
    vm = {}
    for c, d, u, v in _compras():
        am = int(d[:4]) * 100 + int(d[5:7])
        vm.setdefault(c, {})[am] = vm.get(c, {}).get(am, 0) + v
    monkeypatch.setattr(server, '_carregar_venda_mensal_por_cliente', lambda: vm)
    return server


def test_resumo_admin_ponte_fecha_e_credito_de_quem_vendeu(client, usuario_admin, cenario, clean_redis):
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    d = client.get('/api/recuperacao?mes=202608').get_json()
    assert d['ok'] and d['mes'] == 202608 and d['mes_fechado'] == 202608
    p = d['ponte']['clientes']
    assert p['risco_fim'] == p['risco_ini'] + p['entraram'] - p['recuperados'] - p['viraram_perdidos']
    assert p['recuperados'] == 1                         # cliente 1, recuperado pelo 100
    rcas = {r['codusur']: r for r in d['rcas']}
    assert rcas[100]['rec_por_ele'] == 1 and rcas[100]['rec_de_outra_base'] == 1
    assert rcas[573]['rec_da_base'] == 1 and rcas[573]['rec_por_ele'] == 0
    assert d['cards']['venda_recuperada'] == 900.0
    assert d['regua']['inativo_dias'] == 60
    assert d['cards']['dinheiro_na_mesa'] == pytest.approx(
        d['cards']['camada_a_risco'] + d['cards']['camada_b_positivacao'])


def test_vendedor_so_ve_a_propria_base_e_o_que_ele_recuperou(client, usuario_vendedor, cenario, clean_redis):
    login_as(client, usuario_vendedor['email'], usuario_vendedor['senha'])   # codusur 573
    d = client.get('/api/recuperacao?mes=202608&supervisor=99').get_json()   # querystring não amplia
    assert {r['codusur'] for r in d['rcas']} <= {573}
    assert d['times'] == []
    lst = client.get('/api/recuperacao/listas?tipo=recuperados&mes=202608').get_json()
    assert [r['codcli'] for r in lst['rows']] == [1]     # cliente da base dele, recuperado por outro
    assert lst['rows'][0]['de_outra_base'] is True


def test_lista_de_risco_hoje_e_csv(client, usuario_admin, cenario, clean_redis):
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    lst = client.get('/api/recuperacao/listas?tipo=risco').get_json()
    assert {r['codcli'] for r in lst['rows']} == {2}
    r = lst['rows'][0]
    assert {'chance_volta', 'prioridade', 'inativo_erp', 'dono_nome', 'valor_mensal'} <= set(r)
    csv = client.get('/api/recuperacao/listas/csv?tipo=risco')
    assert csv.status_code == 200 and 'CLIENTE DOIS' in csv.get_data(as_text=True)


def test_mes_fora_da_janela_cai_no_mes_fechado(client, usuario_admin, cenario, clean_redis):
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    assert client.get('/api/recuperacao?mes=199901').get_json()['mes'] == 202608


def test_parametros_so_admin_e_validados(client, usuario_admin, usuario_vendedor, cenario, clean_redis):
    login_as(client, usuario_vendedor['email'], usuario_vendedor['senha'])
    assert client.put('/api/admin/config/recuperacao', json={'inativo_dias': 90}).status_code in (401, 403)
    client.get('/logout')
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    assert client.put('/api/admin/config/recuperacao', json={'inativo_dias': 400}).status_code == 400
    r = client.put('/api/admin/config/recuperacao', json={'inativo_dias': 60, 'perdido_dias': 365})
    assert r.status_code == 200 and r.get_json()['inativo_dias'] == 60
