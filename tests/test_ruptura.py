"""Teste do ajuste da Ruptura por comprador: 'Custo reposição' passou a ser a SUGESTÃO DE
COMPRA completa (aba Abastecimento) — valor_sugerido_liq de TODO item a comprar, não só
o custo dos itens zerados. Decisão do diretor 07/2026 (opção A: coluna renomeada)."""
from estoque import core


def _prod(cod, codcomp, qtdisp, giro_dia, sugestao_cx, valor_liq,
          suspensa=False, ja_pedida=0, venda_perdida=0.0):
    return {"codprod": cod, "codcomprador": codcomp, "comprador": "Carlos",
            "qtdisp": qtdisp, "giro_dia": giro_dia,
            "sugestao_cx": sugestao_cx, "valor_sugerido_liq": valor_liq,
            "compra_suspensa": suspensa, "qtd_ja_pedida": ja_pedida,
            "venda_perdida": venda_perdida}


def test_sugestao_soma_todos_os_itens_a_comprar_nao_so_os_zerados():
    produtos = [
        # zerado, a comprar → conta na ruptura E na sugestão
        _prod(1, 7, qtdisp=0, giro_dia=2.0, sugestao_cx=5, valor_liq=1000.0, venda_perdida=300.0),
        # COM estoque mas abaixo do alvo, a comprar → NÃO é ruptura, MAS entra na sugestão
        _prod(2, 7, qtdisp=50, giro_dia=1.0, sugestao_cx=3, valor_liq=600.0),
        # a comprar porém compra suspensa → fora da sugestão
        _prod(3, 7, qtdisp=0, giro_dia=1.0, sugestao_cx=2, valor_liq=400.0, suspensa=True),
        # sem giro → fora
        _prod(4, 7, qtdisp=0, giro_dia=0.0, sugestao_cx=0, valor_liq=0.0),
    ]
    r = core.ruptura_por_comprador(produtos)
    assert len(r) == 1
    g = r[0]
    # sugestão = 1000 + 600 (o suspenso e o sem-giro ficam fora) — inclui o item NÃO zerado
    assert g["sugestao_compra_valor"] == 1600
    assert g["custo_reposicao"] == 1600          # alias retrocompatível p/ export
    # ruptura segue contando só os zerados com giro (itens 1 e 3)
    assert g["n_ruptura"] == 2
    assert g["venda_perdida"] == 300             # só o item em ruptura


def test_usa_valor_liq_e_nao_sugestao_crua_vezes_custo():
    """Prova que o número vem de valor_sugerido_liq (caixa fechada), não de sugestao_compra×custo."""
    produtos = [_prod(1, 7, qtdisp=0, giro_dia=2.0, sugestao_cx=4, valor_liq=1234.56)]
    r = core.ruptura_por_comprador(produtos)
    assert r[0]["sugestao_compra_valor"] == 1234.56
