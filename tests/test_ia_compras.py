"""Gate do Agente de IA do módulo Compras (08/2026).

O agente NÃO calcula: ele narra números que o `core` já produziu. Por isso o que estes testes
protegem não é "a resposta está certa" (isso é do modelo), e sim **o que ele recebe** — se o
contexto sair torto, a resposta sai confiante e errada, que é o pior modo de falha possível.

Três famílias:

1. **Régua.** O `core` fala em FRAÇÃO (0-1) e o prompt fala em "%". A conversão acontece uma vez,
   na fronteira. Sem ela o modelo lê 0,595 rotulado como "%" e responde "0,6% de cobertura
   ideal" — número absurdo dito com toda a calma.
2. **Ambiguidade.** Num contexto CONSOLIDADO os números que a tela mantém em abas separadas
   ficam lado a lado, e aí o modelo mistura duas réguas na mesma frase. As duas contagens de
   ruptura e as duas réguas de valor (mercadoria × NF) têm de viajar com nomes distintos e com
   o glossário que diz qual responde qual pergunta.
3. **Comercial.** O Agente é venda adicional: sem credencial o widget APARECE (é oferta), mas
   nenhuma pergunta pode sair do servidor.
"""
import os

import pytest

from estoque import ia


# ───────────────── fixtures ─────────────────

def _produto(cod, **kw):
    p = {"codprod": cod, "descricao": f"PROD {cod}", "fornecedor": "FORN X",
         "valor": 0.0, "qtdisp": 10.0, "giro_dia": 1.0, "venda_perdida": 0.0,
         "status_parado": None}
    p.update(kw)
    return p


def _ctx(**over):
    base = dict(
        produtos=[],
        cockpit={"valor_total": 1000.0, "n_total": 10, "n_com_estoque": 8,
                 "valor_parado": 100.0, "pct_capital_parado": 10.0, "n_sem_giro": 2,
                 "valor_sem_giro": 50.0, "margem_total": 22.5,
                 "parado": {"novo": {"qt": 3, "valor": 30.0}},
                 "abastecimento": {"n_repor": 5, "valor_sugerido": 900.0,
                                   "valor_sugerido_nf": 1035.0,
                                   "urgente": {"qt": 2}, "excesso": {"qt": 1},
                                   "n_suspensos": 0}},
        cobertura={"faixas": [{"faixa": "0-30", "qt": 4, "valor": 400.0}]},
        estoque_ideal={"ideal": {"n": 6, "pct": 0.595}, "em_risco": {"n": 4},
                       "meta_pct": 0.90},
        ruptura={"itens": 12, "total": 100, "perc": 0.12, "venda_perdida": 3400.0,
                 "criterio": "ESTOQUE <= 0 E GIRO MENSAL > 0"},
        ruptura_sem_pedido=5,
        orcamento={"meta": 50000.0, "comprado": 47500.0, "aberto": 1000.0,
                   "saldo": 2500.0, "pct_consumido": 0.95},
        recorte={"unidade": "atacado"},
    )
    base.update(over)
    return ia.montar_contexto(**base)


# ───────────────── 1. régua: fração × percentual ─────────────────

def test_cobertura_ideal_vira_PERCENTUAL_nao_fracao():
    """⚠️ `resumo_estoque_ideal` devolve `ideal.pct` em FRAÇÃO — a tela multiplica na exibição.
    Mandar a fração com rótulo "%" faria o modelo dizer "0,6% de cobertura ideal"."""
    pl = _ctx()["placar"]
    assert pl["pct_cobertura_ideal"] == 59.5
    assert pl["cobertura_ideal_meta_pct"] == 90.0


def test_orcamento_consumido_tambem_vira_percentual():
    """Mesma armadilha, outro campo: `pct_consumido` é fração. 0,95 rotulado como "%" viraria
    "orçamento 1% consumido" num mês que está estourando."""
    assert _ctx()["orcamento"]["consumido_pct"] == 95.0


def test_ruptura_pct_vem_em_percentual():
    assert _ctx()["ruptura"]["ruptura_pct"] == 12.0


# ───────────────── 2. ambiguidade: as palavras com duas réguas ─────────────────

def test_as_DUAS_rupturas_viajam_com_nomes_distintos():
    """⚠️ O motivo de o contexto consolidado ser mais perigoso que o por aba: a ruptura REAL e a
    ruptura SEM PROVIDÊNCIA (régua da Meta) ficam lado a lado. Com nome parecido o modelo troca
    uma pela outra e compara 12% contra uma meta de 2%."""
    r = _ctx()["ruptura"]
    assert r["ruptura_skus"] == 12
    assert r["ruptura_sem_pedido_skus"] == 5
    assert r["ruptura_sem_pedido_skus"] < r["ruptura_skus"], \
        "a sem-providência é subconjunto da real; se inverter, alguém trocou as duas"


def test_as_DUAS_reguas_de_valor_viajam_separadas():
    """Mercadoria vai para a planilha do Winthor; NF consome a meta do orçamento. Um número só
    faria o agente prometer que cabe no orçamento uma compra que não cabe."""
    c = _ctx()["compra"]
    assert c["compra_sugerida_mercadoria"] == 900.0
    assert c["compra_sugerida_nf"] == 1035.0


def test_o_glossario_explica_as_duas_reguas_no_prompt():
    """Nome distinto no payload não basta — o modelo precisa saber QUAL responde qual pergunta.
    Este é o análogo do bloco MODO FRACIONADO do prompt da DRE."""
    sp = ia.system_prompt(_ctx())
    for termo in ("ruptura_sem_pedido_skus", "compra_sugerida_nf",
                  "compra_sugerida_mercadoria", "capital_parado_valor"):
        assert termo in sp, f"{termo} tem de estar explicado no glossário"


def test_o_prompt_proibe_recalcular():
    """O agente narra números prontos. Se ele somar, deixa de bater com a tela — e o módulo
    inteiro é construído sobre bater centavo a centavo com o ERP."""
    sp = ia.system_prompt(_ctx())
    assert "NUNCA recalcule" in sp


def test_o_prompt_avisa_que_queda_de_estoque_nao_e_boa_noticia_sozinha():
    """A mesma decisão que tirou a cor do `valor_estoque` na aba Evolução: sem a ruptura ao lado,
    um desabastecimento parece eficiência — e o agente parabenizaria."""
    sp = ia.system_prompt(_ctx())
    assert "NÃO É, POR SI SÓ, BOA NOTÍCIA" in sp
    assert "desabastecimento" in sp


# ───────────────── recorte e ressalvas ─────────────────

def test_o_recorte_da_tela_viaja_e_o_prompt_manda_declarar():
    """Decisão do Gabriel: o agente responde no recorte da tela "senão fica dupla interpretação".
    Só serve se ele DISSER qual é o recorte — número de uma fatia lido como da empresa é o erro
    que a política inteira existe para evitar."""
    ctx = _ctx(recorte={"unidade": "atacado", "comprador": "MARIA"})
    sp = ia.system_prompt(ctx)
    assert "MARIA" in sp
    assert "RECORTE ATIVO" in sp
    assert "DIGA isso na primeira frase" in sp


def test_filtro_que_o_orcamento_nao_honra_vira_ressalva():
    """Mesma limitação que o `/api/resumos` já declara na tela: meta e comprado do Winthor não
    têm quebra por curva/fornecedor. Sem a ressalva o agente serve um "% da meta" que não
    corresponde ao recorte que ele acabou de declarar."""
    ctx = _ctx(orcamento_ignora=["curva", "fornecedor"])
    assert any("ORÇAMENTO" in r for r in ctx["ressalvas"])
    assert "curva" in ia.system_prompt(ctx)


def test_sem_serie_historica_o_agente_e_proibido_de_falar_em_tendencia():
    """A instância nova tem uma foto só. Sem esta ressalva o modelo infere direção de um ponto —
    e "melhorou" é exatamente o que ninguém pode dizer com uma medição."""
    ctx = _ctx(tendencia=None)
    r = " ".join(ctx["ressalvas"])
    assert "melhorou" in r and "direção" in r


def _serie(n):
    """Série sintética de `n` dias — onde o teste precisa de MATURIDADE, não de valores."""
    return [{"data": f"2026-08-{i % 28 + 1:02d}", "valor_estoque": 10.0,
             "valor_parado": 1.0, "n_ruptura": 3} for i in range(n)]


def test_com_serie_MADURA_a_tendencia_entra_e_a_ressalva_some():
    """⚠️ Antes bastava 1 ponto. Passou a exigir 28 dias (as faixas da própria aba) quando se
    viu que a produção tinha UM dia de foto e o agente falaria de tendência sobre ele."""
    ctx = _ctx(tendencia=_serie(30))
    assert ctx["maturidade_serie"] == "util"
    assert not any("POUCOS" in r or "NÃO EXISTE" in r for r in ctx["ressalvas"])
    assert "EVOLUÇÃO" in ia.system_prompt(ctx)


def test_o_contexto_declara_o_que_NAO_contem():
    """Com payload fixo o agente é analista de PLACAR, não de item. Declarar o limite é o que
    faz ele recusar em vez de inventar o giro de um produto que não está na lista."""
    ctx = _ctx()
    assert ctx["fora_do_escopo"]
    assert "FORA DO SEU ALCANCE" in ia.system_prompt(ctx)


# ───────────────── top-N ─────────────────

def test_top_ofensores_usa_a_regra_unica_de_parado():
    """`core.eh_parado` é a fonte única — item recém-chegado (`novo`) não é dead stock. Uma lista
    de "maiores ofensores" com produto que chegou ontem destrói a confiança na aba inteira."""
    produtos = [
        _produto(1, valor=500.0, status_parado="critico"),
        _produto(2, valor=900.0, status_parado="novo"),      # chegou agora: NÃO é ofensor
        _produto(3, valor=300.0, status_parado="atencao"),
    ]
    top = _ctx(produtos=produtos)["top"]["maiores_ofensores_parado"]
    assert [i["codprod"] for i in top] == [1, 3]


def test_top_rupturas_so_conta_quem_tem_giro():
    """Item zerado sem giro não é ruptura — é item morto. Misturar os dois inflaria a venda
    perdida com produto que ninguém compra."""
    produtos = [
        _produto(1, qtdisp=0, giro_dia=2.0, venda_perdida=800.0),
        _produto(2, qtdisp=0, giro_dia=0.0, venda_perdida=999.0),   # sem giro → fora
        _produto(3, qtdisp=5, giro_dia=2.0, venda_perdida=700.0),   # tem estoque → fora
    ]
    top = _ctx(produtos=produtos)["top"]["maiores_rupturas_por_venda_perdida"]
    assert [i["codprod"] for i in top] == [1]


# ───────────────── 3. gate comercial ─────────────────

def test_sem_credencial_o_agente_nao_esta_disponivel(monkeypatch):
    monkeypatch.setenv("MODULOS", "comercial,compras,ia")   # módulo ligado, faltando só a chave
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    ok, motivo = ia.disponivel()
    assert ok is False and motivo == "sem_credencial"


def test_chave_em_branco_conta_como_ausente(monkeypatch):
    monkeypatch.setenv("MODULOS", "comercial,compras,ia")
    """`OPENAI_API_KEY=` vazia no .env é o estado real de uma instância que não contratou —
    e foi o estado em que o .env do Multpel estava. Tratar como presente faria toda pergunta
    bater na API sem credencial e devolver erro seco em vez da oferta."""
    monkeypatch.setenv("OPENAI_API_KEY", "   ")
    assert ia.disponivel()[0] is False


def test_com_credencial_fica_disponivel(monkeypatch):
    monkeypatch.setenv("MODULOS", "comercial,compras,ia")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    assert ia.disponivel()[0] is True


def test_a_oferta_e_convite_e_nao_erro():
    """O widget aparece nas instâncias sem o Agente de propósito (decisão comercial): o cliente
    vê o que existe e pergunta. Texto de erro ali mataria a venda."""
    assert "não faz parte do seu plano" in ia.UPSELL["texto"]
    assert ia.UPSELL["cta"]
    for feio in ("erro", "falha", "indisponível", "403", "negado"):
        assert feio not in ia.UPSELL["texto"].lower()


# ───────────────── sugestões ─────────────────

def test_sugestao_reage_ao_estado():
    """Sugestão fixa envelhece e vira decoração; a que reage ao número é o que ensina a usar."""
    s = ia.sugestoes(_ctx())
    assert any("ruptura" in x.lower() for x in s), "há 12 itens em ruptura — tem de sugerir"
    assert any("orçamento" in x.lower() for x in s), "meta 95% consumida — tem de sugerir"


def test_sugestoes_cobrem_PILARES_diferentes():
    """⚠️ Pedido do João Victor junto com os pilares: as perguntas propostas têm de abrir as
    ÁREAS. A 1ª versão sugeria três variações de ruptura e o comprador nunca descobria que dava
    para perguntar de validade ou de fornecedor — a largura do agente ficava invisível."""
    ctx = _ctx(
        produtos=[_produto(1, valor=900.0, status_parado="critico", codfornec=7,
                           venda=100.0, lucro=30.0, giro_mes=1.0, cobertura_dias=200)],
        orcamento_compradores=[{"comprador": "MARIA", "meta": 10.0, "comprado": 5.0,
                                "saldo": 5.0, "pct_consumido": 0.5}],
        validade={"faixas": []},
        lotes_proximos=[{"cod": 1, "desc": "X", "dt_validade": "2026-09-01",
                         "dias": 12, "qt": 5, "valor": 50.0}],
    )
    s = [x.lower() for x in ia.sugestoes(ctx)]
    temas = {
        "ruptura": any("ruptura" in x or "faltar" in x for x in s),
        "parado": any("parado" in x for x in s),
        "validade": any("vence" in x for x in s),
        "fornecedor": any("fornecedor" in x for x in s),
        "comprador": any("comprador" in x for x in s),
    }
    assert sum(temas.values()) >= 4, f"sugestões concentradas demais: {temas} · {s}"
    assert len(s) <= 6


def test_sugestao_de_tendencia_so_aparece_com_serie_MADURA():
    assert not any("melhorando" in x for x in ia.sugestoes(_ctx(tendencia=None)))
    assert any("melhorando" in x for x in ia.sugestoes(_ctx(tendencia=_serie(30))))


def test_o_TETO_de_sugestoes_vale_com_TODOS_os_candidatos_ativos():
    """⚠️ Este teste era CEGO, das duas maneiras possíveis, e é o mesmo modo de falha do mock de
    metas sem `[venda_sb]` registrado no README:

    · o `n` do `parametrize` nunca era usado — a mesma asserção rodava três vezes;
    · o contexto era o `_ctx()` padrão, com `produtos=[]`, então `pilares` saía `{}` e metade dos
      candidatos a sugestão nem chegava a ser avaliada.

    Resultado: ele afirmava um teto de 5 sobre um código que corta em 6, e passava. Aqui o
    contexto ativa TODOS os candidatos de uma vez, que é a única condição em que o corte importa.
    """
    ctx = _ctx(
        produtos=[_produto(1, valor=900.0, status_parado="critico", codfornec=7,
                           venda=100.0, lucro=30.0, giro_mes=1.0, cobertura_dias=200)],
        orcamento={"meta": 100.0, "comprado": 99.0, "aberto": 1.0, "saldo": 1.0,
                   "pct_consumido": 0.99},                       # >= 90% liga a de orçamento
        orcamento_compradores=[{"comprador": "MARIA", "meta": 10.0, "comprado": 5.0,
                                "saldo": 5.0, "pct_consumido": 0.5}],
        validade={"faixas": []},
        lotes_proximos=[{"cod": 1, "desc": "X", "dt_validade": "2026-09-01",
                         "dias": 12, "qt": 5, "valor": 50.0}],
        tendencia=_serie(120),                                    # madura: liga a de direção
    )
    s = ia.sugestoes(ctx)
    assert len(s) == len(set(s)), f"sugestão repetida: {s}"
    assert len(s) <= 6, f"{len(s)} sugestões — o chat é estreito: {s}"
    # e a prova de que o contexto REALMENTE encheu a fila, senão o teto volta a não ser exercido
    assert len(s) >= 5, (f"só {len(s)} candidatos ativos — o teto não foi exercido e o teste "
                         f"voltou a ser cego: {s}")


def test_a_serie_NAO_pode_ser_comparada_com_o_placar():
    """⚠️ Achado no 1º ensaio com dado REAL da Multpel — e é a razão de esse ensaio existir.

    O modelo leu o último ponto da série como "o valor de hoje" e anunciou que o capital parado
    tinha subido de R$ 181 mil para R$ 486 mil. **Os dois números estavam certos**, em réguas
    diferentes (60 dias no placar, 15 na série).

    ⚠️ **As réguas foram unificadas em 08/2026** (`core.status_parado_de` dos dois lados), mas a
    PROIBIÇÃO continua — mudou só o motivo dela: hoje o que separa os dois é o MOMENTO (a série é
    a foto do fim do dia; o placar é a posição de agora). Este teste trava a barreira; quem trava
    a explicação contra o código é o `tests/test_ia_prompt_x_core.py`, que existe porque a
    explicação daqui ficou 
    defasada em silêncio quando a régua mudou.
    """
    ctx = _ctx(tendencia=[
        {"data": "2026-08-18", "valor_estoque": 6077944.22, "valor_parado": 436989.09, "n_ruptura": 309},
        {"data": "2026-08-19", "valor_estoque": 6241686.24, "valor_parado": 486732.51, "n_ruptura": 314},
    ])
    sp = ia.system_prompt(ctx)
    assert "nunca com o placar" in sp, "o render tem de bloquear a comparação"
    assert "como se fosse variação real" in sp, "o glossário tem de bloquear a leitura de delta"
    # o rótulo da série segue DIFERENTE do rótulo do placar: nome igual convida à comparação
    assert "parado (foto)" in sp
    assert "Capital parado: " in sp, "o placar mantém o rótulo dele"
    assert "parado-15d" not in sp, "régua velha não pode voltar ao rótulo"


def test_o_cache_do_contexto_leva_o_PAPEL_na_chave():
    """⚠️ O contexto é cacheado por 120s (montá-lo custava 8,6s e a pergunta pagava isso antes do
    primeiro token — o chat parecia travado).

    Mas a TENDÊNCIA só entra no contexto para admin, mesma guarda do `/api/evolucao`. Sem o papel
    na chave, o contexto montado para um admin seria servido a um comprador — e o chat entregaria
    por TEXTO exatamente a aba que o endpoint recusa por HTTP. Cache que vaza é pior que ausência
    de cache.
    """
    import inspect

    from estoque import routes

    fonte = inspect.getsource(routes._contexto_ia)
    corpo = fonte.split('"""')[-1]
    assert "_CACHE.get(_ck)" in corpo, "o contexto tem de ser cacheado (custa 8,6s montar)"
    chave = next(l for l in corpo.splitlines() if "_ck = " in l or '"ia:ctx' in l)
    bloco = corpo[corpo.index("_ck ="):corpo.index("_hit")]
    assert "role" in bloco, "o papel do usuário TEM de entrar na chave do cache"
    assert "_unidade()" in bloco, "unidades diferentes não podem compartilhar contexto"
    assert "request.args" in bloco, "o recorte da tela faz parte da identidade do contexto"


# ───────────────── maturidade da série (instância nova / produção com 1 dia) ─────────────────

def test_serie_com_UM_dia_nao_autoriza_falar_em_tendencia():
    """⚠️ O caso REAL de produção: a foto começou em 19/08 e havia **um** dia. O código tratava
    qualquer lista não-vazia como tendência, então o modelo diria "a série mostra que…" sobre um
    único ponto — inferir direção de uma medição é o erro mais caro possível numa aba que existe
    para PROVAR gestão.

    (Meu ensaio com dado real rodou contra o banco local, que tinha 12 dias — por isso este caso
    não apareceu lá. Levantado pelo Gabriel.)"""
    ctx = _ctx(tendencia=[{"data": "2026-08-19", "valor_estoque": 6.2e6,
                           "valor_parado": 1.8e5, "n_ruptura": 314}])
    assert ctx["maturidade_serie"] == "enchendo"
    assert ctx["dias_medidos"] == 1
    sp = ia.system_prompt(ctx)
    assert "POUCOS PONTOS" in sp
    assert "PROIBIDO dizer que algo melhorou" in sp


def test_cliente_NOVO_nao_tem_serie_e_o_agente_explica_por_que():
    """Cliente que acabou de comprar entra com ZERO histórico, e isso não é falha — estoque é
    SALDO, e o saldo de ontem não existe em lugar nenhum. O agente tem de saber explicar isso,
    senão a primeira impressão do produto é "o sistema não tem dados"."""
    ctx = _ctx(tendencia=None)
    assert ctx["maturidade_serie"] == "vazia"
    r = " ".join(ctx["ressalvas"])
    assert "NÃO EXISTE série" in r
    assert "SALDO" in r, "tem de explicar POR QUE não há histórico, não só que não há"
    assert "4 semanas" in r, "e quando passa a haver"


@pytest.mark.parametrize("n,esperado", [(0, "vazia"), (1, "enchendo"), (27, "enchendo"),
                                        (28, "util"), (89, "util"), (90, "tendencia")])
def test_faixas_de_maturidade_batem_com_as_da_aba(n, esperado):
    """As faixas são as MESMAS do `_resumo_evolucao` (28 = "já dá para ler direção", 90 = firme).
    Divergir faria o chat afirmar o que a tela ao lado diz que ainda não dá para afirmar."""
    serie = [{"data": f"2026-08-{i%28+1:02d}", "valor_estoque": 1.0,
              "valor_parado": 0.0, "n_ruptura": 0} for i in range(n)]
    assert ia.maturidade_serie(serie) == esperado


def test_com_serie_curta_nao_sugere_pergunta_sobre_direcao():
    """Sugerir "está melhorando?" com 1 dia convida a pessoa a fazer exatamente a pergunta que o
    agente é obrigado a recusar. Frustração no primeiro clique."""
    curta = ia.sugestoes(_ctx(tendencia=[{"data": "2026-08-19", "valor_estoque": 1.0,
                                          "valor_parado": 0.0, "n_ruptura": 0}]))
    assert not any("melhorando" in x for x in curta)


def test_a_serie_da_evolucao_usa_a_MESMA_regua_do_cockpit():
    """⚠️ 08/2026: a série contava parado a partir de 15 dias e o Cockpit a partir de 60 — no BI
    real, R$ 433.647 contra R$ 181.182 no mesmo dia, com o MESMO rótulo. Não eram conceitos
    diferentes: somando as faixas de 61+ a régua antiga dava R$ 181.155 (os R$ 26,79 de resto
    eram o item com exatamente 60 dias). Hoje as duas chamam `core.status_parado_de`.

    A aba Estoque parado segue em 15+ de propósito — lá o papel é mostrar o gradiente."""
    import inspect
    from datetime import date

    from estoque import core, historico

    fonte = inspect.getsource(historico.agregar)
    assert "status_parado_de" in fonte, "a série tem de usar a fonte única do Cockpit"
    # só o CÓDIGO: o comentário cita `parado_faixa_de` para explicar por que ele saiu
    corpo = fonte.split('"""')[-1].splitlines()
    codigo = [l for l in corpo if not l.strip().startswith("#")]
    assert not any("parado_faixa_de" in l for l in codigo), \
        "a régua de 15 dias não pode voltar ao capital parado da série"

    d = date(2026, 8, 19)
    # 20 dias sem vender: ROTAÇÃO num distribuidor, não dead stock
    curto = historico.agregar([(d, 10, 1000.0, 1.0, 10, date(2026, 7, 30), None, "A", 0, 0)])
    # 70 dias sem vender: dead stock nas duas telas
    longo = historico.agregar([(d, 10, 1000.0, 1.0, 10, date(2026, 6, 10), None, "A", 0, 0)])
    assert curto[0]["valor_parado"] == 0.0
    assert longo[0]["valor_parado"] == 1000.0
    assert core.status_parado_de(70, 10, None, 15) == "atencao"
    assert core.status_parado_de(20, 10, None, 15) is None


# ───────────────── orçamento: o erro achado no 1º teste no navegador ─────────────────

def test_aberto_esta_DENTRO_do_comprado_e_o_prompt_diz_isso():
    """⚠️ Achado pelo Gabriel testando no navegador, e é o pior tipo de erro que este agente pode
    cometer. Perguntado sobre os compradores, o modelo somou comprado (R$ 3.539.257,14) com
    aberto (R$ 1.764.416,83), declarou "R$ 5.303.673,97, acima da meta", concluiu que o orçamento
    estava estourado e então **inverteu o sinal do saldo** — reportou "negativo em R$ 652.823,17"
    um valor que o contexto tinha entregue como POSITIVO.

    Dois defeitos encadeados:
      1. `aberto` é SUBCONJUNTO de `comprado` (`core.orcamento_winthor`: os dois saem do mesmo
         laço sobre os pedidos do mês — `realizado += vlt` e `aberto += aberto_val`). Somar conta
         o mesmo dinheiro duas vezes;
      2. com a soma errada em mãos, o modelo reescreveu um número recebido para encaixá-lo na
         narrativa. Contradizer o dado é pior que não responder.
    """
    sp = ia.system_prompt(_ctx())
    assert "está DENTRO de `comprado_mes`" in sp
    assert "conta o mesmo dinheiro duas vezes" in sp.replace("**", "")
    assert "NUNCA some dois números do contexto" in sp
    assert "NUNCA contradiga um número que recebeu" in sp


def test_desempenho_dos_compradores_tem_bloco_proprio():
    """A pergunta "como está o desempenho dos compradores?" é das primeiras que alguém faz. Sem
    este bloco o agente respondia com o total da EMPRESA sem perceber que trocou a pergunta —
    e trocar a pergunta em silêncio é a falha clássica deste módulo."""
    ctx = _ctx(orcamento_compradores=[
        {"comprador": "MARIA", "meta": 100.0, "comprado": 80.0, "saldo": 20.0,
         "pct_consumido": 0.80},
        {"comprador": "JOAO", "meta": 50.0, "comprado": 60.0, "saldo": -10.0,
         "pct_consumido": 1.20},
    ])
    linhas = ctx["orcamento_por_comprador"]
    assert [c["comprador"] for c in linhas] == ["MARIA", "JOAO"]
    assert linhas[0]["consumido_pct"] == 80.0 and linhas[1]["consumido_pct"] == 120.0
    sp = ia.system_prompt(ctx)
    assert "ORÇAMENTO POR COMPRADOR" in sp
    assert "MARIA" in sp and "JOAO" in sp
    assert "saldo POSITIVO = ainda tem orçamento" in sp


# ───────── índice x conteúdo: o "promete e falta" ─────────

def test_pilar_vazio_NAO_e_anunciado_no_indice():
    """⚠️ Achado pelo `tests/smoke_ia_real.py` (bateria contra a API real), e é uma família de
    bug, não um caso.

    Eu tinha CHUTADO as chaves de retorno do `core.leadtime_fornecedores` (`lead_mediano` em vez
    de `lead_real`). O filtro descartava todas as linhas, o pilar saía vazio — mas o índice era
    montado a partir dos pilares que EXISTIAM no dicionário, então anunciava "leadtime". O agente
    procurava, não achava, e respondia "o painel não tem lead time" sobre uma aba que existe.

    Hoje o índice é derivado do texto REALMENTE renderizado. Chave errada degrada para "não
    anunciado" em vez de "prometido e ausente" — e essa direção é a que importa.
    """
    from estoque import ia_pilares as P

    txt = P.renderizar({
        "ocupacao": {"skus_endereçados": 3, "posicoes_ocupadas": 9, "m3_endereçado": 2.0,
                     "espaco_morto_skus": 0, "espaco_morto_valor": 0, "espaco_morto_posicoes": 0,
                     "maiores_espacos_mortos": []},
        "leadtime": {"piores": [], "melhores": []},      # veio vazio (chave errada na origem)
    })
    indice = txt.split("=== PILAR")[0]
    assert "ocupacao:" in indice, "pilar com conteúdo tem de ser anunciado"
    assert "leadtime:" not in indice, "pilar vazio NÃO pode ser anunciado"


def test_pilares_sem_conteudo_devolvem_None_na_origem():
    """Cinto e suspensório: além do índice, a própria função devolve None quando não há o que
    mostrar. Dict com listas vazias é truthy e faria o render imprimir cabeçalho oco."""
    from estoque import ia_pilares as P
    assert P.leadtime({"fornecedores": []}) is None
    assert P.leadtime(None) is None
    assert P.verbas({"fornecedores": [], "resumo": {}}) is None
    assert P.vencidos({"meses": [], "compradores": []}) is None
    assert P.ocupacao([]) is None
    assert P.validade(None, None) is None


def test_as_chaves_do_leadtime_e_das_verbas_batem_com_o_core():
    """⚠️ O gate que teria evitado o bug. As duas funções consomem retorno de OUTRO módulo, e
    chute de nome de campo não estoura — só produz vazio."""
    from estoque import ia_pilares as P

    lead = P.leadtime({"fornecedores": [
        {"fornecedor": "FORN A", "lead_real": 12.0, "n_reais": 5, "confiavel": True},
        {"fornecedor": "FORN B", "lead_real": 3.0, "n_reais": 9, "confiavel": False}]})
    assert lead["piores"][0]["fornecedor"] == "FORN A" and lead["piores"][0]["lead"] == 12.0
    assert lead["piores"][0]["pedidos"] == 5

    vb = P.verbas({"fornecedores": [{"fornecedor": "F", "negociado": 10.0, "saldo": 2.0}],
                   "resumo": {"negociado_12m": 100.0, "aplicado_12m": 40.0,
                              "saldo_aberto": 60.0, "n_fornec": 7}})
    assert vb["resumo"]["negociado"] == 100.0 and vb["resumo"]["saldo"] == 60.0


def test_o_prompt_exige_o_CODIGO_do_produto_em_toda_mencao():
    """⚠️ Pedido do diretor (08/2026): *"sempre trazer o cod do produto, pois assim conseguimos
    validar — por exemplo, essa semana chegou copo, pode ser que isso tenha implicado nessa
    avaliação; então trazer o cod permite avaliar o que ocorreu"*.

    É requisito de AUDITORIA, não de formatação. O agente cita um item como maior espaço morto;
    sem o código ninguém consegue abrir aquele produto no ERP e descobrir que chegou carga na
    semana — e um número que não se confere não vira decisão. O dado sempre esteve no contexto
    (`_item` monta `cod` em toda linha); o que faltava era a obrigação de repeti-lo na resposta.
    """
    sp = ia.system_prompt(_ctx())
    assert "SEMPRE cite o CÓDIGO do produto" in sp
    assert "auditoria" in sp.lower()


def test_toda_linha_de_item_dos_pilares_carrega_o_codigo():
    """A regra do prompt só se sustenta se o dado estiver lá: linha de ranking sem `cod` deixaria
    o modelo sem como obedecer, e ele acabaria inventando ou omitindo."""
    from estoque import ia_pilares as P

    produtos = [_produto(57417, valor=55733.93, status_parado="critico", dias_sem_venda=200,
                         cobertura_dias=9999, giro_mes=0.0, pos_end=18, m3_end=4.0,
                         espaco_morto=True, qtdisp=100, giro_dia=0.0)]
    for lista in (P.parado(produtos)["maiores"],
                  P.ocupacao(produtos)["maiores_espacos_mortos"]):
        assert lista, "a fixture deveria produzir linha"
        for linha in lista:
            assert linha.get("cod"), f"linha sem código: {linha}"
    # e o texto renderizado tem de mostrar o código junto da descrição
    txt = P.renderizar({"parado": P.parado(produtos), "ocupacao": P.ocupacao(produtos)})
    assert "57417" in txt


def test_as_sugestoes_VOLTAM_depois_de_cada_resposta():
    """Pedido do Gabriel: deixar as perguntas sugeridas no chat para facilitar a vida do usuário.

    ⚠️ Elas já existiam, mas apareciam SÓ na abertura e sumiam na primeira pergunta — justamente
    quando passam a ser mais úteis: a pessoa acabou de ver que o agente responde e é aí que ela
    quer saber o que MAIS dá para perguntar. Sem isso o chat vira uma caixa de texto em branco
    depois do primeiro uso, e o comprador não descobre que existem 14 pilares atrás dela.
    """
    from pathlib import Path

    js = Path("static/estoque/estoque-ia.js").read_text(encoding="utf-8")
    ini = js.index("async function iaEnviar(")
    corpo = js[ini:]
    assert "iaSugestoes();" in corpo, \
        "iaEnviar tem de recarregar as sugestões ao terminar a resposta"
    # e continuam saindo do servidor, que é quem sabe o estado (ruptura, orçamento, validade)
    assert "/estoque/api/ia/contexto" in js


# ───────────────── gate por MÓDULO: os três estados ─────────────────

def test_tres_estados_do_agente(monkeypatch):
    """⚠️ O gate que permite o Agente viajar na MESMA imagem sem aparecer para quem não contratou.

    Antes só havia dois estados, decididos pela presença da chave — e isso não serve quando a
    mesma imagem serve produção e demo: a Multpel, que já paga o painel, veria um upsell que não
    pediu só porque a chave dela não existe. Separando MÓDULO (o que a empresa contratou) de
    CREDENCIAL (o que está ligado), a instância escolhe entre não ter, ter como oferta, ou ter
    ativo. É o mesmo princípio do `MODULOS=comercial,compras` que já governa o `/estoque`.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    monkeypatch.setenv("MODULOS", "comercial,compras")
    assert ia.estado() == "off", "sem `ia` no MODULOS o Agente não existe, mesmo com chave"
    assert ia.disponivel() == (False, "modulo_desligado")

    monkeypatch.setenv("MODULOS", "comercial,compras,ia")
    monkeypatch.setenv("OPENAI_API_KEY", "   ")
    assert ia.estado() == "oferta", "módulo ligado sem chave = mostra a oferta"

    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    assert ia.estado() == "ativo"
    assert ia.disponivel()[0] is True


def test_modulo_le_a_env_e_nao_importa_o_server(monkeypatch):
    """⚠️ `from server import MODULOS` traria uma SEGUNDA cópia do módulo: em produção o container
    roda `python server.py`, então `server` é `__main__`. A armadilha já está documentada duas
    vezes no `routes.py` — duplicar a leitura da env é o preço de não duplicar o módulo."""
    import inspect
    fonte = inspect.getsource(ia.modulo_ligado)
    # só o CÓDIGO: o docstring cita `from server import` justamente para explicar a armadilha
    corpo = fonte.split('"""')[-1]
    assert "from server import" not in corpo
    assert 'os.getenv("MODULOS"' in corpo
    monkeypatch.setenv("MODULOS", " comercial , ia , compras ")
    assert ia.modulo_ligado() is True, "tem de tolerar espaços na env"


def test_o_import_do_agente_nao_pode_derrubar_o_boot():
    """⚠️ Código na imagem é código que pode quebrar a imagem. Sem o `try`, um erro de import em
    `ia.py` derrubaria o BOOT do app inteiro — Comercial junto, e na instância da Multpel, que
    nem tem o recurso ligado. Este é o risco que a decisão de commitar o Agente introduz, e o
    `try` é o que o paga."""
    import inspect

    from estoque import routes

    fonte = inspect.getsource(routes)
    ini = fonte.index("try:\n    from . import ia\n")
    bloco = fonte[ini:ini + 400]
    assert "except Exception" in bloco, "o import do Agente tem de ser tolerante a falha"
    assert "ia = iac = None" in bloco
    assert callable(routes._ia_off)


def test_o_widget_some_quando_o_modulo_esta_desligado():
    """O botão não pode nem aparecer na instância que não tem o Agente. `modulo` e `disponivel`
    são coisas diferentes: o primeiro decide se o widget EXISTE, o segundo se ele conversa."""
    from pathlib import Path
    js = Path("static/estoque/estoque-ia.js").read_text(encoding="utf-8")
    assert "if (!IA.modulo) { fab.hidden = true; return; }" in js
    assert "IA.modulo = !!j.modulo;" in js
