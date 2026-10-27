"""Gate do filtro de Time na /uso (10/2026, pedido do Gabriel: "tem como filtrar por time?").

Decisões que estes testes travam (`server._uso_grupos`):
  · vendedor cai no time do CADASTRO do RCA (`PCUSUARI.CODSUPERVISOR`), o de HOJE;
  · supervisor aparece em cada time que cobre — MAS, com 4+ áreas, vira Gestão. Na Evolução da
    carteira um supervisor com quase todas as áreas marcava todos os times como "usa" (06/10);
  · admin/viewer = Gestão; quem não tem a área Comercial = Compras (não tem time comercial);
  · ninguém some do filtro: o que não casa vai para "Sem time";
  · o BI fora do ar não derruba a tela de adoção (`times_ok=False`, nomes "Time N").
"""
from pathlib import Path

import pytest

import server
from tests.conftest import _criar_usuario, _remover_usuario, login_as

VMAP = {'101': {'codsupervisor': 12.0}, '102': {'codsupervisor': None}}   # float, como vem do BI
SMAP = {'12': {'nome': 'AFONSO ES-SUL'}, '13': {'nome': 'G. VITÓRIA'}}
COM = ['comercial']


def g(role, areas=COM, codusur=None, sups=None, sup=None):
    return [k for k, _ in server._uso_grupos(role, areas, codusur, sups or [], sup, VMAP, SMAP)]


def test_vendedor_cai_no_time_do_cadastro_do_rca():
    assert server._uso_grupos('vendedor', COM, 101, [], None, VMAP, SMAP) == [('t12', 'AFONSO ES-SUL')]


def test_vendedor_sem_codigo_ou_sem_time_vai_para_sem_time():
    assert g('vendedor') == ['sem']
    assert g('vendedor', codusur=102) == ['sem']
    assert g('vendedor', codusur=999) == ['sem']


def test_supervisor_aparece_em_cada_time_que_cobre():
    assert g('supervisor', sups=[13, 12, '12']) == ['t12', 't13']
    assert g('supervisor', sup=13) == ['t13']          # coluna legada (área única)


def test_supervisor_com_4_areas_ou_mais_e_gestao():
    assert g('supervisor', sups=[1, 2, 3]) == ['t1', 't2', 't3']
    assert g('supervisor', sups=[1, 2, 3, 4]) == ['gestao']


def test_admin_e_viewer_sao_gestao_e_sem_comercial_e_compras():
    assert g('admin') == ['gestao'] and g('viewer') == ['gestao']
    assert g('viewer', areas=['compras']) == ['compras']
    assert g('supervisor', areas=['compras'], sups=[12]) == ['compras']


def test_time_sem_nome_no_bi_sai_como_time_n():
    assert server._uso_grupos('supervisor', COM, None, [77], None, {}, {}) == [('t77', 'Time 77')]


@pytest.fixture
def vendedor_real():
    email = 'joao.rca@empresa.com.br'    # fora do filtro de contas de teste da /uso
    uid = _criar_usuario(email, 'senha123', role='vendedor', codusur=101, must_change=False)
    yield uid
    _remover_usuario(email)


def test_endpoint_traz_o_time_de_cada_usuario(client, usuario_admin, vendedor_real, monkeypatch):
    monkeypatch.setattr(server, '_carregar_vendedores_map', lambda: VMAP)
    monkeypatch.setattr(server, '_carregar_supervisores_map', lambda: SMAP)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    j = client.get('/api/admin/uso').get_json()
    assert j['times_ok'] is True
    u = next(x for x in j['usuarios'] if x['id'] == vendedor_real)
    assert u['times'] == [{'chave': 't12', 'nome': 'AFONSO ES-SUL'}]
    assert all(x['times'] for x in j['usuarios'])        # ninguém fica fora do filtro


def test_bi_fora_do_ar_nao_derruba_a_tela(client, usuario_admin, vendedor_real, monkeypatch):
    def falha():
        raise RuntimeError('BI fora')
    monkeypatch.setattr(server, '_carregar_vendedores_map', falha)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    r = client.get('/api/admin/uso')
    assert r.status_code == 200
    j = r.get_json()
    assert j['times_ok'] is False
    u = next(x for x in j['usuarios'] if x['id'] == vendedor_real)
    assert u['times'] == [{'chave': 'sem', 'nome': 'Sem time'}]


def test_front_recalcula_os_cards_sobre_o_recorte():
    html = Path('uso.html').read_text(encoding='utf-8')
    assert 'id="time"' in html
    assert 'resumoDe(us)' in html            # card e tabela no MESMO universo
    assert "$('#time').onchange = aplicarFiltro" in html
