# -*- coding: utf-8 -*-
"""Nota do comprador — as escalas da Metodologia de Performance (documento do diretor, 30/08/2026).

Módulo **PURO**: recebe valores já medidos e devolve notas. Não toca banco, Power BI nem sessão —
mesmo padrão de `cobertura.py`/`rfm.py`/`metas.py` e do `historico.agregar`. É essa pureza que
permite travar as seis escalas em teste sem Postgres nem BI, e é o que faz a nota do passado poder
ser recalculada a partir da foto.

⚠️ **A nota NUNCA é gravada.** O documento pede (item 7) "salvar a nota por data/período, e não
apenas recalculá-la com o dado atual" — e aqui o projeto faz o CONTRÁRIO de propósito, porque a
foto (`estoque_foto_item`) guarda o INGREDIENTE. Gravar o resultado congelaria a régua do dia:
corrigir uma faixa viraria degrau no gráfico, e numa tela feita para avaliar pessoas um degrau de
definição é lido como mudança de desempenho. Recalculando, a régua nova reescreve a série inteira
de forma consistente — foi o que aconteceu quando a régua oficial mudou em 01/09/2026 e a série
de 46 dias se refez sem buraco.

A ÚNICA peça que não se recalcula é a **meta de margem**: ela é decisão, não medição, e por isso
vive em `estoque_meta_margem` com competência (ver `store.metas_margem`).
"""

# ⚠️ VERSÃO DA RÉGUA. Mesma função do `historico._ROLLUP_VERSAO`, e aqui o motivo é mais sério:
# mudar uma faixa ou um peso muda a AVALIAÇÃO DE UMA PESSOA, retroativamente e em toda a série.
# O selo viaja até o rodapé da tela para que a nota de setembro possa ser explicada em dezembro.
# **Suba sempre que uma faixa, um peso ou a régua de um indicador mudar o resultado.**
#   1 → 09/2026: versão inicial (documento de 30/08 + as correções do diretor por WhatsApp:
#       estoque parado passa a ser a aba Parado sem os novos, contado em SKUs).
#   2 → 09/2026: a nota passa a SAIR mesmo com indicador faltando, renormalizada sobre o peso
#       medido (decisão do usuário: "traz a nota mesmo sem a margem; quando inserir, recalcula").
#       ⚠️ As faixas e os pesos NÃO mudaram — mudou quando existe nota, e quanto ela vale para
#       quem está incompleto (antes: nenhuma; agora: 7,94 no lugar de nada). É mudança de
#       resultado visível para uma pessoa, então o selo sobe mesmo sem escala nova.
NOTA_VERSAO = 3

# Pesos do documento (item 1). Somam 100 — travado em teste, porque um peso solto redistribui a
# nota de todo mundo sem erro nenhum.
PESOS = {
    "ruptura":   25,
    "cobertura": 20,
    "margem":    20,
    "parado":    20,
    "compras":   15,
}

# Ordem de exibição (a do documento). Também desempata o "pior indicador".
ORDEM = ("ruptura", "cobertura", "margem", "parado", "compras")

ROTULOS = {
    "ruptura":   "Ruptura",
    "cobertura": "Cobertura A+B",
    "margem":    "Margem × Meta",
    "parado":    "Estoque Parado",
    "compras":   "Compras × Meta",
}

# O que cada indicador mede, palavra por palavra — vai para o tooltip e para o rodapé da tela.
# ⚠️ Numa tela que avalia pessoas, a régua escrita não é enfeite: é o que permite alguém
# contestar o número em vez de contestar a pessoa.
REGUAS = {
    "ruptura":   "Itens zerados com giro ÷ SKUs do comprador. Ruptura REAL: conta tenha ou não "
                 "pedido em aberto. Todas as curvas.",
    "cobertura": "SKUs de curva A+B com cobertura ≥ o mínimo do ⚙ Parâmetros, sobre os que giram.",
    "margem":    "Margem realizada ÷ meta de margem do comprador na competência, em %.",
    "parado":    "SKUs na aba Estoque parado (sem venda há 15+ dias) ÷ SKUs do comprador, "
                 "descontados os novos e os recém-chegados. CONTAGEM DE SKUs, não valor.",
    "compras":   "Comprado ÷ meta do Orçamento no mês fechado. Todas as curvas, como a aba "
                 "Orçamento. Penaliza as duas pontas, e o ESTOURO ~1,4x mais que a falta: a "
                 "meta é teto da fase de calibração de estoque, não alvo.",
}

# ── As seis escalas do documento ──────────────────────────────────────────────────────────────
#
# ⚠️ **As faixas do documento são COLADAS mas não contíguas**: ele escreve "Até 5%" e depois
# "5,1% a 8%", o que deixa 5,00 < v < 5,10 sem faixa se a leitura for literal. Aqui a régua é
# CONTÍNUA (`<=` encadeado), e o valor do meio cai na faixa de BAIXO: 5,05% vale 9, não 10.
# É o lado ESTRITO de propósito — "até 5%" significa até 5,0 mesmo. Numa avaliação de pessoas,
# inflar uma nota por artefato de arredondamento é pior que o contrário, e a decisão precisa
# estar escrita porque a leitura literal do documento deixaria o indicador sem nota nenhuma.
# Na prática o caso é raro: os percentuais chegam aqui já arredondados a 1 casa.
#
# Formato: lista de (limite, nota), avaliada em ordem. Indicador "menor melhor" usa
# `valor <= limite`; "maior melhor" usa `valor >= limite`. `None` no limite = "o resto".

# A. Ruptura (peso 25%) — quanto MENOR, melhor.
ESCALA_RUPTURA = [(5, 10), (8, 9), (12, 8), (15, 7), (20, 6), (25, 4), (None, 2)]

# B. Cobertura A+B (peso 20%) — quanto MAIOR, melhor.
ESCALA_COBERTURA = [(70, 10), (65, 9), (60, 8), (55, 7), (50, 6), (None, 4)]

# C. Margem × meta (peso 20%) — atingimento da meta, quanto MAIOR, melhor.
# ⚠️ A margem NÃO se compara direto entre compradores (é o que o documento diz, e a medição de
# 07/09/2026 confirma: 16,3% × 19,6% × 24,1% são mixes diferentes, não desempenhos diferentes).
# O que entra aqui é o ATINGIMENTO — por isso o indicador exige meta cadastrada.
ESCALA_MARGEM = [(105, 10), (100, 9), (95, 8), (90, 7), (85, 6), (None, 4)]

# D. Estoque parado (peso 20%) — quanto MENOR, melhor.
# ⚠️ A escala foi escrita sobre OUTRA régua. O 48,6% do exemplo do documento é "% de SKUs com
# cobertura acima de 90 dias" (medido em 48,8% para o mesmo comprador em 07/09/2026); o diretor
# corrigiu depois para "a aba parado msm, sem contar os produtos até 20 dias / novos". Na régua
# nova o mesmo comprador dá 36,1% — ou seja, **a troca de régua vale 3 pontos (nota 3 → 6) sem
# ninguém mexer na operação**. As faixas seguem as do documento, que discriminam bem nela
# (36,1 / 28,3 / 15,9 → 6 / 8 / 10); o que mudou foi o que se mede, não como se pontua.
ESCALA_PARADO = [(20, 10), (25, 9), (30, 8), (35, 7), (40, 6), (45, 5), (None, 3)]

# E. Compras × meta (peso 15%) — ASSIMÉTRICA: estourar dói ~1,4x mais que faltar.
# Bandas encaixadas (a primeira que contém o valor vence), como o documento escreve
# ("90% a 94,9% ou 105,1% a 110%" = a banda [90,110] menos a de dentro).
#
# ⚠️ **Era simétrica até 09/2026 e estava mal calibrada.** Medido em 60 comprador-meses
# (jan/2025–ago/2026, 3 compradores): **53% dos casos caíam no piso (nota 4)** — um indicador de
# 15% do peso que era quase constante, o mesmo defeito que fez o "parado por valor" ser recusado.
#
# **Por que assimétrica** (pedido do João Victor, 09/2026: *"punir mais o estouro que a falta de
# compra"*): perguntado se o 65% do Orçamento é meta de reposição ou de enxugamento, ele
# respondeu *"nesse momento é calibrar o estoque"*. Sob uma fase de calibração a meta é um
# **TETO, não um alvo** — ficar abaixo não desfaz a política, estourar desfaz. A simetria antiga
# contradizia a fase.
#
# ⚠️ **Por que ~1,4x e não 2x, que seria a leitura literal de "punir mais".** A meta é 65% da
# venda líquida e a REPOSIÇÃO PURA (o custo do que se vendeu) medida em abr–ago/2026 é **80,6%**
# da venda. Logo o comprador só passa a CRESCER estoque a partir de **80,6 ÷ 65 = 124%** da meta;
# abaixo disso, mesmo "estourando", ele ainda está desestocando. Punir 122% como sobrecompra
# grave puniria alguém que ainda compra menos do que vendeu. Por isso a banda que contém 124%
# já custa 4 pontos (10 → 6), e o castigo pesado começa depois dela.
# ⚠️ Se o 65% mudar, **este 124% muda junto** — a assimetria está ancorada na razão entre a meta
# e a reposição, não num gosto. Remedir antes de mexer.
#
# ⚠️ **Nenhum degrau passa de 2 pontos**, e isso é requisito, não estética: o denominador é a
# venda dos últimos 30 dias, que se mexe sozinha **7,3% ao mês** na mediana (medido em 6
# fechamentos; o comprador 47 viu a meta cair 19,1% de jul para ago). Um penhasco faria a nota de
# uma pessoa virar por causa da venda dos outros. A proposta original tinha um degrau de 7 pontos
# em 0,1 p.p. — foi o que a medição derrubou.
#
# ⚠️ **Comprar ZERO é o pior caso da escala**, nunca melhor que qualquer atingimento positivo.
# A 1ª versão do pedido punha "abaixo de 90% → 5" e "acima de 110% → 0", o que fazia quem não
# comprou nada valer mais que quem estourou 1 p.p. num indicador chamado "Compras × Meta".
ESCALA_COMPRAS = [(90, 105, 10), (80, 112, 9), (70, 119, 8), (58, 128, 6), (44, 138, 4)]
ESCALA_COMPRAS_FORA = 2

MENOR_MELHOR = ("ruptura", "parado")
MAIOR_MELHOR = ("cobertura", "margem")

_ESCALAS = {
    "ruptura":   ESCALA_RUPTURA,
    "cobertura": ESCALA_COBERTURA,
    "margem":    ESCALA_MARGEM,
    "parado":    ESCALA_PARADO,
    "compras":   ESCALA_COMPRAS,
}

# Faixa de cor da NOTA FINAL. Não está no documento — ele define as notas dos indicadores, nunca
# quando a nota final é "boa". Fica aqui, nomeado, para o diretor calibrar como calibrou o
# `ideal_meta_pct`, em vez de ficar cravado no CSS.
COR_VERDE_DE = 8.0
COR_AMARELO_DE = 6.0


def _num(v):
    """Número ou None. ⚠️ `None` significa NÃO MEDIDO e é diferente de zero: zero é medição
    (ruptura 0% é um resultado ótimo), None é ausência de dado. Confundir os dois faria um
    comprador sem meta cadastrada aparecer com margem 0% e nota 4 — punido por um cadastro."""
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None          # NaN é ausência, não número


def nota_de(indicador, valor):
    """Nota 0-10 de UM indicador. `None` quando o valor não foi medido (nunca 0)."""
    v = _num(valor)
    if v is None or indicador not in _ESCALAS:
        return None
    if indicador == "compras":
        for lo, hi, nota in ESCALA_COMPRAS:
            if lo <= v <= hi:
                return nota
        return ESCALA_COMPRAS_FORA
    escala = _ESCALAS[indicador]
    maior_melhor = indicador in MAIOR_MELHOR
    for limite, nota in escala:
        if limite is None:
            return nota
        if (v >= limite) if maior_melhor else (v <= limite):
            return nota
    return escala[-1][1]


def cor_da_nota(nota):
    """verde | amarelo | vermelho — o semáforo da nota final."""
    n = _num(nota)
    if n is None:
        return None
    if n >= COR_VERDE_DE:
        return "verde"
    return "amarelo" if n >= COR_AMARELO_DE else "vermelho"


def nota_final(indicadores):
    """Fecha a nota de UM comprador.

    `indicadores`: {"ruptura": 7.1, "cobertura": 73.2, "margem": 95.9, "parado": 36.1,
                    "compras": 128.1} — valores JÁ medidos; `None` no que não foi medido.

    Devolve {nota, completa, parcial, faltando, peso_medido, cor, pior, itens, versao}, com
    `itens` na ordem do documento.

    ⚠️ **Indicador não medido não impede a nota: ela sai PARCIAL e RENORMALIZADA.** Decisão do
    usuário (09/2026) — "traz a nota mesmo sem a margem; quando inserir, recalcula".

    Renormalizar (dividir pelo peso EFETIVAMENTE medido) é o que torna isso utilizável: sem
    dividir, quem não tem meta de margem perderia 20 pontos percentuais de peso e apareceria com
    nota até 8,0 no máximo — ficaria em último por causa de um cadastro, não do trabalho. Com a
    divisão, a nota continua na escala 0-10 e comparável.

    ⚠️ **O que a renormalização NÃO resolve, e a tela precisa dizer:** a nota MUDA quando a meta
    entra, e muda para os dois lados. É o preço aceito da decisão — por isso `completa` viaja
    junto, a tela marca a nota como parcial e nomeia o indicador que falta. Sem essa marcação, a
    variação do dia do cadastro seria lida como desempenho.

    Sem NENHUM indicador medido não há o que renormalizar: `nota=None`.
    """
    itens, faltando = [], []
    for k in ORDEM:
        valor = _num((indicadores or {}).get(k))
        n = nota_de(k, valor)
        peso = PESOS[k]
        itens.append({
            "indicador": k, "rotulo": ROTULOS[k], "regua": REGUAS[k],
            "valor": valor, "nota": n, "peso": peso,
            "pontos": round(n * peso / 100.0, 4) if n is not None else None,
        })
        if n is None:
            faltando.append(k)

    completa = not faltando
    peso_medido = sum(i["peso"] for i in itens if i["nota"] is not None)
    nota = (round(sum(i["pontos"] for i in itens if i["pontos"] is not None) * 100.0 / peso_medido, 2)
            if peso_medido else None)

    # Pior indicador (item 8 do documento: "identificar automaticamente o indicador com pior
    # avaliação e apresentá-lo como prioridade"). Determinístico — um `min`, não a IA: o Agente é
    # módulo opcional e está DESLIGADO na Multpel, e prioridade que só existe com IA ligada não é
    # prioridade. Empate resolve pelo PESO (o de maior peso dói mais na nota) e depois pela ordem
    # do documento, para o resultado não depender da ordem do dicionário.
    medidos = [i for i in itens if i["nota"] is not None]
    pior = None
    if medidos:
        pior = min(medidos,
                   key=lambda i: (i["nota"], -i["peso"], ORDEM.index(i["indicador"])))["indicador"]

    return {
        "nota": nota,
        "completa": completa,
        # `parcial` é o que a tela marca. Nome próprio (e não `not completa`) porque é ele que
        # explica a variação do dia em que a meta entra: a nota mudou porque passou a medir mais
        # coisa, não porque a pessoa mudou.
        "parcial": bool(nota is not None and not completa),
        "faltando": faltando,
        "peso_medido": peso_medido,
        "cor": cor_da_nota(nota),
        "pior": pior,
        "itens": itens,
        "versao": NOTA_VERSAO,
    }


def ranking(compradores):
    """Ordena e numera, no lugar. Entra **quem tem nota**, completa ou parcial (decisão do usuário,
    09/2026). Quem não tem indicador NENHUM medido fica de fora (`posicao=None`).

    ⚠️ Um ranking que mistura nota completa e parcial compara réguas diferentes: a parcial é
    renormalizada sobre o que foi medido, então ela responde a menos coisa. É legítimo enquanto a
    tela DECLARA quais linhas são parciais — o que ela faz com o selo "parcial". Sem essa
    declaração, o 1º lugar poderia ser de quem tem menos indicador medido, e ninguém saberia.

    Empate de nota desempata pelo nome, para a ordem não variar entre requisições."""
    completos = [c for c in (compradores or []) if c.get("nota") is not None]
    completos.sort(key=lambda c: (-(c.get("nota") or 0), (c.get("nome") or "")))
    pos = {id(c): i + 1 for i, c in enumerate(completos)}
    for c in (compradores or []):
        c["posicao"] = pos.get(id(c))
    return compradores


def escalas_publicas():
    """As seis escalas em forma serializável — a tela desenha a régua a partir daqui em vez de
    reescrevê-la em JS. Duas cópias da mesma faixa divergem no primeiro ajuste."""
    return {
        "versao": NOTA_VERSAO, "pesos": PESOS, "ordem": list(ORDEM),
        "rotulos": ROTULOS, "reguas": REGUAS,
        "cor": {"verde_de": COR_VERDE_DE, "amarelo_de": COR_AMARELO_DE},
        "escalas": {
            "ruptura":   {"tipo": "menor_melhor", "faixas": ESCALA_RUPTURA},
            "cobertura": {"tipo": "maior_melhor", "faixas": ESCALA_COBERTURA},
            "margem":    {"tipo": "maior_melhor", "faixas": ESCALA_MARGEM},
            "parado":    {"tipo": "menor_melhor", "faixas": ESCALA_PARADO},
            "compras":   {"tipo": "assimetrica", "faixas": ESCALA_COMPRAS,
                          "fora": ESCALA_COMPRAS_FORA},
        },
    }
