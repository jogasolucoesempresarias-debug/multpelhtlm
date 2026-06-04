"""Testes de autenticação: login OK, senha errada, must_change_password, logout."""
from tests.conftest import login_as


def test_login_sucesso(client, usuario_admin):
    r = login_as(client, usuario_admin['email'], usuario_admin['senha'])
    assert r.status_code == 200
    data = r.get_json()
    assert data['ok'] is True
    assert data['redirect'] == '/'


def test_login_senha_errada(client, usuario_admin):
    r = login_as(client, usuario_admin['email'], 'senha-incorreta')
    assert r.status_code == 401
    data = r.get_json()
    assert data['ok'] is False
    assert 'inválidos' in data['error'].lower() or 'invalidos' in data['error'].lower()


def test_must_change_password_redireciona(client, usuario_must_change):
    r = login_as(client, usuario_must_change['email'], usuario_must_change['senha'])
    assert r.status_code == 200
    data = r.get_json()
    assert data['ok'] is True
    assert data['redirect'] == '/trocar-senha'

    # Tentar acessar /api/me bloqueado enquanto must_change_password=True
    r2 = client.get('/api/me')
    assert r2.status_code == 403


def test_logout_limpa_sessao(client, usuario_admin):
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    r = client.get('/api/me')
    assert r.status_code == 200

    client.get('/logout', follow_redirects=False)
    r2 = client.get('/api/me')
    assert r2.status_code == 401
