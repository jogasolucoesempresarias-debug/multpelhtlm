"""Gate da ancoragem de data do módulo Compras em modo BD (18/08/2026).

O Comercial sempre ancorou o "hoje" no DADO (`provider_sql.hoje_analitico` — `max(dtsaida)` ou
`ANALYTICS_HOJE`). O Compras ficou de fora e usava `date.today()` puro.

Medido na demo: fato sintético até **24/07/2026**, relógio em **18/08/2026**. Toda janela de data
do módulo caía 25 dias à frente do dado:

  · venda "mês atual" ....... R$ 0 (agosto não existe no fato)
  · meta do orçamento ....... 0   (65% de uma venda 30d que era zero)
  · `dias_sem_venda` ........ +25 dias → itens saudáveis virando "parado"
  · curva ABC ............... o catálogo INTEIRO como "C" (3.781 produtos, nenhum A/B), porque
                              `core._aplicar_curva` faz isso quando o total da janela é zero

O último foi o sintoma que apareceu: o filtro de curva da aba Evolução voltava vazio na demo.

⚠️ O que estes testes protegem, além da correção: que ela **não vaze para o Power BI**. Em
produção o "hoje" TEM de ser o do calendário — ancorar no dado ali mudaria `dias_sem_venda`,
cobertura e parado do cliente real sem ninguém pedir.
"""
from datetime import date

import pytest


ANCORA = date(2026, 7, 24)
HOJE_RELOGIO = date.today()


@pytest.fixture
def app_ctx():
    """App Flask mínimo — `_hoje` só precisa de `request.args`."""
    from flask import Flask
    return Flask(__name__)


def _hoje_em(modo, app_ctx, qs="", ancora=ANCORA, quebrar=False):
    from estoque import routes, pbi
    orig_cfg = pbi.CONFIG["data_source"]
    orig_fn = routes.PS.hoje_analitico
    pbi.CONFIG["data_source"] = modo

    def _fake():
        if quebrar:
            raise RuntimeError("banco analítico fora")
        return ancora

    routes.PS.hoje_analitico = _fake
    try:
        with app_ctx.test_request_context("/x" + qs):
            return routes._hoje()
    finally:
        pbi.CONFIG["data_source"] = orig_cfg
        routes.PS.hoje_analitico = orig_fn


def test_modo_banco_usa_a_ancora_do_dado(app_ctx):
    assert _hoje_em("postgres", app_ctx) == ANCORA


def test_power_bi_continua_no_calendario(app_ctx):
    """A prova de que a correção não vaza para produção."""
    assert _hoje_em("powerbi", app_ctx) == HOJE_RELOGIO


def test_o_parametro_hoje_da_querystring_ganha_dos_dois(app_ctx):
    """É como o seeder da demo alinha a foto ao dia que está reconstruindo."""
    for modo in ("postgres", "powerbi"):
        assert _hoje_em(modo, app_ctx, qs="?hoje=2026-03-15") == date(2026, 3, 15)


def test_hoje_invalido_na_querystring_e_ignorado(app_ctx):
    assert _hoje_em("powerbi", app_ctx, qs="?hoje=banana") == HOJE_RELOGIO
    assert _hoje_em("postgres", app_ctx, qs="?hoje=banana") == ANCORA


def test_banco_analitico_fora_nao_derruba_a_tela(app_ctx):
    """Degrada para o calendário — pior que a âncora, melhor que erro em toda tela."""
    assert _hoje_em("postgres", app_ctx, quebrar=True) == HOJE_RELOGIO


def test_a_ancora_e_a_MESMA_funcao_que_o_comercial_usa():
    """Duas âncoras diferentes seriam dois "hojes" no mesmo app — o defeito que isto conserta."""
    import provider_sql
    from estoque import provider_sql as PS
    assert PS.hoje_analitico is provider_sql.hoje_analitico
