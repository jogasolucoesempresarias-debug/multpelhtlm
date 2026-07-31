"""Gate da curva ABC do FORNECEDOR (bug achado pelo diretor 07/2026).

Sintoma: filtrando só a BOMBRIL na aba Fornecedores ela aparecia como **C**; com todos os
fornecedores na tela, voltava a **A**.

Causa: o Pareto era refeito sobre a lista JÁ FILTRADA. Pareto sobre recorte não significa nada —
com um fornecedor só, o acumulado dele é 100%, o que cai direto na faixa C. Não é arredondamento
nem empate: a classificação inverte, e é o tipo de número que destrói a confiança na tela porque
o usuário vê os dois valores na mesma sessão.

A política correta já existia para os PRODUTOS e está no README: a curva é atribuída sobre o
conjunto INTEIRO; os filtros de tela apenas recortam a lista. Só unidade e período a redefinem.
"""
import pytest

from estoque import core


def _p(cod, forn, venda):
    return {"codprod": cod, "codfornec": forn, "fornecedor": f"F{forn}", "venda": venda,
            "valor": 10.0, "lucro": venda * 0.2, "qtdisp": 5, "giro_dia": 1.0, "giro_mes": 30,
            "custo_unit": 1.0, "status_parado": None, "curva_abc": "A", "cobertura": 30,
            "valor_sugerido_liq": 0.0, "valor_sugerido_nf": 0.0, "sugestao_cx": 0}


# universo: um fornecedor gigante (80% da venda) e uma cauda de pequenos
UNIVERSO = ([_p(1, 113, 800.0)]                                   # BOMBRIL: domina o Pareto → A
            + [_p(10 + i, 200 + i, 20.0) for i in range(10)])     # cauda: 200 no total


def test_fornecedor_grande_e_A_no_universo():
    m = core.curva_abc_fornecedores(UNIVERSO)
    assert m[113] == "A"


def test_o_MESMO_fornecedor_continua_A_quando_filtrado_sozinho():
    """O caso exato do relato: filtrar a BOMBRIL não pode transformá-la em C."""
    universo = core.curva_abc_fornecedores(UNIVERSO)
    so_bombril = [p for p in UNIVERSO if p["codfornec"] == 113]
    linhas = core.fornecedores(so_bombril, curva_map=universo)
    assert len(linhas) == 1
    assert linhas[0]["curva_abc"] == "A", "curva mudou por causa do filtro de tela"


def test_sem_o_mapa_o_pareto_local_REPRODUZ_o_bug():
    """Documenta por que o `curva_map` existe. Se um dia alguém achar que dá para voltar a
    calcular no recorte, este teste mostra o resultado: o líder de venda vira C."""
    so_bombril = [p for p in UNIVERSO if p["codfornec"] == 113]
    linhas = core.fornecedores(so_bombril)          # sem curva_map → Pareto local
    assert linhas[0]["curva_abc"] == "C"


def test_curva_nao_muda_com_recorte_nenhum():
    """Qualquer subconjunto tem de devolver a mesma curva que o universo — é o contrato."""
    universo = core.curva_abc_fornecedores(UNIVERSO)
    for alvo in (113, 200, 205):
        sub = [p for p in UNIVERSO if p["codfornec"] == alvo]
        linhas = core.fornecedores(sub, curva_map=universo)
        assert linhas[0]["curva_abc"] == universo[alvo]


def test_fornecedor_fora_do_mapa_nao_explode():
    """Produto cujo fornecedor não estava no universo (cadastro novo entre duas cargas)."""
    linhas = core.fornecedores([_p(99, 777, 5.0)], curva_map={113: "A"})
    assert linhas[0]["curva_abc"] == "C"


def test_produto_sem_fornecedor_nao_entra_no_pareto():
    """`codfornec` nulo viraria uma chave fantasma somando venda de ninguém."""
    m = core.curva_abc_fornecedores(UNIVERSO + [_p(500, None, 9999.0)])
    assert None not in m
    assert m[113] == "A", "a venda órfã não pode diluir o Pareto dos demais"


def test_universo_sem_venda_nao_quebra():
    m = core.curva_abc_fornecedores([_p(1, 113, 0.0), _p(2, 114, 0.0)])
    assert set(m.values()) <= {"A", "B", "C"}


@pytest.mark.parametrize("chamador", ["export"])
def test_export_de_fornecedores_usa_o_universo(chamador):
    """O export tinha o mesmo defeito: `core.fornecedores` recebia a lista já filtrada, então o
    Excel repetia na planilha a curva errada que a tela mostrava."""
    import inspect

    from estoque import routes
    src = inspect.getsource(routes._export_data)
    trecho = src[src.index('elif view == "fornecedores"'):src.index('elif view == "compradores"')]
    assert "curva_abc_fornecedores(produtos" in trecho, \
        "a curva do export tem de sair de `produtos` (universo), não da lista filtrada"
