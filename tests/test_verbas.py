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


# ───────────── recorte no SERVIDOR: comprador e fornecedor (bug 08/2026) ─────────────
# O diretor filtrou a RAZZO e viu a barra de julho em ~R$ 40k, quando a verba dela era
# R$ 10.054,80. Causa: `S.cli.fornec` filtrava só a TABELA no cliente; resumo, gráfico mensal e
# "por conta" vinham agregados do servidor no universo inteiro — dois universos na mesma tela.
# É o MESMO defeito que já tinha levado o recorte por comprador para o core, e que não tinha
# gate nenhum (estes testes cobrem os dois).
FORN_MULTI = {
    10: {"FORNECEDOR": "ACME LTDA", "CGC": "11.111.111/0001-11", "CODCOMPRADOR": 7},
    20: {"FORNECEDOR": "BETA SA", "CGC": "22.222.222/0001-22", "CODCOMPRADOR": 7},
    30: {"FORNECEDOR": "GAMA ME", "CGC": "33.333.333/0001-33", "CODCOMPRADOR": 8},
    40: {"FORNECEDOR": "DELTA SA", "CGC": "44.444.444/0001-44", "CODCOMPRADOR": 7},
}
COMP_MULTI = {7: "Carlos Andrade", 8: "Ana Souza"}
# 10 e 20 do comprador 7; 30 do comprador 8; 40 compra alto e não dá verba nenhuma.
VB_MULTI = [_vb(1, "2026-06-01", 10, 1000.0), _vb(2, "2026-06-15", 20, 500.0),
            _vb(3, "2026-05-10", 30, 700.0)]
AP_MULTI = [_ap(1, "2026-06-10", 600.0), _ap(2, "2026-06-20", 500.0),
            _ap(3, "2026-05-20", 700.0)]
CM_MULTI = {10: 50000.0, 20: 40000.0, 30: 30000.0, 40: 900000.0}


def _run(**kw):
    return core.verbas_fornecedores(VB_MULTI, AP_MULTI, FORN_MULTI, COMP_MULTI,
                                    compras_map=CM_MULTI, hoje=HOJE,
                                    cnpj_empresa=CNPJ_EMPRESA, **kw)


def test_sem_filtro_soma_o_universo_inteiro():
    """Âncora: sem recorte, os agregados somam todo mundo. É contra ESTES números que os
    recortes abaixo têm de diferir — senão o teste passaria com o filtro sendo ignorado."""
    r = _run()
    assert r["resumo"]["negociado_12m"] == 2200      # 1000 + 500 + 700
    assert r["resumo"]["aplicado_12m"] == 1800       # 600 + 500 + 700
    assert len(r["fornecedores"]) == 3


def test_fornec_recorta_resumo_grafico_e_contas():
    """O bug em uma linha: com um fornecedor filtrado, os TRÊS agregados do servidor têm de
    acompanhar a tabela — não só ela."""
    r = _run(fornec=10)
    assert [f["codfornec"] for f in r["fornecedores"]] == [10]
    assert r["resumo"]["negociado_12m"] == 1000      # não 2200
    assert r["resumo"]["saldo_aberto"] == 400
    jun = next(m for m in r["meses"] if m["mes"] == "2026-06")
    assert jun["negociado"] == 1000                  # não 1500 (10 + 20)
    # o eixo vem da JANELA, não dos dados: maio era só do fornecedor 30 e agora sai zerado,
    # em vez de desaparecer e colar as barras vizinhas
    assert len(r["meses"]) == 13
    assert next(m for m in r["meses"] if m["mes"] == "2026-05")["negociado"] == 0
    assert [c["negociado"] for c in r["contas"]] == [1000]


def test_fornec_recorta_tambem_as_aplicacoes():
    """A parte que se esquece: a APLICAÇÃO não tem CODFORNEC, a ponte é NUMVERBA → CODFORNEC.
    Sem cortá-la, "Aplicado 12m" e a barra verde ficam no total da empresa ao lado de um
    negociado já recortado — pior que o bug original, porque as duas séries mentem juntas."""
    r = _run(fornec=10)
    assert r["resumo"]["aplicado_12m"] == 600        # não 1100 (junho inteiro), nem 1800
    jun = next(m for m in r["meses"] if m["mes"] == "2026-06")
    assert jun["aplicado"] == 600


def test_fornec_recorta_grandes_sem_verba():
    """`compras_map` alimenta "compram e não dão verba"; sem cortá-lo, o painel seguiria
    listando fornecedor de fora do recorte."""
    assert [g["codfornec"] for g in _run()["grandes_sem_verba"]] == [40]
    assert _run(fornec=10)["grandes_sem_verba"] == []


def test_comprador_recorta_os_agregados():
    """Gate que faltava desde 07/2026: o recorte por comprador nunca foi travado por teste."""
    r = _run(comprador=8)
    assert [f["codfornec"] for f in r["fornecedores"]] == [30]
    assert r["resumo"]["negociado_12m"] == 700 and r["resumo"]["aplicado_12m"] == 700
    assert next(m for m in r["meses"] if m["mes"] == "2026-05")["negociado"] == 700
    assert round(sum(m["negociado"] for m in r["meses"]), 2) == 700


def test_comprador_e_fornec_compoem_em_intersecao():
    """Os dois filtros se somam, não se substituem: o comprador 7 pedindo o fornecedor 30
    (que é do comprador 8) tem de ver VAZIO — não o fornecedor 30, nem a carteira inteira do 7.
    ⚠️ É o caso que um `if escopo:` (em vez de `is not None`) devolveria como 'sem filtro'."""
    r = _run(comprador=7, fornec=30)
    assert r["fornecedores"] == [] and r["contas"] == []
    # o eixo do gráfico é propriedade da JANELA, então continua existindo — zerado
    assert sum(m["negociado"] for m in r["meses"]) == 0
    assert r["resumo"]["negociado_12m"] == 0 and r["resumo"]["saldo_aberto"] == 0
    r_ok = _run(comprador=7, fornec=10)
    assert [f["codfornec"] for f in r_ok["fornecedores"]] == [10]


def test_front_manda_fornec_e_carrega_a_chave_de_cache():
    """Gate de código. A resposta é cacheada por 30min no servidor e memoizada em S.verbas no
    cliente: se o fornecedor não entrar nas DUAS chaves, o primeiro recorte consultado é servido
    aos demais — e número plausível do fornecedor errado não denuncia nada na tela."""
    from pathlib import Path
    js = Path('static/estoque/estoque.js').read_text(encoding='utf-8')
    trecho = js[js.index('async function renderVerbas'):][:1600]
    assert "p.set('fornec'" in trecho, 'renderVerbas parou de mandar o fornecedor ao servidor'
    vbkey = next(l for l in trecho.splitlines() if 'const vbKey' in l)
    assert 'S.cli.fornec' in vbkey, f'fornecedor fora da chave de cache do cliente: {vbkey}'


def test_cache_do_servidor_separa_por_fornecedor():
    """O par do gate acima, do lado do servidor: `_verbas_res` cacheia 30min. Chave sem o
    fornecedor = a primeira resposta serve todos os recortes seguintes."""
    import inspect

    from estoque import routes
    src = inspect.getsource(routes._verbas_res)
    key = next(l for l in src.splitlines() if l.strip().startswith('key ='))
    assert 'fornec' in key, f'fornecedor fora da chave de cache do servidor: {key.strip()}'
    assert 'comprador' in key, f'comprador fora da chave de cache do servidor: {key.strip()}'


# ─────────── coerência de janela na página (bug 08/2026, achado pelo diretor) ───────────
# Ele olhou o gráfico com a RAZZO filtrada e perguntou "essa data está certa?". Estavam duas
# coisas erradas: o eixo listava os 14 meses QUE TIVERAM MOVIMENTO (27 meses de calendário em
# 14 barras, 13 escondidos) e o "por conta" somava a base inteira (2024+). Nenhum dos dois
# fechava com os cards — na empresa, R$ 915.676 e R$ 2.137.441 contra R$ 819.002 do card.
# O rodapé da aba já prometia "negociado/aplicado = últimos 12 meses"; eram eles que mentiam.
def _cenario_janela():
    """Verbas dentro e fora dos 12m, com meses vazios no meio (o que o eixo escondia)."""
    verbas = [
        _vb(1, "2024-03-10", 10, 5000.0),   # fora dos 12m (base 2024+)
        _vb(2, "2025-01-15", 10, 3000.0),   # fora dos 12m
        _vb(3, "2025-09-10", 10, 1000.0),   # dentro
        _vb(4, "2026-02-20", 10, 2000.0),   # dentro (com meses vazios antes e depois)
        _vb(5, "2026-07-05", 20, 700.0, conta=250008),   # dentro, outra conta
    ]
    aplics = [_ap(1, "2024-04-01", 5000.0), _ap(3, "2025-10-01", 400.0)]
    return core.verbas_fornecedores(verbas, aplics, FORN, COMP, compras_map={10: 90000.0},
                                    hoje=HOJE, cnpj_empresa=CNPJ_EMPRESA)


def test_invariante_um_numero_quatro_lugares():
    """O contrato da página: card = coluna da tabela = barras do gráfico = 'por conta'.
    É a conferência que o diretor tentou fazer de cabeça e não fechava."""
    r = _cenario_janela()
    kpi = r["resumo"]["negociado_12m"]
    assert kpi == 3700                                            # 1000 + 2000 + 700
    assert round(sum(f["negociado"] for f in r["fornecedores"]), 2) == kpi
    assert round(sum(m["negociado"] for m in r["meses"]), 2) == kpi
    assert round(sum(c["negociado"] for c in r["contas"]), 2) == kpi


def test_aplicado_do_grafico_fecha_com_o_card():
    """Mesma invariante do lado verde: aplicação fora da janela não pode entrar na série."""
    r = _cenario_janela()
    assert r["resumo"]["aplicado_12m"] == 400                     # o de 2024 fica fora
    assert round(sum(m["aplicado"] for m in r["meses"]), 2) == 400


def test_eixo_e_calendario_continuo_sem_pular_mes():
    """Mês sem verba TEM de aparecer com zero. Sem isso o Chart.js cola barras não
    consecutivas e o eixo mente sobre o espaçamento — 96% dos fornecedores têm buraco."""
    r = _cenario_janela()
    ms = [m["mes"] for m in r["meses"]]
    assert ms == sorted(ms)
    for a, b in zip(ms, ms[1:]):                                  # sempre +1 mês, nunca um salto
        ya, ma = int(a[:4]), int(a[5:])
        assert b == f"{ya + (ma == 12)}-{(ma % 12) + 1:02d}"
    assert "2025-11" in ms and next(m for m in r["meses"] if m["mes"] == "2025-11")["negociado"] == 0
    # e a janela é a dos cards: começa no mês do corte e termina no mês de hoje
    assert ms[0] == "2025-07" and ms[-1] == "2026-07"


def test_primeira_barra_marcada_como_parcial():
    """A janela são 365 DIAS, então começa no meio do mês. Sem a marca, quem comparasse a
    barra com o mês fechado do ERP acharia diferença (R$ 13.019,12 no caso real) e
    desconfiaria da tela inteira."""
    r = _cenario_janela()
    assert r["meses"][0]["parcial"] is True
    assert not any(m["parcial"] for m in r["meses"][1:])


def test_saldo_por_conta_continua_sendo_posicao():
    """Exceção proposital à janela: saldo é ESTOQUE. Verba de 2024 ainda em aberto tem de
    seguir no saldo mesmo sem entrar no negociado 12m — senão ela desaparece da cobrança."""
    r = _cenario_janela()
    c9 = next(c for c in r["contas"] if c["codconta"] == 250009)
    assert c9["negociado"] == 3000                                # 12m: só nv3 + nv4
    # saldo = posição: nv2 (2025-01, fora da janela) + o que sobrou de nv3 + nv4.
    # Repare que o SALDO é MAIOR que o negociado da própria conta — é esperado, e é a razão
    # de a tela precisar dizer que as duas colunas falam janelas diferentes.
    assert c9["saldo"] == 5600                                    # 3000 + 600 + 2000
    assert r["resumo"]["saldo_aberto"] == 6300                    # + os 700 do fornecedor 20


def test_conta_so_com_verba_antiga_ja_aplicada_nao_vira_linha_de_zeros():
    """Cortar o negociado para 12m não pode encher o painel de contas zeradas."""
    verbas = [_vb(1, "2024-03-10", 10, 5000.0, conta=250008), _vb(2, "2026-05-10", 10, 900.0)]
    r = core.verbas_fornecedores(verbas, [_ap(1, "2024-04-01", 5000.0)], FORN, COMP,
                                 hoje=HOJE, cnpj_empresa=CNPJ_EMPRESA)
    assert [c["codconta"] for c in r["contas"]] == [250009]
