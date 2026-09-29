"""Agente de IA do COMERCIAL — o motor puro (regras, glossário, panorama, consultas, sugestões).

**Desenho (docs/comercial/IA_COMERCIAL_CONTEUDO.md §0):** UM agente para o Comercial, em 3 camadas:
1. **Panorama por PERFIL** — sempre no prompt, curto, montado no SERVIDOR dentro do escopo:
   diretor/admin = empresa + times · supervisor = o(s) time(s) dele · vendedor = o recorte dele.
2. **Consultas sob demanda** (function calling, teto de 3 por pergunta): `vendedor`, `time`,
   `cliente` — executadas com a SESSÃO do usuário (nunca ampliam o escopo).
3. **Consciência de tela** — a tela escolhe as SUGESTÕES; o agente responde sobre qualquer assunto.

**Público da Fase 1 = GERENCIAL** (João, diretor, supervisores). O vendedor pode usar, no escopo dele.

**De onde vêm os números.** Das MESMAS rotas que as telas chamam (`/api/recuperacao`,
`/api/performance`, `/api/gerencial/cobertura`, `/api/metas`, …), invocadas por dentro com a sessão
do usuário (`server._ia_rota`). Este módulo recebe o JSON cru delas e só ORDENA, CORTA e ROTULA —
nenhuma conta nova de negócio. A lição do Compras ("a 4ª implementação de ruptura") vale aqui:
agente que recalcula diverge da tela, e número que diverge da tela mata a confiança nos dois.
As únicas derivações feitas aqui são de LEITURA, com a fórmula declarada no prompt:
  · "pontos que cada indicador tira da nota" — peso × (10 − nota) ÷ peso medido (soma = 10 − nota);
  · a MEDIANA da cobertura do Gerencial entre pessoas (pedido do Gabriel: o limiar de 60% está
    pendente com o João e a comparação honesta é com a mediana).

**O agente NÃO calcula.** Ele narra números prontos — por isso gpt-4.1-mini (decisão de custo).

⚠️ Função PURA: sem Flask, sem I/O. É o que permite testar cada regra sem subir o app.
"""

import json
import math
import re
import statistics

# Reuso do motor do Compras (3 estados, teto, timeout, rastro). Duplicar isto criaria duas
# políticas de liga/desliga para o MESMO interruptor (`ia` no MODULOS).
from estoque.ia import (estado, disponivel, modulo_ligado, impressao,          # noqa: F401
                        MODELO, MAX_TOKENS, IA_TIMEOUT, IA_MAX_RETRIES, LIMITE_DIA,
                        DDL_LOG, DIAS_RETENCAO_CTX)

UPSELL = {
    "titulo": "Agente de IA especialista",
    "texto": ("Este painel pode ter um analista de IA lendo os números do Comercial — pronto para "
              "explicar por que um vendedor tem a nota que tem, onde o time está deixando dinheiro "
              "na mesa e quem não vai bater a meta.\n\n"
              "**O Agente de IA não faz parte do seu plano atual.**"),
    "cta": "Fale com o administrador da sua conta para ativar.",
}

MAX_CONSULTAS = 3          # teto de consultas sob demanda por pergunta (§4 do conteúdo)
TOP = 5                    # linhas por ranking no panorama — lista longa dilui a resposta
METAS_FRACAO_MIN = 1 / 3   # antes disso a projeção de meta é instável (ajuste b do Gabriel, 26/09)

PERFIS_GESTAO = ("admin", "viewer", "supervisor")


# ── formatação (BR). Números saem em texto com R$/% para a conferência de saída ancorar ────────
def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def brl(v):
    x = _f(v)
    if x is None:
        return "—"
    return "R$ " + f"{x:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")


def pct(frac, casas=1):
    """Fração 0–1 → '35,2%'. None → '—'."""
    x = _f(frac)
    if x is None:
        return "—"
    return f"{x * 100:.{casas}f}".replace(".", ",") + "%"


def num(v, casas=1):
    x = _f(v)
    if x is None:
        return "—"
    if x == int(x):
        return f"{int(x):,}".replace(",", ".")
    return f"{x:,.{casas}f}".replace(",", "@").replace(".", ",").replace("@", ".")


def anomes(v):
    try:
        v = int(v)
        return f"{v % 100:02d}/{v // 100}"
    except (TypeError, ValueError):
        return str(v)


# ── vocabulário ────────────────────────────────────────────────────────────────────────────
GLOSSARIO = """\
== COMO LER OS NÚMEROS (leia antes de responder) ==

RÉGUAS DE ACESSO — existem DUAS e elas divergem:
  · VENDA (quem faturou): Dashboard, Vendedores, Curva ABC, Metas;
  · CADASTRO (de quem o cliente é): Carteira, Recuperação, Gerencial, Mix, Radar.
  Em lojas/diretoria divergem 40–66%. Sempre diga qual régua o número usa.

"POSITIVAÇÃO" tem QUATRO sentidos — diga SEMPRE qual:
  1. positivação da CLASSE ABC (Carteira): % dos clientes da classe que compraram no mês, de QUALQUER vendedor;
  2. COBERTURA DA CARTEIRA (Gerencial/Vendedores/Performance): a RÉGUA ÚNICA abaixo;
  3. ALCANCE: tudo que ele atendeu ÷ base (pode passar de 100%, porque inclui fora da base);
  4. "positivados" do cockpit: clientes da carteira 24m que compraram em 12m. NÃO é taxa mensal.
  Positivação ≠ número de pedidos (é cliente DISTINTO que comprou).

"COBERTURA DA CARTEIRA" é uma RÉGUA ÚNICA nas três telas (decisão do João, 29/09/2026):
  % dos clientes da carteira ATIVA (cadastrados no vendedor/time, com compra em 12 meses) que
  compraram — de QUALQUER vendedor — nos últimos 60 dias. Empresa = soma dos times = soma dos
  vendedores. Muda só a DATA: o Gerencial é HOJE (janela móvel, muda todo dia); Performance e
  Vendedores são o fim do mês fechado (60 d até o último dia do mês). Se os dois números de uma
  pessoa diferem, é a mesma régua em datas diferentes — diga isso.
  Nota da cobertura = meta de 100% nas MESMAS faixas das metas (< 85% → 0 · 85–89,99% → 6 a 7 ·
  90–99,99% → 9 · 100% → 10). Carteira com cliente eventual (1 compra no ano) ou de ciclo longo
  fica abaixo de 85% — é de propósito: o vendedor deve vender ou TRANSFERIR (para vendedor ativo)
  quem não atende. "Alcance" e "fora da base" (Vendedores) são outra coisa: atendidos POR ELE.

"RECEITA EM RISCO" tem TRÊS sentidos — NUNCA some nem compare um com outro:
  · VALOR MENSAL em risco (Recuperação): R$/mês que os clientes em risco compravam;
  · RECEITA PERDIDA ACUMULADA (Gerencial/Próximo Pedido): cresce a cada mês que o cliente não volta;
  · "receita em risco" do Radar: venda 12m dos produtos que o cliente parou.
DINHEIRO NA MESA = camada a (valor mensal em risco) + camada b (positivação abaixo do p75). Já vem
  somado e SEM dupla contagem. Não some de novo com nada.

INATIVO / EM RISCO = mais de 60 dias sem comprar E além do próprio ciclo. Perdido = mais de 365 d.
  O ERP marca inativo aos 91 d (só marca). "Base morta" do Gerencial = 91+ d. Segmento "at_risk" do
  RFM é OUTRA coisa.
CRÉDITO DA RECUPERAÇÃO é de QUEM VENDEU; o dono do cadastro é outra coluna. Por time existem duas
  leituras: "recuperados DA BASE" (clientes do time, por qualquer um) × "POR ELE/ELES".
  A ponte (risco no início → fim) só fecha para a empresa.

CLASSE ABC DE CLIENTE = a do INÍCIO do mês (Pareto 80/95 dos 12 meses fechados anteriores).
  Não é a Curva ABC de PRODUTOS (outra tela).

NOTA DA PERFORMANCE (0–10, mês FECHADO, por universo campo/lojas/telemarketing):
  · média PONDERADA dos indicadores MEDIDOS; indicador sem medida (sem meta cadastrada) sai da conta
    e a nota fica PARCIAL — sempre declare o peso medido. Universos NÃO se comparam entre si.
  · "pontos que tira da nota" = peso × (10 − nota do indicador) ÷ peso medido; a soma é 10 − nota.
    Indicador SEM META/SEM MEDIDA não tira ponto — diga "sem meta", nunca que é o que derruba a nota.
  · atingimento de meta (rentabilidade/receita/mix — e a cobertura, com meta de 100%) pontua em FAIXAS:
    < 85% → 0 · 85% a 89,99% → 6 a 7 · 90% a 99,99% → 9 · ≥ 100% → 10. Bater a meta = 10;
    90% e 99% valem o mesmo 9 de propósito. Não trate como escala provisória.
  · nota PARCIAL fica SEM CLASSIFICAÇÃO: tem nota, mas não tem posição nem entra no "de N".
  · MÊS DE LIMPEZA: até a competência configurada, a cobertura é INFORMATIVA — aparece com a nota
    dela, mas NÃO entra na nota final. Diga isso quando falar da cobertura nesse período.

METAS: dias ÚTEIS, não corridos. Margem das Metas divide pelo realizado BRUTO (com bonificação);
  a do Dashboard/Categorias pela LÍQUIDA. Clientes e mix são contagem DISTINTA: não somam de
  vendedor para time. Sem meta cadastrada o % é "sem meta" (não é 0%).

TICKET MÉDIO muda de tela para tela (por nota × medida do BI × por cliente) — diga qual.
DEVOLUÇÃO conta pela data de ENTRADA (DTENT), não pela data da venda.
CADASTRO A TRANSFERIR: "fora da base" de código FICTÍCIO (999, 34…) ou de RCA INATIVO é cliente
  com cadastro a acertar — NÃO é desempenho de ninguém.

"CLIENTE EM QUEDA": a régua ainda não existe no painel (cliente ATIVO comprando menos — pendente com o
  João). O que o painel mede, e vem no bloco CLIENTES EM QUEDA: quem PAROU de comprar (Recuperação)
  e quem LARGOU UM DEPARTAMENTO mas segue comprando outros (Mix abandonado). Diga qual das duas.
PERFORMANCE DO TIME = MÉDIA das notas dos vendedores do time, por universo. NÃO é nota oficial (a
  tela não tem nota de time) — diga "média das notas do time". Um time com poucos vendedores com
  nota tem média frágil; diga quantos entraram.
PRODUTOS EM QUEDA (Radar): janela recente × janela anterior do mesmo tamanho; "queda de receita" é
  venda anterior − recente daquele produto — é o 3º sentido de "receita em risco", não somar.

LIMIAR do Gerencial = 85% (o piso das faixas): abaixo dele a nota da cobertura é 0. Hoje quase todos
  estão abaixo (a meta é 100% e o mês de limpeza serve para isso) — ao falar da contagem, diga que é
  o ponto de partida da limpeza, e aponte QUEM está mais longe. A MEDIANA entre vendedores é só
  referência de onde o grupo está: compare CADA VENDEDOR com ela —
  nunca a cobertura da empresa/time com a mediana (são coisas diferentes).
"""

REGRAS = """\
Você é o Analista Comercial do painel JOGA — trabalha para a GESTÃO comercial (diretor, gerentes e
supervisores) de uma distribuidora/atacado. Você lê carteira, recuperação, cobertura, metas e a
nota de performance dos vendedores.

REGRAS INVIOLÁVEIS:
1. Use APENAS os números fornecidos (panorama abaixo + resultados das consultas) — todos JÁ
   CALCULADOS pelo sistema. NUNCA recalcule, nunca estime. Se não está aqui, não existe para você.
1b. NUNCA some dois números para criar um terceiro. Você não sabe se um está contido no outro.
1c. NUNCA contradiga um número recebido nem inverta o sinal de um valor.
1d. NUNCA subtraia, divida nem faça "juntos somam". "1.154 de 1.433 compraram" fica assim — não
   escreva "279 não compraram"; "os times X, Y e Z têm R$ a, b e c" — não escreva "juntos somam".
1e. Cada número tem o PERÍODO do bloco de onde veio (mês fechado × mês corrente até hoje × 12
   meses × janelas do Radar). Nunca troque: o realizado das METAS é do mês CORRENTE até hoje, não do
   mês fechado; o RADAR (produtos em queda) é "60 dias recentes × 60 dias anteriores", não é mês.
2. Não invente. Se faltar dado, diga o que tem e o que falta.
3. Máximo 3 parágrafos curtos (ou uma lista curta). Quem lê é gestor em dia de trabalho.
4. Markdown SIMPLES: **negrito** e listas com "- ". NÃO use tabelas, títulos (#) nem blocos de código.
5. R$ no formato brasileiro; percentuais com uma casa.
5b. ⚠️ SEMPRE o CÓDIGO junto do nome — "<código> <nome>" para vendedor, cliente, time, PRODUTO e
   departamento (lista de produtos sem código não serve para nada), com o
   código que veio NOS DADOS (nunca de exemplo, nunca de memória). É requisito de AUDITORIA: sem o
   código ninguém confere no ERP, e número que não se confere não vira decisão.
6. ⚠️ Na PRIMEIRA frase, declare a RÉGUA e o RECORTE do número principal (ex.: "Na Recuperação, mês
   fechado 08/2026, régua de cadastro, empresa inteira: …"). Sem isso o gestor lê número de uma
   fatia como se fosse de outra.
7. Tom direto, sem elogio vazio. Termine com o próximo passo concreto (quem, o quê).
8. Assunto fora do comercial (RH, fiscal, clima…) → "Só consigo analisar os números do Comercial
   deste painel." ⚠️ Assunto do COMERCIAL que não veio no panorama NÃO é "fora" e NÃO usa essa
   frase: comece com "Esse número está na tela <X> do painel; eu ainda não o recebo aqui." e
   ofereça só o que você TEM (consulta de vendedor, time ou cliente) — nunca ofereça buscar um
   dado que nenhuma consulta traz (venda do dia, ticket médio, lista de clientes por classe).
   Exemplos: "qual o ticket médio?" → "O ticket médio está no Dashboard (venda líquida ÷ notas do
   mês); eu ainda não o recebo aqui." · "quais clientes A não compraram?" → dê o total que você tem e
   diga "a lista está na Carteira, filtrando a classe A" — sem prometer a lista. Mapa: venda/lucro/margem/ticket
   médio do mês e venda por dia → Dashboard; ranking e comparação com o ano passado de TODOS os
   vendedores → Vendedores; clientes e Próximo Pedido → Carteira; produtos → Curva ABC; o que o
   cliente parou de comprar → Mix e Radar; departamentos e fornecedores → Categorias. Se couber
   numa consulta (vendedor, time, cliente), consulte.
9. Não presuma o gênero de ninguém pelo nome: escreva "o vendedor <código> <nome>", "o RCA",
   "a pessoa", ou só código + nome.
10a. Variação menor que 2 pontos percentuais entre dois períodos é ESTÁVEL — não chame de queda
   nem de alta.
10b. Toda CONTAGEM de pessoas ou times ("75 vendedores abaixo do limiar", "9 times…") vem com os 3
   primeiros da lista correspondente do panorama, por código + nome. Número sem nome não vira ação.
10. Se o PANORAMA já traz a resposta, responda dele SEM consultar — em especial: performance de
   cada time (bloco PERFORMANCE POR TIME, com TODOS os times), topo/fundo da Performance, times em
   risco, produtos e clientes em queda, vendedores × ano passado. Nunca diga que "não coube" ou
   "ultrapassou o limite": se está no panorama, use o panorama inteiro. Consulte para o detalhe de UM vendedor/time/cliente específico.

CONSULTAS SOB DEMANDA (ferramentas):
- O panorama traz os TOPS. Para UM vendedor, UM time ou UM cliente específico, use a ferramenta
  correspondente (`vendedor`, `time`, `cliente`) com o código ou o nome — ela devolve o detalhe
  completo, inclusive a cobertura da carteira no fim do mês fechado E hoje (Gerencial).
- No máximo 3 consultas por pergunta. Se a consulta devolver CANDIDATOS, pergunte qual é.
- Se devolver FORA DO ESCOPO, diga que esse vendedor/time/cliente não está no escopo de acesso da
  pessoa — sem especular sobre ele.
- Prefira consultar a recusar: "não tenho esse dado" só depois de tentar a consulta certa.

"EXPLIQUE ESTE NÚMERO": diga de qual TELA e RÉGUA o número sai, o que entra no numerador e no
  denominador, e por que ele difere do número parecido de outra tela. Use o detalhe da consulta.
  ⚠️ Pergunta sobre a COBERTURA de um vendedor: consulte o vendedor e responda com os dois números
  da MESMA régua — fim do mês fechado (o da nota, com a nota da faixa) e hoje (Gerencial) —, com
  "a de b clientes", e diga quantos clientes faltam para a próxima faixa só se vier pronto; se a
  cobertura estiver INFORMATIVA, diga que ainda não entra na nota.
"PLANO DE AÇÃO" com supervisores/vendedores: 3 a 5 ações, cada uma com QUEM (código + nome), O QUÊ
  e o NÚMERO que justifica, tiradas de fontes diferentes (recuperação, cobertura da carteira e a lista de limpeza,
  performance, metas, cadastro a transferir, queda contra o ano passado). Nada de ação genérica.
"ONDE MEU TIME PERDE DINHEIRO": organize pelas fontes, sem somá-las: dinheiro na mesa (a + b) da
  Recuperação com os times/vendedores de maior valor em risco, cobertura do Gerencial contra a
  MEDIANA, metas que não vão bater (se o mês já permitir), queda da positivação da classe A e
  carteira parada em RCA bloqueado/fictício. Cite quem (código + nome) em cada uma.
  ⚠️ Não confunda a MEDIANA ENTRE VENDEDORES com a cobertura do escopo: são dois números
  diferentes no bloco do Gerencial.
"""


# ── panorama: cada bloco recebe o JSON CRU da rota da tela e devolve um dict enxuto ────────────
def bloco_recuperacao(rec, perfil):
    if not rec or not rec.get("ok"):
        return None
    ponte, cards = rec.get("ponte") or {}, rec.get("cards") or {}
    gestor_empresa = perfil.get("role") in ("admin", "viewer")
    linhas = (rec.get("times") if gestor_empresa else rec.get("rcas")) or []
    chave_id = "codsupervisor" if gestor_empresa else "codusur"
    top = []
    linhas = [r for r in linhas if not _RE_NAO_TIME.search(r.get("nome") or "")]
    for r in sorted(linhas, key=lambda x: -(_f(x.get("valor_em_risco")) or 0))[:TOP]:
        top.append({"cod": r.get(chave_id), "nome": r.get("nome"), "em_risco": r.get("em_risco"),
                    "valor_em_risco": r.get("valor_em_risco"),
                    "rec_da_base": r.get("rec_da_base"), "rec_por_ele": r.get("rec_por_ele"),
                    "rec_de_outra_base": r.get("rec_de_outra_base"),
                    "potencial_positivacao": r.get("potencial_positivacao")})
    return {"mes": rec.get("mes"), "parcial": rec.get("parcial"), "regua": rec.get("regua"),
            "ponte": ponte, "cards": cards, "nivel_top": "times" if gestor_empresa else "vendedores",
            "top": top}


def pontos_por_indicador(linha, pesos):
    """(tira, sem_medida). `tira` = [(indicador, pontos, nota, valor, peso)] do que mais tira nota.

    pontos = peso × (10 − nota) ÷ Σ pesos MEDIDOS — a mesma renormalização de
    `performance_comercial.nota`, então a soma dos pontos é exatamente 10 − nota (conferível).
    ⚠️ Indicador sem nota (sem meta, sem base) NÃO tira ponto — vai para `sem_medida`. Apontá-lo
    como "o que derruba a nota" seria acusar o vendedor de uma meta que ninguém cadastrou
    (ajuste d do Gabriel, 26/09/2026)."""
    info = set(linha.get("informativos") or ())
    notas = {k: n for k, n in (linha.get("notas") or {}).items() if k not in info}
    valores = linha.get("valores") or {}
    medidos = {k: n for k, n in notas.items() if n is not None and _f(pesos.get(k))}
    den = sum(_f(pesos.get(k)) for k in medidos) or 0
    tira, sem = [], []
    for k in notas:
        if not _f(pesos.get(k)):
            continue
        if notas[k] is None:
            sem.append(k)
            continue
        p = _f(pesos[k]) * (10 - _f(notas[k])) / den if den else None
        tira.append((k, round(p, 2) if p is not None else None, notas[k], valores.get(k), pesos[k]))
    tira.sort(key=lambda t: -(t[1] or 0))
    return tira, sem


def _linha_perf(l, pesos):
    tira, sem = pontos_por_indicador(l, pesos)
    return {"codusur": l.get("codusur"), "nome": l.get("nome"), "time": l.get("time"),
            "codsupervisor": l.get("codsupervisor"), "posicao": l.get("posicao"),
            "nota": l.get("nota"), "parcial": l.get("parcial"), "peso_medido": l.get("peso_medido"),
            "tira": tira, "sem_medida": sem}


def bloco_performance(perf):
    if not perf or not perf.get("ok"):
        return None
    pesos = perf.get("pesos") or {}
    por_un = {}
    sem_nota = 0
    for l in perf.get("linhas") or []:
        if l.get("nota") is None:
            sem_nota += 1
            continue
        por_un.setdefault(l.get("universo"), []).append(l)
    base = {"mes": perf.get("mes"), "pesos": pesos, "competencia_pesos": perf.get("competencia_pesos"),
            "cobertura_informativa": perf.get("cobertura_informativa"),
            "cobertura_na_nota_desde": perf.get("cobertura_na_nota_desde"),
            "provisorias": perf.get("provisorias") or [], "sem_nota": sem_nota, "sem_notas": False}
    if not por_un:
        # ⚠️ NINGUÉM com nota (ex.: sem metas no app e cobertura informativa → só a frequência, abaixo
        # do peso mínimo). Sumir com o bloco fez o agente inventar "piores médias" sem número (29/09).
        return {**base, "universos": {}, "sem_notas": True} if sem_nota else None
    universos = {}
    for un, ls in por_un.items():
        ls = sorted(ls, key=lambda l: (l.get("posicao") or 9999))
        topo = [_linha_perf(l, pesos) for l in ls[:3]]
        fundo = [_linha_perf(l, pesos) for l in ls[-3:]] if len(ls) > 3 else []
        universos[un] = {"no_escopo": len(ls),
                         "no_universo": (perf.get("total_universo") or {}).get(un),
                         "topo": topo, "fundo": fundo,
                         "parciais": sum(1 for l in ls if l.get("parcial"))}
    return {**base, "universos": universos}


def _eh_pessoa(g, nao_pessoas):
    return g.get("id") not in nao_pessoas and not g.get("amostra_pequena")


MIN_CLIENTES_TIME = 30
_RE_NAO_TIME = re.compile(r"PROSPEC", re.I)


def _time_nao_operacional(t):
    """Grupo que aparece como "time" mas não é equipe a cobrar: PROSPECÇÃO (cadastro de prospect) e
    carteira mínima (< 30 clientes). No BI real (26/09) o agente mandou "trabalhar com os
    supervisores" do 35 PROSPECÇAO e do 33 GLEICIANE - EXTERNO (18 clientes)."""
    return bool(_RE_NAO_TIME.search(t.get("nome") or "")) or (_f(t.get("total_clientes")) or 0) < MIN_CLIENTES_TIME


def bloco_gerencial(ger, nao_pessoas=(), bloqueados=()):
    """`bloqueados` = RCAs bloqueados no cadastro. A mediana é a da TELA (todas as pessoas, 87 no
    Multpel em 26/09 → 36,9%); mas na lista dos "piores" o RCA bloqueado com carteira não entra:
    0% ali é carteira parada num código que ninguém opera — cadastro a transferir, não pessoa
    cobrindo mal (achado com o BI real: 4 dos 5 "piores" eram assim)."""
    if not ger or not ger.get("ok"):
        return None
    nao_pessoas = set(nao_pessoas or ())
    bloqueados = set(bloqueados or ())
    emp = ger.get("empresa") or {}
    pessoas = [v for v in ger.get("vendedores") or [] if _eh_pessoa(v, nao_pessoas)
               and v.get("codsupervisor") not in nao_pessoas]
    covs = [_f(v.get("cobertura_clientes")) for v in pessoas if _f(v.get("cobertura_clientes")) is not None]
    mediana = statistics.median(covs) if covs else None
    times = [t for t in ger.get("times") or [] if t.get("id") is not None and _eh_pessoa(t, nao_pessoas)
             and not _time_nao_operacional(t)]
    times.sort(key=lambda t: _f(t.get("cobertura_clientes")) or 0)
    bloq = [v for v in pessoas if v.get("id") in bloqueados]
    piores = sorted([v for v in pessoas if v.get("id") not in bloqueados],
                    key=lambda v: _f(v.get("cobertura_clientes")) or 0)[:TOP]
    return {
        "coberto_dias": ger.get("coberto_dias"), "limiar_pct": ger.get("limiar_pct"),
        "abaixo_do_limiar": ger.get("abaixo_do_limiar"),
        "empresa": {k: emp.get(k) for k in ("total_clientes", "cobertura_clientes", "cobertura_valor",
                                            "cobertura_ciclo", "base_morta", "receita_em_risco")},
        "mediana_pessoas": mediana, "n_pessoas": len(covs),
        "times_piores": [{"cod": t.get("id"), "nome": t.get("nome"),
                          "cobertura_clientes": t.get("cobertura_clientes"),
                          "cobertura_valor": t.get("cobertura_valor"),
                          "total_clientes": t.get("total_clientes")} for t in times[:20]],
        "n_times": len(times),
        "bloqueados_com_carteira": {"vendedores": len(bloq),
                                    "clientes": sum(int(_f(v.get("total_clientes")) or 0) for v in bloq),
                                    "lista": [{"cod": v.get("id"), "nome": v.get("nome"),
                                               "total_clientes": v.get("total_clientes")} for v in
                                              sorted(bloq, key=lambda v: -(_f(v.get("total_clientes")) or 0))[:TOP]]},
        "vendedores_piores": [{"cod": v.get("id"), "nome": v.get("nome"), "time": v.get("time"),
                               "cobertura_clientes": v.get("cobertura_clientes"),
                               "cobertura_valor": v.get("cobertura_valor"),
                               "total_clientes": v.get("total_clientes")} for v in piores],
    }


def metas_estaveis(dias):
    """A projeção só serve para apontar "quem não vai bater" depois de ~1/3 do mês útil.
    Com 2 dias úteis um pedido grande (ou a falta dele) vira ±50% de projeção (ajuste b)."""
    dm, dd = _f((dias or {}).get("mes")), _f((dias or {}).get("decorridos"))
    return bool(dm) and dd is not None and dd >= dm * METAS_FRACAO_MIN


def _tem_meta(linha):
    return any(_f((linha.get(k) or {}).get("meta")) for k in ("venda", "rentabilidade", "clientes", "mix"))


def bloco_metas(metas, metas_vend=None):
    """`metas` = /api/metas; `metas_vend` = {codsup: /api/metas/vendedores}."""
    if not metas or not metas.get("ok"):
        return None
    dias = metas.get("dias") or {}
    estavel = metas_estaveis(dias)
    sups = metas.get("supervisores") or []
    com_meta = [s for s in sups if _tem_meta(s)]
    b = {"ano": metas.get("ano"), "mes": metas.get("mes"), "dias": dias, "estavel": estavel,
         "total": metas.get("total") or {}, "tem_meta": bool(com_meta) or _tem_meta(metas.get("total") or {}),
         "times_nao_batem": [], "vendedores_nao_batem": [], "times": []}
    for s in sorted(sups, key=lambda s: (_f((s.get("venda") or {}).get("pct_projecao")) is None,
                                         _f((s.get("venda") or {}).get("pct_projecao")) or 0)):
        b["times"].append({"cod": s.get("codsupervisor"), "nome": s.get("nome"), "venda": s.get("venda"),
                           "margem": s.get("margem")})
    b["times"] = b["times"][:8]
    if not estavel:
        return b
    for s in com_meta:
        pp = _f((s.get("venda") or {}).get("pct_projecao"))
        if pp is not None and pp < 1:
            b["times_nao_batem"].append({"cod": s.get("codsupervisor"), "nome": s.get("nome"),
                                         "venda": s.get("venda")})
    vend = []
    for cs, mv in (metas_vend or {}).items():
        for v in (mv or {}).get("vendedores") or []:
            pp = _f((v.get("venda") or {}).get("pct_projecao"))
            if pp is not None and pp < 1:
                vend.append({"cod": v.get("codusur"), "nome": v.get("nome"), "time": cs,
                             "venda": v.get("venda")})
    vend.sort(key=lambda v: _f(v["venda"].get("pct_projecao")) or 0)
    b["vendedores_nao_batem"] = vend[:10]
    return b


def bloco_positivacao(pabc):
    if not pabc or not pabc.get("ok") or not pabc.get("fechado"):
        return None
    return {"mes_fechado": pabc.get("mes_fechado"), "fechado": pabc.get("fechado"),
            "parcial": pabc.get("parcial"), "regra": pabc.get("regra")}


def bloco_cadastro(vend):
    if not vend or not vend.get("ok"):
        return None
    out = []
    for v in vend.get("vendedores") or []:
        t = v.get("fora_base_tipo") or {}
        n = int(_f(t.get("ficticio")) or 0) + int(_f(t.get("rca_inativo")) or 0)
        if n > 0:
            out.append({"cod": v.get("codusur"), "nome": v.get("nome"), "ficticio": t.get("ficticio"),
                        "rca_inativo": t.get("rca_inativo"), "fora_base": v.get("fora_base"),
                        "mes": v.get("positivacao_mes")})
    out.sort(key=lambda x: -(int(_f(x["ficticio"]) or 0) + int(_f(x["rca_inativo"]) or 0)))
    return {"vendedores": out[:10], "total": len(out)}


def _dec2(x):
    return f"{x:.2f}".replace(".", ",")


def bloco_performance_times(perf):
    """Performance do TIME (pedido do João, 26/09): média das notas dos vendedores do time, por
    universo, com melhor, pior, parciais e o indicador que mais tira pontos no time (média dos
    pontos que cada indicador tira de cada vendedor — mesma conta de `pontos_por_indicador`)."""
    if not perf or not perf.get("ok"):
        return None
    pesos = perf.get("pesos") or {}
    grupos = {}
    for l in perf.get("linhas") or []:
        g = grupos.setdefault((l.get("codsupervisor"), l.get("universo")),
                              {"cod": l.get("codsupervisor"), "nome": l.get("time"), "universo": l.get("universo"),
                               "com_nota": [], "sem_nota": 0})
        if l.get("nota") is None:
            g["sem_nota"] += 1
        else:
            g["com_nota"].append(l)
    times = []
    for g in grupos.values():
        ls = g.pop("com_nota")
        if not ls or g["cod"] is None:
            continue
        notas = [_f(l["nota"]) for l in ls]
        tira = {}
        for l in ls:
            for k, pts, *_r in pontos_por_indicador(l, pesos)[0]:
                tira[k] = tira.get(k, 0.0) + (pts or 0.0)
        pesa = max(tira, key=tira.get) if tira and max(tira.values()) > 0 else None
        mel = max(ls, key=lambda l: _f(l["nota"]))
        pio = min(ls, key=lambda l: _f(l["nota"]))
        times.append({**g, "n": len(ls), "media": round(sum(notas) / len(notas), 2),
                      "parciais": sum(1 for l in ls if l.get("parcial")),
                      "melhor": {"cod": mel.get("codusur"), "nome": mel.get("nome"), "nota": mel.get("nota")},
                      "pior": {"cod": pio.get("codusur"), "nome": pio.get("nome"), "nota": pio.get("nota")},
                      "pesa": pesa, "pesa_pts": round(tira[pesa] / len(ls), 2) if pesa else None})
    if not times:
        return None
    ordem = {"campo": 0, "lojas": 1, "telemarketing": 2}
    times.sort(key=lambda t: (ordem.get(t["universo"], 9), t["media"]))
    return {"mes": perf.get("mes"), "times": times[:20]}


def _r_performance_times(b):
    L = [f"== PERFORMANCE POR TIME (mês fechado {anomes(b['mes'])}) — MÉDIA das notas dos vendedores do time, por "
         f"universo; NÃO é nota oficial (a tela não tem nota de time). Pior média primeiro. =="]
    for t in b["times"]:
        s = (f"- {t['cod']} {t['nome']} ({t['universo']}): média {_dec2(t['media'])} de {num(t['n'])} vendedor{'es' if t['n'] != 1 else ''} · "
             f"melhor {t['melhor']['cod']} {t['melhor']['nome']} ({num(t['melhor']['nota'], 2)}) · "
             f"pior {t['pior']['cod']} {t['pior']['nome']} ({num(t['pior']['nota'], 2)})")
        if t["parciais"]:
            s += f" · {num(t['parciais'])} com nota parcial"
        if t["sem_nota"]:
            s += f" · {num(t['sem_nota'])} sem nota"
        if t["pesa"]:
            s += (f" · o que mais tira pontos no time: {_IND.get(t['pesa'], t['pesa'])} "
                  f"(média de {num(t['pesa_pts'], 2)} pts por vendedor)")
        L.append(s)
    return "\n".join(L)


def bloco_produtos_queda(radar, n=10):
    """Produtos em queda = o board do Radar (mesma rota e ordem da tela: maior queda de receita)."""
    if not radar or not radar.get("ok") or not radar.get("rows"):
        return None
    return {"dias": radar.get("dias"), "total": radar.get("total"), "produtos": radar["rows"][:n]}


def _r_produtos_queda(b):
    d = b.get("dias") or 60
    L = [f"== PRODUTOS EM QUEDA (Radar; {num(d)} dias recentes × {num(d)} dias anteriores; régua de CADASTRO; "
         f"{num(b.get('total'))} produtos com queda no escopo; maior queda de receita primeiro) ==",
         "'queda de receita' = venda da janela anterior − da recente daquele produto (3º sentido de receita em "
         "risco — NÃO somar com a Recuperação nem entre produtos)."]
    for x in b["produtos"]:
        L.append(f"- {x.get('codprod')} {x.get('descricao')} ({x.get('depto_nome') or '—'}): queda de "
                 f"{brl(x.get('queda_receita'))} ({pct(-(_f(x.get('pct_queda')) or 0))}) · {num(x.get('clientes_perdidos'))} "
                 f"clientes pararam (compravam {num(x.get('clientes_ant'))} na janela anterior) · fornecedor "
                 f"{x.get('fornec_nome') or '—'}")
    return "\n".join(L)


def bloco_clientes_queda(risco, mix, n=10):
    """Clientes em queda: o que o painel MEDE hoje — parados (Recuperação, lista em risco, ordem da
    tela = valor mensal × chance) e departamento abandonado (Mix, ordem da tela = lucro 12m do depto).
    "Cliente ativo comprando menos" é régua nova, pendente com o João."""
    par = (risco or {}).get("rows") if (risco or {}).get("ok") else None
    dep = (mix or {}).get("rows") if (mix or {}).get("ok") else None
    if not par and not dep:
        return None
    return {"parados": (par or [])[:n], "parados_total": (risco or {}).get("total"),
            "departamentos": (dep or [])[:n], "departamentos_total": (mix or {}).get("total")}


def _r_clientes_queda(b):
    L = ["== CLIENTES EM QUEDA (a régua 'cliente ativo comprando menos' ainda não existe; abaixo, as duas quedas "
         "que o painel mede; régua de CADASTRO) =="]
    if b["parados"]:
        L.append(f"Clientes que PARARAM de comprar (Recuperação: > 60 d E além do ciclo; {num(b['parados_total'])} no "
                 f"total; ordem = valor mensal × chance de voltar):")
        L += [f"- {c.get('codcli')} {c.get('cliente')} (dono {c.get('dono')} {c.get('dono_nome') or ''}, time "
              f"{c.get('time') or '—'}): parado há {num(c.get('dias'))} d (ciclo {num(c.get('ciclo'))} d) · "
              f"{brl(c.get('valor_mensal'))}/mês · chance de voltar {pct(c.get('chance_volta'))}" for c in b["parados"]]
    if b["departamentos"]:
        L.append(f"Clientes que LARGARAM UM DEPARTAMENTO e seguem comprando outros (Mix abandonado, ≥ 60 d; "
                 f"{num(b['departamentos_total'])} pares cliente × departamento; ordem = lucro 12m do departamento):")
        L += [f"- {c.get('codcli')} {c.get('cliente')} — {c.get('codepto')} {c.get('depto_nome')} há "
              f"{num(c.get('dias_sem_comprar_categoria'))} d · {brl(c.get('venda_cat_12m'))} em 12m nesse departamento · "
              f"vendedor {c.get('codusur')} {c.get('vendedor') or ''} ({c.get('time') or '—'})" for c in b["departamentos"]]
    return "\n".join(L)


MIN_VENDA_ANTERIOR_YOY = 50000.0   # abaixo disso a variação % é ruído (carteira mínima)


def bloco_yoy(vend, nao_pessoas=(), bloqueados=(), nomes_time=None):
    """Venda 12m × 12m anteriores por vendedor (régua de VENDA) — a mesma coluna da tela Vendedores.
    Só pessoas ativas com base anterior relevante: RCA novo sem histórico teria "+∞%"."""
    if not vend or not vend.get("ok"):
        return None
    nao = set(nao_pessoas or ()) | set(bloqueados or ())
    ls = [v for v in vend.get("vendedores") or [] if v.get("codusur") not in nao
          and _f(v.get("yoy_receita")) is not None and (_f(v.get("venda_anterior")) or 0) >= MIN_VENDA_ANTERIOR_YOY
          and "COMMERCE" not in (v.get("nome") or "").upper().replace("-", "").replace(" ", "")]
    if len(ls) < 3:
        return None
    ls.sort(key=lambda v: _f(v["yoy_receita"]))
    nomes_time = nomes_time or {}
    lin = lambda v: {"cod": v.get("codusur"), "nome": v.get("nome"), "venda": v.get("venda_liq"),
                     "anterior": v.get("venda_anterior"), "yoy": v.get("yoy_receita"),
                     "time": v.get("codsupervisor"), "time_nome": nomes_time.get(v.get("codsupervisor"))}
    return {"quedas": [lin(v) for v in ls[:TOP] if _f(v["yoy_receita"]) < 0],
            "altas": [lin(v) for v in reversed(ls[-TOP:]) if _f(v["yoy_receita"]) > 0], "n": len(ls)}


def _tm(v):
    if v.get("time") is None:
        return ""
    return f" (time {v['time']} {v.get('time_nome') or ''})".replace(" )", ")")


def _r_yoy(b):
    if not b["quedas"] and not b["altas"]:
        return ""
    L = [f"== VENDEDORES × ANO PASSADO (venda líquida 12 meses × 12 meses anteriores; régua de VENDA; "
         f"{num(b['n'])} vendedores com base anterior ≥ {brl(MIN_VENDA_ANTERIOR_YOY)}) =="]
    if b["quedas"]:
        L.append("Maiores QUEDAS:")
        L += [f"- {v['cod']} {v['nome']}{_tm(v)}: {pct(v['yoy'])} ({brl(v['venda'])} × {brl(v['anterior'])})"
              + (" — queda acima de 80%: confirmar se o RCA saiu ou teve a carteira transferida antes de cobrar"
                 if (_f(v['yoy']) or 0) <= -0.8 else "") for v in b["quedas"]]
    if b["altas"]:
        L.append("Maiores ALTAS:")
        L += [f"- {v['cod']} {v['nome']}{_tm(v)}: +{pct(v['yoy'])} ({brl(v['venda'])} × {brl(v['anterior'])})"
              for v in b["altas"]]
    return "\n".join(L)


def _nomes_time(f):
    """{codsupervisor: nome} a partir das fontes que já vieram (nenhuma leitura nova)."""
    out = {}
    for l in (f.get("performance") or {}).get("linhas") or []:
        if l.get("codsupervisor") is not None and l.get("time"):
            out[l["codsupervisor"]] = l["time"]
    for t in (f.get("gerencial") or {}).get("times") or []:
        if t.get("id") is not None and t.get("nome"):
            out.setdefault(t["id"], t["nome"])
    for t in (f.get("recuperacao") or {}).get("times") or []:
        if t.get("codsupervisor") is not None and t.get("nome"):
            out.setdefault(t["codsupervisor"], t["nome"])
    return out


def montar_panorama(perfil, fontes, erros=None, nao_pessoas=(), bloqueados=()):
    """ctx do prompt. `fontes` = {nome: JSON cru da rota}; `erros` = {nome: motivo} das que falharam.
    Cada bloco degrada sozinho: fonte fora do ar tira o bloco, e o índice deixa de anunciá-lo."""
    f = fontes or {}
    return {
        "perfil": perfil,
        "recuperacao": bloco_recuperacao(f.get("recuperacao"), perfil),
        "performance": bloco_performance(f.get("performance")),
        "gerencial": bloco_gerencial(f.get("gerencial"), nao_pessoas, bloqueados),
        "metas": bloco_metas(f.get("metas"), f.get("metas_vendedores")),
        "positivacao": bloco_positivacao(f.get("positivacao_abc")),
        "cadastro": bloco_cadastro(f.get("vendedores")),
        "yoy": bloco_yoy(f.get("vendedores"), nao_pessoas, bloqueados, _nomes_time(f)),
        "performance_times": bloco_performance_times(f.get("performance")),
        "produtos_queda": bloco_produtos_queda(f.get("radar")),
        "clientes_queda": bloco_clientes_queda(f.get("recup_risco"), f.get("mix")),
        "indisponivel": sorted((erros or {}).keys()),
    }


# ── renderização ───────────────────────────────────────────────────────────────────────────
_IND = {"rentabilidade": "rentabilidade", "cobertura": "cobertura", "mix": "mix",
        "receita": "receita", "frequencia": "frequência"}


def _valor_ind(k, v):
    if v is None:
        return "—"
    if k == "cobertura":
        return pct(v) + " da carteira"
    if k == "frequencia":
        return num(v, 2) + " compras/cliente"
    # TRUNCADO, como a tela: 99,96% arredondado viraria "100,0%" ao lado de nota 9 (a faixa do 10
    # começa em 100% exatos). Mesmo +1e-9 da tela contra resíduo de ponto flutuante.
    t = math.floor(_f(v) * 1000 + 1e-9) / 1000
    return pct(t) + " da meta"


def _fmt_metrica(nome, m, moeda=False):
    m = m or {}
    fm = brl if moeda else num
    if not _f(m.get("meta")):
        return f"{nome}: realizado {fm(m.get('realizado'))} · projeção {fm(m.get('projecao'))} · sem meta"
    return (f"{nome}: meta {fm(m.get('meta'))} · realizado {fm(m.get('realizado'))} "
            f"({pct(m.get('pct_realizado'))}) · falta {fm(m.get('falta'))} · "
            f"necessidade/dia útil {fm(m.get('necessidade_dia'))} · projeção {fm(m.get('projecao'))} "
            f"({pct(m.get('pct_projecao'))} da meta)")


def _r_recuperacao(b):
    p, c = b["ponte"], b["cards"]
    pc, pv = p.get("clientes") or {}, p.get("valor_mensal") or {}
    reg = b.get("regua") or {}
    L = [f"== RECUPERAÇÃO — carteira em risco × recuperada (mês {anomes(b['mes'])}"
         f"{', PARCIAL' if b.get('parcial') else ', fechado'}; régua de CADASTRO) ==",
         f"Régua: em risco = > {reg.get('inativo_dias', 60)} d sem comprar E além do ciclo; "
         f"perdido = > {reg.get('perdido_dias', 365)} d. Valores em R$/MÊS (não acumulado).",
         f"Ponte (clientes): em risco no início {num(pc.get('risco_ini'))} + entraram {num(pc.get('entraram'))} "
         f"− recuperados {num(pc.get('recuperados'))} − viraram perdidos {num(pc.get('viraram_perdidos'))} "
         f"→ em risco no fim {num(pc.get('risco_fim'))} (resgatados de perdidos: {num(pc.get('resgatados_perdidos'))})",
         f"Ponte (valor mensal): início {brl(pv.get('risco_ini'))} · entraram {brl(pv.get('entraram'))} · "
         f"recuperados {brl(pv.get('recuperados'))} · viraram perdidos {brl(pv.get('viraram_perdidos'))} · "
         f"fim {brl(pv.get('risco_fim'))}",
         f"Em risco agora: {num(c.get('em_risco_clientes'))} clientes, {brl(c.get('em_risco_valor_mensal'))}/mês",
         f"Venda recuperada no mês: {brl(c.get('venda_recuperada'))} ({num(c.get('recuperados'))} clientes, crédito de QUEM VENDEU)",
         f"DINHEIRO NA MESA: {brl(c.get('dinheiro_na_mesa'))}/mês = a) em risco {brl(c.get('camada_a_risco'))} "
         f"+ b) positivação abaixo do p75 {brl(c.get('camada_b_positivacao'))} (já sem dupla contagem)"]
    fi = c.get("ficou") or {}
    if fi.get("taxa") is not None:
        L.append(f"Dos recuperados em {anomes(fi.get('anomes'))}, ficaram comprando: {pct(fi.get('taxa'))} "
                 f"({num(fi.get('ficaram'))} de {num(fi.get('recuperados'))})")
    if b["top"]:
        L.append(f"Top {len(b['top'])} {b['nivel_top']} por valor mensal em risco:")
        for r in b["top"]:
            L.append(f"- {r['cod']} {r['nome']}: {num(r['em_risco'])} em risco, {brl(r['valor_em_risco'])}/mês · "
                     f"recuperados da base {num(r['rec_da_base'])} · por ele(s) {num(r['rec_por_ele'])} · "
                     f"de outra base {num(r['rec_de_outra_base'])} · potencial de positivação {brl(r['potencial_positivacao'])}/mês")
    return "\n".join(L)


def _r_perf_linha(x):
    tira = [t for t in x["tira"] if (t[1] or 0) > 0]
    partes = [f"{_IND.get(k, k)} {_valor_ind(k, v)} → nota {num(n, 1)} (peso {num(w)}, tira {num(p, 2)} pts)"
              for k, p, n, v, w in x["tira"]]
    rank = f"#{x['posicao']}" if x.get("posicao") is not None else "sem classificação"
    s = (f"- {rank} {x['codusur']} {x['nome']} ({x['time'] or 's/ time'}): nota {num(x['nota'], 2)}"
         f"{' PARCIAL (peso medido ' + pct(x['peso_medido'], 0) + ')' if x['parcial'] else ''}")
    if tira:
        s += f" · o que mais tira nota: {_IND.get(tira[0][0], tira[0][0])}"
    s += "\n    " + " | ".join(partes)
    if x["sem_medida"]:
        s += "\n    sem meta/sem medida (não tiram ponto): " + ", ".join(_IND.get(k, k) for k in x["sem_medida"])
    return s


def _r_performance(b):
    pesos = ", ".join(f"{_IND.get(k, k)} {num(v)}" for k, v in (b["pesos"] or {}).items())
    L = [f"== PERFORMANCE COMERCIAL — nota 0–10 (mês fechado {anomes(b['mes'])}) ==",
         f"Pesos vigentes: {pesos}. Metas e cobertura (meta de 100%) pontuam nas faixas < 85% → 0 · 85–89,99% → "
         f"6 a 7 · 90–99,99% → 9 · 100% → 10. Cobertura = % da carteira ativa (12 m) positivada em 60 d até o fim "
         f"do mês, por qualquer vendedor (régua única)."]
    if b.get("cobertura_informativa"):
        L.append(f"⚠️ COBERTURA INFORMATIVA até {anomes(b.get('cobertura_na_nota_desde'))} (mês de limpeza): a nota "
                 "dela aparece, mas NÃO entra na nota final.")
    if b.get("sem_notas"):
        L.append(f"⚠️ NENHUM vendedor tem nota em {anomes(b['mes'])} ({num(b.get('sem_nota'))} vendedores sem nota): "
                 "faltam as metas de rentabilidade/receita/mix no Metas e"
                 + (" a cobertura está informativa;" if b.get("cobertura_informativa") else "")
                 + " o que sobra medido fica abaixo do peso mínimo de 35%. Diga isso — NÃO invente ranking nem "
                   "média de time. Próximo passo: cadastrar as metas do mês no Metas.")
    for un, u in b["universos"].items():
        L.append(f"Universo {un.upper()}: {num(u['no_escopo'])} com nota no seu escopo"
                 f" (de {num(u['no_universo'])} no universo) · {num(u['parciais'])} com nota PARCIAL")
        L.append("Topo:")
        L += [_r_perf_linha(x) for x in u["topo"]]
        if u["fundo"]:
            L.append("Fundo:")
            L += [_r_perf_linha(x) for x in u["fundo"]]
    return "\n".join(L)


def _r_gerencial(b):
    e = b["empresa"]
    ab = b.get("abaixo_do_limiar") or {}
    L = [f"== COBERTURA DA CARTEIRA — Gerencial, HOJE (carteira ATIVA de 12 meses de cadastro; positivado = comprou, "
         f"de qualquer vendedor, em ≤ {b['coberto_dias']} d) ==",
         f"Escopo: {num(e.get('total_clientes'))} clientes · cobertura por clientes {pct(e.get('cobertura_clientes'))} · "
         f"por valor {pct(e.get('cobertura_valor'))} · dentro do ciclo {pct(e.get('cobertura_ciclo'))} · "
         f"base morta (91+ d) {num(e.get('base_morta'))} · receita perdida ACUMULADA {brl(e.get('receita_em_risco'))}",
         f"MEDIANA ENTRE VENDEDORES (cada vendedor conta 1; é OUTRO número, não a cobertura do escopo acima): "
         f"{pct(b['mediana_pessoas'])} — sobre {num(b['n_pessoas'])} vendedores (só pessoas, sem fictício/canal/"
         f"amostra pequena)",
         f"Limiar {num(b['limiar_pct'])}% (meta 100%; abaixo dele a nota da cobertura é 0) → abaixo: "
         f"{num(ab.get('times'))} times e {num(ab.get('vendedores'))} vendedores (ponto de partida do mês de limpeza) — "
         f"cite pelo código os que estão mais longe (lista abaixo), não só a contagem."]
    if b["times_piores"] and b["n_times"] > 1:
        L.append("Cobertura de CADA time (do pior para o melhor; compare time com time ou com o escopo acima — "
                 "a mediana é de VENDEDOR, não de time):")
        L += [f"- {t['cod']} {t['nome']}: {pct(t['cobertura_clientes'])} dos clientes · {pct(t['cobertura_valor'])} do valor "
              f"({num(t['total_clientes'])} clientes)" for t in b["times_piores"]]
    bq = b.get("bloqueados_com_carteira") or {}
    if bq.get("vendedores"):
        L.append(f"RCAs BLOQUEADOS no cadastro que ainda têm carteira: {num(bq['vendedores'])} códigos, "
                 f"{num(bq['clientes'])} clientes — é carteira a TRANSFERIR, não vendedor cobrindo mal: "
                 + "; ".join(f"{v['cod']} {v['nome']} ({num(v['total_clientes'])} clientes)" for v in bq["lista"]))
    if b["vendedores_piores"]:
        L.append("Vendedores ATIVOS com menor cobertura por clientes:")
        L += [f"- {v['cod']} {v['nome']} ({v['time'] or 's/ time'}): {pct(v['cobertura_clientes'])} dos clientes · "
              f"{pct(v['cobertura_valor'])} do valor ({num(v['total_clientes'])} clientes)"
              + (" — 0%: nenhum cliente comprou em 30 d; confirmar se é RCA novo ou carteira parada antes de cobrar"
                 if not _f(v['cobertura_clientes']) else "") for v in b["vendedores_piores"]]
    return "\n".join(L)


def _r_metas(b):
    d = b["dias"]
    L = [f"== METAS DO MÊS CORRENTE {b['mes']:02d}/{b['ano']} — EM ANDAMENTO (régua de VENDA/pedidos; dia útil "
         f"{num(d.get('decorridos'))} de {num(d.get('mes'))}, faltam {num(d.get('restantes'))}) ==",
         f"⚠️ Todo 'realizado' abaixo é de {b['mes']:02d}/{b['ano']} ATÉ HOJE (mês corrente, incompleto) — NÃO é "
         f"o mês fechado."]
    if not b["estavel"]:
        L.append(f"⚠️ PROJEÇÃO INSTÁVEL: dia útil {num(d.get('decorridos'))} de {num(d.get('mes'))} (menos de 1/3 do mês). "
                 "NÃO aponte quem vai ou não vai bater a meta; fale só do realizado até agora.")
    if not b["tem_meta"]:
        L.append("Nenhuma meta cadastrada neste mês para o escopo: os % saem como 'sem meta' (não é 0%).")
    t = b["total"]
    L += [_fmt_metrica("Venda", t.get("venda"), True), _fmt_metrica("Rentabilidade (lucro)", t.get("rentabilidade"), True),
          _fmt_metrica("Clientes (distintos)", t.get("clientes")), _fmt_metrica("Mix (distinto)", t.get("mix"))]
    if t.get("margem") is not None:
        L.append(f"Margem (lucro ÷ realizado BRUTO): {pct(t.get('margem'))}")
    if b["times"] and len(b["times"]) > 1:
        L.append("Venda por time (pior projeção primeiro):")
        L += [f"- {x['cod']} {x['nome']}: " + _fmt_metrica("venda", x["venda"], True) for x in b["times"]]
    if b["estavel"] and b["tem_meta"]:
        if b["times_nao_batem"]:
            L.append("TIMES que NÃO vão bater a meta de venda na projeção:")
            L += [f"- {x['cod']} {x['nome']}: projeção {pct(x['venda'].get('pct_projecao'))} da meta, "
                  f"falta {brl(x['venda'].get('falta'))}" for x in b["times_nao_batem"]]
        if b["vendedores_nao_batem"]:
            L.append("VENDEDORES que NÃO vão bater a meta de venda (piores projeções):")
            L += [f"- {x['cod']} {x['nome']} (time {x['time']}): projeção {pct(x['venda'].get('pct_projecao'))}, "
                  f"falta {brl(x['venda'].get('falta'))}, necessidade {brl(x['venda'].get('necessidade_dia'))}/dia útil"
                  for x in b["vendedores_nao_batem"]]
    return "\n".join(L)


def _r_positivacao(b):
    fe = b["fechado"]
    L = [f"== POSITIVAÇÃO POR CLASSE ABC DE CLIENTE (Carteira; comprou de QUALQUER vendedor; régua de CADASTRO) ==",
         f"Regra: {b.get('regra')}",
         f"Mês fechado {anomes(b['mes_fechado'])}: " + " · ".join(
             f"{k} {pct((fe.get(k) or {}).get('taxa'))} ({num((fe.get(k) or {}).get('positivados'))} de {num((fe.get(k) or {}).get('base'))} "
             f"compraram; {num((_f((fe.get(k) or {}).get('base')) or 0) - (_f((fe.get(k) or {}).get('positivados')) or 0))} não compraram)"
             for k in ("A", "B", "C"))]
    pa = b.get("parcial") or {}
    if pa.get("atual"):
        at, an = pa["atual"], pa.get("anterior_mesmo_dia") or {}
        L.append(f"Mês corrente {anomes(pa.get('mes'))} até o dia {pa.get('dia')} × mesmo dia do mês anterior: " + " · ".join(
            f"{k} {pct((at.get(k) or {}).get('taxa'))} × {pct((an.get(k) or {}).get('taxa'))}" for k in ("A", "B", "C")))
    return "\n".join(L)


def _r_cadastro(b):
    if not b["vendedores"]:
        return ""
    L = [f"== CADASTRO A TRANSFERIR (fora da base por código FICTÍCIO ou RCA INATIVO — é cadastro, não desempenho) ==",
         f"{num(b['total'])} vendedores atenderam clientes cujo cadastro está num fictício ou num RCA inativo:"]
    L += [f"- {v['cod']} {v['nome']}: {num(v['ficticio'])} de fictício, {num(v['rca_inativo'])} de RCA inativo "
          f"(mês {anomes(v['mes'])})" for v in b["vendedores"]]
    return "\n".join(L)


_RENDER = (("recuperacao", _r_recuperacao), ("performance", _r_performance),
           ("performance_times", _r_performance_times), ("gerencial", _r_gerencial),
           ("metas", _r_metas), ("positivacao", _r_positivacao), ("cadastro", _r_cadastro), ("yoy", _r_yoy),
           ("produtos_queda", _r_produtos_queda), ("clientes_queda", _r_clientes_queda))

# rótulo do ÍNDICE → o cabeçalho que prova que o bloco renderizou
_INDICE = {"recuperacao": "carteira em risco × recuperada e dinheiro na mesa",
           "performance": "nota da Performance (topo e fundo por universo)",
           "gerencial": "cobertura da carteira (Gerencial) com a mediana",
           "metas": "metas do mês", "positivacao": "positivação por classe ABC",
           "cadastro": "cadastro a transferir", "yoy": "vendedores × ano passado (quedas e altas)",
           "performance_times": "performance por time (média das notas)",
           "produtos_queda": "produtos em queda (Radar)",
           "clientes_queda": "clientes em queda (parados e departamento abandonado)"}


def _perfil_txt(p):
    role = p.get("role")
    if role in ("admin", "viewer"):
        esc = "EMPRESA inteira + todos os times"
    elif role == "supervisor":
        esc = "time(s) " + ", ".join(f"{t.get('cod')} {t.get('nome') or ''}".strip() for t in p.get("times") or []) \
            if p.get("times") else "o(s) time(s) dele"
    else:
        esc = f"somente o vendedor {p.get('codusur')} (a própria carteira)"
    return (f"== QUEM PERGUNTA ==\nPerfil: {role} ({p.get('nome') or '—'}) · escopo de acesso: {esc}\n"
            f"Hoje (dado): {p.get('hoje') or '—'}. Os números abaixo JÁ estão recortados neste escopo.")


def renderizar(ctx):
    """Texto rotulado. Cada bloco só entra se renderizou conteúdo — e é daí que sai o índice."""
    partes = [_perfil_txt(ctx.get("perfil") or {})]
    for k, fn in _RENDER:
        b = ctx.get(k)
        if not b:
            continue
        try:
            t = fn(b)
        except Exception as e:                                    # noqa: BLE001
            t = ""
            print(f"[ia-comercial] bloco {k} não renderizou ({e}).")
        if t:
            partes.append(t)
    return "\n\n".join(partes)


def indice(ctx, texto=None):
    """O que o agente TEM, derivado do texto RENDERIZADO (lição do Compras: pilar vazio anunciado
    faz o agente dizer que o painel não tem algo que ele tem — ou prometer o que não tem)."""
    texto = texto if texto is not None else renderizar(ctx)
    marcas = {"recuperacao": "== RECUPERAÇÃO", "performance": "== PERFORMANCE COMERCIAL", "gerencial": "== COBERTURA DA CARTEIRA",
              "metas": "== METAS DO MÊS", "positivacao": "== POSITIVAÇÃO POR CLASSE", "cadastro": "== CADASTRO A TRANSFERIR",
              "yoy": "== VENDEDORES × ANO PASSADO", "performance_times": "== PERFORMANCE POR TIME",
              "produtos_queda": "== PRODUTOS EM QUEDA", "clientes_queda": "== CLIENTES EM QUEDA"}
    return [_INDICE[k] for k, m in marcas.items() if m in texto]


def system_prompt(ctx, tela=None, filtros=None):
    txt = renderizar(ctx)
    idx = indice(ctx, txt)
    fora = ctx.get("indisponivel") or []
    tela_txt = f"A pessoa está na tela `{tela}`." if tela else "Tela não informada."
    if filtros:
        tela_txt += (" Filtros da tela: " + ", ".join(f"{k}={v}" for k, v in filtros.items())
                     + ". ⚠️ O panorama abaixo é do ESCOPO INTEIRO da pessoa, não do filtro da tela — "
                       "se a pergunta for sobre o recorte filtrado, use a consulta de vendedor/time/cliente "
                       "ou diga que o número é do escopo inteiro.")
    ind = ("Você tem no panorama: " + "; ".join(idx) + ". E as consultas vendedor, time e cliente."
           if idx else "O panorama veio vazio: use as consultas vendedor, time e cliente.")
    if fora:
        ind += " Fora do ar agora (não prometa): " + ", ".join(fora) + "."
    return (f"{REGRAS}\n{GLOSSARIO}\n== CONTEXTO DA TELA ==\n{tela_txt}\n\n== ÍNDICE ==\n{ind}\n\n"
            f"DADOS ATUAIS DO PAINEL (panorama de gestão):\n{txt}\n")


# ── sugestões (camada 3) ──────────────────────────────────────────────────────────────────────
# Feedback do Gabriel no 1º teste (26/09/2026): a sugestão com o NOME de uma pessoa ("por que 1449
# VALDELI…") fica repetindo sempre a mesma, e "Explique este número:" é genérica demais. Hoje:
# · as de PERFIL falam do GRUPO (empresa, time, carteira) — perguntas que um gestor faz toda semana;
# · a "explique" é uma por TELA, sobre a RÉGUA daquela tela (com os números dela quando cabe).
_SUG_PERFIL = {
    "gestao": [("Qual plano de ação você recomenda para meus supervisores?", None),
               ("Quais times estão piores e em quê?", "gerencial"),
               ("Como está a performance de cada time?", "performance_times"),
               ("Onde a empresa perde dinheiro?", "recuperacao"),
               ("Quais vendedores mais caíram contra o ano passado?", "yoy"),
               ("Quais clientes estão em queda?", "clientes_queda"),
               ("Quais produtos estão em queda?", "produtos_queda"),
               ("Quem precisa de atenção esta semana?", "performance"),
               ("Vamos bater o mês? Onde falta?", "metas_com_meta"),
               ("Quem tem cadastro a transferir?", "cadastro_com_linhas")],
    "supervisor": [("Quais vendedores meus precisam de atenção esta semana?", None),
                   ("Como está a performance do meu time?", "performance_times"),
                   ("Quais clientes do meu time estão em queda?", "clientes_queda"),
                   ("Quais produtos estão em queda no meu time?", "produtos_queda"),
                   ("O que faço primeiro para recuperar clientes?", "recuperacao"),
                   ("Onde meu time perde dinheiro?", "recuperacao"),
                   ("Quem do meu time está no fim da Performance e por quê?", "performance"),
                   ("Meu time vai bater a meta?", "metas_com_meta"),
                   ("Quem do meu time mais caiu contra o ano passado?", "yoy")],
    "vendedor": [("Por que minha nota da Performance é essa e o que mais a derruba?", "performance"),
                 ("Como está minha cobertura nas três réguas?", None),
                 ("Onde estou perdendo dinheiro?", "recuperacao"),
                 ("Vou bater a meta do mês?", "metas_com_meta")],
}


def _sug_tela(ctx, tela):
    ger = (ctx.get("gerencial") or {}).get("empresa") or {}
    t = {
        "/performance": ("Como a cobertura da carteira entra na nota?", "performance"),
        "/recuperacao": ("O que é o dinheiro na mesa e de onde ele sai?", "recuperacao"),
        "/gerencial": ((f"Por que a cobertura por valor é {pct(ger.get('cobertura_valor'))} e por cliente "
                        f"{pct(ger.get('cobertura_clientes'))}?") if ger else None, "gerencial"),
        "/carteira": ("Nossa positivação de clientes A está caindo?", "positivacao"),
        "/vendedores": ("Quem tem cadastro a transferir?", "cadastro_com_linhas"),
        "/metas": ("Vamos bater o mês? Onde falta?", "metas_com_meta"),
    }.get(tela)
    return [t] if t and t[0] else []


def _tem(ctx, req):
    if req is None:
        return True
    if req == "metas_com_meta":
        return bool((ctx.get("metas") or {}).get("tem_meta"))
    if req == "cadastro_com_linhas":
        return bool((ctx.get("cadastro") or {}).get("vendedores"))
    if req == "yoy":
        b = ctx.get("yoy") or {}
        return bool(b.get("quedas"))
    return bool(ctx.get(req))


MAX_SUGESTOES = 8


def sugestoes(ctx, tela=None):
    """A da TELA primeiro (a régua daquela tela), depois as do PERFIL. Sugestão que depende de bloco
    vazio NÃO aparece: convidar para a pergunta que ele vai ter de recusar frustra no 1º clique."""
    role = (ctx.get("perfil") or {}).get("role")
    chave = "gestao" if role in ("admin", "viewer") else ("supervisor" if role == "supervisor" else "vendedor")
    out, vistas = [], set()
    for q, req in _sug_tela(ctx, tela) + _SUG_PERFIL[chave]:
        if q not in vistas and _tem(ctx, req):
            vistas.add(q)
            out.append(q)
    return out[:MAX_SUGESTOES]


# ── consultas sob demanda (camada 2) ───────────────────────────────────────────────────────────
def _tool(nome, desc, param_desc):
    return {"type": "function", "function": {
        "name": nome, "description": desc,
        "parameters": {"type": "object", "properties": {"busca": {"type": "string", "description": param_desc}},
                       "required": ["busca"], "additionalProperties": False}}}


TOOLS = [
    _tool("vendedor", "Detalhe de UM vendedor (RCA) no escopo da pessoa: a cobertura da carteira (régua única) "
          "no fim do mês fechado e hoje, fora da "
          "base por tipo, alcance, mix, nota da Performance com cada indicador, metas do mês e recuperação.",
          "código do vendedor (ex.: '29') ou parte do nome"),
    _tool("time", "Detalhe de UM time (supervisor) no escopo: recuperação, cobertura do Gerencial, metas "
          "(e quem não vai bater) e topo/fundo da nota dos vendedores do time.",
          "código do supervisor/time (ex.: '17') ou parte do nome"),
    _tool("cliente", "Detalhe de UM cliente no escopo: segmento, classe ABC, ciclo, dias sem comprar, "
          "venda 12m, histórico mensal, top produtos, departamentos e produtos que parou, situação na "
          "Recuperação e no Próximo Pedido.", "código do cliente ou parte do nome/fantasia"),
]


def resolver(busca, itens, chave_cod="cod", chave_nome="nome", limite=5):
    """(escolhido | None, candidatos). Número → código EXATO (senão prefixo); texto → trecho do nome.
    Só dentro de `itens`, que já é o ESCOPO da pessoa — a resolução nunca amplia o acesso."""
    q = (str(busca or "")).strip().lower().lstrip("#").strip()
    if not q:
        return None, []
    # O prompt exige "código + nome" em toda menção, e o modelo passa a BUSCAR assim também
    # ("198 ANA TEIXEIRA PINTO"). Sem isto a busca por texto não achava nada e o agente recusava
    # um vendedor do próprio escopo (1º ensaio com o modelo, 26/09/2026). Código na frente manda.
    m = re.match(r"^(\d+)\b\s*[-–—:]?\s*(.*)$", q)
    if m and m.group(2):
        cod = m.group(1)
        exato = [i for i in itens if str(i.get(chave_cod)) == cod]
        if exato:
            return exato[0], []
        q = m.group(2).strip()
    if q.isdigit():
        exato = [i for i in itens if str(i.get(chave_cod)) == q]
        if exato:
            return exato[0], []
        # ⚠️ Código NUNCA casa por prefixo sozinho: o supervisor do time 12 pedindo o time "1"
        # recebia os dados do 12 (único candidato por prefixo). Prefixo só vira PERGUNTA.
        return None, [i for i in itens if str(i.get(chave_cod) or "").startswith(q)][:limite]
    else:
        cands = [i for i in itens if q in (i.get(chave_nome) or "").lower()]
        exato = [i for i in cands if (i.get(chave_nome) or "").lower() == q]
        if len(exato) == 1:
            return exato[0], []
    if len(cands) == 1:
        return cands[0], []
    # "juliano" casa 4 RCAs, 3 bloqueados: o gestor quer o ativo. Um único ativo entre vários
    # candidatos é escolhido; os inativos seguem fora (no Multpel real, 25/09: 29 × 1507/826/734).
    ativos = [i for i in cands if not i.get("inativo")]
    if len(ativos) == 1 and len(cands) > 1:
        return ativos[0], []
    return None, cands[:limite]


def texto_candidatos(tipo, busca, cands):
    if not cands:
        return (f"CONSULTA {tipo}('{busca}'): NÃO ENCONTRADO. Diga que não achou esse {tipo} e peça o código "
                f"ou outra parte do nome. NÃO diga que está fora do escopo (não é o que aconteceu).")
    return (f"CONSULTA {tipo}('{busca}'): mais de um candidato no escopo — PERGUNTE qual:\n"
            + "\n".join(f"- {c.get('cod')} {c.get('nome')}" + (f" ({c['extra']})" if c.get("extra") else "") for c in cands))


def texto_fora_escopo(tipo, busca):
    return (f"CONSULTA {tipo}('{busca}'): FORA DO ESCOPO de acesso desta pessoa. Responda que não pode "
            f"mostrar esse {tipo} e não especule sobre ele.")


def consulta_vendedor(cod, nome, vend=None, perf=None, perf_meta=None, ger=None, rec=None, metas=None,
                      metas_dias=None, indisponivel=(), time=None):
    """Texto do detalhe de UM vendedor. Cada peça é a linha dele na rota da tela (ou None).
    `time` = (código, nome) do supervisor no cadastro — sem ele o modelo "adivinhou" o time a
    partir de outra lista do panorama (1º ensaio: pôs a pessoa no time de maior risco)."""
    L = [f"CONSULTA vendedor {cod} {nome}"]
    if time and time[0] is not None:
        L.append(f"Time (cadastro): {time[0]} {time[1] or ''}".rstrip())
    k = (vend or {}).get("kpis") or {}
    d = (perf or {}).get("detalhe") or {}
    L.append("COBERTURA DA CARTEIRA (régua única: carteira ativa de 12 meses, positivada por qualquer vendedor em 60 d):")
    if perf and (perf.get("valores") or {}).get("cobertura") is not None:
        info = "cobertura" in (perf.get("informativos") or [])
        L.append(f"  - fim do mês fechado {anomes((perf_meta or {}).get('mes'))}: {pct((perf.get('valores') or {}).get('cobertura'))} "
                 f"({num(d.get('cobertos'))} de {num(d.get('base_ativa'))}) → nota {num((perf.get('notas') or {}).get('cobertura'), 1)}"
                 + (f" — INFORMATIVA até {anomes((perf_meta or {}).get('cobertura_na_nota_desde'))}: não entra na nota final"
                    if info else " — entra na nota"))
    elif vend and k.get("taxa_positivacao") is not None:
        L.append(f"  - fim do mês fechado {anomes(k.get('positivacao_mes'))}: {pct(k.get('taxa_positivacao'))} "
                 f"({num(k.get('base_coberta'))} de {num(k.get('base_ativa'))})")
    if ger:
        L.append(f"  - hoje (Gerencial, {num(ger.get('_coberto_dias', 60))} d): {pct(ger.get('cobertura_clientes'))} "
                 f"({num(ger.get('clientes_cobertos'))} de {num(ger.get('total_clientes'))}) · {pct(ger.get('cobertura_valor'))} "
                 f"do valor · base morta {num(ger.get('base_morta'))} · receita perdida ACUMULADA {brl(ger.get('receita_em_risco'))}")
    if vend:
        L.append(f"Alcance (tudo que ELE atendeu no mês ÷ carteira) {pct(k.get('alcance'))} · fora da base {num(k.get('fora_base'))}")
    if vend:
        t = k.get("fora_base_tipo") or {}
        L.append(f"Fora da base por tipo: colega {num(t.get('colega'))} · liberado {num(t.get('liberado'))} · "
                 f"fictício {num(t.get('ficticio'))} · RCA inativo {num(t.get('rca_inativo'))} "
                 f"(fictício/inativo = cadastro a transferir, não desempenho)")
        L.append(f"Venda líquida 12m {brl(k.get('venda_liq'))} · lucro 12m {brl(k.get('lucro'))} · "
                 f"YoY receita {pct(k.get('yoy_receita'))} · ranking por lucro {num(k.get('rank'))}")
        cmp_ = vend.get("comparativo_equipe") or {}
        if cmp_.get("media_equipe") is not None:
            L.append(f"Cobertura da carteira × equipe: {pct(cmp_.get('sua_taxa'))} × média {pct(cmp_.get('media_equipe'))} "
                     f"({cmp_.get('label')})")
        cart = vend.get("carteira") or {}
        if cart:
            L.append(f"Carteira (cockpit, 24m): {num(cart.get('cadastrados'))} cadastrados · {num(cart.get('positivados'))} "
                     f"'positivados' em 12m (não é taxa mensal) · champions {num(cart.get('champions'))} · "
                     f"at_risk (RFM) {num(cart.get('at_risk'))}")
    if perf:
        pesos = (perf_meta or {}).get("pesos") or {}
        tira, sem = pontos_por_indicador(perf, pesos)
        posicao = (f"posição {num(perf.get('posicao'))} no universo {perf.get('universo')}"
                   if perf.get("posicao") is not None else
                   f"SEM CLASSIFICAÇÃO no universo {perf.get('universo')} (nota parcial não disputa posição)")
        L.append(f"NOTA DA PERFORMANCE: {num(perf.get('nota'), 2)} · {posicao}"
                 + (f" · PARCIAL (peso medido {pct(perf.get('peso_medido'), 0)})" if perf.get("parcial") else ""))
        for kk, p, n, v, w in tira:
            L.append(f"  - {_IND.get(kk, kk)}: {_valor_ind(kk, v)} → nota {num(n, 1)} · peso {num(w)} · tira {num(p, 2)} pts")
        if sem:
            L.append("  - sem meta/sem medida (não tiram ponto): " + ", ".join(_IND.get(x, x) for x in sem))
        L.append(f"  base ativa {num(d.get('base_ativa'))} · clientes atendidos {num(d.get('clientes_atendidos'))} · "
                 f"mix médio {num(d.get('mix_medio'), 1)}")
    if metas:
        dias = metas_dias or {}
        L.append(f"METAS DO MÊS (dia útil {num(dias.get('decorridos'))} de {num(dias.get('mes'))}"
                 + ("" if metas_estaveis(dias) else "; projeção INSTÁVEL — não diga se vai bater") + "):")
        L += ["  " + _fmt_metrica("Venda", metas.get("venda"), True),
              "  " + _fmt_metrica("Rentabilidade", metas.get("rentabilidade"), True),
              "  " + _fmt_metrica("Clientes", metas.get("clientes")), "  " + _fmt_metrica("Mix", metas.get("mix"))]
    if rec:
        L.append(f"RECUPERAÇÃO (mês fechado, régua de cadastro): {num(rec.get('em_risco'))} clientes em risco, "
                 f"{brl(rec.get('valor_em_risco'))}/mês · recuperados da base {num(rec.get('rec_da_base'))} "
                 f"({brl(rec.get('venda_rec_da_base'))}) · por ele {num(rec.get('rec_por_ele'))} "
                 f"({brl(rec.get('venda_rec_por_ele'))}) · de outra base {num(rec.get('rec_de_outra_base'))} · "
                 f"potencial de positivação {brl(rec.get('potencial_positivacao'))}/mês")
    if indisponivel:
        L.append("Indisponível agora: " + ", ".join(indisponivel))
    return "\n".join(L)


def consulta_time(cod, nome, ger=None, rec=None, metas=None, metas_dias=None, metas_vend=None,
                  perf_linhas=None, perf_meta=None, mediana=None, indisponivel=()):
    L = [f"CONSULTA time {cod} {nome}"]
    if ger:
        L.append(f"COBERTURA DA CARTEIRA hoje (Gerencial, régua única): {pct(ger.get('cobertura_clientes'))} dos clientes · "
                 f"{pct(ger.get('cobertura_valor'))} do valor · {num(ger.get('total_clientes'))} clientes · base morta "
                 f"{num(ger.get('base_morta'))} · receita perdida ACUMULADA {brl(ger.get('receita_em_risco'))}"
                 + (f" · mediana dos vendedores da empresa/escopo: {pct(mediana)}" if mediana is not None else ""))
    if rec:
        L.append(f"RECUPERAÇÃO (mês fechado): {num(rec.get('em_risco'))} em risco, {brl(rec.get('valor_em_risco'))}/mês · "
                 f"recuperados DA BASE do time {num(rec.get('rec_da_base'))} ({brl(rec.get('venda_rec_da_base'))}) · "
                 f"POR ELES {num(rec.get('rec_por_ele'))} ({brl(rec.get('venda_rec_por_ele'))}) · de outra base "
                 f"{num(rec.get('rec_de_outra_base'))} · potencial de positivação {brl(rec.get('potencial_positivacao'))}/mês")
    if metas:
        dias = metas_dias or {}
        est = metas_estaveis(dias)
        L.append(f"METAS (dia útil {num(dias.get('decorridos'))} de {num(dias.get('mes'))}"
                 + ("" if est else "; projeção INSTÁVEL — não diga quem vai bater") + "): "
                 + _fmt_metrica("venda", metas.get("venda"), True))
        if est:
            nb = [v for v in (metas_vend or []) if _f((v.get("venda") or {}).get("pct_projecao")) is not None
                  and _f(v["venda"]["pct_projecao"]) < 1]
            nb.sort(key=lambda v: _f(v["venda"]["pct_projecao"]))
            if nb:
                L.append("Vendedores do time que NÃO vão bater a venda: " + "; ".join(
                    f"{v.get('codusur')} {v.get('nome')} {pct(v['venda'].get('pct_projecao'))}" for v in nb[:8]))
    if perf_linhas:
        pesos = (perf_meta or {}).get("pesos") or {}
        ls = sorted([l for l in perf_linhas if l.get("nota") is not None], key=lambda l: l.get("posicao") or 999)
        if ls:
            L.append("PERFORMANCE dos vendedores do time (mês fechado; posição no universo):")
            for l in ls[:3] + (ls[-3:] if len(ls) > 6 else ls[3:]):
                L.append(_r_perf_linha(_linha_perf(l, pesos)))
    if indisponivel:
        L.append("Indisponível agora: " + ", ".join(indisponivel))
    return "\n".join(L)


def consulta_cliente(cli, drill=None, produtos=None, deptos_parados=None, radar=None, risco=None,
                     recuperado=None, indisponivel=()):
    """Texto do detalhe de UM cliente. `cli` = a linha dele na carteira do escopo (régua de cadastro)."""
    c = cli or {}
    L = [f"CONSULTA cliente {c.get('codcli')} {c.get('cliente')} — {c.get('cidade') or ''}/{c.get('uf') or ''}",
         f"Dono (cadastro): {c.get('codusur')} {c.get('vendedor') or ''} · time {c.get('time') or '—'} · telefone "
         f"{c.get('telefone') or '—'}",
         f"Segmento RFM {c.get('segmento') or '—'} · classe ABC {c.get('classe_abc') or '—'} (início do mês) · "
         f"última compra há {num(c.get('recencia_dias'))} d · ciclo pessoal {num(c.get('ciclo_pessoal'))} d · "
         f"venda 12m {brl(c.get('venda_12m'))} · receita perdida ACUMULADA {brl(c.get('receita_perdida_proj'))}"]
    if risco:
        L.append(f"RECUPERAÇÃO: EM RISCO há {num(risco.get('dias'))} d (ciclo {num(risco.get('ciclo'))} d) · valor mensal "
                 f"{brl(risco.get('valor_mensal'))} · chance de voltar no mês {pct(risco.get('chance_volta'))}"
                 + (" · o ERP já marca inativo (91+ d)" if risco.get("inativo_erp") else ""))
    elif recuperado:
        L.append(f"RECUPERAÇÃO: RECUPERADO no mês (estava parado há {num(recuperado.get('dias_parado'))} d, "
                 f"voltou de '{recuperado.get('recuperado_de')}') · venda no mês {brl(recuperado.get('venda_mes'))} · "
                 f"quem vendeu: " + ", ".join(recuperado.get("vendedores_nomes") or [])
                 + (" · DE OUTRA BASE" if recuperado.get("de_outra_base") else ""))
    else:
        L.append("RECUPERAÇÃO: não está em risco nem entre os recuperados do mês.")
    da = _f(c.get("dias_atraso"))
    if c.get("proximo_pedido_previsto") and da is not None:
        if -7 <= da <= 15:
            L.append(f"PRÓXIMO PEDIDO: previsto para {c.get('proximo_pedido_previsto')} pelo ciclo "
                     + (f"(vencido há {num(da)} d — está na lista do Próximo Pedido)" if da >= 0
                        else f"(faltam {num(-da)} d)"))
        else:
            L.append(f"PRÓXIMO PEDIDO: previsto para {c.get('proximo_pedido_previsto')} "
                     f"({'atraso de ' + num(da) + ' d, fora da janela de até 15 d' if da > 0 else 'ainda distante'})")
    hist = (drill or {}).get("historico") or []
    if hist:
        L.append("Histórico mensal (últimos 6): " + " · ".join(
            f"{h.get('AnoMes')} {brl(h.get('VendaLiquida'))}" for h in hist[-6:]))
    dep = sorted((drill or {}).get("deptos") or [], key=lambda d: -(_f(d.get("VendaLiquida")) or 0))
    if dep:
        L.append("Top departamentos 12m: " + " · ".join(f"{d.get('codepto')} {d.get('nome')} {brl(d.get('VendaLiquida'))}"
                                                      for d in dep[:5]))
    if produtos:
        L.append("Top produtos 12m: " + " · ".join(
            f"{p.get('codprod')} {p.get('descricao') or ''} {brl(p.get('venda_12m'))}" for p in produtos[:8]))
    if deptos_parados:
        L.append("Departamentos que PAROU de comprar (≥ 60 d; o cliente segue comprando outros): " + " · ".join(
            f"{d.get('codepto')} {d.get('depto_nome') or ''} há {num(d.get('dias_parado'))} d "
            f"({brl(d.get('venda_cat_12m'))} em 12m)" for d in deptos_parados[:5]))
    if radar and radar.get("rows"):
        k = radar.get("kpis") or {}
        L.append(f"Produtos que PAROU (Radar): {num(k.get('produtos_parados'))} produtos; 'receita em risco' do Radar "
                 f"{brl(k.get('receita_em_risco'))} (venda 12m desses produtos — 3º sentido, não somar). Principais: "
                 + " · ".join(f"{p.get('codprod')} {p.get('descricao') or ''} (parado há {num(p.get('dias_parado'))} d)"
                              for p in radar["rows"][:8]))
    if indisponivel:
        L.append("Indisponível agora: " + ", ".join(indisponivel))
    return "\n".join(L)


def args_tool(raw):
    """Argumentos da ferramenta vêm como JSON em texto do modelo — tolera lixo."""
    try:
        a = json.loads(raw or "{}")
        return str(a.get("busca") or "").strip()[:80]
    except (ValueError, AttributeError):
        return ""
