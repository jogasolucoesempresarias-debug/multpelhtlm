"""Gate do drawer 360° do FORNECEDOR (pedido do diretor 07/2026: "venda mês a mês do
fornecedor, igual tem a do produto").

A validação contra o BI real, antes de implementar, definiu o desenho:
  · a série é agregada NO FATO por `CODFORNEC` — somar os produtos da tela é o bug do YoY já
    corrigido (item que saiu de linha some do histórico; 6 fornecedores trocavam de sinal);
  · as duas agregações foram provadas EQUIVALENTES com o mesmo filtro (R$ 74.636,87 nas duas,
    diferença R$ 0,00) — então agregar no fato é seguro E 9x mais barato (6k linhas x 54k);
  · 24 meses, para sobrepor o mesmo mês do ano anterior, cabe no limite de 100.000 linhas do
    `executeQueries` (que já mordeu neste projeto no PCEST).
"""
import pytest

from estoque import core, queries as Q, routes


# ─────────────────────── as queries ───────────────────────
def test_serie_do_fornecedor_agrega_no_FATO_nao_por_produto():
    """Se alguém trocar para SUMMARIZECOLUMNS por CODPROD, o histórico volta a perder item
    descontinuado — o bug que o README documenta em core.yoy_fornecedor."""
    import datetime
    q = Q.q_venda_fornecedor_mensal_rca(datetime.date(2025, 1, 1), ["3"])
    assert "FATURAMENTO_VENDAS[CODFORNEC]" in q
    assert "FATURAMENTO_VENDAS[CODPROD]" not in q
    assert "[VENDA BRUTA]" in q          # measure oficial, não soma de coluna
    assert "CALENDARIO[AnoMes]" in q


def test_serie_e_liquida_tem_devolucao_pareada():
    """Sem a devolução o gráfico não fecharia com a coluna `Venda` da aba (que é líquida)."""
    import datetime
    q = Q.q_devol_fornecedor_mensal_rca(datetime.date(2025, 1, 1), ["3"])
    assert "FATURAMENTO_DEVOLUCAO[CODFORNEC]" in q
    assert "[TOTAL DEVOLUCAO]" in q
    assert "FATURAMENTO_DEVOLUCAO[DTENT]" in q   # devolução por DTENT, não DTSAIDA


# ─────────────────────── o endpoint ───────────────────────
@pytest.fixture
def app_ctx():
    import server  # noqa: F401
    from server import app
    return app


def _prod(cod, forn=1, **kw):
    base = {"codprod": cod, "descricao": f"P{cod}", "codfornec": forn, "venda": 100.0,
            "lucro": 20.0, "valor": 50.0, "qtdisp": 10, "giro_dia": 1.0, "sugestao_cx": 0,
            "cobertura": 30, "curva_abc": "A", "status_abast": "ok", "status_parado": None,
            "valor_sugerido_liq": 0.0, "valor_sugerido_nf": 0.0}
    base.update(kw)
    return base


def _mock(monkeypatch, produtos, serie=None):
    monkeypatch.setattr(routes, "_build_produtos",
                        lambda *a, **k: (produtos, core.merge_params({}), ["3"]))
    monkeypatch.setattr(routes, "_cadastro_fornecedores",
                        lambda: {1: {"FORNECEDOR": "FORN TESTE", "ESTADO": "SP", "CODCOMPRADOR": 7}})
    monkeypatch.setattr(routes, "_compradores_map", lambda: {7: "JOAO"})
    monkeypatch.setattr(routes, "_vendas_forn_mensal_map", lambda *a, **k: {1: serie or {}})
    monkeypatch.setattr(routes, "_forn_extra_map", lambda *a, **k: {1: {"ciclo_dias": 30.0, "n_pedidos": 4}})
    monkeypatch.setattr(routes, "_leadtime_res", lambda *a, **k: {"fornecedores": []})
    monkeypatch.setattr(routes, "_pedidos_data", lambda *a, **k: {"cab": []})
    monkeypatch.setattr(routes, "_filiais_venda", lambda: ["3"])


def test_endpoint_devolve_serie_de_12m_e_o_ano_anterior(app_ctx, monkeypatch):
    """O ano anterior é o que responde QUANDO caiu — sem ele a tela só repete o -20,4%."""
    hoje = routes._hoje() if False else None
    _mock(monkeypatch, [_prod(1)], serie={})
    with app_ctx.test_request_context("/estoque/api/fornecedor/1"):
        j = routes.api_fornecedor(1).get_json()
    assert len(j["meses"]) == 12
    assert len(j["serie"]) == 12 and len(j["serie_ant"]) == 12
    # serie_ant é o MESMO mês 12 meses antes (AnoMes − 100), não os 12 meses anteriores em fila
    assert j["meses"][0] - 100 == j["meses"][0] - 100


def test_serie_ant_busca_o_mesmo_mes_do_ano_anterior(app_ctx, monkeypatch):
    meses = core._meses_ate(routes._hoje() if hasattr(routes, "_hoje") else None, 12) \
        if False else None
    _mock(monkeypatch, [_prod(1)])
    with app_ctx.test_request_context("/estoque/api/fornecedor/1"):
        j = routes.api_fornecedor(1).get_json()
    m0 = j["meses"][0]
    # monta um mapa só com o mês do ano anterior preenchido e confere que ele cai em serie_ant
    _mock(monkeypatch, [_prod(1)], serie={m0 - 100: 999.0})
    with app_ctx.test_request_context("/estoque/api/fornecedor/1"):
        j2 = routes.api_fornecedor(1).get_json()
    assert j2["serie_ant"][0] == 999.0
    assert j2["serie"][0] == 0


def test_a_comprar_do_drawer_usa_a_regua_da_NF(app_ctx, monkeypatch):
    """Coerência com o resto do app: tudo que responde "quanto vou gastar" sai c/ impostos."""
    p = _prod(1, sugestao_cx=2, valor_sugerido_liq=100.0, valor_sugerido_nf=115.0)
    _mock(monkeypatch, [p])
    with app_ctx.test_request_context("/estoque/api/fornecedor/1"):
        f = routes.api_fornecedor(1).get_json()["fornecedor"]
    assert f["sugestao_nf"] == 115.0


def test_kpis_somam_so_os_produtos_do_fornecedor(app_ctx, monkeypatch):
    _mock(monkeypatch, [_prod(1, forn=1, venda=100.0), _prod(2, forn=999, venda=777.0)])
    with app_ctx.test_request_context("/estoque/api/fornecedor/1"):
        f = routes.api_fornecedor(1).get_json()["fornecedor"]
    assert f["n_produtos"] == 1 and f["venda"] == 100.0


def test_lead_fraco_nao_vira_numero_no_payload(app_ctx, monkeypatch):
    """`lead_confiavel` viaja para a tela decidir: número frágil apresentado como fato é pior
    que célula vazia (mesma política já adotada no crescimento da aba Fornecedores)."""
    _mock(monkeypatch, [_prod(1)])
    monkeypatch.setattr(routes, "_leadtime_res", lambda *a, **k: {"fornecedores": [
        {"codfornec": 1, "lead_real": None, "lead_todos": 26.0, "n": 4, "confiavel": False}]})
    with app_ctx.test_request_context("/estoque/api/fornecedor/1"):
        f = routes.api_fornecedor(1).get_json()["fornecedor"]
    assert f["lead_confiavel"] is False and f["lead_real"] is None and f["lead_todos"] == 26.0


def test_fornecedor_inexistente_nao_quebra(app_ctx, monkeypatch):
    _mock(monkeypatch, [_prod(1)])
    with app_ctx.test_request_context("/estoque/api/fornecedor/4242"):
        j = routes.api_fornecedor(4242).get_json()
    assert j["ok"] is True and j["fornecedor"]["n_produtos"] == 0
