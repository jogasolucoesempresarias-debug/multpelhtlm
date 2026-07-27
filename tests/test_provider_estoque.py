"""Gate Fase 3 Inc.1+2 — Cockpit/Cobertura + Abastecimento + Ruptura no modo DATA_SOURCE=postgres.
Lê o estoque do joga_demo (PCEST/endereço/cadastro/embalagem/pedido); core.py intacto. Inc.2 liga a
venda por produto (faturamento→venda/lucro/margem/ABC). Foco: contrato loader→core (giro/qtdisp/cobertura
não-nulos) + colunas de venda reais.
"""
import server  # noqa: F401 (registra o blueprint /estoque no app)
from estoque import pbi
from tests.conftest import login_as


def _postgres(monkeypatch):
    pbi._CACHE.clear()
    monkeypatch.setitem(pbi.CONFIG, 'data_source', 'postgres')


def _login_compras(client, u):
    """Login admin + concede a área 'compras' na sessão (o guard do blueprint exige tem_area)."""
    login_as(client, u['email'], u['senha'])
    with client.session_transaction() as sess:
        sess['areas'] = ['comercial', 'compras']


def test_estoque_filtros_modo_postgres(client, usuario_admin, monkeypatch):
    _postgres(monkeypatch)
    _login_compras(client, usuario_admin)
    r = client.get('/estoque/api/filtros').get_json()
    assert r['ok'] is True
    assert len(r['filiais']) > 0
    assert len(r['fornecedores']) > 0
    assert isinstance(r['compradores'], list)


def test_estoque_snapshot_cockpit_modo_postgres(client, usuario_admin, monkeypatch):
    _postgres(monkeypatch)
    _login_compras(client, usuario_admin)
    r = client.get('/estoque/api/snapshot').get_json()
    assert r['ok'] is True
    assert r['n'] > 1000                      # base cheia da demo (~3,8k produtos)
    assert r['bi_refresh'] is None            # modo BD: sem refresh do Power BI

    ck = r['cockpit']
    assert ck['valor_total'] > 100_000        # valor de estoque real (PCEST × custofin)
    assert ck['ruptura']['total'] > 0         # ruptura = qtdisp<=0 E giro>0
    ab = ck['abastecimento']
    assert (ab['urgente']['qt'] + ab['alta']['qt'] + ab['atencao']['qt'] + ab['excesso']['qt']) > 0
    # faixas de cobertura com spread (não degenerou tudo num bucket → giro chegou do PCEST)
    assert len([f for f in ck['faixas_cobertura'] if f['qt'] > 0]) >= 3

    # ── cross-check loader→core (o risco silencioso): alias errado zeraria isto ──
    com_giro = [p for p in r['produtos'] if (p.get('giro_mes') or 0) > 0 and (p.get('qtdisp') or 0) > 0]
    assert len(com_giro) > 100
    p = com_giro[0]
    assert p['cobertura_dias'] is not None and p['cobertura_dias'] > 0
    assert 'sugestao_cx' in p and 'status_ruptura' in p

    # ── Inc.2: colunas de venda reais (faturamento por produto) ──
    assert ck['venda_total'] > 100_000        # venda líquida do período (não mais 0)
    assert ck['lucro_total'] != 0
    # ABC não degenerou tudo em 'C' (curva é venda-based → precisa de venda)
    assert ck['abc']['A']['valor'] > 0
    assert any((p.get('venda') or 0) > 0 for p in r['produtos'])


def test_estoque_desempenho_modo_postgres(client, usuario_admin, monkeypatch):
    """Inc.3: aba Desempenho (receita por comprador) — venda líq/lucro/margem/positivação/YoY."""
    _postgres(monkeypatch)
    _login_compras(client, usuario_admin)
    r = client.get('/estoque/api/desempenho?venda_periodo=mes').get_json()
    assert r['ok'] is True
    comps = r['compradores']
    assert len(comps) > 0
    c = comps[0]
    assert {'comprador', 'venda_liquida', 'lucro_bruto', 'margem', 'clientes_pos',
            'part_lucro', 'status_lucro'} <= set(c)
    assert c['venda_liquida'] > 0 and c['clientes_pos'] > 0
    # ordenado por lucro desc
    assert [x['lucro_bruto'] for x in comps] == sorted((x['lucro_bruto'] for x in comps), reverse=True)


def test_estoque_inc4_telas_island_modo_postgres(client, usuario_admin, monkeypatch):
    """Inc.4: Validade/FEFO, Vencidos, Ocupação/WMS, Lead time, Verbas — island tables do joga_demo.
    Todas devem renderizar (ok=True) sem 500 e sem cair na rede de segurança (que zeraria)."""
    _postgres(monkeypatch)
    _login_compras(client, usuario_admin)

    # Validade/FEFO — lotes vencendo na janela
    v = client.get('/estoque/api/validade').get_json()
    assert v['ok'] is True and isinstance(v['lotes'], list) and len(v['lotes']) > 0
    assert v['resumo']['n'] == len(v['lotes'])

    # Vencidos — perda por validade (PCMOV)
    vc = client.get('/estoque/api/vencidos').get_json()
    assert vc['ok'] is True

    # Ocupação/WMS — KPIs de posições reais
    oc = client.get('/estoque/api/ocupacao').get_json()
    assert oc['ok'] is True
    kp = oc.get('kpis') or oc.get('resumo') or oc
    # nº de posições > 0 em algum lugar do payload (não degenerou)
    assert any(isinstance(x, (int, float)) and x > 0
               for x in (kp.values() if isinstance(kp, dict) else []))

    # Lead time por fornecedor (PCPEDIDO + PEDIDO_ENTRADA)
    lt = client.get('/estoque/api/leadtime').get_json()
    assert lt['ok'] is True

    # Verbas (PCVERBA + PCAPLICVERBA)
    vb = client.get('/estoque/api/verbas').get_json()
    assert vb['ok'] is True


def test_estoque_run_dax_bloqueado_em_postgres(monkeypatch):
    """Rede de segurança: em modo BD, qualquer query ao Power BI falha alto (não vaza dado real)."""
    import pytest
    monkeypatch.setitem(pbi.CONFIG, 'data_source', 'postgres')
    with pytest.raises(RuntimeError):
        pbi._execute('tok', 'EVALUATE ROW("x",1)')


def test_estoque_default_powerbi_intocado():
    """Garantia: o default segue powerbi (modo BD não liga sozinho)."""
    assert pbi.CONFIG['data_source'] == 'powerbi'
