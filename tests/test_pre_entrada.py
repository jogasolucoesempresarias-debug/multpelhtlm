"""Gate do BO da PRÉ-ENTRADA (relatado pelo diretor em 07/2026).

Fluxo que causava o problema:
  1. pedido emitido            → PCITEM qtentregue=0  → item conta em "Já pedido"
  2. mercadoria chega (pré-entrada) → Winthor baixa qtentregue → SAI de "Já pedido"
  3. Winthor lança QTESTGER **e** QTBLOQUEADA        → disponível = 0
  4. app: sem estoque + sem pedido → "Ruptura s/ pedido" → **sugeria comprar de novo**

Medido no BI: 130 linhas, R$ 198.683 de mercadoria já no armazém (22 viravam ruptura falsa,
108 inflavam a sugestão em silêncio).
"""
from datetime import date, timedelta

import pytest

from estoque import core

HOJE = date(2026, 7, 28)


# ─────────────────────── a heurística ───────────────────────
def test_bloqueio_com_entrada_recente_e_transicao():
    assert core.qt_em_transicao(240, "2026-07-28", hoje=HOJE) == 240
    assert core.qt_em_transicao(240, "2026-07-22", hoje=HOJE) == 240      # 6 dias


def test_bloqueio_antigo_e_avaria_e_NAO_entra():
    """Os 122 itens de bloqueio velho são avaria real — precisam continuar sugerindo compra."""
    assert core.qt_em_transicao(240, "2026-05-01", hoje=HOJE) == 0
    assert core.qt_em_transicao(240, "2026-07-20", hoje=HOJE) == 0        # 8 dias > janela


def test_sem_bloqueio_ou_sem_data_nao_inventa_nada():
    assert core.qt_em_transicao(0, "2026-07-28", hoje=HOJE) == 0
    assert core.qt_em_transicao(240, None, hoje=HOJE) == 0
    assert core.qt_em_transicao(None, None, hoje=HOJE) == 0


def test_janela_e_configuravel():
    assert core.qt_em_transicao(10, "2026-07-18", hoje=HOJE, dias=15) == 10
    assert core.qt_em_transicao(10, "2026-07-18", hoje=HOJE, dias=3) == 0


# ─────────────────────── efeito no produto ───────────────────────
def _produto(qtbloq, dtultent, giro=300, qtestger=None, ja_pedida=None):
    """Item com giro e TODO o estoque bloqueado — o caso do 67856 (240 estger / 240 bloq)."""
    estger = qtestger if qtestger is not None else qtbloq
    snap = [{"CODPROD": 67856, "codfilial": "3", "qtestger": estger, "qtbloq": qtbloq,
             "qtreserv": 0, "giro_m1": giro, "giro_m2": giro, "giro_m3": giro,
             "custofin": 15.75, "dtultent": dtultent}]
    cad = {67856: {"CODPROD": 67856, "DESCRICAO": "BOB.PIC", "CODFORNEC": 1, "QTUNITCX": 60}}
    return core.construir_produtos(snap, {}, cad, {}, {}, {}, core.merge_params({}),
                                   hoje=HOJE, ja_pedida_map=ja_pedida or {})[0]


def test_pre_entrada_derruba_a_sugestao_de_compra():
    """O BO: mercadoria que já chegou não pode ser sugerida de novo."""
    com = _produto(240, "2026-07-28")     # chegou hoje, aguardando liberação
    sem = _produto(240, "2026-01-10")     # bloqueio velho (avaria)
    assert com["qt_transicao"] == 240
    assert sem["qt_transicao"] == 0
    assert com["sugestao_compra"] < sem["sugestao_compra"], \
        "a mercadoria em transição tem de abater a sugestão"


def test_transicao_NAO_mexe_no_disponivel_nem_no_valor_de_estoque():
    """Decisão de escopo: entra no ESTOQUE PROJETADO, não no `qtdisp`. A mercadoria ainda não
    pode ser vendida — então segue contando como ruptura e fora do valor de estoque. Mexer no
    qtdisp contaminaria valor de estoque, ABC, cobertura e a aba Ruptura inteira."""
    p = _produto(240, "2026-07-28")
    assert p["qtdisp"] == 0                    # continua indisponível
    assert p["valor"] == 0                     # não vira valor de estoque
    assert p["estoque_projetado"] == 240       # mas conta na projeção


def test_status_avisa_o_comprador_em_vez_de_sumir_com_o_item():
    """Não basta parar de sugerir: o comprador precisa entender por quê."""
    p = _produto(240, "2026-07-28")
    assert p["status_exec"] == "aguardando_liberacao"


def test_avaria_antiga_continua_como_ruptura_sem_pedido():
    p = _produto(240, "2026-01-10")
    assert p["status_exec"] == "ruptura_sem_pedido"


def test_item_com_estoque_parcial_bloqueado_tambem_desconta():
    """Os 108 itens que NÃO viravam ruptura, mas tinham a sugestão inflada em silêncio —
    metade do dinheiro do problema (R$ 115 mil de R$ 198 mil)."""
    com = _produto(100, "2026-07-28", qtestger=300)   # 200 disp + 100 em transição
    sem = _produto(100, "2026-01-10", qtestger=300)
    assert com["qtdisp"] == 200 and sem["qtdisp"] == 200
    assert com["estoque_projetado"] == 300 and sem["estoque_projetado"] == 200
    assert com["sugestao_compra"] < sem["sugestao_compra"]


def test_transicao_soma_com_o_ja_pedido_sem_duplicar():
    """Pedido em aberto + mercadoria em transição são coisas diferentes e somam."""
    p = _produto(100, "2026-07-28", qtestger=100, ja_pedida={67856: 50})
    assert p["qtd_ja_pedida"] == 50
    assert p["qt_transicao"] == 100
    assert p["estoque_projetado"] == 150       # 0 disp + 50 pedido + 100 transição


def test_cobertura_projetada_reflete_a_mercadoria_que_chegou():
    com = _produto(240, "2026-07-28")
    sem = _produto(240, "2026-01-10")
    assert com["cobertura_proj"] is not None
    assert sem["cobertura_proj"] in (None, 0) or sem["cobertura_proj"] < com["cobertura_proj"]
