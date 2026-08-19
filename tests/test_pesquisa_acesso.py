"""Gate de acesso e de forma da tela de campo da pesquisa de preço (08/2026).

Duas coisas em jogo:

1. **Acesso.** A captura é do time de compras. A guarda vem do `before_request` do blueprint —
   estes testes provam que ela cobre a página nova e os endpoints novos, e que não basta estar
   logado.

2. **Forma.** A tela de campo só se justifica se continuar LEVE. O painel é um SPA que carrega o
   snapshot inteiro (3-6 mil produtos) antes de pintar, e tem exatamente uma `@media` em 401
   linhas de CSS — nenhum breakpoint de largura. Se alguém "aproveitar" o `estoque.js` aqui, a
   página deixa de servir para o que foi feita e ninguém percebe até a primeira visita.

⚠️ Os testes de forma são gates de CÓDIGO porque leveza e ergonomia não se testam em pytest.
Mesmo padrão do `test_modal_pedido_layout` e do `test_export_filtros_aba`.
"""
import re
from pathlib import Path

import pytest

from tests.conftest import _criar_usuario, _remover_usuario, login_as

_BRUTO = Path("estoque/pesquisa.html").read_text(encoding="utf-8")
# ⚠️ Sem os comentários: eles citam de propósito os padrões proibidos ("nunca carregue o
# estoque.js", "um <link> que não carrega...") e fariam o gate acusar a própria explicação.
PAG = re.sub(r"<!--.*?-->", "", _BRUTO, flags=re.S)


def _usuario(email, areas):
    _criar_usuario(email, "senha123", role="viewer", must_change=False)
    import server
    conn = server.get_db()
    cur = conn.cursor()
    cur.execute("UPDATE multpel_users SET areas = %s::jsonb, ativo = true WHERE email = %s",
                (areas, email))
    conn.commit(); cur.close(); conn.close()


@pytest.fixture
def com_compras(client):
    email = "pesq-ok@teste.local"
    _usuario(email, '["comercial","compras"]')
    login_as(client, email, "senha123")
    yield email
    _remover_usuario(email)


@pytest.fixture
def sem_compras(client):
    email = "pesq-no@teste.local"
    _usuario(email, '["comercial"]')
    login_as(client, email, "senha123")
    yield email
    _remover_usuario(email)


# ───────────────── acesso ─────────────────

def test_sem_area_compras_nao_entra_na_pagina(client, sem_compras):
    assert client.get("/estoque/pesquisa").status_code == 403


def test_sem_area_compras_nao_grava(client, sem_compras):
    r = client.post("/estoque/api/pesquisa-preco", json={"codprod": 1, "preco": 10})
    assert r.status_code == 403


def test_com_area_compras_abre_a_pagina(client, com_compras):
    r = client.get("/estoque/pesquisa")
    assert r.status_code == 200
    assert b"Pesquisa de pre" in r.data


def test_payload_invalido_e_recusado(client, com_compras):
    """Dado de campo entra torto com facilidade; uma linha inválida contamina a comparação."""
    for corpo, pedaco in (({"preco": 10}, "codprod"),
                          ({"codprod": 999999, "preco": 0}, "zero"),
                          ({"codprod": 999999, "preco": -3}, "zero")):
        r = client.post("/estoque/api/pesquisa-preco", json=corpo)
        assert r.status_code == 400, corpo
        assert pedaco in (r.get_json() or {}).get("error", "")


def test_busca_curta_nao_varre_o_cadastro(client, com_compras):
    r = client.get("/estoque/api/busca?q=a")
    assert r.status_code == 200 and r.get_json()["produtos"] == []


# ───────────────── forma da tela ─────────────────

def test_a_pagina_NAO_carrega_o_spa():
    """O ponto inteiro da tela de campo. Puxar o `estoque.js` traria o snapshot inteiro para
    dentro de um celular no corredor de uma loja."""
    assert "estoque.js" not in PAG, "a tela de campo não pode carregar o JS do painel"
    assert "/api/snapshot" not in PAG, "a tela de campo não carrega o snapshot"


def test_a_pagina_e_autocontida():
    """Sem <link> nem <script src>: em loja sem sinal, um asset externo que não carrega deixa a
    tela quebrada justamente onde ela precisa funcionar. E o repo não tem cache-busting."""
    assert "<link" not in PAG and "script src" not in PAG


def test_a_pagina_e_mobile_first():
    assert 'name="viewport"' in PAG
    assert "@media (min-width:" in PAG, "precisa de breakpoint de largura — o painel não tem nenhum"
    assert "min-height:46px" in PAG, "alvo de toque: a tela é usada em pé, com uma mão"


def test_a_fila_offline_existe():
    """⚠️ Sem isto, 40 itens digitados numa loja sem sinal viram nada — e não há segunda visita.
    Alguém pode achar que é complexidade desnecessária e remover; este teste avisa."""
    assert "localStorage" in PAG
    assert "addEventListener('online'" in PAG, "tem de sincronizar ao voltar a conexão"
    assert "gravarFila([...lerFila(), item])" in PAG, "entra na fila ANTES de tentar a rede"


def test_erro_de_dado_nao_fica_preso_na_fila_para_sempre():
    """4xx é dado inválido: reenviar eternamente nunca esvaziaria a fila e a tela ficaria
    avisando 'aguardando conexão' com conexão perfeita."""
    assert "r.status >= 500" in PAG


def test_o_modal_de_pedido_continua_com_8_colunas():
    """A exibição do preço pesquisado entra como linha sob o nome do produto, NÃO como 9ª
    coluna: a tabela é `table-layout: fixed` e o diretor já reportou DUAS vezes coluna cortada
    (ver test_modal_pedido_layout)."""
    js = Path("static/estoque/estoque.js").read_text(encoding="utf-8")
    marca = 'class="pd-tab"><colgroup>'
    ini = js.index(marca) + len(marca)      # DEPOIS da abertura: "<colgroup" também casa "<col"
    bloco = js[ini:js.index("</colgroup>", ini)]
    assert bloco.count("<col") == 8, "o modal de pedido ganhou/perdeu coluna"


def test_o_campo_de_preco_aceita_virgula():
    """Achado no teste de campo (viewport de celular): `input[type=number]` DESCARTA a vírgula.
    Em pt-BR a pessoa digita "12,50", o campo fica vazio e ela não entende por quê — no corredor
    de uma loja, com uma mão. `type=text` + `inputmode=decimal` dá o teclado numérico sem o
    descarte; a conversão para ponto acontece no salvar."""
    import re as _re
    campo = _re.search(r'<input id="preco"[^>]*>', PAG).group(0)
    assert 'type="text"' in campo, 'type=number descarta a vírgula do teclado pt-BR'
    assert 'inputmode="decimal"' in campo, 'sem isto o celular abre o teclado alfabético'
    assert "replace(',', '.')" in PAG, 'a vírgula tem de virar ponto antes de enviar'


# ───────────────── roteiro por fornecedor ─────────────────

def test_busca_de_fornecedor(client, com_compras):
    """Pedido do diretor: "filtrar por fornecedor, aí traz os itens daquele fornecedor"."""
    r = client.get("/estoque/api/busca?tipo=fornec&q=re")
    assert r.status_code == 200 and "fornecedores" in r.get_json()


def test_roteiro_lista_sem_termo_de_busca(client, com_compras):
    """Com fornecedor escolhido a lista aparece SEM digitar nada — é ela que É o roteiro da
    visita. Sem fornecedor, termo curto continua devolvendo vazio (varrer o cadastro por nada
    é caro e inútil)."""
    assert client.get("/estoque/api/busca?fornec=11161").status_code == 200
    assert client.get("/estoque/api/busca?q=a").get_json()["produtos"] == []


def test_o_roteiro_nao_e_cortado_em_30(client, com_compras):
    """Busca livre corta em 30 (tela de polegar). O roteiro vai a 200: cortar a lista do
    fornecedor deixaria o comprador sem saber o que falta pesquisar."""
    src = Path("estoque/routes.py").read_text(encoding="utf-8")
    assert "200 if fcod else 30" in src


def test_a_lista_do_fornecedor_sai_por_nome():
    """Em campo se procura pelo nome do produto na prateleira, não pelo código."""
    src = Path("estoque/routes.py").read_text(encoding="utf-8")
    assert 'out.sort(key=lambda x: ((x["descricao"] or "").upper(), x["codprod"]))' in src


def test_apagar_a_busca_volta_a_lista_do_fornecedor():
    assert "if (forn) { listarDoFornecedor(); }" in PAG


# ───────────────── cor e local (decisões do diretor, 19/08) ─────────────────

def test_a_cor_e_pela_perspectiva_do_NOSSO_preco():
    """⚠️ Eu implementei INVERTIDO na primeira versão. `delta` é (pesquisado − nosso), então:

        delta < 0  → acharam mais barato → o NOSSO está caro  → VERMELHO
        delta > 0  → acharam mais caro   → o NOSSO está bom   → VERDE

    A leitura de "oportunidade" (achei barato = verde) é a intuitiva e está errada aqui: para
    quem compra, vermelho tem de significar "pago mal, aja". Decisão explícita do diretor.
    Este teste existe porque a inversão é fácil de fazer sem perceber — eu fiz."""
    assert "m.delta_pct<0?'var(--red)':'var(--green)'" in PAG, \
        "tela de campo: nosso mais caro (delta<0) tem de ser VERMELHO"
    js = Path("static/estoque/estoque.js").read_text(encoding="utf-8")
    assert "${d<0?C.red:C.green}" in js, \
        "drawer 360°: nosso mais caro (d<0) tem de ser VERMELHO"


def test_o_local_da_pesquisa_e_pedido_e_exibido():
    """"Ta faltando o lugar da pesquisa / qual loja ou atacado foi pesquisado" (diretor).
    Preço sem origem não se confere nem se volta a negociar — vale metade."""
    assert "Onde foi pesquisado" in PAG, "o rótulo tem de dizer que é o LUGAR"
    assert "local não informado" in PAG, "quando vazio, a lista tem de dizer que falta"
    src = Path("estoque/routes.py").read_text(encoding="utf-8")
    assert '("origem", "Onde pesquisou"' in src, "a coluna do PDF/Excel também"
