"""Gate do bloco "Estoque por filial" no drawer 360° do produto (10/2026, pedido do João Victor).

"Temos como trazer nessa aba à direita o estoque do produto por filial? Preciso saber se alguma
filial tem o produto 58389, pois na filial atacado está zerado, aí tenho que entrar filial por
filial para saber isso."

O caso dele, medido no BI em 16/10/2026: Matriz 1 un (giro 42/mês) · A&M 4 (giro ~12) · AC 3
(giro ~3). As lojas somam 7 un, uns 5 dias do Atacado, e as duas ficariam sem estoque. É por isso
que o bloco traz disponível, giro e cobertura, e não só o saldo.

O que estes testes protegem:
  · a consulta é POR PRODUTO e SEM recorte de filial — a pergunta é o que está fora da unidade;
  · as réguas são as do card (disponível = gerencial − avaria − reserva; giro com o consumo de
    produção; matéria-prima pelo consumo de 12m). Numa unidade de uma filial só, a linha repete
    o KPI;
  · filial de faturamento (7/8) não aparece só por ter giro; filial com saldo aparece mesmo fora
    de toda unidade (a 10);
  · nada disso derruba o drawer, e o modo BD (demo) tem espelho.
"""
import json
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from estoque import core, queries as Q, routes

JS = Path('static/estoque/estoque.js').read_text(encoding='utf-8')
NODE = shutil.which('node')
HOJE = date(2026, 10, 16)
NOMES = {"3": "Matriz", "4": "A&M", "5": "Deposito", "8": "Atacado", "9": "JID", "14": "AC"}
DE_ESTOQUE = {"3", "5", "4", "14", "9"}


def _linha(fil, ger=0.0, bloq=0.0, res=0.0, g=(0.0, 0.0, 0.0)):
    return {"CODFILIAL": fil, "qtestger": ger, "qtbloq": bloq, "qtreserv": res,
            "giro_m1": g[0], "giro_m2": g[1], "giro_m3": g[2]}


# O 58389 como veio do BI em 16/10/2026 (q_estoque_filiais_produto)
CASO_58389 = [_linha("14", 3.0, g=(5, 3, 0)), _linha("3", 1.0, g=(41, 58, 27)),
              _linha("4", 4.0, g=(4, 18, 13)), _linha("5"), _linha("8"), _linha("1")]


def _pf(linhas, unidade=("3", "5"), **kw):
    return core.estoque_por_filial(linhas, core.merge_params({}), nomes=NOMES,
                                   filiais_unidade=list(unidade), filiais_estoque=DE_ESTOQUE, **kw)


# ─────────────────────── a query ───────────────────────
def test_query_filtra_o_produto_e_NAO_recorta_filial():
    """Recortar pela unidade, como todas as outras queries do módulo, devolveria exatamente o
    que o comprador já vê — e esconderia a resposta que ele foi buscar."""
    q = Q.q_estoque_filiais_produto(58389)
    assert "PCEST[CODPROD] = 58389" in q
    assert "PCEST[CODFILIAL]" in q and "CODFILIAL] IN" not in q
    for col in ("QTESTGER", "QTBLOQUEADA", "QTRESERV", "QTVENDMES1", "QTVENDMES3"):
        assert col in q


def test_query_de_consumo_e_por_produto_e_por_filial():
    q = Q.q_consumo_filiais_produto(58389, [202609, 202608])
    assert "CONSUMO_PRODUCAO[CODPROD] = 58389" in q
    assert "CONSUMO_PRODUCAO[CODFILIAL]" in q
    assert '"SP"' in q and "DATE(2026, 9, 1)" in q


# ─────────────────────── a regra (core) ───────────────────────
def test_o_caso_do_58389():
    fils = _pf(CASO_58389)
    assert [(f["nome"], f["disponivel"], f["giro_mes"]) for f in fils] == [
        ("Matriz", 1.0, 42.0), ("A&M", 4.0, 12.0), ("AC", 3.0, 3.0)]
    assert [f["na_unidade"] for f in fils] == [True, False, False]
    # 4 un ÷ (12/30 por dia) = 10 dias: é o giro da loja que diz que aquilo não é sobra
    assert fils[1]["cobertura"] == 10.0


def test_disponivel_desconta_avaria_e_reserva_e_nao_tem_piso():
    """Régua do card (decisão do diretor 07/2026). Negativo é erro de saldo e fica à vista,
    como no `qtdisp` do snapshot."""
    f = _pf([_linha("3", 86, bloq=81, res=5, g=(30, 30, 30)),
             _linha("4", 2, res=5, g=(3, 3, 3))])
    assert f[0]["disponivel"] == 0 and f[0]["avaria"] == 81 and f[0]["reserva"] == 5
    assert f[0]["cobertura"] is None, "sem disponível não há cobertura"
    assert f[1]["disponivel"] == -3


def test_filial_de_faturamento_NAO_entra_so_por_ter_giro():
    """A 8 fatura sem estoque e carrega QTVENDMES no PCEST (1.528 itens em 10/2026). Sem a regra,
    quase todo item listaria "Atacado 0 un · giro 30/mês"."""
    fils = _pf([_linha("3", 10, g=(9, 9, 9)), _linha("8", 0, g=(30, 30, 30))])
    assert [f["codfilial"] for f in fils] == ["3"]


def test_filial_de_estoque_com_giro_e_saldo_zero_ENTRA():
    """Zerada com demanda é ruptura — informação, não ruído."""
    fils = _pf([_linha("5", 0, g=(6, 6, 6))])
    assert fils and fils[0]["disponivel"] == 0 and fils[0]["giro_mes"] == 6


def test_filial_com_saldo_fora_de_toda_unidade_aparece_pelo_codigo():
    """A filial 10 tinha 12 itens com saldo em 10/2026 e não está em nenhuma unidade do painel —
    é justamente o que o comprador está procurando."""
    fils = _pf([_linha("10", 7)])
    assert fils[0]["nome"] == "Filial 10" and fils[0]["na_unidade"] is False


def test_filial_sem_saldo_e_sem_giro_some():
    assert _pf([_linha("1"), _linha("5"), _linha("15")]) == []


def test_ordena_pelo_codigo_numerico():
    fils = _pf([_linha("14", 1), _linha("3", 1), _linha("10", 1), _linha("4", 1)])
    assert [f["codfilial"] for f in fils] == ["3", "4", "10", "14"]


def test_codfilial_float_ou_char_vira_a_mesma_chave():
    """BI devolve texto, a demo devolve CHAR; float apareceria se o modelo mudasse o tipo.
    `cod_str` nas duas pontas, senão `na_unidade` nunca casa (o bug do filtro de Departamento)."""
    fils = _pf([_linha(3.0, 1), _linha("4 ", 1)], unidade=("3",))
    assert [(f["codfilial"], f["na_unidade"]) for f in fils] == [("3", True), ("4", False)]


def test_consumo_de_producao_soma_ao_giro_da_filial():
    """Item de kit: a 1122 baixa o componente fora da venda. Sem somar, a linha da Matriz sairia
    com giro menor que o card do Atacado."""
    fils = _pf([_linha("3", 30, g=(10, 10, 10))],
               consumo={"3": {"m1": 20, "m2": 20, "m3": 20}})
    assert fils[0]["giro_mes"] == 30


def test_materia_prima_da_industria_usa_consumo_de_12_meses():
    """Bobina não vende, se transforma: na filial industrial o consumo de 12m ÷ 12 SUBSTITUI a
    venda, como no `construir_produtos`. Sem isso a JID sairia "sem giro" em toda matéria-prima."""
    fils = _pf([_linha("9", 500)], unidade=("9",), consumo_12m={"9": 1200},
               filiais_industria={"9"})
    assert fils[0]["giro_mes"] == 100 and fils[0]["cobertura"] == 150.0


def test_filial_industrial_sem_consumo_cai_na_venda():
    fils = _pf([_linha("9", 10, g=(3, 3, 3))], filiais_industria={"9"})
    assert fils[0]["giro_mes"] == 3


def test_giro_base_m1_e_respeitado():
    p = core.merge_params({"giro_base": "m1"})
    fils = core.estoque_por_filial([_linha("3", 10, g=(40, 1, 1))], p, filiais_estoque=DE_ESTOQUE)
    assert fils[0]["giro_mes"] == 40


def test_unidade_de_UMA_filial_repete_o_card():
    """A linha e o KPI saem de funções diferentes (`estoque_por_filial` × `construir_produtos`);
    numa unidade de uma filial só têm de dar o mesmo número. Se divergirem, alguém mexeu numa
    régua de um lado só."""
    lin = _linha("4", 13, bloq=2, res=1, g=(4, 18, 13))
    snap = [{"CODPROD": 58389, **lin, "custofin": 17.0}]
    cad = {58389: {"CODPROD": 58389, "DESCRICAO": "AP. PAPEL HIG.", "CODFORNEC": 1, "QTUNITCX": 1}}
    card = core.construir_produtos(snap, {}, cad, {}, {}, {}, core.merge_params({}), hoje=HOJE)[0]
    fil = _pf([lin], unidade=("4",))[0]
    assert fil["disponivel"] == card["qtdisp"]
    assert fil["giro_mes"] == card["giro_mes"]
    assert fil["cobertura"] == card["cobertura"]


# ─────────────────────── a rota ───────────────────────
@pytest.fixture
def app_ctx():
    import server  # noqa: F401
    from server import app
    return app


def _mock_bi(monkeypatch, est, cons=None, explode_est=False, explode_cons=False, chamadas=None):
    def _dax(q, *a, **k):
        if chamadas is not None:
            chamadas.append(q)
        if "CONSUMO_PRODUCAO" in q:
            if explode_cons:
                raise RuntimeError("tabela não publicada")
            return cons or []
        if explode_est:
            raise RuntimeError("BI fora")
        return est
    monkeypatch.setattr(routes.pbi, "run_dax", _dax)
    monkeypatch.setattr(routes, "_pg", lambda: False)
    monkeypatch.setattr(routes, "_hoje", lambda: HOJE)
    routes.pbi._CACHE.clear()


def _rota(app_ctx, unidade=("3", "5")):
    with app_ctx.test_request_context("/estoque/api/produto/58389"):
        return routes._estoque_por_filial(58389, core.merge_params({}), list(unidade))


def test_rota_junta_o_consumo_nos_3_meses_fechados(app_ctx, monkeypatch):
    """Em 16/10 os 3 meses do QTVENDMES são set/ago/jul. Consumo de junho conta só nos 12m."""
    _mock_bi(monkeypatch, [_linha("3", 30, g=(10, 10, 10))], cons=[
        {"codfilial": "3", "mes": "2026-09-01T00:00:00", "qt": 30.0},
        {"codfilial": "3", "mes": "2026-08-01T00:00:00", "qt": 30.0},
        {"codfilial": "3", "mes": "2026-07-01T00:00:00", "qt": 30.0},
        {"codfilial": "3", "mes": "2026-06-01T00:00:00", "qt": 900.0},
    ])
    assert _rota(app_ctx)[0]["giro_mes"] == 40


def test_sem_CONSUMO_PRODUCAO_o_giro_sai_so_de_venda(app_ctx, monkeypatch):
    _mock_bi(monkeypatch, CASO_58389, explode_cons=True)
    assert [f["giro_mes"] for f in _rota(app_ctx)] == [42.0, 12.0, 3.0]


def test_BI_fora_o_bloco_some_e_o_drawer_continua(app_ctx, monkeypatch):
    _mock_bi(monkeypatch, [], explode_est=True)
    assert _rota(app_ctx) == []


def test_segunda_abertura_vem_do_cache_e_a_unidade_e_recalculada(app_ctx, monkeypatch):
    """Duas consultas por produto aberto; trocar a unidade não refaz consulta, só a marcação."""
    chamadas = []
    _mock_bi(monkeypatch, CASO_58389, chamadas=chamadas)
    _rota(app_ctx)
    outra = _rota(app_ctx, unidade=("4",))
    assert len(chamadas) == 2
    assert [f["na_unidade"] for f in outra] == [False, True, False]


def test_api_produto_entrega_o_bloco():
    import inspect
    src = inspect.getsource(routes.api_produto)
    assert "_estoque_por_filial(codprod, params, filiais)" in src
    assert '"por_filial": por_filial' in src


# ─────────────────────── multi-fonte ───────────────────────
def test_modo_postgres_tem_provider_espelhado():
    """Sem o espelho, em modo BD o `run_dax` levanta (rede de segurança) e o bloco some da demo."""
    import inspect
    from estoque import provider_sql as PS
    src = inspect.getsource(PS.estoque_filiais_produto)
    assert "codprod = %s" in src and "GROUP BY codfilial" in src
    for chave in ("CODFILIAL", "qtestger", "qtreserv", "qtbloq", "giro_m1", "giro_m2", "giro_m3"):
        assert f'"{chave}"' in src
    assert PS.consumo_filiais_produto(58389, [202609]) == []


# ─────────────────────── a tela ───────────────────────
def test_drawer_renderiza_o_bloco():
    assert "${filiaisDrawer(j.por_filial)}" in JS
    assert "giro ${int(f.giro_mes)}/mês" in JS


def test_card_e_linha_usam_a_MESMA_cobertura():
    assert "cobQt(p.qtdisp,p.giro_mes,p.cobertura)" in JS
    assert "cobQt(f.disponivel,f.giro_mes,f.cobertura)" in JS


def _cob_qt(qt, giro, c):
    linha_cob = next(l for l in JS.splitlines() if l.startswith('const cob = '))
    linha_qt = next(l for l in JS.splitlines() if l.startswith('const cobQt='))
    script = ("const dec=(v,n)=>Number(v).toFixed(n);\n" + linha_cob + "\n" + linha_qt +
              f"\nconsole.log(JSON.stringify(cobQt({json.dumps(qt)},{json.dumps(giro)},{json.dumps(c)})));")
    out = subprocess.run([NODE, '-e', script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@pytest.mark.skipif(NODE is None, reason='node não instalado')
def test_item_em_RUPTURA_tem_cobertura_zero_e_nao_infinita():
    """O card pintava "∞" num item zerado com giro: o servidor manda `cobertura=None` nos dois
    casos e o `cob()` não sabia distinguir."""
    assert _cob_qt(0, 42, None) == '0d'
    assert _cob_qt(-3, 42, None) == '0d'
    assert _cob_qt(5, 0, None) == '∞'
    assert _cob_qt(0, 0, None) == '—'
    assert _cob_qt(4, 12, 10.0) == '10d'
