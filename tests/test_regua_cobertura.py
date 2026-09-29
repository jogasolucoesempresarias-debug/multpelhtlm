"""Régua ÚNICA de cobertura da carteira (decisão do João, 28–29/09/2026 — PLANO_MELHORIAS §11).

Uma métrica em Gerencial, Vendedores e Performance, e em empresa/time/vendedor:
  base       = clientes CADASTRADOS no vendedor com última compra (de QUALQUER vendedor) nos
               últimos 365 dias até a data de referência;
  positivado = última compra nos últimos 60 dias até a data de referência;
  cobertura  = positivados ÷ base.
O Gerencial usa a data de HOJE; a Performance e Vendedores, o último dia do MÊS FECHADO. Mesma conta.
"""
from datetime import date, timedelta
from pathlib import Path

import pytest

import cobertura as cob


# ── bordas ──────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize('dias, na_base', [(0, True), (365, True), (366, False), (None, False), (-1, False)])
def test_base_e_quem_comprou_nos_ultimos_365_dias(dias, na_base):
    assert cob.na_base(dias) is na_base


@pytest.mark.parametrize('dias, pos', [(0, True), (60, True), (61, False), (None, False)])
def test_positivado_e_quem_comprou_na_janela_de_60_dias(dias, pos):
    assert cob.positivado(dias) is pos
    assert cob.positivado(dias, janela=cob.COBERTO_DIAS_PADRAO) is pos


def test_padroes_decididos_pelo_joao():
    assert cob.BASE_DIAS == 365 and cob.COBERTO_DIAS_PADRAO == 60 and cob.LIMIAR_PADRAO == 85.0


# ── Gerencial: a base de 12 meses entra DENTRO do motor (tela, CSV, PDF e e-mail usam ele) ──
def _cli(codcli, dias, codusur=1, sup=10, venda=100.0):
    return {'codcli': codcli, 'recencia_dias': dias, 'venda_12m': venda, 'codusur': codusur,
            'vendedor': f'V{codusur}', 'codsupervisor': sup, 'time': f'T{sup}',
            'status_personalizada': 'ok', 'receita_perdida_proj': 0.0}


def test_gerencial_so_conta_a_base_ativa_de_12_meses():
    """A carteira de 24 meses saía com quem comprou 12–24 meses atrás e derrubava todo mundo
    (empresa 35%). Agora esse cliente não é base: não entra nem no denominador."""
    clientes = [_cli(1, 10), _cli(2, 59), _cli(3, 200), _cli(4, 364), _cli(5, 400), _cli(6, 700)]
    n = cob.agregar_niveis(clientes)                 # janela padrão = 60
    e = n['empresa']
    assert e['total_clientes'] == 4                  # 400 e 700 d fora da base
    assert e['clientes_cobertos'] == 2               # 10 e 59 d
    assert e['cobertura_clientes'] == pytest.approx(0.5)
    assert n['coberto_dias'] == 60


def test_empresa_e_a_soma_dos_times_e_dos_vendedores():
    """Positivado por QUALQUER vendedor + base por cadastro → a conta fecha nos três níveis."""
    clientes = ([_cli(i, 10 * i, codusur=1 + i % 3, sup=10 + i % 2) for i in range(1, 40)]
                + [_cli(100, 500, codusur=1)])
    n = cob.agregar_niveis(clientes)
    for nivel in ('times', 'vendedores'):
        assert sum(g['total_clientes'] for g in n[nivel]) == n['empresa']['total_clientes']
        assert sum(g['clientes_cobertos'] for g in n[nivel]) == n['empresa']['clientes_cobertos']


# ── mês fechado (Performance/Vendedores): a MESMA conta, com a data do fim do mês ─────────────
def test_cobertura_por_dono_no_fim_do_mes():
    fim = date(2026, 8, 31)
    ult = {1: fim, 2: fim - timedelta(days=60), 3: fim - timedelta(days=61), 4: fim - timedelta(days=365),
           5: fim - timedelta(days=366), 6: fim - timedelta(days=10), 7: fim, 8: fim,
           9: fim - timedelta(days=100)}
    dono = {1: 29, 2: 29, 3: 29, 4: 29, 5: 29, 9: 29, 6: 30, 7: None}  # 8 sem cadastro
    r = cob.cobertura_por_dono(ult, dono, fim)
    assert r[29] == {'base': 5, 'positivados': 2, 'pct': 0.4}         # 366 d fora; 61 e 100 d são base não positivada
    assert r[30]['base'] == 1 and r[30]['pct'] is None                # base < MIN_AMOSTRA → sem %
    assert None not in r


def test_compra_depois_da_data_de_referencia_nao_conta():
    """Defesa: a fonte é "última compra ANTES do mês seguinte". Se vier data futura, o cliente não é
    base naquela data (não dá para saber a compra anterior) — nunca vira positivado por engano."""
    fim = date(2026, 8, 31)
    r = cob.cobertura_por_dono({1: fim + timedelta(days=3)}, {1: 29}, fim)
    assert r.get(29, {'base': 0})['base'] == 0


def test_gerencial_e_mes_fechado_concordam_na_mesma_data():
    """É a mesma régua: os mesmos clientes na mesma data dão o mesmo % nas duas funções."""
    hoje = date(2026, 9, 29)
    dias = [5, 30, 60, 61, 90, 180, 365, 366, 20, 45]
    clientes = [_cli(i, d, codusur=7) for i, d in enumerate(dias)]
    ult = {i: hoje - timedelta(days=d) for i, d in enumerate(dias)}
    g = cob.agregar_niveis(clientes)['vendedores'][0]
    m = cob.cobertura_por_dono(ult, {i: 7 for i in ult}, hoje)[7]
    assert (g['total_clientes'], g['clientes_cobertos']) == (m['base'], m['positivados'])
    assert g['cobertura_clientes'] == pytest.approx(m['pct'])


# ── servidor e tela ────────────────────────────────────────────────────────────────────────
def test_padroes_do_admin_sao_85_e_60(monkeypatch):
    import server
    monkeypatch.setattr(server, '_config_get', lambda k, default=None: default)
    assert server._cobertura_limiar_pct() == 85.0
    assert server._cobertura_coberto_dias() == 60


def test_tela_gerencial_abre_em_60_dias_e_colore_pelas_faixas_da_nota():
    """A tela abria FIXA em 30 d (ignorava o Admin). Cor = faixas da nota: < limiar (85) vermelho,
    até 99,9% amarelo, 100% verde — não mais "limiar + 15"."""
    html = Path('gerencial.html').read_text(encoding='utf-8')
    assert 'let _coberto = 60;' in html and 'let _limiar = 85;' in html
    corpo = html[html.index('function limiarClasse('):]
    corpo = corpo[:corpo.index('\n}\n')]
    assert "if (p < _limiar) return 'r';" in corpo and "if (p < 100) return 'y';" in corpo
    assert '_limiar + 15' not in corpo
    assert '<button data-d="60" class="on"' in html or '<button class="on" data-d="60"' in html


def test_gerencial_modo_postgres_usa_a_base_de_12_meses(client, usuario_admin, monkeypatch):
    import server
    from tests.conftest import login_as
    server._R.flushall()
    monkeypatch.setitem(server.CONFIG, 'data_source', 'postgres')
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    j = client.get('/api/gerencial/cobertura?coberto_dias=60').get_json()
    assert j['ok'] and j['coberto_dias'] == 60
    with server.app.test_request_context('/'):
        from flask import session
        session.update({'user_id': 1, 'role': 'admin'})
        base = [c for c in server._carteira_no_escopo() if cob.na_base(c.get('recencia_dias'))]
    assert j['empresa']['total_clientes'] == len(base)
    assert j['empresa']['clientes_cobertos'] == sum(1 for c in base if cob.positivado(c['recencia_dias']))


# ══════════════ etapa 2 — Performance e Vendedores no mês fechado ══════════════
@pytest.fixture
def pg(monkeypatch):
    import server
    server._R.flushall()
    monkeypatch.setitem(server.CONFIG, 'data_source', 'postgres')
    return server


def _sessao_admin(server):
    from flask import session
    ctx = server.app.test_request_context('/')
    ctx.push()
    session.update({'user_id': 1, 'role': 'admin'})
    return ctx


def test_performance_usa_a_regua_unica_no_fim_do_mes_fechado(pg):
    server = pg
    ctx = _sessao_admin(server)
    try:
        m = server._cobertura_mes_fechado()
        d = server._performance_dados()
    finally:
        ctx.pop()
    assert m['fim'].month == m['ref'] % 100 and (m['fim'] + timedelta(days=1)).day == 1   # último dia do mês
    linhas = [l for l in d['linhas'] if l['valores'].get('cobertura') is not None]
    assert linhas
    for l in linhas:
        c = m['por_dono'][l['codusur']]
        assert l['valores']['cobertura'] == pytest.approx(c['pct'])
        assert (l['detalhe']['base_ativa'], l['detalhe']['cobertos']) == (c['base'], c['positivados'])
        assert 'esperado' not in l['detalhe'] and 'por_classe' not in l['detalhe']   # sai o índice ABC


def test_mes_de_limpeza_cobertura_informativa_ate_a_competencia_configurada(pg, monkeypatch):
    """Decisão (29/09): outubro é o mês de limpeza; a cobertura entra na nota a partir da competência
    de NOVEMBRO/26 (configurável no Admin). Antes disso aparece, com nota, mas fora da nota final."""
    server = pg
    assert server._cobertura_na_nota_desde() == 202611
    cfg = {}
    monkeypatch.setattr(server, '_config_get', lambda k, default=None: cfg.get(k, default))
    ctx = _sessao_admin(server)
    try:
        cfg['cobertura_na_nota_desde'] = '209912'
        fora = server._performance_dados()
        cfg['cobertura_na_nota_desde'] = '200001'
        dentro = server._performance_dados()
    finally:
        ctx.pop()
    assert fora['cobertura_informativa'] is True and dentro['cobertura_informativa'] is False
    lf = next(l for l in fora['linhas'] if l['notas'].get('cobertura') is not None)
    ld = next(l for l in dentro['linhas'] if l['codusur'] == lf['codusur'])
    assert lf['informativos'] == ['cobertura'] and ld['informativos'] == []
    assert lf['notas']['cobertura'] == ld['notas']['cobertura']          # a nota do indicador aparece igual


def test_vendedores_mostra_a_mesma_cobertura(pg, client, usuario_admin):
    server = pg
    from tests.conftest import login_as
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    j = client.get('/api/vendedores?tipovend=').get_json()
    ctx = _sessao_admin(server)
    try:
        m = server._cobertura_mes_fechado()
    finally:
        ctx.pop()
    com = [v for v in j['vendedores'] if v.get('taxa_positivacao') is not None]
    assert com
    for v in com:
        c = m['por_dono'][v['codusur']]
        assert v['taxa_positivacao'] == pytest.approx(c['pct']) and v['base_ativa'] == c['base']
        assert v['base_coberta'] == c['positivados']


def test_telas_descrevem_a_regua_unica():
    perf = Path('performance.html').read_text(encoding='utf-8')
    assert "k === 'cobertura'" in perf and 'informativa' in perf
    assert 'positivados ÷ esperados' not in perf                   # sai a descrição do índice ABC
    # os títulos da COBERTURA (o "Fora da base" segue "atendidos por ele" — é outra coisa, correta)
    for arq, marca in (('vendedores.html', 'id="th_pos"'), ('vendedores.html', 'id="h_hist_pos"'),
                       ('vendedor.html', 'id="l_pos"')):
        html = Path(arq).read_text(encoding='utf-8')
        linha = next(l for l in html.splitlines() if marca in l)
        assert 'qualquer vendedor' in linha and '60 dias' in linha and 'ELE atendeu' not in linha, (arq, marca)


# ══════════════ etapa 3 — lista de limpeza e foto do cadastro (sem tela nova) ══════════════
def test_lista_de_limpeza_e_a_base_nao_positivada_pela_maior_venda():
    """O mês de limpeza (regra 1 do João): cada vendedor recebe os clientes da BASE que não
    compraram na janela — para vender ou transferir. Quem já comprou e quem nem é base ficam fora."""
    cl = [_cli(1, 10, venda=900), _cli(2, 61, venda=100), _cli(3, 200, venda=500), _cli(4, 400, venda=999),
          _cli(5, 365, venda=50), _cli(6, None, venda=10)]
    lista = cob.lista_limpeza(cl)
    assert [c['codcli'] for c in lista] == [3, 2, 5]                    # maior venda 12m primeiro
    assert [c['codcli'] for c in cob.lista_limpeza(cl, janela=30)] == [3, 2, 5]
    assert [c['codcli'] for c in cob.lista_limpeza(cl, janela=90)] == [3, 5]


def test_linhas_da_foto_do_cadastro():
    cl = [_cli(1, 10, codusur=29, sup=17), _cli(2, 400, codusur=30, sup=19), {'codcli': None, 'codusur': 1}]
    assert cob.linhas_foto(cl, 202609) == [(202609, 1, 29, 17), (202609, 2, 30, 19)]   # TODA a carteira, não só a base


def test_foto_do_cadastro_grava_idempotente_e_atualiza_o_dono(monkeypatch):
    """Regra 3 (relatório de retirados): o BI só guarda o dono ATUAL do cliente — sem a foto mensal
    não existe "quem saiu da carteira de quem". É só tabela + agendador, sem tela (pedido do Gabriel)."""
    import server
    anomes = 190001                                                      # mês de teste, apagado no fim
    clientes = [_cli(1, 10, codusur=29, sup=17), _cli(2, 50, codusur=30, sup=19)]
    monkeypatch.setattr(server, '_carregar_carteira_full', lambda: clientes)
    try:
        assert server._fotografar_carteira(anomes=anomes) == 2
        clientes[1] = _cli(2, 50, codusur=999, sup=None)                   # transferido para fictício
        assert server._fotografar_carteira(anomes=anomes) == 2
        conn = server.get_db()
        with conn, conn.cursor() as cur:
            cur.execute("SELECT codcli, codusur FROM carteira_foto WHERE anomes=%s ORDER BY codcli", (anomes,))
            assert cur.fetchall() == [(1, 29), (2, 999)]
        conn.close()
    finally:
        conn = server.get_db()
        with conn, conn.cursor() as cur:
            cur.execute("DELETE FROM carteira_foto WHERE anomes=%s", (anomes,))
        conn.close()


def test_foto_roda_no_agendador_sem_depender_do_interruptor_de_email():
    fonte = Path('server.py').read_text(encoding='utf-8')
    assert "add_job(_fotografar_carteira_job, 'cron'" in fonte
    ini = fonte.index('def _fotografar_carteira(')
    corpo = fonte[ini:fonte.index('\ndef _avancar_demo', ini)]            # a função e o job
    assert 'if not CRON_HABILITADO' not in corpo and 'if CRON_HABILITADO' not in corpo
    assert 'CREATE TABLE IF NOT EXISTS carteira_foto' in Path('init_db.py').read_text(encoding='utf-8')


def test_lista_de_limpeza_csv_respeita_o_escopo(pg, client):
    """Supervisor baixa a lista do PRÓPRIO time; pedir outro time não traz ninguém de fora."""
    server = pg
    from tests.conftest import _criar_usuario, _remover_usuario, login_as
    email = 'sup-limpeza@teste.local'
    _criar_usuario(email, 'senha123', role='supervisor', codsupervisor=12, codsupervisores=[12])
    try:
        login_as(client, email, 'senha123')
        r = client.get('/api/gerencial/limpeza/csv?codsupervisor=12&coberto_dias=60')   # a janela segue a tela
        assert r.status_code == 200 and 'attachment' in r.headers.get('Content-Disposition', '')
        linhas = [l for l in r.get_data(as_text=True).splitlines() if l.strip()]
        cab = next(i for i, l in enumerate(linhas) if l.startswith('Vendedor;'))
        corpo = [l.split(';') for l in linhas[cab + 1:]]
        assert corpo, 'o time 12 da demo tem clientes a limpar'
        i_dias = linhas[cab].split(';').index('Dias sem comprar')
        assert all(60 < int(c[i_dias]) <= 365 for c in corpo)
        outro = client.get('/api/gerencial/limpeza/csv?codsupervisor=14&coberto_dias=60').get_data(as_text=True).splitlines()
        cab2 = next(i for i, l in enumerate(outro) if l.startswith('Vendedor;'))
        assert [l for l in outro[cab2 + 1:] if l.strip()] == []            # fora do escopo: vazio
    finally:
        _remover_usuario(email)


def test_gerencial_tem_o_botao_da_lista_de_limpeza_so_no_drill():
    html = Path('gerencial.html').read_text(encoding='utf-8')
    assert '/api/gerencial/limpeza/csv' in html and 'id="btnLimpeza"' in html
