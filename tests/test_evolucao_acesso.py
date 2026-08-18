"""Gate do acesso à aba Evolução do estoque (08/2026).

A aba nasce **restrita ao ADM** por decisão de produto: ela só fica útil depois de ~4 semanas
de foto, e mostrá-la vazia para o comprador seria entregar tela em branco como se fosse
produto.

⚠️ O que este gate protege é o lugar onde a restrição mora. No front a aba é escondida com
`hidden` + um teste de `role` no boot — isso é COSMÉTICO. Quem barra de verdade é o
`/api/evolucao`, e é ele que estes testes exercitam: um usuário de Compras não-admin, com a
área liberada (ou seja, que passa pela guarda do blueprint), tem de tomar 403 mesmo assim.

Sem este teste, alguém "simplifica" o endpoint um dia, a aba continua escondida no menu, e o
dado histórico fica exposto por URL direta sem ninguém perceber.
"""
import pytest

from tests.conftest import _criar_usuario, _remover_usuario, login_as


def _com_area_compras(email, role):
    """Cria o usuário e libera a área `compras` — senão o 403 viria da guarda do blueprint e o
    teste passaria pelo motivo errado."""
    uid = _criar_usuario(email, "senha123", role=role, must_change=False)
    import server
    conn = server.get_db()
    cur = conn.cursor()
    cur.execute("""UPDATE multpel_users SET areas = '["comercial","compras"]'::jsonb, ativo = true
                   WHERE email = %s""", (email,))
    conn.commit()
    cur.close()
    conn.close()
    return uid


@pytest.fixture
def viewer_compras(client):
    email = "viewer-evo@teste.local"
    _com_area_compras(email, "viewer")
    login_as(client, email, "senha123")
    yield email
    _remover_usuario(email)


@pytest.fixture
def admin_compras(client):
    email = "adm-evo@teste.local"
    _com_area_compras(email, "admin")
    login_as(client, email, "senha123")
    yield email
    _remover_usuario(email)


def test_nao_admin_com_area_compras_ainda_assim_toma_403(client, viewer_compras):
    """O caso que importa: ele PASSA pela guarda do módulo e mesmo assim não vê a aba."""
    r = client.get("/estoque/api/evolucao")
    assert r.status_code == 403
    assert "administrador" in (r.get_json() or {}).get("error", "")


def test_admin_entra(client, admin_compras):
    r = client.get("/estoque/api/evolucao")
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] is True
    # sem foto nenhuma ainda, a resposta é vazia mas BEM FORMADA — a tela precisa distinguir
    # "medição não começou" de "falhou", e por isso `resumo.maturidade` sempre vem
    assert "dias" in j and "resumo" in j


def test_sem_login_nao_entra(client):
    client.get("/logout")
    r = client.get("/estoque/api/evolucao")
    assert r.status_code in (401, 302, 403)


def test_a_aba_nasce_escondida_no_html():
    """Cinto e suspensório: o `hidden` no HTML é o que impede a aba de piscar para todo mundo
    entre o boot e a resposta do /api/me."""
    from pathlib import Path
    html = Path("estoque/index.html").read_text(encoding="utf-8")
    linha = next(l for l in html.splitlines() if 'data-view="evolucao"' in l)
    assert "hidden" in linha, "a aba tem de nascer escondida e só ser revelada para admin"


def test_valor_de_estoque_nao_tem_direcao_e_por_isso_nao_ganha_cor():
    """Decisão de produto fácil de desfazer sem perceber: só métricas com direção INEQUÍVOCA são
    coloridas. Estoque caindo pode ser boa gestão OU desabastecimento — pintar de verde faria a
    aba, um dia, comemorar uma ruptura. Quem dá o sinal é a linha de ruptura ao lado.

    O gate é aqui porque o mapa vive no servidor (`_EVO_DIRECAO`) e viaja até a tela, então uma
    linha só decide a cor de todos os KPIs."""
    from estoque.routes import _EVO_DIRECAO
    assert _EVO_DIRECAO["valor_estoque"] is None, \
        "valor de estoque não pode ter direção: queda não é, por si só, boa notícia"
    assert _EVO_DIRECAO["valor_parado"] == "menor_melhor"
    assert _EVO_DIRECAO["n_ruptura"] == "menor_melhor"
    assert _EVO_DIRECAO["pct_ideal"] == "maior_melhor"


def test_a_ruptura_viaja_junto_como_contrapeso():
    """Sem esta série a aba conta meia história. Ela é o que separa "reduzi capital" de
    "desabasteci" — e foi a melhoria proposta em cima do pedido original."""
    from estoque import historico
    from datetime import date
    s = historico.agregar([(date(2026, 8, 14), 0, 0.0, 2.0, 0, None, None)])
    assert s[0]["n_ruptura"] == 1
