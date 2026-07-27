"""Gate Fase 2 — telas de Metas no modo DATA_SOURCE=postgres.
Realizado vem de PEDIDOS (pcpedc) + faturamento (lucro/mix), lido do joga_demo — não do dataset META.
"""
import server
from tests.conftest import login_as


def _postgres(monkeypatch):
    server._R.flushall()
    monkeypatch.setitem(server.CONFIG, 'data_source', 'postgres')


def _periodo():
    h = server.provider_sql.hoje_analitico()   # 'hoje' ancorado no dado da demo
    return h.year, h.month


def test_metas_painel_modo_postgres(client, usuario_admin, monkeypatch):
    _postgres(monkeypatch)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    ano, mes = _periodo()
    resp = client.get(f'/api/metas?ano={ano}&mes={mes}')

    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True
    assert {'ano', 'mes', 'dias', 'supervisores', 'total'} <= set(data)
    assert {'mes', 'decorridos', 'restantes'} <= set(data['dias'])
    assert data['dias']['mes'] > 0

    tot = data['total']
    assert set(tot) >= {'venda', 'rentabilidade', 'clientes', 'mix', 'margem'}
    # cada métrica é uma linha_metrica completa
    assert {'meta', 'realizado', 'projecao', 'pct_realizado'} <= set(tot['venda'])
    # realizado coerente (base cheia da demo)
    assert tot['venda']['realizado'] > 100_000
    assert tot['clientes']['realizado'] > 0
    assert tot['mix']['realizado'] > 0
    assert tot['rentabilidade']['realizado'] > 0
    # projeção run-rate = realizado × dias/decorridos ≥ realizado
    assert tot['venda']['projecao'] >= tot['venda']['realizado']
    # há supervisores no painel (mesmo sem meta cadastrada: fallback mostra o realizado)
    assert len(data['supervisores']) > 0


def test_metas_vendedores_e_serie(client, usuario_admin, monkeypatch):
    _postgres(monkeypatch)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    ano, mes = _periodo()
    base = f'ano={ano}&mes={mes}'

    painel = client.get(f'/api/metas?{base}').get_json()
    codsup = painel['supervisores'][0]['codsupervisor']

    vd = client.get(f'/api/metas/vendedores?{base}&codsupervisor={codsup}').get_json()
    assert vd['ok'] is True and vd['codsupervisor'] == codsup
    assert len(vd['vendedores']) > 0
    v = vd['vendedores'][0]
    assert {'codusur', 'nome', 'venda', 'rentabilidade', 'clientes', 'mix'} <= set(v)
    assert {'meta', 'realizado', 'projecao'} <= set(v['venda'])

    serie = client.get(f'/api/metas/serie?{base}').get_json()
    assert serie['ok'] is True
    assert len(serie['serie']) > 0
    assert {'data', 'venda'} <= set(serie['serie'][0])
    # a soma da série bate a ordem de grandeza do realizado total (venda de pedidos F/L/B)
    assert sum(p['venda'] for p in serie['serie']) > 100_000


def test_metas_admin_sugestao_modo_postgres(client, usuario_admin, monkeypatch):
    """Fase 4: sugestão de meta do Admin lê o histórico do joga_demo (não do RCA via DAX)."""
    _postgres(monkeypatch)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    ano, mes = _periodo()
    r = client.get(f'/api/admin/metas/sugestao?codusur=213&ano={ano}&mes={mes}'
                   f'&metodo=media_3m&crescimento=0.10').get_json()
    assert r['ok'] is True and r['codusur'] == 213
    s = r['sugestao']
    assert {'valor_meta', 'rentabilidade_meta', 'clientes_meta', 'mix_meta'} <= set(s)
    assert (s['valor_meta'] or 0) > 0          # histórico real → sugestão positiva


def test_metas_default_powerbi_intocado():
    """Garantia: o default segue powerbi (modo BD não liga sozinho)."""
    assert server.CONFIG['data_source'] == 'powerbi'
