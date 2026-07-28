"""Gate do 'Estoque ideal' (Painel gerencial) virar PARAMETRIZÁVEL — pedido do diretor 07/2026:
os dois números (limiar de dias e meta %) estavam cravados e precisam ser calibrados na prática.

O que estes testes travam:
1. os defaults NÃO mudaram (45d / 90%) — quem não mexer no ⚙ Parâmetros vê o painel de sempre;
2. o limiar realmente move a fronteira;
3. a FRONTEIRA CONTINUA INCLUSIVA em qualquer limiar (armadilha do README: cobertura == limiar
   já é ideal; contá-la como risco punia quem repôs exatamente no alvo);
4. a meta move só o GATILHO de alerta, nunca a contagem;
5. o clamp da querystring (o limiar chega do usuário — 0 faria tudo virar "ideal" em silêncio).
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
