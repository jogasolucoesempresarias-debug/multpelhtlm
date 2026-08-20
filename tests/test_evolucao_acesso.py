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
    s = historico.agregar([(date(2026, 8, 14), 0, 0.0, 2.0, 0, None, None, "A", 0, 0)])
    assert s[0]["n_ruptura"] == 1


def test_a_tela_separa_sem_foto_de_sem_dado_no_recorte():
    """18/08: a demo tinha 90 fotos, o filtro de curva não casou nada e a tela disse "a primeira
    foto ainda não foi tirada". O diretor e eu fomos os dois atrás do seeder — que estava certo.

    Confundir "a medição não começou" com "seu filtro não achou nada" manda a pessoa caçar o
    problema errado. `J.log` é quem sabe a diferença: ele lista os dias fotografados SEM recorte.
    Gate de código porque a decisão vive no JS."""
    from pathlib import Path
    js = Path("static/estoque/estoque.js").read_text(encoding="utf-8")
    ini = js.index("async function renderEvolucao(")
    bloco = js[ini:ini + 4000]
    assert "const temFoto=(J.log||[]).length>0" in bloco, \
        "a tela precisa distinguir 'sem foto' de 'sem dado no recorte' pelo log"
    assert "Nenhum dado para este recorte." in bloco
    assert "A primeira foto ainda não foi tirada." in bloco
    assert "curva C" in bloco, \
        "sem venda na janela, o catálogo inteiro vira C — a tela tem de explicar isso"


def test_um_ponto_so_ainda_desenha_o_grafico():
    """19/08, 1ª foto em PRODUÇÃO: "PQ ficou em branco?" (diretor).

    O Chart.js pinta SEGMENTOS entre pontos. Com `pointRadius:0` e um único dia não há segmento
    nenhum, então a área da cobertura e a linha de ruptura saíam **em branco** — mas com o eixo
    já escalado no valor certo (R$ 7.000k / 300), o que prova que o dado tinha chegado. A barra
    do 1º gráfico aparecia porque barra se desenha POR ponto, e isso fez parecer que só a
    cobertura tinha falhado.

    O gate mora aqui porque o sintoma só existe no PRIMEIRO dia de cada instância nova — quem
    for mexer nesses gráficos depois já terá série cheia e nunca mais verá o bug.
    """
    from pathlib import Path
    js = Path("static/estoque/estoque.js").read_text(encoding="utf-8")
    ini = js.index("async function renderEvolucao(")
    bloco = js[ini:js.index("function wireEvo(", ini)]
    assert "dias.length===1 ? 4 : 0" in bloco, \
        "série de 1 dia precisa de marcador, senão o gráfico sai vazio com dado dentro"
    # `pointRadius:0,` é a forma do CÓDIGO (sempre seguida de vírgula) — sem ela o assert casaria
    # o próprio comentário que explica o bug, e o gate reprovaria o conserto
    assert "pointRadius:0," not in bloco, \
        "pointRadius fixo em 0 volta a apagar o gráfico do primeiro dia"
    assert bloco.count("pointRadius:_pr") == 6, \
        ("TODO dataset de linha/área precisa do marcador: parado, EM DESACELERAÇÃO, OCUPAÇÃO, "
         "cobertura, ruptura e ruptura/curva. Subiu para 6 em 08/2026 (watchlist + ocupação do WMS): "
         "série nova que esquecesse o `_pr` sairia invisível no 1º dia de cada instância, "
         "que é exatamente o bug que este gate existe para impedir.")


def test_com_uma_foto_a_variacao_nao_finge_medicao():
    """Mesmo dia, mesmo print: os quatro KPIs diziam "R$ 0,00 (0%) na janela".

    Com uma foto, `dias[0]` e `dias[-1]` são o MESMO ponto e o delta é zero por construção —
    mas "0%" se lê como "não mudou nada", quando o certo é "ainda não dá para comparar". A aba
    inteira é construída sobre não afirmar o que não mediu (o log que separa "sem foto" de "sem
    dado", o aviso de maturidade); o delta era o único lugar que ainda afirmava.
    """
    from datetime import date

    from estoque import historico
    from estoque.routes import _EVO_DIRECAO, _resumo_evolucao

    d1, d2 = date(2026, 8, 19), date(2026, 8, 20)
    um = historico.agregar([(d1, 10, 1000.0, 1.0, 10, None, None, "A", 0, 0)])
    r = _resumo_evolucao(um, [{"data": "2026-08-19", "n_itens": 1}])
    assert r["fotos"] == 1 and r["maturidade"] == "enchendo"
    for k in _EVO_DIRECAO:
        assert r["variacao"][k] is None, f"{k}: com uma foto não há variação a declarar"

    # ⚠️ e o caminho normal continua medindo — o conserto não pode calar a série de verdade
    dois = historico.agregar([(d1, 10, 1000.0, 1.0, 10, None, None, "A", 0, 0),
                              (d2, 8, 800.0, 1.0, 8, None, None, "A", 0, 0)])
    r2 = _resumo_evolucao(dois, [])
    assert r2["variacao"]["valor_estoque"]["delta"] == -200.0


def test_o_endpoint_devolve_o_log_que_a_tela_usa_para_decidir():
    """Se o `log` sumir da resposta, a tela volta a dizer 'sem foto' para todo filtro vazio."""
    from estoque import routes
    src = routes.api_evolucao.__doc__ or ""
    import inspect
    corpo = inspect.getsource(routes.api_evolucao)
    assert '"log": log' in corpo and "dias_com_foto" in corpo


def test_ruptura_por_curva_sai_na_foto_do_dia():
    """Pedido do diretor (08/2026): "trazer a ruptura por curva ABC na foto do dia, e incluir o
    % de ruptura além da quantidade de itens".

    Deu para atender sem perder história porque a `curva_abc` já era gravada por item desde a
    criação da aba — a decisão de gravá-la "porque custa nada hoje e custaria meses de série
    depois" pagou no segundo dia de uso.
    """
    from datetime import date

    from estoque import historico

    d = date(2026, 8, 19)
    s = historico.agregar([
        (d, 0, 0.0, 2.0, 0, None, None, "A", 0, 0),      # A em ruptura
        (d, 10, 100.0, 1.0, 10, None, None, "A", 0, 0),  # A ok
        (d, 0, 0.0, 1.0, 0, None, None, "C", 0, 0),      # C em ruptura
        (d, 5, 50.0, 1.0, 5, None, None, "C", 0, 0),
        (d, 5, 50.0, 1.0, 5, None, None, "C", 0, 0),
        (d, 5, 50.0, 1.0, 5, None, None, "C", 0, 0),
    ])[0]
    assert s["n_ruptura"] == 2 and s["n_skus"] == 6
    assert s["pct_ruptura"] == 33.3
    rc = s["ruptura_curva"]
    assert rc["A"] == {"n": 1, "skus": 2, "pct": 50.0}
    assert rc["C"] == {"n": 1, "skus": 4, "pct": 25.0}
    # ⚠️ curva sem NENHUM item existe e não pode virar 0% — 0% se leria como "sem ruptura"
    assert rc["B"] == {"n": 0, "skus": 0, "pct": None}


def test_o_percentual_por_curva_e_o_que_torna_A_e_C_comparaveis():
    """A curva C tem muito mais SKUs que a A (medido na base real: 2.061 contra 366). Na
    contagem crua a C aparece sempre no topo e a pergunta "qual curva está pior" fica sem
    resposta — que é justamente a pergunta do diretor."""
    from datetime import date

    from estoque import historico

    d = date(2026, 8, 19)
    linhas = [(d, 0, 0.0, 1.0, 0, None, None, "A", 0, 0)]                       # 1 de 1 = 100%
    linhas += [(d, 0, 0.0, 1.0, 0, None, None, "C", 0, 0) for _ in range(2)]    # 2 de 10 = 20%
    linhas += [(d, 9, 9.0, 1.0, 9, None, None, "C", 0, 0) for _ in range(8)]
    rc = historico.agregar(linhas)[0]["ruptura_curva"]
    assert rc["C"]["n"] > rc["A"]["n"], "em contagem a C parece pior"
    assert rc["A"]["pct"] > rc["C"]["pct"], "em % a A aparece como a que dói — e é a leitura certa"


def test_e_a_ruptura_REAL_nao_a_regua_da_meta():
    """⚠️ Decisão explícita do diretor: "pode usar a ruptura real, esquece a ruptura da meta; o
    objetivo é medir a evolução da real, o que tem ou não tem de fato no estoque".

    Item zerado COM pedido em aberto continua contando aqui — e por isso este número é MAIOR que
    o do placar da Meta de ruptura, que só conta o que está sem providência
    (`core._sem_providencia`). São duas réguas convivendo de propósito; o que não pode é a tela
    deixar de dizer qual está mostrando, senão alguém lê 11% daqui contra a meta de 2% de lá e
    conclui catástrofe onde não há.
    """
    from pathlib import Path

    js = Path("static/estoque/estoque.js").read_text(encoding="utf-8")
    ini = js.index("async function renderEvolucao(")
    bloco = js[ini:js.index("function wireEvo(", ini)]
    assert "Não é a régua da aba Meta de ruptura" in bloco, \
        "a tela tem de declarar que este % não é o do placar da meta"


def test_item_sem_curva_cai_na_C_como_no_placar_da_meta():
    """Se cada tela decidir sozinha o que fazer com item sem curva, as duas somam universos
    diferentes e ninguém descobre pelo número."""
    from estoque import historico
    assert historico.curva_de(None) == "C"
    assert historico.curva_de("") == "C"
    assert historico.curva_de("z") == "C"
    assert historico.curva_de("a") == "A"


def test_a_ordem_do_select_casa_com_o_agregar():
    """⚠️ A foto viaja como TUPLA POSICIONAL do SQL até o `agregar`. Coluna nova no SELECT sem o
    nome correspondente no desempacotamento (ou vice-versa) desloca TODOS os campos seguintes —
    valor vira giro, data vira cobertura — e a série sai torta sem levantar erro nenhum.

    É o mesmo risco que o `test_a_ordem_de_linha_casa_com_as_colunas_do_insert` cobre do lado da
    GRAVAÇÃO; este cobre o da LEITURA, que estava descoberto.
    """
    import inspect

    from estoque import historico

    sel = historico._SQL_CRU.split("SELECT", 1)[1].split("FROM")[0]
    colunas = [c.strip() for c in sel.split(",")]
    fonte = inspect.getsource(historico.agregar)
    linha = next(l for l in fonte.splitlines() if l.strip().startswith("for dia,"))
    nomes = [x.strip() for x in linha.split("for", 1)[1].split(" in ")[0].split(",")]
    assert len(colunas) == len(nomes), \
        f"SELECT tem {len(colunas)} colunas e o agregar desempacota {len(nomes)}"
    # ⚠️ A ÚLTIMA coluna muda quando o SELECT cresce — e é justamente aí que o desalinhamento
    # se esconde. Em 08/2026 entraram `qtd_ja_pedida`/`qt_transicao` (régua da Meta de ruptura).
    assert colunas[-1] == "qt_transicao" and nomes[-1] == "transito"
    assert "curva_abc" in colunas and "curva" in nomes
