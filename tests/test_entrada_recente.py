"""Gate do "Entrou nos últimos X dias" da aba Cobertura (João Victor, 10/11/2026).

Pedido: "preciso saber todos os itens que chegaram ontem" para avisar o time de vendas, e os
70283/70284 — que chegaram em 06/11 e saíram em bonificação no dia 07/11 — não apareciam em tela
nenhuma (estoque zero e sem venda: o corte de atividade do snapshot os descartava).
"""
import pytest

from estoque import core, routes


# ─────────── quantidade da última entrada: só as filiais da data mais recente ───────────
def test_quantidade_soma_so_as_filiais_da_entrada_mais_recente():
    linhas = [{"CODPROD": 70284, "CODFILIAL": "3", "qtultent": 10, "dtultent": "2026-11-06"},
              {"CODPROD": 70284, "CODFILIAL": "8", "qtultent": 500, "dtultent": "2026-08-01"},
              {"CODPROD": 1, "CODFILIAL": "3", "qtultent": 12, "dtultent": "2026-11-09"},
              {"CODPROD": 1, "CODFILIAL": "5", "qtultent": 24, "dtultent": "2026-11-09"}]
    m = core.ultima_entrada_por_produto(linhas)
    assert m[70284] == {"dtultent": "2026-11-06", "qtultent": 10}, \
        "a NF de agosto da outra filial não é a última entrada"
    assert m[1] == {"dtultent": "2026-11-09", "qtultent": 36}, "mesma data em 2 filiais soma"


def test_linha_sem_data_e_ignorada():
    assert core.ultima_entrada_por_produto([{"CODPROD": 5, "qtultent": 3, "dtultent": None}]) == {}
    assert core.ultima_entrada_por_produto(None) == {}


# ─────────── o filtro chega ao export ───────────
@pytest.fixture
def app_ctx():
    import server  # noqa: F401
    from server import app
    return app


def _p(cod, dias):
    return {"codprod": cod, "descricao": f"P{cod}", "fornecedor": "F", "codfornec": 1,
            "dias_sem_entrada": dias}


def test_filtro_entrada_dias_chega_ao_export(app_ctx):
    prods = [_p(1, 0), _p(2, 1), _p(3, 5), _p(4, 6), _p(5, None)]
    with app_ctx.test_request_context("/estoque/api/export/ruptura.csv?entrada_dias=5"):
        assert {p["codprod"] for p in routes._aplicar_filtros_cliente(prods)} == {1, 2, 3}
    with app_ctx.test_request_context("/estoque/api/export/ruptura.csv?entrada_dias=1"):
        assert {p["codprod"] for p in routes._aplicar_filtros_cliente(prods)} == {1, 2}, \
            "'ontem' = 1 dia: hoje e ontem"
    with app_ctx.test_request_context("/estoque/api/export/ruptura.csv"):
        assert len(routes._aplicar_filtros_cliente(prods)) == 5, "sem o parâmetro, nada recorta"


def test_colunas_de_entrada_no_export_da_cobertura():
    assert {"dtultent", "qtultent", "qt_transicao"} <= set(routes._CSV_COLS["ruptura"])
    assert {"dtultent", "qtultent", "qt_transicao"} <= {c[0] for c in routes._PDF_COLS["ruptura"]}


# ─────────── o que chegou e já saiu NÃO entra na lista principal ───────────
def test_chegou_e_saiu_fica_fora_da_lista_principal(app_ctx, monkeypatch):
    """Estoque zero e sem giro: na lista principal cairia no 121+ como "estoque morto" e no
    Estoque zerado como "ruptura sem pedido". Só o filtro de entrada o mostra."""
    snap = [{"CODPROD": 10}]
    prod_map = {10: {}, 70283: {}, 70284: {}}
    entradas = {10: {}, 70283: {}, 70284: {}, 99999: {}}   # 99999 fora do cadastro de revenda
    pedidos = []
    monkeypatch.setattr(routes.pbi, "run_dax", lambda q: pedidos.append(q) or
                        [{"CODPROD": 70283}, {"CODPROD": 70284}])
    monkeypatch.setitem(routes.pbi.CONFIG, "data_source", "powerbi")
    routes.pbi._CACHE.clear()
    extra = routes._chegou_e_saiu(snap, ["3"], prod_map, entradas)
    assert [r["CODPROD"] for r in extra] == [70283, 70284]
    assert "70283, 70284" in pedidos[0] and "99999" not in pedidos[0]
    with app_ctx.test_request_context("/"):
        assert routes.chegou_e_saiu() == [], "sem _build_produtos na request, lista vazia"
