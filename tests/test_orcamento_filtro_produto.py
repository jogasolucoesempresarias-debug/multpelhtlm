"""Gate do filtro "Buscar produto" sobre os pedidos em aberto do Orçamento (08/2026).

Pedido do diretor: "quando filtrar o produto, trazer apenas os pedidos que constam o produto
filtrado, assim conseguimos saber se existe pedido para aquele item, qual a quantidade pedida
e quando foi feito o pedido".

Decisões que estes testes travam:
  · o recorte vale para a LISTA **e** para os agregados que a tela mostra ao lado dela — os
    cards de prazo leem a contagem do resumo e o valor da lista; recortar só um deixaria
    "15 entregas atrasadas" ao lado de uma tabela com um pedido (o defeito de dois universos
    que a aba Verbas teve duas vezes);
  · os KPIs de ORÇAMENTO não entram no recorte: meta e comprado são do comprador no mês;
  · duas quantidades por linha — "pedida" responde "eu já pedi?", "a chegar" responde "está
    chegando?". O diretor pediu as duas coisas na mesma frase.
"""
from datetime import date, timedelta

from estoque import core

HOJE = date(2026, 8, 10)
PROD = {
    100: {"CODPROD": 100, "DESCRICAO": "MAIONESE D AJUDA BAG 3KG", "QTUNITCX": 6},
    200: {"CODPROD": 200, "DESCRICAO": "CATCHUP D AJUDA GALAO", "QTUNITCX": 4},
    300: {"CODPROD": 300, "DESCRICAO": "MOSTARDA GALAO", "QTUNITCX": 4},
}
FORN = {9: {"FORNECEDOR": "ALIMENTOS WILSON LTDA", "CGC": "55.323.216/0002-60"}}
COMP = {47: "JOÃO VICTOR"}


def _cab(numped, dias_atras, prev_dias, vltotal=1000.0, vlentregue=0.0):
    return {"NUMPED": numped, "DTEMISSAO": (HOJE - timedelta(days=dias_atras)).isoformat(),
            "DTPREVENT": (HOJE + timedelta(days=prev_dias)).isoformat(),
            "VLTOTAL": vltotal, "VLENTREGUE": vlentregue,
            "CODFORNEC": 9, "CODCOMPRADOR": 47, "CODFILIAL": "3"}


def _item(numped, codprod, qtped, qtentregue=0.0):
    return {"NUMPED": numped, "CODPROD": codprod, "qtped": qtped, "qtentregue": qtentregue}


CAB = [_cab(1, 10, 5), _cab(2, 20, -3), _cab(3, 30, 20)]
ITENS = [
    _item(1, 100, 600), _item(1, 200, 400),      # pedido 1 tem a maionese
    _item(2, 200, 800),                          # pedido 2 NÃO tem
    _item(3, 100, 300, qtentregue=300),          # pedido 3: maionese JÁ ENTREGUE
    _item(3, 300, 500),
]


def _log(busca=None):
    return core.logistica_pedidos(CAB, ITENS, PROD, {}, COMP, FORN, hoje=HOJE, busca=busca)


def test_sem_busca_nada_muda():
    """Âncora: sem filtro, o mapa de produto sai vazio e nenhum pedido é recortado."""
    r = _log()
    assert r["do_produto"] == {}
    assert {p["numped"] for p in r["pedidos"]} == {1, 2, 3}


def test_busca_por_descricao_pega_so_quem_tem_o_item():
    r = _log("maionese")
    assert set(r["do_produto"]) == {1, 3}          # o pedido 2 não tem o produto
    assert r["do_produto"][1] == {"qt_pedida": 600.0, "qt_aberta": 600.0}


def test_busca_por_codigo_tambem_funciona():
    """Mesma regra da tela e do export: código OU descrição (core.casa_busca)."""
    assert set(_log("100")["do_produto"]) == {1, 3}
    assert core.casa_busca(PROD[100], "MAIONESE") is True
    assert core.casa_busca(PROD[200], "maionese") is False
    assert core.casa_busca(PROD[100], "") is True        # sem busca não filtra nada


def test_item_ja_entregue_aparece_com_zero_a_chegar():
    """Responde "eu já pedi?" mesmo não respondendo "está chegando?". Se fosse descartado,
    o comprador pediria de novo o que já recebeu — o mesmo erro da pré-entrada."""
    d = _log("maionese")["do_produto"][3]
    assert d["qt_pedida"] == 300.0 and d["qt_aberta"] == 0.0


# ── o recorte no bloco do Orçamento ──
def _res():
    """Resumo/abertos no formato de core.orcamento_winthor, com os 3 pedidos."""
    abertos = [
        {"numped": 1, "status_prazo": "chega_7", "valor_aberto": 1000.0},
        {"numped": 2, "status_prazo": "atrasado", "valor_aberto": 500.0},
        {"numped": 3, "status_prazo": "no_prazo", "valor_aberto": 300.0},
    ]
    resumo = {"meta": 90000.0, "comprado": 50000.0, "saldo": 40000.0, "pct_consumido": 0.55,
              "n_abertos": 3, "n_atrasados": 1, "n_chega7": 1, "valor_aberto": 1800.0}
    return {"resumo": resumo, "abertos": abertos, "pedidos": abertos, "por_comprador": []}


def test_recorte_refaz_os_agregados_dos_cards():
    """O ponto central: contagem (resumo) e valor (lista) TÊM de sair do mesmo universo."""
    r = core.recorta_abertos_por_produto(_res(), _log("maionese")["do_produto"])
    assert [p["numped"] for p in r["abertos"]] == [1, 3]
    assert r["resumo"]["n_abertos"] == 2
    assert r["resumo"]["n_atrasados"] == 0          # o atrasado era o pedido 2, sem o item
    assert r["resumo"]["n_chega7"] == 1
    assert r["resumo"]["valor_aberto"] == 1300.0    # 1000 + 300, não 1800


def test_kpis_de_orcamento_ficam_intactos():
    """Meta/comprado/saldo são do COMPRADOR no mês. Recortá-los por produto não significaria
    nada — e é o erro que a tela cometeria se o filtro fosse aplicado no lugar errado."""
    orig = _res()["resumo"]
    r = core.recorta_abertos_por_produto(_res(), _log("maionese")["do_produto"])
    for k in ("meta", "comprado", "saldo", "pct_consumido"):
        assert r["resumo"][k] == orig[k]
    assert r["resumo"]["filtro_produto"] is True


def test_as_duas_quantidades_viajam_na_linha():
    r = core.recorta_abertos_por_produto(_res(), _log("maionese")["do_produto"])
    p1 = next(p for p in r["abertos"] if p["numped"] == 1)
    p3 = next(p for p in r["abertos"] if p["numped"] == 3)
    assert (p1["qt_produto_pedida"], p1["qt_produto_aberta"]) == (600.0, 600.0)
    assert (p3["qt_produto_pedida"], p3["qt_produto_aberta"]) == (300.0, 0.0)


def test_busca_sem_resultado_esvazia_em_vez_de_mostrar_tudo():
    """Conjunto vazio é recorte legítimo, não "sem filtro" — a lista tem de sair vazia."""
    r = core.recorta_abertos_por_produto(_res(), _log("produto inexistente")["do_produto"])
    assert r["abertos"] == []
    assert r["resumo"]["n_abertos"] == 0 and r["resumo"]["valor_aberto"] == 0


def test_front_manda_a_busca_e_carrega_a_chave_de_cache():
    """Gate de código: sem a busca na chave, a 1ª pesquisa fica cacheada e é servida às
    seguintes — o comprador veria os pedidos do item ERRADO."""
    from pathlib import Path
    js = Path('static/estoque/estoque.js').read_text(encoding='utf-8')
    t = js[js.index('async function renderOrcamento'):][:1400]
    assert "'&busca='" in t, 'renderOrcamento parou de mandar a busca ao servidor'
    key = next(l for l in t.splitlines() if 'const orcKey' in l)
    assert 'bq' in key, f'busca fora da chave de cache do Orçamento: {key.strip()}'


# ───────────────────── recorte por FORNECEDOR (08/2026) ─────────────────────

def _ped_f(numped, codfornec, valor=1000.0, prazo="atrasado"):
    return {"numped": numped, "codfornec": codfornec, "valor_aberto": valor,
            "status_prazo": prazo, "recebido": False}


def _res_f(pedidos):
    return {"abertos": list(pedidos), "pedidos": list(pedidos),
            "resumo": {"n_abertos": len(pedidos), "n_atrasados": 0, "n_chega7": 0,
                       "valor_aberto": 0.0, "meta": 100.0, "comprado": 50.0, "saldo": 50.0}}


def test_o_filtro_de_fornecedor_recorta_os_pedidos_em_aberto():
    """⚠️ Achado pelo diretor 08/2026: "quando uso o filtro de fornecedor, não está filtrando os
    pedidos abaixo; apenas quando filtro produto". O recorte por produto existia (`?busca=`) e o
    de fornecedor nunca foi ligado — a tela mostrava GALVANOTEK no filtro e a lista trazia MINAS
    MAIS, EMVAC e BOMBRIL."""
    from estoque import core
    res = _res_f([_ped_f(1, 113), _ped_f(2, 999), _ped_f(3, 113)])
    out = core.recorta_abertos_por_fornecedor(res, 113)
    assert [p["numped"] for p in out["abertos"]] == [1, 3]
    assert out["resumo"]["filtro_fornecedor"] is True


def test_o_recorte_de_fornecedor_REAGREGA_os_cards_junto_com_a_lista():
    """⚠️ Os cards de prazo leem a CONTAGEM do resumo e o VALOR da lista. Recortar só um lado
    deixaria "15 entregas atrasadas" ao lado de uma tabela com um pedido — o defeito de dois
    universos que a aba Verbas teve duas vezes."""
    from estoque import core
    res = _res_f([_ped_f(1, 113, 500.0, "atrasado"), _ped_f(2, 999, 900.0, "atrasado"),
                _ped_f(3, 113, 250.0, "chega_7")])
    out = core.recorta_abertos_por_fornecedor(res, 113)
    r = out["resumo"]
    assert r["n_abertos"] == 2 == len(out["abertos"])
    assert r["n_atrasados"] == 1 and r["n_chega7"] == 1
    assert r["valor_aberto"] == 750.0


def test_os_KPIs_de_orcamento_NAO_entram_no_recorte_de_fornecedor():
    """A meta é 65% da venda do COMPRADOR no mês — não tem quebra por fornecedor. Recortá-la
    daria um "% da meta" que não corresponde a meta nenhuma. Mesma política do recorte por
    produto; a tela avisa."""
    from estoque import core
    res = _res_f([_ped_f(1, 113), _ped_f(2, 999)])
    out = core.recorta_abertos_por_fornecedor(res, 113)
    for k in ("meta", "comprado", "saldo"):
        assert out["resumo"][k] == res["resumo"][k]


def test_fornecedor_vazio_ou_invalido_devolve_a_lista_INTEIRA_sem_estourar():
    """Querystring montada à mão não pode derrubar a aba — mas também não pode filtrar errado."""
    from estoque import core
    res = _res_f([_ped_f(1, 113), _ped_f(2, 999)])
    for v in ("", None, 0, "abc"):
        assert len(core.recorta_abertos_por_fornecedor(res, v)["abertos"]) == 2


def test_os_DOIS_recortes_usam_a_MESMA_reagregacao():
    """⚠️ Duplicar a reagregação era garantir que um dos dois esquecesse um campo na próxima
    métrica. Os dois chamam `_reagrega_abertos`."""
    import inspect
    from estoque import core
    for fn in (core.recorta_abertos_por_produto, core.recorta_abertos_por_fornecedor):
        assert "_reagrega_abertos" in inspect.getsource(fn)


def test_o_fornec_viaja_na_querystring_E_na_chave_de_cache_do_front():
    """⚠️ Sem o `fornec` na chave de cache, o 1º fornecedor consultado seria servido aos demais
    pelo cache do cliente — o mesmo defeito da aba Verbas (tabela de um fornecedor ao lado do
    gráfico da empresa toda)."""
    import io
    js = io.open("static/estoque/estoque.js", encoding="utf-8").read()
    bloco = js[js.index("const bq=(S.cli.busca"):js.index("const bq=(S.cli.busca") + 1200]
    assert "const fq=(S.cli.fornec" in bloco, "o fornecedor tem de sair na querystring"
    assert "+bq+'|'+fq" in bloco, "e tem de entrar na chave de cache junto com a busca"
    assert "'&fornec='+encodeURIComponent(fq)" in bloco
