"""Gate do rename "Compras" → "Gestão de Estoque" (08/2026, decisão do João).

O que mudou é **rótulo**, e só. A chave interna `compras` continua igual em três lugares que
não se veem na tela e que quebrariam feio se alguém "terminasse" o rename:

  · `MODULOS` — env var das stacks (`MODULOS=comercial,compras`);
  · `multpel_users.areas` — JSONB, POR PESSOA, já gravado no banco de produção;
  · a guarda do blueprint e a URL `/estoque`.

Renomear a chave exigiria migração de dados em todas as instâncias e tiraria o acesso de todo
mundo até ela rodar. Este teste existe para que a próxima pessoa que buscar "Compras" no repo e
trocar tudo bata numa falha vermelha em vez de descobrir em produção.

⚠️ Fora do escopo de propósito: o `MANUAL_COMPRAS.md` (base do agente de IA de dúvidas) segue
falando "Compras" — decisão explícita de adiar. Enquanto isso, o agente responde com o nome
antigo enquanto a tela mostra o novo.
"""
from pathlib import Path

NOVO = "Gestão de Estoque"


def _le(p):
    return Path(p).read_text(encoding="utf-8")


def test_o_portal_mostra_o_nome_novo_e_guarda_a_chave_velha():
    portal = _le("portal.html")
    assert f'data-area="compras">{NOVO}</button>' in portal, "o botão do portal tem de mostrar o nome novo"
    assert "'compras'" in portal or 'data-area="compras"' in portal, "a CHAVE não pode mudar"


def test_o_seletor_de_area_mostra_o_nome_novo():
    header = _le("static/joga-header.js")
    assert f"rotulo: '{NOVO}'" in header
    assert "compras: {" in header, "a chave do dicionário de áreas continua `compras`"


def test_o_cabecalho_do_modulo_mostra_o_nome_novo():
    html = _le("estoque/index.html")
    assert f"<title>JOGA · {NOVO}</title>" in html
    assert f"<h1>JOGA <span>· {NOVO}</span></h1>" in html


def test_o_grupo_de_menu_Estoque_NAO_muda():
    """"Estoque" também é um dos 5 grupos de abas (Visão · Comprar · Pedidos · Estoque · Análise).
    Renomear esse para "Gestão de Estoque" ao lado de "Comprar" e "Análise" não faz sentido."""
    html = _le("estoque/index.html")
    assert '<div class="navgroup" data-group="estoque">Estoque</div>' in html


def test_a_env_MODULOS_continua_falando_compras():
    """Trocar isto exigiria mexer no compose de TODA instância — e o app perderia o módulo até lá."""
    server = _le("server.py")
    assert "os.getenv('MODULOS', 'comercial,compras')" in server


def test_o_admin_ainda_grava_a_area_como_compras():
    admin = _le("admin.html")
    assert "areas.includes('compras')" in admin, "a área gravada no banco continua `compras`"
    assert "f_area_compras" in admin, "o id do checkbox é interno — não é rótulo"
