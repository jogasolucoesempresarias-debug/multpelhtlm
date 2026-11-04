"""Gate do pedido do João de 30/10/2026: zerados que JÁ FORAM COMPRADOS, para o time de vendas.

"Informar o time de vendas dos itens que estão zerados, porém já foram comprados, e a previsão de
entrega… pois eles ficam perguntando nos grupos." Botão `zerado_vendas` na aba Estoque zerado:
Cód · Produto · Fornecedor · Comprado (cx) · Previsão · Situação.

O que este gate trava:
  · a `DTPREVENT` só vale com mais de 1 dia após a emissão — o Winthor preenche emissão+1
    sozinho, e esse "chega amanhã" iria do PDF para o cliente;
  · previsão vencida sai como ATRASO, não como data velha;
  · o universo é o do "Já ped." (`montar_ja_pedida`): todo item com pedido tem previsão, e só ele;
  · item recebido em pré-entrada entra (já está no armazém, em conferência).
"""
from datetime import date
from pathlib import Path

import pytest

from estoque import core, routes

JS = Path('static/estoque/estoque.js').read_text(encoding='utf-8')
HOJE = date(2026, 10, 30)
FORN = {113: {"PRAZOENTREGA": 15}, 200: {"PRAZOENTREGA": 0}}


def _cab(numped, emissao, prevent=None, fornec=113):
    return {"NUMPED": numped, "DTEMISSAO": emissao, "DTPREVENT": prevent, "CODFORNEC": fornec}


def _it(numped, cod, qtped, qtentregue=0):
    return {"NUMPED": numped, "CODPROD": cod, "qtped": qtped, "qtentregue": qtentregue}


# ─────────────────────── a previsão de UM pedido ───────────────────────
def test_promessa_real_do_winthor_e_usada():
    dt, est = core.previsao_entrega(_cab(1, "2026-10-20", "2026-11-05"), FORN[113])
    assert (dt, est) == (date(2026, 11, 5), False)


def test_emissao_mais_1_automatica_NAO_e_promessa():
    """O caso que motivou a regra: o Winthor carimba emissão+1 em pedido que leva semanas."""
    dt, est = core.previsao_entrega(_cab(1, "2026-10-20", "2026-10-21"), FORN[113])
    assert (dt, est) == (date(2026, 11, 4), True), "emissão + PRAZOENTREGA (15d), marcada estimada"


def test_sem_prazo_do_fornecedor_cai_no_padrao():
    dt, est = core.previsao_entrega(_cab(1, "2026-10-20", None, fornec=200), FORN[200], lead_padrao=10)
    assert (dt, est) == (date(2026, 10, 30), True)


# ─────────────────────── por produto ───────────────────────
def test_o_universo_e_o_mesmo_do_ja_pedido():
    cab = [_cab(1, "2026-10-20", "2026-11-05"), _cab(2, "2026-10-25"),
           _cab(3, "2026-01-02", "2026-02-01")]                  # fora da janela de 180 dias
    itens = [_it(1, 10, 24), _it(2, 11, 12, 12),                  # 11 já entregue inteiro
             _it(2, 12, 48, 24), _it(3, 13, 10)]
    ja = core.montar_ja_pedida(cab, itens, hoje=HOJE)
    ch = core.chegadas_por_produto(cab, itens, FORN, hoje=HOJE)
    assert set(ch) == set(ja) == {10, 12}


def test_vale_a_chegada_mais_proxima_e_conta_os_pedidos():
    cab = [_cab(1, "2026-10-20", "2026-11-20"), _cab(2, "2026-10-25", "2026-11-03")]
    ch = core.chegadas_por_produto(cab, [_it(1, 10, 24), _it(2, 10, 24)], FORN, hoje=HOJE)[10]
    assert ch["previsao_entrega"] == "2026-11-03" and ch["pedidos_abertos"] == 2
    assert ch["previsao_estimada"] is False and ch["previsao_atraso"] is None


def test_previsao_vencida_vira_atraso():
    cab = [_cab(1, "2026-10-01", "2026-10-22")]
    ch = core.chegadas_por_produto(cab, [_it(1, 10, 24)], FORN, hoje=HOJE)[10]
    assert ch["previsao_atraso"] == 8


# ─────────────────────── export ───────────────────────
def _p(cod, **kw):
    base = {"codprod": cod, "descricao": f"P{cod}", "fornecedor": "FORN", "codfornec": 113,
            "qtdisp": 0, "caixa": 12, "qtd_ja_pedida": 0, "qt_transicao": 0,
            "status_exec": "pedido_cobre"}
    base.update(kw)
    return base


@pytest.fixture
def app_ctx():
    import server  # noqa: F401 — registra o blueprint
    from server import app
    return app


def _export(app_ctx, monkeypatch, produtos, qs=""):
    monkeypatch.setattr(routes, "_build_produtos", lambda *a, **k: (produtos, {}, ["3", "5"]))
    with app_ctx.test_request_context(f"/estoque/api/export/zerado_vendas.xlsx?{qs}"):
        return routes._export_data("zerado_vendas")


def test_so_zerado_ja_comprado_ou_recebido(app_ctx, monkeypatch):
    prods = [_p(1, qtd_ja_pedida=24, previsao_entrega="2026-11-05"),
             _p(2),                                               # zerado sem pedido
             _p(3, qtdisp=50, qtd_ja_pedida=24),                  # tem estoque
             _p(4, qt_transicao=36)]                              # chegou, em pré-entrada
    cols, linhas = _export(app_ctx, monkeypatch, prods)
    assert {l["codprod"] for l in linhas} == {1, 4}
    assert cols == ["codprod", "descricao", "fornecedor", "comprado_cx", "comprado_un_sem_fator",
                    "previsao_entrega", "situacao_entrega"]


def test_quantidade_em_caixas_e_situacao_escrita(app_ctx, monkeypatch):
    prods = [_p(1, qtd_ja_pedida=240, previsao_entrega="2026-10-22", previsao_atraso=8),
             _p(2, qtd_ja_pedida=24, previsao_entrega="2026-11-04", previsao_estimada=True,
                pedidos_abertos=2),
             _p(3, qt_transicao=36),
             _p(4, qtd_ja_pedida=7, caixa=1, previsao_entrega="2026-11-05")]
    _, linhas = _export(app_ctx, monkeypatch, prods)
    por = {l["codprod"]: l for l in linhas}
    assert por[1]["comprado_cx"] == 20 and por[1]["situacao_entrega"] == "atrasado 8 d"
    assert "estimada" in por[2]["situacao_entrega"] and "2 pedidos" in por[2]["situacao_entrega"]
    assert por[3]["situacao_entrega"] == "3 cx já chegaram, em conferência"
    assert por[4]["comprado_cx"] is None and por[4]["comprado_un_sem_fator"] == 7


def test_honra_o_filtro_de_status_e_o_do_topo(app_ctx, monkeypatch):
    prods = [_p(1, qtd_ja_pedida=24, codfornec=113), _p(2, qtd_ja_pedida=24, codfornec=8579),
             _p(3, qtd_ja_pedida=24, status_exec="rever_compra")]
    _, linhas = _export(app_ctx, monkeypatch, prods, "fornec=113&ez_status=pedido_cobre")
    assert [l["codprod"] for l in linhas] == [1]


def test_pdf_sai(app_ctx, monkeypatch):
    _, linhas = _export(app_ctx, monkeypatch, [_p(1, qtd_ja_pedida=24, previsao_entrega="2026-11-05"),
                                               _p(2, qt_transicao=12, caixa=1)])
    assert routes._gerar_pdf("zerado_vendas", linhas)[:4] == b"%PDF"


# ─────────────────────── a tela ───────────────────────
def test_botao_e_coluna_na_aba_estoque_zerado():
    corpo = JS[JS.index('function renderEstoqueZero('):JS.index('const QUAL_CHK=')]
    assert "${exportBtns('zerado_vendas')}" in corpo
    assert "key:'previsao_entrega'" in corpo
