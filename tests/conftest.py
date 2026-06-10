"""Fixtures pra testes Multpel.
- `app` / `client`: app Flask em modo TESTING com fakeredis + DB real
- `usuario_admin` / `usuario_vendedor`: cria usuários temporários no DB e remove no teardown
- `mock_dax`: substitui execute_dax por função que lê de tests/fixtures/*.json
"""
import os
import sys
import json
import pathlib

import pytest
import fakeredis
from werkzeug.security import generate_password_hash

# Garante que o root do projeto está no path antes de importar server
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / 'fixtures'


@pytest.fixture(scope='session', autouse=True)
def _setup_fake_redis():
    """Substitui Redis global de server.py por fakeredis antes de qualquer teste."""
    import server
    server._R = fakeredis.FakeRedis(decode_responses=True)
    yield


@pytest.fixture
def app():
    import server
    server.app.config['TESTING'] = True
    server.app.config['SECRET_KEY'] = 'test-secret'
    return server.app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def clean_redis():
    """Limpa Redis fake antes/depois de cada teste que precise de estado limpo."""
    import server
    server._R.flushall()
    yield server._R
    server._R.flushall()


def _criar_usuario(email, senha, role='viewer', codusur=None, codsupervisor=None, must_change=False,
                   codsupervisores=None):
    """Cria/atualiza usuário no DB real. Retorna id."""
    import server
    conn = server.get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM multpel_users WHERE email = %s", (email,))
    cur.execute(
        """INSERT INTO multpel_users
           (nome, email, password_hash, role, ativo, codusur, codsupervisor, codsupervisores, must_change_password)
           VALUES (%s, %s, %s, %s, true, %s, %s, %s::jsonb, %s) RETURNING id""",
        ('Teste ' + email, email, generate_password_hash(senha), role, codusur, codsupervisor,
         json.dumps(codsupervisores or []), must_change)
    )
    uid = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return uid


def _remover_usuario(email):
    import server
    try:
        conn = server.get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM multpel_users WHERE email = %s", (email,))
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass


@pytest.fixture
def usuario_admin():
    email = 'admin-test@multpel.com.br'
    uid = _criar_usuario(email, 'senha123', role='admin', must_change=False)
    yield {'id': uid, 'email': email, 'senha': 'senha123', 'role': 'admin'}
    _remover_usuario(email)


@pytest.fixture
def usuario_must_change():
    email = 'mcp-test@multpel.com.br'
    uid = _criar_usuario(email, 'oldsenha', role='viewer', must_change=True)
    yield {'id': uid, 'email': email, 'senha': 'oldsenha'}
    _remover_usuario(email)


@pytest.fixture
def usuario_vendedor():
    email = 'vendedor-test@multpel.com.br'
    uid = _criar_usuario(email, 'senha123', role='vendedor', codusur=573, must_change=False)
    yield {'id': uid, 'email': email, 'senha': 'senha123', 'role': 'vendedor', 'codusur': 573}
    _remover_usuario(email)


@pytest.fixture
def usuario_supervisor():
    email = 'sup-test@multpel.com.br'
    uid = _criar_usuario(email, 'senha123', role='supervisor', codsupervisor=18, must_change=False)
    yield {'id': uid, 'email': email, 'senha': 'senha123', 'role': 'supervisor', 'codsupervisor': 18}
    _remover_usuario(email)


@pytest.fixture
def usuario_supervisor_multi():
    """Supervisor cuidando de 2 áreas (codsupervisores = [18, 19])."""
    email = 'sup-multi-test@multpel.com.br'
    uid = _criar_usuario(email, 'senha123', role='supervisor', codsupervisor=18,
                         codsupervisores=[18, 19], must_change=False)
    yield {'id': uid, 'email': email, 'senha': 'senha123', 'role': 'supervisor',
           'codsupervisores': [18, 19]}
    _remover_usuario(email)


@pytest.fixture
def usuario_viewer():
    email = 'viewer-test@multpel.com.br'
    uid = _criar_usuario(email, 'senha123', role='viewer', must_change=False)
    yield {'id': uid, 'email': email, 'senha': 'senha123', 'role': 'viewer'}
    _remover_usuario(email)


@pytest.fixture
def mock_dax_capture(monkeypatch):
    """Captura todas as queries DAX + retorna respostas mockadas por substring routing.

    Uso:
        cap = mock_dax_capture  # já é o objeto Captor (não chame como função)
        cap.set_routes([('UltimaCompra', payload1), ('PCUSUARI', payload2)])
        login_as(...)
        client.get('/api/...')
        assert any('CODUSUR = 573' in q for q in cap.queries)
    """
    import server

    class Captor:
        def __init__(self):
            self.queries = []
            self.routes = []

        def set_routes(self, routes):
            """routes = [(substring, payload_dict), ...]"""
            self.routes = routes

    captor = Captor()

    def _fake_execute(token, query, dataset_id=None):
        captor.queries.append(query)
        for kw, payload in captor.routes:
            if kw in query:
                return payload
        # Default: tabela vazia (não quebra fluxo, só não tem dados)
        return {'results': [{'tables': [{'rows': []}]}]}

    monkeypatch.setattr(server, 'execute_dax', _fake_execute)
    monkeypatch.setattr(server, 'get_token_cached', lambda: 'fake-token')
    return captor


@pytest.fixture
def mock_dax(monkeypatch):
    """Substitui execute_dax por função que lê fixtures/<nome>.json.
    Use: mock_dax('dax_kpis') -> qualquer chamada de execute_dax retorna esse JSON.
    """
    import server

    def _make(fixture_name):
        path = FIXTURES_DIR / f'{fixture_name}.json'
        with open(path, encoding='utf-8') as f:
            payload = json.load(f)

        def _fake_execute(token, query, dataset_id=None):
            return payload

        def _fake_token():
            return 'fake-token'

        monkeypatch.setattr(server, 'execute_dax', _fake_execute)
        monkeypatch.setattr(server, 'get_token_cached', _fake_token)
        return payload

    return _make


def login_as(client, email, senha):
    """Helper pra autenticar via test_client e retornar a resposta."""
    return client.post('/api/login', json={'email': email, 'senha': senha})
