"""Gate do % da perda por validade quando há filtro de COMPRADOR (achado 07/2026).

O sintoma que o diretor viu era a linha azul do gráfico sumindo ao filtrar um comprador. A causa
não era o gráfico: `_venda_liq_mensal` recebia a venda no grão comprador × mês
(`q_venda_comprador_mensal_rca` agrupa por CODCOMPRADOR **e** AnoMes) e colapsava tudo em dois
mapas 1-D — um perdendo o comprador, outro perdendo o mês. Sem denominador cruzado, o front não
tinha como calcular o % daquele comprador naquele mês.

O problema PIOR, escondido atrás do sintoma: o card "% da venda" caía no percentual **all-time**
do comprador, então respondia a um período diferente do selecionado na tela — sem avisar. Número
errado é pior que gráfico incompleto, e por isso este gate trava o cruzado, não a linha.

(A devolução — `dev_idx` — sempre manteve o grão. Era só a venda que o perdia.)
"""
import pytest

from estoque import core, routes


@pytest.fixture
def app_ctx():
    import server  # noqa: F401
    from server import app
    return app


def _venda(cc, anomes, valor):
    return {"CODCOMPRADOR": cc, "AnoMes": anomes, "venda": valor}


def _mock_venda(monkeypatch, vendas, devol=()):
    monkeypatch.setattr(routes.pbi, "run_dax_rca",
                        lambda q, *a, **k: list(devol) if "DEVOLUCAO" in q else list(vendas))
    monkeypatch.setattr(routes, "_pg", lambda: False)


# ─────────────────── o mapa cruzado ───────────────────
def test_venda_liq_mensal_devolve_o_mapa_cruzado(app_ctx, monkeypatch):
    """A dimensão que faltava: comprador × mês, não comprador OU mês."""
    _mock_venda(monkeypatch, [_venda(7, 202601, 1000.0), _venda(7, 202602, 500.0),
                              _venda(9, 202601, 300.0)])
    por_mes, por_comp, por_comp_mes = routes._venda_liq_mensal(["3"])
    assert por_mes["2026-01"] == 1300.0          # os dois compradores no mês
    assert por_comp[7] == 1500.0                 # os dois meses do comprador
    assert por_comp_mes["7|2026-01"] == 1000.0   # o cruzamento, que antes se perdia
    assert por_comp_mes["7|2026-02"] == 500.0
    assert por_comp_mes["9|2026-01"] == 300.0


def test_cruzado_desconta_a_devolucao_do_par_certo(app_ctx, monkeypatch):
    """Líquida = bruta − devolução, no MESMO par comprador×mês — não a devolução do mês inteiro."""
    _mock_venda(monkeypatch,
                [_venda(7, 202601, 1000.0), _venda(9, 202601, 300.0)],
                devol=[{"CODCOMPRADOR": 7, "AnoMes": 202601, "dev": 200.0}])
    _pm, _pc, cruz = routes._venda_liq_mensal(["3"])
    assert cruz["7|2026-01"] == 800.0    # só o 7 leva o desconto
    assert cruz["9|2026-01"] == 300.0


def test_chave_do_cruzado_sobrevive_ao_json():
    """Chave é string porque o mapa viaja no payload — tupla não sobrevive à serialização,
    e dict com chave int volta como string do outro lado (a mordida que o jsonify já deu aqui)."""
    import json
    _pm = {"7|2026-01": 10.0}
    assert json.loads(json.dumps(_pm)) == _pm


# ─────────────────── o payload ───────────────────
def test_vencidos_publica_o_cruzado_para_o_front():
    """É o front que sabe qual comprador e qual período estão na tela, então o denominador
    viaja cru em vez de o servidor pré-calcular um % que ele não sabe recortar."""
    res = core.vencidos_por_mes([], venda_comp_mes_map={"7|2026-01": 1000.0})
    assert res["venda_comp_mes"] == {"7|2026-01": 1000.0}


def test_vencidos_sem_o_cruzado_nao_quebra():
    """O export chama `vencidos_por_mes` sem os mapas — o parâmetro novo é opcional."""
    res = core.vencidos_por_mes([])
    assert res["venda_comp_mes"] == {}
    assert res["meses"] == []


# ─────────────────── a regra que o front aplica ───────────────────
def _pct_do_periodo(itens_por_mes, venda_por_mes):
    """Espelha o cálculo do card no estoque.js: soma perda e venda só dos meses VISÍVEIS que
    têm denominador, e divide no fim. Somar percentuais de meses daria outro número."""
    pares = [(v, venda_por_mes[m]) for m, v in itens_por_mes.items() if venda_por_mes.get(m)]
    perda, venda = sum(p for p, _ in pares), sum(v for _, v in pares)
    return (perda / venda * 100) if venda else None


def test_card_do_periodo_ignora_meses_fora_do_recorte():
    """O bug real: com comprador filtrado o card mostrava o all-time. Aqui, período = só 2026-01,
    então 2025 não pode entrar em nenhum dos dois lados da divisão."""
    perda_visivel = {"2026-01": 50.0}                      # jan/26 é o único mês do período
    venda = {"2026-01": 1000.0, "2025-12": 999999.0}
    assert _pct_do_periodo(perda_visivel, venda) == pytest.approx(5.0)


def test_mes_sem_denominador_fica_fora_dos_DOIS_lados():
    """Mês sem venda (RCA só tem ≥2024) não pode somar no numerador — senão o % afunda com uma
    perda cujo denominador não existe."""
    perda = {"2023-05": 400.0, "2026-01": 50.0}
    venda = {"2026-01": 1000.0}
    assert _pct_do_periodo(perda, venda) == pytest.approx(5.0)
