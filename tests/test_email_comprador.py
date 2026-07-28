"""Gate: o email de Compras respeita o COMPRADOR VINCULADO ao usuário (Admin).

Bug encontrado em 07/2026: `emails.gerar_anexos` montava a querystring com `comprador=<cod>`,
mas o export lê **`comprador_cod`**. Resultado: o comprador recebia um relatório com o nome dele
no corpo ("Comprador JOÃO VICTOR") e os dados da EMPRESA TODA — inclusive o desempenho dos
colegas. O pedido do diretor ("o filtro tem de se estender ao email") já estava no código, só
não funcionava por causa do nome do parâmetro.
"""
import pytest

from estoque import emails, routes


def test_querystring_do_email_usa_o_parametro_QUE_O_EXPORT_LE(monkeypatch):
    """O teste que teria pego o bug: compara o nome do param com o que o filtro consome."""
    capturado = {}

    class _FakeApp:
        def test_request_context(self, path, query_string=None):
            capturado["qs"] = dict(query_string or {})
            raise RuntimeError("parar aqui — só queremos a querystring")

    anexos, erros = emails.gerar_anexos(_FakeApp(), ["produtos"], codcomprador=47)
    assert capturado["qs"].get("comprador_cod") == "47", \
        "o export lê `comprador_cod`; mandar `comprador` não filtra nada"
    assert anexos == [] and erros                     # a exceção foi só para interromper


def test_sem_comprador_vinculado_manda_a_empresa_toda():
    """Usuário sem comprador vinculado (diretor/admin) continua recebendo tudo."""
    capturado = {}

    class _FakeApp:
        def test_request_context(self, path, query_string=None):
            capturado["qs"] = dict(query_string or {})
            raise RuntimeError("parar")

    emails.gerar_anexos(_FakeApp(), ["produtos"], codcomprador=None)
    assert "comprador_cod" not in capturado["qs"]
    for vazio in ("", 0):
        emails.gerar_anexos(_FakeApp(), ["produtos"], codcomprador=vazio)
        assert "comprador_cod" not in capturado["qs"]


@pytest.fixture
def app_ctx():
    import server  # noqa: F401
    from server import app
    return app


def test_filtro_de_produtos_recorta_pelo_comprador(app_ctx):
    prods = [{"codprod": 1, "codcomprador": 47}, {"codprod": 2, "codcomprador": 99}]
    with app_ctx.test_request_context("/estoque/api/export/produtos.xlsx?comprador_cod=47"):
        assert [p["codprod"] for p in routes._aplicar_filtros_cliente(prods)] == [1]
    # e o nome ERRADO não pode filtrar (documenta o bug para não voltar)
    with app_ctx.test_request_context("/estoque/api/export/produtos.xlsx?comprador=47"):
        assert len(routes._aplicar_filtros_cliente(prods)) == 2


def test_relatorio_de_desempenho_tambem_recorta(app_ctx, monkeypatch):
    """`desempenho` é a única view que não passa por _aplicar_filtros_cliente — sem tratamento
    própria ela vazaria o desempenho de todos os compradores no email de um só."""
    monkeypatch.setattr(routes, "_desempenho_data",
                        lambda *a, **k: {"compradores": [{"codcomprador": 47, "comprador": "JOAO"},
                                                         {"codcomprador": 99, "comprador": "OUTRO"}]})
    monkeypatch.setattr(routes, "_filiais_venda", lambda: ["1"])
    with app_ctx.test_request_context("/estoque/api/export/desempenho.xlsx?comprador_cod=47"):
        _cols, linhas = routes._export_data("desempenho")
    assert [l["codcomprador"] for l in linhas] == [47]


def test_todas_as_views_do_catalogo_conhecem_o_recorte():
    """Documenta o mapa: quais relatórios recortam por comprador e quais não fazem sentido
    recortar (são por endereço/rua, não têm comprador)."""
    from estoque import relatorios as rel
    sem_comprador = {"conferencia", "vazias"}      # ocupação/WMS: grão é endereço
    assert sem_comprador.issubset(set(rel.VIEWS))
    # as demais têm de ter comprador no dado (via produto ou coluna própria)
    assert len(set(rel.VIEWS) - sem_comprador) >= 14
