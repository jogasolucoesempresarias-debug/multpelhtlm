# -*- coding: utf-8 -*-
"""Gate das RÉGUAS dos indicadores da Nota do comprador (`routes._indicadores_estoque`).

O `test_nota_escalas.py` trava como se pontua; este trava **o que se mede**. Cada um dos três
indicadores de estoque tem uma régua irmã, muito parecida, que dá outro número — e trocar uma
pela outra não gera erro nenhum, só uma avaliação diferente da pessoa.
"""
from estoque import core, nota
from estoque import routes as R


def _p(cc=1, curva="C", qtdisp=10, giro=1.0, dsv=None, dse=None, valor=100.0, cob=None):
    """Produto sintético no formato que `_build_produtos` entrega."""
    return {"codcomprador": cc, "comprador": "TESTE", "curva_abc": curva,
            "qtdisp": qtdisp, "giro_dia": giro, "dias_sem_venda": dsv,
            "dias_sem_entrada": dse, "valor": valor,
            "cobertura_dias": cob if cob is not None else core.cobertura_dias_oficial(qtdisp, giro)}


PARAMS = core.merge_params({"ideal_dias": 25, "ideal_meta_pct": 70, "novo_dias": 20})


# ───────────────── A. ruptura ─────────────────

def test_a_ruptura_e_a_REAL_e_nao_a_da_meta():
    """⚠️ O módulo tem DUAS réguas de ruptura e a diferença é grande: a real (item zerado com
    giro) e a da Meta de ruptura (só o que está sem providência), sempre menor. Medido em
    07/09/2026: 7,1% × 4,2% para o mesmo comprador — nota 9 contra nota 10. Decisão do usuário:
    a real. Um item COM pedido em aberto tem de continuar contando aqui."""
    prods = [_p(qtdisp=0, giro=1.0), _p(qtdisp=0, giro=1.0), _p(qtdisp=5, giro=1.0)]
    prods[0]["qtd_ja_pedida"] = 999          # providência tomada — irrelevante para esta régua
    prods[1]["qt_transicao"] = 999           # pré-entrada — idem
    r = R._indicadores_estoque(prods, PARAMS)[1]
    assert r["n_ruptura"] == 2
    assert r["ruptura"] == core._round(2 / 3 * 100, 1)


def test_item_zerado_SEM_giro_nao_e_ruptura():
    """Sem giro não há venda perdida — é a mesma condição do KPI da tela."""
    r = R._indicadores_estoque([_p(qtdisp=0, giro=0), _p(qtdisp=5, giro=1.0)], PARAMS)[1]
    assert r["n_ruptura"] == 0


# ───────────────── D. estoque parado ─────────────────

def test_o_parado_DESCARTA_novo_e_recem_chegado():
    """⚠️ A régua é "a aba Parado mesmo, sem contar os produtos até 20 dias / novos" (diretor,
    07/09/2026). A aba mostra `novo` e `recem_chegado` em cards SEPARADOS, fora das faixas —
    contá-los aqui infla o indicador de quem acabou de receber mercadoria. Foi exatamente o que
    a medição manual fez de errado antes deste gate existir."""
    prods = [
        _p(dsv=None, dse=5),        # nunca vendeu, chegou há 5d  -> `novo`
        _p(dsv=300, dse=3),         # vendeu, parou, chegou agora -> `recem_chegado`
        _p(dsv=40),                 # parado de verdade (faixa 31-60)
        _p(dsv=2),                  # ativo (abaixo do piso de 15)
    ]
    r = R._indicadores_estoque(prods, PARAMS)[1]
    assert r["n_parado"] == 1
    assert r["parado"] == 25.0                      # 1 de 4 SKUs


def test_o_parado_e_CONTAGEM_DE_SKUS_e_nao_valor():
    """⚠️ A escolha que faz o indicador existir. Medido em 07/09: por VALOR os três compradores
    dão 3,0% / 1,8% / 1,9% — todos na faixa "até 20%", nota 10, e 20% do peso da nota vira
    constante. Por SKU dão 35,7% / 27,6% / 14,2% e separam os três. Um item parado caríssimo não
    pode valer mais que um barato nesta conta."""
    caro_parado = _p(dsv=40, valor=1_000_000.0)
    baratos_ok = [_p(dsv=1, valor=1.0) for _ in range(9)]
    r = R._indicadores_estoque([caro_parado] + baratos_ok, PARAMS)[1]
    assert r["parado"] == 10.0, "1 SKU de 10 = 10%, independentemente do valor"


def test_o_denominador_do_parado_e_o_TOTAL_de_SKUs():
    """Mesma base do `pct_ruptura`. Trocar por "SKUs com estoque" mudaria o número sem mudar a
    operação — e o placar andaria sozinho."""
    prods = [_p(dsv=40), _p(qtdisp=0, giro=1.0), _p(dsv=1)]
    r = R._indicadores_estoque(prods, PARAMS)[1]
    assert r["n_skus"] == 3 and r["parado"] == core._round(1 / 3 * 100, 1)


def test_novo_dias_move_a_fronteira_do_novo():
    """O diretor pediu 20 dias (o oficial estava em 15). O parâmetro tem de mandar de verdade."""
    prods = [_p(dsv=None, dse=18)]
    assert R._indicadores_estoque(prods, core.merge_params({"novo_dias": 20}))[1]["n_parado"] == 0
    assert R._indicadores_estoque(prods, core.merge_params({"novo_dias": 15}))[1]["n_parado"] == 1


# ───────────────── B. cobertura ─────────────────

def test_a_cobertura_olha_SO_as_curvas_A_e_B():
    """O documento restringe este indicador a A+B. Item C na conta mudaria o número sem mudar
    nada na operação — a curva C é ~6x maior e domina qualquer média."""
    prods = [_p(curva="A", qtdisp=100, giro=1.0),     # cobertura 100d -> ideal
             _p(curva="B", qtdisp=100, giro=1.0),     # ideal
             _p(curva="C", qtdisp=1, giro=1.0)]       # em risco, mas NÃO conta
    r = R._indicadores_estoque(prods, PARAMS)[1]
    assert r["cobertura"] == 100.0
    assert r["cobertura_ab_n"] == 2


def test_comprador_sem_itens_A_ou_B_fica_SEM_cobertura_e_nao_com_zero():
    """⚠️ Zero seria nota 4 (a pior faixa) para quem simplesmente não tem item A/B na carteira —
    punido pelo mix, não pelo desempenho. Aconteceu de verdade: um comprador tem 1 SKU, nenhum A+B.
    Sem medição não há nota, e sem nota ela sai do ranking em vez de aparecer em último."""
    r = R._indicadores_estoque([_p(curva="C")], PARAMS)[1]
    assert r["cobertura"] is None
    assert nota.nota_de("cobertura", r["cobertura"]) is None


def test_a_cobertura_sai_em_PERCENTUAL_e_nao_em_fracao():
    """⚠️ `resumo_estoque_ideal` devolve `pct` em fração (0-1) e a escala pontua em %. Sem a
    conversão, 73,2% chegaria como 0,732 e a nota 10 viraria nota 4 — em silêncio."""
    r = R._indicadores_estoque([_p(curva="A", qtdisp=100, giro=1.0)], PARAMS)[1]
    assert r["cobertura"] == 100.0 and r["cobertura"] > 1


def test_ideal_dias_move_a_fronteira_da_cobertura():
    """A régua oficial saiu de 45 para 25 dias em 01/09; o indicador tem de seguir o parâmetro."""
    prods = [_p(curva="A", qtdisp=30, giro=1.0)]      # cobertura 30d
    assert R._indicadores_estoque(prods, core.merge_params({"ideal_dias": 25}))[1]["cobertura"] == 100.0
    assert R._indicadores_estoque(prods, core.merge_params({"ideal_dias": 45}))[1]["cobertura"] == 0.0


# ───────────────── separação por comprador ─────────────────

def test_os_compradores_nao_se_misturam():
    prods = [_p(cc=1, qtdisp=0, giro=1.0), _p(cc=1, qtdisp=5), _p(cc=2, qtdisp=5)]
    ind = R._indicadores_estoque(prods, PARAMS)
    assert ind[1]["ruptura"] == 50.0
    assert ind[2]["ruptura"] == 0.0


# ───────────────── E. mês fechado ─────────────────

def test_o_indicador_de_compras_usa_o_mes_FECHADO():
    """⚠️ Medido em 07/09 (dia 7 do mês): o mês corrente dava 42,6% da meta para o João — nota 4
    por "subcompra" com 23 dias de mês pela frente. Comparar realizado parcial contra meta cheia
    é errado por construção, e isto avalia uma pessoa. Agosto fechado dá 128,1%, o número real."""
    from datetime import date
    assert R._mes_fechado(date(2026, 9, 7)) == "2026-08"
    assert R._mes_fechado(date(2026, 1, 15)) == "2025-12"     # vira o ano


# ───────────────── ausência ≠ zero, ponta a ponta ─────────────────

def test_comprador_sem_produto_nenhum_nao_aparece_com_nota_10():
    """`_pct_nota` devolve None quando o denominador é zero. Um comprador de carteira vazia com
    "ruptura 0%" e "parado 0%" apareceria com duas notas 10 — o topo do ranking por não ter nada."""
    assert R._pct_nota(0, 0) is None
    assert R._pct_nota(0, 10) == 0.0


def test_a_meta_do_mes_fechado_e_ancorada_no_FECHAMENTO_e_nao_em_hoje():
    """⚠️ O defeito que este gate impede: a meta do Orçamento é 65% da venda dos ÚLTIMOS 30 DIAS,
    e essa janela é medida a partir do `hoje` que se passa. Usando o relógio, o realizado de
    AGOSTO passava a ser comparado com uma meta que anda todo dia — flagrado ao virar 07→08/09/2026,
    quando duas notas de Compras subiram de 9 para 10 da noite para o dia sem ninguém comprar nada.

    Numa avaliação de pessoa, a nota de um mês fechado tem de ser a MESMA em qualquer dia em que
    se olhe para ela."""
    from datetime import date
    # a âncora é o último dia do mês avaliado, não importa quando se pergunta
    for hoje in (date(2026, 9, 8), date(2026, 9, 30), date(2027, 3, 1)):
        assert R._fim_do_mes("2026-08", hoje) == date(2026, 8, 31), hoje
    assert R._fim_do_mes("2026-02") == date(2026, 2, 28)      # fevereiro
    assert R._fim_do_mes("2024-02") == date(2024, 2, 29)      # bissexto
    assert R._fim_do_mes("2026-12") == date(2026, 12, 31)


def test_a_ancora_nunca_passa_do_teto():
    """Se o mês de referência for o corrente (não deveria, mas o teto é barato), a janela não pode
    ir ao FUTURO: pediria venda de dias que ainda não aconteceram e a meta sairia menor."""
    from datetime import date
    assert R._fim_do_mes("2026-09", date(2026, 9, 8)) == date(2026, 9, 8)


def test_a_meta_de_margem_e_lida_na_competencia_de_HOJE_e_nao_no_mes_fechado():
    """🩹 O bug que deixou o diretor esperando 1h por uma meta que nunca chegaria (08/09/2026:
    *"atualizei a meta, mas não veio para cá ainda"*).

    A 1ª versão lia a meta na competência do mês FECHADO, e isso estava errado duas vezes:

    1. **Descasamento.** A margem REALIZADA vem do `_desempenho_data` no seletor "Venda", cujo
       default é o mês CORRENTE — dividir isso pela meta do mês anterior compara períodos
       diferentes;
    2. **E o efeito visível:** o painel do Admin grava na competência corrente (é o default do
       seletor lá). A meta ia para 2026-09 e a nota procurava em 2026-08; como a busca é
       `(ano*100+mes) <= competência`, a meta recém-cadastrada não existia para a nota. Nenhum
       erro em lugar nenhum — só "— —" na coluna, para sempre.

    ⚠️ O mês FECHADO continua certo para o indicador de COMPRAS. São janelas diferentes de
    propósito, e este gate trava que elas não voltem a se confundir."""
    import inspect
    fonte = inspect.getsource(R.api_nota)
    codigo = [l for l in fonte.splitlines() if not l.strip().startswith("#")]
    # a meta sai de hoje...
    assert any("ano_meta, mes_meta = hoje.year, hoje.month" in l for l in codigo), \
        "a meta de margem tem de ser lida na competência de hoje"
    assert any("store.metas_margem(ano_meta, mes_meta)" in l for l in codigo)
    # ...e o mês fechado NÃO pode alimentar a leitura da meta
    for i, linha in enumerate(codigo):
        if "metas_margem(" in linha:
            assert "mes_ref" not in linha and "_mes_fechado" not in linha, \
                f"a meta voltou a ser lida no mês fechado: {linha.strip()}"
    # e o mês fechado continua servindo às COMPRAS
    assert any("_mes_fechado(hoje)" in l for l in codigo)


def test_as_duas_competencias_viajam_para_a_tela():
    """Numa tela que avalia pessoas, janela não declarada é número sem definição. A resposta tem
    de dizer AS DUAS: a da meta de margem (mês corrente) e a de compras (mês fechado)."""
    import inspect
    fonte = inspect.getsource(R.api_nota)
    assert '"mes_meta"' in fonte and '"mes_compras"' in fonte


def test_a_margem_da_nota_NAO_segue_o_seletor_de_venda_do_topo():
    """🩹 A nota não pode mudar por causa de um filtro que alguém deixou ligado na tela.

    Medido no BI real em 08/09/2026, o MESMO comprador no MESMO dia, variando só o seletor:
        mês 17,1% · 30d 16,5% · 90d 16,3% · 6m 17,1% · 12m 15,9%
    Com uma meta realista de 17% isso é **nota 9 no "mês" e nota 7 no "12m"** — duas avaliações
    da mesma pessoa. E junto vinha um erro conceitual: a meta de margem é MENSAL, então comparar
    12 meses de realizado contra ela soma períodos diferentes.

    ⚠️ As OUTRAS abas seguem respeitando o seletor; quem não pode é a nota."""
    import inspect
    assert R.PERIODO_MARGEM_NOTA == "mes", "a margem da nota é a da competência (mês corrente)"
    fonte = inspect.getsource(R.api_nota)
    codigo = [l for l in fonte.splitlines() if not l.strip().startswith("#")]
    for i, linha in enumerate(codigo):
        if "_desempenho_data(" in linha:
            assert "PERIODO_MARGEM_NOTA" in linha, \
                f"a margem da nota voltou a seguir o seletor: {linha.strip()}"
            assert "venda_periodo" not in linha, linha.strip()


def test_o_mes_em_curso_viaja_como_INFORMACAO_e_nao_entra_na_nota():
    """Decisão do diretor (08/09/2026, depois de ver a medição): *"blz, vamos fazer assim e
    avaliar"*. Ele queria o mês corrente na nota; a medição mostrou que no dia 8 de 30 ele não
    discrimina — cru dá 44,5/35,2/2,0 e pró-rata dá 166,7/132,1/7,5, **nota 4 para os três nos
    dois casos**. Então ele aparece ao lado, e a nota segue no mês fechado.

    ⚠️ Este gate trava o "não entra": `nota_final` só recebe as cinco chaves da régua."""
    import inspect
    fonte = inspect.getsource(R.api_nota)
    codigo = [l for l in fonte.splitlines() if not l.strip().startswith("#")]
    # o campo existe e viaja
    assert any('"compras_em_curso"' in l for l in codigo)
    assert any('"mes_em_curso"' in l for l in codigo)
    # ...mas nunca alimenta a nota
    trecho = "\n".join(codigo)
    inicio = trecho.find("nota.nota_final(")
    fim = trecho.find(")", trecho.find("compras", inicio))
    assert "em_curso" not in trecho[inicio:fim + 1], \
        "o mês em curso não pode entrar no cálculo da nota"


def test_a_curva_ABC_da_nota_tem_janela_FIXA_e_nao_a_do_seletor():
    """🩹 O segundo caminho pelo qual o seletor do topo mexia na nota — e o mais escondido.

    A **curva ABC é o Pareto da venda do PERÍODO**, então trocar o seletor muda QUAIS itens são
    A+B e, com eles, o indicador de Cobertura. Medido no BI real em 08/09/2026, o mesmo comprador
    no mesmo dia:
        mês 65,0% (214 itens A+B) · 90d 59,2% (306) · 12m 54,6% (326)  →  notas 9, 7 e 6.

    A janela fixa é a MESMA da foto diária (`historico.PERIODO_CURVA`, 90 dias), o que de quebra
    alinha a MATRIZ com o GRÁFICO do drill: a série lê a curva gravada na foto, então antes as
    duas metades da mesma tela respondiam com curvas diferentes.

    ⚠️ O override é SÓ da nota. As outras abas seguem respeitando o seletor — o default de
    `_build_produtos` continua sendo ler o `request.args`."""
    import inspect

    from estoque import historico
    fonte = inspect.getsource(R.api_nota)
    codigo = [l for l in fonte.splitlines() if not l.strip().startswith("#")]
    chamada = [l for l in codigo if "_build_produtos(" in l]
    assert chamada, "a nota parou de montar produtos?"
    assert all("historico.PERIODO_CURVA" in l for l in chamada), \
        f"a nota voltou a herdar a janela do seletor: {chamada}"
    assert historico.PERIODO_CURVA == "90d"
    # e o default de `_build_produtos` continua sendo o seletor, para as OUTRAS abas
    padrao = inspect.signature(R._build_produtos).parameters["venda_periodo"].default
    assert padrao is None, "o override é SÓ da nota — as outras abas seguem o seletor"


def test_a_nota_declara_as_janelas_que_usa():
    """Numa tela que avalia pessoas, janela não declarada é número sem definição. A resposta leva
    as quatro: competência da meta, mês de compras, período da margem e janela da curva."""
    import inspect
    fonte = inspect.getsource(R.api_nota)
    for chave in ('"mes_meta"', '"mes_compras"', '"periodo_margem"', '"periodo_curva"',
                  '"mes_em_curso"'):
        assert chave in fonte, chave
