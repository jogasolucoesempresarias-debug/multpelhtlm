"""Evolução da carteira — histórico de performance do Comercial (pedido do Gabriel, 05/10/2026:
"criar um histórico de performance na gestão de carteira, igual temos na gestão de estoque… e
cruzar essa evolução, quem usa a plataforma versus quem não usa").

Diferença de fundo para a foto do estoque: aqui quase tudo é EVENTO (venda), então a série se
reconstrói PARA TRÁS a partir do fato — nada de gravar "cobertura = X%" (congelaria a régua do dia).
Só o que o sistema SOBRESCREVE é fotografado: dono do cliente (`carteira_foto`, já existia),
vendedor → time (`vendedor_foto`) e o uso por pessoa (`uso_mensal`, porque o `multpel_log` é
expurgado aos 12 meses).

Decisões travadas aqui:
- cobertura = a RÉGUA ÚNICA (`cobertura.cobertura_por_dono`) no fim de cada mês FECHADO — o último
  mês da série tem de ser o número da Performance;
- time agrega CONTAGENS (Σ positivados ÷ Σ base), nunca média de percentuais;
- "usa a plataforma" é do TIME: algum usuário ligado a ele (supervisor pelas áreas, vendedor pelo
  time dele) com ≥ `limiar` dias ativos num mês a partir do marco; todos os times entram (decisão
  do Gabriel: medir tudo e filtrar na tela);
- antes × depois = MÉDIA DO PERÍODO (contagens somadas), nunca dia de início contra dia de fim —
  a lição do relatório do estoque.
"""
from datetime import date
from pathlib import Path

import pytest

import evolucao_carteira as ev
from recuperacao import ATIVO, RISCO


# ───────────────────────── calendário ─────────────────────────
def test_meses_fechados_e_fim_do_mes():
    assert ev.meses_fechados(202609, 4) == [202606, 202607, 202608, 202609]
    assert ev.meses_fechados(202602, 3) == [202512, 202601, 202602]
    assert ev.fim_do_mes(202602) == date(2026, 2, 28) and ev.fim_do_mes(202609) == date(2026, 9, 30)


# ───────────────────────── cobertura por mês ─────────────────────────
def test_cobertura_do_time_soma_contagens_e_nao_media_percentuais():
    fim = date(2026, 9, 30)
    # RCA 1 (time 10): 10 na base, 8 positivados (80%); RCA 2 (time 10): 5 na base, 1 positivado (20%)
    ultima, dono = {}, {}
    for i in range(10):
        ultima[i] = date(2026, 9, 20) if i < 8 else date(2026, 5, 1)
        dono[i] = 1
    for i in range(10, 15):
        ultima[i] = date(2026, 9, 20) if i == 10 else date(2026, 5, 1)
        dono[i] = 2
    c = ev.cobertura_mes(ultima, dono, {1: 10, 2: 10}, fim)
    assert (c['rcas'][1]['base'], c['rcas'][1]['positivados']) == (10, 8)
    assert (c['times'][10]['base'], c['times'][10]['positivados']) == (15, 9)   # 60%, não (80+20)/2


def test_cliente_sem_dono_ou_de_vendedor_sem_time_vai_para_sem_time():
    c = ev.cobertura_mes({1: date(2026, 9, 1)}, {1: 99}, {}, date(2026, 9, 30))
    assert c['times'][None]['base'] == 1


# ───────────────────────── linha do mês ─────────────────────────
def test_linha_do_mes_junta_cobertura_e_recuperacao():
    ln = ev.linha(anomes=202609, base=100, positivados=60, em_risco=20, valor_em_risco=5000.0,
                  entraram=9, recuperados=16, valor_saldo=6419.0)
    assert ln['cobertura'] == pytest.approx(0.60) and ln['pct_risco'] == pytest.approx(0.20)
    assert ln['saldo'] == 7                    # recuperados − entraram: positivo = bom
    vazio = ev.linha(anomes=202609, base=0, positivados=0, em_risco=0, valor_em_risco=0.0,
                     entraram=0, recuperados=0, valor_saldo=0.0)
    assert vazio['cobertura'] is None and vazio['pct_risco'] is None


def test_somar_linhas_recalcula_percentuais():
    a = ev.linha(202609, 100, 60, 20, 10.0, 5, 3, 1.0)
    b = ev.linha(202609, 50, 10, 5, 5.0, 2, 4, -2.0)
    s = ev.somar([a, b], 202609)
    assert (s['base'], s['positivados'], s['saldo']) == (150, 70, 0)
    assert s['cobertura'] == pytest.approx(70 / 150) and s['valor_em_risco'] == 15.0


# ───────────────────────── uso → time ─────────────────────────
USO = [
    # supervisor das áreas 10 e 11, 6 dias em ago
    {'anomes': 202608, 'usuario_id': 1, 'role': 'supervisor', 'codusur': None, 'codsupervisores': [10, 11],
     'dias_ativos': 6},
    # vendedor 5 (time 12 em ago), 2 dias
    {'anomes': 202608, 'usuario_id': 2, 'role': 'vendedor', 'codusur': 5, 'codsupervisores': [],
     'dias_ativos': 2},
    # vendedor 5 em set, 7 dias
    {'anomes': 202609, 'usuario_id': 2, 'role': 'vendedor', 'codusur': 5, 'codsupervisores': [],
     'dias_ativos': 7},
    # diretoria (viewer) não é de time nenhum
    {'anomes': 202609, 'usuario_id': 3, 'role': 'viewer', 'codusur': None, 'codsupervisores': [],
     'dias_ativos': 20},
]
TIME_MES = {202608: {5: 12}, 202609: {5: 12}}


def test_uso_e_atribuido_ao_time_pelo_papel():
    u = ev.uso_por_time(USO, TIME_MES)
    assert u[202608][10]['dias_ativos'] == 6 and u[202608][11]['dias_ativos'] == 6
    assert u[202608][12]['dias_ativos'] == 2 and u[202609][12]['dias_ativos'] == 7
    assert all(None not in por for por in u.values())          # viewer não vira "time"


def test_time_usa_a_partir_do_primeiro_mes_acima_do_limiar():
    u = ev.uso_por_time(USO, TIME_MES)
    cl = ev.classificar_uso(u, times=[10, 11, 12, 13], marco=202608, limiar=4)
    assert cl[10] == {'usa': True, 'comecou_em': 202608}
    assert cl[12] == {'usa': True, 'comecou_em': 202609}       # ago teve só 2 dias
    assert cl[13] == {'usa': False, 'comecou_em': None}        # medido mesmo sem login
    cl8 = ev.classificar_uso(u, times=[12], marco=202608, limiar=8)
    assert cl8[12]['usa'] is False


# ───────────────────────── antes × depois ─────────────────────────
def _serie():
    out = []
    for am, base, pos, risco, var in ((202605, 100, 50, 30, 4), (202606, 100, 52, 30, 2),
                                      (202607, 100, 48, 32, 3), (202608, 100, 60, 25, -2),
                                      (202609, 100, 64, 22, -4)):
        out.append(ev.linha(am, base, pos, risco, 0.0, max(var, 0) + 5, 5 - min(var, 0), 0.0))
    return out


def test_antes_depois_e_media_do_periodo_com_contagens():
    ad = ev.antes_depois(_serie(), marco=202608, n_antes=3)
    assert ad['antes']['meses'] == [202605, 202606, 202607]
    assert ad['depois']['meses'] == [202608, 202609]
    assert ad['antes']['cobertura'] == pytest.approx(150 / 300)
    assert ad['depois']['cobertura'] == pytest.approx(124 / 200)
    assert ad['delta']['cobertura_pp'] == pytest.approx((124 / 200 - 150 / 300) * 100)
    assert ad['antes']['saldo_medio'] == pytest.approx(-3.0)
    assert ad['depois']['saldo_medio'] == pytest.approx(3.0)


def test_antes_depois_sem_meses_suficientes_nao_inventa():
    ad = ev.antes_depois(_serie()[3:], marco=202608, n_antes=3)
    assert ad['antes']['meses'] == [] and ad['antes']['cobertura'] is None
    assert ad['delta']['cobertura_pp'] is None


# ───────────────────────── telas e fotos ─────────────────────────
def test_menu_e_tela():
    assert "href: '/evolucao'" in Path('static/joga-header.js').read_text(encoding='utf-8')
    h = Path('evolucao.html').read_text(encoding='utf-8')
    for t in ('/api/evolucao-carteira', 'universo', 'Antes', 'Depois', 'dono_fonte'):
        assert t in h, t
    # 06/10/2026: a classificação "usa / não usa" saiu da tela (contaminada por um usuário com quase
    # todas as áreas); o backend continua gravando o uso para ela voltar corrigida
    for t in ('selUso', 'selLimiar', 'Times que usam', 'Uso da plataforma</th>'):
        assert t not in h, t


def test_fotos_novas_no_init_db():
    s = Path('init_db.py').read_text(encoding='utf-8')
    assert 'CREATE TABLE IF NOT EXISTS vendedor_foto' in s
    assert 'CREATE TABLE IF NOT EXISTS uso_mensal' in s


# ══════════════ servidor (modo postgres = base da demo) ══════════════
@pytest.fixture
def pg(monkeypatch):
    import server
    server._R.flushall()
    monkeypatch.setitem(server.CONFIG, 'data_source', 'postgres')
    return server


def test_foto_do_mes_grava_vendedor_e_time(pg):
    server = pg
    try:
        n = server._fotografar_carteira(anomes=209912)
        assert n > 0
        conn = server.get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT count(*), count(codsupervisor) FROM vendedor_foto WHERE anomes=209912")
            tot, com_time = cur.fetchone()
        conn.close()
        assert tot > 0 and com_time > 0
    finally:
        conn = server.get_db()
        with conn, conn.cursor() as cur:
            cur.execute("DELETE FROM carteira_foto WHERE anomes=209912")
            cur.execute("DELETE FROM vendedor_foto WHERE anomes=209912")
        conn.close()


def test_uso_mensal_consolida_dias_ativos_no_fuso_de_brasilia(pg):
    server = pg
    from tests.conftest import _criar_usuario, _remover_usuario
    uid = _criar_usuario('uso-evol@teste.local', 'x', role='supervisor', codsupervisor=12,
                         codsupervisores=[12, 14])
    conn = server.get_db()
    with conn, conn.cursor() as cur:
        # 3 logins em 2 dias de Brasília: 02/01 23:30 BRT = 03/01 02:30 UTC conta como dia 02
        for ts in ('2099-01-02 15:00+00', '2099-01-03 02:30+00', '2099-01-05 12:00+00'):
            cur.execute("INSERT INTO multpel_log (usuario_id, rota, acessado_em) VALUES (%s,'login:sucesso',%s)",
                        (uid, ts))
        cur.execute("INSERT INTO multpel_log (usuario_id, rota, acessado_em) VALUES (%s,'export:/api/x','2099-01-05 12:05+00')",
                    (uid,))
    conn.close()
    try:
        server._consolidar_uso_mensal(meses=[209901])
        conn = server.get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT role, codsupervisores, dias_ativos, logins, downloads FROM uso_mensal "
                        "WHERE anomes=209901 AND usuario_id=%s", (uid,))
            role, sups, dias, logins, downs = cur.fetchone()
        conn.close()
        assert role == 'supervisor' and sorted(sups) == [12, 14]
        assert (dias, logins, downs) == (2, 3, 1)
    finally:
        conn = server.get_db()
        with conn, conn.cursor() as cur:
            cur.execute("DELETE FROM multpel_log WHERE usuario_id=%s", (uid,))
            cur.execute("DELETE FROM uso_mensal WHERE usuario_id=%s", (uid,))
        conn.close()
        _remover_usuario('uso-evol@teste.local')


def test_endpoint_empresa_times_e_ultimo_mes_bate_com_a_performance(client, usuario_admin, pg):
    server = pg
    from tests.conftest import login_as
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    j = client.get('/api/evolucao-carteira').get_json()
    assert j['ok'] and len(j['meses']) == 12 and len(j['empresa']) == 12
    assert set(j['dono_fonte']) == set(str(m) for m in j['meses'])
    assert j['times'] and all({'codsupervisor', 'nome', 'universo', 'uso', 'serie', 'antes_depois'} <= set(t)
                              for t in j['times'])
    # o último mês é a régua da Performance/Gerencial (mesma função, mesma data)
    with server.app.test_request_context('/'):
        por_dono = server._cobertura_mes_fechado()['por_dono']
    ult = j['empresa'][-1]
    assert ult['anomes'] == j['meses'][-1]
    assert ult['base'] == sum(g['base'] for g in por_dono.values())
    assert ult['positivados'] == sum(g['positivados'] for g in por_dono.values())
    # empresa = soma dos times (inclusive "Sem time")
    assert ult['base'] == sum(t['serie'][-1]['base'] for t in j['times'])
    # grupos usa/não usa fecham com a empresa
    assert (j['grupos']['usa'][-1]['base'] + j['grupos']['nao_usa'][-1]['base']) == ult['base']


def test_filtros_de_uso_e_universo(client, usuario_admin, pg):
    from tests.conftest import login_as
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    j = client.get('/api/evolucao-carteira?uso=nao').get_json()
    assert all(not t['uso']['usa'] for t in j['times'])
    j = client.get('/api/evolucao-carteira?universo=campo').get_json()
    assert all(t['universo'] == 'campo' for t in j['times'])


def test_drill_do_time_traz_vendedores(client, usuario_admin, pg):
    from tests.conftest import login_as
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    j = client.get('/api/evolucao-carteira?supervisor=12').get_json()
    assert j['ok'] and j['rcas'] and all(len(r['serie']) == 12 for r in j['rcas'])


def test_supervisor_so_ve_os_proprios_times(client, pg):
    from tests.conftest import _criar_usuario, _remover_usuario, login_as
    _criar_usuario('sup-evol@teste.local', 'senha123', role='supervisor', codsupervisor=12, codsupervisores=[12])
    try:
        login_as(client, 'sup-evol@teste.local', 'senha123')
        j = client.get('/api/evolucao-carteira').get_json()
        assert {t['codsupervisor'] for t in j['times']} == {12}
        assert client.get('/api/evolucao-carteira?supervisor=14').get_json()['rcas'] == []
    finally:
        _remover_usuario('sup-evol@teste.local')


# ───────────────────────── clique no vendedor (Gabriel, 06/10/2026) ─────────────────────────
def test_clicar_no_vendedor_troca_graficos_e_cards():
    """No drill do time, clicar num vendedor passa os cards e os dois gráficos para ele (a série
    dele já vem no payload — nada de nova chamada); o caminho mostra Empresa ▸ Time ▸ Vendedor."""
    h = Path('evolucao.html').read_text(encoding='utf-8')
    assert 'function irVendedor(' in h and 'F.vendedor' in h
    corpo = h[h.index('function escopo('):h.index('function renderKpis(')]
    assert '_d.rcas.find' in corpo and 'F.vendedor' in corpo
    assert "irVendedor(" in h[h.index('function linhaTabela('):h.index('function renderTabela(')]
