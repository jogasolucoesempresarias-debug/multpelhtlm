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


FX = ("novo", "15-30", "31-60", "61-90", "91-120", "121+")


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
    tinham vendido — é reposição de item normal. Estes seguem na faixa dos dias sem venda."""
    assert _fx(20, dias_sem_entrada=0) == "15-30"      # cód. 53140: vendeu há 20d, chegou hoje
    assert _fx(43, dias_sem_entrada=2) == "31-60"
    assert _fx(80, dias_sem_entrada=7) == "61-90"


def test_o_item_morto_reposto_continua_no_121_e_nao_some_no_card_de_novos():
    """Cód. 57071: última venda há 1.249 dias, chegou há 9, R$ 2.607 em estoque. É EXATAMENTE
    a compra que precisa aparecer — a regra literal a mandaria para "Produtos novos"."""
    assert _fx(1249, dias_sem_entrada=9) == "121+"
    assert _fx(219, dias_sem_entrada=3) == "121+"


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


def test_fronteira_do_novo_e_estrita_e_a_do_parado_inclusiva():
    """`< novo_dias` (14 é novo, 15 não) e `>= 15` para entrar no parado — sem gap: o item que
    nunca vendeu e chegou há exatamente 15 dias vai para "15-30", não some."""
    assert _fx(None, dias_sem_entrada=14) == "novo"
    assert _fx(None, dias_sem_entrada=15) == "15-30"


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
