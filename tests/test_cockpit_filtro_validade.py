"""Gate: os cards de VENCIMENTO do Cockpit respeitam os filtros do topo.

Defeito reportado 2x pelo diretor (07/2026): "quando eu filtro um fornecedor aqui, tudo
acompanha, MENOS os maiores ofensores — risco de vencimento".

Causa: `/api/validade` devolve o FEFO inteiro (não recebe filtro de cliente) e o Cockpit usava
`S.validade.lotes` e `S.validade.resumo` crus. Todo o resto da tela usa `filtered()`.

Como o recorte é no front, o gate aqui é sobre o CONTRATO que sustenta a correção:
  1. o endpoint devolve os lotes COMPLETOS (senão o front não pode recalcular com filtro);
  2. cada lote carrega `codprod` (chave do recorte) e `classificacao`/`valor_risco` (o resumo);
  3. o EXPORT aplica todos os filtros — é com ele que a tela tem de bater.
"""
import pytest

from estoque import core, routes


def _lote(cod, classificacao="critico", risco=100.0):
    return {"codprod": cod, "descricao": f"P{cod}", "classificacao": classificacao,
            "valor_risco": risco, "dias_para_vencer": 5, "risco": "normal"}


def test_contrato_do_lote_tem_o_que_o_recorte_precisa():
    """Se algum destes campos sumir, o Cockpit volta a mostrar vencimento sem filtro."""
    l = _lote(1)
    for campo in ("codprod", "classificacao", "valor_risco"):
        assert campo in l, f"o front recorta/soma por `{campo}`"


@pytest.fixture
def app_ctx():
    import server  # noqa: F401
    from server import app
    return app


def test_export_de_validade_recorta_por_fornecedor(app_ctx, monkeypatch):
    """A tela tem de bater com isto — é o export que define a verdade do recorte."""
    produtos = [{"codprod": 1, "codfornec": 113, "curva_abc": "A"},
                {"codprod": 2, "codfornec": 999, "curva_abc": "A"}]
    monkeypatch.setattr(routes, "_build_produtos",
                        lambda *a, **k: (produtos, core.merge_params({}), ["3"]))
    monkeypatch.setattr(routes, "_pg", lambda: False)
    monkeypatch.setattr(routes.pbi, "run_dax", lambda *a, **k: [])
    monkeypatch.setattr(core, "validade_fefo", lambda *a, **k: [_lote(1), _lote(2)])
    with app_ctx.test_request_context("/estoque/api/export/validade.xlsx?fornec=113"):
        _cols, linhas = routes._export_data("validade")
    assert [l["codprod"] for l in linhas] == [1], \
        "o export recorta por fornecedor — a tela do Cockpit precisa fazer o mesmo"


def test_export_de_validade_recorta_por_curva(app_ctx, monkeypatch):
    """O furo secundário: `lotesFiltrados()` aplicava só comprador/fornecedor/busca, mas o
    export aplica TODOS os filtros. Filtrar por Curva fazia tela e Excel divergirem."""
    produtos = [{"codprod": 1, "codfornec": 113, "curva_abc": "A"},
                {"codprod": 2, "codfornec": 113, "curva_abc": "C"}]
    monkeypatch.setattr(routes, "_build_produtos",
                        lambda *a, **k: (produtos, core.merge_params({}), ["3"]))
    monkeypatch.setattr(routes, "_pg", lambda: False)
    monkeypatch.setattr(routes.pbi, "run_dax", lambda *a, **k: [])
    monkeypatch.setattr(core, "validade_fefo", lambda *a, **k: [_lote(1), _lote(2)])
    with app_ctx.test_request_context("/estoque/api/export/validade.xlsx?curva=A"):
        _cols, linhas = routes._export_data("validade")
    assert [l["codprod"] for l in linhas] == [1]


def test_endpoint_devolve_os_lotes_completos(app_ctx, monkeypatch):
    """O front só consegue recalcular o resumo COM filtro porque recebe o FEFO inteiro.
    Se um dia o endpoint passar a truncar/paginar, o recorte do Cockpit quebra em silêncio."""
    monkeypatch.setattr(routes, "_build_produtos",
                        lambda *a, **k: ([{"codprod": 1}], core.merge_params({}), ["3"]))
    monkeypatch.setattr(routes, "_pg", lambda: False)
    monkeypatch.setattr(routes.pbi, "run_dax", lambda *a, **k: [])
    lotes = [_lote(i, "critico" if i % 2 else "atencao", 10.0 * i) for i in range(1, 8)]
    monkeypatch.setattr(core, "validade_fefo", lambda *a, **k: lotes)
    with app_ctx.test_request_context("/estoque/api/validade"):
        payload = routes.api_validade().get_json()
    assert len(payload["lotes"]) == len(lotes), "o Cockpit precisa do FEFO completo"
    # e o resumo do servidor é GLOBAL — por isso o front recalcula quando há filtro
    assert payload["resumo"]["critico"] == sum(1 for l in lotes if l["classificacao"] == "critico")
