# -*- coding: utf-8 -*-
"""Gate das seis escalas da Nota do comprador (`estoque/nota.py`).

O módulo é PURO, então estes testes não precisam de Postgres nem de Power BI — e é justamente por
isso que eles são o gate mais barato do recurso: uma faixa trocada aqui muda a avaliação de uma
pessoa em toda a série histórica, sem erro nenhum em lugar nenhum.
"""
import pytest

from estoque import nota


# ───────────────── 1. o contrato dos pesos ─────────────────

def test_os_pesos_somam_100():
    """Peso solto redistribui a nota de todo mundo em silêncio: 95% de peso faria toda nota cair
    5% e parecer queda de desempenho."""
    assert sum(nota.PESOS.values()) == 100


def test_todo_indicador_da_ORDEM_tem_peso_rotulo_e_regua():
    """A tela desenha a partir destes dicionários. Indicador sem régua escrita é número sem
    definição numa tela que avalia pessoas."""
    for k in nota.ORDEM:
        assert k in nota.PESOS and nota.ROTULOS.get(k) and nota.REGUAS.get(k)
    assert set(nota.ORDEM) == set(nota.PESOS)


# ───────────────── 2. o exemplo do documento ─────────────────

def test_o_exemplo_do_documento_reproduz_7_65_e_so_COMPRAS_divergiu():
    """A tabela do item 5 do documento, linha a linha.

    ⚠️ O total do documento é **7,50** e hoje o mesmo exemplo dá **7,65**. A diferença é UM
    indicador e é DELIBERADA: em 09/2026 a escala de Compras deixou de ser simétrica (ver
    `nota.ESCALA_COMPRAS`), e o 117% do exemplo passou de nota 7 para 8. Os outros quatro
    seguem reproduzindo o documento no ponto — é isso que este teste protege.

    Mantido como prova de TRANSCRIÇÃO: se algum dia um dos quatro sair do lugar, a falha aparece
    aqui e não numa tela. Trocar o 7,65 por outro número exige a mesma justificativa que a
    mudança de 09/2026 teve: medição, não preferência."""
    r = nota.nota_final({"ruptura": 5.9, "cobertura": 74.3, "margem": 95.9,
                         "parado": 48.6, "compras": 117.0})
    assert r["completa"] is True
    esperado = {"ruptura": (9, 2.25), "cobertura": (10, 2.0), "margem": (8, 1.6),
                "parado": (3, 0.6), "compras": (8, 1.2)}
    for i in r["itens"]:
        n, p = esperado[i["indicador"]]
        assert (i["nota"], i["pontos"]) == (n, p), i["indicador"]
    assert r["nota"] == 7.65
    # o documento fechava em 7,50 com compras=7; a diferença é exatamente o degrau de 1 ponto
    assert round(7.65 - (8 - 7) * 0.15, 2) == 7.50


def test_o_pior_indicador_do_exemplo_e_o_estoque_parado():
    """Item 8 do documento: o exemplo dele fecha em nota 7,5 com 'Estoque parado: crítico →
    Prioridade sugerida: reduzir estoque parado'."""
    r = nota.nota_final({"ruptura": 5.9, "cobertura": 74.3, "margem": 95.9,
                         "parado": 48.6, "compras": 117.0})
    assert r["pior"] == "parado"


# ───────────────── 3. as fronteiras de cada escala ─────────────────
# ⚠️ O documento escreve as faixas COLADAS mas não contíguas ("Até 5%" / "5,1% a 8%"), então
# 5,00 < v < 5,10 não tem faixa na leitura literal. A régua aqui é contínua e o valor do meio cai
# na faixa de BAIXO (5,05 -> 9): "até 5%" é até 5,0 mesmo. Estes casos travam essa decisão, que
# é a diferença entre inflar e não inflar a nota de alguém por arredondamento.

@pytest.mark.parametrize("v,esperado", [
    (0, 10), (5, 10), (5.05, 9), (5.1, 9), (8, 9), (8.1, 8), (12, 8),
    (15, 7), (15.1, 6), (20, 6), (25, 4), (25.1, 2), (100, 2),
])
def test_escala_ruptura(v, esperado):
    assert nota.nota_de("ruptura", v) == esperado


@pytest.mark.parametrize("v,esperado", [
    (100, 10), (70, 10), (69.9, 9), (65, 9), (64.9, 8), (60, 8),
    (55, 7), (50, 6), (49.9, 4), (0, 4),
])
def test_escala_cobertura(v, esperado):
    assert nota.nota_de("cobertura", v) == esperado


@pytest.mark.parametrize("v,esperado", [
    (200, 10), (105, 10), (104.9, 9), (100, 9), (95, 8), (95.9, 8),
    (90, 7), (85, 6), (84.9, 4), (0, 4),
])
def test_escala_margem(v, esperado):
    assert nota.nota_de("margem", v) == esperado


@pytest.mark.parametrize("v,esperado", [
    (0, 10), (20, 10), (20.1, 9), (25, 9), (30, 8), (35, 7),
    (40, 6), (45, 5), (45.1, 3), (48.6, 3), (100, 3),
])
def test_escala_parado(v, esperado):
    assert nota.nota_de("parado", v) == esperado


@pytest.mark.parametrize("v,esperado", [
    (90, 10), (100, 10), (105, 10), (89.9, 9), (105.1, 9), (80, 9), (112, 9),
    (70, 8), (119, 8), (58, 6), (128, 6), (44, 4), (138, 4), (138.1, 2), (300, 2), (0, 2),
])
def test_escala_compras(v, esperado):
    assert nota.nota_de("compras", v) == esperado


def test_a_escala_de_compras_e_ASSIMETRICA_o_estouro_dói_mais():
    """Pedido do João Victor em 09/2026: *"punir mais o estouro que a falta de compra"*.

    A justificativa não é gosto: perguntado se o 65% do Orçamento é meta de reposição ou de
    enxugamento, ele respondeu *"nesse momento é calibrar o estoque"*. Numa fase de calibração a
    meta é TETO, não alvo — ficar abaixo não desfaz a política, estourar desfaz.

    ⚠️ Isto REVERTE o documento, que pedia simetria explícita. A reversão é intencional e o
    motivo é a fase, não o indicador. Se a empresa voltar a repor estoque, isto tem de voltar."""
    for lo, hi, _ in nota.ESCALA_COMPRAS[1:]:
        abaixo = nota.ESCALA_COMPRAS[0][0] - lo         # quanto se pode CAIR
        acima = hi - nota.ESCALA_COMPRAS[0][1]          # quanto se pode SUBIR
        assert abaixo > acima, (lo, hi)
        assert 1.3 <= abaixo / acima <= 1.5, (lo, hi, abaixo / acima)


def test_comprar_ZERO_e_o_pior_caso_da_escala():
    """A 1ª versão do pedido punha "abaixo de 90% → 5" e "acima de 110% → 0" — o que fazia quem
    **não comprou nada** valer mais que quem estourou 1 p.p., num indicador chamado
    "Compras × Meta". Este teste é o que impede a ideia de voltar."""
    piores = min(nota.nota_de("compras", v / 10) for v in range(0, 3001))
    assert nota.nota_de("compras", 0) == piores
    for v in (44, 58, 70, 80, 90, 100, 112, 128):
        assert nota.nota_de("compras", v) > nota.nota_de("compras", 0), v


def test_nenhum_degrau_da_escala_de_compras_passa_de_2_pontos():
    """Requisito, não estética. O denominador é a venda dos últimos 30 dias, que se mexe sozinha
    **7,3% ao mês** na mediana (medido em 6 fechamentos; um comprador viu a meta cair 19,1% de
    jul para ago/2026). Com penhasco, a nota de uma pessoa vira por causa da venda dos outros —
    a proposta original tinha um degrau de **7 pontos em 0,1 p.p.**."""
    xs = [x / 10 for x in range(0, 3001)]
    saltos = [abs(nota.nota_de("compras", xs[i]) - nota.nota_de("compras", xs[i - 1]))
              for i in range(1, len(xs))]
    assert max(saltos) <= 2


def test_a_banda_de_124pct_ja_custa_4_pontos_porque_e_ali_que_o_estoque_CRESCE():
    """A âncora da assimetria. A meta é 65% da venda líquida e a reposição pura (custo do que se
    vendeu) medida em abr–ago/2026 é **80,6%** da venda: o comprador só passa a CRESCER estoque
    a partir de 80,6 ÷ 65 = **124%** da meta. Abaixo disso, mesmo "estourando", ele ainda
    desestoca — por isso o castigo pesado começa depois dessa banda, não antes.
    ⚠️ Se o 65% mudar, o 124% muda junto. Remedir antes de mexer nas faixas."""
    assert nota.nota_de("compras", 124) == 6
    assert nota.nota_de("compras", 119) == 8


# ───────────────── 4. não medido ≠ zero ─────────────────

def test_None_nao_e_zero():
    """⚠️ A confusão que este teste impede: comprador sem meta de margem cadastrada apareceria
    com atingimento 0% e nota 4 — punido por um cadastro que ninguém preencheu."""
    assert nota.nota_de("margem", None) is None
    assert nota.nota_de("margem", "") is None
    assert nota.nota_de("margem", "abc") is None
    assert nota.nota_de("margem", 0) == 4          # zero É medição: atingiu 0% da meta
    assert nota.nota_de("ruptura", 0) == 10        # e aqui zero é o melhor resultado possível


def test_sem_meta_de_margem_a_nota_sai_PARCIAL_e_renormalizada():
    """Decisão do usuário (09/2026): "traz a nota mesmo sem a margem; quando inserir, recalcula".

    ⚠️ O ponto que faz isso funcionar é a RENORMALIZAÇÃO. Sem dividir pelo peso medido, quem não
    tem meta perderia 20 p.p. de peso e teria teto de 8,0 — ficaria atrás por causa de um cadastro,
    não do trabalho. Com a divisão, a nota fica na escala 0-10 e comparável."""
    r = nota.nota_final({"ruptura": 7.1, "cobertura": 73.2, "margem": None,
                         "parado": 36.1, "compras": 128.1})
    assert r["completa"] is False
    assert r["parcial"] is True
    assert r["peso_medido"] == 80
    assert r["faltando"] == ["margem"]
    # 9×25 + 10×20 + 6×20 + 4×15 = 605 pontos sobre 80 de peso -> 7,56 (e não 6,05)
    assert r["nota"] == 7.56
    assert r["cor"] == "amarelo"
    medidos = {i["indicador"]: i["nota"] for i in r["itens"] if i["nota"] is not None}
    assert medidos == {"ruptura": 9, "cobertura": 10, "parado": 6, "compras": 4}


def test_a_nota_MUDA_quando_a_meta_entra_e_por_isso_o_selo_parcial_existe():
    """⚠️ O efeito colateral aceito da decisão: cadastrar a meta move a nota, para cima ou para
    baixo. É legítimo (passou a medir mais coisa), mas indistinguível de desempenho se a tela não
    disser. Por isso `parcial` viaja na resposta — este teste trava o par nota+selo."""
    base = {"ruptura": 7.1, "cobertura": 73.2, "parado": 36.1, "compras": 128.1}
    sem = nota.nota_final({**base, "margem": None})
    boa = nota.nota_final({**base, "margem": 110})       # nota 10 na margem: puxa para CIMA
    ruim = nota.nota_final({**base, "margem": 50})       # nota 4: puxa para BAIXO
    assert sem["parcial"] is True and boa["parcial"] is False and ruim["parcial"] is False
    assert boa["nota"] > sem["nota"] > ruim["nota"], (boa["nota"], sem["nota"], ruim["nota"])


def test_a_nota_parcial_de_UM_indicador_so_ainda_sai_na_escala_0_10():
    """O caso extremo da renormalização: medido só a ruptura, a nota é a própria nota dela."""
    r = nota.nota_final({"ruptura": 0, "cobertura": None, "margem": None,
                         "parado": None, "compras": None})
    assert (r["nota"], r["peso_medido"], r["parcial"]) == (10.0, 25, True)


def test_sem_indicador_NENHUM_nao_ha_o_que_renormalizar():
    """Divisão por zero de peso. `None` — e a tela mostra "sem dado", não uma nota inventada."""
    r = nota.nota_final({})
    assert r["nota"] is None and r["parcial"] is False and r["cor"] is None


def test_o_pior_indicador_sai_mesmo_com_nota_incompleta():
    """A prioridade de ação (item 8) não depende da meta de margem — é o que a aba tem de útil
    enquanto o cadastro não chega."""
    r = nota.nota_final({"ruptura": 7.1, "cobertura": 73.2, "margem": None,
                         "parado": 36.1, "compras": 128.1})
    assert r["pior"] == "compras"          # nota 4, a menor entre os medidos


def test_empate_no_pior_desempata_pelo_PESO():
    """Dois indicadores com a mesma nota: prioridade é o que dói mais na nota final. Sem esta
    regra o resultado dependeria da ordem do dicionário e mudaria entre versões do Python."""
    r = nota.nota_final({"ruptura": 25, "cobertura": 49, "margem": 84,
                         "parado": 20, "compras": 100})
    # ruptura=4 (peso 25), cobertura=4 (peso 20), margem=4 (peso 20) -> vence o peso 25
    assert r["pior"] == "ruptura"


# ───────────────── 5. ranking ─────────────────

def test_o_ranking_INCLUI_a_nota_parcial():
    """Decisão do usuário: quem está sem meta entra no ranking com a nota parcial, em vez de ficar
    de fora. ⚠️ Isso mistura réguas — a parcial responde a menos indicador — e é por isso que a
    tela marca cada linha parcial com um selo."""
    base = {"ruptura": 7.1, "cobertura": 73.2, "parado": 36.1, "compras": 128.1}
    a = {"codcomprador": 1, "nome": "A", **nota.nota_final({**base, "margem": 120})}
    b = {"codcomprador": 2, "nome": "B", **nota.nota_final({**base, "margem": 85})}
    c = {"codcomprador": 3, "nome": "C", **nota.nota_final({**base, "margem": None})}
    nota.ranking([a, b, c])
    assert all(x["posicao"] is not None for x in (a, b, c)), "todos têm nota, todos ranqueiam"
    ordenado = sorted([a, b, c], key=lambda x: x["posicao"])
    assert [x["nota"] for x in ordenado] == sorted([x["nota"] for x in ordenado], reverse=True)


def test_quem_nao_tem_indicador_NENHUM_fica_de_fora_do_ranking():
    """O único caso que ainda sai: sem nota não há posição."""
    vazio = {"codcomprador": 9, "nome": "Z", **nota.nota_final({})}
    com = {"codcomprador": 1, "nome": "A",
           **nota.nota_final({"ruptura": 1, "cobertura": 99, "margem": 110,
                              "parado": 1, "compras": 100})}
    nota.ranking([vazio, com])
    assert com["posicao"] == 1 and vazio["posicao"] is None


def test_o_ranking_desempata_pelo_nome_e_nao_pela_ordem_de_entrada():
    """Duas notas iguais têm de sair sempre na mesma ordem — senão a posição do comprador muda a
    cada F5 e o ranking perde credibilidade. (Medido em 07/09: dois compradores empataram em 6,05
    nos 4 indicadores sem margem, então o empate não é hipotético.)"""
    ind = {"ruptura": 7.1, "cobertura": 73.2, "margem": 100, "parado": 36.1, "compras": 128.1}
    z = {"codcomprador": 1, "nome": "Zeca", **nota.nota_final(ind)}
    a = {"codcomprador": 2, "nome": "Ana", **nota.nota_final(ind)}
    nota.ranking([z, a])
    assert (a["posicao"], z["posicao"]) == (1, 2)


# ───────────────── 6. cor e versão ─────────────────

@pytest.mark.parametrize("n,cor", [(10, "verde"), (8, "verde"), (7.9, "amarelo"),
                                   (6, "amarelo"), (5.9, "vermelho"), (0, "vermelho")])
def test_cor_da_nota(n, cor):
    assert nota.cor_da_nota(n) == cor


def test_a_versao_da_regua_viaja_na_resposta():
    """⚠️ Sem o selo, a nota de setembro não tem como ser explicada em dezembro: mudar uma faixa
    reescreve a série inteira (a nota é recalculada, nunca gravada) e não sobra rastro de qual
    régua produziu o número. Mesmo papel do `historico._ROLLUP_VERSAO`."""
    r = nota.nota_final({"ruptura": 1, "cobertura": 99, "margem": 110,
                         "parado": 1, "compras": 100})
    assert r["versao"] == nota.NOTA_VERSAO == nota.escalas_publicas()["versao"]


def test_escalas_publicas_expoe_as_seis_escalas_serializaveis():
    """A tela desenha a régua a partir daqui. Duas cópias da mesma faixa (uma em Python, outra em
    JS) divergem no primeiro ajuste — foi assim que a aba Fornecedores passou a ser calculada
    duas vezes."""
    import json
    pub = nota.escalas_publicas()
    json.dumps(pub)                                  # tem de serializar sem ajuda
    assert set(pub["escalas"]) == set(nota.ORDEM)
    assert pub["escalas"]["compras"]["tipo"] == "assimetrica"
    assert pub["pesos"] == nota.PESOS
