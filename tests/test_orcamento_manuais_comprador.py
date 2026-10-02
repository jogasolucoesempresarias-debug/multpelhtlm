"""Gate: "Pedidos da nossa plataforma" seguem o comprador do FORNECEDOR (10/2026).

Achado pelo João Victor: os pedidos só apareciam com o filtro "Empresa toda"; filtrando o próprio
comprador, sumiam — "sendo que são meus fornecedores". O pedido gravava como comprador o que
estava no FILTRO na hora de lançar (`TODOS`). A régua do módulo é PCFORNEC.CODCOMPRADOR.
"""
from estoque import core

FORN = {10215: {"CODCOMPRADOR": 47}, 500: {"CODCOMPRADOR": 12}, 600: {"CODCOMPRADOR": None}}
COMP = {47: "JOÃO VICTOR EUGÊNIO PEREIRA", 12: "RONILSON"}
JOAO, RONI = COMP[47], COMP[12]


def _pe(pid, codfornec, comprador="TODOS"):
    return {"id": pid, "codfornec": codfornec, "fornecedor": f"F{codfornec}", "comprador": comprador}


def test_lancado_com_empresa_toda_aparece_para_o_comprador_do_fornecedor():
    manuais = [_pe(1, 10215, "TODOS")]
    out = core.manuais_do_comprador(manuais, JOAO, FORN, COMP)
    assert [p["id"] for p in out] == [1]
    assert out[0]["comprador"] == JOAO


def test_lancado_com_o_filtro_de_OUTRO_comprador_vai_para_o_dono_do_fornecedor():
    manuais = [_pe(1, 10215, RONI)]          # lançado com o filtro do Ronilson ligado
    assert core.manuais_do_comprador(manuais, RONI, FORN, COMP) == []
    assert [p["id"] for p in core.manuais_do_comprador(manuais, JOAO, FORN, COMP)] == [1]


def test_empresa_toda_continua_vendo_todos():
    manuais = [_pe(1, 10215), _pe(2, 500), _pe(3, None, "TODOS")]
    out = core.manuais_do_comprador(manuais, "TODOS", FORN, COMP)
    assert [p["id"] for p in out] == [1, 2, 3]


def test_fornecedor_sem_cadastro_cai_no_nome_gravado_e_TODOS_nao_e_nome():
    manuais = [_pe(1, None, RONI), _pe(2, None, "TODOS"), _pe(3, 600, JOAO)]
    assert [p["id"] for p in core.manuais_do_comprador(manuais, RONI, FORN, COMP)] == [1]
    assert [p["id"] for p in core.manuais_do_comprador(manuais, JOAO, FORN, COMP)] == [3]
    # sem fornecedor e sem nome: só aparece em "Empresa toda", nunca no filtro de alguém
    assert 2 not in [p["id"] for p in core.manuais_do_comprador(manuais, JOAO, FORN, COMP)]


def test_codfornec_como_texto_ou_float_resolve_igual():
    assert core.comprador_do_fornecedor("10215", FORN, COMP) == JOAO
    assert core.comprador_do_fornecedor(10215.0, FORN, COMP) == JOAO
    assert core.comprador_do_fornecedor("", FORN, COMP) is None
