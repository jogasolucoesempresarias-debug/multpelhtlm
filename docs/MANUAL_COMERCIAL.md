# Manual do Módulo COMERCIAL — JOGA Analytics

**Versão 3.0 · Atualizado em 28/07/2026 · Base de conhecimento do agente de IA de dúvidas.**

> **Escopo:** este manual cobre o **módulo Comercial** (dashboards, carteira RFM, vendedores,
> categorias, mix, radar, tendências, metas, cobertura gerencial, admin) e a **plataforma comum**
> (login, áreas, tema, cache, e-mails). O **módulo Compras/Estoque** tem manual próprio:
> **`docs/MANUAL_COMPRAS.md`** — pergunta sobre estoque, ruptura, pedido de compra, validade,
> orçamento de compras ou fornecedor vai para lá.
>
> **Este documento substitui** o antigo `MANUAL.md` da raiz (v2.0, 13/07/2026), que descreve o
> mundo pré-fusão e não conhece portal, áreas, tema, segurança de login nem multi-fonte.
>
> **Regra de precedência:** quando o comportamento observado divergir deste manual, **o código
> manda**. Fontes da verdade: `server.py` (rotas/DAX), `rfm.py`, `cohort.py`, `metas.py`,
> `cobertura.py` (matemática pura), `provider_sql.py` (modo Postgres).

---

## 0. Como usar este manual

Organização: **plataforma → conceitos → fórmulas centrais → uma seção por página → acesso →
e-mails → operação → glossário → FAQ**. Cada seção é auto-contida (pensada para busca/RAG).

Para responder uma pergunta:
1. Identifique a **página** (§5 a §14) — ela lista filtros, cards, colunas, drills e exports.
2. Se a dúvida é sobre **como a conta é feita**, vá à fórmula em §3 ou §4.
3. Se é sobre **quem vê o quê**, vá a §15 (RBAC) e §2 (áreas).
4. Se é "por que o número está diferente do ERP / da outra tela", vá a §19 (FAQ) e §2.1.

**Regra de ouro dos números:** tudo é alinhado ao **RCA do ERP (Totvs/Winthor)**. O app **não**
usa cegamente as medidas nativas do Power BI quando elas divergem do que o vendedor vê no
Winthor. Ver §3.1.

---

## 1. O que é o sistema

**JOGA Analytics** é um painel web (Flask) que lê o **Power BI** do cliente (modelo
Totvs/Winthor, Oracle) e entrega análise comercial e de compras num único login.

- **Cliente atual:** Multpel (o produto é da JOGA; "Multpel" aparece em rótulos por ser dado do
  cliente, não marca do produto).
- **Produção:** `painel.jogasolucoes.com.br`.
- **Demonstração:** `demo.jogasolucoes.com.br` — **dados sintéticos**, não toca o BI de ninguém
  (ver §18).
- **Atualização dos dados:** o BI do cliente atualiza ~1×/dia; o cabeçalho mostra a data/hora do
  último refresh concluído (§17.1).

### 1.1 Os dois módulos

| Módulo | O que responde | Manual |
|---|---|---|
| **Comercial** | Para quem vendemos, quem vende, o que vende, quem está esfriando, batemos a meta | **este** |
| **Compras** | O que comprar, o que vai vencer, o que está parado, ruptura, orçamento de compras | `docs/MANUAL_COMPRAS.md` |

A **Administração** é um terceiro lugar neutro — não pertence a nenhum dos dois.

---

## 2. Acesso: login, áreas, portal e tema

### 2.1 Um login, duas áreas

O usuário loga uma vez. O que ele enxerga é a **interseção** de duas coisas:

- **`MODULOS`** — o que a **empresa** contratou nesta instância (variável de ambiente:
  `comercial,compras`, `comercial` ou `compras`). Módulo não contratado → a rota **nem existe**
  (**404**).
- **`areas`** do usuário (coluna JSONB em `multpel_users`) — o que **a pessoa** acessa.
  **Default `["comercial"]`**: ninguém ganha Compras por acidente; o admin libera pessoa a
  pessoa. Usuário sem a área → **403**.

**Para onde vai depois do login:**
- 1 área efetiva → direto para ela (`/` no Comercial, `/estoque/` no Compras) — sem portal.
- 2 áreas → respeita o "fixar" do portal (coluna `area_padrao`: `portal` | `comercial` |
  `compras`). Default `portal`.

### 2.2 Portal (`/portal`) e seletor de área

Tela de escolha entre **Comercial** e **Compras**, mais uma faixa para a **Administração** (só
para admin). Um **seletor de área fixo no topo** de todas as páginas troca entre os dois sem
voltar ao portal. Dá para **fixar** uma área como padrão (grava em `area_padrao`).

### 2.3 Tema claro/escuro

- Botão **☀️/🌙** no cabeçalho. **Padrão escuro** (o claro é opt-in).
- Persiste em **duas camadas**: `localStorage` (aplica sem piscar) **e** banco (coluna `tema`),
  então o tema **segue a pessoa entre máquinas**. No carregamento, **o banco vence**.
- Toda cor vive em `static/tema.css`; o claro é validado em contraste **WCAG AA**.
- Gráficos repintam ao vivo na troca de tema.

### 2.4 Segurança do login

- **Bloqueio progressivo por conta:** 5 erros → 15 min; escalona (1 h, 4 h); zera no acerto.
  Colunas `tentativas_falhas` / `bloqueado_ate` / `bloqueios_seguidos`. Limiares em
  `multpel_config`. O admin tem botão **"desbloquear"**.
- **Limite por IP** (Redis, *fail-open*: se o Redis cair, o login não trava).
- Cookie `HttpOnly` + `SameSite=Lax` + expiração; `Secure` em produção.
- Rastro de login com IP em `multpel_log`. Enumeração por tempo mitigada.
- **`SECRET_KEY` é obrigatória em produção** — sem ela o app **não sobe** (proposital).

### 2.5 Colunas de acesso em `multpel_users`

`areas` (JSONB, default `["comercial"]`) · `area_padrao` · `codcomprador` (filtro **inicial** do
Compras, não trava) · `relatorios_estoque` (JSONB — quais relatórios de Compras recebe por
e-mail) · `tema` · `codusur` · `codsupervisores` · `emails_cc` · `segmentos_rfm` ·
`cron_*` · `tentativas_falhas` / `bloqueado_ate` / `bloqueios_seguidos`.

---

## 3. Conceitos-base

### 3.1 Alinhamento com o RCA (a regra dos números)

O RCA do Totvs e o Power BI calculam venda/lucro de forma sutilmente diferente. O app usa
fórmulas customizadas para bater **centavo a centavo** com o RCA:

- A **devolução é contada pela data de ENTRADA no estoque (`DTENT`)**, não pela data da venda
  original (`DTSAIDA`). Sem isso há divergência média de 1–2%.
- Validação oficial: **Sup. AFONSO ES-SUL, Abr/26** → Venda Líquida R$ 2.385.853,77 / Lucro
  R$ 520.326,87 (bate com o RCA).

### 3.2 RFM (Recência, Frequência, Monetário)

Classifica cada cliente em 3 dimensões dos últimos 12 meses, com nota **1 a 5** (quintis):

- **R (Recência)** — dias desde a última compra. **Menos dias = nota maior.**
- **F (Frequência)** — nº de compras/notas em 12m. Mais = melhor.
- **M (Monetário)** — **lucro** gerado em 12m. Mais = melhor.

**Por que quintis e não valores fixos?** Porque "muita compra" varia por perfil de cliente. O
quintil normaliza: sempre 20% da base no topo.

**Detalhe importante dos cutoffs:** os cortes de **R** são calculados sobre **toda a base**
(24m). Os de **F** e **M** só sobre clientes **ativos** (≥1 compra em 12m); os inativos recebem
**F=1 e M=1** direto. Sem isso, uma base cheia de inativos empurraria os cortes para baixo e
todo mundo pareceria bom.

### 3.3 Ciclo pessoal e régua personalizada

Cada cliente tem um **ciclo pessoal** = **mediana** dos intervalos entre compras nos últimos
12m, com **piso de 7 dias**. Cliente com menos de 2 compras não tem ciclo (`None`).

A **régua personalizada** mede o atraso **relativo ao padrão do próprio cliente**
(`dias ÷ ciclo`), não a um número fixo. Uma padaria que compra a cada 7 dias e está há 14 sem
comprar está **pior** (2× o ciclo) que um hotel que compra a cada 90 e está há 30.

### 3.4 Positivação

**Cliente positivado** = comprou no período analisado (≠ cliente cadastrado mas inativo).

### 3.5 Cobertura de carteira

**% da carteira que está em dia** (comprou dentro da janela; padrão 30 dias). É o mesmo conceito
em dois zooms: o **índice %** (placar) e a **distribuição por faixa** de recência. Ver §13.

### 3.6 RBAC — duas réguas de isolamento

O acesso existe em **duas naturezas** (detalhe em §15):

- **Por VENDA** — filtra a transação (`CODUSUR` / `CODSUPERVISOR` na nota). Usado em Dashboard,
  Vendedores, agregados. Injetado no DAX por `aplicar_rbac_dax()`.
- **Por CADASTRO** — filtra pelo cliente registrado no vendedor (`PCCLIENT.CODUSUR1`). Usado em
  Carteira, Categorias, Mix, Radar, Tendências, Gerencial e e-mails. Recorte em Python por
  `_carteira_no_escopo()`.

---

## 4. Fórmulas centrais (as contas que se repetem)

### 4.1 Receita Líquida (alinhada RCA)
```
Receita Líquida = VENDA BRUTA(DTSAIDA)
                − TOTAL DEVOLUCAO(DTENT)
                − TOTAL DEVOLUCAO AVULSA(DTENT)
```

### 4.2 Lucro Total (alinhado RCA)
```
Lucro Total = Receita Líquida
            − ( CUSTO TOTAL
              − CUSTO TOTAL DEVOLUCAO(DTENT)
              − CUSTO TOTAL DEVOLUCAO AVULSA(DTENT) )
```
O custo da mercadoria devolvida **volta** para o lucro — senão ele seria contado em dobro.

### 4.3 Métricas derivadas do mês
```
Margem (%)           = Lucro Total ÷ Receita Líquida
Ticket Médio         = Receita Líquida ÷ DISTINCTCOUNT(NUMTRANSVENDA)   (nº de pedidos)
Clientes Positivados = DISTINCTCOUNT(CODCLI) no período
Mix Médio            = medida [TOTAL MIX]
Clientes Novos       = medida [TOTAL CLIENTES NOVO]   (1ª compra no mês)
Valor Médio / Kg     = medida [VALOR MEDIO PESO]
```
Mês corrente = `MONTH(DTSAIDA)=MONTH(HOJE) && YEAR(DTSAIDA)=YEAR(HOJE)`.

### 4.4 YoY (Year-over-Year)
Recalculado no app — **não** usa a medida nativa, que diverge e não aceita filtro de supervisor.
Janela de **12 meses vs. os 12 meses anteriores**:
```
YoY(métrica) = (valor_12m − valor_12m_anterior) ÷ valor_12m_anterior
```
Aplicado a: Receita Líquida, Lucro, Positivação de Cliente (DISTINCTCOUNT CODCLI) e Mix.
⚠️ O **ranking de vendedores** usa janela de **365 dias exatos** (não `EDATE`), para bater com o
BI do cliente.

### 4.5 Ciclo pessoal e status de régua
```
ciclo_pessoal = max(7, mediana(intervalos entre compras nos últimos 12m))
                (None se o cliente tem < 2 compras)

Régua PERSONALIZADA (padrão do app):   razao = dias_sem_comprar ÷ ciclo
  razao < 1  → ok        (dentro do ciclo)
  razao < 2  → normal
  razao < 3  → atenção
  razao ≥ 3  → urgente
  (sem ciclo → cai na régua fixa)

Régua FIXA (planilha original do cliente):
  ≤10 → ok · ≤30 → normal · ≤45 → atenção · >45 → urgente
```

### 4.6 Receita / Lucro perdido projetado
```
se dias_sem_comprar < ciclo:  perdido = 0
senão:
  meses_atrasado       = (dias_sem_comprar − ciclo) ÷ 30
  receita_perdida_proj = (venda_12m ÷ 12) × meses_atrasado
  lucro_perdido_proj   = (lucro_12m ÷ 12) × meses_atrasado
```
Cliente sem ciclo (1 compra só) → 0. Nunca negativo.
**Exemplo:** cliente que dá R$ 1.200/mês, ciclo 30d, 90d parado → 2 meses atrasado →
R$ 2.400 projetados como perdidos.

### 4.7 Próximo pedido e prioridade de contato
```
proximo_pedido_previsto = ultima_compra + ciclo_pessoal
prioridade_contato      = (venda_12m ÷ 12) × (dias_sem_comprar ÷ ciclo)   [0 se razão < 1]
```
⚠️ A prioridade **não** usa `receita_perdida_proj` de propósito: aquela zera exatamente no
"vence hoje" (dias == ciclo) e afundaria justamente quem deve ser contatado hoje.
Clientes sem ciclo não têm previsão e ficam de fora da lista do dia.

### 4.8 Quintis e segmentos RFM
Segmentos canônicos — **a primeira regra que casa vence** (evita sobreposição):
```
champions            : R=5 e F=5 e M=5
loyal (Fiéis)        : R≥4 e F≥4
cant_lose (Não Perder): 2≤R≤3 e F≥4
at_risk (Em Risco)   : 2≤R≤3 e F≥3
new (Novos)          : R=5 e F=1
potential_loyalist   : R≥4 e 1≤F≤3
lost (Perdidos)      : R≤2 e F≤2 e M≤2
hibernating (Inativos): todo o resto
```
⚠️ `loyal`, `cant_lose` e `at_risk` **não exigem M**: mercadinhos e padarias compram com alta
frequência e ticket pequeno; exigir volume os jogava em "hibernating", que é semanticamente
errado. Alta R + alta F = fiel, **independente do volume**.

---

## 5. Página: Dashboard (`/`)

**Pergunta que responde:** "Como está o mês?"

**Filtro (admin/viewer):** "Supervisor (Time)" — aceita 1 ou vários; sem filtro = empresa toda.
Vendedor/supervisor logado já vê só o seu (o parâmetro é ignorado para eles).

**Cards — linha 1:** Venda Líquida (mês) · Lucro Total (mês) · Margem (%) · Clientes
Positivados.
**Cards — linha 2:** Ticket Médio · Mix Médio · Clientes Novos · Valor Médio/Kg.
Fórmulas em §4.3; venda/lucro alinhados RCA (§4.1/§4.2). Os cards primários mostram o
indicador **YoY** (↗ verde / ↘ vermelho).

**Gráficos e tabelas:**
- **Série temporal 12 meses** (linha dupla): Venda Líquida + Lucro Total por mês. É o *merge* de
  3 queries (vendas, devolução, devolução avulsa) por AnoMes, alinhado RCA.
- **YoY — 4 métricas** (barras): % de Receita, Lucro, Positivação de Cliente e Mix vs. ano
  anterior.
- **Top 10 Departamentos por Lucro (12m)** — clique abre os clientes do departamento.
- **Top 10 Vendedores por Lucro (12m)**.
- **Top 10 Clientes por Lucro (12m)** — clique abre o **drill 360°** do cliente.

**Cache:** 30 min a 1 h.

---

## 6. Página: Carteira (`/carteira`)

**Pergunta que responde:** "Para quem estamos vendendo e quem está esfriando?"
É a tela mais rica. Tem **duas abas**: **Visão Geral (RFM)** e **📞 Próximo Pedido**.

### 6.1 Aba Visão Geral (RFM)

**Universo:** clientes ativos nos últimos **24 meses** (base RFM), com os **números totais** do
cliente. A carteira é carregada global (1 entrada de cache para todos) e **recortada por
cadastro** conforme o RBAC.

**Filtros:** Time (supervisor) · Vendedor · UF · Cidade · Busca livre (cliente, cidade, código,
vendedor, time) · "Dias s/ comprar (mín.)" com atalhos 10+/30+/60+/90+ · chips de **Segmento**
(8). Os dropdowns fazem **cross-filter** (escolher um estreita os outros) e listam só quem
realmente tem cliente na carteira.

**Visualizações:** 8 cards de segmento · donut de distribuição · gráfico **Receita líquida ×
Clientes positivados (12m)**, que reage aos filtros e é clicável (abre o detalhe do mês).

**Tabela acionável — colunas:** CodCli · Cliente · Cidade/UF · Vendedor · Time · **R (dias)** ·
**F (12m)** · Venda 12m · Média Venda (= venda_12m ÷ 12) · **⚠ Receita Perdida proj.** ·
Segmento · Telefone.

**Drill 360° do cliente** (clique na linha): última compra, ciclo pessoal, notas R/F/M,
histórico mensal 12m e **top 5 departamentos comprados**.

**Exports:** CSV (com BOM UTF-8, abre no Excel com acento) e PDF. O nome do arquivo reflete os
filtros ativos.

**Deep-link por faixa (vindo do Gerencial):** a Carteira lê parâmetros da URL no carregamento —
`dias_min`, `dias_max`, `vendedor`, `time`, `uf`, `cidade`, `segmento`, `busca`. É assim que o
clique numa faixa do painel Gerencial abre exatamente aqueles clientes
(ex.: `?dias_min=31&dias_max=45&time=18`).

### 6.2 Aba Próximo Pedido (a "lista do dia")

Previsão de recompra = **última compra + ciclo pessoal** (§4.7).

- **Janela:** "A ligar hoje" · "A ligar nos próximos 7 dias" · "Vencido há +15 dias".
- **Filtros:** Time, Vendedor, Busca.
- **Ordenação:** por **prioridade** (valor mensal × quão vencido está).
- Quando o cliente compra, ele **sai automaticamente** da lista (a recência muda).
- Clique na linha → **top produtos a oferecer** àquele cliente.
- **Export CSV/PDF** em formato de agenda cronológica por data de previsão.

---

## 7. Página: Vendedores (`/vendedores`) e Cockpit (`/vendedor/<codusur>`)

**Pergunta que responde:** "Quem está vendendo?"

### 7.1 Ranking (`/vendedores`)

**Filtros:** Tipo (R = rota, I = interno, P) · Supervisor · UF · Busca · toggle "mostrar
internos".
**Sempre excluídos:** vendedores técnicos (códigos **999, 900, 4, 272**) e os com
`PCUSUARI[BLOQUEIO]='S'`.

**Colunas:** rank · Vendedor · Tipo · Time · UF · Venda 12m · Lucro 12m · Ticket ·
**Positivação** · Clientes únicos · **YoY %**. Ordena por **Lucro 12m**.

**Taxa de Positivação** — fórmula do app, porque a medida nativa estava bugada:
```
taxa_positivacao = clientes_que_compraram_12m ÷ carteira_oficial
carteira_oficial = DISTINCTCOUNT(PCCLIENT[CODCLI]) agrupado por PCCLIENT[CODUSUR1]
```
O **YoY é calculado em Python** (janela de 365 dias), porque a medida nativa retorna NULL quando
filtrada por `CODUSUR`.

### 7.2 Cockpit individual (`/vendedor/<codusur>`)

- **Header de perfil** (dados do PCUSUARI).
- **4 cards:** Venda 12m · Lucro 12m (+ posição no ranking) · Carteira (X cadastrados /
  Y positivados) · Positivação vs. time e vs. empresa.
- **Alertas acionáveis:** clientes At Risk somando R$/ano em risco; top 3 Champions clicáveis.
- **3 gráficos:** série 12m (venda + lucro) · donut RFM · distribuição de status de régua.
- **Tabela da carteira** restrita ao `CODUSUR`, com export.

Um vendedor só abre o próprio cockpit; tentar outro devolve **403** ("Sem permissão").

---

## 8. Página: Categorias (`/categorias`)

**Pergunta que responde:** "Quais departamentos vendem e lucram?"

Análise por **departamento** (`CODEPTO`, 33 valores com nome textual). Usa CODEPTO — e não
`CODCATEGORIA` — porque a categoria vem **~92% nula** no faturamento.

- **Treemap (shelf):** tamanho ∝ venda, **cor ∝ margem** (verde = alta, vermelho = baixa).
  Clique → top clientes do departamento.
- **Top 10 fornecedores** (barras).
- **Tabela:** Venda · Lucro · Margem % · Share · Clientes únicos · Produtos únicos, por depto.

```
por depto (janela 12m): [VENDA LIQUIDA], [LUCRO TOTAL],
                        DISTINCTCOUNT(CODCLI), DISTINCTCOUNT(CODPROD)
Margem = Lucro ÷ Venda        Share = venda_depto ÷ venda_total
```
Não-admin: recorte **por cadastro** (`CODCLI IN {...}` do escopo).

---

## 9. Página: Mix Abandonado (`/mix`)

**Pergunta que responde:** "Quem parou de comprar um DEPARTAMENTO?" (cross-sell perdido)

Lista pares **Cliente × Departamento**: quem comprava o departamento nos últimos 12m mas
**parou** nos últimos N dias.

- **Filtros:** Período (30/60/90/180 dias) · Departamento · Fornecedor · Vendedor/Busca (locais).
- **Cards:** nº de pares parados · Lucro 12m em risco · Top 5 maiores perdas.
- **Tabela:** Cliente · Cidade/UF · Departamento · Última compra · **Dias parado** (badge
  amarelo 30–60, laranja 60–90, vermelho 90+) · Venda/Lucro da categoria 12m · Vendedor.
- **Drills:** clique no cliente → **top 5 departamentos perdidos**; há também drill de
  **fornecedores abandonados por cliente**, com export CSV e PDF próprios
  (`/api/mix/cliente/<codcli>/fornecedores`).
- **Export** CSV/PDF da lista completa.

---

## 10. Página: Radar (`/radar`)

**Pergunta que responde:** "Quem parou de comprar um PRODUTO?"

Mesma ideia do Mix, mas no nível de **produto**, comparando **janela recente vs. janela
anterior** (queda de volume).

**Status do cliente em relação ao produto** (janela = N dias):
```
perdido   : dias_parado ≥ 2×N   (ou nunca comprou)
parou     : dias_parado ≥ N
esfriando : ainda compra, mas venda recente < 50% da anterior
ativo     : os demais
```

- **Board:** produtos "sangrando", ordenados por queda de receita.
- Marca **troca vs. abandono**: "Trocou (outro do depto)" quando o cliente migrou para outro
  produto do mesmo departamento — isso separa perda real de substituição.
- **Filtros** por fornecedor; busca de produto; drill por produto, por cliente e série
  produto × cliente.
- **Exports** CSV/PDF em todos os níveis. Escopo por **cadastro** (não-admin).

**Diferença Mix × Radar (pergunta frequente):** Mix = parou de comprar um **departamento**;
Radar = parou de comprar um **produto** específico.

---

## 11. Página: Tendências (`/tendencias`) — Cohort Retention

**Pergunta que responde:** "A base nova continua comprando?"

Cada **linha** é uma "turma" (cohort) que fez a **1ª compra** num mês; cada **coluna** é quantos
meses depois (M+0, M+1, …, M+12); cada **célula** é o **% daquele cohort que comprou naquele mês
específico**.

⚠️ **Retenção não-cumulativa:** pular um mês **não** conta. O cliente precisa ter comprado
naquele mês exato.
```
mes_aquisicao(cliente) = min(meses com compra)
retido em M+N  ⇔  existe compra no mês (mes_aquisicao + N)
```

- **Cores:** 🟢 ≥70% · 🟡 40–70% · 🔴 <40% · **M+0 = 100%** por definição (referência).
- **Filtros:** Período (12/18/24 meses) · Vendedor e Supervisor (em cascata).
- **Drills:** clique na célula → clientes daquele bucket; clique no rótulo da linha → cohort
  completo.
- **Cache:** 24 h (é a query mais pesada do app).

---

## 12. Página: Metas (`/metas`)

**Pergunta que responde:** "Estamos batendo a meta por vendedor/time?"

Réplica das **4 telas META** do BI do cliente: **Venda (valor)**, **Rentabilidade (lucro)**,
**Clientes** e **Mix**. Abas no topo trocam a métrica.

### 12.1 De onde vem cada número

| Peça | Fonte |
|---|---|
| **Meta (alvo)** | **Postgres** — tabela `multpel_metas`, digitada no app. O app é dono do alvo. |
| **Realizado — mês CORRENTE** | Dataset **META** (pedidos: `PCPEDC`/`PCPEDI`) — meta é sobre **PEDIDO**, não faturamento. |
| **Realizado — mês FECHADO** | Dataset **RCA** (faturamento) — o dataset META esvazia ao virar o mês. |
| **Dias úteis** | Calendário de meta do dataset (mês corrente) ou cálculo de dias úteis (mês fechado). |

### 12.2 Fórmulas (dias ÚTEIS, não corridos)
```
Projeção        = realizado × DiasMetaMes ÷ DiasMetaDecorridos      (run-rate por dia útil)
Falta           = max(0, Meta − Realizado)
Necessidade/dia = Falta ÷ DiasMetaRestantes
% Realizado     = Realizado ÷ Meta
% Proj. Meta    = Projeção ÷ Meta
```
No **mês corrente** a projeção usa a medida oficial `[Projecao]` do dataset (bate centavo com o
BI); no mês fechado ela é recalculada em Python pelo run-rate.

**Medidas usadas no mês corrente:** `[Tem Pedido]` (realizado **bruto**, com bônus — é o que o BI
mostra em "($) REALIZADO"), `[Realizado Sem Bonus]`, `[MARGEM META(%)]`, `[Projecao]`,
`DISTINCTCOUNT(PCPEDC[CODCLI])` com `POSICAO IN {F,L,B}` e `DISTINCTCOUNT(PCPEDI[CODPROD])`.

⚠️ **Margem da tela = lucro realizado ÷ realizado BRUTO** (o mesmo número da coluna *Realizado*,
com bonificação) — é a régua da medida oficial `[MARGEM(%)]` do dataset META
(= `[LUCRO TOTAL] ÷ [VENDA TOTAL]`, e `[VENDA TOTAL]` ≡ `[Tem Pedido]`). Conferido ao vivo em
13/08/2026 nos 9 supervisores: bate na 4ª casa.
**NÃO dividir por `venda_sb`** (sem bônus): de 07 a 08/2026 dividia e a margem saía inflada em até
**6,3 p.p.** (FABIANE BA 30,48% onde o BI dizia 24,22%; empresa 20,34% × 18,81%). O erro cresce com
o bônus do time e some onde o bônus é zero — por isso passou despercebido em jun/2026, quando a
bonificação era 1% da venda (hoje é 7,5%). Sinal de alarme para o futuro: um % que **não se
reproduz com nenhum número da própria tela**.

### 12.3 Aditividade (armadilha clássica)

**Venda e rentabilidade são aditivas** (somam vendedor → supervisor → total).
**Clientes e mix NÃO são** — são `DISTINCTCOUNT`: um cliente atendido por 2 vendedores conta
**1×** no total. Por isso o realizado de clientes/mix é medido **em cada grão separadamente** e
o app **nunca** soma esses dois.

### 12.4 Universo do painel

Só entram **supervisores com pelo menos 1 vendedor com meta cadastrada** no mês. Isso alinha com
o BI — times sem meta (ex.: DIRETORIA, E-COMMERCE) não entram nem no painel nem no total. Se
não houver **nenhuma** meta no mês, o painel mostra tudo (não esconde o realizado).

### 12.5 Editor de metas (Admin)

CRUD de meta por vendedor/mês, com salvamento em lote (tudo ou nada) e **importação**.
**Sugestão de meta** a partir do histórico de realizados:
```
metodo = 'ano_anterior' : mesmo mês do ano anterior × (1 + crescimento)
metodo = 'media_3m'     : média dos 3 meses anteriores × (1 + crescimento)
```
O admin sempre pode ajustar o valor sugerido.

---

## 13. Página: Gerencial — Cobertura de Carteira (`/gerencial`)

**Pergunta que responde:** "Quem está dando conta da carteira e quem precisa de atenção?"

Placar de **cobertura** por **Empresa / Time / RCA**. Calculado 100% sobre a carteira já
carregada (sem query nova ao BI), respeitando o RBAC por cadastro.

### 13.1 Definições e fórmulas
```
Coberto            = cliente com recência ≤ coberto_dias (padrão 30; toggle 30/45/60)
Cobertura clientes = nº de clientes cobertos ÷ total da carteira
Cobertura valor    = venda_12m dos cobertos ÷ venda_12m total
Cobertura ciclo    = clientes com status personalizado ∈ {ok, normal} ÷ total  (régua justa)
Receita em risco   = Σ receita_perdida_proj                                    (§4.6)
Base morta         = nº de clientes na faixa 91+
Média mensal       = valor_total ÷ 12
```
- **Denominador** = carteira ativa 24m (mesma base RFM).
- **Cobertura por clientes × por valor** aparecem lado a lado: base atendida × faturamento
  protegido.
- **Cobertura dentro do ciclo** é a régua justa para ranquear times/RCAs de perfis diferentes —
  não pune quem atende cliente de ciclo longo.

### 13.2 Faixas de recência
Faixas **fixas** (não mudam com o toggle), com nº de clientes e venda 12m em cada:
```
0–15 · 16–30 · 31–45 · 46–60 · 61–90 · 91+      (+ rollup 0–30 = "em dia")
```
O índice de cobertura de 30 dias é exatamente a fatia **0–30 ÷ total**. Clicar numa faixa (ou no
rollup) abre a **lista exata** daqueles clientes na Carteira, por deep-link (§6.1).

### 13.3 Layout e drill-down
- **Banner:** "Hoje N time(s) e M RCA(s) estão abaixo de X%" — é exatamente o que o alerta por
  e-mail dispara.
- **Controles:** toggle da janela "em dia" (30/45/60) · export CSV/PDF.
- **Placar (KPIs) do escopo:** Cobertura por clientes · Cobertura por valor · Receita em risco
  (R$) · Dentro do ciclo (%). Cores de semáforo contra o limiar.
- **Distribuição por faixa:** barra empilhada + tabela (linha 0–30 destacada).
- **Ranking dos filhos** (Times no nível Empresa; RCAs ao entrar num Time), **pior → melhor**:
  Nome · **Positivados / Carteira** · Cobertura clientes · Cobertura valor · Dentro do ciclo ·
  Receita em risco · Base morta · ⚑ (abaixo do limiar).
  Linha de Time → drilla nos RCAs; linha de RCA → abre o cockpit.
- **Amostra pequena:** RCA com **menos de 5 clientes** é marcado — o % ali é ruído.
- **Breadcrumb:** Empresa ▸ Time ▸ RCA (clique para subir).

### 13.4 Limiar e escopo
- **Limiar de baixa performance** (corte vermelho + gatilho do alerta): configurável no **Admin**
  (padrão **60%**), vale na hora, sem redeploy. Gravado em `multpel_config`.
- **Escopo:** admin/viewer veem a empresa toda; supervisor vê seus times e os RCAs deles;
  vendedor vê só a própria carteira.

---

## 14. Página: Admin (`/admin`)

Só para papel **admin**. A Administração é **neutra** — não pertence ao Comercial nem ao
Compras, então um admin que só tem a área Compras consegue administrar o sistema.

### 14.1 Cobertura de carteira (Gerencial)
- **Limiar de baixa performance (%)** — padrão 60.
- **Janela "em dia" (dias)** — 30/45/60.
Salvos em `multpel_config`; valem para o painel **e** para o alerta por e-mail.

### 14.2 Usuários cadastrados (CRUD)

| Campo | Observação |
|---|---|
| **Acesso ao sistema** (áreas) | Checkboxes **Comercial** / **Compras**. Default: só Comercial. |
| Nome · E-mail · Telefone | — |
| **Destinatários adicionais (CC)** | Até **5** e-mails; recebem os mesmos relatórios. |
| Função | admin · supervisor · vendedor · viewer |
| **Vendedor (codusur)** | Para papel vendedor — autocomplete por nome ou código. |
| **Supervisor / Áreas** | Um supervisor pode ter **várias** áreas (`codsupervisores`). |
| **Comprador vinculado** (`codcomprador`) | Filtro **inicial** do módulo Compras — **não trava**: ele pode ver os outros. Também recorta os relatórios de Compras por e-mail. |
| Senha | Vazio = gerada automaticamente. |
| **Cron ativo** | Envia o relatório de carteira por e-mail. |
| **Incluir "Lista do Dia"** | Anexa o Próximo Pedido ao e-mail. |
| **Receber alerta de cobertura** | Opt-in do alerta gerencial. |
| **Usuário ativo** | Desmarcado = não consegue logar. |
| **Horário de envio** / **Frequência** | Diária · Semanal (segunda) · Semanal (sexta). |
| **Segmentos RFM** | Vazio = carteira completa; marcado = só aqueles segmentos no e-mail. |
| **Relatórios de Compras por e-mail** | Checkboxes vindos do catálogo único (`estoque/relatorios.py`) — ver `MANUAL_COMPRAS.md` §13. |

**Ações por usuário:** editar · excluir · **desbloquear** (zera o bloqueio de login) · **enviar
relatório agora** (Comercial ou `?tipo=compras`).

---

## 15. RBAC — quem vê o quê

| Papel | codusur | codsupervisor(es) | Enxerga |
|---|---|---|---|
| **admin** | — | — | Tudo, e administra |
| **viewer** | — | — | Tudo (só leitura) |
| **supervisor** | — | 1 ou mais áreas | Só suas áreas (multi-área suportado) |
| **vendedor** | preenchido | — | Só a própria carteira |

**As duas réguas (§3.6) na prática:**

- **Por VENDA** (Dashboard, Vendedores, agregados, Metas): fragmento DAX
  `CODUSUR IN {...}` / `CODSUPERVISOR IN {...}` injetado na query.
- **Por CADASTRO** (Carteira, Categorias, Mix, Radar, Tendências, Gerencial, e-mails): recorte em
  Python por `PCCLIENT.CODUSUR1`, com os **números totais** do cliente.

**Consequências que geram dúvida:**
- **Admin filtrando uma área == supervisor daquela área**, cliente por cliente.
- O filtro é aplicado **no servidor**. Um vendedor **não** amplia acesso trocando a URL — recebe
  403.
- A **chave de cache inclui o RBAC** (papel + codusur + lista de supervisores), então escopos
  **não vazam** entre si.
- **Metas** rodam no dataset META por `CODUSUR` (`_metas_escopo_codusur()`).

---

## 16. E-mails automáticos (Comercial)

Disparados por um agendador interno (APScheduler) que verifica **a cada 5 minutos** quem está na
janela de horário/frequência. Só enviam de fato se `CRON_HABILITADO=true` no ambiente. Entrega
via **Resend API**.

1. **Relatório de Carteira** (opção "Cron ativo") — PDF + CSV da carteira do destinatário.
   Vendedor: 1 PDF ordenado por lucro. Supervisor: 1 PDF por área + 1 CSV combinado. Respeita o
   filtro de **segmento RFM** do usuário e os **CCs**.
2. **Lista do Dia** (opção "Incluir Lista do Dia") — clientes a contatar hoje + vencidos até 15
   dias + top 5 produtos a oferecer.
3. **Alerta de Cobertura** (opção "Receber alerta") — digest dos **Times e RCAs abaixo do
   limiar** (padrão 60%), do pior para o melhor, no escopo do destinatário. Colunas: Nome ·
   Cobertura · **Positivados (≤Nd)** · **Carteira (total)** · Receita em risco.
   **Só envia se houver alguém abaixo do limiar.** Traz legenda "Como ler" para evitar a confusão
   entre positivados e carteira.

Os relatórios do **módulo Compras** têm catálogo e regras próprias — ver `MANUAL_COMPRAS.md` §13.

---

## 17. Operação: atualização, cache e performance

### 17.1 "BI atualizado em …" (linha do topo)
Vem da **API REST do Power BI** (histórico de refresh do dataset): pega o último refresh com
status **Completed**, converte de UTC para **America/São_Paulo** e exibe. Se houver refresh em
andamento, mostra **"🔄 atualizando"**. Cache de 5 min.
Se aparecer **"atualização indisponível"**, é permissão de *refresh history* do Service Principal
no Power BI — não é erro de dados.

### 17.2 Cache (Redis)

| O que | TTL |
|---|---|
| Carteira global, mapas mensais, ranking de vendedores | **1 h** |
| Metadados (vendedores, supervisores, departamentos) | **24 h** |
| Cohort (Tendências) | **24 h** |
| Dashboard agregados / drills | **30 min** |
| Refresh do BI (linha do topo) | 5 min |
| Token do Power BI | ~50 min |
| Metas do mês corrente | atado à **tag de refresh do dataset META** (~2 h) — quando o BI atualiza, a chave muda e o número volta a bater na hora |

Carteira/cohort/mapas são **globais** (1 entrada para todos) e recortados por usuário em Python;
endpoints agregados incluem o RBAC na chave. **2ª visita de qualquer tela dentro do TTL: < 500 ms.**

### 17.3 Se a tela mostrar "Erro" ou ficar carregando
O Power BI pode estar em **refresh diário** (rejeita queries nesse período). Aguarde 5–10 min,
use "Tentar agora" ou F5. Persistindo por mais de 30 min, avisar o time técnico.

---

## 18. Multi-fonte e instância DEMO (por que existe)

O app roda de **Power BI OU Postgres** com o **mesmo código**, decidido por variável de ambiente
— **não** são dois caminhos de produto.

| Env | Valores | O que faz |
|---|---|---|
| `DATA_SOURCE` | `powerbi` (default) \| `postgres` | de onde vêm os dados analíticos |
| `MEDIDAS` | `cliente` (default) \| `joga` | usa as medidas do BI do cliente ou a reconstrução própria |

- **`powerbi` + `cliente` = a produção de hoje.** Sai **idêntica** — validado centavo-a-centavo
  contra o BI real.
- **`postgres`** = lê de um Postgres analítico. É o que sustenta a **demo** (`joga_demo`,
  sintético) e permitiria atender um cliente "só banco".
- **Rede de segurança:** em modo `postgres` o caminho DAX **levanta erro de propósito**, para que
  um endpoint esquecido falhe alto (e degrade para vazio) em vez de **vazar dado real do cliente**
  numa demonstração.
- **Ancoragem de data na demo:** o "hoje" é o `max(dtsaida)` do banco (ou `ANALYTICS_HOJE`) — a
  demo **não envelhece**. No modo Power BI o "hoje" é `TODAY()` normal.

**Para o agente:** se o usuário estiver na demo, os nomes de clientes/vendedores e os valores são
**sintéticos**. As fórmulas e telas são as mesmas.

---

## 19. Glossário

| Termo | Significa |
|---|---|
| **CODUSUR** | Código do vendedor no Winthor |
| **CODSUPERVISOR** | Código do supervisor (agrupa vendedores num "time") |
| **CODUSUR1** | Vendedor de **cadastro** do cliente (`PCCLIENT`) — base do RBAC por cadastro |
| **CODEPTO** | Departamento de produto (33 valores) |
| **DTSAIDA / DTENT** | Data da venda / data de entrada da devolução no estoque |
| **NUMTRANSVENDA** | Identificador único da transação de venda (usado no ticket médio) |
| **Ciclo pessoal** | Mediana dos intervalos entre compras do cliente (12m), com piso de 7 dias |
| **Recência (R)** | Dias desde a última compra |
| **Positivação** | Cliente que comprou no período |
| **Cobertura** | % da carteira em dia (comprou há ≤ N dias) |
| **Base morta** | Clientes na faixa 91+ (dormentes) |
| **Receita/Lucro perdido projetado** | Estimativa do que se deixa de faturar/lucrar se o cliente atrasado não for resgatado |
| **Cohort** | Turma de clientes com 1ª compra no mesmo mês |
| **YoY** | Comparação com os 12 meses anteriores |
| **Drill 360°** | Painel lateral com todos os detalhes de uma entidade |
| **RBAC** | Controle de acesso por papel |
| **Área** | Comercial ou Compras — o que a PESSOA acessa |
| **Módulo** | O que a EMPRESA contratou nesta instância (`MODULOS`) |
| **RCA** | Representante Comercial Autônomo / o app do vendedor no Totvs — a referência dos números |

---

## 20. Perguntas frequentes (FAQ do agente)

### Números e alinhamento
- **"O número bate com o RCA?"** Sim. Receita e Lucro contam a devolução por **DTENT** (§3.1,
  §4.1, §4.2), validado centavo a centavo.
- **"Por que o YoY do painel é diferente do YoY do Power BI?"** O app **recalcula** o YoY (§4.4):
  a medida nativa diverge e não aceita filtro de supervisor. O ranking de vendedores usa 365 dias
  exatos para bater com o BI do cliente.
- **"A Carteira mostra números totais mesmo com filtro de vendedor?"** Sim — o recorte é por
  **cadastro** (quem é dono do cliente), mas os valores exibidos são os **totais do cliente**.
- **"Metas: por que clientes/mix não somam?"** São `DISTINCTCOUNT` — um cliente atendido por 2
  vendedores conta 1× no total (§12.3). Só venda e rentabilidade são aditivas.
- **"Por que a meta não mostra o time X?"** Porque nenhum vendedor dele tem meta cadastrada no
  mês (§12.4).

### Conceitos
- **"Cobertura é positivado ou não positivado?"** Cobertura % é a fatia **positivada** (comprou
  há ≤ N dias). "Carteira (total)" é o universo; "Positivados" são os que compraram na janela.
- **"Por que meu RCA aparece com 0% e poucos clientes?"** É **amostra pequena** (< 5 clientes) —
  o % vira ruído. Olhe o número absoluto e a receita em risco.
- **"Qual a diferença entre Mix e Radar?"** Mix = parou de comprar um **departamento**; Radar =
  parou de comprar um **produto**.
- **"Por que a régua fixa e a personalizada dão status diferentes?"** A fixa usa dias absolutos
  (10/30/45); a personalizada usa o ciclo do próprio cliente (`dias ÷ ciclo`) — §4.5.
- **"Um cliente pequeno e fiel cai em qual segmento?"** `loyal` — os segmentos de fidelidade
  **não exigem M** de propósito (§4.8).
- **"Por que o cliente sumiu da Lista do Dia?"** Porque ele comprou: a recência mudou e a
  previsão foi para frente (§6.2).
- **"Cliente com uma compra só aparece na Lista do Dia?"** Não — sem 2 compras não há ciclo, e
  sem ciclo não há previsão (§4.5, §4.7).
- **"Retenção: se o cliente pulou um mês e voltou, conta?"** Não naquele mês (retenção é
  **não-cumulativa**); conta no mês em que efetivamente comprou (§11).

### Acesso e configuração
- **"Como mudo o limiar do alerta de cobertura?"** Admin → Cobertura de carteira → Limiar (%).
  Vale na hora, sem redeploy (§13.4).
- **"Liberei o usuário e ele não vê Compras."** Verifique os dois níveis: a **área** do usuário
  (Admin → Acesso ao sistema) **e** o `MODULOS` da instância. Sem o módulo contratado a rota nem
  existe (404) — §2.1.
- **"O comprador vinculado trava o que ele vê?"** Não. É **filtro inicial** — ele pode trocar.
- **"Mudei o tema e voltou ao escuro em outra máquina."** O tema é gravado no banco e segue a
  pessoa; no carregamento **o banco vence** o `localStorage` (§2.3).
- **"Errei a senha e travou."** Bloqueio progressivo: 5 erros → 15 min, depois 1 h, 4 h. Um admin
  pode liberar na hora pelo botão **desbloquear** (§2.4, §14.2).

### Operação
- **"A tela está lenta / dando erro."** Provável refresh do BI em andamento; espere 5–10 min
  (§17.3). Segunda visita dentro do TTL é sempre rápida (§17.2).
- **"O e-mail não chegou."** Confira: `CRON_HABILITADO`, "Cron ativo" marcado, horário e
  frequência, e — no caso do alerta de cobertura — se havia **alguém abaixo do limiar** (sem isso
  ele não envia) — §16.
- **"Os dados desta tela são reais?"** Se o endereço é `demo.jogasolucoes.com.br`, **não**: são
  sintéticos (§18).

---

*Fim do manual do Comercial. Dúvidas de estoque/compras: `docs/MANUAL_COMPRAS.md`.
Quando o comportamento divergir deste texto, o código manda.*
