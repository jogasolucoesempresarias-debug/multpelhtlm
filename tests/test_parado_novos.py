"""Gate do card "Produtos novos" no Estoque parado — pedido do diretor 08/2026:
"os produtos novos estão caindo como itens parados, sem venda… hoje eles vão para 121+".

Causa: `dias_sem_venda is None` (nunca vendeu) virava 10**9 e caía direto em "121+", mesmo
que a mercadoria tivesse chegado ontem.

O que estes testes travam:
1. quem nunca vendeu conta os dias a partir da ENTRADA (é o conserto de verdade — sem ele o
   121+ segue com item que chegou há 40 dias rotulado "parado 121+ dias");
2. a regra exige NUNCA TER VENDIDO, não só entrada recente — a leitura literal do pedido
   ("tudo que chegou há <15d") escondia reposição de item morto atrás do rótulo "novo";
3. a invariante da aba: as faixas continuam SOMANDO o total;
4. `status_parado` (Cockpit) anda junto com `parado_faixa` (aba) — a armadilha da Ruptura,
   em que uma das implementações do mesmo conceito ficou para trás;
5. "novo" NÃO é capital parado (`core.eh_parado`);
6. a janela é parâmetro, e 0 não pode esvaziar o card em silêncio.
"""
import pytest

from estoque import core


FX = ("novo", "recem_chegado", "15-30", "31-60", "61-90", "91-120", "121+")


def _fx(dias_sem_venda, dias_sem_entrada=None, qtdisp=10.0, novo_dias=15):
    return core.parado_faixa_de(dias_sem_venda, qtdisp, dias_sem_entrada, novo_dias)


# ───────────────── 1. nunca vendeu conta a partir da ENTRADA ─────────────────

def test_nunca_vendeu_e_chegou_agora_vira_novo():
    """O caso do pedido: cód. 69779, entrada há 4 dias, nunca vendeu — dizia 121+."""
    assert _fx(None, dias_sem_entrada=4) == "novo"
    assert _fx(None, dias_sem_entrada=14) == "novo"


def test_nunca_vendeu_e_chegou_ha_tempo_cai_na_faixa_VERDADEIRA_nao_no_121():
    """Item que nunca vendeu e entrou há 40 dias não está parado há 121+ dias — é impossível.
    Medido no BI real: 8 itens em 121+ estavam nesta situação (entrada de 15 a 90 dias)."""
    assert _fx(None, dias_sem_entrada=20) == "15-30"
    assert _fx(None, dias_sem_entrada=40) == "31-60"
    assert _fx(None, dias_sem_entrada=75) == "61-90"
    assert _fx(None, dias_sem_entrada=100) == "91-120"
    assert _fx(None, dias_sem_entrada=400) == "121+"     # esse sim é dead stock de verdade


def test_nunca_vendeu_sem_data_de_entrada_continua_em_121():
    """Lado conservador: sem data não dá para provar que é novo — na dúvida ele APARECE."""
    assert _fx(None, dias_sem_entrada=None) == "121+"


# ───────── 2. entrada recente NÃO basta: tem de nunca ter vendido ─────────

def test_item_que_ja_vendeu_nao_vira_novo_por_ter_sido_REPOSTO():
    """A leitura literal ("chegou há <15 dias") pegava 85 itens no BI real, 75 dos quais já
    tinham vendido — é reposição de item normal. Estes seguem na faixa dos dias sem venda.

    ⚠️ Continua valendo ABAIXO do piso de 60 dias: item que vendeu há 3 semanas e recebeu
    mercadoria hoje é rotação de curva C, não compra a rever. É esse piso que separa os dois."""
    assert _fx(20, dias_sem_entrada=0) == "15-30"      # cód. 53140: vendeu há 20d, chegou hoje
    assert _fx(43, dias_sem_entrada=2) == "31-60"
    assert _fx(59, dias_sem_entrada=0) == "31-60"      # 59 < 60: ainda não é dead stock


def test_o_item_morto_reposto_NAO_vira_novo_e_ganha_caixa_PROPRIA():
    """Cód. 57071: última venda há 1.249 dias, chegou há 9, R$ 2.607 em estoque.

    ⚠️ Este teste mudou de resposta em 08/2026 e o MOTIVO importa. Antes ele travava
    `== "121+"`, porque a alternativa em cima da mesa era mandá-lo para "Produtos novos" — e
    chamar de novo um item parado há 3,4 anos escondia exatamente a compra que precisa aparecer.

    O diretor então pediu a régua certa ("mudar só os itens que chegaram recentemente e a última
    venda é maior que 60 dias"). A garantia que este teste sempre defendeu — **ele não pode ser
    chamado de NOVO** — continua travada; o que mudou é que agora ele tem caixa própria em vez de
    ficar no 121+ inflando um capital parado cujo dinheiro chegou anteontem."""
    assert _fx(1249, dias_sem_entrada=9) == core.PARADO_RECEM_CHEGADO
    assert _fx(219, dias_sem_entrada=3) == core.PARADO_RECEM_CHEGADO
    # a garantia original, intacta: NUNCA "novo"
    assert _fx(1249, dias_sem_entrada=9) != core.PARADO_NOVO
    # e sem entrada recente ele continua onde sempre esteve
    assert _fx(1249, dias_sem_entrada=90) == "121+"


# ───────────────── 3. invariantes da aba ─────────────────

def test_sem_estoque_e_menos_de_15_dias_seguem_fora_do_parado():
    assert _fx(200, dias_sem_entrada=300, qtdisp=0) is None      # sem estoque
    assert _fx(0, dias_sem_entrada=1) is None                    # vendeu hoje
    assert _fx(14, dias_sem_entrada=1) is None                   # 14d < 15d


def test_faixas_somam_o_total_e_nenhum_item_fica_sem_caixa():
    """A aba escreve "As faixas somam o total" — o card Novos não pode criar buraco nem
    sobreposição. Varre uma grade de combinações e confere partição."""
    universo = []
    for dsv in (None, 0, 14, 15, 30, 31, 60, 61, 90, 91, 120, 121, 400):
        for dse in (None, 0, 3, 14, 15, 40, 90, 200):
            universo.append((dsv, dse))
    faixas = [_fx(dsv, dse) for dsv, dse in universo]
    dentro = [f for f in faixas if f is not None]
    assert set(dentro) <= set(FX), f"faixa desconhecida: {set(dentro) - set(FX)}"
    # cada item cai em EXATAMENTE uma faixa (a função devolve um valor só) e o total fecha
    assert sum(faixas.count(f) for f in FX) == len(dentro)


def test_a_fronteira_de_novo_dias_e_INCLUSIVA_e_igual_nas_duas_caixas():
    """"Chegaram até 20 dias" (diretor, 08/2026) inclui o 20 — então `<= novo_dias`.

    ⚠️ E as DUAS caixas usam a mesma fronteira. Antes o `novo` era `<` e o recorte novo nasceria
    `<=`: dois cards vizinhos com réguas de borda diferentes é o defeito que já produziu os
    R$ 26,79 do item de 60 dias exatos entre `parado_faixa_de` e `status_parado_de`. Sem gap:
    quem chega no dia seguinte à janela cai na faixa real, não some."""
    assert _fx(None, dias_sem_entrada=15) == "novo"          # nunca vendeu, no limite
    assert _fx(None, dias_sem_entrada=16) == "15-30"         # 1 dia depois, faixa real
    assert _fx(300, dias_sem_entrada=15) == core.PARADO_RECEM_CHEGADO   # já vendeu, no limite
    assert _fx(300, dias_sem_entrada=16) == "121+"           # 1 dia depois, volta ao parado


# ───────────────── 4. o Cockpit anda junto com a aba ─────────────────

def _produto(dtultsaida, dtultent, hoje, qtestger=10.0):
    return {"CODPROD": 1, "qtestger": qtestger, "qtreserv": 0, "qtbloq": 0,
            "giro_m1": 0, "giro_m2": 0, "giro_m3": 0, "custofin": 1.0,
            "dtultsaida": dtultsaida, "dtultent": dtultent}


def _monta(dtultsaida, dtultent, hoje, params=None):
    prod = core.construir_produtos(
        [_produto(dtultsaida, dtultent, hoje)], {}, {1: {"CODPROD": 1, "DESCRICAO": "X",
                                                        "CODFORNEC": 1, "REVENDA": "S"}},
        {}, {}, {}, core.merge_params(params or {}), hoje=hoje)
    return prod[0]


def test_status_parado_e_parado_faixa_concordam_no_produto_novo():
    """Armadilha da Ruptura: mexer num eixo e esquecer o outro. A aba lê `parado_faixa` e o
    Cockpit lê `status_parado` — os dois têm de dizer 'novo' para o mesmo item."""
    from datetime import date
    hoje = date(2026, 8, 14)
    p = _monta(None, "2026-08-10", hoje)          # nunca vendeu, chegou há 4 dias
    assert p["parado_faixa"] == "novo"
    assert p["status_parado"] == "novo"


def test_status_parado_de_quem_nunca_vendeu_e_chegou_ha_tempo_nao_e_mais_120():
    from datetime import date
    hoje = date(2026, 8, 14)
    p = _monta(None, "2026-07-05", hoje)          # nunca vendeu, chegou há 40 dias
    assert p["parado_faixa"] == "31-60"
    assert p["status_parado"] is None             # 40d não atinge nem o "atencao" (60d)
    velho = _monta(None, "2025-01-10", hoje)      # nunca vendeu, chegou há mais de 1 ano
    assert velho["parado_faixa"] == "121+"
    assert velho["status_parado"] == "muito_critico"


# ───────────────── 5. "novo" não é capital parado ─────────────────

def test_eh_parado_exclui_o_novo():
    assert core.eh_parado({"status_parado": "muito_critico"}) is True
    assert core.eh_parado({"status_parado": "atencao"}) is True
    assert core.eh_parado({"status_parado": "novo"}) is False
    assert core.eh_parado({"status_parado": None}) is False


def test_cockpit_nao_soma_o_novo_no_capital_parado_nem_no_alerta_120():
    from datetime import date
    hoje = date(2026, 8, 14)
    novo = _monta(None, "2026-08-12", hoje)
    velho = _monta(None, "2024-01-01", hoje)
    ck = core.cockpit([novo, velho])
    assert ck["parado"]["novo"]["qt"] == 1
    assert ck["parado"]["muito_critico"]["qt"] == 1                 # só o velho
    assert ck["valor_parado"] == velho["valor"], "o novo entrou no capital parado"


# ───────────────── 6. a janela é parâmetro ─────────────────

def test_janela_configuravel():
    assert _fx(None, dias_sem_entrada=20, novo_dias=30) == "novo"
    assert _fx(None, dias_sem_entrada=20, novo_dias=15) == "15-30"


def test_default_continua_15():
    assert core.merge_params({})["novo_dias"] == 15


@pytest.mark.parametrize("valor", ["0", "-5", "abc", ""])
def test_clamp_impede_janela_que_esvazia_o_card_em_silencio(valor):
    """`novo_dias=0` não erraria alto: só devolveria os itens ao 121+ sem avisar."""
    assert core.merge_params({"novo_dias": valor})["novo_dias"] >= 1


# ───────── 7. TODO consumidor de "capital parado" usa a mesma régua (08/2026) ─────────
# O `eh_parado` nasceu como fonte única, mas dois chamadores ficaram testando a VERDADE do
# campo (`if status_parado:`) — e `novo` é truthy. Resultado medido: R$ 15.000 contra R$ 5.000
# no Cockpit, para os mesmos dois itens. Os dois pontos cegos eram justamente os que o gate
# original não cobria (ele travava `cockpit` e `eh_parado`, não estes).

def _par_novo_e_velho(hoje):
    """(novo, velho) — um item que nunca vendeu e chegou anteontem, e um dead stock real."""
    return _monta(None, "2026-08-12", hoje), _monta(None, "2024-01-01", hoje)


def test_por_comprador_nao_soma_o_novo_no_capital_parado():
    """Sai no relatório "Compradores" (export + email): divergia do Cockpit na mão do cliente."""
    from datetime import date
    hoje = date(2026, 8, 14)
    novo, velho = _par_novo_e_velho(hoje)
    linha = core.por_comprador([novo, velho])[0]
    assert linha["valor_parado"] == velho["valor"], "o `novo` entrou no capital parado"
    assert linha["valor_parado"] == core.cockpit([novo, velho])["valor_parado"], \
        "por_comprador e cockpit têm de falar o mesmo número"


def test_resumo_do_fornecedor_usa_a_mesma_regua_do_export_da_aba():
    """Drawer 360° do fornecedor × export da MESMA aba Fornecedores: dois números para o mesmo
    fornecedor era o sintoma. O export sempre usou `eh_parado`; o drawer não."""
    from datetime import date
    from estoque import routes
    hoje = date(2026, 8, 14)
    novo, velho = _par_novo_e_velho(hoje)
    itens = [novo, velho]
    # `_resumo_fornecedor` consulta `_compradores_map()` (Power BI); neutralizado p/ o teste
    orig = routes._compradores_map
    routes._compradores_map = lambda: {}
    try:
        r = routes._resumo_fornecedor(1, itens, {}, {}, None)
    finally:
        routes._compradores_map = orig
    assert r["valor_parado"] == velho["valor"], "o `novo` entrou no capital parado do drawer"
    # a régua do export da aba Fornecedores (routes._export_data, view="fornecedores")
    export = core._round(sum(p.get("valor") or 0 for p in itens if core.eh_parado(p)))
    assert r["valor_parado"] == export, "drawer e export divergem para o mesmo fornecedor"


# ═════════ 7. "Recém-chegados sem giro" (08/2026, pedido do diretor) ═════════
# "não precisa mudar a regra toda, temos que mudar só os itens que chegaram recentemente e a
# última venda é maior que 60 dias".
#
# O caso que originou: cód. 59289 (MILHO VERDE PREDILECTA), última venda 14/10/2025 (317 dias),
# 1.632 un recebidas há 3 dias, R$ 24.438,68 — sozinho, 26,7% do 121+ da carteira do comprador.
# A faixa estava CERTA pela régua de venda e ERRADA como capital parado: valor × tempo, e aquele
# dinheiro tinha 3 dias de casa.

def test_o_caso_59289_sai_do_121_com_nome_proprio():
    """Vendeu há 317 dias, recebeu mercadoria há 3: não é dead stock de 121+ nem produto novo."""
    assert _fx(317, dias_sem_entrada=3) == core.PARADO_RECEM_CHEGADO
    assert _fx(317, dias_sem_entrada=3) != core.PARADO_NOVO
    assert _fx(317, dias_sem_entrada=3) != "121+"


def test_o_piso_de_60_dias_protege_a_rotacao_normal():
    """A régua SEM o piso moveria 146 itens / R$ 172.176,33 no BI real, levando junto item
    saudável — o 51312 gira 376/mês, tem 51 dias de cobertura e só ficou 22 dias sem vender.
    Descer este piso exige refazer aquela medição, não é ajuste de gosto."""
    for dsv in (15, 20, 30, 45, 59):
        assert _fx(dsv, dias_sem_entrada=0) != core.PARADO_RECEM_CHEGADO, \
            f"{dsv} dias sem venda não é dead stock — é rotação"
    assert _fx(60, dias_sem_entrada=0) == core.PARADO_RECEM_CHEGADO   # piso inclusivo


def test_recem_chegado_NAO_e_capital_parado_nos_dois_eixos():
    """`parado_faixa` (aba) e `status_parado` (Cockpit) têm de andar juntos — foi assim que a
    Ruptura terminou com três implementações do mesmo conceito fora de sincronia."""
    st = core.status_parado_de(317, 10.0, dias_sem_entrada=3, novo_dias=15)
    assert st == core.PARADO_RECEM_CHEGADO
    assert core.eh_parado({"status_parado": st}) is False
    assert _fx(317, dias_sem_entrada=3) == st, "os dois eixos discordam"


def test_as_duas_caixas_sao_DISJUNTAS():
    """`novo` = nunca vendeu; `recem_chegado` = já vendeu. Nenhum item pode ser os dois — se um
    dia puderem, os cards somam o mesmo item e a aba passa do valor de estoque."""
    for dse in (0, 3, 15):
        assert _fx(None, dias_sem_entrada=dse) == core.PARADO_NOVO
        for dsv in (60, 317, 3934):
            assert _fx(dsv, dias_sem_entrada=dse) == core.PARADO_RECEM_CHEGADO


def test_sem_data_de_entrada_o_item_parado_NAO_escapa():
    """Lado conservador, igual ao do `novo`: sem saber quando chegou, ele aparece como parado."""
    assert _fx(317, dias_sem_entrada=None) == "121+"
    assert core.status_parado_de(317, 10.0, None, 15) == "muito_critico"


def test_nao_invade_a_watchlist_de_desaceleracao():
    """A watchlist é 20-59 dias e o `recem_chegado` começa em 60 — disjuntos por construção.
    Se um dia se tocarem, "parado + desacelerando + recém-chegado" passa do valor de estoque."""
    p = {"status_parado": core.PARADO_RECEM_CHEGADO, "dias_sem_venda": 317,
         "qtdisp": 10.0, "cobertura_dias": 9999, "valor": 24438.68}
    assert core.em_desaceleracao(p) is False


def test_a_janela_e_o_MESMO_parametro_do_card_novos():
    """Uma janela só para as duas caixas: `novo_dias` do ⚙ Parâmetros. Dois parâmetros para o
    mesmo conceito era como as faixas de cobertura acabaram com duas convenções."""
    assert _fx(317, dias_sem_entrada=18, novo_dias=15) == "121+"
    assert _fx(317, dias_sem_entrada=18, novo_dias=20) == core.PARADO_RECEM_CHEGADO
    assert _fx(None, dias_sem_entrada=18, novo_dias=20) == core.PARADO_NOVO


def test_o_capital_parado_do_cockpit_CAI_e_e_de_proposito():
    """⚠️ Gate de consciência: isto AFROUXA o placar sem ninguém mexer na operação. Medido no BI
    real (27/08/2026, Atacado): capital parado R$ 206.035,96 → R$ 154.821,86 (−24,9%) e 121+
    R$ 128.818,73 → R$ 87.569,54 (−32,0%). Se este teste cair, a decisão foi revertida sem querer."""
    from datetime import date
    hoje = date(2026, 8, 14)
    # item que vendeu há 300 dias e recebeu mercadoria há 2 → sai do capital parado
    p = _monta("2025-10-18", "2026-08-12", hoje)
    assert p["status_parado"] == core.PARADO_RECEM_CHEGADO
    assert core.cockpit([p])["valor_parado"] == 0, "o recém-chegado entrou no capital parado"
    # o MESMO item, sem a entrada recente, continua contando
    q = _monta("2025-10-18", "2025-11-01", hoje)
    assert core.eh_parado(q) is True
    assert core.cockpit([q])["valor_parado"] == q["valor"]
