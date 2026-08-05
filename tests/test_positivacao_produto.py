"""Gate da POSITIVAÇÃO do item no gráfico 12m do drawer 360°.

Pedido do diretor 07/2026: "acrescentar uma linha de positivação de cliente, para entender se
além da venda estamos perdendo positivação em clientes?". É a decomposição certa — venda caindo
com clientes caindo é perda de BASE; venda caindo com clientes estável é os mesmos comprando
MENOS. São problemas com ações diferentes, e a tela não separava os dois.

Decisões travadas aqui:
  · a positivação vem de CARONA na query que já existia (mesmo grão, zero linha nova). Medido no
    BI real, o DISTINCTCOUNT custou +0,09s (+5%) — barato porque distingue dentro de grupos
    pequenos (um produto, um mês), não na tabela toda;
  · positivação NÃO desconta devolução, ao contrário do valor: é visita ("o cliente comprou este
    item no mês"), e devolver não desfaz a visita;
  · mês sem venda vale 0 clientes, não "sem dado" — a queda tem de aparecer desenhada.
"""
import datetime

import pytest

from estoque import queries as Q, routes


# ─────────────────────── a query ───────────────────────
def test_query_traz_clientes_sem_linha_a_mais():
    """A positivação entra como COLUNA na query que já rodava. Se virar query própria, dobra o
    custo do gráfico para um dado que vem de graça no mesmo agrupamento."""
    q = Q.q_venda_produto_mensal_rca(datetime.date(2025, 8, 1), ["3"])
    assert "DISTINCTCOUNT(FATURAMENTO_VENDAS[CODCLI])" in q
    assert q.count("SUMMARIZECOLUMNS") == 1
    # o grão não mudou: produto × mês
    assert "FATURAMENTO_VENDAS[CODPROD]" in q and "CALENDARIO[AnoMes]" in q


def test_provider_postgres_espelha_a_positivacao():
    """Regra do README: caminho novo sem branch em modo BD quebra a DEMO."""
    import inspect

    from estoque import provider_sql as PS
    src = inspect.getsource(PS.venda_produto_mensal)
    assert "count(DISTINCT codcli)" in src
    assert '"clientes"' in src


# ─────────────────────── o mapa ───────────────────────
@pytest.fixture
def app_ctx():
    import server  # noqa: F401
    from server import app
    return app


def _mock(monkeypatch, vendas, devol=()):
    def _dax(q, *a, **k):
        return list(devol) if "DEVOLUCAO" in q else list(vendas)
    monkeypatch.setattr(routes.pbi, "run_dax_rca", _dax)
    monkeypatch.setitem(routes.pbi.CONFIG, "data_source", "powerbi")
    routes.pbi._CACHE.clear()


def _v(cod, am, venda, clientes=None):
    r = {"CODPROD": cod, "AnoMes": am, "venda": venda}
    if clientes is not None:
        r["clientes"] = clientes
    return r


def test_mapa_devolve_venda_e_clientes(app_ctx, monkeypatch):
    _mock(monkeypatch, [_v(1, 202606, 1000.0, 24), _v(1, 202607, 400.0, 9)])
    venda, cli = routes._vendas_mensal_rs_map(datetime.date(2026, 7, 31), ["3"])
    assert venda[1][202606] == 1000.0
    assert cli[1] == {202606: 24, 202607: 9}


def test_devolucao_abate_o_VALOR_e_nao_a_positivacao(app_ctx, monkeypatch):
    """O valor é líquido; a positivação é visita. Um cliente que comprou e devolveu continua
    tendo comprado no mês — descontar ali responderia outra pergunta."""
    _mock(monkeypatch, [_v(1, 202606, 1000.0, 24)],
          devol=[{"CODPROD": 1, "AnoMes": 202606, "dev": 300.0}])
    venda, cli = routes._vendas_mensal_rs_map(datetime.date(2026, 7, 31), ["3"])
    assert venda[1][202606] == 700.0     # valor caiu
    assert cli[1][202606] == 24          # positivação não


def test_RCA_fora_devolve_os_dois_mapas_vazios(app_ctx, monkeypatch):
    """Degradação tem de manter a FORMA — quem desempacota a tupla não pode receber um dict."""
    def _boom(*a, **k):
        raise RuntimeError("BI fora")
    monkeypatch.setattr(routes.pbi, "run_dax_rca", _boom)
    routes.pbi._CACHE.clear()
    venda, cli = routes._vendas_mensal_rs_map(datetime.date(2026, 7, 31), ["3"])
    assert venda == {} and cli == {}


def test_cache_no_formato_antigo_nao_quebra(app_ctx, monkeypatch):
    """Cache em memória de um processo que já rodava a versão anterior (só o mapa de venda).
    Morre no deploy, mas um processo de vida longa não pode estourar por causa disso."""
    routes.pbi._CACHE.clear()
    hoje = datetime.date(2026, 7, 31)
    key = f"vmesrs:{routes._filiais_key(['3'])}:{hoje.strftime('%Y-%m')}"
    routes.pbi._CACHE.set(key, {1: {202606: 500.0}}, 60)      # formato ANTIGO (dict puro)
    venda, cli = routes._vendas_mensal_rs_map(hoje, ["3"])
    assert venda == {1: {202606: 500.0}} and cli == {}


def test_serie_de_clientes_preenche_zero_no_mes_sem_venda(app_ctx, monkeypatch):
    """Mês sem venda = 0 clientes, não None: a queda a zero é justamente o que ele quer ver."""
    _mock(monkeypatch, [_v(1, 202606, 1000.0, 24)])
    _venda, cli = routes._vendas_mensal_rs_map(datetime.date(2026, 7, 31), ["3"])
    serie = [int(cli[1].get(am) or 0) for am in (202605, 202606, 202607)]
    assert serie == [0, 24, 0]
