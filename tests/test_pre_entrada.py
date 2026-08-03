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


# ─────────── falsos positivos de AVARIA (reportados pelo diretor em 08/2026) ───────────
# "esses itens estão aparecendo como pré-entrada, porém eles estão na avaria... estou verificando
# se mudou alguma rotina de lançamento aqui". Não mudou rotina nenhuma: eram dois furos do
# próprio cálculo, e os dois estão nas linhas POR FILIAL que a heurística agregada não via.
def test_avaria_de_uma_filial_NAO_vira_pre_entrada_por_entrada_em_outra():
    """O furo nº 1, e o mais grave: o snapshot agrega `SUM(QTBLOQUEADA)` com `MAX(DTULTENT)`.

    No Atacado (filiais 3+5), uma entrada recente na Matriz carimbava como "chegando" uma avaria
    velha parada no Depósito — bloqueio de uma filial casado com a data de outra. Nenhum limiar
    de dias conserta isso: o pareamento é que estava errado.
    """
    linhas = [{"qtbloq": 8, "dtultent": "2026-07-28", "qtultent": 8},     # filial 3: chegou hoje
              {"qtbloq": 200, "dtultent": "2026-01-15", "qtultent": 200}]  # filial 5: avaria velha
    # agregado (o que o app via): 208 bloqueadas + MAX(dtultent)=hoje → tudo vira "chegando"
    assert core.qt_em_transicao(208, "2026-07-28", hoje=HOJE) == 208
    # pareado filial a filial: só as 8 que de fato entraram
    assert core.qt_em_transicao(208, "2026-07-28", hoje=HOJE, linhas=linhas) == 8


def test_bloqueio_maior_que_a_ultima_entrada_e_avaria_no_excedente():
    """O furo nº 2: `DTULTENT` é a data da última entrada de QUALQUER natureza, e não prova que
    ela explique o que está bloqueado hoje. 200 un bloqueadas com uma entrada de 12 un no mesmo
    dia = 12 podem ser pré-entrada, 188 são avaria. É TETO, não filtro: descartar a linha inteira
    jogaria fora a parte legítima e traria de volta o BO original (comprar o que já chegou)."""
    linhas = [{"qtbloq": 200, "dtultent": "2026-07-27", "qtultent": 12}]
    assert core.qt_em_transicao(200, "2026-07-27", hoje=HOJE, linhas=linhas) == 12
    # entrada maior que o bloqueio não inventa transição além do que está bloqueado
    assert core.qt_em_transicao(12, "2026-07-27", hoje=HOJE,
                                linhas=[{"qtbloq": 12, "dtultent": "2026-07-27",
                                         "qtultent": 500}]) == 12


def test_sem_qtultent_o_teto_nao_se_aplica():
    """Base sem a coluna (demo antiga) não pode zerar a pré-entrada por falta de dado — seria
    trocar um erro por outro, e o desta direção é o BO original."""
    linhas = [{"qtbloq": 200, "dtultent": "2026-07-27", "qtultent": None}]
    assert core.qt_em_transicao(200, "2026-07-27", hoje=HOJE, linhas=linhas) == 200


def test_sem_linhas_cai_no_modo_agregado_antigo():
    """Query de bloqueio por filial fora do ar → degrada, não quebra."""
    assert core.qt_em_transicao(240, "2026-07-28", hoje=HOJE, linhas=None) == 240
    assert core.qt_em_transicao(240, "2026-07-28", hoje=HOJE, linhas=[]) == 240


# ─────────────────────── efeito no produto ───────────────────────
def _produto(qtbloq, dtultent, giro=300, qtestger=None, ja_pedida=None, bloqueio=None):
    """Item com giro e TODO o estoque bloqueado — o caso do 67856 (240 estger / 240 bloq)."""
    estger = qtestger if qtestger is not None else qtbloq
    snap = [{"CODPROD": 67856, "codfilial": "3", "qtestger": estger, "qtbloq": qtbloq,
             "qtreserv": 0, "giro_m1": giro, "giro_m2": giro, "giro_m3": giro,
             "custofin": 15.75, "dtultent": dtultent}]
    cad = {67856: {"CODPROD": 67856, "DESCRICAO": "BOB.PIC", "CODFORNEC": 1, "QTUNITCX": 60}}
    return core.construir_produtos(snap, {}, cad, {}, {}, {}, core.merge_params({}),
                                   hoje=HOJE, ja_pedida_map=ja_pedida or {},
                                   bloqueio_map={67856: bloqueio} if bloqueio else None)[0]


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


# ─────── "sem pedido" × pré-entrada (achado do diretor 08/2026, curva A: 4 × 3) ───────
def _p_rupt(cod, ja_pedida=0.0, transicao=0.0, curva="A"):
    """Item em ruptura (sem estoque, com giro) com a providência variando."""
    return {"codprod": cod, "codcomprador": 7, "comprador": "JOAO", "curva_abc": curva,
            "qtdisp": 0, "giro_dia": 5.0, "qtd_ja_pedida": ja_pedida, "qt_transicao": transicao,
            "venda_perdida": 100.0, "sugestao_cx": 0, "valor_sugerido_liq": 0.0,
            "valor_sugerido_nf": 0.0}


def test_pre_entrada_sai_do_sem_pedido_mas_continua_em_ruptura():
    """O 4 × 3: a aba Ruptura contava 4 "sem pedido" na curva A e o drill (Estoque zerado,
    filtro "Ruptura s/ pedido") mostrava 3 — o 4º estava em pré-entrada. `status_exec` já
    tratava pré-entrada como estado exclusivo; a agregação de ruptura, não.

    O item continua em RUPTURA (não há estoque vendável, a venda perdida é real). O que ele
    deixa de ser é risco de omissão — que é o que "sem pedido" mede e o que a meta cobra.
    """
    produtos = [_p_rupt(1), _p_rupt(2), _p_rupt(3),          # sem nada: risco real
                _p_rupt(4, transicao=120)]                   # chegou, aguardando liberação
    g = core.ruptura_por_comprador(produtos)[0]
    assert g["n_ruptura"] == 4, "os 4 seguem em ruptura — falta estoque de fato"
    assert g["n_sem_pedido"] == 3, "o item em pré-entrada não é omissão do comprador"


def test_sem_providencia_cobre_as_duas_formas_de_providencia():
    assert core._sem_providencia(_p_rupt(1)) is True
    assert core._sem_providencia(_p_rupt(2, ja_pedida=50)) is False     # pedido em aberto
    assert core._sem_providencia(_p_rupt(3, transicao=50)) is False     # já no armazém


def test_ruptura_e_estoque_zerado_contam_o_mesmo():
    """A regra de ouro do bug: `status_exec` (tela Estoque zerado) e a agregação da aba Ruptura
    são duas implementações do MESMO conceito. Este teste amarra as duas partindo dos MESMOS
    produtos — se alguém mexer numa e esquecer a outra, o "4 × 3" volta."""
    zerado = _produto(0, None, qtestger=0)                          # sem estoque, sem bloqueio
    pre = _produto(240, "2026-07-28", qtestger=240,
                   bloqueio=[{"qtbloq": 240, "dtultent": "2026-07-28", "qtultent": 240}])
    assert zerado["status_exec"] == "ruptura_sem_pedido"
    assert pre["status_exec"] == "aguardando_liberacao"
    g = core.ruptura_por_comprador([zerado, pre])[0]
    # a contagem "sem pedido" tem de bater com quantos itens têm status_exec == ruptura_sem_pedido
    esperado = sum(1 for p in (zerado, pre) if p["status_exec"] == "ruptura_sem_pedido")
    assert g["n_sem_pedido"] == esperado == 1
    assert g["n_ruptura"] == 2


def test_item_de_avaria_volta_a_sugerir_compra_com_a_posicao_por_filial():
    """Ponta a ponta do caso reportado (HIPERROLL, 08/2026): a coluna "Avaria" do Explorador e o
    "+N" de já-pedido no Abastecimento mostravam A MESMA quantidade — o mesmo `QTBLOQUEADA` lido
    como duas coisas contraditórias. Com a posição por filial, a avaria volta a ser avaria: o
    item conta como ruptura e a compra é sugerida de novo, que é o comportamento correto para
    mercadoria danificada."""
    linhas = [{"qtbloq": 41, "dtultent": "2026-01-15", "qtultent": 41}]   # avaria velha
    p = _produto(41, "2026-07-28", bloqueio=linhas)   # snapshot agregado diria "chegou hoje"
    assert p["qt_transicao"] == 0
    assert p["status_exec"] == "ruptura_sem_pedido"
    assert p["sugestao_compra"] > 0, "avaria não pode abater a compra"


def test_cobertura_projetada_reflete_a_mercadoria_que_chegou():
    com = _produto(240, "2026-07-28")
    sem = _produto(240, "2026-01-10")
    assert com["cobertura_proj"] is not None
    assert sem["cobertura_proj"] in (None, 0) or sem["cobertura_proj"] < com["cobertura_proj"]
