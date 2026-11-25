"""Gate dos dois pedidos do João Victor de 24–25/11/2026 no Compras.

1. **"Esse item não tem venda, deveria estar em estoque parado"** (dois itens). O
   `PCEST[DTULTSAIDA]` é a última SAÍDA de qualquer tipo: os dois tinham uma em 24/11/2026 com o
   estoque intacto e nenhuma venda desde 2025. "Dias sem venda" passa a contar pela última VENDA
   do RCA (`core.data_ultima_venda`).
2. **"Na tabela está zerado e na aba lateral tem estoque"** (um item). A tabela é a foto da
   carga da página; o BI atualizou com a tela aberta e o cache do servidor segurava o dado velho.
   Agora o cache é limpo quando o BI atualiza, a tela pergunta a hora do BI e a linha segue a gaveta.
"""
from datetime import date, timedelta
from pathlib import Path

import pytest

from estoque import core, pbi, queries as Q, routes

HOJE = date(2026, 11, 25)
DESDE = date(2024, 1, 2)            # 1º dia da FATURAMENTO_VENDAS no RCA real
JS = Path('static/estoque/estoque.js').read_text(encoding='utf-8')


def _snap(cod, dtultsaida, dtultent="2024-09-19", qt=57.0, g=(0, 0, 0)):
    return {"CODPROD": cod, "CODFILIAL": "3", "qtestger": qt, "qtbloq": 0.0, "qtreserv": 0.0,
            "giro_m1": g[0], "giro_m2": g[1], "giro_m3": g[2], "custofin": 33.4,
            "dtultsaida": dtultsaida, "dtultent": dtultent}


def _cad(*cods):
    return {c: {"CODPROD": c, "DESCRICAO": f"ITEM {c}", "CODFORNEC": 1, "QTUNITCX": 1} for c in cods}


def _prod(snap, ultima_venda, **kw):
    cods = [r["CODPROD"] for r in snap]
    out = core.construir_produtos(snap, {}, _cad(*cods), {}, {}, {}, core.merge_params({}),
                                  hoje=HOJE, ultima_venda=ultima_venda, **kw)
    return {p["codprod"]: p for p in out}


# ── a regra ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("saida, venda, esperado", [
    (date(2026, 11, 24), date(2025, 12, 31), date(2025, 12, 31)),   # saída que não era venda
    (date(2023, 5, 1), None, date(2023, 5, 1)),                     # antes do histórico: vale ela
    (date(2026, 11, 24), None, DESDE - timedelta(days=1)),          # saída sem venda no histórico
    (None, None, None),                                             # nunca saiu = nunca vendeu
    (date(2025, 1, 10), date(2026, 3, 1), date(2026, 3, 1)),        # a venda é a verdade
])
def test_data_ultima_venda(saida, venda, esperado):
    assert core.data_ultima_venda(saida, venda, DESDE) == esperado


def test_sem_historico_do_rca_volta_ao_dtultsaida():
    assert core.data_ultima_venda(date(2026, 11, 24), None, None) == date(2026, 11, 24)


# ── o caso do João ───────────────────────────────────────────────────
def test_saida_que_nao_e_venda_volta_para_o_parado():
    uv = {"por_cod": {1001: date(2025, 12, 31), 1002: date(2025, 8, 28)}, "desde": DESDE}
    ps = _prod([_snap(1001, "2026-11-24"), _snap(1002, "2026-11-24", dtultent="2023-09-12")], uv)
    assert ps[1001]["dias_sem_venda"] == 329 and ps[1002]["dias_sem_venda"] == 454
    assert ps[1001]["parado_faixa"] == "121+" and ps[1002]["parado_faixa"] == "121+"
    assert ps[1001]["dtultsaida"] == "2025-12-31"        # a "Última venda" da gaveta e do Excel


def test_antes_eles_sumiam_do_parado():
    """A régua antiga (sem o mapa): a saída de ontem tirava o item do Parado."""
    ps = _prod([_snap(1001, "2026-11-24")], None)
    assert ps[1001]["dias_sem_venda"] == 1 and ps[1001]["parado_faixa"] is None


def test_saida_sem_venda_no_historico_nao_vira_novo():
    """Sem venda no RCA e com entrada recente: `None` faria dele "Novo" (nunca vendeu, não teve
    chance). Ele pode ter vendido antes de 2024 — sai em "Recém-chegado", a caixa de quem JÁ
    vendeu, parou e acabou de receber; sem a entrada recente, 121+."""
    uv = {"por_cod": {1: date(2026, 11, 1)}, "desde": DESDE}
    ps = _prod([_snap(1003, "2026-11-17", dtultent="2026-11-20"),
                _snap(1004, "2026-11-17", dtultent="2025-02-01")], uv)
    assert ps[1003]["parado_faixa"] == core.PARADO_RECEM_CHEGADO
    assert ps[1004]["parado_faixa"] == "121+"


def test_materia_prima_segue_a_saida_porque_nao_vende():
    """Bobina da fábrica é consumida, não vendida: pela régua de venda viraria parada."""
    uv = {"por_cod": {}, "desde": DESDE}
    ps = _prod([_snap(900, "2026-11-20", g=(0, 0, 0))], uv, giro_industria_map={900: 40})
    assert ps[900]["dias_sem_venda"] == 5


def test_item_com_consumo_de_producao_segue_a_saida():
    uv = {"por_cod": {901: date(2025, 1, 1)}, "desde": DESDE}
    ps = _prod([_snap(901, "2026-11-20")], uv, consumo_map={901: {"m1": 12, "m2": 0, "m3": 0}})
    assert ps[901]["dias_sem_venda"] == 5


# ── a consulta e a degradação ────────────────────────────────────────
def test_query_usa_filtro_de_coluna_e_so_venda():
    q = Q.q_ultima_venda_rca(["3", "7", "8"])
    assert 'TREATAS({"S"}, FATURAMENTO_VENDAS[CODOPER])' in q
    assert 'TREATAS({"3", "7", "8"}, FATURAMENTO_VENDAS[CODFILIAL])' in q
    assert "FILTER(FATURAMENTO_VENDAS" not in q        # filtro de tabela no histórico estoura o RCA


def test_rca_fora_degrada_para_none_e_nao_para_vazio(monkeypatch):
    """Mapa vazio com `desde` diria "ninguém vendeu" e jogaria o catálogo em 121+."""
    pbi._CACHE.clear()
    monkeypatch.setattr(routes, "_pg", lambda: False)
    monkeypatch.setattr(routes.pbi, "run_dax_rca", lambda q: (_ for _ in ()).throw(RuntimeError("fora")))
    assert routes._ultima_venda(["3"], HOJE) is None
    monkeypatch.setattr(routes.pbi, "run_dax_rca", lambda q: [])
    assert routes._ultima_venda(["3"], HOJE) is None


def test_ultima_venda_le_o_rca(monkeypatch):
    pbi._CACHE.clear()
    monkeypatch.setattr(routes, "_pg", lambda: False)
    monkeypatch.setattr(routes.pbi, "run_dax_rca", lambda q: [
        {"CODPROD": 1001, "ult": "2025-12-31T00:00:00", "desde": "2024-01-02T00:00:00"}])
    assert routes._ultima_venda(["3"], HOJE) == {"por_cod": {1001: date(2025, 12, 31)}, "desde": DESDE}


# ── tabela × gaveta ──────────────────────────────────────────
class _Resp:
    def __init__(self, end):
        self._end = end

    def raise_for_status(self):
        pass

    def json(self):
        return {"value": [{"status": "Completed", "endTime": self._end}]}


def test_bi_atualizou_limpa_o_cache(monkeypatch):
    pbi._CACHE.clear()
    pbi._REFRESH_VISTO.clear()
    monkeypatch.setitem(pbi.CONFIG, "data_source", "powerbi")
    monkeypatch.setattr(pbi, "get_token", lambda: "t")
    ends = iter(["2026-11-24T17:24:00Z", "2026-11-24T19:23:00Z"])
    monkeypatch.setattr(pbi.requests, "get", lambda *a, **k: _Resp(next(ends)))
    pbi.get_dataset_refresh()
    pbi._CACHE.set("snap:3,5", ["velho"], 1800)
    pbi._CACHE._store.pop("pbi:refresh:" + pbi.CONFIG["dataset_id"], None)   # vence os 5 min
    pbi.get_dataset_refresh()
    assert pbi._CACHE.get("snap:3,5") is None


def test_mesma_atualizacao_nao_limpa(monkeypatch):
    pbi._CACHE.clear()
    pbi._REFRESH_VISTO.clear()
    monkeypatch.setitem(pbi.CONFIG, "data_source", "powerbi")
    monkeypatch.setattr(pbi, "get_token", lambda: "t")
    monkeypatch.setattr(pbi.requests, "get", lambda *a, **k: _Resp("2026-11-24T17:24:00Z"))
    pbi.get_dataset_refresh()
    pbi._CACHE.set("snap:3,5", ["ok"], 1800)
    pbi._CACHE._store.pop("pbi:refresh:" + pbi.CONFIG["dataset_id"], None)
    pbi.get_dataset_refresh()
    assert pbi._CACHE.get("snap:3,5") == ["ok"]


def test_tela_pergunta_a_hora_do_bi_e_a_gaveta_sincroniza_a_linha():
    assert "'/estoque/api/bi-refresh'" in JS and "setInterval(biNovoCheca" in JS
    assert "sincronizaLinha(p)" in JS
    assert "<span>Última venda</span>" in JS and "<span>Última saída</span>" not in JS
