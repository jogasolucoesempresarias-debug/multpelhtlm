"""Gate da régua de QUANTIDADE do faturamento (09/2026).

**O defeito, em uma frase:** medida FILTRADA no numerador, `SUM` CRU no denominador.

`FATURAMENTO_VENDAS` registra tudo que sai do armazém. A medida `[VENDA BRUTA]` conta só
`CODOPER="S"`; `SUM(QT)` contava também `"ST"` (transferência entre filiais) e `"SB"`
(bonificação, que sai com VLVENDA = 0,00). Todo preço médio (venda ÷ qtd) saía diluído.

Achado pelo diretor na Pesquisa de preço: *"o preço de alguns itens aí, nosso preço está
errado... Exemplo limpol, esponja"*. Medido no BI (11/06→09/09/2026, filiais 3/7/8):

| cód   | produto            | app     | correto | ST no denominador |
|-------|--------------------|---------|---------|-------------------|
| 58511 | ESPONJA BOMBRIL    | R$ 2,07 | R$ 4,74 | 56,3%             |
| 42253 | DETERGENTE LIMPOL  | R$ 1,65 | R$ 2,40 | 31,0%             |
| 67322 | AMACIANTE M.BIJOU  | R$ 4,36 | R$ 8,28 | 47,2%             |

O relatório anunciava que estávamos **189% mais baratos** que o concorrente na esponja. Estávamos
**26%**. E o detergente saía a R$ 1,65 com `CUSTOULTENT` de R$ 1,87 — a tela dizia que vendíamos
abaixo do custo, e ninguém viu porque as duas colunas nunca tinham ficado lado a lado.

⚠️ **O sintoma NÃO é uniforme.** Item sem transferência saía certo (o 57433 batia no centavo),
então conferir um punhado de produtos não prova nada. 157 de 2.478 produtos erravam.

⚠️ **Já tinha mordido antes, e o conserto foi na ponta errada:** no top vendedores do drawer 360°
a transferência aparece como `CODUSUR 999` com R$ 0,00, e a saída foi excluir esse código no
`_vendedores_tecnicos()` em vez de filtrar o `CODOPER` na origem. Por isso o veneno seguiu
chegando ao preço. Saneamento por código de vendedor não substitui a régua da operação.
"""
import datetime as dt
import re

import pytest

from estoque import queries as Q

INI, FIM = dt.date(2026, 6, 11), dt.date(2026, 9, 9)
FIL = ["3", "7", "8"]


def _norm(s):
    """DAX sem quebra de linha nem espaço repetido — o teste é sobre a regra, não a indentação."""
    return re.sub(r"\s+", " ", s)


# ───────────────── venda: o denominador tem de obedecer à mesma régua da medida ─────────────────

@pytest.mark.parametrize("dax", [
    Q.q_vendas_rca(INI, FIM, FIL),
    Q.q_receita_comprador_rca(INI, FIM, FIL),
])
def test_toda_query_que_soma_QT_com_VENDA_BRUTA_filtra_CODOPER_S(dax):
    """Se a query pede `[VENDA BRUTA]`, a quantidade ao lado tem de ser da MESMA operação.

    Este é o teste que faltava: o defeito não é uma query errada, é a DUPLA (numerador filtrado,
    denominador cru) — e ela só se enxerga olhando as duas linhas juntas."""
    d = _norm(dax)
    assert "[VENDA BRUTA]" in d
    assert 'CALCULATE(SUM(FATURAMENTO_VENDAS[QT]), FATURAMENTO_VENDAS[CODOPER] = "S")' in d, (
        "quantidade de venda sem o filtro CODOPER: transferência (ST) e bonificação (SB) "
        "entram no denominador e diluem todo preço médio")


def test_nenhuma_soma_CRUA_de_QT_do_faturamento_sobrou():
    """Varre TODOS os builders do módulo. Query nova que repita o padrão cai aqui.

    Deliberadamente sobre o texto gerado, não sobre uma lista de nomes: a próxima query a somar
    `QT` ainda não existe, e é ela que este teste precisa pegar."""
    dax_gerados = [
        Q.q_vendas_rca(INI, FIM, FIL),
        Q.q_devol_rca(INI, FIM, FIL),
        Q.q_vendas_mensal_rca(INI, FIL),
        Q.q_receita_comprador_rca(INI, FIM, FIL),
        Q.q_vendedores_do_produto_rca(42253, INI, FIM, FIL),
        Q.q_vendedores_do_fornecedor_rca(113, INI, FIM, FIL),
    ]
    for d in dax_gerados:
        assert "SUM(FATURAMENTO_VENDAS[QT])" not in _norm(d).replace(
            'CALCULATE(SUM(FATURAMENTO_VENDAS[QT]), FATURAMENTO_VENDAS[CODOPER] = "S")', ""), (
            "SUM cru de QT do faturamento — ver a constante QT_VENDA em queries.py")


def test_a_serie_mensal_tambem_filtra_e_usa_IF_porque_CALCULATE_nao_serve():
    """Dentro de `GROUPBY`/`CURRENTGROUP` o `CALCULATE` não recebe o contexto de linha.

    A série mensal alimenta o forecast, o fallback de giro de item NOVO e o gráfico de 12 meses do
    drawer 360°. Medido: mar/2026 tinha **25,2%** de transferência e ago/2026, 17,9% — um item novo
    herdaria giro de mercadoria que só mudou de filial."""
    d = _norm(Q.q_vendas_mensal_rca(INI, FIL))
    assert 'IF(FATURAMENTO_VENDAS[CODOPER] = "S", FATURAMENTO_VENDAS[QT], 0)' in d


# ───────────────── devolução: o outro lado da mesma armadilha ─────────────────

def test_a_quantidade_devolvida_usa_o_MESMO_filtro_da_medida():
    """`[TOTAL DEVOLUCAO]` exclui a devolução de TRANSFERÊNCIA (CODATIV=37 com CODDEVOL<>9);
    a quantidade tem de excluir também.

    Sem isto o app subtraía **371.096** unidades onde a devolução real são **53.120** — 7x — e a
    "Qtd vendida" saía a menos. Validado no 42253: a linha excluída (CODATIV=37/CODDEVOL=65,
    37.440 un, R$ 68.741,86) é exatamente a diferença entre `SUM(VLDEVOLUCAO)` e o que a medida
    devolve, e a devolução que sobra sai a R$ 2,4992/un — coerente com o preço de venda real de
    R$ 2,40. Confere em 3 de 3 produtos."""
    d = _norm(Q.q_devol_rca(INI, FIM, FIL))
    assert "[TOTAL DEVOLUCAO]" in d
    assert ("NOT(FATURAMENTO_DEVOLUCAO[CODATIV] = 37 && "
            "FATURAMENTO_DEVOLUCAO[CODDEVOL] <> 9)") in d
    assert "SUM(FATURAMENTO_DEVOLUCAO[QT])" in d and "CALCULATE(SUM(FATURAMENTO_DEVOLUCAO[QT])" in d


def test_a_devolucao_AVULSA_segue_sem_filtro_de_proposito():
    """A medida `[TOTAL DEVOLUCAO AVULSA]` é um `SUM` puro, sem exclusão nenhuma — então a
    quantidade crua já está pareada com ela.

    Está aqui para que a próxima pessoa não "conserte" por simetria: copiar o filtro para cá
    criaria o defeito espelhado, com a quantidade filtrada e o valor não."""
    d = _norm(Q.q_devol_av_rca(INI, FIM, FIL))
    assert "SUM(FATURAMENTO_DEVOLUCAO_AVULSA[QT])" in d
    assert "CODATIV" not in d


# ───────────────── o custo que voltou ─────────────────

def test_o_snapshot_traz_o_custo_da_ULTIMA_ENTRADA_alem_do_CUSTOFIN():
    """São coisas diferentes e o diretor pediu a segunda: *"preço de custo, puxar a última
    entrada do item"*. `CUSTOFIN` é o custo financeiro (reposição contábil); `CUSTOULTENT` é o
    que a mercadoria custou ao entrar. Medido no 42253: 1,8746 × 1,8750."""
    d = _norm(Q.q_snapshot_estoque(["3", "5"]))
    assert "MAX(PCEST[CUSTOFIN])" in d
    assert "MAX(PCEST[CUSTOULTENT])" in d
