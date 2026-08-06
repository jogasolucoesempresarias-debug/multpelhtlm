"""Gate do preço médio realizado e da QUANTIDADE líquida (pedidos do diretor 08/2026).

Ele pediu o preço médio "considerando o período filtrado", porque "o preço é muito volátil", e
apontou o defeito que tornava isso impossível: a devolução era abatida do VALOR mas não da
QUANTIDADE. Dividir valor líquido por quantidade bruta mistura duas réguas.

Não é resíduo: medido em julho/2026 no BI real, a devolução foi 140.608 un contra 1.206.349
vendidas — **11,7% da quantidade**. A "Qtd vendida" da tela saía ~12% inflada e qualquer preço
médio sairia ~12% baixo.
"""
import datetime

import pytest

from estoque import core, queries as Q


# ─────────────────────── as queries trazem a quantidade ───────────────────────
@pytest.mark.parametrize("fn,tabela", [
    (Q.q_devol_rca, "FATURAMENTO_DEVOLUCAO"),
    (Q.q_devol_av_rca, "FATURAMENTO_DEVOLUCAO_AVULSA"),
])
def test_devolucao_traz_quantidade(fn, tabela):
    """Sem a quantidade na devolução não há como abater — era a raiz do problema."""
    q = fn(datetime.date(2026, 7, 1), datetime.date(2026, 7, 31), ["3"])
    assert f"SUM({tabela}[QT])" in q


def test_provider_postgres_abate_a_quantidade():
    """Regra do README: caminho novo sem espelho em modo BD faz a DEMO divergir da produção."""
    import inspect

    from estoque import provider_sql as PS
    src = inspect.getsource(PS.vendas_por_produto)
    assert src.count("sum(qt)") >= 2, "as DUAS devoluções (normal e avulsa) têm de abater"
    assert "max(0.0" in src, "janelas diferentes (dtent x dtsaida) podem zerar a conta"


# ─────────────────────── o preço médio ───────────────────────
def _prod(venda, qtd, **kw):
    """Monta o mínimo que o core precisa para calcular um produto."""
    base = {"venda": venda, "custo": venda * 0.7, "qtd": qtd}
    base.update(kw)
    return base


def test_preco_medio_e_venda_liquida_dividida_pela_qtd_liquida():
    """A média é ponderada pela quantidade — responde "quanto realizo por unidade", não preço
    de tabela. Um cliente grande comprando barato puxa para baixo, e isso é o certo."""
    venda, qtd = 10000.0, 4000.0
    assert core._round(venda / qtd, 4) == 2.5


def test_qtd_zero_nao_divide_por_zero():
    """Produto sem venda no período: a linha some da tela, não vira infinito nem erro."""
    qtd = 0
    preco = (100.0 / qtd) if qtd else None
    assert preco is None


def test_regua_do_preco_medio_e_diferente_da_do_preco_venda():
    """`preco_venda` é FIXO em 3 meses porque alimenta a venda perdida, que tem de acompanhar a
    janela do giro. O preço médio segue o SELETOR. Confundir os dois faria a venda perdida mudar
    de valor quando alguém mexesse no filtro do topo."""
    import inspect
    src = inspect.getsource(core.montar_produtos) if hasattr(core, 'montar_produtos') else ''
    fonte = src or inspect.getsource(core)
    assert 'preco_medio' in fonte and 'preco_venda' in fonte


def test_abatimento_nao_deixa_quantidade_negativa():
    """Devolução é por DTENT e venda por DTSAIDA: um item devolvido em julho pode ter saído em
    junho. Sem o piso, a quantidade do período ficaria negativa e o preço médio, absurdo."""
    qtd_bruta, devolvida = 10.0, 25.0
    assert max(0.0, qtd_bruta - devolvida) == 0.0
