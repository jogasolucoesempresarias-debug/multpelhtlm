"""Gates dos dois defeitos reportados pelo diretor em 07/2026 nos relatórios do Compras:

  1. **PDF/Excel não respeitavam os filtros** do Explorador de produtos: a tela mostrava 116 itens
     e o export saía com o universo inteiro. Causa: `margem`, `cob_max` e `sem_ped` existiam só no
     front — o `exportQS()` não os enviava e o servidor não os conhecia.
  2. **Relatório de Reposição** saía como lista plana na ordem do código do produto. Tem de vir
     agrupado por fornecedor, do fornecedor com MAIS dinheiro a comprar para o menor.
"""
import pytest

from estoque import core, routes


# ─────────────────────── 1) filtros do Explorador chegam ao export ───────────────────────
def _p(cod, **kw):
    base = {"codprod": cod, "descricao": f"P{cod}", "fornecedor": "FORN", "codfornec": 1,
            "margem": 25.0, "cobertura_dias": 10, "qtd_ja_pedida": 0, "status_abast": "urgente"}
    base.update(kw)
    return base


@pytest.fixture
def app_ctx():
    import server  # noqa: F401 — registra o blueprint
    from server import app
    return app


def _filtrar(app_ctx, produtos, qs):
    with app_ctx.test_request_context(f"/estoque/api/export/produtos.pdf?{qs}"):
        return routes._aplicar_filtros_cliente(produtos)


def test_filtro_margem_chega_ao_export(app_ctx):
    prods = [_p(1, margem=-5.0), _p(2, margem=5.0), _p(3, margem=25.0), _p(4, margem=None)]
    out = _filtrar(app_ctx, prods, "margem=neg,sv")
    assert {p["codprod"] for p in out} == {1, 4}


def test_filtro_cobertura_maxima_chega_ao_export(app_ctx):
    """`cobertura_dias` oficial: inclui ruptura (0d) e exclui sem-giro (9999) — igual à tela."""
    prods = [_p(1, cobertura_dias=0), _p(2, cobertura_dias=30), _p(3, cobertura_dias=31),
             _p(4, cobertura_dias=9999), _p(5, cobertura_dias=None)]
    out = _filtrar(app_ctx, prods, "cob_max=30")
    assert {p["codprod"] for p in out} == {1, 2}


def test_filtro_so_sem_pedido_chega_ao_export(app_ctx):
    prods = [_p(1, qtd_ja_pedida=0), _p(2, qtd_ja_pedida=100)]
    assert {p["codprod"] for p in _filtrar(app_ctx, prods, "sem_ped=1")} == {1}


def test_filtros_combinam_como_na_tela(app_ctx):
    """O caso real: Cobertura ≤ 30 + Só sem pedido. O export tem de dar o MESMO recorte."""
    prods = [_p(1, cobertura_dias=10, qtd_ja_pedida=0),     # passa
             _p(2, cobertura_dias=10, qtd_ja_pedida=50),    # tem pedido
             _p(3, cobertura_dias=90, qtd_ja_pedida=0),     # cobertura alta
             _p(4, cobertura_dias=9999, qtd_ja_pedida=0)]   # sem giro
    assert {p["codprod"] for p in _filtrar(app_ctx, prods, "cob_max=30&sem_ped=1")} == {1}


def test_sem_filtro_nao_recorta_nada(app_ctx):
    prods = [_p(1), _p(2), _p(3)]
    assert len(_filtrar(app_ctx, prods, "")) == 3


def test_margem_bucket_espelha_o_front():
    """Mesmos cortes do `margemBucket` do estoque.js — se divergir, tela e PDF discordam."""
    casos = [(-0.1, "neg"), (0, "b0"), (9.9, "b0"), (10, "b10"), (19.9, "b10"),
             (20, "b20"), (29.9, "b20"), (30, "b30"), (100, "b30"), (None, "sv")]
    for m, esperado in casos:
        assert routes._margem_bucket({"margem": m}) == esperado, f"margem {m}"


# ─────────────────────── 2) relatório de Reposição agrupado ───────────────────────
def test_pdf_reposicao_agrupa_por_fornecedor_e_ordena_pelo_maior_valor():
    """Fornecedor com mais dinheiro a comprar vem primeiro; dentro dele, o item mais caro no topo.
    O valor é o da NF (c/ impostos) — a régua do Orçamento, decisão do diretor."""
    linhas = [
        {"codprod": 1, "fornecedor": "PEQUENO", "descricao": "a", "valor_sugerido_nf": 100.0},
        {"codprod": 2, "fornecedor": "GRANDE", "descricao": "b", "valor_sugerido_nf": 900.0},
        {"codprod": 3, "fornecedor": "GRANDE", "descricao": "c", "valor_sugerido_nf": 5000.0},
        {"codprod": 4, "fornecedor": "MEDIO", "descricao": "d", "valor_sugerido_nf": 2000.0},
    ]
    blob = routes._gerar_pdf("reposicao", linhas, group_by="fornecedor",
                             group_valor="valor_sugerido_nf", group_rotulo="A comprar")
    assert blob[:4] == b"%PDF"
    txt = _texto_pdf(blob)
    pos = {nome: txt.find(nome) for nome in ("GRANDE", "MEDIO", "PEQUENO")}
    assert all(v >= 0 for v in pos.values()), f"fornecedor faltando no PDF: {pos}"
    # GRANDE (5.900) > MEDIO (2.000) > PEQUENO (100)
    assert pos["GRANDE"] < pos["MEDIO"] < pos["PEQUENO"]
    assert "A comprar" in txt          # rótulo do subtotal do grupo


def test_pdf_reposicao_mostra_a_coluna_de_valor():
    spec = routes._PDF_COLS["reposicao"]
    campos = [c[0] for c in spec]
    assert "valor_sugerido_nf" in campos, "o valor a comprar é o critério do relatório"
    # padrão da aba Análise→Produtos (pedido do diretor): ABC + Já ped. + Cob.
    for c in ("curva_abc", "qtd_ja_pedida", "cobertura"):
        assert c in campos


def test_csv_reposicao_leva_as_duas_reguas():
    cols = routes._CSV_COLS["reposicao"]
    assert "valor_sugerido_nf" in cols and "valor_sugerido_liq" in cols
    assert "trib_fonte" in cols        # auditoria: de onde veio a alíquota


def _texto_pdf(blob):
    import base64
    import re
    import zlib
    out = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", blob, re.S):
        d = m.group(1).strip()
        for tentativa in (lambda x: zlib.decompress(base64.a85decode(x, adobe=True)),
                          zlib.decompress, lambda x: x):
            try:
                out.append(tentativa(d).decode("latin-1"))
                break
            except Exception:
                continue
    return "\n".join(out)


def test_agrupamento_sem_group_valor_mantem_ordem_alfabetica():
    """A view `produtos` continua agrupando por nome — não pode ter mudado junto."""
    linhas = [{"codprod": 1, "fornecedor": "ZETA", "descricao": "a", "valor": 10},
              {"codprod": 2, "fornecedor": "ALFA", "descricao": "b", "valor": 99}]
    txt = _texto_pdf(routes._gerar_pdf("produtos", linhas, group_by="fornecedor"))
    assert txt.find("ALFA") < txt.find("ZETA")
