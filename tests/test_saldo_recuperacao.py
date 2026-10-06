"""Saldo do mês na Recuperação (pedido do João, 29/09/2026: "risco menos recuperado = saldo").

⚠️ SINAL INVERTIDO em 06/10/2026 (João: "na nossa cabeça + é bom e − é ruim… quem fez um bom
trabalho fica negativo"): agora é o SALDO DE RECUPERAÇÃO = recuperados do risco − entraram.
Positivo = recuperou mais do que perdeu (bom). A informação é a mesma; só o sinal mudou.

Decisão confirmada por ele: saldo = clientes que ENTRARAM em risco no mês − RECUPERADOS do risco no
mês (e o mesmo em R$/mês). Positivo = a carteira piorou (diminuindo); negativo = avançando.
Mesmos critérios da ponte da empresa (`ponte_de`): resgatado de PERDIDO não é "recuperado do risco"
e "virou perdido" não entra no saldo (sai do risco por motivo ruim — já tem coluna própria).
"""
from pathlib import Path

import pytest

import recuperacao as recup
from recuperacao import ATIVO, RISCO, PERDIDO


def _m(ini=ATIVO, fim=ATIVO, entrou=False, recuperado=False, de=None, vfim=0.0, vrec=0.0, vendas=None, perdido=False):
    return {'ini': ini, 'fim': fim, 'valor_ini': 0.0, 'valor_fim': vfim, 'entrou': entrou, 'recuperado': recuperado,
            'recuperado_de': de, 'dias_parado': None, 'valor_mensal_rec': vrec, 'vendas': vendas or {},
            'virou_perdido': perdido}


MOVS = {
    1: _m(fim=RISCO, entrou=True, vfim=100.0),                                  # entrou (dono 29)
    2: _m(fim=RISCO, entrou=True, vfim=50.0),                                   # entrou (dono 29)
    3: _m(ini=RISCO, recuperado=True, de=RISCO, vrec=70.0, vendas={29: 500.0}),  # recuperado do risco (dono 29)
    4: _m(ini=PERDIDO, recuperado=True, de=PERDIDO, vrec=10.0, vendas={29: 90.0}),  # resgatado de perdido: fora
    5: _m(ini=RISCO, fim=PERDIDO, perdido=True),                                 # virou perdido: fora
    6: _m(fim=RISCO, entrou=True, vfim=40.0),                                   # entrou (dono 30, outro time)
    7: _m(ini=RISCO, recuperado=True, de=RISCO, vrec=25.0, vendas={29: 300.0}),  # recuperado do risco (dono 30) por 29
}
DONO = {1: 29, 2: 29, 3: 29, 4: 29, 5: 29, 6: 30, 7: 30}
TIME = {29: 17, 30: 19}


def test_saldo_por_vendedor_e_da_carteira_do_dono():
    pl = recup.placar_de(MOVS, DONO, TIME)
    a, b = pl['rcas'][29], pl['rcas'][30]
    assert (a['entraram'], a['rec_risco_da_base'], a['saldo']) == (2, 1, -1)         # 1 recuperado − 2 entraram
    assert a['valor_saldo'] == pytest.approx(70.0 - 150.0)
    assert (b['entraram'], b['rec_risco_da_base'], b['saldo']) == (1, 1, 0)
    assert b['valor_saldo'] == pytest.approx(25.0 - 40.0)
    # a recuperação do cliente 7 conta na BASE do dono (30), não em quem vendeu (29) — é a carteira dele
    t = pl['times']
    assert (t[17]['saldo'], t[19]['saldo']) == (-1, 0)


def test_soma_dos_saldos_fecha_com_a_ponte_da_empresa():
    pl = recup.placar_de(MOVS, DONO, TIME)
    p = recup.ponte_de(MOVS, 202608)
    saldo_emp = p['clientes']['recuperados'] - p['clientes']['entraram']
    assert sum(r['saldo'] for r in pl['rcas'].values()) == saldo_emp == -1
    assert sum(r['saldo'] for r in pl['times'].values()) == saldo_emp
    assert sum(r['valor_saldo'] for r in pl['rcas'].values()) == pytest.approx(
        p['valor_mensal']['recuperados'] - p['valor_mensal']['entraram'])


def test_rota_devolve_card_e_coluna_de_saldo(client, usuario_admin, monkeypatch):
    import server
    from tests.conftest import login_as
    server._R.flushall()
    monkeypatch.setitem(server.CONFIG, 'data_source', 'postgres')
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    j = client.get('/api/recuperacao').get_json()
    p = j['ponte']
    assert j['cards']['saldo'] == p['clientes']['recuperados'] - p['clientes']['entraram']
    assert j['cards']['saldo_valor'] == pytest.approx(p['valor_mensal']['recuperados'] - p['valor_mensal']['entraram'], abs=0.02)
    assert all('saldo' in t and 'valor_saldo' in t for t in j['times'])
    assert all('saldo' in r for r in j['rcas'])
    assert sum(t['saldo'] for t in j['times']) == j['cards']['saldo']
    sem = [i for i, t in enumerate(j['times']) if t['codsupervisor'] is None]
    assert sem in ([], [len(j['times']) - 1])                  # 'Sem time' sempre por último


def test_tela_mostra_o_saldo():
    html = Path('recuperacao.html').read_text(encoding='utf-8')
    assert 'Saldo de recuperação' in html and 'saldo_valor' in html     # 06/10: positivo = bom
    assert html.count('>Saldo de recuperação<') >= 2              # coluna nas tabelas de time e de vendedor


def test_versao_do_cache_sobe_quando_a_resposta_muda():
    """Produção, 29/09/2026: depois do deploy a Recuperação mostrou "Saldo do mês 0" — o Redis serviu a
    resposta ANTIGA (sem o campo) com a mesma chave. A chave tem de mudar junto com o conteúdo."""
    fonte = Path('server.py').read_text(encoding='utf-8')
    assert "'recuperacao:resumo:v4'" in fonte and "'recuperacao:resumo:v3'" not in fonte
    assert "'vendedores:ranking:v3'" in fonte and "vendedor:full:v3:" in fonte
    assert "classList.toggle('hidden', c.saldo == null)" in Path('recuperacao.html').read_text(encoding='utf-8')

