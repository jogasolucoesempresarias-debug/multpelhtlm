"""Gate do "pedido válido / em aberto pelos ITENS" (10/2026, pedido 565975 "garrado" no Orçamento).

O Winthor deixa cabeçalho sem item no banco (conferido no Oracle: MULTPEL.PCPEDIDO 565975 e
564011, 0 linhas em PCITEM) e as telas dele o escondem. A regra que estes testes travam é a do
próprio Winthor — o pendente da PCEST é Σ(QTPEDIDA − QTENTREGUE) dos itens (744/747 batendo):

  · cabeçalho sem item NÃO é pedido: sai da lista, do comprado e do a entregar;
  · em aberto = algum item com saldo; o VALOR segue o cabeçalho (régua da NF);
  · NÃO é a "solução de zero": pedido com valor e sem item também sai, e pedido de valor zero
    com item em aberto continua;
  · sem itens, ou com a lista no teto do executeQueries (100 mil, corte CALADO), o cabeçalho
    passa intacto — validar contra lista truncada tiraria pedido de verdade em silêncio.
"""
from datetime import date

from estoque import core

HOJE = date(2026, 10, 27)
MES = "2026-10"
COMP = {47: "JOÃO"}
FORN = {9778: {"FORNECEDOR": "SO FRUTA ALIMENTOS LTDA", "PRAZOENTREGA": 10}}


def _cab(numped, vlt, vle=0.0, dt="2026-10-05"):
    return {"NUMPED": numped, "DTEMISSAO": dt, "CODFORNEC": 9778, "CODCOMPRADOR": 47,
            "VLTOTAL": vlt, "VLENTREGUE": vle, "DTPREVENT": None, "CODFILIAL": "3"}


def _item(numped, qtped, qtent, cod=1):
    return {"NUMPED": numped, "CODPROD": cod, "qtped": qtped, "qtentregue": qtent}


def _orc(cab):
    return core.orcamento_winthor(cab, {"JOÃO": 100000.0}, COMP, FORN, MES, "TODOS",
                                  pct=0.65, hoje=HOJE)


def test_cabecalho_sem_item_nao_e_pedido():
    cab = [_cab(565975, 0.0, dt="2026-08-24"), _cab(565977, 26258.88, 26258.88, dt="2026-08-24")]
    v = core.validar_pedidos(cab, [_item(565977, 10, 10)])
    assert [int(r["NUMPED"]) for r in v] == [565977]


def test_nao_e_a_solucao_de_zero():
    """Órfão COM valor também sai (o 564011 tem R$ 24.666,90); valor zero COM item aberto fica."""
    cab = [_cab(564011, 24666.90, 24666.90), _cab(600001, 0.0)]
    v = core.validar_pedidos(cab, [_item(600001, 5, 0)])
    assert [(int(r["NUMPED"]), r["_itens_em_aberto"]) for r in v] == [(600001, True)]


def test_aberto_e_qualquer_item_com_saldo():
    cab = [_cab(1, 1000.0), _cab(2, 1000.0, 1000.0)]
    itens = [_item(1, 10, 10, cod=1), _item(1, 10, 4, cod=2), _item(2, 10, 10)]
    v = {int(r["NUMPED"]): r["_itens_em_aberto"] for r in core.validar_pedidos(cab, itens)}
    assert v == {1: True, 2: False}


def test_o_565975_sai_do_orcamento_inteiro():
    """Caso real: antes ele ficava "atrasado" com R$ 0,00 para sempre."""
    cab = [_cab(565975, 0.0), _cab(566183, 75081.67), _cab(566100, 5000.0, 5000.0)]
    itens = [_item(566183, 100, 0), _item(566100, 50, 50)]
    antes = _orc(cab)
    depois = _orc(core.validar_pedidos(cab, itens))
    assert 565975 in [p["numped"] for p in antes["abertos"]]
    assert [p["numped"] for p in depois["abertos"]] == [566183]
    assert depois["resumo"]["n_abertos"] == antes["resumo"]["n_abertos"] - 1
    # o valor a entregar e o comprado não mudam com o órfão de valor zero
    assert depois["resumo"]["valor_aberto"] == antes["resumo"]["valor_aberto"]
    assert 565975 not in [p["numped"] for p in depois["pedidos"]]


def test_orfao_com_valor_sai_do_comprado():
    cab = [_cab(564011, 24666.90, 24666.90), _cab(566183, 1000.0)]
    depois = _orc(core.validar_pedidos(cab, [_item(566183, 1, 0)]))
    antes = _orc(cab)
    assert round(antes["resumo"]["comprado"] - depois["resumo"]["comprado"], 2) == 24666.90


def test_valor_aberto_segue_o_cabecalho():
    """Os itens decidem SE está aberto; quanto falta é o VLTOTAL − VLENTREGUE (régua da NF)."""
    cab = [_cab(1, 1200.0, 200.0)]
    p = _orc(core.validar_pedidos(cab, [_item(1, 10, 2)]))["abertos"][0]
    assert p["valor_aberto"] == 1000.0


def test_itens_mandam_no_recebido():
    """Cabeçalho com resíduo, itens todos entregues → recebido (e o contrário)."""
    cab = [_cab(1, 1000.0, 900.0), _cab(2, 1000.0, 1000.0)]
    v = core.validar_pedidos(cab, [_item(1, 10, 10), _item(2, 10, 9)])
    o = _orc(v)
    assert [p["numped"] for p in o["abertos"]] == [2]


def test_sem_itens_ou_no_teto_o_cabecalho_passa_intacto():
    cab = [_cab(565975, 0.0), _cab(566183, 1000.0)]
    assert core.validar_pedidos(cab, []) is cab
    assert core.validar_pedidos(cab, None) is cab
    cheia = [_item(566183, 1, 0)] * 100000
    assert core.validar_pedidos(cab, cheia) is cab
    # e sem a marca o orcamento_winthor usa a régua antiga do cabeçalho
    assert "_itens_em_aberto" not in _orc(cab)["abertos"][0]


def test_a_validacao_acontece_uma_vez_no_pedidos_data():
    """Os 5 chamadores do orcamento_winthor leem `_pedidos_data()["cab"]` — validar lá é o que
    impede um deles de ficar com o órfão (o defeito de dois universos)."""
    from pathlib import Path
    src = Path("estoque/routes.py").read_text(encoding="utf-8")
    corpo = src.split("def _pedidos_data(", 1)[1].split("\ndef ", 1)[0]
    assert "cab = core.validar_pedidos(cab, itens)" in corpo
    assert 'peddata:v2:' in corpo
