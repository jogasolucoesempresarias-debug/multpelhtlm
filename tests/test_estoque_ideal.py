"""Gate do 'Estoque ideal' (Painel gerencial) virar PARAMETRIZÁVEL — pedido do diretor 07/2026:
os dois números (limiar de dias e meta %) estavam cravados e precisam ser calibrados na prática.

O que estes testes travam:
1. os defaults NÃO mudaram (45d / 90%) — quem não mexer no ⚙ Parâmetros vê o painel de sempre;
2. o limiar realmente move a fronteira;
3. a FRONTEIRA CONTINUA INCLUSIVA em qualquer limiar (armadilha do README: cobertura == limiar
   já é ideal; contá-la como risco punia quem repôs exatamente no alvo);
4. a meta move só o GATILHO de alerta, nunca a contagem;
5. o clamp da querystring (o limiar chega do usuário — 0 faria tudo virar "ideal" em silêncio);
6. (08/2026) o card "Em risco" virou CLICÁVEL → card e lista têm de dar o mesmo número.
"""
from estoque import core


def _p(cobertura_dias, valor=100.0):
    """Produto que gira 1 un/dia — o disponível vira a cobertura em dias direto."""
    return {"giro_dia": 1.0, "qtdisp": float(cobertura_dias), "valor": valor}


def _sem_giro(valor=100.0):
    return {"giro_dia": 0.0, "qtdisp": 10.0, "valor": valor}


# ───────────────────────── 1. defaults intocados ─────────────────────────

def test_defaults_continuam_45d_e_90pct():
    """Regressão do 'não muda nada pra quem não mexeu': os defaults do parâmetro têm de bater
    com a assinatura antiga de resumo_estoque_ideal(limiar_dias=45, meta_pct=0.90)."""
    p = core.merge_params({})
    assert p["ideal_dias"] == 45
    assert p["ideal_meta_pct"] == 90
    assert core.regua_estoque_ideal(p) == (45, 0.90)


def test_sem_params_o_resumo_sai_igual_ao_de_antes():
    produtos = [_p(20), _p(45), _p(60), _sem_giro()]
    antigo = core.resumo_estoque_ideal(produtos)                                # assinatura antiga
    novo = core.resumo_estoque_ideal(produtos, *core.regua_estoque_ideal(core.merge_params({})))
    assert novo == antigo


# ───────────────────────── 2 e 3. o limiar move a fronteira (e é inclusivo) ─────────────────────

def test_limiar_move_a_fronteira():
    produtos = [_p(20), _p(45), _p(60)]
    assert core.resumo_estoque_ideal(produtos, 30, 0.90)["ideal"]["n"] == 2     # 45 e 60
    assert core.resumo_estoque_ideal(produtos, 60, 0.90)["ideal"]["n"] == 1     # só o 60
    assert core.resumo_estoque_ideal(produtos, 10, 0.90)["ideal"]["n"] == 3     # todos


def test_fronteira_inclusiva_em_qualquer_limiar():
    """Armadilha do README: o item que pousa EXATAMENTE no limiar atingiu a meta.
    Isso valia com o 45 cravado; tem de continuar valendo com o limiar configurável."""
    for lim in (15, 30, 45, 60, 90):
        r = core.resumo_estoque_ideal([_p(lim)], lim, 0.90)
        assert r["ideal"]["n"] == 1, f"cobertura == limiar ({lim}d) deveria contar como ideal"
        assert r["em_risco"]["n"] == 0


def test_limiar_ecoa_no_payload_para_a_tela_nao_mentir():
    """A tela deriva TODOS os rótulos de `limiar`/`meta_pct` do payload (é o que impede o
    '≥45d' cravado em HTML de mentir quando o parâmetro muda)."""
    r = core.resumo_estoque_ideal([_p(30)], 60, 0.75)
    assert r["limiar"] == 60
    assert r["meta_pct"] == 0.75


# ───────────────────────── 4. a meta move só o gatilho ─────────────────────────

def test_meta_move_o_alerta_mas_nao_a_contagem():
    produtos = [_p(20), _p(60), _p(60)]                       # 2 de 3 ideais = 66,7%
    baixa = core.resumo_estoque_ideal(produtos, 45, 0.50)
    alta = core.resumo_estoque_ideal(produtos, 45, 0.90)
    assert baixa["ideal"]["n"] == alta["ideal"]["n"] == 2
    assert baixa["ideal"]["pct"] == alta["ideal"]["pct"]
    assert baixa["alerta"] is False                            # 66,7% >= 50% → dentro da meta
    assert alta["alerta"] is True                              # 66,7% <  90% → abaixo da meta


def test_sem_giro_fica_fora_do_percentual():
    """'Sem giro' é reportado à parte e não entra no denominador — senão calibrar o limiar
    mexeria num número contaminado por item que nem gira."""
    r = core.resumo_estoque_ideal([_p(60), _sem_giro(), _sem_giro()], 45, 0.90)
    assert r["com_giro"] == 1
    assert r["ideal"]["pct"] == 1.0
    assert r["sem_giro"]["n"] == 2
    assert r["total"] == 3


# ───────────────────────── 5. clamp da querystring ─────────────────────────

def test_clamp_limiar_zero_volta_ao_default_e_nao_zera_a_regua():
    """Limiar 0 faria TODO item virar 'ideal' e o painel diria 100% pra sempre — em silêncio."""
    lim, _ = core.regua_estoque_ideal(core.merge_params({"ideal_dias": "0"}))
    assert lim == 45


def test_clamp_meta_satura_em_0_e_100():
    assert core.regua_estoque_ideal({"ideal_dias": 45, "ideal_meta_pct": 999})[1] == 1.0
    assert core.regua_estoque_ideal({"ideal_dias": 45, "ideal_meta_pct": -5})[1] == 0.0


def test_clamp_ignora_lixo_na_querystring():
    assert core.regua_estoque_ideal(core.merge_params({"ideal_dias": "abc",
                                                       "ideal_meta_pct": "abc"})) == (45, 0.90)


def test_meta_aceita_fracionaria():
    """O campo tem step 1, mas a querystring aceita 92,5% — não pode virar int e truncar."""
    p = core.merge_params({"ideal_dias": "30", "ideal_meta_pct": "92.5"})
    assert core.regua_estoque_ideal(p) == (30, 0.925)


# ────────── 6. o card "Em risco" é clicável: card e lista TÊM de dar o mesmo número ──────────
# Pedido do diretor 08/2026 ("clicar aqui e já trazer os itens abaixo dessa cobertura"). O clique
# leva à aba Produtos com `cobertura ≤ limiar−1`, que filtra por `p["cobertura_dias"]` — o mesmo
# campo que o export (`cob_max` em `_aplicar_filtros_cliente`) e a aba Cobertura leem.

def _p_real(cobertura_dias, qtdisp, giro_dia, valor=100.0):
    """Produto como o `construir_produtos` entrega: `cobertura_dias` calculado com os valores
    CRUS, ao lado de `qtdisp`/`giro_dia` já ARREDONDADOS para exibição."""
    return {"cobertura_dias": cobertura_dias, "qtdisp": qtdisp, "giro_dia": giro_dia,
            "valor": valor}


def test_resumo_segue_o_cobertura_dias_do_produto_e_nao_o_recalculo():
    """O bug que o drill expôs: recalcular `ceil(qtdisp ÷ giro_dia)` a partir dos campos
    ARREDONDADOS dá outro dia que o `cobertura_dias` calculado com os crus.

    Caso REAL medido no BI (cód. 44398): 104 un ÷ giro cru 4,3333… = 24,0 exatos → 24d; mas
    ÷ 4,333 (arredondado, que é o que vai no dict) = 24,0018 → ceil 25d. Com limiar 25 o item
    é "em risco" pela lista e "ideal" pelo recálculo — card e lista discordavam em 2 SKUs."""
    p = _p_real(cobertura_dias=24, qtdisp=104.0, giro_dia=4.333)
    assert core.cobertura_dias_oficial(104.0, 4.333) == 25, "premissa do caso real mudou"
    r = core.resumo_estoque_ideal([p], 25, 0.90)
    assert r["em_risco"]["n"] == 1, "o card tem de seguir o cobertura_dias (24d), não o recálculo"
    assert r["ideal"]["n"] == 0


def test_card_em_risco_bate_com_a_lista_que_o_clique_abre():
    """Invariante do drill, em vários limiares: nº de SKUs e VALOR do card == os da lista.
    A lista é o filtro `cob_max` (front e export): `cobertura_dias != None and <= limiar−1`."""
    produtos = [
        _p_real(24, 104.0, 4.333, valor=209.63),    # o caso real do 44398
        _p_real(0, 0.0, 2.0, valor=50.0),           # ruptura: 0d entra em risco
        _p_real(25, 50.0, 2.0, valor=300.0),        # pousa no limiar 25 → ideal (fronteira inclusiva)
        _p_real(80, 160.0, 2.0, valor=700.0),
        {"giro_dia": 0.0, "qtdisp": 10.0, "valor": 900.0, "cobertura_dias": 9999},   # sem giro
    ]
    for lim in (10, 25, 30, 45, 60):
        r = core.resumo_estoque_ideal(produtos, lim, 0.90)
        lista = [p for p in produtos
                 if p.get("cobertura_dias") is not None and p["cobertura_dias"] <= lim - 1]
        assert len(lista) == r["em_risco"]["n"], f"card × lista divergem no limiar {lim}"
        assert round(sum(p["valor"] for p in lista), 2) == r["em_risco"]["valor"]


def test_sem_giro_nunca_vaza_para_a_lista_do_drill():
    """`cobertura_dias` = 9999 no item sem giro — é o que mantém ele fora do `cob_max` mesmo
    num limiar alto. Se vazasse, a lista traria itens que o card conta na caixa 'Sem giro'."""
    sem_giro = {"giro_dia": 0.0, "qtdisp": 10.0, "valor": 100.0, "cobertura_dias": 9999}
    r = core.resumo_estoque_ideal([sem_giro], 90, 0.90)
    assert r["sem_giro"]["n"] == 1 and r["em_risco"]["n"] == 0
    assert [p for p in [sem_giro] if p["cobertura_dias"] <= 89] == []


def test_fallback_para_quem_nao_tem_cobertura_dias():
    """Produto sintético (sem o campo) tem de continuar sendo classificado pelo recálculo —
    é o que os demais testes deste arquivo montam."""
    r = core.resumo_estoque_ideal([_p(20), _p(60)], 45, 0.90)
    assert r["em_risco"]["n"] == 1 and r["ideal"]["n"] == 1
