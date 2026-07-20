"""Testes do YoY MENSAL dos cards do Dashboard.

Contexto: o % embaixo dos cards mostrava 12m vs 12m — um número quase imóvel (a janela
troca 1 dia de 365 por vez) e que chegou a ficar com o sinal trocado em relação ao mês
(jul/26 marcava -6,7% no card enquanto o mês corria +10,6%). Agora os cards comparam
MÊS com MÊS, ancorados no último dia COM DADO no BI.
"""
import datetime

import pytest

from tests.conftest import login_as


# ── _range_dax: o token que faz as janelas MTD atravessarem o pipeline existente ──
def test_range_dax_monta_intervalo_fechado():
    import server
    f = server._range_dax('range:2026-07-01:2026-07-18', 'FATURAMENTO_VENDAS', 'DTSAIDA')
    assert f == ('FATURAMENTO_VENDAS[DTSAIDA] >= DATE(2026,7,1) && '
                 'FATURAMENTO_VENDAS[DTSAIDA] <= DATE(2026,7,18)')


def test_range_dax_respeita_coluna_de_cada_tabela():
    """Devolução filtra por DTENT (alinhamento RCA), não por DTSAIDA."""
    import server
    f = server._range_dax('range:2025-07-01:2025-07-18', 'FATURAMENTO_DEVOLUCAO', 'DTENT')
    assert 'FATURAMENTO_DEVOLUCAO[DTENT]' in f
    assert 'DTSAIDA' not in f


@pytest.mark.parametrize('tipo', ['mes_atual', '12m', '12m_anterior', 'ytd', '2025-7'])
def test_range_dax_ignora_periodos_nomeados(tipo):
    import server
    assert server._range_dax(tipo, 'FATURAMENTO_VENDAS', 'DTSAIDA') is None


def test_periodos_nomeados_continuam_funcionando():
    """O token novo não pode ter quebrado nenhum período existente."""
    import server
    assert 'MONTH(TODAY())' in server.filtro_periodo('mes_atual')
    assert 'EDATE(TODAY(), -12)' in server.filtro_periodo('12m')
    assert server.filtro_periodo('2025-7') == (
        'YEAR(FATURAMENTO_VENDAS[DTSAIDA])=2025 && MONTH(FATURAMENTO_VENDAS[DTSAIDA])=7')


def test_filtro_periodo_aceita_range_nas_tres_tabelas():
    import server
    t = 'range:2026-07-01:2026-07-18'
    assert 'FATURAMENTO_VENDAS[DTSAIDA] >= DATE(2026,7,1)' in server.filtro_periodo(t)
    assert 'FATURAMENTO_DEVOLUCAO[DTENT] >= DATE(2026,7,1)' in server.filtro_periodo_devol(t)
    assert 'FATURAMENTO_DEVOLUCAO_AVULSA[DTENT] >= DATE(2026,7,1)' in server.filtro_periodo_devol_av(t)


# ── Janelas: ancoradas no dado, não em TODAY() ──
def _fixar(monkeypatch, corte, hoje):
    """Fixa o corte do BI e a data de 'hoje' dentro de server."""
    import server

    class _FakeDate(datetime.date):
        @classmethod
        def today(cls):
            return hoje

    monkeypatch.setattr(server, '_corte_dados', lambda: corte)
    monkeypatch.setattr(datetime, 'date', _FakeDate)


def test_janela_ancora_no_ultimo_dia_com_dado_nao_em_today(monkeypatch):
    """BI atrasado 2 dias: compara 01-18 contra 01-18, não 01-20 contra 01-20.
    Usar TODAY() colocaria 18 dias deste ano contra 20 do ano passado."""
    _fixar(monkeypatch, datetime.date(2026, 7, 18), datetime.date(2026, 7, 20))
    import server
    j = server._janelas_yoy_mes()
    assert j['atual'] == (datetime.date(2026, 7, 1), datetime.date(2026, 7, 18))
    assert j['anterior'] == (datetime.date(2025, 7, 1), datetime.date(2025, 7, 18))


def test_janela_clampa_dia_no_fim_do_mes_do_ano_anterior(monkeypatch):
    """29/02/2024 (bissexto) → o ano anterior não tem dia 29; clampa em 28/02/2023."""
    _fixar(monkeypatch, datetime.date(2024, 2, 29), datetime.date(2024, 2, 29))
    import server
    j = server._janelas_yoy_mes()
    assert j['atual'][1] == datetime.date(2024, 2, 29)
    assert j['anterior'][1] == datetime.date(2023, 2, 28)


def test_janela_none_quando_bi_ainda_nao_carregou_o_mes_novo(monkeypatch):
    """Virou o mês e o BI só tem dado do mês passado → o card mostra R$ 0.
    Comparar isso com um mês inteiro daria um -100% falso; melhor não mostrar %."""
    _fixar(monkeypatch, datetime.date(2026, 6, 30), datetime.date(2026, 7, 2))
    import server
    assert server._janelas_yoy_mes() is None


def test_janela_none_sem_corte(monkeypatch):
    import server
    monkeypatch.setattr(server, '_corte_dados', lambda: None)
    assert server._janelas_yoy_mes() is None


# ── Dias úteis (informativo do tooltip) ──
def test_dias_uteis_entre_ignora_fim_de_semana():
    import server
    # 01-18/jul/2026: 13 dias úteis | 01-18/jul/2025: 14
    assert server._dias_uteis_entre(datetime.date(2026, 7, 1), datetime.date(2026, 7, 18)) == 13
    assert server._dias_uteis_entre(datetime.date(2025, 7, 1), datetime.date(2025, 7, 18)) == 14


def test_meta_expoe_rotulo_e_distorcao_de_calendario():
    """O tooltip precisa denunciar que 13 dias úteis estão sendo comparados com 14."""
    import server
    meta = server._yoy_mes_meta({
        'atual':    (datetime.date(2026, 7, 1), datetime.date(2026, 7, 18)),
        'anterior': (datetime.date(2025, 7, 1), datetime.date(2025, 7, 18)),
    })
    assert meta['rotulo'] == 'vs jul/25'
    assert meta['periodo'] == '01–18/jul/26 vs 01–18/jul/25'
    assert (meta['dias_uteis'], meta['dias_uteis_anterior']) == (13, 14)


# ── Query ──
def test_yoy_mes_query_usa_as_duas_janelas(app, monkeypatch):
    import server
    j = {'atual':    (datetime.date(2026, 7, 1), datetime.date(2026, 7, 18)),
         'anterior': (datetime.date(2025, 7, 1), datetime.date(2025, 7, 18))}
    with app.test_request_context('/'):
        from flask import session
        session['role'] = 'admin'
        q = server._yoy_mes_query(j, None)
    assert 'DATE(2026,7,1)' in q and 'DATE(2026,7,18)' in q
    assert 'DATE(2025,7,1)' in q and 'DATE(2025,7,18)' in q
    # sem resíduo da janela de 12 meses
    assert 'EDATE(TODAY(), -12)' not in q
    # devoluções entram por DTENT nas duas pontas (alinhamento RCA preservado)
    assert 'FATURAMENTO_DEVOLUCAO[DTENT] >= DATE(2025,7,1)' in q


def test_yoy_mes_query_carrega_rbac_de_supervisor(app):
    """O recorte do usuário logado tem que sobreviver às janelas novas."""
    import server
    j = {'atual':    (datetime.date(2026, 7, 1), datetime.date(2026, 7, 18)),
         'anterior': (datetime.date(2025, 7, 1), datetime.date(2025, 7, 18))}
    with app.test_request_context('/'):
        from flask import session
        session['role'] = 'supervisor'
        session['codsupervisores'] = [18, 19]
        q = server._yoy_mes_query(j, None)
    assert 'CODSUPERVISOR] IN {18, 19}' in q


# ── Endpoint ──
def test_kpis_devolve_yoy_mes_e_mantem_yoy_12m(client, usuario_admin, mock_dax_capture,
                                                clean_redis, monkeypatch):
    """Contrato: os cards passam a ler yoy_mes; o gráfico segue lendo yoy (12m)."""
    _fixar(monkeypatch, datetime.date(2026, 7, 18), datetime.date(2026, 7, 20))
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    r = client.get('/api/dashboard/kpis')
    assert r.status_code == 200
    d = r.get_json()
    assert 'yoy' in d, '12m vs 12m removido — o gráfico YoY depende dele'
    assert d['yoy_mes'] is not None
    assert d['yoy_mes_info']['rotulo'] == 'vs jul/25'
    # a query mensal foi de fato disparada
    assert any('DATE(2026,7,18)' in q for q in mock_dax_capture.queries)


def test_kpis_yoy_mes_none_na_virada_de_mes(client, usuario_admin, mock_dax_capture,
                                             clean_redis, monkeypatch):
    """Sem carga do mês novo: yoy_mes vem None e o frontend mostra '—'."""
    _fixar(monkeypatch, datetime.date(2026, 6, 30), datetime.date(2026, 7, 2))
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    r = client.get('/api/dashboard/kpis')
    assert r.status_code == 200
    d = r.get_json()
    assert d['yoy_mes'] is None
    assert d['yoy_mes_info'] is None
