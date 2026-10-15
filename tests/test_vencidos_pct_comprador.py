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
def _pct_do_periodo(perda_por_mes, venda_por_mes, periodo, em_curso=None):
    """Espelha o cálculo do card no estoque.js: soma perda e venda dos meses DO PERÍODO que têm
    denominador, e divide no fim. Somar percentuais de meses daria outro número.

    ⚠️ (10/2026) O período é o CALENDÁRIO, não os meses que têm baixa: mês FECHADO sem perda
    entra com perda 0 e a venda dele inteira. Antes ele simplesmente não existia no front, e o %
    dividia só pelos meses COM perda — inflado justamente quando um mês ia bem. O mês EM CURSO
    sem baixa fica fora: venda de meio mês contra uma baixa que ainda não saiu."""
    meses = [m for m in periodo if venda_por_mes.get(m)
             and not (m == em_curso and not perda_por_mes.get(m))]
    perda = sum(perda_por_mes.get(m, 0.0) for m in meses)
    venda = sum(venda_por_mes[m] for m in meses)
    return (perda / venda * 100) if venda else None


def test_card_do_periodo_ignora_meses_fora_do_recorte():
    """O bug real: com comprador filtrado o card mostrava o all-time. Aqui, período = só 2026-01,
    então 2025 não pode entrar em nenhum dos dois lados da divisão."""
    perda = {"2026-01": 50.0, "2025-12": 777.0}
    venda = {"2026-01": 1000.0, "2025-12": 999999.0}
    assert _pct_do_periodo(perda, venda, ["2026-01"]) == pytest.approx(5.0)


def test_mes_sem_denominador_fica_fora_dos_DOIS_lados():
    """Mês sem venda (RCA só tem ≥2024) não pode somar no numerador — senão o % afunda com uma
    perda cujo denominador não existe."""
    perda = {"2023-05": 400.0, "2026-01": 50.0}
    venda = {"2026-01": 1000.0}
    assert _pct_do_periodo(perda, venda, ["2023-05", "2026-01"]) == pytest.approx(5.0)


def test_mes_sem_perda_entra_no_denominador():
    """Setembro/26 sem nenhuma baixa: a venda dele CONTA. Fora do denominador, o % do período
    sairia 8% em vez de 4% — o mês bom piorando o indicador."""
    perda = {"2026-08": 80.0}                              # setembro: nenhuma baixa
    venda = {"2026-08": 1000.0, "2026-09": 1000.0}
    assert _pct_do_periodo(perda, venda, ["2026-08", "2026-09"]) == pytest.approx(4.0)


def test_mes_em_curso_sem_baixa_fica_fora_do_percentual():
    """Outubro pela metade, nenhuma baixa AINDA (elas saem em lote): a venda dele não entra, senão
    o % cairia só porque o mês não acabou. Com baixa, entra como sempre entrou."""
    venda = {"2026-09": 1000.0, "2026-10": 500.0}
    per = ["2026-09", "2026-10"]
    assert _pct_do_periodo({"2026-09": 40.0}, venda, per, em_curso="2026-10") == pytest.approx(4.0)
    assert _pct_do_periodo({"2026-09": 40.0, "2026-10": 20.0}, venda, per,
                           em_curso="2026-10") == pytest.approx(4.0)   # 60 / 1500


def test_tela_tira_o_mes_em_curso_sem_baixa_do_percentual():
    from pathlib import Path
    js = Path("static/estoque/estoque.js").read_text(encoding="utf-8")
    assert "aberto=emCurso&&g.itens===0" in js and "venda:aberto?null:v" in js


# ─────────────────── mês sem perda (o "traço" do João, 10/2026) ───────────────────
def test_vencidos_entrega_hoje_e_a_venda_de_TODOS_os_meses():
    """O front monta o período pelo calendário — precisa do "hoje" do módulo (ancorado no dado
    em modo BD) e da venda dos meses sem perda, que o `meses` do core não traz."""
    import inspect
    fonte = inspect.getsource(routes.api_vencidos)
    assert '"hoje": _hoje().isoformat()' in fonte and '"venda_mes": venda_mes' in fonte


def test_tela_monta_o_periodo_pelo_calendario_e_marca_so_mes_fechado():
    from pathlib import Path
    js = Path("static/estoque/estoque.js").read_text(encoding="utf-8")
    ini = js.index("async function renderVencidos")
    corpo = js[ini:js.index("\nfunction renderTableInline", ini)]
    assert "calMeses.forEach(m=>{mm[m]=" in corpo          # todo mês do período nasce zerado
    assert "semPerda:g.itens===0&&g.mes<mesAtual" in corpo   # o mês EM CURSO nunca é "sem perda"
    assert "J.venda_mes" in corpo                            # denominador do mês zerado
    assert "pointStyle:trofeu" in corpo and "Mês sem perda lançada" in corpo
    assert "Nenhuma baixa por validade lançada no mês" in corpo   # o troféu diz o que mediu
    assert "comPerda.reduce" in corpo                        # "pior mês" nunca é um mês de R$ 0
