"""Agente de IA do módulo Compras — o motor do contexto e do prompt.

**A decisão de desenho (08/2026, Gabriel):** contexto CONSOLIDADO, não por aba. O módulo tem 22
abas, mas as perguntas reais do comprador atravessam quase todas — "por que o capital parado
subiu?" mistura Estoque parado, Cockpit e Fornecedores; "o que eu compro hoje?" mistura
Abastecimento, Orçamento, Ruptura e Lead time. Um agente por aba responderia "não tenho essa
informação" o tempo todo, e isso ensina a pessoa a parar de perguntar.

**De onde vem o contexto.** Do SERVIDOR, não da tela. No chat da DRE (Tabela Auditoria, que é o
modelo desta feature) dava para ler `window.dreData` porque a DRE é UMA tela. Aqui as abas carregam
sob demanda — `S.evo` só existe se a pessoa abriu a Evolução, o `/api/fornecedores_extra` idem —
então montar do front daria um contexto que depende de quais abas ela visitou. Imprevisível.

⚠️ Isso custa duas propriedades boas que o desenho da DRE ganhava de graça, e as duas são
recuperadas de propósito:

1. **RBAC/recorte herdado.** O endpoint recebe a MESMA querystring da tela e monta em cima do
   `_build_produtos()` + `_aplicar_filtros_cliente()`. O recorte ativo viaja no contexto e o
   agente é obrigado a declará-lo — foi a escolha do Gabriel ("com recorte da tela acho mais
   seguro, senão fica dupla interpretação").
2. **Não divergir da tela.** Tudo aqui vem de `core.cockpit`, `core.resumo_*` e
   `historico.serie` — as MESMAS funções que as abas usam. Recalcular qualquer coisa aqui faria
   do agente a quarta implementação de ruptura do módulo, que é a ferida recorrente daqui.

**O agente NÃO calcula.** Ele narra números prontos. É por isso que um modelo pequeno resolve.
"""

import hashlib
import os
from datetime import date

from . import core
from . import ia_pilares as P

# ── disponibilidade ────────────────────────────────────────────────────────────────────────
# O Agente de IA é VENDA ADICIONAL: a instância que não contratou não recebe a chave, e o chat
# aparece assim mesmo, com o convite. Mostrar o recurso desligado é deliberado (decisão comercial
# do Gabriel) — o cliente vê o que existe e pergunta. Esconder não vende nada.
MODELO = os.getenv("IA_MODELO", "gpt-4.1-mini")
MAX_TOKENS = int(os.getenv("IA_MAX_TOKENS", "800"))

# ── timeout: a guarda que protege o APP INTEIRO, não o chat ────────────────────────────────
# ⚠️ Os defaults do SDK da OpenAI são `read=600s` e `max_retries=2` — pior caso de **1800s (30
# min)** com um worker preso. O Waitress serve com `threads=8`, então OITO perguntas travadas
# derrubam o painel todo, Comercial junto: quem nunca abriu o chat também para. Não é risco do
# recurso, é risco da instância — e por isso o timeout é obrigatório, não afinação.
#
# O `read` do httpx conta o silêncio ENTRE CHUNKS, não a duração total: resposta longa que segue
# streamando não é cortada; conexão morta é. É a semântica certa para SSE, e está provada em
# `tests/test_ia_timeout.py` contra um socket que aceita e nunca responde.
IA_TIMEOUT = float(os.getenv("IA_TIMEOUT", "60"))       # silêncio máximo entre chunks
IA_MAX_RETRIES = int(os.getenv("IA_MAX_RETRIES", "1"))  # pior caso = TIMEOUT x (1 + retries)

UPSELL = {
    "titulo": "Agente de IA especialista",
    "texto": ("Este painel pode ter um analista de IA lendo os seus números em tempo real — "
              "pronto para dizer onde está o capital parado, o que comprar primeiro e se o "
              "estoque está melhorando ou só encolhendo.\n\n"
              "**O Agente de IA não faz parte do seu plano atual.**"),
    "cta": "Fale com o administrador da sua conta para ativar.",
}


# Teto de uso por pessoa/dia. O Agente e VENDIDO por assinatura: sem teto, um laco no navegador
# (ou alguem "testando") vira custo aberto na conta do cliente. O numero e generoso de proposito —
# nao existe para racionar, existe para que um acidente nao vire fatura.
LIMITE_DIA = int(os.getenv("IA_LIMITE_DIA", "200"))


# ── rastro auditável: o que o modelo REALMENTE viu ─────────────────────────────────────────
# ⚠️ **A ironia que originou isto.** Este módulo inteiro existe porque vocês entenderam que
# `PCEST` é POSIÇÃO: o saldo de ontem foi sobrescrito e não se reconstrói (é a razão da aba
# Evolução e da foto diária). O log do Agente cometia exatamente o erro oposto: gravava a
# pergunta e o TAMANHO da resposta, e assumia que o contexto daquele momento poderia ser
# reconstruído depois pelo `/api/ia/contexto`. Não pode — o snapshot mudou, o BI atualizou 7x no
# dia, o recorte era outro, e o cache do contexto dura 120 segundos.
#
# Resultado prático: quando o diretor dissesse "ele me falou uma coisa errada ontem", havia a
# pergunta e mais nada. Sem a resposta e sem o contexto, não há investigação possível — só
# opinião. Num recurso VENDIDO por assinatura, é a diferença entre "ele errou" e "ele errou por
# isso, e aqui está a correção".
#
# O contexto é gravado DEDUPLICADO por impressão digital: numa conversa a pessoa faz 3-4
# perguntas dentro da mesma janela de cache, e todas apontam para a mesma linha. O volume passa
# a ser função dos recortes distintos do dia, não do número de perguntas.
DDL_LOG = """
CREATE TABLE IF NOT EXISTS estoque_ia_contexto (
    ctx_hash   TEXT PRIMARY KEY,
    criado_em  TIMESTAMPTZ DEFAULT now(),
    unidade    TEXT,
    recorte    TEXT,
    modelo     TEXT,
    prompt     TEXT
);
CREATE INDEX IF NOT EXISTS ix_ia_contexto_data ON estoque_ia_contexto (criado_em DESC);
"""

# Expurgo: 90 dias. Investigar resposta ruim é atividade de dias/semanas, não de trimestres — e
# o `multpel_log` (que aponta para cá) já expurga em 12 meses. Prompt guardado para sempre vira
# tabela grande sem dono.
DIAS_RETENCAO_CTX = int(os.getenv("IA_RETENCAO_CTX_DIAS", "90"))


def impressao(prompt_txt):
    """Identidade do que o modelo viu. Inclui REGRAS e GLOSSÁRIO de propósito: mudar o PROMPT
    muda a impressão, então uma resposta ruim de ontem não é lida sob as regras de hoje."""
    return hashlib.sha256((prompt_txt or "").encode("utf-8")).hexdigest()[:16]


def modulo_ligado():
    """O Agente está habilitado nesta instância? (`MODULOS=comercial,compras,ia`)

    ⚠️ Lê a env DIRETO, e não `from server import MODULOS`: em produção o container roda
    `python server.py`, então `server` é `__main__` e o import traria uma SEGUNDA cópia do
    módulo — armadilha já documentada duas vezes no `routes.py`. Duplicar a lista aqui é o preço
    de não duplicar o módulo inteiro."""
    mods = [m.strip() for m in os.getenv("MODULOS", "comercial,compras").split(",") if m.strip()]
    return "ia" in mods


def estado():
    """`off` | `oferta` | `ativo` — os TRÊS estados do Agente.

    A 1ª versão tinha só dois, decididos pela presença da chave: com chave, chat; sem chave,
    oferta. Isso não serve quando o mesmo código roda em instâncias diferentes — a Multpel já
    paga o painel e veria um upsell que não pediu, só porque a chave dela não existe.

    Separando módulo (o que a EMPRESA contratou) de credencial (o que está ligado), dá para
    escolher POR INSTÂNCIA:
      · `off`    — `ia` fora do MODULOS. O widget nem aparece. É a Multpel hoje.
      · `oferta` — módulo ligado, sem chave. Mostra o que o Agente faria. Para instigar quem
                   ainda não contratou; a decisão comercial de deixar o recurso VISÍVEL.
      · `ativo`  — módulo ligado e com chave. A demo, e quem contratar.
    """
    if not modulo_ligado():
        return "off"
    if not (os.getenv("OPENAI_API_KEY") or "").strip():
        return "oferta"
    return "ativo"


def disponivel():
    """(bool, motivo) — o Agente responde perguntas nesta instância?

    Só no estado `ativo`. Mantida com esta assinatura porque é o que os endpoints e os gates já
    consomem; quem precisa distinguir `off` de `oferta` chama `estado()`."""
    e = estado()
    if e == "ativo":
        return True, None
    return False, ("modulo_desligado" if e == "off" else "sem_credencial")


# ── vocabulário: as palavras que têm MAIS DE UMA régua neste módulo ────────────────────────
# ⚠️ Este bloco é o coração do prompt e o motivo de ele existir. Num contexto consolidado os
# números que a tela mantém em abas separadas passam a ficar LADO A LADO — e aí o modelo mistura
# duas réguas na mesma frase, com toda a confiança do mundo. Cada número do payload tem nome
# autoexplicativo e este glossário diz qual responde qual pergunta.
GLOSSARIO = """\
--- COMO LER OS NÚMEROS (leia antes de responder) ---

RUPTURA — existem DUAS contagens neste painel e elas NÃO são a mesma coisa:
  · `ruptura_skus`  = itens com estoque disponível <= 0 E giro > 0. É a ruptura REAL: o que falta
    de fato na prateleira. É esta que o placar e a evolução usam.
  · `ruptura_sem_pedido_skus` = os que, além disso, não têm NENHUMA providência (nem pedido em
    aberto, nem mercadoria em pré-entrada). É risco de OMISSÃO, e é a régua da aba "Meta de
    ruptura". É sempre MENOR que a primeira.
  Nunca compare uma com a meta da outra. Se a pergunta for sobre meta, use a segunda.

VALOR DE COMPRA — existem DUAS réguas e a diferença é imposto:
  · `compra_sugerida_mercadoria` = só a mercadoria. É o preço que vai na planilha de importação
    do Winthor.
  · `compra_sugerida_nf` = mercadoria + IPI + ST. É o que a nota vai custar e o que CONSOME a
    meta do orçamento.
  Ao falar de "quanto vou gastar", use a régua da NF. Ao falar de preço de pedido, a mercadoria.

ORÇAMENTO — ⚠️ `aberto_mes` está DENTRO de `comprado_mes`, não ao lado dele. Os dois saem do
  mesmo conjunto de pedidos do mês: `comprado_mes` é tudo que foi pedido, `aberto_mes` é a parte
  desses pedidos que ainda não chegou. **Somar os dois conta o mesmo dinheiro duas vezes.**
  · `saldo` = `meta_mes` − `comprado_mes`, e vem PRONTO. Positivo = sobra orçamento.
    Negativo = estourou. Não decida isso por conta própria: leia o sinal do saldo.
  · `consumido_pct` já é `comprado_mes ÷ meta_mes`. Se ele está abaixo de 100%, o orçamento NÃO
    estourou, ponto final.

CAPITAL PARADO — `capital_parado_valor` já EXCLUI os itens recém-chegados que nunca venderam
  (faixa "novo"). Item novo sem venda não é dead stock; ele aparece separado em `novos_qt`.

COBERTURA — itens com giro <= 0 não têm cobertura calculável. Eles ficam FORA do
  `pct_cobertura_ideal` (que mede só quem gira) e aparecem à parte em `sem_giro_qt`. Não os trate
  como "cobertura zero".

SÉRIE DE EVOLUÇÃO x PLACAR — MESMA RÉGUA, MOMENTOS DIFERENTES. Os dois contam como parado o
  item com estoque e sem venda há **60 dias ou mais**. O que os separa é QUANDO cada um mede:
  · o PLACAR é a posição de AGORA, refeita toda vez que a tela carrega;
  · a SÉRIE é uma FOTO diária, tirada no FIM do dia, depois do último refresh. O ponto mais
    recente é o fechamento de um dia que já terminou, não a posição deste instante.
  Por isso os dois podem não bater sem que nada tenha acontecido na operação — é defasagem de
  horário, não movimento. Use a série SÓ para direção (subiu/desceu/estável ao longo dos dias),
  comparando ponto com ponto DENTRO dela. NUNCA apresente a diferença entre um ponto da série e
  o placar como se fosse variação real, e nunca diga que algo "subiu para" o valor da série.

ESTOQUE CAINDO NÃO É, POR SI SÓ, BOA NOTÍCIA. Queda de estoque com ruptura subindo é
  desabastecimento, não gestão. Sempre olhe `ruptura_skus` antes de elogiar uma redução de
  estoque ou de capital parado.
"""

REGRAS = """\
Você é o Analista de Compras do painel JOGA — trabalha para o comprador e para o diretor de uma
distribuidora/atacado. Você lê a POSIÇÃO DE ESTOQUE e as compras.

REGRAS INVIOLÁVEIS:
1. Use APENAS os números fornecidos abaixo — todos JÁ CALCULADOS pelo sistema. NUNCA recalcule,
   nunca some, nunca estime. Se um número não está no contexto, ele não existe para você.
1b. NUNCA some dois números do contexto para criar um terceiro. Você não sabe se um está contido
   no outro — e frequentemente está. Se a conta que você quer fazer não veio pronta, diga que o
   painel não fornece esse total.
1c. NUNCA contradiga um número que recebeu. Se o contexto diz que o saldo é POSITIVO, sobra
   orçamento — mesmo que você ache que deveria ser diferente. Inverter o sinal de um valor para
   encaixá-lo numa conclusão é o pior erro que você pode cometer aqui.
2. Não invente. Se a pergunta pedir algo que não foi enviado, diga o que você tem e o que falta.
3. Máximo 3 parágrafos curtos. Quem lê é comprador em dia de trabalho.
4. Markdown SIMPLES: **negrito** nos números e nas conclusões que exigem ação, e listas com "- ".
   NÃO use tabelas, títulos (#) nem blocos de código — a janela do chat é estreita e só renderiza
   negrito, itálico, `código` e lista.
5. Valores em R$ no formato brasileiro. Percentuais com uma casa.
5b. ⚠️ **SEMPRE cite o CÓDIGO do produto junto do nome, em TODA menção a item** — "57417 COPO
   PLASTICO...", nunca só "o copo plástico". Vale para fornecedor também (código + nome).
   O motivo é AUDITORIA, não estética: sem o código a pessoa não consegue conferir o item no
   ERP, e um número sem conferência não vira decisão. Exemplo real do diretor: um item apareceu
   como maior espaço morto, mas tinha chegado carga naquela semana — só com o código dá para ir
   ver o que aconteceu. Se um item aparece na lista sem código, diga isso em vez de omitir.
6. Tom direto e prático, sem rodeios e sem elogio vazio. Aponte o próximo passo concreto.
7. Se o número indicar problema (ruptura na curva A, orçamento estourado, parado crescendo),
   DESTAQUE.
8. Pergunta fora de estoque/compras → "Só consigo analisar o estoque e as compras deste painel."

REGRA DO RECORTE (importante):
- Os números abaixo respeitam EXATAMENTE os filtros que a pessoa tem na tela agora. Eles estão
  descritos em "RECORTE ATIVO".
- Se houver qualquer filtro ativo, DIGA isso na primeira frase da resposta (ex.: "no recorte do
  comprador X..."). Sem isso a pessoa lê número de uma fatia como se fosse da empresa.
- Alguns blocos NÃO conseguem honrar todos os filtros — quando isso acontece vem escrito em
  "RESSALVAS". Respeite e avise.

LIMITE DO QUE VOCÊ ENXERGA — E O QUE FAZER COM ELE:
- Você recebe o PLACAR e os PILARES (rankings de até 10 itens por tema: estoque parado,
  cobertura, ruptura, fornecedores, compradores, validade). Use SEMPRE o pilar do assunto antes
  de dizer que não tem o dado — a resposta específica quase sempre está num deles.
- ⚠️ Quando a pergunta pedir um recorte que os pilares não trazem (um fornecedor específico, um
  departamento, uma faixa que não está listada), NÃO encerre com "não tenho". Diga que os seus
  números respeitam os filtros da tela e peça para a pessoa aplicar aquele filtro no topo do
  painel e refazer a pergunta — aí você responde com o recorte dela. Isso é um caminho, e é o
  que diferencia um assistente de um muro.
- Só recuse de verdade quando o assunto estiver fora de estoque/compras.
- Nunca INFIRA um total a partir de uma lista de tops (ex.: concluir qual fornecedor tem mais
  estoque olhando quantas vezes ele aparece numa lista de itens). Se o total existe, ele está
  no pilar; se não está, diga que não está.
"""


# ── maturidade da série histórica ──────────────────────────────────────────────────────────
# ⚠️ O agente NÃO pode inferir direção de uma medição. Em produção a foto começou em 19/08 —
# **um dia** —, e um cliente novo entra com ZERO. O código tratava qualquer lista não-vazia como
# tendência, então com um ponto o modelo diria "a série mostra que…" sobre nada.
# As faixas são as MESMAS da aba (`_resumo_evolucao`): <28 enchendo, 28+ útil, 90+ tendência.
# Divergir delas faria o chat afirmar o que a tela ao lado diz que ainda não dá para afirmar.
MIN_DIRECAO = 28          # 4 semanas — o que a própria aba chama de "já dá para ler direção"
MIN_TENDENCIA = 90        # tendência firme


def maturidade_serie(tendencia):
    """'vazia' | 'enchendo' | 'util' | 'tendencia' — quanto a série já autoriza dizer."""
    n = len(tendencia or [])
    if n == 0:
        return "vazia"
    if n >= MIN_TENDENCIA:
        return "tendencia"
    return "util" if n >= MIN_DIRECAO else "enchendo"


# ── contexto ───────────────────────────────────────────────────────────────────────────────
def _pct(fracao):
    """Fração (0-1) do `core` → percentual para o prompt. O `core` fala em fração e a TELA
    multiplica na exibição; aqui a conversão acontece uma vez, na fronteira. Sem isso o rótulo
    diria "%" sobre um 0,595 e o modelo repetiria "0,6% de cobertura ideal" com toda a calma."""
    return None if fracao is None else core._round(core._n(fracao) * 100, 1)


def _topn(produtos, chave, n=8, filtro=None, campos=("codprod", "descricao", "fornecedor")):
    """Os N maiores por `chave`. Lista curta de propósito: o payload não é o catálogo, e lista
    longa sem hierarquia dilui a resposta em vez de enriquecê-la."""
    itens = [p for p in produtos if (filtro(p) if filtro else True)]
    itens.sort(key=lambda p: core._n(p.get(chave)), reverse=True)
    out = []
    for p in itens[:n]:
        linha = {c: p.get(c) for c in campos}
        linha[chave] = core._round(core._n(p.get(chave)))
        out.append(linha)
    return out


def montar_contexto(*, produtos, cockpit, cobertura, estoque_ideal, ruptura, orcamento,
                    orcamento_ignora=None, validade=None, tendencia=None, recorte=None,
                    hoje=None, ruptura_sem_pedido=None, orcamento_compradores=None,
                    lotes_proximos=None, params=None, qualidade_cadastro=None,
                    vencidos_res=None, leadtime_res=None, verbas_res=None):
    """O payload consolidado. Função PURA: recebe o que as abas já calcularam e organiza.

    Nada é calculado aqui além de contagens triviais sobre listas já prontas — se um número
    precisar de regra, ele vem do `core`."""
    hoje = hoje or date.today()
    rec = dict(recorte or {})
    cp = cockpit or {}
    ei = estoque_ideal or {}
    abast = cp.get("abastecimento") or {}
    par = cp.get("parado") or {}
    orc = orcamento or {}

    ctx = {
        "gerado_em": hoje.isoformat(),
        "recorte": rec,
        "ressalvas": [],
        "placar": {
            "estoque_valor": cp.get("valor_total"),
            "skus_total": cp.get("n_total"),
            "skus_com_estoque": cp.get("n_com_estoque"),
            "capital_parado_valor": cp.get("valor_parado"),
            "capital_parado_pct": cp.get("pct_capital_parado"),
            "novos_qt": (par.get("novo") or {}).get("qt"),
            "sem_giro_qt": cp.get("n_sem_giro"),
            "sem_giro_valor": cp.get("valor_sem_giro"),
            # ⚠️ `resumo_estoque_ideal` devolve FRAÇÃO (0-1) em `ideal.pct` e `meta_pct`; a tela
            # multiplica na hora de exibir. Aqui converte-se uma vez só, porque o prompt fala em
            # "%" — mandar 0,595 rotulado como "%" faria o modelo dizer "0,6% de cobertura ideal".
            "pct_cobertura_ideal": _pct((ei.get("ideal") or {}).get("pct")),
            "cobertura_ideal_meta_pct": _pct(ei.get("meta_pct")),
            "em_risco_qt": (ei.get("em_risco") or {}).get("n"),
            "margem_pct": cp.get("margem_total"),
        },
        "ruptura": {
            "ruptura_skus": (ruptura or {}).get("itens"),
            "universo_skus": (ruptura or {}).get("total"),
            "ruptura_pct": core._round(core._n((ruptura or {}).get("perc")) * 100, 1),
            "venda_perdida_mes": (ruptura or {}).get("venda_perdida"),
            "ruptura_sem_pedido_skus": ruptura_sem_pedido,
            "criterio": (ruptura or {}).get("criterio"),
        },
        "compra": {
            "itens_a_repor": abast.get("n_repor"),
            "compra_sugerida_mercadoria": abast.get("valor_sugerido"),
            "compra_sugerida_nf": abast.get("valor_sugerido_nf"),
            "urgentes_qt": (abast.get("urgente") or {}).get("qt"),
            "excesso_qt": (abast.get("excesso") or {}).get("qt"),
            "suspensos_qt": abast.get("n_suspensos"),
        },
        "orcamento": {
            "meta_mes": orc.get("meta"),
            "comprado_mes": orc.get("comprado"),
            "aberto_mes": orc.get("aberto"),
            "saldo": orc.get("saldo"),
            "consumido_pct": _pct(orc.get("pct_consumido")),   # também vem em fração
        },
        # ⚠️ "como está o desempenho dos compradores?" é das primeiras perguntas que alguém faz,
        # e sem este bloco o agente respondia com o total da EMPRESA sem perceber a troca.
        # Achado no 1º teste do Gabriel no navegador.
        "orcamento_por_comprador": [
            {"comprador": c.get("comprador"), "meta": c.get("meta"),
             "comprado": c.get("comprado"), "saldo": c.get("saldo"),
             "consumido_pct": _pct(c.get("pct_consumido"))}
            for c in (orcamento_compradores or [])[:12]],
        "cobertura_faixas": (cobertura or {}).get("faixas") or cobertura,
        "top": {
            "maiores_ofensores_parado": _topn(produtos, "valor", filtro=core.eh_parado),
            "maiores_rupturas_por_venda_perdida": _topn(
                produtos, "venda_perdida",
                filtro=lambda p: core._n(p.get("qtdisp")) <= 0 and core._n(p.get("giro_dia")) > 0),
        },
        # PILARES (ver `ia_pilares.py`): os rankings que respondem pergunta específica. Antes
        # daqui o agente recusava 8 de 9 perguntas reais — honesto e inútil.
        "pilares": {
            "parado": P.parado(produtos),
            "cobertura": P.cobertura(produtos),
            "ruptura": P.ruptura(produtos),
            "fornecedores": P.fornecedores(produtos),
            "compradores": P.compradores(produtos),
            "validade": P.validade(validade, lotes_proximos),
            # 2º ciclo — auditoria das 22 abas (ver `ia_pilares`). Os cinco primeiros saem de
            # graça do próprio `produtos`; os quatro últimos chegam prontos das rotas.
            "meta_ruptura": P.meta_ruptura(produtos, params),
            "abc_xyz": P.abc_xyz(produtos),
            "ocupacao": P.ocupacao(produtos),
            "desempenho": P.desempenho(produtos),
            "qualidade": P.qualidade(produtos, qualidade_cadastro),
            "vencidos": P.vencidos(vencidos_res),
            "leadtime": P.leadtime(leadtime_res),
            "verbas": P.verbas(verbas_res),
        } if produtos else {},
        "tendencia": tendencia,
        "fora_do_escopo": [
            "itens fora dos rankings dos pilares (o painel tem o catálogo completo; aqui só os tops)",
            "histórico de preço de venda e pesquisa de concorrente",
            "dados de outras unidades além da que está selecionada",
        ],
    }

    if validade:
        ctx["validade"] = validade
    if orcamento_ignora:
        ctx["ressalvas"].append(
            "O bloco ORÇAMENTO não consegue honrar o filtro de "
            + ", ".join(orcamento_ignora)
            + ": meta e comprado do Winthor não têm quebra por esses eixos. Trate o orçamento "
              "como do comprador inteiro e avise a pessoa.")
    mat = maturidade_serie(tendencia)
    ctx["maturidade_serie"] = mat
    ctx["dias_medidos"] = len(tendencia or [])
    if mat == "vazia":
        ctx["ressalvas"].append(
            "NÃO EXISTE série histórica nesta instância. O estoque é um SALDO: a posição de ontem "
            "não fica guardada em lugar nenhum, então o histórico só começa no dia em que a "
            "medição liga — não é falha nem atraso. Se perguntarem se algo melhorou, piorou ou "
            "está evoluindo, responda que a medição diária começou agora e que a leitura de "
            "direção fica disponível em cerca de 4 semanas. Fale só da posição de hoje.")
    elif mat == "enchendo":
        ctx["ressalvas"].append(
            f"A série tem apenas {len(tendencia)} dia(s) medido(s) — é MEDIÇÃO REAL, mas é POUCO "
            f"para direção. PROIBIDO dizer que algo melhorou, piorou, está subindo, caindo ou "
            f"tem tendência. Você pode citar os valores medidos e dizer há quantos dias a "
            f"medição começou, informando que a leitura de direção fica confiável a partir de "
            f"{MIN_DIRECAO} dias.")
    return ctx


# ── render ─────────────────────────────────────────────────────────────────────────────────
def _brl(v):
    if v is None:
        return "—"
    try:
        return "R$ " + f"{float(v):,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")
    except (TypeError, ValueError):
        return str(v)


def _num(v, suf=""):
    if v is None:
        return "—"
    try:
        f = float(v)
        s = f"{int(f):,}".replace(",", ".") if f == int(f) else f"{f:.1f}".replace(".", ",")
        return s + suf
    except (TypeError, ValueError):
        return str(v)


_ROTULOS = {
    "estoque_valor": ("Valor em estoque", _brl),
    "capital_parado_valor": ("Capital parado", _brl),
    "capital_parado_pct": ("Capital parado (%)", lambda v: _num(v, "%")),
    "skus_total": ("SKUs no universo", _num),
    "skus_com_estoque": ("SKUs com estoque", _num),
    "novos_qt": ("Itens novos (nunca venderam, chegada recente)", _num),
    "sem_giro_qt": ("Itens sem giro", _num),
    "sem_giro_valor": ("Valor sem giro", _brl),
    "pct_cobertura_ideal": ("Cobertura ideal atingida (%)", lambda v: _num(v, "%")),
    "cobertura_ideal_meta_pct": ("Meta de cobertura ideal (%)", lambda v: _num(v, "%")),
    "em_risco_qt": ("SKUs abaixo da cobertura ideal", _num),
    "margem_pct": ("Margem (%)", lambda v: _num(v, "%")),
    "ruptura_skus": ("Itens em ruptura (real)", _num),
    "universo_skus": ("Universo medido", _num),
    "ruptura_pct": ("Ruptura (%)", lambda v: _num(v, "%")),
    "venda_perdida_mes": ("Venda perdida estimada/mês", _brl),
    "ruptura_sem_pedido_skus": ("Ruptura SEM providência (régua da Meta)", _num),
    "itens_a_repor": ("Itens com sugestão de compra", _num),
    "compra_sugerida_mercadoria": ("Compra sugerida (mercadoria)", _brl),
    "compra_sugerida_nf": ("Compra sugerida (régua da NF, c/ impostos)", _brl),
    "urgentes_qt": ("Itens urgentes", _num),
    "excesso_qt": ("Itens em excesso", _num),
    "suspensos_qt": ("Itens com compra suspensa", _num),
    "meta_mes": ("Meta de compra do mês", _brl),
    "comprado_mes": ("Comprado no mês (Winthor)", _brl),
    "aberto_mes": ("Pedidos em aberto do mês", _brl),
    "saldo": ("Saldo da meta", _brl),
    "consumido_pct": ("Meta consumida (%)", lambda v: _num(v, "%")),
}


def _bloco(titulo, dados):
    linhas = [f"--- {titulo} ---"]
    for k, v in (dados or {}).items():
        if v is None or k == "criterio":
            continue
        rot, fmt = _ROTULOS.get(k, (k, _num))
        linhas.append(f"{rot}: {fmt(v)}")
    return "\n".join(linhas) if len(linhas) > 1 else ""


def renderizar_contexto(ctx):
    """O contexto vira TEXTO rotulado. Rótulo em português e unidade explícita porque o modelo
    lê melhor um rótulo do que um nome de campo — e porque é o texto que aparece no debug quando
    alguém for entender por que ele respondeu o que respondeu."""
    p = []
    rec = ctx.get("recorte") or {}
    ativos = [f"{k}={v}" for k, v in rec.items() if v not in (None, "", [], "TODOS")]
    p.append("--- RECORTE ATIVO ---")
    p.append("Unidade/filtros: " + (", ".join(ativos) if ativos else
                                    "nenhum filtro — números da unidade inteira"))
    p.append(f"Posição de: {ctx.get('gerado_em')}\n")

    for titulo, chave in (("PLACAR DO ESTOQUE", "placar"), ("RUPTURA", "ruptura"),
                          ("COMPRA SUGERIDA", "compra"), ("ORÇAMENTO DO MÊS", "orcamento")):
        b = _bloco(titulo, ctx.get(chave))
        if b:
            p.append(b + "\n")

    faixas = ctx.get("cobertura_faixas")
    if faixas:
        p.append("--- CAPITAL POR FAIXA DE COBERTURA (dias de estoque) ---")
        for f in (faixas if isinstance(faixas, list) else []):
            # ⚠️ duas fontes, dois nomes para a MESMA contagem: `resumo_cobertura` chama de
            # `produtos` e o `cockpit` de `qt`. Achado no smoke com dado real — todas as faixas
            # saíam "(— SKUs)" e o modelo teria falado de valor sem saber quantos itens são.
            qt = f.get("produtos") if f.get("produtos") is not None else f.get("qt")
            p.append(f"{f.get('faixa')}: {_brl(f.get('valor'))} ({_num(qt)} SKUs)")
        p.append("")

    porc = ctx.get("orcamento_por_comprador") or []
    if porc:
        p.append("--- ORÇAMENTO POR COMPRADOR (é o que responde \"desempenho dos compradores\") ---")
        p.append("saldo POSITIVO = ainda tem orçamento; NEGATIVO = estourou.")
        for c in porc:
            p.append(f"- {c.get('comprador')}: meta {_brl(c.get('meta'))} · "
                     f"comprado {_brl(c.get('comprado'))} · saldo {_brl(c.get('saldo'))} · "
                     f"{_num(c.get('consumido_pct'), '%')} da meta")
        p.append("")

    top = ctx.get("top") or {}
    if top.get("maiores_ofensores_parado"):
        p.append("--- MAIORES OFENSORES DE CAPITAL PARADO ---")
        for i in top["maiores_ofensores_parado"]:
            p.append(f"- {i.get('codprod')} {i.get('descricao')} ({i.get('fornecedor') or 's/ forn.'}): "
                     f"{_brl(i.get('valor'))}")
        p.append("")
    if top.get("maiores_rupturas_por_venda_perdida"):
        p.append("--- RUPTURAS QUE MAIS CUSTAM (venda perdida estimada/mês) ---")
        for i in top["maiores_rupturas_por_venda_perdida"]:
            p.append(f"- {i.get('codprod')} {i.get('descricao')} ({i.get('fornecedor') or 's/ forn.'}): "
                     f"{_brl(i.get('venda_perdida'))}")
        p.append("")

    pil = ctx.get("pilares")
    if pil:
        bloco = P.renderizar(pil)
        if bloco:
            p.append(bloco)

    tend = ctx.get("tendencia")
    if tend:
        mat = ctx.get("maturidade_serie")
        p.append("--- EVOLUÇÃO: SÉRIE DE FOTOS DIÁRIAS (do mais antigo ao mais recente) ---")
        p.append(f"Dias medidos: {ctx.get('dias_medidos')} · maturidade: {mat}")
        if mat in ("util", "tendencia"):
            p.append("É a única fonte de TENDÊNCIA. Sem ela não afirme que algo melhorou ou piorou.")
        else:
            # ⚠️ com poucos pontos o bloco vira INFORMAÇÃO, não evidência de direção
            p.append("⚠️ POUCOS PONTOS: são medições reais, mas NÃO autorizam falar em melhora, "
                     "piora ou tendência. Cite os valores e diga há quantos dias a medição roda.")
        # ⚠️ O rótulo é DIFERENTE do bloco do placar de propósito. No 1º ensaio com dado real o
        # modelo comparou o último ponto com o placar e inventou um salto de R$ 181 mil para
        # R$ 486 mil no capital parado — os dois estavam certos, em réguas diferentes (60 dias
        # no placar, 15 na série). Nome igual convida à comparação; nome distinto a bloqueia.
        # ⚠️ 08/2026: as RÉGUAS foram unificadas em 60 (`core.status_parado_de` dos dois lados),
        # então o rótulo não pode mais afirmar "15+ dias" — afirmava, e o prompt seguiu ensinando
        # ao comprador uma diferença que já não existia. O que separa os dois hoje é o MOMENTO
        # (foto do fim do dia × posição de agora), e é isso que o rótulo tem de dizer.
        # Gate: `tests/test_ia_prompt_x_core.py`.
        p.append("⚠️ Momento próprio: é a FOTO do fim do dia, não a posição de agora. "
                 "Compare pontos da série ENTRE SI; nunca com o placar acima.")
        for d in tend:
            p.append(f"{d.get('data')}: estoque na foto {_brl(d.get('valor_estoque'))} · "
                     f"parado (foto) {_brl(d.get('valor_parado'))} · "
                     f"ruptura {_num(d.get('n_ruptura'))} SKUs")
        p.append("")

    if ctx.get("ressalvas"):
        p.append("--- RESSALVAS (respeite e avise na resposta) ---")
        for r in ctx["ressalvas"]:
            p.append(f"* {r}")
        p.append("")

    p.append("--- FORA DO SEU ALCANCE ---")
    for f in ctx.get("fora_do_escopo") or []:
        p.append(f"- {f}")
    return "\n".join(p)


def system_prompt(ctx):
    return f"{REGRAS}\n{GLOSSARIO}\nDADOS ATUAIS DO PAINEL:\n{renderizar_contexto(ctx)}\n"


# ── sugestões ──────────────────────────────────────────────────────────────────────────────
def sugestoes(ctx):
    """As perguntas propostas — uma por PILAR, priorizadas pelo que está pior AGORA.

    Pedido do João Victor junto com os pilares: *"aí desenhar as melhores perguntas nessas áreas,
    que vão trazer mais impacto"*. Duas regras:

    · **uma por pilar**, para a pessoa descobrir a largura do agente — a 1ª versão sugeria três
      variações de ruptura e o comprador nunca descobria que dava para perguntar de validade;
    · **ordenadas por urgência real**, não fixas. Sugestão que não olha o número vira decoração e
      envelhece na primeira semana.
    """
    pl = ctx.get("placar") or {}
    ru = ctx.get("ruptura") or {}
    cp = ctx.get("compra") or {}
    orc = ctx.get("orcamento") or {}
    pil = ctx.get("pilares") or {}
    cand = []          # (urgencia, pergunta) — urgência maior primeiro

    if core._n(ru.get("ruptura_skus")) > 0:
        cand.append((core._n(ru.get("ruptura_pct")), "Quais rupturas estão me custando mais caro?"))
    if core._n(orc.get("consumido_pct")) >= 90:
        cand.append((100.0, "O orçamento do mês aguenta o que ainda preciso comprar?"))
    elif core._n(cp.get("itens_a_repor")) > 0:
        cand.append((30.0, "O que eu compro primeiro hoje?"))

    par = pil.get("parado") or {}
    if core._n(par.get("valor")) > 0:
        cand.append((core._n(pl.get("capital_parado_pct")) * 2,
                     "Onde está o capital parado e de quem é?"))

    if (pil.get("fornecedores") or {}).get("maior_lucro"):
        cand.append((20.0, "Quais fornecedores dão mais lucro e quais menos?"))

    if (pil.get("compradores") or []):
        cand.append((25.0, "Como está o desempenho dos compradores?"))

    val = pil.get("validade") or {}
    if val.get("proximos_vencimentos"):
        # urgência = quão perto está o primeiro vencimento
        d = core._n((val["proximos_vencimentos"][0] or {}).get("dias"))
        cand.append((90.0 if d <= 30 else 15.0, "O que vence primeiro e quanto vale?"))

    if (pil.get("cobertura") or {}).get("menor_cobertura"):
        cand.append((35.0, "Que itens vão faltar primeiro?"))

    if ctx.get("maturidade_serie") in ("util", "tendencia"):
        cand.append((10.0, "O estoque está melhorando ou só encolhendo?"))

    cand.sort(key=lambda x: x[0], reverse=True)
    vistas, out = set(), []
    for _u, q in cand:
        if q not in vistas:
            vistas.add(q)
            out.append(q)
    if not out:
        out = ["Como está a saúde do estoque agora?"]
    return out[:6]
