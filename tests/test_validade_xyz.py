"""Gate da coluna XYZ na aba Validade/FEFO (19/08/2026, pedido do diretor).

> "Consegue colocar na coluna a classificação XYZ, pois estamos colocando um controle de risco
> de validade aqui, e trazer essa informação ali, iria ser muito útil."

Por que ela pertence a esta tela: a aba projeta `saldo_proj` e `valor_risco` a partir do giro
MÉDIO. Num item **Z** (demanda errática) essa média é justamente o número menos confiável —
a coluna é o qualificador da própria estimativa que a tela já mostra. Item X com 30 dias de
validade é administrável; item Z com os mesmos 30 dias não se projeta.

⚠️ O XYZ é do PRODUTO e a linha é do LOTE. Ele viaja pelo mesmo caminho do ABC (o índice de
produtos que o `validade_fefo` já recebe), então herda o mesmo comportamento: vem `None` para
produto sem série de 3 meses ou fora do snapshot da unidade, e a tela mostra "—".
"""
from datetime import date

from estoque import core


HOJE = date(2026, 8, 19)


def _lote(cod=1, qt=10.0, dtval="2026-09-10"):
    return {"CODPROD": cod, "qt": qt, "DTVAL": dtval, "NUMLOTE": "L1", "DESCRICAO": "ITEM"}


def _prod(cod=1, xyz="Z", curva="A"):
    return {cod: {"codprod": cod, "descricao": "ITEM", "xyz": xyz, "curva_abc": curva,
                  "giro_dia": 1.0, "custo_unit": 10.0, "fornecedor": "F", "comprador": "C"}}


def _linhas(idx):
    return core.validade_fefo([_lote()], idx, core.merge_params({"horizonte_val": 600}), hoje=HOJE)


def test_o_lote_carrega_o_xyz_do_produto():
    l = _linhas(_prod(xyz="Z"))[0]
    assert l["xyz"] == "Z"
    assert l["curva_abc"] == "A", "o ABC continua vindo — a coluna nova não substitui nada"


def test_produto_sem_xyz_nao_inventa_classificacao():
    """Sem série de 3 meses não há coeficiente de variação. A célula fica vazia (a tela mostra
    "—"), nunca um 'X' de conveniência — mesma política do ABC nesta tabela."""
    assert _linhas(_prod(xyz=None))[0]["xyz"] is None


def test_produto_fora_do_indice_nao_quebra():
    """Lote de item que não está no snapshot da unidade: ABC e XYZ vêm vazios, a linha existe.
    É o comportamento que o ABC já tinha — a coluna nova não pode mudá-lo."""
    l = _linhas({})[0]
    assert l["xyz"] is None and l["curva_abc"] is None
    assert l["codprod"] == 1, "a linha do lote continua saindo"


# ───────────────── a coluna tem de existir nos TRÊS lugares ─────────────────
# Filtro/coluna que fica só na tela e não viaja no export é a cicatriz mais repetida do módulo.

def test_a_coluna_aparece_na_tela():
    from pathlib import Path
    js = Path("static/estoque/estoque.js").read_text(encoding="utf-8")
    ini = js.index("function renderValidade(")
    bloco = js[ini:js.index("\nfunction ", ini + 10)]
    assert "{key:'xyz',label:'XYZ',badge:true}" in bloco
    assert bloco.index("curva_abc") < bloco.index("key:'xyz'"), "XYZ vem depois do ABC"


def test_a_coluna_viaja_no_excel_e_no_pdf():
    from pathlib import Path
    src = Path("estoque/routes.py").read_text(encoding="utf-8")
    assert '"curva_abc", "xyz",' in src, "CSV/XLSX da validade precisa levar o XYZ"
    assert '("xyz", "XYZ", "text")' in src, "PDF da validade precisa levar o XYZ"
