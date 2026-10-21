"""Gate do item ZERADO que vendeu no mês corrente (10/2026, achado pelo João Victor).

"O item cod 56664 não aparece na JOGA, porém está ativo no Winthor."

O `q_snapshot_estoque` corta quem tem estoque zero E venda zero nos 3 meses fechados. Certo para
o catálogo morto (1.607 itens de revenda no Atacado em 20/10/2026), errado para o 56664: vendeu em
abr–jun, nada em jul–set, 8 un em 10/10, zerado em todas as filiais. A ruptura que dura 3 meses
apagava o item justamente da tela que mede ruptura.

O que estes testes protegem:
  · volta SÓ o item do universo, fora do snapshot, que vendeu no MÊS CORRENTE (opção estreita):
    "vendeu nos últimos 6 meses" traria pico pontual e item fora de linha com sugestão de compra;
  · ele entra como RUPTURA (giro > 0), nunca como "sem giro" de estoque zero;
  · a consulta extra tem as MESMAS colunas do snapshot, e o snapshot de sempre não muda;
  · BI fora → o painel segue com o snapshot de sempre; o modo BD (demo) tem espelho.
"""
import inspect
from datetime import date
from pathlib import Path

import pytest

from estoque import core, queries as Q, routes

HOJE = date(2026, 10, 20)
JS = Path('static/estoque/estoque.js').read_text(encoding='utf-8')

# O 56664 como veio do RCA em 20/10/2026 (venda mensal, CODOPER S, filiais 3/7/8)
SERIE_56664 = {202604: 3.0, 202605: 5.0, 202606: 4.0, 202610: 8.0}
# O 55465: pico em abril e quase nada depois — o caso que a opção larga traria com 414/mês
SERIE_55465 = {202604: 2209.0, 202605: 277.0}


def _snap(*cods, qt=10.0):
    return [{"CODPROD": float(c), "qtestger": qt, "giro_m1": 1.0, "giro_m2": 1.0, "giro_m3": 1.0}
            for c in cods]


def _zerada(cod):
    """A linha do PCEST de um item zerado em todas as filiais, como a consulta extra devolve."""
    return {"CODPROD": float(cod), "qtestger": 0.0, "qtreserv": 0.0, "qtbloq": 0.0, "qtpend": 0.0,
            "qttransito": 0.0, "giro_m1": 0.0, "giro_m2": 0.0, "giro_m3": 0.0,
            "custofin": 74.83781, "dtultsaida": "2026-10-10T00:00:00",
            "dtultent": "2026-08-14T00:00:00"}


CAD = {56664: {"descricao": "EMB.GALV.GA13 P/DOCE CRISTAL QUADRADA", "codfornec": 8579},
       55465: {"descricao": "PICO EM ABRIL"}, 10: {"descricao": "COM ESTOQUE"},
       70199: {"descricao": "CÓDIGO NOVO, SÓ PEDIDO"}}


# ─────────────────────── a regra (core) ───────────────────────
def test_o_caso_do_56664_volta():
    assert core.zerados_com_venda_no_mes(_snap(10), CAD, {56664: SERIE_56664}, HOJE) == [56664]


def test_venda_so_em_meses_anteriores_NAO_volta():
    """Opção estreita: o 55465 teria giro 414/mês pelo `giro_novo_item` e sugestão de compra."""
    assert core.zerados_com_venda_no_mes(_snap(10), CAD, {55465: SERIE_55465}, HOJE) == []


def test_item_fora_do_universo_NAO_volta():
    """Não-revenda / fora de linha continua fora, vendendo ou não."""
    assert core.zerados_com_venda_no_mes(_snap(10), CAD, {99999: {202610: 5.0}}, HOJE) == []


def test_item_que_ja_esta_no_snapshot_nao_duplica():
    """O CODPROD vem float do BI: a comparação passa pelo `_n`, senão 56664.0 ≠ 56664."""
    assert core.zerados_com_venda_no_mes(_snap(56664), CAD, {56664: SERIE_56664}, HOJE) == []


def test_zerado_so_com_pedido_em_aberto_NAO_volta():
    """Sem venda no mês ele entraria com giro 0 e estoque 0 — o card "sem giro" do painel conta
    todo item de giro zero, e inflaria. Aparece no Orçamento."""
    assert core.zerados_com_venda_no_mes(_snap(10), CAD, {70199: {}}, HOJE) == []


def test_sem_serie_mensal_nada_volta():
    """RCA fora → `_vendas_mensal_map` devolve {}: o painel fica como antes, sem erro."""
    assert core.zerados_com_venda_no_mes(_snap(10), CAD, {}, HOJE) == []
    assert core.zerados_com_venda_no_mes(_snap(10), CAD, None, HOJE) == []


def test_o_item_que_volta_entra_como_RUPTURA():
    """Giro pelo fallback que já existia (média desde a 1ª venda dos últimos 6 meses fechados:
    abr–set = 12 un ÷ 6 = 2/mês), disponível zero, status urgente."""
    p = core.construir_produtos([_zerada(56664)], {}, CAD, {}, {}, {}, core.merge_params({}),
                                hoje=HOJE, venda_mensal_map={56664: SERIE_56664})[0]
    assert p["qtdisp"] == 0
    assert p["giro_mes"] == 2 and p["giro_fonte"] == "novo_item"
    assert p["status_abast"] == "urgente"


def test_sem_historico_nos_6_meses_o_giro_e_a_venda_do_mes():
    p = core.construir_produtos([_zerada(56664)], {}, CAD, {}, {}, {}, core.merge_params({}),
                                hoje=HOJE, venda_mensal_map={56664: {202610: 8.0}})[0]
    assert p["giro_mes"] == 8 and p["giro_fonte"] == "mes_corrente"


# ─────────────────────── a query ───────────────────────
def _colunas(q):
    return q[q.index("ADDCOLUMNS"):q.index("    )") + 5]


def test_a_query_extra_filtra_os_codigos_e_tem_as_MESMAS_colunas():
    padrao = Q.q_snapshot_estoque(["3", "5"])
    extra = Q.q_snapshot_estoque(["3", "5"], codprods=[56664, 57042])
    assert "PCEST[CODPROD] IN {56664, 57042}" in extra
    assert 'PCEST[CODFILIAL] IN {"3", "5"}' in extra
    assert "[qtestger] <> 0" not in extra
    assert _colunas(extra) == _colunas(padrao)


def test_lista_VAZIA_nao_vira_o_snapshot_inteiro():
    """Cair no corte de atividade devolveria ~2.900 linhas, e quem concatena duplicaria tudo."""
    q = Q.q_snapshot_estoque(["3", "5"], codprods=[])
    assert "FALSE()" in q and "[qtestger] <> 0" not in q and "IN {}" not in q
    from estoque import provider_sql as PS
    src = inspect.getsource(PS.snapshot_estoque)
    assert "if codprods is not None" in src


def test_o_snapshot_de_sempre_NAO_mudou():
    q = Q.q_snapshot_estoque(["3", "5"])
    assert "[qtestger] <> 0 || [giro_m1] <> 0 || [giro_m2] <> 0 || [giro_m3] <> 0" in q
    assert "PCEST[CODPROD] IN" not in q


# ─────────────────────── a rota ───────────────────────
@pytest.fixture
def app_ctx():
    import server  # noqa: F401
    from server import app
    return app


def _mock_bi(monkeypatch, extra=None, explode=False, chamadas=None):
    def _dax(q, *a, **k):
        if chamadas is not None:
            chamadas.append(q)
        if explode:
            raise RuntimeError("BI fora")
        return extra or []
    monkeypatch.setattr(routes.pbi, "run_dax", _dax)
    monkeypatch.setitem(routes.pbi.CONFIG, "data_source", "powerbi")
    routes.pbi._CACHE.clear()


def test_rota_junta_a_linha_do_zerado_ao_snapshot(monkeypatch):
    chamadas = []
    _mock_bi(monkeypatch, extra=[_zerada(56664)], chamadas=chamadas)
    snap = _snap(10)
    out = routes._snapshot_com_venda_no_mes(snap, ["3", "5"], CAD, {56664: SERIE_56664}, HOJE)
    assert [int(r["CODPROD"]) for r in out] == [10, 56664]
    assert len(chamadas) == 1 and "PCEST[CODPROD] IN {56664}" in chamadas[0]
    assert len(snap) == 1          # não muta o snapshot que está em cache


def test_sem_zerado_vendendo_nao_ha_consulta_extra(monkeypatch):
    chamadas = []
    _mock_bi(monkeypatch, chamadas=chamadas)
    snap = _snap(10)
    assert routes._snapshot_com_venda_no_mes(snap, ["3", "5"], CAD, {}, HOJE) is snap
    assert chamadas == []


def test_BI_fora_o_painel_segue_com_o_snapshot_de_sempre(monkeypatch):
    _mock_bi(monkeypatch, explode=True)
    out = routes._snapshot_com_venda_no_mes(_snap(10), ["3", "5"], CAD, {56664: SERIE_56664}, HOJE)
    assert [int(r["CODPROD"]) for r in out] == [10]


def test_segunda_carga_vem_do_cache(monkeypatch):
    chamadas = []
    _mock_bi(monkeypatch, extra=[_zerada(56664)], chamadas=chamadas)
    for _ in range(2):
        routes._snapshot_com_venda_no_mes(_snap(10), ["3", "5"], CAD, {56664: SERIE_56664}, HOJE)
    assert len(chamadas) == 1


def test_build_produtos_passa_pelo_complemento_DEPOIS_da_serie_mensal():
    """A regra precisa da série do RCA; chamada antes dela, nada voltaria."""
    src = inspect.getsource(routes._build_produtos)
    i_serie = src.index("venda_mensal = _vendas_mensal_map(12")
    i_comp = src.index("snap = _snapshot_com_venda_no_mes(snap, filiais_e, prod_map, venda_mensal")
    i_cons = src.index("core.construir_produtos(snap,")
    assert i_serie < i_comp < i_cons


# ─────────────────────── multi-fonte ───────────────────────
def test_modo_postgres_tem_provider_espelhado():
    """Sem o espelho, em modo BD o `run_dax` levanta (rede de segurança) e o item não volta na demo."""
    from estoque import provider_sql as PS
    src = inspect.getsource(PS.snapshot_estoque)
    assert "codprod = ANY(%s)" in src
    assert "HAVING sum(qtestger)<>0 OR sum(qtvendmes1)<>0" in src
    assert "codprods=faltam" in inspect.getsource(routes._snapshot_com_venda_no_mes)


# ─────────────────────── a tela ───────────────────────
def test_o_360_nao_afirma_que_todo_giro_do_fallback_e_de_item_novo():
    """O 56664 é item antigo que ficou 3 meses sem vender — "menos de 3 meses de casa" seria falso."""
    assert "menos de 3 meses de casa" not in JS
    assert "só começou a vender agora" not in JS
    assert "produto novo ou que ficou sem vender" in JS
