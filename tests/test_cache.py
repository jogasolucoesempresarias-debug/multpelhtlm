"""Testes do helper de cache Redis."""
import json
import time

import server


def test_cache_set_get_roundtrip(clean_redis):
    server._cache_set('teste:roundtrip', {'a': 1, 'b': [1, 2, 3]}, 'dax_agregado')
    out = server._cache_get('teste:roundtrip')
    assert out == {'a': 1, 'b': [1, 2, 3]}


def test_cache_get_miss_retorna_none(clean_redis):
    assert server._cache_get('chave-que-nao-existe') is None


def test_cache_key_inclui_rbac(client, usuario_admin):
    """A chave de cache deve diferir entre usuários de RBAC diferente — protege contra vazamento."""
    # Sem sessão
    with client.application.test_request_context('/'):
        from flask import session
        session.clear()
        key_anon = server.cache_key_for_user('dashboard:kpis')

    # Com sessão admin
    with client.application.test_request_context('/'):
        from flask import session
        session['role'] = 'admin'
        session['codusur'] = None
        session['codsupervisor'] = None
        key_admin = server.cache_key_for_user('dashboard:kpis')

    # Com sessão vendedor
    with client.application.test_request_context('/'):
        from flask import session
        session['role'] = 'vendedor'
        session['codusur'] = 573
        session['codsupervisor'] = None
        key_vendedor = server.cache_key_for_user('dashboard:kpis')

    assert key_anon != key_admin
    assert key_admin != key_vendedor
    assert 'usur=573' in key_vendedor
