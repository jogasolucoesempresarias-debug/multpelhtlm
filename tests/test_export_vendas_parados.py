"""Gate dos dois pedidos do João de 28/10/2026 na Gestão de Estoque.

1. **Total de caixas no rodapé do pedido** ("trazer aqui por favor o total de caixas compradas"):
   a soma do que a coluna "Caixas" mostra; item sem fator de caixa sai em unidades, à parte.
2. **Relatório enxuto para o time de vendas** ("esses itens parados do fornecedor, já enviamos direto
   para o time de vendas… pode sair apenas dias sem vendas e o total de estoque em caixa"). Dois
   botões, e cada um exporta o que a SUA tela lista:
     · `rever_compra` — o bloco "Rever antes de comprar" da Abastecimento (compra suspensa);
     · `parado_vendas` — a aba Parado, com a MESMA seleção do export "parado".
   Medido na GALVANOTEK: 24 no bloco × 38 parados; os 15 sem giro só saem pela aba Parado.
"""
from pathlib import Path

import pytest

from estoque import routes

JS = Path('static/estoque/estoque.js').read_text(encoding='utf-8')


def _p(cod, **kw):
    base = {"codprod": cod, "descricao": f"P{cod}", "fornecedor": "FORN", "codfornec": 1,
            "qtdisp": 240, "caixa": 24, "dias_sem_venda": 70, "compra_suspensa": False,
            "parado_faixa": None}
    base.update(kw)
    return base


@pytest.fixture
def app_ctx():
    import server  # noqa: F401 — registra o blueprint
    from server import app
    return app


def _export(app_ctx, monkeypatch, view, produtos, qs=""):
    monkeypatch.setattr(routes, "_build_produtos", lambda *a, **k: (produtos, {}, ["3", "5"]))
    with app_ctx.test_request_context(f"/estoque/api/export/{view}.xlsx?{qs}"):
        return routes._export_data(view)


# ─────────────────────── estoque em caixas ───────────────────────
def test_estoque_em_caixas_na_regua_da_coluna_disp_cx():
    """A coluna "Disp. cx" das abas é round(disponível ÷ caixa)."""
    assert routes._com_estoque_cx(_p(1, qtdisp=10800, caixa=24))["estoque_cx"] == 450
    assert routes._com_estoque_cx(_p(1, qtdisp=250, caixa=24))["estoque_cx"] == 10


def test_item_sem_fator_sai_em_unidades_e_nao_como_caixa():
    r = routes._com_estoque_cx(_p(1, qtdisp=37, caixa=1))
    assert r["estoque_cx"] is None and r["estoque_un_sem_fator"] == 37


# ─────────────────────── o que cada botão exporta ───────────────────────
def test_rever_compra_exporta_o_bloco_e_so_as_colunas_pedidas(app_ctx, monkeypatch):
    prods = [_p(1, compra_suspensa=True, dias_sem_venda=61), _p(2, compra_suspensa=True, dias_sem_venda=95),
             _p(3, compra_suspensa=False, parado_faixa="121+")]
    cols, linhas = _export(app_ctx, monkeypatch, "rever_compra", prods)
    assert [l["codprod"] for l in linhas] == [2, 1], "só a compra suspensa, mais dias sem venda primeiro"
    assert cols == ["codprod", "descricao", "fornecedor", "dias_sem_venda", "estoque_cx", "estoque_un_sem_fator"]


def test_parado_vendas_tem_a_mesma_selecao_do_export_parado(app_ctx, monkeypatch):
    prods = [_p(1, parado_faixa="121+"), _p(2, parado_faixa="61-90"), _p(3, parado_faixa=None),
             _p(4, parado_faixa="novo")]
    for qs in ("", "par_faixa=121%2B,61-90"):
        _, parado = _export(app_ctx, monkeypatch, "parado", prods, qs)
        _, vendas = _export(app_ctx, monkeypatch, "parado_vendas", prods, qs)
        assert {l["codprod"] for l in vendas} == {l["codprod"] for l in parado}, qs


def test_o_filtro_de_fornecedor_do_topo_chega_ao_export(app_ctx, monkeypatch):
    prods = [_p(1, compra_suspensa=True, codfornec=8579), _p(2, compra_suspensa=True, codfornec=113)]
    _, linhas = _export(app_ctx, monkeypatch, "rever_compra", prods, "fornec=8579")
    assert [l["codprod"] for l in linhas] == [1]


def test_pdf_sai(app_ctx, monkeypatch):
    _, linhas = _export(app_ctx, monkeypatch, "rever_compra",
                        [_p(1, compra_suspensa=True), _p(2, compra_suspensa=True, caixa=1)])
    assert routes._gerar_pdf("rever_compra", linhas)[:4] == b"%PDF"


# ─────────────────────── a tela ───────────────────────
def test_os_botoes_moram_nas_duas_telas():
    assert "${exportBtns('rever_compra')}" in JS
    assert "${exportBtns('parado_vendas')}" in JS


def test_o_bloco_avisa_quando_corta_em_100():
    """Antes o cabeçalho dizia 279 e a tabela mostrava 100, sem aviso."""
    assert "Mostrando 100 de ${int(suspensos.length)}" in JS


def test_rodape_do_pedido_soma_as_caixas_e_separa_o_item_sem_fator():
    corpo = JS[JS.index('const caixas=()=>'):JS.index('const rodape=()=>')]
    assert 'Math.ceil((+x.qtd||0)/x.qtunitcx)' in corpo, "a mesma conta da coluna Caixas"
    assert 'un sem fator' in corpo
    assert '+ caixas() + carga()' in JS
