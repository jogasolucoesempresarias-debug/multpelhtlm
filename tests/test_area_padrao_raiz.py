# -*- coding: utf-8 -*-
"""Gate do "fixar área padrão" na ROTA RAIZ (09/2026).

**O que estava quebrado.** `destino_pos_login()` é a fonte única do destino e respeita o
`area_padrao` que o Portal grava — mas a rota `/` não a consultava para quem tem a área Comercial.
Como o cookie de sessão dura 12h e é `permanent`, fechar o navegador não derruba a sessão: ao
reabrir, a aba volta em `/`, o login não acontece, e a pessoa caía no Comercial mesmo tendo fixado
"Gestão de Estoque". O item de menu "Escolher e fixar a área padrão" prometia uma coisa que este
caminho desfazia — e o sintoma não é erro nenhum, é a tela errada abrindo todo dia.

⚠️ **O gate protege os DOIS lados**, e o segundo é o que impede a correção de virar regressão:
a raiz redireciona para `/estoque/` quando a pessoa fixou Compras, e **NÃO** redireciona para o
`/portal` quando ela não fixou nada. O default da coluna é `'portal'` (`init_db.py`), então honrar
o `area_padrao` inteiro faria a maioria — que nunca tocou no Portal — receber a tela de escolha no
lugar do dashboard.
"""
import pytest

from tests.conftest import _criar_usuario, _remover_usuario, login_as


def _com_areas(email, area_padrao, areas='["comercial","compras"]'):
    _criar_usuario(email, "senha123", role="admin", must_change=False)
    import server
    conn = server.get_db()
    cur = conn.cursor()
    cur.execute("UPDATE multpel_users SET areas = %s::jsonb, area_padrao = %s, ativo = true "
                " WHERE email = %s", (areas, area_padrao, email))
    conn.commit()
    cur.close()
    conn.close()


@pytest.fixture
def usuario(client):
    """Devolve um criador de usuário logado com o `area_padrao` que o teste pedir."""
    criados = []

    def _mk(area_padrao, areas='["comercial","compras"]'):
        email = f"raiz-{area_padrao}-{len(criados)}@teste.local"
        _com_areas(email, area_padrao, areas)
        criados.append(email)
        login_as(client, email, "senha123")
        return email

    yield _mk
    for e in criados:
        _remover_usuario(e)


def test_quem_fixou_COMPRAS_e_redirecionado_pela_raiz(client, usuario):
    """O caso que originou o ajuste: sessão viva + aba restaurada em `/` = Comercial, sempre."""
    usuario("compras")
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (301, 302), r.status_code
    assert r.headers["Location"].endswith("/estoque/"), r.headers["Location"]


def test_quem_NAO_fixou_nada_continua_caindo_no_COMERCIAL(client, usuario):
    """⚠️ O lado que impede a regressão. `'portal'` é o DEFAULT da coluna, então este é o caso da
    maioria: honrar o `area_padrao` inteiro mandaria essa gente para a tela de escolha em vez do
    dashboard — consertar quem escolheu incomodando quem não pediu nada."""
    usuario("portal")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200, f"não pode redirecionar: {r.headers.get('Location')}"


def test_quem_fixou_COMERCIAL_continua_no_comercial(client, usuario):
    """Redirecionar `/` para `/` seria um laço; o gate trava que isso não acontece."""
    usuario("comercial")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200, f"não pode redirecionar: {r.headers.get('Location')}"


def test_quem_SO_tem_compras_segue_indo_pro_estoque(client, usuario):
    """Comportamento que já existia (o `if not tem_area('comercial')` acima) — o ajuste não pode
    tê-lo quebrado. Aqui o `area_padrao` nem importa: com uma área só não há o que escolher."""
    usuario("portal", areas='["compras"]')
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (301, 302)
    assert r.headers["Location"].endswith("/estoque/")


def test_a_raiz_reusa_destino_pos_login_em_vez_de_reimplementar_a_regua():
    """⚠️ São QUATRO caminhos decidindo o mesmo destino (`/`, `GET /login`, `POST /api/login`,
    `/portal`). Uma segunda cópia da régua na raiz sairia de sincronia no primeiro ajuste — é o
    defeito que este módulo já pagou na aba Fornecedores e na Ruptura."""
    import inspect

    import server
    fonte = inspect.getsource(server.index_page)
    assert "destino_pos_login()" in fonte
    # só o CÓDIGO: os comentários citam `area_padrao` justamente para explicar por que ele NÃO é
    # lido aqui (mesma leitura do gate irmão em `test_ia_compras`, que caiu nesta armadilha antes)
    codigo = [l for l in fonte.splitlines() if not l.strip().startswith("#")]
    assert not any("area_padrao" in l for l in codigo), \
        "a raiz não pode ler `area_padrao` direto — quem lê é o `destino_pos_login`"
