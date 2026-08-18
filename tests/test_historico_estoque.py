"""Gate da foto diária do estoque (aba Evolução) — 08/2026.

O contexto que estes testes protegem: o `PCEST` é POSIÇÃO, não evento. O saldo de ontem é
sobrescrito e não existe em lugar nenhum, então o histórico **não pode ser gerado para trás** —
só fotografado daqui pra frente. Isso torna cada decisão de gravação irreversível.

Daí as duas escolhas que estes testes travam:

1. **Grava-se o INGREDIENTE, não o resultado.** Se a foto guardasse "parado = R$ X", a série
   ficaria congelada na régua daquele dia e qualquer correção futura (o `eh_parado`, o
   `novo_dias`, o `ideal_dias`) desenharia um degrau no gráfico. Como a aba existe para PROVAR
   gestão, degrau de definição seria lido como resultado de operação.
2. **A foto só sai depois do refresh do BI do dia.** Tirada antes, grava a posição de ontem
   carimbada com a data de hoje — e ninguém consegue explicar o degrau meses depois.
"""
from datetime import date

import pytest

from estoque import core, historico


D1, D2 = date(2026, 8, 14), date(2026, 8, 15)


def _linha(dia, qtdisp, valor, giro_dia, cob, dtsaida=None, dtent=None):
    """Uma linha CRUA da foto, na ordem que o SELECT do `serie()` devolve."""
    return (dia, qtdisp, valor, giro_dia, cob, dtsaida, dtent)


# ───────────────── 1. as 4 séries saem do cru ─────────────────

def test_valor_de_estoque_e_a_soma_do_dia():
    s = historico.agregar([_linha(D1, 10, 1000.0, 1.0, 10),
                           _linha(D1, 5, 500.0, 1.0, 5),
                           _linha(D2, 10, 800.0, 1.0, 10)])
    assert [d["data"] for d in s] == ["2026-08-14", "2026-08-15"]
    assert s[0]["valor_estoque"] == 1500.0
    assert s[1]["valor_estoque"] == 800.0


def test_ruptura_e_o_contrapeso_estoque_zerado_com_giro():
    """Sem esta série, "o estoque caiu" pode ser desabastecimento e a aba comemoraria."""
    s = historico.agregar([_linha(D1, 0, 0.0, 2.0, 0),      # zerado E gira  → ruptura
                           _linha(D1, 0, 0.0, 0.0, 9999),   # zerado sem giro → não é ruptura
                           _linha(D1, 8, 800.0, 1.0, 8)])
    assert s[0]["n_ruptura"] == 1


def test_cobertura_vira_distribuicao_por_faixa_porque_dias_nao_somam():
    s = historico.agregar([_linha(D1, 10, 100.0, 1.0, 10),     # 0-30
                           _linha(D1, 50, 500.0, 1.0, 50),     # 31-60
                           _linha(D1, 200, 900.0, 1.0, 200)])  # 121+
    assert s[0]["faixas"]["0-30"] == 100.0
    assert s[0]["faixas"]["31-60"] == 500.0
    assert s[0]["faixas"]["121+"] == 900.0
    assert sum(s[0]["faixas"].values()) == s[0]["valor_estoque"]


def test_sem_giro_entra_no_121_como_no_painel_gerencial():
    """As faixas espelham `core.resumo_cobertura`, que joga giro<=0 no 121+. Sem isto, Σ faixas
    ficaria menor que o valor de estoque e o gráfico empilhado não fecharia com o KPI do dia —
    achado rodando a foto contra o Postgres real (o capital sem giro sumia do gráfico)."""
    s = historico.agregar([_linha(D1, 10, 1000.0, 1.0, 10),     # gira, 0-30
                           _linha(D1, 5, 900.0, 0.0, 9999)])    # sem giro
    assert s[0]["faixas"]["121+"] == 900.0
    assert sum(s[0]["faixas"].values()) == s[0]["valor_estoque"] == 1900.0
    assert s[0]["semgiro_n"] == 1, "e continua reportado à parte para o % ideal"


def test_sem_giro_fica_fora_do_percentual_ideal():
    s = historico.agregar([_linha(D1, 10, 100.0, 0.0, 9999),   # sem giro
                           _linha(D1, 90, 900.0, 1.0, 90)])    # ideal (>=45)
    assert s[0]["semgiro_n"] == 1
    assert s[0]["ideal_n"] == 1 and s[0]["risco_n"] == 0
    assert s[0]["pct_ideal"] == 1.0, "o sem-giro não pode entrar no denominador"


# ───────────────── 2. o passado se RECALCULA (a decisão irreversível) ─────────────────

def test_mudar_a_regua_do_produto_novo_reescreve_o_passado():
    """A prova de que guardar o cru valeu: MESMAS linhas, `novo_dias` diferente, capital parado
    diferente — sem tocar no banco. Se a foto guardasse o total já somado, este número estaria
    congelado e a correção da régua viraria um degrau no gráfico."""
    # item que NUNCA vendeu e entrou há 20 dias
    linhas = [_linha(D1, 10, 1000.0, 0.0, 9999, dtsaida=None, dtent=date(2026, 7, 25))]
    with_15 = historico.agregar(linhas, {"novo_dias": 15})
    with_30 = historico.agregar(linhas, {"novo_dias": 30})
    assert with_15[0]["valor_parado"] == 1000.0, "com janela de 15d ele é dead stock"
    assert with_30[0]["valor_parado"] == 0.0, "com janela de 30d ele ainda é 'novo'"


def test_mudar_o_limiar_do_estoque_ideal_reescreve_o_passado():
    linhas = [_linha(D1, 40, 400.0, 1.0, 40)]
    assert historico.agregar(linhas, {"ideal_dias": 30})[0]["ideal_n"] == 1
    assert historico.agregar(linhas, {"ideal_dias": 60})[0]["risco_n"] == 1


def test_o_novo_nao_conta_como_capital_parado():
    """Mesma régua do `core.eh_parado` — a série não pode discordar da tela."""
    linhas = [_linha(D1, 10, 1000.0, 0.0, 9999, dtsaida=None, dtent=date(2026, 8, 13))]
    assert historico.agregar(linhas)[0]["valor_parado"] == 0.0


# ───────────────── 3. contrato do INSERT ─────────────────

def test_a_ordem_de_linha_casa_com_as_colunas_do_insert():
    """`_COLS` e `_linha` desalinhados gravariam valor em coluna errada — silenciosamente, e a
    série inteira sairia torta sem erro nenhum."""
    p = {"codprod": 7, "codfornec": 9, "codcomprador": 3, "qtdisp": 10.0, "custo_unit": 2.0,
         "valor": 20.0, "giro_mes": 30.0, "giro_dia": 1.0, "cobertura_dias": 10,
         "dtultsaida": "2026-08-01", "dtultent": "2026-07-01",
         "qtd_ja_pedida": 5.0, "qt_transicao": 0.0}
    linha = historico._linha(p, D1, "atacado")
    assert len(linha) == len(historico._COLS)
    d = dict(zip(historico._COLS, linha))
    assert d["data"] == D1 and d["unidade"] == "atacado"
    assert d["codprod"] == 7 and d["valor"] == 20.0 and d["cobertura_dias"] == 10


# ───────────────── 4. as guardas do robô ─────────────────

def _refresh(monkeypatch, end, in_progress=False):
    from estoque import pbi
    monkeypatch.setitem(pbi.CONFIG, "data_source", "powerbi")
    monkeypatch.setattr(pbi, "get_dataset_refresh",
                        lambda *a, **k: {"end": end, "end_fmt": end, "in_progress": in_progress})
    monkeypatch.setattr(historico.store, "ensure", lambda: True)


def test_nao_fotografa_com_bi_de_ontem(monkeypatch):
    """A guarda que impede gravar a posição de ONTEM com a data de HOJE."""
    _refresh(monkeypatch, "2026-08-14T03:00:00")
    pode, motivo = historico.pode_fotografar(D2)
    assert pode is False and "não de hoje" in motivo


def test_nao_fotografa_com_bi_atualizando(monkeypatch):
    _refresh(monkeypatch, "2026-08-15T03:00:00", in_progress=True)
    assert historico.pode_fotografar(D2)[0] is False


def test_fotografa_quando_o_bi_e_do_dia(monkeypatch):
    _refresh(monkeypatch, "2026-08-15T03:00:00")
    assert historico.pode_fotografar(D2)[0] is True


def test_demo_nao_fotografa(monkeypatch):
    """`ANALYTICS_HOJE` fixo = a base não envelhece; o robô reescreveria a mesma data todo dia."""
    monkeypatch.setenv("ANALYTICS_HOJE", "2026-07-24")
    pode, motivo = historico.pode_fotografar(D2)
    assert pode is False and "demo" in motivo


def test_modo_banco_nao_espera_refresh_de_bi(monkeypatch):
    from estoque import pbi
    monkeypatch.delenv("ANALYTICS_HOJE", raising=False)
    monkeypatch.setitem(pbi.CONFIG, "data_source", "postgres")
    monkeypatch.setattr(historico.store, "ensure", lambda: True)
    assert historico.pode_fotografar(D2)[0] is True


def test_postgres_fora_nao_quebra_o_job(monkeypatch):
    from estoque import pbi
    monkeypatch.delenv("ANALYTICS_HOJE", raising=False)
    monkeypatch.setitem(pbi.CONFIG, "data_source", "powerbi")
    monkeypatch.setattr(historico.store, "ensure", lambda: False)
    assert historico.pode_fotografar(D2)[0] is False


# ───────── 5. recorte por curva/XYZ (08/2026, dúvida do diretor: "filtra por curva?") ─────────
# A curva é gravada NA FOTO, não lida do cadastro de hoje: é isso que permite ver a ruptura da
# curva A ao longo do tempo sem o passado se reclassificar sozinho quando um item muda de curva.

def test_a_foto_grava_curva_e_xyz():
    """Se `_COLS` e `_linha` saírem de sincronia, o INSERT grava valor em coluna errada — e a
    série inteira sai torta sem erro nenhum."""
    p = {"codprod": 7, "codfornec": 9, "codcomprador": 3, "qtdisp": 10.0, "custo_unit": 2.0,
         "valor": 20.0, "giro_mes": 30.0, "giro_dia": 1.0, "cobertura_dias": 10,
         "dtultsaida": "2026-08-01", "dtultent": "2026-07-01",
         "qtd_ja_pedida": 0.0, "qt_transicao": 0.0, "curva_abc": "A", "xyz": "X"}
    d = dict(zip(historico._COLS, historico._linha(p, D1, "atacado")))
    assert d["curva_abc"] == "A" and d["xyz"] == "X"
    assert len(historico._COLS) == len(historico._linha(p, D1, "atacado"))


def test_qualquer_recorte_desvia_do_rollup(monkeypatch):
    """⚠️ O rollup é o agregado da EMPRESA na régua padrão. Se um filtro novo não entrar na
    condição, a aba serve o total da empresa para quem pediu a curva A — sem erro, só o número
    errado. Este teste é o que impede o próximo filtro de esquecer disso."""
    monkeypatch.setattr(historico, "_serie_rollup", lambda *a, **k: ["ROLLUP"])
    monkeypatch.setattr(historico, "_linhas_cruas", lambda *a, **k: [])
    monkeypatch.setattr(historico, "agregar", lambda linhas, params=None: ["CRU"])

    assert historico.serie("atacado") == ["ROLLUP"], "sem recorte, o caminho rápido tem de valer"
    for recorte in ({"curva": "A"}, {"xyz": "X"}, {"comprador": "3"}, {"fornecedor": "9"}):
        assert historico.serie("atacado", **recorte) == ["CRU"], \
            f"{list(recorte)[0]} não pode ser servido pelo rollup da empresa"
    # ⚙ Parâmetro fora do padrão também recalcula (é o "reescrever o passado")
    assert historico.serie("atacado", params={"novo_dias": 30}) == ["CRU"]
