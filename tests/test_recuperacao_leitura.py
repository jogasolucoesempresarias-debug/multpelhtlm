"""Recuperação — a tela tem de deixar REFAZER as contas (pedido do Gabriel, 05/10/2026: "acredito
que não está batendo, mas não sei explicar o porquê").

As contas fechavam; quem não fechava era a LEITURA. Cinco causas, cada uma travada aqui:
1. "Recuperados" com dois sentidos: a ponte conta só os do RISCO (16), a coluna "Recuperado da base"
   somava também os voltados da base PERDIDA (17). Agora a coluna se abre em risco + perdida.
2. O saldo saía de uma coluna que a tabela não mostrava ("entraram"). Agora ela aparece, e o saldo
   (renomeado "Variação do risco") se refaz com o que está na linha: entraram − recuperados do risco.
3. R$ de duas réguas lado a lado: ao lado do recuperado é VENDA NO MÊS; no saldo e na ponte é VALOR
   MENSAL. O cabeçalho declara cada um.
4. "De outra base" é de mão única (o que ELE tirou dos outros). Faltava a volta: clientes DA BASE
   dele que OUTROS recuperaram (`rec_por_outros`). Com ela a linha fecha:
       recuperado da base = (recuperado por ele − de outra base) + por outros
5. A lista é "em risco HOJE" e a ponte é o FIM do mês escolhido — a tela diz as duas datas.
"""
from pathlib import Path

import pytest

import recuperacao as recup
from recuperacao import ATIVO, RISCO, PERDIDO


def _m(ini=ATIVO, fim=ATIVO, entrou=False, recuperado=False, de=None, vfim=0.0, vrec=0.0, vendas=None,
       perdido=False):
    return {'ini': ini, 'fim': fim, 'valor_ini': 0.0, 'valor_fim': vfim, 'entrou': entrou,
            'recuperado': recuperado, 'recuperado_de': de, 'dias_parado': None, 'valor_mensal_rec': vrec,
            'vendas': vendas or {}, 'virou_perdido': perdido}


# RCAs 29 e 31 no time 17, RCA 30 no time 19.
MOVS = {
    1: _m(fim=RISCO, entrou=True, vfim=100.0),                                     # base 29, entrou
    2: _m(ini=RISCO, recuperado=True, de=RISCO, vrec=70.0, vendas={29: 500.0}),     # base 29, ele mesmo
    3: _m(ini=PERDIDO, recuperado=True, de=PERDIDO, vrec=10.0, vendas={29: 90.0}),  # base 29, da PERDIDA
    4: _m(ini=RISCO, recuperado=True, de=RISCO, vrec=40.0, vendas={30: 300.0}),     # base 29, OUTRO time
    5: _m(ini=RISCO, recuperado=True, de=RISCO, vrec=20.0, vendas={31: 80.0}),      # base 29, colega do time
    6: _m(ini=RISCO, recuperado=True, de=RISCO, vrec=25.0, vendas={29: 60.0}),      # base 30, por 29
    7: _m(ini=RISCO, recuperado=True, de=RISCO, vrec=15.0, vendas={29: 50.0, 30: 40.0}),  # base 30, dois
}
DONO = {1: 29, 2: 29, 3: 29, 4: 29, 5: 29, 6: 30, 7: 30}
TIME = {29: 17, 31: 17, 30: 19}


@pytest.fixture
def pl():
    return recup.placar_de(MOVS, DONO, TIME)


def _slots(pl):
    return [*pl['rcas'].values(), *pl['times'].values()]


# ───────────────────────── 1. risco + perdida ─────────────────────────
def test_recuperado_da_base_se_abre_em_risco_mais_perdida(pl):
    a = pl['rcas'][29]
    assert (a['rec_da_base'], a['rec_risco_da_base'], a['rec_perdido_da_base']) == (4, 3, 1)
    for s in _slots(pl):
        assert s['rec_da_base'] == s['rec_risco_da_base'] + s['rec_perdido_da_base']


# ───────────────────────── 2. o saldo se refaz com a linha ─────────────────────────
def test_variacao_se_refaz_com_as_colunas_da_linha(pl):
    for s in _slots(pl):
        assert s['saldo'] == s['entraram'] - s['rec_risco_da_base']
    assert pl['rcas'][29]['saldo'] == 1 - 3


# ───────────────────────── 4. a volta do "de outra base" ─────────────────────────
def test_por_outros_e_a_base_dele_recuperada_por_quem_nao_e_ele(pl):
    assert pl['rcas'][29]['rec_por_outros'] == 2          # cli 4 (RCA 30) e 5 (colega 31)
    assert pl['rcas'][30]['rec_por_outros'] == 1          # cli 6; o 7 ele vendeu junto → é dele
    assert pl['times'][17]['rec_por_outros'] == 1         # só o cli 4: o 5 foi o próprio time
    assert pl['times'][19]['rec_por_outros'] == 1         # cli 6 (o 7 o time 19 também vendeu)


def test_a_linha_fecha_base_igual_proprios_mais_outros(pl):
    """O que o Gabriel tentou fazer de cabeça no AFONSO: 67 da base, 62 pelo time, 1 de outra base.
    61 são da própria base; os 6 que faltam eram recuperações de OUTROS times — invisíveis."""
    for k, s in [*pl['rcas'].items(), *pl['times'].items()]:
        assert s['rec_da_base'] == (s['rec_por_ele'] - s['rec_de_outra_base']) + s['rec_por_outros'], k


def test_soma_do_por_outros_nao_muda_a_ponte(pl):
    p = recup.ponte_de(MOVS, 202609)
    assert sum(s['saldo'] for s in pl['rcas'].values()) == p['clientes']['entraram'] - p['clientes']['recuperados']


# ───────────────────────── endpoint ─────────────────────────
def test_rota_devolve_os_campos_novos(client, usuario_admin, monkeypatch):
    import server
    from tests.conftest import login_as
    server._R.flushall()
    monkeypatch.setitem(server.CONFIG, 'data_source', 'postgres')
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    j = client.get('/api/recuperacao').get_json()
    for s in [*j['times'], *j['rcas']]:
        assert {'rec_perdido_da_base', 'rec_por_outros', 'entraram', 'valor_entraram'} <= set(s)
        assert s['rec_da_base'] == s['rec_risco_da_base'] + s['rec_perdido_da_base']


def test_cache_da_resposta_subiu_de_versao():
    fonte = Path('server.py').read_text(encoding='utf-8')
    assert "'recuperacao:resumo:v3'" in fonte and "'recuperacao:resumo:v2'" not in fonte


# ───────────────────────── tela ─────────────────────────
HTML = Path('recuperacao.html')


def test_placar_mostra_entraram_por_outros_e_a_variacao():
    h = HTML.read_text(encoding='utf-8')
    assert h.count('>Entraram em risco<') >= 2              # times e vendedores
    assert h.count('>Por outros<') >= 2
    assert h.count('>Variação do risco<') >= 2
    assert '>Saldo<' not in h                               # o "+179" em vermelho lia como ganho
    assert 'rec_perdido_da_base' in h and 'rec_por_outros' in h and 'valor_entraram' in h


def test_cada_rs_declara_a_regua():
    h = HTML.read_text(encoding='utf-8')
    assert h.count('<span class="small">venda no mês</span></th>') >= 4   # recuperado da base / por ele, 2 tabelas
    assert 'Recuperados do risco' in h                      # caixa da ponte: não é o "recuperados" do card


def test_lista_e_ponte_declaram_a_data():
    h = HTML.read_text(encoding='utf-8')
    assert 'em risco em ${' in h                            # a lista leva a data de hoje
    assert 'j.hoje' in h
