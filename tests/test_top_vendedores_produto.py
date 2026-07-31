"""Gate do "top vendedores do item" no drawer 360° do produto (pedido do diretor 07/2026:
"trazer o melhor vendedor do item nessa janela").

O que estes testes protegem:
  · a consulta é POR PRODUTO. O corte transversal (todos os produtos × todos os vendedores) daria
    ~2.900 × ~50 ≈ 145 mil linhas e estouraria o limite de 100.000 do `executeQueries` — em
    SILÊNCIO, que é o pior modo de falhar e já mordeu este projeto no PCEST;
  · ordena por QUANTIDADE (é ela que escoa estoque), com o faturamento viajando junto;
  · o dado é do RCA, não do dataset de Estoque — e o nome do vendedor vem do mapa do Comercial,
    por import tardio (o server importa este blueprint; no topo seria circular);
  · nada disso pode derrubar o drawer: sem RCA, sem mapa de nomes ou sem venda, a seção some e o
    resto da janela continua.
"""
import datetime

import pytest

from estoque import core, queries as Q, routes


# ─────────────────────── a query ───────────────────────
def test_query_filtra_por_produto_antes_de_agrupar():
    """Se alguém tirar o CODPROD do filtro para "trazer tudo de uma vez", volta a estourar o
    limite do executeQueries — e cortado em silêncio, ninguém percebe."""
    q = Q.q_vendedores_do_produto_rca(47363, datetime.date(2026, 1, 1), datetime.date(2026, 7, 31), ["3"])
    assert "FATURAMENTO_VENDAS[CODPROD] = 47363" in q
    assert "FATURAMENTO_VENDAS[CODUSUR]" in q          # o agrupamento é por vendedor
    assert "FATURAMENTO_VENDAS[CODPROD]," not in q      # e NÃO por produto (não é corte transversal)


def test_query_traz_quantidade_e_faturamento():
    """O diretor pediu os dois: quantidade escoa estoque, faturamento diz se vale a pena."""
    q = Q.q_vendedores_do_produto_rca(1, datetime.date(2026, 1, 1), datetime.date(2026, 7, 31))
    assert "SUM(FATURAMENTO_VENDAS[QT])" in q
    assert "[VENDA BRUTA]" in q                          # measure oficial, não SUM de coluna
    assert "DTSAIDA" in q


# ─────────────────────── o ranking ───────────────────────
@pytest.fixture
def app_ctx():
    import server  # noqa: F401
    from server import app
    return app


def _mock(monkeypatch, rows, nomes=None, explode=False):
    def _dax(*a, **k):
        if explode:
            raise RuntimeError("BI fora")
        return rows
    monkeypatch.setattr(routes.pbi, "run_dax_rca", _dax)
    monkeypatch.setattr(routes, "_pg", lambda: False)
    routes.pbi._CACHE.clear()
    import server
    monkeypatch.setattr(server, "_carregar_vendedores_map",
                        lambda: nomes if nomes is not None else {})


def _top(app_ctx, **kw):
    with app_ctx.test_request_context("/estoque/api/produto/1"):
        return routes._top_vendedores_produto(1, "mes", ["3"], datetime.date(2026, 7, 31), **kw)


def test_ordena_por_QUANTIDADE_nao_por_faturamento(app_ctx, monkeypatch):
    """Quem gira volume é quem escoa estoque parado — o caso de uso que motivou o pedido.
    Aqui o campeão de faturamento vende POUCA unidade cara: não pode liderar."""
    _mock(monkeypatch, [
        {"CODUSUR": 1, "qtd": 10.0, "valor": 90000.0},    # caro, pouco volume
        {"CODUSUR": 2, "qtd": 500.0, "valor": 7000.0},    # é este que escoa
    ], nomes={"1": {"nome": "CARO"}, "2": {"nome": "VOLUME"}})
    top = _top(app_ctx)
    assert [v["nome"] for v in top] == ["VOLUME", "CARO"]
    assert top[0]["valor"] == 7000.0, "o faturamento viaja junto, para o comprador ver o conflito"


def test_corta_no_top_3(app_ctx, monkeypatch):
    _mock(monkeypatch, [{"CODUSUR": i, "qtd": float(i), "valor": float(i)} for i in range(1, 9)])
    assert len(_top(app_ctx)) == 3


def test_sem_nome_cai_no_codigo_do_RCA(app_ctx, monkeypatch):
    """Mapa de nomes indisponível não pode zerar a seção: ranking com número vale mais que
    ranking nenhum."""
    _mock(monkeypatch, [{"CODUSUR": 77, "qtd": 5.0, "valor": 50.0}], nomes={})
    assert _top(app_ctx)[0]["nome"] == "RCA 77"


def test_vendedor_sem_codusur_fica_de_fora(app_ctx, monkeypatch):
    """Linha sem CODUSUR viraria "RCA 0" e poluiria o ranking com um vendedor que não existe."""
    _mock(monkeypatch, [{"CODUSUR": None, "qtd": 999.0, "valor": 999.0},
                        {"CODUSUR": 5, "qtd": 1.0, "valor": 1.0}], nomes={"5": {"nome": "REAL"}})
    top = _top(app_ctx)
    assert len(top) == 1 and top[0]["nome"] == "REAL"


def test_RCA_fora_degrada_para_lista_vazia(app_ctx, monkeypatch):
    """A seção some; o resto do drawer (estoque, sugestão, lotes) continua de pé."""
    _mock(monkeypatch, [], explode=True)
    assert _top(app_ctx) == []


def test_produto_sem_venda_no_periodo_nao_inventa_secao(app_ctx, monkeypatch):
    _mock(monkeypatch, [])
    assert _top(app_ctx) == []


def test_segunda_chamada_vem_do_cache(app_ctx, monkeypatch):
    """Uma consulta por produto aberto é aceitável; uma por render, não."""
    chamadas = []

    def _dax(*a, **k):
        chamadas.append(1)
        return [{"CODUSUR": 1, "qtd": 3.0, "valor": 30.0}]
    monkeypatch.setattr(routes.pbi, "run_dax_rca", _dax)
    monkeypatch.setattr(routes, "_pg", lambda: False)
    routes.pbi._CACHE.clear()
    import server
    monkeypatch.setattr(server, "_carregar_vendedores_map", lambda: {})
    _top(app_ctx)
    _top(app_ctx)
    assert len(chamadas) == 1


# ─────────────────────── multi-fonte ───────────────────────
def test_modo_postgres_tem_provider_espelhado():
    """Regra do README: caminho novo sem branch em modo BD levanta RuntimeError e a DEMO quebra.
    O provider tem de existir e falar a MESMA forma que o DAX (CODUSUR/qtd/valor)."""
    from estoque import provider_sql as PS
    assert hasattr(PS, "vendedores_do_produto")
    import inspect
    src = inspect.getsource(PS.vendedores_do_produto)
    assert "codprod = %s" in src, "filtra no SQL, não em Python (1,17M linhas no fato sintético)"
    for chave in ("CODUSUR", "qtd", "valor"):
        assert chave in src


def test_core_round_nao_quebra_com_none():
    """As linhas vêm do BI e podem trazer null — o ranking não pode explodir por isso."""
    assert core._n(None) == 0


# ─────────────────── o modo como a PRODUÇÃO carrega o app ───────────────────
# O Dockerfile roda `python server.py`, então lá aquele arquivo é o módulo `__main__` — não
# `server`. A 1ª versão fazia `from server import _carregar_vendedores_map`, o que em produção
# importava uma SEGUNDA cópia de um módulo de ~8,7 mil linhas (outro Flask app, outra conexão
# Redis) a cada drawer aberto; o que falhasse ali caía no except e o mapa vinha vazio — TODO
# vendedor virava "RCA 950" na tela do diretor.
#
# ⚠️ Nenhum teste pegaria isso pelo caminho normal: na suíte o `import server` registra o módulo
# com esse nome e o import funciona. Por isso este teste SIMULA a condição de produção.
def test_resolve_nomes_quando_o_app_e_o_modulo___main__(monkeypatch):
    import sys
    import server as _srv
    monkeypatch.setattr(_srv, "_carregar_vendedores_map",
                        lambda: {"950": {"nome": "IGOR CLAUDIO"}})
    # produção: o arquivo virou __main__ e NÃO existe módulo chamado "server"
    monkeypatch.setitem(sys.modules, "__main__", _srv)
    monkeypatch.delitem(sys.modules, "server", raising=False)
    assert routes._vendedores_nomes() == {"950": "IGOR CLAUDIO"}


def test_nao_importa_server_de_novo():
    """Se alguém reintroduzir o `from server import ...`, volta o bug — e volta invisível,
    porque a suíte roda com o módulo registrado como 'server'."""
    import inspect
    # olha só LINHAS DE CÓDIGO: a docstring da função cita o padrão proibido de propósito,
    # para quem for mexer entender por que ele não pode voltar
    linhas = [l.strip() for l in inspect.getsource(routes._vendedores_nomes).splitlines()]
    assert not [l for l in linhas if l.startswith("from server import")]
    assert any("sys.modules" in l for l in linhas), "tem de reusar o módulo já carregado"


def test_mapa_ausente_nao_derruba_o_ranking(monkeypatch):
    """Sem mapa em módulo nenhum, o ranking sai com o código — não some."""
    import sys
    monkeypatch.setitem(sys.modules, "server", object())
    monkeypatch.setitem(sys.modules, "__main__", object())
    assert routes._vendedores_nomes() == {}
