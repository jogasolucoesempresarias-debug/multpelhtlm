"""Gate da tributação do pedido de compra (IPI/ST) — régua da NF na sugestão.

Contexto do bug que originou isto (07/2026, pedido 45 GALVANOTEK / 565684 no Winthor):
  · o Orçamento mede o realizado por PCPEDIDO[VLTOTAL] = NF CHEIA (R$ 44.982,01);
  · a sugestão de compra saía só em mercadoria (R$ 39.536,28) → o comprador planejava numa
    régua e consumia a meta em outra (7,1% no agregado de 120d; 13,8% neste fornecedor);
  · e a tela ainda divergia do PDF em R$ 0,10 por somar o custo cru enquanto o documento usava
    o custo arredondado a 4 casas (o que a planilha entrega ao ERP).

Os números deste arquivo vêm do relatório 211 REAL e das medições no BI — não são inventados.
"""
import math

import pytest

from estoque import core


# ─────────────────────── montar_tributacao: extração do pedido real ───────────────────────
def _cab(numped, codfornec, dtemissao="2026-07-01"):
    return {"NUMPED": numped, "CODFORNEC": codfornec, "DTEMISSAO": dtemissao}


def _item(numped, codprod, periipi=0.0, vlipi=0.0, percst=0.0, vlst=0.0, qtped=1):
    return {"NUMPED": numped, "CODPROD": codprod, "qtped": qtped, "qtentregue": 0,
            "periipi": periipi, "vlipi": vlipi, "percst": percst, "vlst": vlst}


def test_extrai_aliquota_praticada_do_pedido_real():
    """O par (fornecedor, produto) sai do pedido real — 15% de IPI como no 211 da GALVANOTEK."""
    trib = core.montar_tributacao([_cab(565684, 8579)],
                                  [_item(565684, 45017, periipi=15.0, vlipi=6.980055)],
                                  hoje=__import__("datetime").date(2026, 7, 27))
    ipi, st, fonte = core.tributacao_de(trib, 8579, 45017)
    assert (ipi, st, fonte) == (15.0, 0.0, "pedido_real")


def test_st_efetivo_sai_sobre_a_mercadoria_nao_sobre_a_base_majorada():
    """ST previsto = VLST ÷ preço (preço derivado de VLIPI÷IPI%), não o PERCST cru.

    Medido no fornecedor 113 (pedido 565104): preço 1,7325 · VLIPI 0,0563 (3,25%) ·
    VLST 0,3587 · PERCST 20,05%. O efetivo sobre a mercadoria é 20,71% — a diferença é a
    majoração da base (MVA), que o fator já embute. Usar 20,05% subestimaria o ST."""
    trib = core.montar_tributacao(
        [_cab(565104, 113)],
        [_item(565104, 42248, periipi=3.25, vlipi=0.0563, percst=20.05, vlst=0.3587)],
        hoje=__import__("datetime").date(2026, 7, 27))
    ipi, st, _ = core.tributacao_de(trib, 113, 42248)
    assert ipi == 3.25
    assert st == pytest.approx(20.71, abs=0.02)      # e NÃO 20.05


def test_sem_ipi_na_linha_o_st_cai_no_percst_declarado():
    """Sem IPI não dá pra derivar o preço → usa PERCST (aproximação, nunca inventa imposto)."""
    trib = core.montar_tributacao([_cab(1, 999)],
                                  [_item(1, 50, periipi=0.0, percst=18.0, vlst=2.0)])
    _, st, _ = core.tributacao_de(trib, 999, 50)
    assert st == 18.0


def test_historico_so_manda_quando_nao_ha_figura_nem_cadastro():
    """O histórico foi REBAIXADO na cascata (era a fonte primária) — decisão por medição.

    Ele parecia bom em janelas de 30d (82%), mas isso media o passado: quando o redutor de 35%
    do IPI caiu em 21/07/2026, o histórico seguiu prevendo a alíquota velha (9,75 em vez de 15)
    por semanas, e o acerto real para o PRÓXIMO pedido despencou para 53%. A tributação de
    entrada do ERP muda antes do histórico — por isso ela é a primária e o histórico virou o
    último degrau antes do zero, para item sem figura e sem cadastro."""
    trib = core.montar_tributacao([_cab(10, 9745)], [_item(10, 777, periipi=0.0)])
    ipi, _, fonte = core.tributacao_de(trib, 9745, 777, percipi_cadastro=None)
    assert ipi == 0.0 and fonte == "pedido_real"


def test_produto_novo_herda_o_perfil_do_fornecedor_pela_moda():
    """Produto que o fornecedor nunca vendeu ainda: usa a MODA das linhas dele (não a média —
    o fornecedor que cobra 15% em tudo deve projetar 15%, mesmo com uma linha isenta no meio)."""
    itens = [_item(20, i, periipi=15.0) for i in range(5)] + [_item(20, 99, periipi=0.0)]
    trib = core.montar_tributacao([_cab(20, 8579)], itens)
    ipi, _, fonte = core.tributacao_de(trib, 8579, 12345)
    assert ipi == 15.0 and fonte == "perfil_fornecedor"


def test_fornecedor_desconhecido_cai_no_cadastro_e_depois_no_zero():
    trib = core.montar_tributacao([], [])
    assert core.tributacao_de(trib, 1, 1, percipi_cadastro=5.0) == (5.0, 0.0, "cadastro")
    assert core.tributacao_de(trib, 1, 1) == (0.0, 0.0, "sem_dado")
    assert core.tributacao_de(None, 1, 1) == (0.0, 0.0, "sem_dado")


def test_aliquota_do_par_e_a_moda_nao_a_do_ultimo_pedido():
    """A alíquota do MESMO produto oscila (a GALVANOTEK alterna 9,75% e 15% no cód. 42334 entre
    pedidos da mesma semana), então o último pedido pode ser o atípico. Medido em 1.487 linhas
    fora da amostra: moda acerta 86,4% × 85,7% do "último vence"."""
    trib = core.montar_tributacao(
        [_cab(100, 7), _cab(200, 7), _cab(300, 7)],
        [_item(100, 5, periipi=9.75), _item(200, 5, periipi=9.75), _item(300, 5, periipi=15.0)])
    assert core.tributacao_de(trib, 7, 5)[0] == 9.75

def test_empate_de_aliquota_resolve_pelo_maior():
    """Conservador de propósito: no empate, projeta o imposto MAIOR — subestimar o consumo da
    meta é o erro que causou este trabalho."""
    trib = core.montar_tributacao([_cab(1, 7), _cab(2, 7)],
                                  [_item(1, 5, periipi=9.75), _item(2, 5, periipi=15.0)])
    assert core.tributacao_de(trib, 7, 5)[0] == 15.0


def test_pedido_fora_da_janela_nao_entra():
    """Só pedidos dentro de `dias` contam — tributação velha não deve mandar na sugestão."""
    import datetime
    trib = core.montar_tributacao([_cab(1, 7, "2020-01-01")], [_item(1, 5, periipi=15.0)],
                                  hoje=datetime.date(2026, 7, 27), dias=180)
    assert core.tributacao_de(trib, 7, 5) == (0.0, 0.0, "sem_dado")


# ─────────────────────── o valor sugerido nas duas réguas ───────────────────────
def _produtos_um_item(trib_map=None, percipi_cad=None):
    """Um produto com giro e estoque zerado → sugestão > 0, caixa de 100 un."""
    snap = [{"CODPROD": 45017, "codfilial": "3", "qtestger": 0, "qtbloq": 0, "qtreserv": 0,
             "giro_m1": 300, "giro_m2": 300, "giro_m3": 300, "custofin": 1.3204750}]
    cad = {45017: {"CODPROD": 45017, "DESCRICAO": "EMB.GALV.G32", "CODFORNEC": 8579,
                   "QTUNITCX": 100, "PERCIPI": percipi_cad, "EMBALAGEM": "CX/0100/UN"}}
    return core.construir_produtos(snap, {}, cad, {}, {}, {}, core.merge_params({}),
                                   tributacao_map=trib_map)


def test_valor_sugerido_usa_o_custo_do_DOCUMENTO_e_nao_o_custo_cru():
    """Fix da divergência de R$ 0,10: a tela tem de somar o MESMO custo (4 casas) que vira
    preço no PDF/planilha. Com custofin 1,3204750 → custo_unit 1,3205; 200 un ⇒ R$ 264,10
    (o valor do 211), não R$ 264,09 que a soma com o custo cru produzia."""
    p = _produtos_um_item()[0]
    assert p["custo_unit"] == 1.3205
    esperado = p["sugestao_cx"] * p["caixa"] * p["custo_unit"]
    assert p["valor_sugerido_liq"] == pytest.approx(round(esperado, 2), abs=0.005)


def test_valor_sugerido_nf_soma_ipi_e_st_sobre_a_mercadoria():
    trib = core.montar_tributacao([_cab(1, 8579)],
                                  [_item(1, 45017, periipi=15.0, vlipi=0.19807)])
    p = _produtos_um_item(trib)[0]
    assert p["perc_ipi"] == 15.0 and p["trib_fonte"] == "pedido_real"
    assert p["valor_sugerido_nf"] == pytest.approx(p["valor_sugerido_liq"] * 1.15, abs=0.02)
    assert p["vl_ipi_sug"] == pytest.approx(p["valor_sugerido_liq"] * 0.15, abs=0.02)


def test_sem_tributacao_a_nf_iguala_a_mercadoria():
    """Instância sem histórico (ou fornecedor isento): nada muda — nenhum imposto inventado."""
    p = _produtos_um_item()[0]
    assert p["valor_sugerido_nf"] == p["valor_sugerido_liq"]
    assert (p["perc_ipi"], p["perc_st"]) == (0.0, 0.0)


def test_proporcao_do_pedido_real_565684():
    """Fecha com o 211: R$ 39.536,28 de mercadoria + R$ 5.445,73 de IPI = R$ 44.982,01 de NF.
    O IPI efetivo do pedido é 13,77% porque 10 dos 49 itens são isentos."""
    merc, ipi_real, nf = 39536.28, 5445.73, 44982.01
    assert round(merc + ipi_real, 2) == nf
    assert round(ipi_real / merc * 100, 2) == pytest.approx(13.77, abs=0.01)


# ─────────────────────── TRAVA: a planilha do Winthor não leva imposto ───────────────────────
def test_planilha_winthor_leva_preco_LIQUIDO_sem_imposto():
    """⚠️ NÃO "corrigir" isto somando IPI ao preço da planilha.

    O Winthor calcula o imposto SOZINHO na importação, a partir do cadastro: o JOGA mandou
    132,05 (preço líquido da caixa) no pedido 565684 e o ERP produziu os R$ 5.445,73 de IPI e a
    NF de R$ 44.982,01. Mandar o preço já com imposto faria o ERP aplicar IPI SOBRE IPI —
    pedido ~15% inflado e custo de entrada errado. `item_master` é a fonte única do preço do
    documento e tem de continuar devolvendo mercadoria pura."""
    qtd, preco, un = core.item_master(200, 100, 1.3205)
    assert (qtd, un) == (2, "CX")
    assert preco == pytest.approx(132.05, abs=0.001)      # líquido — NUNCA 151,86 (c/ IPI 15%)
    assert qtd * preco == pytest.approx(264.10, abs=0.01)


def test_item_master_preserva_o_valor_da_linha_na_conversao():
    """Invariante antigo que não pode quebrar: qtd × preço não muda ao converter p/ caixa."""
    for qtd_un, fator, custo in ((5700, 100, 0.465337), (13, 1, 45.7592), (200, 100, 1.3205)):
        q, p, _ = core.item_master(qtd_un, fator, custo)
        assert q * p == pytest.approx(qtd_un * custo, rel=1e-6)


def test_moda_desempata_pelo_maior():
    assert core._moda([]) == 0.0
    assert core._moda([15.0, 15.0, 0.0]) == 15.0
    assert core._moda([0.0, 15.0]) == 15.0        # empate → o maior (não subestima)
    assert core._moda([0.0, 0.0, 15.0]) == 0.0


# ─────────────────────── PDF: a totalização em 3 réguas ───────────────────────
def _pdf_texto(itens):
    """Gera o PDF do pedido e devolve (bytes, texto extraído). O texto vem do stream do
    ReportLab (sem dependência nova): basta p/ afirmar que os rótulos de total foram impressos."""
    from estoque.routes import _gerar_pdf_pedido
    pe = {"id": 45, "n_pedido": "45", "data_pedido": "2026-07-27", "fornecedor": "GALVANOTEK",
          "comprador": "JOAO VICTOR", "valor": sum(i["valor"] for i in itens), "status": "ABERTO"}
    blob = _gerar_pdf_pedido(pe, itens, forn=None)
    import base64
    import re as _re
    import zlib
    txt = []
    for m in _re.finditer(rb"stream\r?\n(.*?)endstream", blob, _re.S):
        dado = m.group(1).strip()
        # ReportLab grava ASCII85Decode + FlateDecode (o stream termina em "~>")
        for tentativa in (lambda d: zlib.decompress(base64.a85decode(d, adobe=True)),
                          zlib.decompress,
                          lambda d: d):
            try:
                txt.append(tentativa(dado).decode("latin-1"))
                break
            except Exception:
                continue
    return blob, "\n".join(txt)


def _item_pdf(cod, qtd, custo, percipi=0.0, percst=0.0):
    return {"codprod": cod, "descricao": f"PROD {cod}", "qtd": qtd, "qtunitcx": 100,
            "custo_unit": custo, "valor": round(qtd * custo, 2), "percipi": percipi,
            "percst": percst, "codfab": "550068", "embalagem": "CX/0100/UN", "peso_caixa": 8.0}


def test_pdf_com_imposto_imprime_produtos_ipi_e_total_nf():
    blob, txt = _pdf_texto([_item_pdf(45017, 5700, 0.465337, percipi=15.0)])
    assert blob[:4] == b"%PDF" and len(blob) > 1500
    for rotulo in ("PRODUTOS", "IPI", "TOTAL NF"):
        assert rotulo in txt, f"faltou o rótulo {rotulo} na totalização"
    assert "ST" not in txt.replace("PRODUTOS", "")   # sem ST no pedido → linha não aparece


def test_pdf_sem_imposto_mantem_uma_linha_de_total():
    """Pedido isento não ganha rodapé novo — documento antigo continua igual."""
    _, txt = _pdf_texto([_item_pdf(55183, 100, 1.348628)])
    assert "PRODUTOS" in txt
    assert "TOTAL NF" not in txt


def test_pdf_com_st_imprime_as_quatro_linhas():
    _, txt = _pdf_texto([_item_pdf(42248, 1000, 1.7325, percipi=3.25, percst=20.71)])
    for rotulo in ("PRODUTOS", "IPI", "ST", "TOTAL NF"):
        assert rotulo in txt


# ─────────── cascata com a TRIBUTAÇÃO DE ENTRADA do ERP (fonte primária) ───────────
# Os números vêm da tributação real medida no Oracle da Multpel (rotina 212):
#   52485 (CRISTALCOPO/SC) figura 88 -> IPI 10%   · cadastro dizia 6,75 (IPI de VENDA)
#   42315 (GALVANOTEK/RS)  figura 91 -> IPI 15%
#   42313 (GALVANOTEK/RS)  isento no cadastro     · a figura 91 diria 15, mas o ERP cobra 0
def _te():
    return core.montar_trib_entrada([
        {"CODPROD": 52485, "CODFILIAL": "3", "UFORIGEM": "SC", "TIPOFORNEC": "I",
         "PERIPI": 10.0, "PERCST": 0.0},
        {"CODPROD": 42315, "CODFILIAL": "3", "UFORIGEM": "RS", "TIPOFORNEC": "I",
         "PERIPI": 15.0, "PERCST": 0.0},
        {"CODPROD": 42313, "CODFILIAL": "3", "UFORIGEM": "RS", "TIPOFORNEC": "I",
         "PERIPI": 15.0, "PERCST": 0.0},
    ])


def test_trib_entrada_vence_o_cadastro_defasado():
    """O caso que originou tudo: cadastro 6,75 (IPI de venda) x ERP cobrou 10 (figura 88)."""
    ipi, st, fonte = core.tributacao_de(None, 10393, 52485, 6.75, trib_entrada=_te(),
                                        uf_fornec="SC")
    assert (ipi, fonte) == (10.0, "trib_entrada")


def test_isento_no_cadastro_vence_a_figura():
    """42313 e 42315 são do MESMO fornecedor, mesma UF e mesma figura (91 = 15%), mas o 42313
    saiu com IPI 0 no pedido real. Quem sabe disso é o cadastro: alíquota 0 = isento de fato
    (acerto de 97% nessa leitura). Por isso o isento é o 1º degrau, antes da figura."""
    assert core.tributacao_de(None, 8579, 42313, 0.0, trib_entrada=_te(), uf_fornec="RS") \
        == (0.0, 0.0, "isento_cadastro")
    assert core.tributacao_de(None, 8579, 42315, 15.0, trib_entrada=_te(), uf_fornec="RS")[0] == 15.0


def test_aliquota_muda_com_a_UF_DE_ORIGEM_do_fornecedor():
    """O mesmo produto tem figuras diferentes por origem (medido: cód. 42313 de SP → figura 91
    = 15%, de SC → figura 33 = 0%). Prever sem a UF do fornecedor erra por construção."""
    te = core.montar_trib_entrada([
        {"CODPROD": 777, "CODFILIAL": "3", "UFORIGEM": "SP", "TIPOFORNEC": "I", "PERIPI": 15.0, "PERCST": 0},
        {"CODPROD": 777, "CODFILIAL": "3", "UFORIGEM": "SC", "TIPOFORNEC": "I", "PERIPI": 5.0, "PERCST": 0},
    ])
    assert core.tributacao_de(None, 1, 777, 15.0, trib_entrada=te, uf_fornec="SP")[0] == 15.0
    assert core.tributacao_de(None, 1, 777, 15.0, trib_entrada=te, uf_fornec="SC")[0] == 5.0


def test_sem_figura_para_a_origem_cai_no_cadastro_e_marca_estimativa():
    """~5% dos itens não têm regra fiscal para aquela UF — é onde mora todo o erro residual.
    Tem de sair marcado como estimativa (trib_firme=False) p/ a tela avisar e o comprador
    poder corrigir o % antes de gerar o pedido."""
    ipi, _, fonte = core.tributacao_de(None, 8579, 99999, 6.5, trib_entrada=_te(), uf_fornec="MG")
    assert (ipi, fonte) == (6.5, "cadastro")
    assert fonte not in core.TRIB_FONTES_FIRMES


def test_fontes_firmes_sao_so_figura_e_isento():
    assert set(core.TRIB_FONTES_FIRMES) == {"isento_cadastro", "trib_entrada"}


def test_sem_trib_entrada_publicada_degrada_sem_quebrar():
    """Instância cujo TI ainda não publicou a TRIB_ENTRADA (ou a demo): a cascata continua
    entregando número pelo cadastro/histórico, só que marcado como estimativa."""
    trib = core.montar_tributacao([_cab(1, 8579)], [_item(1, 45017, periipi=15.0, vlipi=0.19)])
    ipi, _, fonte = core.tributacao_de(trib, 8579, 45017, None, trib_entrada={}, uf_fornec="RS")
    assert ipi == 15.0 and fonte == "pedido_real"


def test_produto_marca_a_confianca_da_aliquota():
    """`trib_firme` viaja até a tela e o modal do pedido."""
    snap = [{"CODPROD": 52485, "codfilial": "3", "qtestger": 0, "qtbloq": 0, "qtreserv": 0,
             "giro_m1": 300, "giro_m2": 300, "giro_m3": 300, "custofin": 1.0}]
    cad = {52485: {"CODPROD": 52485, "DESCRICAO": "POTE", "CODFORNEC": 10393,
                   "QTUNITCX": 100, "PERCIPI": 6.75}}
    forn = {10393: {"CODFORNEC": 10393, "ESTADO": "SC", "TIPOFORNEC": "I"}}
    p = core.construir_produtos(snap, {}, cad, forn, {}, {}, core.merge_params({}),
                                trib_entrada_map=_te())[0]
    assert p["perc_ipi"] == 10.0 and p["trib_fonte"] == "trib_entrada" and p["trib_firme"] is True
    assert p["valor_sugerido_nf"] == pytest.approx(p["valor_sugerido_liq"] * 1.10, abs=0.02)
