"""Histórico do RISCO de vencimento + filtro de dias da aba Validade (pedido do João, 10/2026).

O que trava:
- o risco da série é a MESMA régua da aba Validade (`core.validade_fefo`): saldo − giro × dias, a
  custo, com "hoje" = o dia da foto — não uma 2ª fórmula;
- lotes do mesmo produto + validade somam ANTES do `max(0, …)` (o consumo incide sobre o total);
- as faixas são as dos cards da aba, e o "90+" fica de fora (lá ele depende do horizonte);
- dia sem lote gravado fica SEM a chave ("não medido"), nunca zero;
- o risco NÃO tem cor: lote que vence sem vender sai da conta, então ele cai quando a perda se
  consuma;
- a variação da janela parte do 1º dia MEDIDO (o histórico do risco nasce depois do resto);
- na tela, o card e o "≤ N dias" são um eixo só: um limpa o outro, e a tabela por comprador segue.
"""
from datetime import date, timedelta
from pathlib import Path

from estoque import core, historico, routes

D = date(2026, 10, 15)


def _l(cod, dias, qt, giro, custo, dia=D):
    """Uma linha do `_SQL_LOTES`: (data, codprod, dtval, qt, giro_dia, custo_unit)."""
    return (dia, cod, dia + timedelta(days=dias), qt, giro, custo)


# ───────────────── a régua é a da aba ─────────────────

def test_risco_e_saldo_que_nao_vende_ate_vencer_a_custo():
    r = historico.risco_validade([
        _l(1, 10, 100, 2.0, 5.0),     # vende 20 em 10 dias → sobram 80 × 5 = 400 de risco
        _l(2, 40, 10, 1.0, 3.0),      # vende 40 > 10 → risco 0, mas o estoque (30) aparece
        _l(3, 120, 50, 0.0, 9.0),     # 120 dias: fora do ≤90
    ])[D.isoformat()]
    assert r["faixas"]["0-15"] == {"risco": 400.0, "estoque": 500.0, "lotes": 1}
    assert r["faixas"]["31-60"] == {"risco": 0.0, "estoque": 30.0, "lotes": 1}
    assert r["risco_90"] == 400.0 and r["estoque_90"] == 530.0 and r["lotes_90"] == 2


def test_bate_com_o_validade_fefo_da_aba():
    """Fonte única: a mesma entrada pela função da aba dá a mesma soma."""
    linhas = [_l(1, 3, 40, 1.5, 2.0), _l(2, 20, 300, 4.0, 1.25), _l(3, 75, 90, 0.5, 7.0)]
    r = historico.risco_validade(linhas)[D.isoformat()]
    fefo = core.validade_fefo(
        [{"CODPROD": c, "DTVAL": dv, "qt": q} for _d, c, dv, q, _g, _c in linhas],
        {c: {"giro_dia": g, "custo_unit": cu} for _d, c, _dv, _q, g, cu in linhas}, {}, hoje=D)
    assert r["risco_90"] == core._round(sum(x["valor_risco"] for x in fefo))


def test_lotes_do_mesmo_produto_e_validade_somam_antes_do_max():
    """Separados, cada lote de 10 com consumo de 15 daria risco 0. Juntos são 20 − 15 = 5."""
    r = historico.risco_validade([_l(1, 10, 10, 1.5, 2.0), _l(1, 10, 10, 1.5, 2.0)])[D.isoformat()]
    assert r["faixas"]["0-15"]["risco"] == 10.0          # 5 un × R$ 2
    assert r["faixas"]["0-15"]["lotes"] == 1             # unificado, como na aba


def test_fronteiras_das_faixas_sao_as_dos_cards():
    r = historico.risco_validade([_l(1, 15, 1, 0, 1), _l(2, 16, 1, 0, 1), _l(3, 90, 1, 0, 1),
                                  _l(4, 91, 1, 0, 1)])[D.isoformat()]
    assert r["faixas"]["0-15"]["lotes"] == 1
    assert r["faixas"]["16-30"]["lotes"] == 1
    assert r["faixas"]["61-90"]["lotes"] == 1
    assert r["lotes_90"] == 3                             # o de 91 dias não entra


def test_hoje_e_o_dia_da_foto_nao_o_calendario():
    """O mesmo lote muda de faixa de um dia para o outro — é a foto que diz quantos dias faltam."""
    d2 = D + timedelta(days=1)
    dtval = D + timedelta(days=16)
    r = historico.risco_validade([(D, 1, dtval, 5, 0, 1), (d2, 1, dtval, 5, 0, 1)])
    assert r[D.isoformat()]["faixas"]["16-30"]["lotes"] == 1
    assert r[d2.isoformat()]["faixas"]["0-15"]["lotes"] == 1


def test_faixas_iguais_as_da_tela():
    js = Path("static/estoque/estoque.js").read_text(encoding="utf-8")
    for n, lo, hi in historico.FAIXAS_RISCO:
        assert f"['{n}',{lo},{hi}]" in js, n             # cards da aba Validade
        assert f"['{n}',C." in js, n                      # gráfico da Evolução


# ───────────────── gravação ─────────────────

def test_lote_sem_validade_ou_sem_saldo_nao_e_gravado_e_os_iguais_somam():
    rows = [{"CODPROD": 7, "DTVAL": "2026-11-01", "qt": 3},
            {"CODPROD": 7.0, "DTVAL": "2026-11-01T00:00:00", "qt": 2},   # float do BI + hora
            {"CODPROD": 8, "DTVAL": None, "qt": 9},
            {"CODPROD": 9, "DTVAL": "2026-11-01", "qt": 0}]
    assert historico.lotes_por_validade(rows) == [(7, date(2026, 11, 1), 5.0)]


# ───────────────── costura na série ─────────────────

class _Cur:
    def __init__(self, rows, log):
        self.rows, self.log = rows, log

    def execute(self, sql, args):
        self.log.append(args)

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    def __init__(self, rows, log):
        self.rows, self.log = rows, log

    def cursor(self):
        return _Cur(self.rows, self.log)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_dia_sem_lote_fica_sem_a_chave_e_o_recorte_vai_na_consulta(monkeypatch):
    log = []
    monkeypatch.setattr(historico.store, "ensure", lambda: True)
    monkeypatch.setattr(historico.store, "get_db", lambda: _Conn([_l(1, 10, 100, 2.0, 5.0)], log))
    d0 = (D - timedelta(days=1)).isoformat()
    dias = [{"data": d0}, {"data": D.isoformat()}]
    historico.juntar_risco_validade(dias, "atacado", comprador="47", curva="A")
    assert "risco_validade" not in dias[0] and "risco_90" not in dias[0]   # não medido ≠ zero
    assert dias[1]["risco_90"] == 400.0
    assert "47" in log[0] and "A" in log[0]                               # recorte honrado


def test_falha_na_leitura_degrada_para_ausente(monkeypatch):
    monkeypatch.setattr(historico.store, "ensure", lambda: True)

    def _quebra():
        raise RuntimeError("tabela nao existe")
    monkeypatch.setattr(historico.store, "get_db", _quebra)
    dias = [{"data": D.isoformat(), "valor_estoque": 1.0}]
    historico.juntar_risco_validade(dias, "atacado")
    assert dias == [{"data": D.isoformat(), "valor_estoque": 1.0}]


# ───────────────── resumo e tela ─────────────────

def test_risco_nao_tem_cor():
    assert "risco_90" in routes._EVO_DIRECAO and routes._EVO_DIRECAO["risco_90"] is None


def test_variacao_parte_do_primeiro_dia_medido():
    dias = [{"data": "2026-10-01", "valor_estoque": 10.0},
            {"data": "2026-10-02", "valor_estoque": 12.0, "risco_90": 300.0},
            {"data": "2026-10-03", "valor_estoque": 11.0, "risco_90": 240.0}]
    var = routes._resumo_evolucao(dias, [])["variacao"]
    assert var["risco_90"]["de"] == "2026-10-02" and var["risco_90"]["delta"] == -60.0
    assert var["valor_estoque"]["de"] == "2026-10-01"          # métrica antiga: igual a antes
    um = routes._resumo_evolucao(dias[:2], [])["variacao"]
    assert um["risco_90"] is None                              # 1 ponto medido não é variação


def test_excel_leva_o_risco():
    rot = [c for c, _ in routes._EVO_XLSX_COLS]
    assert "Risco ≤90d R$" in rot and "Risco 0-15d R$" in rot and "Lotes ≤90d" in rot


def test_validade_um_eixo_de_dias_so():
    """Card e "≤ N" não podem voltar a se somar: definir um limpa o outro, e a tabela por
    comprador, o total e a lista seguem o mesmo intervalo (`Lf`)."""
    js = Path("static/estoque/estoque.js").read_text(encoding="utf-8")
    ini = js.index("function renderValidade")
    corpo = js[ini:js.index("async function renderVencidos", ini)]
    assert "if(S.valFaixa)S.cli.valDias=''" in corpo
    assert "if(S.cli.valDias)S.valFaixa=null" in corpo
    assert "renderTableInline(Lf," in corpo                    # lista = mesmo intervalo da tabela
    assert "money(g.risco)" in corpo and "moneyK(g.risco)" not in corpo
    assert "<tfoot>" in corpo                                  # o total acumulado
    assert "alemHor" in corpo                                  # avisa quando passa do horizonte
