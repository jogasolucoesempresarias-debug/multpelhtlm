"""Testes do motor de verbas de fornecedor (core.verbas_fornecedores / verbas_detalhe).

Motor puro — sem BI/rede. Regras validadas contra o extrato 1826 em 07/2026:
- cancelada (DTCANCEL) fora; estorno (DTESTORNO) fora;
- saldo = VALOR − Σ aplicações (posição atual, qualquer emissão);
- placar por fornecedor = 12m (casa com a compra do %V/C); saldo antigo não some;
- compra alta sem verba nenhuma → lista de alerta.
"""
from datetime import date

from estoque import core

CNPJ_EMPRESA = "02.262.785/0001-04"
HOJE = date(2026, 7, 23)

FORN = {
    10: {"FORNECEDOR": "ACME LTDA", "CGC": "11.111.111/0001-11", "CODCOMPRADOR": 7},
    20: {"FORNECEDOR": "BETA SA", "CGC": "22.222.222/0001-22", "CODCOMPRADOR": 7},
    99: {"FORNECEDOR": "MULTPEL FILIAL", "CGC": "02.262.785/0002-95", "CODCOMPRADOR": 7},
}
COMP = {7: "Carlos Andrade"}


def _vb(nv, emissao, codfornec, valor, conta=250009, cancel=None, venc=None, ref="VERBA TESTE"):
    return {"NUMVERBA": nv, "CODFORNEC": codfornec, "VALOR": valor, "CODCONTA": conta,
            "DTEMISSAO": f"{emissao}T00:00:00", "DTVENC": f"{venc or emissao}T00:00:00",
            "DTCANCEL": f"{cancel}T00:00:00" if cancel else None,
            "REFERENCIA": ref, "TIPO": 0, "FORMAPGTO": "D", "CODFILIAL": "3"}


def _ap(nv, dtaplic, vl, estorno=None):
    return {"NUMVERBA": nv, "VLAPLIC": vl, "DTAPLIC": f"{dtaplic}T00:00:00",
            "DTESTORNO": f"{estorno}T00:00:00" if estorno else None}


def test_saldo_negociado_aplicado_basico():
    """Verba 1000 com 600 aplicados → saldo 400; %V/C = 1000/50000 = 2%."""
    verbas = [_vb(1, "2026-06-01", 10, 1000.0)]
    aplics = [_ap(1, "2026-06-10", 600.0)]
    r = core.verbas_fornecedores(verbas, aplics, FORN, COMP,
                                 compras_map={10: 50000.0}, lead_map={10: 12},
                                 hoje=HOJE, cnpj_empresa=CNPJ_EMPRESA)
    f = r["fornecedores"][0]
    assert f["negociado"] == 1000 and f["aplicado"] == 600 and f["saldo"] == 400
    assert f["pct_vc"] == 2.0 and f["lead_real"] == 12
    assert f["comprador"] == "Carlos Andrade"
    assert r["resumo"]["saldo_aberto"] == 400
    assert r["resumo"]["saldo_vencido"] == 400          # DTVENC = emissão, já venceu


def test_cancelada_e_estorno_ficam_fora():
    """Cancelada não conta em nada; aplicação estornada não abate o saldo."""
    verbas = [_vb(1, "2026-06-01", 10, 1000.0),
              _vb(2, "2026-06-01", 10, 500.0, cancel="2026-06-05")]
    aplics = [_ap(1, "2026-06-10", 300.0),
              _ap(1, "2026-06-11", 200.0, estorno="2026-06-12")]
    r = core.verbas_fornecedores(verbas, aplics, FORN, COMP, hoje=HOJE,
                                 cnpj_empresa=CNPJ_EMPRESA)
    f = r["fornecedores"][0]
    assert f["negociado"] == 1000                       # cancelada fora
    assert f["aplicado"] == 300 and f["saldo"] == 700   # estorno fora
    assert r["resumo"]["n_cancel"] == 1 and r["resumo"]["n_estornos"] == 1


def test_saldo_antigo_nao_some_do_placar():
    """Verba de 2024 (fora dos 12m) com saldo: fornecedor aparece com n_verbas=0 e o saldo."""
    verbas = [_vb(1, "2024-05-01", 20, 800.0)]
    aplics = []
    r = core.verbas_fornecedores(verbas, aplics, FORN, COMP, hoje=HOJE,
                                 cnpj_empresa=CNPJ_EMPRESA)
    f = r["fornecedores"][0]
    assert f["codfornec"] == 20
    assert f["n_verbas"] == 0 and f["negociado"] == 0   # nada nos 12m
    assert f["saldo"] == 800                             # mas o saldo está lá
    assert f["idade_saldo"] == (HOJE - date(2024, 5, 1)).days


def test_transferencia_filial_fora_e_grandes_sem_verba():
    """Verba da própria empresa fora; fornecedor que compra muito sem verba entra no alerta."""
    verbas = [_vb(1, "2026-06-01", 99, 5000.0),         # filial → fora
              _vb(2, "2026-06-01", 10, 1000.0)]
    r = core.verbas_fornecedores(verbas, [], FORN, COMP,
                                 compras_map={10: 100000.0, 20: 900000.0},
                                 hoje=HOJE, cnpj_empresa=CNPJ_EMPRESA)
    assert [f["codfornec"] for f in r["fornecedores"]] == [10]
    g = r["grandes_sem_verba"]
    assert len(g) == 1 and g[0]["codfornec"] == 20
    assert g[0]["compra_12m"] == 900000 and g[0]["comprador"] == "Carlos Andrade"


def test_contas_e_meses():
    """Quebra por conta nomeia 250008/250009; evolução mensal soma negociado × aplicado."""
    verbas = [_vb(1, "2026-05-01", 10, 1000.0, conta=250009),
              _vb(2, "2026-06-01", 10, 400.0, conta=250008)]
    aplics = [_ap(1, "2026-06-15", 1000.0)]
    r = core.verbas_fornecedores(verbas, aplics, FORN, COMP, hoje=HOJE,
                                 cnpj_empresa=CNPJ_EMPRESA)
    contas = {c["codconta"]: c for c in r["contas"]}
    assert contas[250009]["conta"] == "Rebaixa de custo" and contas[250009]["saldo"] == 0
    assert contas[250008]["conta"] == "Conta corrente" and contas[250008]["saldo"] == 400
    meses = {m["mes"]: m for m in r["meses"]}
    assert meses["2026-05"]["negociado"] == 1000 and meses["2026-05"]["aplicado"] == 0
    assert meses["2026-06"]["negociado"] == 400 and meses["2026-06"]["aplicado"] == 1000


def test_detalhe_verba_a_verba():
    """Drill: cada verba com valor/aplicado/saldo/idade/aplicações; cancelada fora."""
    verbas = [_vb(1, "2026-06-01", 10, 1000.0, ref="VERBA P/REBAIXA"),
              _vb(2, "2026-03-01", 10, 300.0, ref="CAMPANHA X"),
              _vb(3, "2026-06-01", 10, 99.0, cancel="2026-06-02")]
    aplics = [_ap(1, "2026-06-10", 400.0), _ap(1, "2026-06-20", 600.0)]
    d = core.verbas_detalhe(verbas, aplics, 10, hoje=HOJE)
    assert d["stats"]["n_verbas"] == 2                  # cancelada fora
    assert d["stats"]["negociado"] == 1300 and d["stats"]["aplicado"] == 1000
    assert d["stats"]["saldo"] == 300 and d["stats"]["n_abertas"] == 1
    v1 = next(v for v in d["verbas"] if v["numverba"] == 1)
    assert v1["saldo"] == 0 and v1["n_aplic"] == 2 and v1["ult_aplic"] == "2026-06-20"
    assert v1["campanha"] == "VERBA P/REBAIXA" and v1["conta"] == "Rebaixa de custo"
    v2 = next(v for v in d["verbas"] if v["numverba"] == 2)
    assert v2["saldo"] == 300
    assert v2["idade_saldo"] == (HOJE - date(2026, 3, 1)).days
    # mais recente primeiro
    assert d["verbas"][0]["numverba"] == 1
