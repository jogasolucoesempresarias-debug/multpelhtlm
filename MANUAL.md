# Manual Multpel Analytics

> Painel comercial inteligente. Substitui análise manual em planilha por uma ferramenta web com 7 telas conectadas, RBAC granular, atualização diária via Power BI.

**Última atualização**: 2026-05-24 · **Versão**: 1.0 (Ondas A–D concluídas)

---

## 📑 Sumário

1. [Visão Geral](#1-visão-geral)
2. [Como Funciona — conceitos-base](#2-como-funciona--conceitos-base)
3. [Tela: Dashboard Executivo (`/`)](#3-tela-dashboard-executivo-)
4. [Tela: Carteira RFM (`/carteira`)](#4-tela-carteira-rfm-carteira)
5. [Tela: Vendedores (`/vendedores`)](#5-tela-vendedores-vendedores)
6. [Tela: Cockpit Vendedor (`/vendedor/<id>`)](#6-tela-cockpit-vendedor-vendedorid)
7. [Tela: Categorias (`/categorias`)](#7-tela-categorias-categorias)
8. [Tela: Mix Abandonado (`/mix`)](#8-tela-mix-abandonado-mix)
9. [Tela: Tendências (`/tendencias`)](#9-tela-tendências-tendencias)
10. [RBAC — Quem vê o quê](#10-rbac--quem-vê-o-quê)
11. [Glossário](#11-glossário)
12. [Fontes & Refresh](#12-fontes--refresh)

---

## 1. Visão Geral

O painel responde 4 perguntas-chave da operação comercial Multpel:

| Pergunta | Telas que respondem |
|---|---|
| **"Como está o mês?"** | Dashboard (`/`) |
| **"Pra quem estamos vendendo?"** | Carteira (`/carteira`) |
| **"Quem está vendendo?"** | Vendedores + Cockpit (`/vendedores`, `/vendedor/<id>`) |
| **"O quê estamos vendendo?"** | Categorias + Mix (`/categorias`, `/mix`) |
| **"Como evolui no tempo?"** | Tendências (`/tendencias`) |

**Diferenciais vs a planilha manual antiga**:
- Atualiza automaticamente (sem precisar refazer no Excel)
- Régua personalizada por cliente (não só os 10/30/45 dias fixos)
- Segmentação RFM (8 perfis estratégicos)
- Cohort retention (vê se base nova fica)
- Mix abandonado (cross-sell automático)
- Permissão por papel (vendedor só vê o que é dele)

**Fluxo típico de uso pelo diretor/gerente**:

1. Abre o **Dashboard** → vê o mês em 30s (venda, lucro, YoY)
2. Vai pra **Carteira** → identifica os 12 clientes At Risk valendo R$ 24k/ano de lucro
3. Filtra por vendedor → manda lista pro responsável cobrar
4. Abre o **Cockpit** do vendedor pra ver desempenho individual
5. **Tendências** uma vez por semana pra acompanhar saúde da retenção

---

## 2. Como Funciona — conceitos-base

### 2.1 RFM (Recência, Frequência, Monetário)

Modelo clássico de marketing pra classificar a base de clientes em 3 dimensões dos últimos 12 meses:

| Letra | Significa | Exemplo |
|---|---|---|
| **R** | Dias desde a última compra | Cliente que comprou ontem = R alto (bom) |
| **F** | Quantas notas/compras tirou no período | 30 notas em 12m = F alto |
| **M** | Lucro gerado pra Multpel | R$ 50k de lucro = M alto |

Cada cliente recebe nota **1 a 5** em cada letra (quintis: dividimos toda a base em 5 grupos iguais). Cliente com R=5/F=5/M=5 é o melhor do melhor (Campeão).

> **Por que quintis e não thresholds absolutos?** Porque o que é "muita compra" varia por empresa. 50 compras é muito? Pra uma padaria que compra todo dia, não. Pra um hotel sazonal, sim. Quintis normalizam: você sempre tem 20% da base no top.

### 2.2 Régua FIXA vs PERSONALIZADA (Carteira)

**Régua FIXA** (replica a planilha original do cliente):
- ≤10 dias → OK
- 11–30 dias → Normal
- 31–45 dias → Atenção
- \>45 dias → Urgente

**Régua PERSONALIZADA** (diferencial do produto):
- Calcula o **ciclo médio individual** de cada cliente (mediana de intervalos entre compras nos últimos 12m, mínimo 7 dias)
- Classifica relativo ao próprio padrão dele

> **Exemplo**: Padaria X compra a cada 7 dias em média (ciclo pessoal = 7). Se estiver 14 dias sem comprar (2× o ciclo) = **Atenção**. Já a régua fixa diria "Normal" porque só passou 14 dias.
>
> **Exemplo inverso**: Hotel sazonal compra a cada 90 dias. Se estiver 30 dias sem comprar = **OK** na personalizada (ainda no padrão dele). Régua fixa diria "Normal" subestimando — ele ainda nem deveria estar comprando.

### 2.3 Cohort Retention (Tendências)

Mede se clientes novos **continuam comprando** ao longo do tempo. Pergunta concreta: *"Daqueles 121 clientes que apareceram pela primeira vez em jun/2025, quantos voltaram em jul? E em ago? E em dez?"*

Cada linha do heatmap é uma "turma" (cohort) que entrou num mês específico. Cada coluna é "meses depois" (M+0, M+1, M+2…). A cor mostra o % retido.

### 2.4 RBAC — controle de acesso

4 papéis (definidos no cadastro do usuário):

- **Admin**: vê tudo
- **Supervisor**: vê apenas vendedores do seu time (CODSUPERVISOR igual)
- **Vendedor**: vê apenas a própria carteira (CODUSUR igual)
- **Viewer**: vê tudo mas sem editar (read-only — Onda E adicionará controles de edição)

> **Garantia técnica**: o filtro é aplicado no servidor (não só no frontend). Um vendedor não consegue "burlar" digitando outra URL — recebe `403 Forbidden`. 6 testes automatizados garantem que isso não regride.

---

## 3. Tela: Dashboard Executivo (`/`)

**O que mostra**: panorama do mês corrente vs ano anterior, em 8 cards, 4 gráficos e 1 tabela.

### 3.1 Cards (linha 1) — métricas primárias

| Card | O que mede | Como interpretar |
|---|---|---|
| **Venda Líquida (mês)** | Faturamento do mês corrente | ↗ verde = cresceu vs mesmo mês ano passado |
| **Lucro Total (mês)** | Lucro bruto (venda − custo) | Se cresceu menos que venda = margem caindo, investigar |
| **Margem (%)** | Lucro / Venda | <12% = mercadoria de baixa margem ou desconto agressivo |
| **Clientes Positivados** | Clientes únicos que compraram no mês | Indicador de "alcance" do time comercial |

### 3.2 Cards (linha 2) — métricas secundárias

| Card | O que mede |
|---|---|
| **Ticket Médio** | Venda ÷ número de notas (média de cada pedido) |
| **Mix Médio** | Diversidade de produtos vendidos |
| **Clientes Novos** | Clientes que compraram pela primeira vez no mês |
| **Valor Médio / Kg** | Valor por kg vendido — relevante pra atacado de produtos pesados |

### 3.3 Gráficos

- **Série temporal 12m** (linha dupla): Venda Líquida + Lucro Total mensais. Identifica sazonalidade e tendência.
- **YoY 4 métricas** (barras): % crescimento de Receita / Lucro / Positivação / Mix vs ano anterior. Tudo verde = empresa em alta.
- **Pareto** (barra + linha cumulativa): top 30 clientes ordenados por receita 12m. A linha laranja acumula o %. Concentração típica de atacado: top 5 clientes ≈ 20%, top 30 ≈ 50–60% do faturamento.
- **Sazonalidade** (linhas sobrepostas): venda mês a mês, comparando anos. Identifica padrão sazonal (ex: dezembro sempre alto).

### 3.4 Top 10 clientes por lucro 12m

Tabela com Cliente | UF | Vendedor (nome + código RCA) | Lucro 12m | Venda 12m. Click no Pareto (qualquer barra) também abre o drill 360° desse cliente.

### 3.5 Detalhes técnicos

```
Mês corrente = MONTH(DTSAIDA) = MONTH(HOJE) && YEAR(DTSAIDA) = YEAR(HOJE)
YoY = medida [Crescimento Ano a Ano X] (pré-calculada no Power BI)
Top clientes = TOPN(10, SUMMARIZECOLUMNS por CODCLI, ordenado por [LUCRO TOTAL])
```

3 queries DAX rodam em paralelo (KPIs primários, secundários, YoY) com cache Redis de 1h. Recarregar a tela é instantâneo na segunda vez.

---

## 4. Tela: Carteira RFM (`/carteira`)

A tela mais densa e a mais valiosa. Organizada em **3 blocos**:

### 4.1 Bloco 1 — Status operacional

**Toggle FIXA / PERSONALIZADA** no header escolhe qual régua usar (ver seção 2.2).

**4 cards** ordenados por urgência:

| Card | Cor | Significado |
|---|---|---|
| **OK** | 🟢 verde | Cliente em dia (recência dentro do esperado) |
| **Normal** | 🔵 azul | Atraso leve, monitorar |
| **Atenção** | 🟡 amarelo | Risco médio, vendedor deve contatar |
| **Urgente** | 🔴 vermelho | Resgatar agora ou perder o cliente |

**Indicador OK + Normal %**: saúde geral da carteira. Referência do cliente: ~48%. Acima = carteira saudável. Abaixo = problemas de retenção.

**Donut à direita**: distribuição dos 8 segmentos canônicos (% dentro da fatia se for grande o bastante).

### 4.2 Bloco 2 — Segmentação avançada (8 segmentos RFM)

> ⓘ **Como funciona**: cada cliente recebe nota 1-5 em R, F, M (quintis) e cai num dos 8 segmentos abaixo. A explicação completa fica num `<details>` expansível na própria tela.

| Segmento | Quem é | Ação |
|---|---|---|
| **Campeões** 🟢 | R=5, F=5, M=5 (top em tudo) | Premiar, programa fidelidade, evitar concorrência |
| **Fiéis** 🔵 | R≥4, F≥4, M≥3 | Fidelizar, oferecer mais mix |
| **Não Perder** 🟠 | R=2-3, F≥4, M≥4 (alto valor histórico parando) | Ligação urgente, descobrir motivo, oferta personalizada |
| **Em Risco** 🟡 | R=2-3, F≥3, M≥3 | Resgatar com promoção, mais frequência de visita |
| **Promissores** 🟣 | R≥4, F=1-3 (compra pouco mas recente) | Cultivar, apresentar novos produtos |
| **Novos** 🟪 | R=5, F=1 (primeira compra recente) | Onboarding, garantir 2ª compra |
| **Inativos** ⚫ | R baixo, atividade fraca | Tentativa esporádica, baixa prioridade |
| **Perdidos** 🔴 | R≤2, F≤2, M≤2 (tudo baixo) | Última campanha ou aceitar perda |

**Matriz R × F** (gráfico de bolhas):
- Eixo X = R (1 a 5)
- Eixo Y = F (1 a 5)
- Tamanho da bolha = M médio dos clientes naquela célula
- **Cor da bolha = segmento dominante** daquela célula (≥70% das vezes a célula é "pura")
- Tooltip rico: *"Em Risco · R=3, F=4 · 320 clientes · M médio R$ 504 · 82% deles são Em Risco"*

### 4.3 Bloco 3 — Tabela acionável

Filtros: **Time (supervisor)** · **Vendedor** · **Segmento** · **Status régua** · **Busca livre**.

> Os dropdowns Time/Vendedor só listam quem **realmente tem cliente na carteira** (não os 739 cadastros totais do Winthor). Evita "0 resultados" frustrante.

Colunas: Cliente | Cidade/UF | Vendedor | Time | R (dias) | F (12m) | Lucro 12m | **⚠ Perdido proj.** | Status | Segmento | Telefone.

Click linha → painel lateral **drill 360°** com:
- Indicadores (última compra, ciclo, RFM scores, lucro perdido)
- Histórico mensal 12m (gráfico bar dupla)
- Top 5 departamentos comprados (com nomes reais: MATERIAL DE LIMPEZA, ALIMENTÍCIOS, etc)

**Export CSV** (botão ⬇): baixa toda a lista filtrada com BOM UTF-8 (abre no Excel com acentos OK). Inclui `Supervisor` e `CodSupervisor` no cabeçalho.

### 4.4 Detalhes técnicos

#### Ciclo pessoal
```python
def ciclo_pessoal(datas):
    intervalos = [dias_entre(d_i, d_{i+1}) for compras consecutivas]
    return max(7, mediana(intervalos))  # floor 7 dias
```

#### Status régua personalizada
```python
razao = dias_sem_comprar / ciclo_pessoal
if razao < 1.0: 'ok'
if razao < 2.0: 'normal'
if razao < 3.0: 'atencao'
else: 'urgente'
```

#### Lucro perdido projetado
```python
if dias_sem_comprar < ciclo: return 0
meses_atrasado = (dias_sem_comprar - ciclo) / 30
return (lucro_12m / 12) * meses_atrasado
```
> "Cliente que dá R$ 1.200/mês e está 90d parado com ciclo de 30d → 2 meses atrasado → R$ 2.400 projetados como perdidos."

#### DAX crítico (snapshot RFM)
```sql
EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODCLI],
    FILTER(FATURAMENTO_VENDAS, FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12)),
    "Compras12m", DISTINCTCOUNT(FATURAMENTO_VENDAS[NUMNOTA]),
    "Lucro12m",   [LUCRO TOTAL],
    "Venda12m",   [VENDA LIQUIDA]
)
```
A query é **quebrada em 2** (24m recência + 12m frequência/monetário) pra caber no limite de 1.3GB do Power BI Pro.

---

## 5. Tela: Vendedores (`/vendedores`)

**Ranking dos vendedores** com filtros operacionais.

### 5.1 Filtros (no topo)

- **Tipo** (default: R — rota/externo): R/I/P
- **Supervisor**: dropdown com nomes reais (DIRETORIA, TELEMARKETING, JOSE DE ANCHIETA, …)
- **UF**: filtro por estado de atuação
- **Busca**: nome ou código (ex: "879" pra achar JOSE JUNIOR)
- **☐ Mostrar internos**: libera vendedores I (caixa/balcão) que ficam escondidos por padrão

> Vendedores "técnicos" (códigos 999/900/4/272 = TRANSFERÊNCIA/JURÍDICO/CAIXA/MULTPEL) e bloqueados são **sempre excluídos** do ranking.

### 5.2 Gráficos

- **Top 10 por Lucro 12m** (barras horizontais): identificação rápida dos protagonistas
- **Histograma — Taxa de Positivação**: distribui vendedores em faixas (0-2%, 2-5%, 5-10%, 10-20%, 20%+). Útil pra ver se há "outliers" ou se a equipe está homogênea

### 5.3 Tabela

Colunas: # (rank) | Vendedor | Tipo | Time | UF | Venda 12m | Lucro 12m | Ticket | Positivação | Clientes únicos | YoY %.

**Gradiente verde→vermelho** nas linhas, proporcional ao lucro. Bate o olho: top decil = verde, último decil = vermelho.

Click no nome → cockpit individual.

### 5.4 Detalhes técnicos

**YoY %** é calculado em Python (não na medida DAX pré-pronta, que retorna NULL quando filtrada por CODUSUR):
```python
yoy = (venda_12m_atual - venda_12m_anterior) / venda_12m_anterior
```
3 queries DAX em paralelo: vendas 12m atual, vendas 12m anterior (-24 a -12m), métricas (ticket/positivação/clientes).

---

## 6. Tela: Cockpit Vendedor (`/vendedor/<id>`)

Visão 360° de **um** vendedor. Acessível clicando no nome em qualquer ranking, ou via URL direta.

### 6.1 Header de perfil

Nome · RCA · Tipo · Supervisor · Cidade/UF · Telefone · Admissão · Status.

> Dados vêm da tabela PCUSUARI do Winthor.

### 6.2 4 cards KPI

| Card | Métrica |
|---|---|
| **Sua Venda 12m** | Faturamento + indicador YoY ↗↘ |
| **Seu Lucro 12m** | Lucro + posição no ranking (#1 de N) |
| **Sua Carteira** | X cadastrados / Y positivados · Z Champions · W At Risk |
| **Positivação** | Sua % vs média do time (ou empresa) |

**Comparativo equipe** (3 casos):
- Se time tem **≥2 vendedores**: compara com média do mesmo supervisor (label "vs seu time (8 vendedores)")
- Se time tem **só 1 (você)**: compara com média da empresa toda (label "Sua equipe tem só você. Vs média da empresa")
- Se time tem **0**: omite o card de comparativo

### 6.3 ⚠ Alertas acionáveis

Calculados sem query DAX adicional (filtra a carteira já cacheada):

- **At Risk**: "12 clientes At Risk somam R$ 24.500/ano de lucro em risco — ligue."
- **Top 3 Champions**: lista os 3 maiores clientes Campeões da carteira, **clicáveis** → abre drill 360° do cliente.

### 6.4 3 gráficos

- **Série mensal 12m** (Venda + Lucro)
- **Donut RFM da carteira** (Champions / At Risk / Outros)
- **Distribuição de Status** (régua personalizada)

### 6.5 Tabela carteira filtrada

Mesma da Carteira principal mas restrita ao CODUSUR. Filtros: segmento + status. Export CSV próprio do vendedor.

---

## 7. Tela: Categorias (`/categorias`)

### 7.1 O que mostra

Análise de produto **por departamento** (33 deptos com nomes textuais: MATERIAL DE LIMPEZA, ALIMENTÍCIOS, EMBALAGENS, ISOPOR, …).

### 7.2 Treemap (shelf layout)

3 fileiras de altura proporcional ao acumulado:
- **Fileira 1 (50% altura)**: top 2 deptos
- **Fileira 2 (30%)**: deptos 3–6
- **Fileira 3 (20%)**: deptos 7–12

Largura de cada célula = proporcional à venda. **Cor da célula** = margem (verde alta margem → vermelho baixa).

Click célula → painel lateral com top 30 clientes desse departamento.

### 7.3 Top 10 fornecedores

Gráfico de barras horizontais com nomes reais (BOMBRIL SA, PREDILECTA, GALVANOTEK, CRISTALCOPO, …).

### 7.4 Tabela detalhada

Todos os deptos com Venda | Lucro | Margem % | Share | Clientes únicos | Produtos únicos. Click linha → mesmo drill.

### 7.5 Por que "Departamento" e não "Categoria"?

> **Decisão técnica importante**: a coluna CODCATEGORIA do banco tem **92% NULL** em FATURAMENTO_VENDAS (R$ 78M de R$ 85M sem categoria classificada). A coluna CODEPTO está **100% preenchida** com 33 valores distintos e tem **nomes textuais** disponíveis via tabela auxiliar.
>
> O termo "Categoria" foi mantido na UI porque o usuário entende — mas internamente o sistema usa CODEPTO. Quando/se o Winthor passar a classificar produtos por categoria, podemos importar e trocar sem mudar UI.

### 7.6 Detalhes técnicos

```sql
EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODEPTO],
    FILTER(FATURAMENTO_VENDAS, FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12)),
    "VendaLiquida",  [VENDA LIQUIDA],
    "LucroTotal",    [LUCRO TOTAL],
    "ClientesUnicos", DISTINCTCOUNT(FATURAMENTO_VENDAS[CODCLI]),
    "ProdutosUnicos", DISTINCTCOUNT(FATURAMENTO_VENDAS[CODPROD])
)
```
Nomes vêm de uma 2ª query em FATURAMENTO_DEVOLUCAO[DEPARTAMENTO] (tabela auxiliar que tem o nome textual), cacheada 24h.

---

## 8. Tela: Mix Abandonado (`/mix`)

### 8.1 O que mostra

Pares **Cliente × Departamento** que indicam **cross-sell perdido**: clientes que compravam o departamento X nos últimos 12 meses mas pararam nos últimos 60 dias (configurável).

### 8.2 Filtros

- **Período**: 30 / 60 / 90 / 180 dias sem comprar
- **Departamento**: foco numa categoria específica
- **Fornecedor**: ex: "quem parou de comprar produtos BOMBRIL?"
- **Vendedor / Busca**: filtros locais (sem nova consulta)

### 8.3 KPI cards

| Card | Significado |
|---|---|
| **Clientes × Depto parados** | Quantidade total de pares (1 cliente pode parar de comprar 3 deptos → 3 pares) |
| **Lucro 12m em risco** | Soma do lucro que esses pares geraram nos últimos 12m (potencial perdido se não resgatar) |
| **Top 5 maiores perdas** | Lucro acumulado dos 5 maiores pares + nomes |

### 8.4 Tabela

Cliente | Cidade/UF | Departamento | Última compra | **Dias parado** (badge colorido: amarelo 30-60, laranja 60-90, vermelho 90+) | Lucro Cat 12m | Vendedor.

### 8.5 Caso de uso típico

> Diretor: "Quantos clientes que pegavam Laticínios da Predilecta estão parados há mais de 90d?"
>
> Filtros: Período=90, Fornecedor=PREDILECTA → tabela com X clientes ordenados por lucro perdido. Envia lista pro time comercial cobrar.

---

## 9. Tela: Tendências (`/tendencias`)

### 9.1 O que mostra

**Cohort Retention Heatmap** — gráfico mais técnico do painel. Mede se a base nova continua comprando ao longo do tempo.

### 9.2 Como ler

Cada **linha** = um "cohort" (turma de clientes que apareceu pela primeira vez naquele mês).
Cada **coluna** = quantos meses se passaram (M+0 = mês de aquisição, M+1 = mês seguinte, M+12 = 1 ano depois).
Cada **célula** = % daqueles clientes que voltaram a comprar **NAQUELE mês específico**.

**Cores**:
- 🟢 Verde (≥70%) — retenção saudável
- 🟡 Amarelo (40-70%) — retenção média
- 🔴 Vermelho (<40%) — perda alta
- ⚪ M+0 cinza claro — referência (sempre 100% por definição)

### 9.3 Interpretação prática

Cohort hipotético **2025-06 (121 clientes)** com retenção `100% / 52% / 47% / 53% / 51% / 43% / 41% / 44%`:
- 121 clientes adquiridos em jun/2025
- Em jul (M+1): 52% voltaram (63 clientes)
- Em ago (M+2): caiu pra 47% — perdeu 5 pontos
- Em set (M+3): subiu pra 53% — alguns voltaram (sazonalidade)
- Em dez (M+6): 41% ainda comprando

> Compare cohorts entre si: se as turmas mais recentes (2026-01, -02, -03) tiverem retenção **maior** que as antigas, a equipe melhorou (campanha de onboarding, treinamento). Se for **menor**, algo regrediu.

### 9.4 Interação

- **Click em uma célula** → painel lateral com lista dos N clientes que estão naquele bucket (cohort × mês). Útil pra entender quem ficou e quem foi.
- **Click no label da linha** (ex: `2025-06 (121 clientes)`) → lista o cohort completo (todos os 121 adquiridos em jun). Equivale a clicar em M+0.

### 9.5 Filtros

- **Período**: 12 / 18 / 24 meses
- **Vendedor** (opcional): cohort filtrado pela carteira de UM vendedor — útil pra avaliar capacidade de retenção individual

### 9.6 Detalhes técnicos

**Definição clássica de retenção** (não-cumulativa): cliente "retido em M+N" se fez **pelo menos 1 compra naquele mês específico**. Se pulou o mês mas voltou em M+N+1, não conta como retido em M+N.

```python
# Pseudo-código
compras_por_cliente = {codcli: [meses 'YYYY-MM' com compra]}
for codcli, meses in compras_por_cliente:
    mes_aquisicao = min(meses)  # primeira compra
    for mes in meses:
        rel = diferença_meses(mes, mes_aquisicao)
        cohorts[mes_aquisicao][rel].add(codcli)
```

1 query DAX traz `(CODCLI, AnoMes)` do universo de 12-24 meses; cálculo de cohort feito em Python (cache 24h porque histórico muda lentamente).

---

## 10. RBAC — Quem vê o quê

### 10.1 4 papéis

| Papel | `codusur` | `codsupervisor` | Vê o quê |
|---|---|---|---|
| **admin** | vazio | vazio | Tudo (todos vendedores, toda carteira) |
| **supervisor** | vazio | preenchido | Apenas vendedores onde `CODSUPERVISOR = X` |
| **vendedor** | preenchido | vazio | Apenas clientes onde `CODUSUR = Y` |
| **viewer** | vazio | vazio | Tudo (leitura geral, sem filtro) |

### 10.2 Comportamento por tela

| Tela | Admin/Viewer | Supervisor | Vendedor |
|---|---|---|---|
| Dashboard | ✅ visão global | ✅ filtrada pelo time | ✅ filtrada pelo CODUSUR |
| Carteira | ✅ 8.700+ clientes | ✅ só clientes do time | ✅ só própria carteira |
| Vendedores (lista) | ✅ ~88 ativos | ✅ ~N do time | ❌ **403** (não tem ranking) |
| Vendedor `/<id>` | ✅ qualquer | ✅ só do time (outros → 403) | ✅ só o próprio (outros → 403 ou redirect) |
| Categorias / Mix / Tendências | ✅ global | ✅ time | ✅ próprio |

### 10.3 Exemplos práticos

**Cenário 1**: vendedor JOSE JUNIOR (CODUSUR=879) abre o painel.
- Dashboard: vê só seus números
- Carteira: vê seus 92 clientes (não os 8.700 da empresa)
- Tenta `/vendedor/100` (outro vendedor) → frontend redireciona pro próprio cockpit (UX amigável) E backend responde 403 em `/api/vendedor/100` (auditável)
- Link "Vendedores" some do menu

**Cenário 2**: supervisor FABIANE BA (CODSUPERVISOR=19) abre o painel.
- Ranking de Vendedores: vê seus 15 vendedores (não os 88)
- Cockpit de qualquer vendedor do time: OK
- Cockpit de vendedor de outro time: **403**

### 10.4 Garantias técnicas

- Filtros aplicados **no servidor** via fragmentos DAX automáticos
- Chave de cache inclui RBAC: vendedor X não reaproveita cache de vendedor Y
- 6 testes automatizados (`tests/test_rbac.py`) garantem zero regressão

---

## 11. Glossário

| Termo | Significa |
|---|---|
| **CODUSUR** | Código do vendedor no Winthor (ex: JOSE JUNIOR = 879) |
| **CODSUPERVISOR** | Código do supervisor — agrupa vários vendedores em "time" |
| **CODEPTO** | Código do departamento de produto (33 valores: ALIMENTÍCIOS, EMBALAGENS, etc) |
| **Ciclo pessoal** | Mediana de intervalos entre compras de UM cliente nos últimos 12m, mínimo 7 dias |
| **Quintil** | Divisão da base em 5 grupos iguais. Quintil 5 = top 20% |
| **Recência (R)** | Quantos dias desde a última compra. Maior = pior (cliente sumiu) |
| **Frequência (F)** | Quantas notas/compras nos últimos 12m. Maior = melhor |
| **Monetário (M)** | Lucro gerado nos últimos 12m. Maior = melhor |
| **Cohort** | "Turma" de clientes que apareceu pela primeira vez no mesmo mês |
| **Positivação** | Cliente "positivado" = cliente que comprou no período (≠ cliente cadastrado mas inativo) |
| **YoY** | Year-over-Year — comparação com mesmo período do ano anterior |
| **Mix** | Variedade de produtos/categorias vendidos |
| **Share** | Fração desse item no total (ex: depto ALIMENTÍCIOS tem 7% share = 7% do faturamento total) |
| **Lucro perdido projetado** | Estimativa de quanto a empresa deixa de lucrar/ano se cliente em atraso não for resgatado |
| **Drill 360°** | Painel lateral que abre ao clicar numa linha/célula, mostrando todos os detalhes daquela entidade |
| **RBAC** | Role-Based Access Control — quem vê o quê baseado no papel do usuário |
| **RFM** | Recência-Frequência-Monetário — modelo clássico de segmentação de clientes |
| **DAX** | Linguagem de consulta do Power BI (similar a SQL, otimizada pra análise) |
| **PBI** | Power BI — ferramenta da Microsoft que hospeda os dados |

---

## 12. Fontes & Refresh

### 12.1 Origem dos dados

**Power BI workspace** da Multpel → **dataset RCA** (Import puro do Oracle Winthor).

Tabelas relevantes (11 totais):
- **FATURAMENTO_VENDAS** (fato): 1,6M+ linhas, todas as notas emitidas
- **FATURAMENTO_DEVOLUCAO**: notas de devolução (também usada como tabela auxiliar pra nomes de departamento)
- **PCCLIENT**: cadastro de clientes (~9.000 ativos, ~41.000 históricos)
- **PCUSUARI**: cadastro de vendedores (739 cadastros, ~88 ativos em 12m)
- **PCSUPERV**: cadastro de supervisores (37, com nomes reais)
- **PCPRODUT**: cadastro de produtos (21.000)
- **PCFORNEC**, **PCEMPR**, **CALENDARIO**, **DATA ULTIMA ATUALIZAÇÃO**

39 medidas DAX pré-calculadas (VENDA LIQUIDA, LUCRO TOTAL, MARGEM(%), TICKET MEDIO, TAXA POSITIVACAO CLIENTE, Crescimento Ano a Ano X, etc.).

### 12.2 Atualização

- **Power BI**: refresh ~1× ao dia (definido pelo cliente)
- **Cache do painel (Redis)**:
  - KPIs agregados, RFM completo, ranking vendedores: **1 hora**
  - Cohort retention, mapas auxiliares (vendedores, supervisores, departamentos): **24 horas** (mudam devagar)
  - Token Power BI: **50 minutos** (renovado proativamente — válido 1h)

### 12.3 O que fazer se o painel mostrar "Erro" ou "Carregando" demais

1. **Aguarde 5-10 min**: o Power BI pode estar em refresh diário (rejeita queries durante esse processo)
2. **Tentar de novo**: clique no botão "Tentar agora" no banner amarelo
3. **Recarregar a página** (F5): força nova requisição
4. **Se persistir mais que 30 min**: avisar o time técnico

### 12.4 Performance esperada

| Operação | Tempo típico |
|---|---|
| Login + Dashboard (1ª vez) | 3-6 segundos |
| Carteira (1ª vez) | 5-8 segundos (carrega 8.700 clientes + RFM) |
| Mix abandonado (1ª vez) | 10-17 segundos (query pesada) |
| Cohort (1ª vez) | 3-5 segundos |
| Qualquer tela (2ª vez, em < 1h) | < 500ms (cache hit) |
| Drill 360° de cliente | 1-2 segundos |

---

**Fim do manual.** Para dúvidas técnicas ou novas funcionalidades, consultar o time de desenvolvimento.
