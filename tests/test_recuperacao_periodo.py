"""Gate dos pedidos do João de 06/11/2026 na Recuperação.

1. **Período** ("colocar tipo para puxar: mês atual, 3 meses, 6 meses, ano"): os fluxos somam, a
   posição é a do fim do período e a ponte continua fechando no zero. 3 e 6 meses e Ano contam só
   meses fechados. Sem seleção livre: com mês salteado a ponte deixaria de fechar.
2. **O mês em curso termina HOJE.** A ponte calculava o "fim" no dia 1º do mês seguinte: em 06/11 a
   tela dizia 729 entradas e 2.948 em risco, quando até o dia eram 100 e 2.457.
3. **Saldo com os da base perdida** ("ele recuperou 24, entraram 20, e fica +1 porque 3 estavam como
   perdidos"): quem entrou em risco, virou perdido e voltou ficava −1 para sempre.
"""
from datetime import date
from pathlib import Path

import pytest

import evolucao_carteira as evol
import recuperacao as recup
from recuperacao import ATIVO, PERDIDO, RISCO

HTML = Path('recuperacao.html').read_text(encoding='utf-8')


# ─────────────────────── quais meses ───────────────────────
def test_meses_do_periodo():
    assert recup.meses_do_periodo('mes_atual', 202610, 202611) == [202611]
    assert recup.meses_do_periodo('3m', 202610, 202611) == [202608, 202609, 202610]
    assert recup.meses_do_periodo('6m', 202610, 202611) == [202605, 202606, 202607, 202608, 202609, 202610]
    assert recup.meses_do_periodo('ano', 202610, 202611)[0] == 202601
    assert recup.meses_do_periodo('ano', 202610, 202611)[-1] == 202610


def test_periodo_que_atravessa_a_virada_do_ano():
    assert recup.meses_do_periodo('3m', 202601, 202602) == [202511, 202512, 202601]


def test_em_janeiro_o_ano_e_o_anterior_inteiro():
    assert recup.meses_do_periodo('ano', 202512, 202601) == [202500 + m for m in range(1, 13)]


def test_periodo_desconhecido_nao_inventa_meses():
    assert recup.meses_do_periodo('xpto', 202610, 202611) is None
    assert recup.meses_do_periodo(None, 202610, 202611) is None


# ─────────────────────── a ponte do período ───────────────────────
def _hist():
    """Clientes com histórico real: a ponte sai do motor, não de números cravados."""
    ev = []
    for c, datas in {1: ['2026-01-10', '2026-05-20'], 2: ['2026-02-01'], 3: ['2026-06-10', '2026-09-15'],
                     4: ['2025-01-10', '2026-08-05'], 5: ['2026-03-01', '2026-03-20', '2026-07-01', '2026-10-02']}.items():
        ev += [(c, d, 29, 100.0) for d in datas]
    return recup.indexar(ev)


def test_a_ponte_do_periodo_fecha_no_zero():
    h = _hist()
    pontes = [recup.ponte(h, m) for m in (202608, 202609, 202610)]
    p = recup.somar_pontes(pontes)['clientes']
    assert p['risco_ini'] == pontes[0]['clientes']['risco_ini']
    assert p['risco_fim'] == pontes[-1]['clientes']['risco_fim']
    assert p['risco_fim'] == p['risco_ini'] + p['entraram'] - p['recuperados'] - p['viraram_perdidos']


def test_um_mes_so_e_a_ponte_do_mes():
    h = _hist()
    uma = recup.ponte(h, 202609)
    assert recup.somar_pontes([uma])['clientes'] == uma['clientes']
    assert recup.somar_pontes([uma])['valor_mensal'] == uma['valor_mensal']


def _s(**kw):
    base = {'em_risco': 0, 'valor_em_risco': 0.0, 'rec_da_base': 0, 'venda_rec_da_base': 0.0, 'rec_por_ele': 0,
            'venda_rec_por_ele': 0.0, 'rec_de_outra_base': 0, 'entraram': 0, 'valor_entraram': 0.0,
            'rec_risco_da_base': 0, 'valor_rec_risco_da_base': 0.0, 'valor_rec_da_base': 0.0,
            'rec_perdido_da_base': 0, 'rec_por_outros': 0, 'saldo': 0, 'valor_saldo': 0.0}
    return {**base, **kw}


def test_placar_soma_fluxo_e_fica_com_a_posicao_do_fim():
    m1 = {'rcas': {29: _s(em_risco=10, valor_em_risco=1000.0, entraram=2, rec_da_base=1, valor_entraram=50.0,
                          valor_rec_da_base=20.0, saldo=-1),
                   30: _s(em_risco=7, valor_em_risco=70.0, entraram=1)}, 'times': {}}
    m2 = {'rcas': {29: _s(em_risco=8, valor_em_risco=800.0, entraram=1, rec_da_base=3, valor_entraram=10.0,
                          valor_rec_da_base=90.0, saldo=2)}, 'times': {}}
    pl = recup.somar_placares([m1, m2])['rcas']
    assert (pl[29]['em_risco'], pl[29]['valor_em_risco']) == (8, 800.0), "estoque não soma: é o do fim"
    assert (pl[29]['entraram'], pl[29]['rec_da_base'], pl[29]['saldo']) == (3, 4, 1)
    assert pl[29]['valor_saldo'] == pytest.approx(110.0 - 60.0)
    assert pl[30]['em_risco'] == 0, "quem não aparece no último mês não tem estoque no fim"
    assert pl[30]['saldo'] == -1


def test_lista_de_recuperados_tem_uma_linha_por_cliente():
    a = [{'codcli': 1, 'dono': 29, 'vendedores': [29], 'de_outra_base': False, 'recuperado_de': RISCO,
          'dias_parado': 70, 'valor_mensal': 10.0, 'venda_mes': 100.0}]
    b = [{'codcli': 1, 'dono': 29, 'vendedores': [30], 'de_outra_base': True, 'recuperado_de': PERDIDO,
          'dias_parado': 400, 'valor_mensal': 5.0, 'venda_mes': 50.0},
         {'codcli': 2, 'dono': 29, 'vendedores': [29], 'de_outra_base': False, 'recuperado_de': RISCO,
          'dias_parado': 65, 'valor_mensal': 9.0, 'venda_mes': 900.0}]
    out = recup.juntar_recuperados([a, b])
    assert [r['codcli'] for r in out] == [2, 1]
    um = out[1]
    assert (um['vezes'], um['venda_mes'], um['vendedores']) == (2, 150.0, [29, 30])
    assert um['recuperado_de'] == PERDIDO and um['de_outra_base'], "o resto é da recuperação mais recente"


# ─────────────────────── mês em curso ───────────────────────
HOJE = date(2026, 11, 6)


def test_mes_em_curso_termina_hoje_e_nao_no_dia_1_do_mes_seguinte():
    """Compra em 20/09: em 06/11 são 47 dias (ativo). No dia 1º/12 seriam 72 (em risco): o cliente
    entrava em risco por uma data que ainda não chegou."""
    h = recup.indexar([(1, '2026-09-20', 29, 100.0)])[1]
    assert recup.movimento_cliente(h, 202611)['fim'] == RISCO          # o defeito, sem o teto
    assert not recup.movimento_cliente(h, 202611, hoje=HOJE)['entrou']
    assert recup.movimento_cliente(h, 202611, hoje=HOJE)['fim'] == ATIVO


def test_o_fim_do_mes_em_curso_e_a_lista_de_hoje():
    from datetime import timedelta
    h = recup.indexar([(1, '2026-09-20', 29, 100.0), (2, '2026-08-20', 29, 100.0), (3, '2026-11-02', 29, 100.0)])
    p = recup.ponte_de(recup.movimentos(h, 202611, hoje=HOJE), 202611)
    assert p['clientes']['risco_fim'] == len(recup.em_risco_em(h, HOJE + timedelta(days=1))) == 1


def test_compra_de_hoje_conta_no_mes_em_curso():
    h = recup.indexar([(1, '2026-08-01', 29, 100.0), (1, '2026-11-06', 29, 100.0)])[1]
    m = recup.movimento_cliente(h, 202611, hoje=HOJE)
    assert m['recuperado'] and m['fim'] == ATIVO


def test_mes_fechado_nao_muda_com_o_teto():
    h = _hist()
    for c, x in h.items():
        assert recup.movimento_cliente(x, 202609) == recup.movimento_cliente(x, 202609, hoje=HOJE), c


# ─────────────────────── saldo com os da perdida ───────────────────────
def _m(de, vrec=10.0):
    return {'ini': de, 'fim': ATIVO, 'valor_ini': 0.0, 'valor_fim': 0.0, 'entrou': False, 'recuperado': True,
            'recuperado_de': de, 'dias_parado': 100, 'valor_mensal_rec': vrec, 'vendas': {29: 100.0},
            'virou_perdido': False}


def test_o_caso_do_andre_21_do_risco_3_da_perdida_20_entraram():
    movs = {i: _m(RISCO) for i in range(21)}
    movs.update({100 + i: _m(PERDIDO) for i in range(3)})
    movs.update({200 + i: {'ini': ATIVO, 'fim': RISCO, 'valor_ini': 0.0, 'valor_fim': 5.0, 'entrou': True,
                           'recuperado': False, 'recuperado_de': None, 'dias_parado': None,
                           'valor_mensal_rec': 0.0, 'vendas': {}, 'virou_perdido': False} for i in range(20)})
    dono = {c: 29 for c in movs}
    s = recup.placar_de(movs, dono, {29: 17})['rcas'][29]
    assert (s['rec_risco_da_base'], s['rec_perdido_da_base'], s['rec_da_base']) == (21, 3, 24)
    assert s['saldo'] == 4
    assert s['valor_saldo'] == pytest.approx(24 * 10.0 - 20 * 5.0)


def test_a_ponte_continua_sem_a_perdida():
    """A perdida conta no saldo, mas não passa pelo estoque em risco: a ponte não muda."""
    movs = {1: _m(RISCO), 2: _m(PERDIDO)}
    p = recup.ponte_de(movs, 202610)
    assert (p['clientes']['recuperados'], p['clientes']['resgatados_perdidos'], p['clientes']['risco_ini']) == (1, 1, 1)
    assert p['clientes']['risco_fim'] == p['clientes']['risco_ini'] + p['clientes']['entraram'] - p['clientes']['recuperados']
    assert p['valor_mensal']['resgatados_perdidos'] == 10.0


def test_a_evolucao_usa_o_mesmo_saldo():
    pl = recup.placar_de({1: _m(RISCO), 2: _m(PERDIDO)}, {1: 29, 2: 29}, {29: 17})['rcas'][29]
    ln = evol.linha_de(202610, {}, pl)
    assert ln['saldo'] == pl['saldo'] == 2
    assert ln['recuperados'] == 2


# ─────────────────────── a tela ───────────────────────
def test_a_tela_oferece_os_periodos_e_nao_mes_salteado():
    assert "setPeriodo('${k}')" in HTML and "p.set('periodo', _periodo)" in HTML
    assert recup.PERIODOS == {'mes_atual': 'Mês atual', '3m': '3 meses', '6m': '6 meses', 'ano': 'Ano'}


def test_a_tela_abre_o_saldo_e_a_barra_e_a_diferenca_da_linha():
    assert 'do risco + ${NUM.format(p.resgatados_perdidos)} da perdida' in HTML
    assert 'x.clientes.recuperados + (x.clientes.resgatados_perdidos || 0)' in HTML


def test_botoes_na_ordem_pedida():
    """O JSON ordena chaves: com dict, "Mês atual" saía por último (visto no print)."""
    import inspect
    import server
    assert "list(recup.PERIODOS.items())" in inspect.getsource(server.api_recuperacao)
    assert list(recup.PERIODOS) == ['mes_atual', '3m', '6m', 'ano']
    assert "(d.periodos || []).map(([k, l])" in HTML


def test_dinheiro_na_mesa_do_mes_em_curso_usa_o_ultimo_mes_fechado():
    """Em 06/11 a positivação dava R$ 1,23 mi contra R$ 388 mil de outubro: no começo do mês quase
    ninguém comprou. A tela declara de qual mês é."""
    import inspect
    import server
    assert "base['ref'] if am == base['atual'] else am" in inspect.getsource(server.api_recuperacao)
    assert 'o último mês fechado' in HTML


def test_a_rota_entrega_o_periodo():
    import inspect
    import server
    src = inspect.getsource(server.api_recuperacao)
    assert "'per': periodo or ''" in src, "o período tem de entrar na chave do cache"
    assert 'recup.somar_pontes(' in src and 'recup.somar_placares(' in src
    assert 'hoje)' in inspect.getsource(server._recup_base).split('recup.movimentos(')[1].split('\n')[0]
