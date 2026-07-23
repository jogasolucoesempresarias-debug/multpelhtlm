"""Testes do motor de lead time por fornecedor (core.leadtime_fornecedores).

Motor puro — sem BI/rede. Regras validadas com dados reais em 07/2026:
- lead = 1ª entrada (PEDIDO_ENTRADA) − emissão (PCPEDIDO), por NUMPED;
- DOIS leads por fornecedor: 'todos' (mediana geral) e 'real' (mediana dos >= 2d);
- 0–1d = pedido digitado na hora da entrega (fica no 'todos' e no % na hora);
- transferência entre filiais (raiz de CNPJ da empresa) fora; negativos descartados.
"""
from datetime import date

from estoque import core

CNPJ_EMPRESA = "02.262.785/0001-04"
HOJE = date(2026, 7, 23)


def _cab(numped, emissao, codfornec):
    return {"NUMPED": numped, "DTEMISSAO": f"{emissao}T00:00:00", "CODFORNEC": codfornec}


def _ent(numped, entrada):
    return {"NUMPED": numped, "DTENTRADA": f"{entrada}T00:00:00"}


FORN = {
    10: {"FORNECEDOR": "ACME LTDA", "CGC": "11.111.111/0001-11", "PRAZOENTREGA": 20, "CODCOMPRADOR": 7},
    20: {"FORNECEDOR": "BETA SA", "CGC": "22.222.222/0001-22", "PRAZOENTREGA": None, "CODCOMPRADOR": 7},
    99: {"FORNECEDOR": "MULTPEL FILIAL", "CGC": "02.262.785/0002-95", "PRAZOENTREGA": 1, "CODCOMPRADOR": 7},
}
COMP = {7: "Carlos Andrade"}


def test_lead_basico_e_delta():
    """5 pedidos reais (10, 12, 14, 16, 30d) → todos=média 16.4, real=mediana 14;
    Δ = 20 (manual) − 14 = +6 (inflado)."""
    cab = [_cab(i, "2026-06-01", 10) for i in range(1, 6)]
    ent = [_ent(1, "2026-06-11"), _ent(2, "2026-06-13"), _ent(3, "2026-06-15"),
           _ent(4, "2026-06-17"), _ent(5, "2026-07-01")]
    r = core.leadtime_fornecedores(cab, ent, FORN, COMP, hoje=HOJE, cnpj_empresa=CNPJ_EMPRESA)
    f = r["fornecedores"][0]
    assert f["codfornec"] == 10
    assert f["comprador"] == "Carlos Andrade"
    assert f["n"] == 5 and f["n_reais"] == 5 and f["confiavel"]
    assert f["lead_todos"] == 16.4 and f["lead_real"] == 14
    assert f["prazo_manual"] == 20 and f["delta"] == 6
    assert r["resumo"]["n_pedidos"] == 5
    assert r["resumo"]["n_defasados"] == 1          # |Δ| >= 3


def test_digitado_na_hora_fica_no_todos_e_fora_do_real():
    """6 na hora (0d) + 5 reais (10d): lead_todos = média 4.5 (não colapsa p/ 0), real segura 10."""
    cab = [_cab(i, "2026-06-01", 10) for i in range(1, 12)]
    ent = [_ent(i, "2026-06-01") for i in range(1, 7)] + \
          [_ent(i, "2026-06-11") for i in range(7, 12)]
    r = core.leadtime_fornecedores(cab, ent, FORN, COMP, hoje=HOJE, cnpj_empresa=CNPJ_EMPRESA)
    f = r["fornecedores"][0]
    assert f["n"] == 11 and f["na_hora"] == 6
    assert f["pct_na_hora"] == round(100 * 6 / 11, 1)
    assert f["lead_todos"] == round(50 / 11, 1)      # média COM os 0d (6×0 + 5×10)
    assert f["lead_real"] == 10                      # mediana só dos >= 2d
    # histograma: 6 em '0-1' e 5 em '8-15'
    faixas = {x["faixa"]: x["qtd"] for x in r["faixas"]}
    assert faixas["0-1"] == 6 and faixas["8-15"] == 5
    assert sum(faixas.values()) == r["resumo"]["n_pedidos"]


def test_minimo_de_pedidos_para_lead_confiavel():
    """Só 4 pedidos reais (< min_ped=5) → lead_real None, confiavel False (fallback = manual)."""
    cab = [_cab(i, "2026-06-01", 20) for i in range(1, 5)]
    ent = [_ent(i, "2026-06-09") for i in range(1, 5)]
    r = core.leadtime_fornecedores(cab, ent, FORN, COMP, hoje=HOJE, cnpj_empresa=CNPJ_EMPRESA)
    f = r["fornecedores"][0]
    assert f["n_reais"] == 4 and not f["confiavel"]
    assert f["lead_real"] is None and f["delta"] is None
    assert f["lead_todos"] == 8                      # o 'todos' continua informativo
    assert r["resumo"]["n_confiavel"] == 0


def test_transferencia_filial_fora():
    """Pedido p/ fornecedor com raiz de CNPJ da empresa = transferência, não conta."""
    cab = [_cab(1, "2026-06-01", 99), _cab(2, "2026-06-01", 10)]
    ent = [_ent(1, "2026-06-02"), _ent(2, "2026-06-11")]
    r = core.leadtime_fornecedores(cab, ent, FORN, COMP, hoje=HOJE, cnpj_empresa=CNPJ_EMPRESA)
    assert r["resumo"]["n_transfer"] == 1
    assert r["resumo"]["n_pedidos"] == 1
    assert [f["codfornec"] for f in r["fornecedores"]] == [10]


def test_negativo_descartado_e_aberto_contado():
    """NF antes do pedido (lead < 0) descarta; pedido sem entrada conta como aberto."""
    cab = [_cab(1, "2026-06-10", 10),   # entrada 06-09 → lead -1
           _cab(2, "2026-06-01", 10),   # sem entrada → aberto
           _cab(3, "2026-06-01", 10)]   # lead 9
    ent = [_ent(1, "2026-06-09"), _ent(3, "2026-06-10")]
    r = core.leadtime_fornecedores(cab, ent, FORN, COMP, hoje=HOJE, cnpj_empresa=CNPJ_EMPRESA)
    assert r["resumo"]["n_negativos"] == 1
    assert r["resumo"]["n_sem_entrada"] == 1
    assert r["resumo"]["n_pedidos"] == 1
    assert r["fornecedores"][0]["lead_todos"] == 9


def test_sem_prazo_manual_nao_tem_delta():
    """Fornecedor sem PRAZOENTREGA: lead sai normal, delta fica None (tela mostra 'sem prazo')."""
    cab = [_cab(i, "2026-06-01", 20) for i in range(1, 7)]
    ent = [_ent(i, "2026-06-13") for i in range(1, 7)]
    r = core.leadtime_fornecedores(cab, ent, FORN, COMP, hoje=HOJE, cnpj_empresa=CNPJ_EMPRESA)
    f = r["fornecedores"][0]
    assert f["confiavel"] and f["lead_real"] == 12
    assert f["prazo_manual"] is None and f["delta"] is None


# ───────────────────────── drill (leadtime_detalhe) ─────────────────────────
def _cab_full(numped, emissao, codfornec, vlt=100.0, vle=None, prevent=None, filial="3"):
    r = _cab(numped, emissao, codfornec)
    r.update({"VLTOTAL": vlt, "VLENTREGUE": vlt if vle is None else vle,
              "CODFILIAL": filial, "DTPREVENT": f"{prevent}T00:00:00" if prevent else None})
    return r


def test_detalhe_classifica_cada_pedido():
    """Cada pedido sai com o tipo certo: real / na_hora / aberto / negativo — nada escondido."""
    cab = [_cab_full(1, "2026-06-01", 10, vlt=100),            # lead 10 → real
           _cab_full(2, "2026-06-01", 10, vlt=200),            # lead 0 → na hora
           _cab_full(3, "2026-07-10", 10, vlt=300, vle=50),    # sem entrada → aberto
           _cab_full(4, "2026-06-10", 10, vlt=400)]            # entrada antes → negativo
    ent = [_ent(1, "2026-06-11"), _ent(2, "2026-06-01"), _ent(4, "2026-06-09")]
    d = core.leadtime_detalhe(cab, ent, 10, FORN, COMP, hoje=HOJE)
    tipos = {p["numped"]: p["tipo"] for p in d["pedidos"]}
    assert tipos == {1: "real", 2: "na_hora", 3: "aberto", 4: "negativo"}
    ab = next(p for p in d["pedidos"] if p["tipo"] == "aberto")
    assert ab["dias_aberto"] == (HOJE - date(2026, 7, 10)).days
    assert d["stats"]["n_abertos"] == 1 and d["stats"]["n_negativos"] == 1
    assert d["stats"]["valor_aberto"] == 250.0                 # 300 − 50 entregue
    assert d["stats"]["valor_12m"] == 1000.0
    # negativo e aberto ficam FORA das contas de lead
    assert d["stats"]["n"] == 2 and d["stats"]["na_hora"] == 1
    # mais recente primeiro
    assert d["pedidos"][0]["numped"] == 3


def test_detalhe_trimestres_e_faixas():
    """Evolução trimestral separa os períodos e o histograma local fecha com o total."""
    cab = ([_cab_full(i, "2026-02-10", 10) for i in range(1, 4)] +      # T1: leads 20
           [_cab_full(i, "2026-05-10", 10) for i in range(4, 8)])       # T2: leads 5
    ent = [_ent(i, "2026-03-02") for i in range(1, 4)] + \
          [_ent(i, "2026-05-15") for i in range(4, 8)]
    d = core.leadtime_detalhe(cab, ent, 10, FORN, COMP, hoje=HOJE)
    tris = {t["tri"]: t for t in d["trimestres"]}
    assert tris["2026-T1"]["lead_real"] == 20 and tris["2026-T1"]["n"] == 3
    assert tris["2026-T2"]["lead_real"] == 5 and tris["2026-T2"]["pct_na_hora"] == 0
    assert sum(f["qtd"] for f in d["faixas"]) == d["stats"]["n"]


def test_detalhe_promessa_ignora_prevent_automatica():
    """DTPREVENT = emissão+1 é preenchimento automático → fora; promessa real conta prazo/atraso."""
    cab = [_cab_full(1, "2026-06-01", 10, prevent="2026-06-02"),   # automática (emissão+1)
           _cab_full(2, "2026-06-01", 10, prevent="2026-06-15"),   # promessa real, entrega no prazo
           _cab_full(3, "2026-06-01", 10, prevent="2026-06-05")]   # promessa real, atrasa 5d
    ent = [_ent(1, "2026-06-08"), _ent(2, "2026-06-10"), _ent(3, "2026-06-10")]
    d = core.leadtime_detalhe(cab, ent, 10, FORN, COMP, hoje=HOJE)
    pr = d["promessa"]
    assert pr["n_avaliaveis"] == 2 and pr["n_auto"] == 1
    assert pr["pct_no_prazo"] == 50.0
    assert pr["atraso_medio"] == 5.0
    p3 = next(p for p in d["pedidos"] if p["numped"] == 3)
    assert p3["atraso_promessa"] == 5
    # a automática não vira "promessa" na linha do pedido
    p1 = next(p for p in d["pedidos"] if p["numped"] == 1)
    assert p1["dtprevent"] is None
