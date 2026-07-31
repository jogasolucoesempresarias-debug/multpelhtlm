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


# ─────────────────── namespace do cache por FONTE (07/2026) ───────────────────
# Caso real: rodar o app em modo demo (DATA_SOURCE=postgres, base sintética) gravou o mapa de
# vendedores da DEMO na MESMA chave que a produção usa. Na volta ao Power BI o cache respondeu
# com os 114 vendedores sintéticos, e os códigos reais (que não existem na demo) apareceram na
# tela como "RCA 950". É a mesma família que a rede de segurança do execute_dax protege — dado de
# uma fonte vazando na outra — só que pela porta do cache, que aquela guarda não cobre.
def test_chave_de_cache_separa_powerbi_de_postgres(monkeypatch):
    import server
    monkeypatch.setitem(server.CONFIG, "data_source", "powerbi")
    k_pbi = server._cache_ns("vendedores_map:v2")
    monkeypatch.setitem(server.CONFIG, "data_source", "postgres")
    monkeypatch.setenv("ANALYTICS_DB_NAME", "joga_demo")
    k_pg = server._cache_ns("vendedores_map:v2")
    assert k_pbi != k_pg, "demo e produção não podem compartilhar chave de cache"
    assert "powerbi" in k_pbi and "postgres" in k_pg


def test_dois_bancos_analiticos_nao_colidem(monkeypatch):
    """Demo e o Winthor de um cliente 'só banco' são dois Postgres — colidiriam do mesmo jeito."""
    import server
    monkeypatch.setitem(server.CONFIG, "data_source", "postgres")
    monkeypatch.setenv("ANALYTICS_DB_NAME", "joga_demo")
    a = server._cache_ns("x")
    monkeypatch.setenv("ANALYTICS_DB_NAME", "cliente_xyz")
    assert a != server._cache_ns("x")


def test_todo_acesso_ao_redis_passa_pelo_namespace():
    """O namespace vive dentro do _cache_get/_cache_set. Se alguém voltar a chamar _R.get/_R.setex
    direto, a separação por fonte deixa de valer para aquela chave — sem erro nenhum."""
    import inspect
    import server
    src = inspect.getsource(server)
    diretos = [l.strip() for l in src.splitlines()
               if ("_R.get(" in l or "_R.setex(" in l) and "_cache_ns(" not in l]
    assert not diretos, f"acesso ao Redis fora do namespace: {diretos}"
