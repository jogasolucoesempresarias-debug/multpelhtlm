"""Gate da página /uso — acompanhamento de adoção da ferramenta (pedido do diretor 07/2026:
"conseguimos saber quem está usando a nossa ferramenta? última vez que acessou, ranking de uso").

Decisões que estes testes travam:
  · a página é OCULTA (fora de todo menu) mas a proteção real é `@admin_required` — endereço
    secreto não é segurança, e um dia alguém compartilha o link;
  · mede ADOÇÃO, não presença: acessos e downloads, nunca tempo de tela. O app não tem evento de
    saída (ninguém clica em sair, fecha a aba), então "tempo de uso" confundiria aba esquecida
    aberta com trabalho — e ainda viraria placar de produtividade, que se corrompe sozinho;
  · quem NUNCA acessou tem de aparecer: é a linha mais acionável (licença paga e não usada);
  · contas da suíte de testes ficam fora — o log de desenvolvimento tem milhares de logins de
    `*-test@`, e sem o filtro o pódio é do pytest.
"""
import io
from datetime import datetime, timedelta

import pytest

from tests.conftest import _criar_usuario, _remover_usuario, login_as


# As fixtures padrão usam `*-test@multpel.com.br`, que é justamente o padrão FILTRADO pela tela.
# Para testar a listagem é preciso um e-mail que passe pelo filtro.
@pytest.fixture
def usuario_real():
    email = 'maria.compras@empresa.com.br'
    uid = _criar_usuario(email, 'senha123', role='viewer', must_change=False)
    yield {'id': uid, 'email': email, 'senha': 'senha123'}
    _remover_usuario(email)


def _log(uid, rota, quando=None):
    """Injeta um evento no log de auditoria."""
    import server
    conn = server.get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO multpel_log (usuario_id, rota, acessado_em) VALUES (%s, %s, %s)",
        (uid, rota, quando or datetime.now()))
    conn.commit()
    cur.close()
    conn.close()


def _limpa_log(uid):
    import server
    conn = server.get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM multpel_log WHERE usuario_id = %s", (uid,))
    conn.commit()
    cur.close()
    conn.close()


# ─────────────────────── a página é oculta, mas protegida ───────────────────────
def test_pagina_exige_admin(client, usuario_vendedor):
    """Ocultar do menu é higiene; quem barra é o papel. Um vendedor que descubra a URL não entra."""
    login_as(client, usuario_vendedor['email'], usuario_vendedor['senha'])
    assert client.get('/uso').status_code == 403
    assert client.get('/api/admin/uso').status_code == 403


def test_pagina_abre_para_admin(client, usuario_admin):
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    assert client.get('/uso').status_code == 200
    assert client.get('/api/admin/uso').get_json()['ok'] is True


def test_anonimo_nao_entra(client):
    assert client.get('/uso').status_code == 401


def test_pagina_nao_aparece_em_menu_nenhum():
    """É o que sustenta o "oculto". Se alguém acrescentar /uso a uma das listas, o gate cai —
    não existe enumeração automática de rotas no app, então estas listas SÃO o menu."""
    from pathlib import Path
    header = Path('static/joga-header.js').read_text(encoding='utf-8')
    portal = Path('portal.html').read_text(encoding='utf-8')
    assert "'/uso'" not in header and '"/uso"' not in header
    assert "'/uso'" not in portal and '"/uso"' not in portal


def test_rota_e_neutra_para_a_guarda_do_comercial():
    """Sem estar em _ROTAS_NEUTRAS, a guarda trata /uso como página do Comercial: 404 em
    instância só-Compras e 403 para admin sem a área comercial — quebrando para quem deve abrir."""
    import server
    assert '/uso' in server._ROTAS_NEUTRAS
    assert server._rota_neutra('/api/admin/uso')      # entra pelo prefixo /api/admin/


# ─────────────────────── o que a tela mede ───────────────────────
def test_lista_traz_ultimo_acesso_e_contagens(client, usuario_admin, usuario_real):
    _limpa_log(usuario_real['id'])
    hoje = datetime.now()
    _log(usuario_real['id'], 'login:sucesso', hoje - timedelta(days=1))
    _log(usuario_real['id'], 'login:sucesso', hoje - timedelta(days=1, hours=2))  # mesmo DIA
    _log(usuario_real['id'], 'login:sucesso', hoje - timedelta(days=5))
    _log(usuario_real['id'], 'export:/estoque/api/export/produtos.xlsx', hoje - timedelta(days=1))
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    u = next(x for x in client.get('/api/admin/uso?periodo=90d').get_json()['usuarios']
             if x['email'] == usuario_real['email'])
    assert u['acessos'] == 3
    # dias ativos ignora o 2º login do mesmo dia: mede hábito, não sessão que caiu e voltou
    assert u['dias_ativos'] == 2
    assert u['downloads'] == 1
    assert u['ultimo_acesso'] is not None


def test_login_falho_nao_conta_como_acesso(client, usuario_admin, usuario_real):
    """Tentativa errada também está logada. Contá-la premiaria quem esquece a senha."""
    _limpa_log(usuario_real['id'])
    _log(usuario_real['id'], 'login:senha_incorreta')
    _log(usuario_real['id'], 'login:bloqueado:15min')
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    u = next(x for x in client.get('/api/admin/uso').get_json()['usuarios']
             if x['email'] == usuario_real['email'])
    assert u['acessos'] == 0 and u['ultimo_acesso'] is None


def test_quem_nunca_acessou_aparece(client, usuario_admin, usuario_real):
    """LEFT JOIN de propósito: é a informação mais acionável da tela — conta criada e não usada.
    Um INNER JOIN esconderia exatamente o caso que se quer enxergar."""
    _limpa_log(usuario_real['id'])
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    j = client.get('/api/admin/uso').get_json()
    u = next(x for x in j['usuarios'] if x['email'] == usuario_real['email'])
    assert u['ultimo_acesso'] is None and u['acessos'] == 0
    assert j['resumo']['nunca_acessaram'] >= 1


def test_periodo_recorta_acessos_mas_nao_o_ultimo_acesso(client, usuario_admin, usuario_real):
    """"Último acesso" é de qualquer época — a pergunta é "sumiu quando?", e um recorte de 30 dias
    devolveria "nunca" para quem entrou há 60, que é diferente de nunca ter entrado."""
    _limpa_log(usuario_real['id'])
    _log(usuario_real['id'], 'login:sucesso', datetime.now() - timedelta(days=60))
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    u = next(x for x in client.get('/api/admin/uso?periodo=30d').get_json()['usuarios']
             if x['email'] == usuario_real['email'])
    assert u['acessos'] == 0
    assert u['ultimo_acesso'] is not None


def test_contas_de_teste_ficam_fora(client, usuario_admin):
    """O log de desenvolvimento tem milhares de logins de `*-test@`; sem o filtro, o ranking é
    dominado pelo pytest — inclusive o próprio admin que está fazendo a chamada."""
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    emails = [u['email'] for u in client.get('/api/admin/uso').get_json()['usuarios']]
    assert usuario_admin['email'] not in emails
    assert not [e for e in emails if '-test@' in e or e.endswith('@teste.local')]


# ─────────────────────── coleta de downloads ───────────────────────
def _chama_hook(monkeypatch, path, headers, status=200, logado=True):
    """Exercita o after_request direto. Não dá para registrar rota de teste: o Flask trava
    `@app.route` depois do primeiro request, e a suíte inteira compartilha o mesmo app."""
    import server
    from flask import session
    gravado = []
    monkeypatch.setattr(server, 'log_request', lambda rota, **k: gravado.append(rota))
    with server.app.test_request_context(path):
        if logado:
            session['user_id'] = 1
        resp = server.Response(b'x', status=status, headers=headers)
        assert server._log_download(resp) is resp, 'o hook tem de devolver a resposta'
    return gravado


@pytest.mark.parametrize("path", [
    '/estoque/api/export/produtos.xlsx',
    '/estoque/api/export/ficha/produto/68852.pdf',
    '/api/carteira/export.csv',
])
def test_after_request_registra_qualquer_download(monkeypatch, path):
    """Um hook só cobre os 33 pontos de export do app — Comercial, Compras e os futuros."""
    assert _chama_hook(monkeypatch, path,
                       {'Content-Disposition': 'attachment; filename="a"'}) == [f'export:{path}']


def test_download_de_anonimo_nao_e_registrado(monkeypatch):
    """Sem sessão não há a quem atribuir — e a tela mede pessoas, não requisições."""
    assert _chama_hook(monkeypatch, '/x.xlsx',
                       {'Content-Disposition': 'attachment'}, logado=False) == []


def test_resposta_de_erro_nao_conta_como_download(monkeypatch):
    """Export que falhou não é uso da ferramenta."""
    assert _chama_hook(monkeypatch, '/x.xlsx',
                       {'Content-Disposition': 'attachment'}, status=500) == []


def test_inline_nao_e_download(monkeypatch):
    """`inline` é conteúdo exibido no navegador, não relatório levado embora."""
    assert _chama_hook(monkeypatch, '/x.pdf', {'Content-Disposition': 'inline'}) == []


def test_after_request_nao_registra_navegacao_normal(client, usuario_admin, monkeypatch):
    """Logar toda navegação explodiria a tabela; o sinal de adoção vem de login + download."""
    import server
    gravado = []
    monkeypatch.setattr(server, 'log_request', lambda rota, **k: gravado.append(rota))
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    client.get('/api/me')
    client.get('/uso')
    assert gravado == []


def test_after_request_nunca_derruba_a_resposta(monkeypatch):
    """O download já estava pronto: um erro de log não pode transformá-lo em erro 500.
    O hook roda em TODA resposta do app, então ele engolir a exceção não é zelo, é requisito."""
    import server
    from flask import session

    def _explode(*a, **k):
        raise RuntimeError('banco fora')
    monkeypatch.setattr(server, 'log_request', _explode)
    with server.app.test_request_context('/x.xlsx'):
        session['user_id'] = 1
        resp = server.Response(b'ok', headers={'Content-Disposition': 'attachment'})
        assert server._log_download(resp) is resp
        assert resp.status_code == 200 and resp.data == b'ok'


# ─────────────────────── retenção ───────────────────────
def test_expurgo_apaga_velho_e_preserva_o_recente(usuario_real):
    """A tabela nasceu sem limpeza e crescia para sempre. 12 meses cobrem comparação ano-a-ano."""
    import server
    _limpa_log(usuario_real['id'])
    _log(usuario_real['id'], 'login:sucesso', datetime.now() - timedelta(days=400))   # 13 meses
    _log(usuario_real['id'], 'login:sucesso', datetime.now() - timedelta(days=330))   # 11 meses
    server._expurgar_log(meses=12)
    conn = server.get_db()
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM multpel_log WHERE usuario_id = %s", (usuario_real['id'],))
    n = cur.fetchone()[0]
    cur.close()
    conn.close()
    assert n == 1


def test_expurgo_nao_derruba_o_app_se_o_banco_falhar(monkeypatch):
    """Roda no scheduler: exceção não tratada mataria o job silenciosamente."""
    import server

    def _boom():
        raise RuntimeError('sem banco')
    monkeypatch.setattr(server, 'get_db', _boom)
    assert server._expurgar_log() == 0
