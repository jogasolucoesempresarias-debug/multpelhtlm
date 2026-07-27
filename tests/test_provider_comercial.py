"""Gate Fase 2 — telas do Comercial no modo DATA_SOURCE=postgres (lê do joga_demo)."""
import pytest
import server
import rfm
from tests.conftest import login_as


def _postgres(monkeypatch):
    server._R.flushall()
    monkeypatch.setitem(server.CONFIG, 'data_source', 'postgres')


def test_carteira_rfm_modo_postgres(client, usuario_admin, monkeypatch):
    _postgres(monkeypatch)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    resp = client.get('/api/carteira/rfm')

    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True
    # os 8 segmentos canônicos presentes
    assert set(data['segmentos']) == set(rfm.SEGMENTOS_ORDEM)
    assert data['total_clientes'] > 1000            # base cheia da demo
    assert sum(data['segmentos'].values()) == data['total_clientes']
    # emergiram vários segmentos (não degenerou tudo num só)
    assert sum(1 for v in data['segmentos'].values() if v > 0) >= 5
    # matriz R×F e histograma populados
    assert len(data['matriz_rf']) > 0
    assert sum(b['count'] for b in data['histograma_recencia']) == data['total_clientes']


def test_vendedores_modo_postgres(client, usuario_admin, monkeypatch):
    _postgres(monkeypatch)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    # a demo gera vendedores com tipovend='R' (Rota externo, = default da tela)
    resp = client.get('/api/vendedores?tipovend=R')

    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True
    assert data['total'] > 0
    v = data['vendedores'][0]
    assert set(v) >= {'codusur', 'nome', 'venda_liq', 'lucro', 'ticket_medio',
                      'taxa_positivacao', 'rank', 'yoy_receita', 'carteira_oficial'}
    assert v['nome'] and v['rank'] == 1
    # ranking ordenado por lucro desc
    lucros = [x['lucro'] for x in data['vendedores']]
    assert lucros == sorted(lucros, reverse=True)


def test_categorias_modo_postgres(client, usuario_admin, monkeypatch):
    _postgres(monkeypatch)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    resp = client.get('/api/categorias')

    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True
    assert data['total_venda'] > 0
    cats = data['categorias']
    assert len(cats) > 0
    c = cats[0]
    assert set(c) >= {'codepto', 'nome', 'venda', 'lucro', 'margem', 'share',
                      'clientes_unicos', 'produtos_unicos'}
    # ordenado por venda desc, shares somam ~1, margem plausível
    assert cats == sorted(cats, key=lambda x: x['venda'], reverse=True)
    assert sum(x['share'] for x in cats) == pytest.approx(1.0, abs=0.01)
    assert 0 < c['margem'] < 0.6


def test_comercial_endpoints_sweep_modo_postgres(client, usuario_admin, monkeypatch):
    """Sweep dos endpoints comerciais que faltavam branch na Fase 2 (achados pela rede de segurança
    da raiz + fumaça no navegador). Todos devem responder 200/ok em modo BD — antes davam 500 via
    execute_dax (ou pior, vazariam dado real se o .env tivesse token válido)."""
    _postgres(monkeypatch)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    import provider_sql
    conn = provider_sql.analytics_conn()
    cur = conn.cursor()
    cur.execute("SELECT codcli FROM faturamento_vendas WHERE codoper='S' GROUP BY codcli ORDER BY count(*) DESC LIMIT 1")
    cc = cur.fetchone()[0]
    cur.execute("SELECT codprod FROM faturamento_vendas WHERE codoper='S' GROUP BY codprod ORDER BY count(*) DESC LIMIT 1")
    cp = cur.fetchone()[0]
    conn.close()
    urls = [
        '/api/carteira/receita-positivacao-12m', '/api/carteira/mes/202607', '/api/carteira/evolucao',
        f'/api/carteira/cliente/{cc}/produtos?limit=10', '/api/vendedor/213', '/api/vendedor/213/serie',
        '/api/categorias/2/clientes', '/api/marcas', '/api/fornecedores',
        f'/api/radar/produto/{cp}/cliente/{cc}/serie',
    ]
    for u in urls:
        r = client.get(u)
        assert r.status_code == 200, f'{u} -> HTTP {r.status_code}'
        assert (r.get_json() or {}).get('ok') is not False, f'{u} -> ok=False'


def test_carteira_cliente_drill_modo_postgres(client, usuario_admin, monkeypatch):
    """Drill 360° de cliente (histórico 12m + top deptos) — endpoint que faltava branchar na Fase 2."""
    _postgres(monkeypatch)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    import provider_sql
    conn = provider_sql.analytics_conn()
    cur = conn.cursor()
    cur.execute("SELECT codcli FROM faturamento_vendas WHERE codoper='S' "
                "GROUP BY codcli ORDER BY count(*) DESC LIMIT 1")
    codcli = cur.fetchone()[0]
    conn.close()

    r = client.get(f'/api/carteira/cliente/{codcli}').get_json()
    assert r['ok'] is True
    # histórico mensal populado (era o drawer vazio no smoke do navegador)
    assert len(r['historico']) > 0
    assert {'AnoMes', 'VendaLiquida', 'LucroTotal'} <= set(r['historico'][0])
    # top deptos com nome REAL (não "Depto N")
    assert len(r['deptos']) > 0
    assert not str(r['deptos'][0]['nome']).lower().startswith('depto')


def test_tendencias_cohort_modo_postgres(client, usuario_admin, monkeypatch):
    _postgres(monkeypatch)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    resp = client.get('/api/tendencias/cohort')

    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True
    cohorts = data['cohorts']
    assert len(cohorts) > 0
    row = cohorts[0]
    assert {'aquisicao', 'tamanho', 'retencao'} <= set(row)
    assert row['retencao'][0] == 1.0          # M+0 = 100%
    assert row['tamanho'] > 0
