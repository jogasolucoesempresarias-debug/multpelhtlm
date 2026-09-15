"""Carteira por PRAÇA (pedido do João Victor 09/2026) — agregação por UF/cidade do conjunto
FILTRADO de `_filtrar_carteira`. Função pura: clientes sintéticos.

O que trava:
- a praça sai do MESMO conjunto dos segmentos/tabela (clicar em "Perdidos" mostra onde estão);
- sem UF filtrada agrega por UF; com UF, desce para CIDADE (uma fatia só não serve);
- três medidas (clientes, venda 12m, receita em risco) e a soma fecha com o total;
- UF/cidade vazias não somem: viram '—' (o cliente continua contando).
"""
import server
from tests.conftest import login_as


def _cli(codcli, uf, cidade, seg='loyal', venda=100.0, risco=10.0):
    return {'codcli': codcli, 'uf': uf, 'cidade': cidade, 'segmento': seg, 'recencia_dias': 5,
            'venda_12m': venda, 'receita_perdida_proj': risco, 'codusur': 1, 'codsupervisor': 10}


CLIENTES = [
    _cli(1, 'ES', 'VITORIA', 'lost', 500, 50),
    _cli(2, 'ES', 'VITORIA', 'loyal', 200, 0),
    _cli(3, 'ES', 'CACHOEIRO', 'lost', 1000, 300),
    _cli(4, 'BA', 'ILHEUS', 'lost', 50, 5),
    _cli(5, 'ba', 'ITABUNA', 'loyal', 80, 0),          # caixa baixa no cadastro
    _cli(6, None, None, 'loyal', 10, 0),                # sem UF
]


def test_por_uf_sem_filtro_fecha_com_o_total():
    r = server._filtrar_carteira(CLIENTES, {'limit': 100})
    p = r['pracas']
    assert p['nivel'] == 'uf'
    por = {i['praca']: i for i in p['itens']}
    assert set(por) == {'ES', 'BA', '—'}
    assert por['ES']['clientes'] == 3 and por['BA']['clientes'] == 2 and por['—']['clientes'] == 1
    assert sum(i['clientes'] for i in p['itens']) == r['total'] == 6
    assert por['ES']['venda_12m'] == 1700.0 and por['ES']['receita_perdida'] == 350.0
    assert por['BA']['venda_12m'] == 130.0                   # 'ba' e 'BA' somam juntos
    # ordenado por clientes desc
    assert [i['praca'] for i in p['itens']] == ['ES', 'BA', '—']


def test_com_uf_filtrada_desce_para_cidade():
    r = server._filtrar_carteira(CLIENTES, {'uf': 'ES', 'limit': 100})
    p = r['pracas']
    assert p['nivel'] == 'cidade'
    assert [(i['praca'], i['clientes']) for i in p['itens']] == [('VITORIA', 2), ('CACHOEIRO', 1)]
    assert r['total'] == 3


def test_praca_respeita_o_filtro_de_segmento():
    """Clicar em 'Perdidos' → a praça mostra ONDE estão os perdidos (o valor do pedido)."""
    r = server._filtrar_carteira(CLIENTES, {'segmento': 'lost', 'limit': 100})
    por = {i['praca']: i['clientes'] for i in r['pracas']['itens']}
    assert por == {'ES': 2, 'BA': 1}
    assert r['segmentos'] == {'lost': 3}                     # mesmo conjunto dos cards


def test_praca_respeita_paginacao_nao():
    """A praça é do conjunto filtrado INTEIRO, não da página (como os segmentos)."""
    r = server._filtrar_carteira(CLIENTES, {'limit': 1, 'offset': 0})
    assert len(r['rows']) == 1
    assert sum(i['clientes'] for i in r['pracas']['itens']) == 6


def test_recorte_vazio_devolve_lista_vazia():
    r = server._filtrar_carteira(CLIENTES, {'uf': 'MG', 'limit': 100})
    assert r['pracas'] == {'nivel': 'cidade', 'itens': []}


def test_endpoint_modo_postgres_entrega_pracas(client, usuario_admin, monkeypatch):
    server._R.flushall()
    monkeypatch.setitem(server.CONFIG, 'data_source', 'postgres')
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    d = client.get('/api/carteira/clientes?limit=1').get_json()
    assert d['ok'] and d['pracas']['nivel'] == 'uf' and len(d['pracas']['itens']) >= 2
    assert sum(i['clientes'] for i in d['pracas']['itens']) == d['total']
    uf = d['pracas']['itens'][0]['praca']
    d2 = client.get(f'/api/carteira/clientes?limit=1&uf={uf}').get_json()
    assert d2['pracas']['nivel'] == 'cidade' and d2['pracas']['itens']
    assert sum(i['clientes'] for i in d2['pracas']['itens']) == d2['total']
