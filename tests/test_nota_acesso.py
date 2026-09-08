# -*- coding: utf-8 -*-
"""Gate do acesso à aba Nota do comprador.

⚠️ **A política aqui é o OPOSTO da Evolução, e de propósito.** A Evolução é ADM-only enquanto a
série amadurece; a Nota é visível a todo mundo que tem a área `compras`, como a aba Desempenho
comercial já é — decisão do usuário. Este gate existe porque a tentação de copiar o
`if role != 'admin'` do endpoint vizinho é grande, e o efeito seria a aba sumir para os
compradores sem ninguém notar (o front não mostra erro, o painel só não abre).

O que continua barrado: quem não fez login, e quem não tem a área `compras` — que é a guarda do
blueprint (`server._guard_estoque`), não deste endpoint.
"""
import pytest

from tests.conftest import _criar_usuario, _remover_usuario, login_as


def _com_area_compras(email, role, areas='["comercial","compras"]'):
    uid = _criar_usuario(email, "senha123", role=role, must_change=False)
    import server
    conn = server.get_db()
    cur = conn.cursor()
    cur.execute("UPDATE multpel_users SET areas = %s::jsonb, ativo = true WHERE email = %s",
                (areas, email))
    conn.commit()
    cur.close()
    conn.close()
    return uid


@pytest.fixture
def viewer_compras(client):
    email = "viewer-nota@teste.local"
    _com_area_compras(email, "viewer")
    login_as(client, email, "senha123")
    yield email
    _remover_usuario(email)


@pytest.fixture
def so_comercial(client):
    email = "viewer-nota-sc@teste.local"
    _com_area_compras(email, "viewer", areas='["comercial"]')
    login_as(client, email, "senha123")
    yield email
    _remover_usuario(email)


def test_sem_login_nao_entra(client):
    r = client.get("/estoque/api/nota")
    assert r.status_code in (401, 302, 403)


def test_sem_a_area_compras_toma_403(client, so_comercial):
    """Quem barra é a guarda do blueprint, não o endpoint — mas o efeito tem de ser este."""
    r = client.get("/estoque/api/nota")
    assert r.status_code == 403


def test_NAO_ADMIN_com_area_compras_ENTRA(client, viewer_compras, mock_dax):
    """⚠️ O teste que protege a decisão. Se alguém copiar o gate ADM-only da Evolução para cá,
    é aqui que aparece — e o sintoma em produção seria a aba simplesmente não abrir para os
    compradores, sem mensagem de erro."""
    r = client.get("/estoque/api/nota")
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    j = r.get_json()
    assert j["ok"] is True
    assert "compradores" in j and "regua" in j


def test_a_serie_exige_o_comprador(client, viewer_compras, mock_dax):
    r = client.get("/estoque/api/nota/serie")
    assert r.status_code == 400
    assert "comprador_cod" in (r.get_json() or {}).get("error", "")


def test_a_serie_abre_para_nao_admin(client, viewer_compras, mock_dax):
    r = client.get("/estoque/api/nota/serie?comprador_cod=1")
    assert r.status_code == 200


def test_a_aba_NAO_nasce_escondida_no_html():
    """Espelha o teste irmão da Evolução, ao contrário: lá o gate afirma que a linha TEM
    `hidden`; aqui, que NÃO tem. As duas abas ficam no mesmo bloco do HTML, e um `hidden` copiado
    junto é o erro mais fácil de cometer."""
    import pathlib
    html = (pathlib.Path(__file__).resolve().parent.parent / "estoque" / "index.html").read_text(
        encoding="utf-8")
    linha = next(l for l in html.splitlines() if 'data-view="nota"' in l)
    assert "hidden" not in linha, linha


def test_a_aba_esta_no_NAV_do_grupo_visao():
    """⚠️ Sem entrar no `NAV`, o `GROUP_OF` cai no default e a aba some ao trocar de grupo — o
    tipo de defeito que não dá erro nenhum, só uma aba que desaparece."""
    import pathlib
    js = (pathlib.Path(__file__).resolve().parent.parent / "static" / "estoque"
          / "estoque.js").read_text(encoding="utf-8")
    linha = next(l for l in js.splitlines() if l.startswith("const NAV="))
    assert "'nota'" in linha.split("comprar:")[0], linha
