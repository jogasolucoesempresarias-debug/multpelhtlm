# Manual do Módulo COMERCIAL — JOGA Analytics

**Versão 4.0 · Atualizado em 06/10/2026 · Base de conhecimento do agente de IA de dúvidas.**

> **Escopo:** este manual cobre o **módulo Comercial** (Dashboard, Carteira, Vendedores, Categorias,
> Curva ABC, Mix, Radar, Tendências, Metas, Gerencial, Recuperação, Performance, Evolução, Plano de
> ação, Agente de IA) e a **plataforma comum** (login, áreas, tema, cache, e-mails, Admin). O
> **módulo Gestão de Estoque** (antigo "Compras") tem manual próprio: **`docs/MANUAL_COMPRAS.md`** —
> pergunta sobre estoque, ruptura, pedido de compra, validade, orçamento de compras ou fornecedor vai
> para lá.
>
> **O que mudou desde a v3.0 (28/07/2026):** régua ÚNICA de cobertura (Gerencial, Vendedores e
> Performance passaram a usar a mesma conta — §3.5); positivação dos vendedores corrigida (§7);
> "Clientes Novos" do Dashboard corrigido e comparação do mês contra o MESMO PERÍODO do ano anterior
> (§5); páginas novas **Curva ABC** (§9), **Recuperação** (§15), **Performance** (§16) e **Evolução da
> carteira** (§17); **Plano de ação por cliente** (§18); **Agente de IA do Comercial** (§19); Carteira
> ganhou classe ABC de clientes, praça e margem 12m (§6); Radar ganhou cards de total (§11);
> **"Saldo de recuperação"** com sinal positivo = bom (§15.4).
>
> **Regra de precedência:** quando o comportamento observado divergir deste manual, **o código
> manda**. Fontes da verdade: `server.py` (rotas/DAX), e os módulos puros `rfm.py`, `cohort.py`,
> `metas.py`, `cobertura.py`, `positivacao.py`, `recuperacao.py`, `potencial.py`,
> `performance_comercial.py`, `curva_abc.py`, `plano_cliente.py`, `evolucao_carteira.py`;
> `provider_sql.py` (modo Postgres/demo).

---

## 0. Como usar este manual

Organização: **plataforma → conceitos → fórmulas centrais → uma seção por página → componentes
comuns → Admin → acesso → e-mails → operação → glossário → FAQ**. Cada seção é auto-contida (pensada
para busca/RAG).

Para responder uma pergunta:
1. Identifique a **página** (§5 a §17) — ela lista filtros, cards, colunas, drills e exports.
2. Se a dúvida é sobre **como a conta é feita**, vá a §3 (conceitos) e §4 (fórmulas).
3. Se é sobre **quem vê o quê**, vá a §21 (RBAC) e §2 (áreas).
4. Se é "por que o número está diferente do ERP / da outra tela", vá a §26 (glossário — as palavras
   que têm mais de um sentido) e §27 (FAQ).

**Regra de ouro dos números:** tudo é alinhado ao **RCA do ERP (Totvs/Winthor)**. O app **não**
usa cegamente as medidas nativas do Power BI quando elas divergem do que o vendedor vê no
Winthor. Ver §3.1.

**Regra de ouro das respostas:** sempre dizer **qual régua** e **qual período** o número usa. Várias
palavras têm mais de um sentido no sistema ("positivação", "receita em risco", "curva ABC", "ticket
médio" — §26). E sempre citar o **código** do cliente, vendedor ou produto, para que a pessoa
consiga conferir no ERP.

---

## 1. O que é o sistema

**JOGA Analytics** é um painel web (Flask) que lê o **Power BI** do cliente (modelo
Totvs/Winthor, Oracle) e entrega análise comercial e de gestão de estoque num único login.

- **Cliente atual:** Multpel (o produto é da JOGA; "Multpel" aparece em rótulos por ser dado do
  cliente, não marca do produto).
- **Produção:** `painel.jogasolucoes.com.br`.
- **Demonstração:** `demo.jogasolucoes.com.br` — **dados sintéticos**, não toca o BI de ninguém
  (ver §25).
- **Atualização dos dados:** o BI do cliente atualiza várias vezes ao dia; o cabeçalho mostra a
  data/hora do último refresh concluído (§24.1).

### 1.1 Os módulos

| Módulo | O que responde | Manual |
|---|---|---|
| **Comercial** | Para quem vendemos, quem vende, o que vende, quem está esfriando, quem recuperamos, batemos a meta, como cada vendedor performa | **este** |
| **Gestão de Estoque** (chave interna `compras`) | O que comprar, o que vai vencer, o que está parado, ruptura, orçamento de compras, performance do comprador | `docs/MANUAL_COMPRAS.md` |
| **Agente de IA** (chave `ia`) | Analista que conversa sobre os números das telas | opcional, contratado à parte (§19) |

A **Administração** é um terceiro lugar neutro — não pertence a nenhum dos módulos.

### 1.2 Mapa das páginas do Comercial

| Menu | Rota | Pergunta que responde | Seção |
|---|---|---|---|
| Dashboard | `/` | Como está o mês? | §5 |
| Carteira | `/carteira` | Para quem vendemos, quem está esfriando, quem ligar hoje? | §6 |
| Vendedores | `/vendedores` (+ cockpit `/vendedor/<cod>`) | Quem vende e quem cobre a própria base? | §7 |
| Categorias | `/categorias` | Quais departamentos vendem e lucram? | §8 |
| Curva ABC | `/abc` | Quais produtos fazem 80% da venda do meu escopo? | §9 |
| Mix | `/mix` | Quem parou de comprar um departamento? | §10 |
| Radar | `/radar` | Quais produtos estão perdendo clientes? | §11 |
| Tendências | `/tendencias` | A base nova continua comprando? | §12 |
| Metas | `/metas` | Estamos batendo a meta? | §13 |
| Gerencial | `/gerencial` | A carteira está sendo atendida? | §14 |
| Recuperação | `/recuperacao` | Quanto está em risco, quanto voltou, quem trouxe de volta? | §15 |
| Performance | `/performance` | Qual a nota de cada vendedor no mês fechado? | §16 |
| Evolução | `/evolucao` | A carteira está melhorando mês a mês? | §17 |
| *(oculta, admin)* | `/uso` | Quem está usando a ferramenta? | §20.4 |

---

## 2. Acesso: login, áreas, portal e tema

### 2.1 Um login, duas áreas

O usuário loga uma vez. O que ele enxerga é a **interseção** de duas coisas:

- **`MODULOS`** — o que a **empresa** contratou nesta instância (variável de ambiente:
  `comercial,compras`, `comercial` ou `compras`, mais `ia` opcional). Módulo não contratado → a rota
  **nem existe** (**404**).
- **`areas`** do usuário (coluna JSONB em `multpel_users`) — o que **a pessoa** acessa.
  **Default `["comercial"]`**: ninguém ganha Gestão de Estoque por acidente; o admin libera pessoa a
  pessoa. Usuário sem a área → **403**.

**Para onde vai depois do login:**
- 1 área efetiva → direto para ela (`/` no Comercial, `/estoque/` na Gestão de Estoque) — sem portal.
- 2 áreas → respeita o "fixar" do portal (coluna `area_padrao`: `portal` | `comercial` |
  `compras`). Default `portal`.
- **O "fixar área padrão" vale para ABRIR o sistema, não para navegar** (09/2026). Quem fixou Gestão
  de Estoque e reabre o navegador cai no estoque; mas clicar em "Dashboard" ou no card do Comercial
  leva ao Comercial normalmente. (Aba restaurada pelo navegador pode, raramente, abrir no Comercial —
  é o custo aceito para nunca prender a pessoa numa área.)

### 2.2 Portal (`/portal`) e seletor de área

Tela de escolha entre **Comercial** e **Gestão de Estoque**, mais uma faixa para a **Administração**
(só para admin). Um **seletor de área fixo no topo** de todas as páginas troca entre os dois sem
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
- Cookie `HttpOnly` + `SameSite=Lax` + expiração de **12 h**; `Secure` em produção.
- Rastro de login com IP em `multpel_log`. Enumeração por tempo mitigada.
- **`SECRET_KEY` é obrigatória em produção** — sem ela o app **não sobe** (proposital).

### 2.5 Colunas de acesso em `multpel_users`

`areas` (JSONB, default `["comercial"]`) · `area_padrao` · `codcomprador` (filtro **inicial** da
Gestão de Estoque, não trava) · `relatorios_estoque` (JSONB — quais relatórios de estoque recebe por
e-mail) · `tema` · `codusur` · `codsupervisores` · `emails_cc` · `segmentos_rfm` · `cron_*` ·
`tentativas_falhas` / `bloqueado_ate` / `bloqueios_seguidos`.

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

Classifica cada cliente em 3 dimensões, com nota **1 a 5** (quintis):

- **R (Recência)** — dias desde a última compra. **Menos dias = nota maior.**
- **F (Frequência)** — nº de compras/notas em 12m. Mais = melhor.
- **M (Monetário)** — **lucro** gerado em 12m. Mais = melhor.

**Por que quintis e não valores fixos?** Porque "muita compra" varia por perfil de cliente. O
quintil normaliza: sempre 20% da base no topo.

**Detalhe dos cortes:** os de **R** são calculados sobre **toda a base** (24m). Os de **F** e **M**
só sobre clientes **ativos** (≥1 compra em 12m); os inativos recebem **F=1 e M=1** direto.

### 3.3 Ciclo pessoal e régua personalizada

Cada cliente tem um **ciclo pessoal** = **mediana** dos intervalos entre compras nos últimos
12m, com **piso de 7 dias**. Cliente com menos de 2 compras não tem ciclo (`None`).

A **régua personalizada** mede o atraso **relativo ao padrão do próprio cliente**
(`dias ÷ ciclo`), não a um número fixo. Uma padaria que compra a cada 7 dias e está há 14 sem
comprar está **pior** (2× o ciclo) que um hotel que compra a cada 90 e está há 30.

### 3.4 Clientes fora da análise por cliente

**CONSUMIDOR FINAL** (cadastros genéricos, ex.: codcli 1, venda de balcão no caixa RCA 4) fica
**fora** de toda análise POR CLIENTE — Carteira, classe ABC, positivação, Recuperação, drill do
Radar, Evolução. A **venda** dele continua nos totais (Dashboard, Categorias, Curva ABC de
produtos). Motivo (09/2026): eram R$ 2,2 mi/12m em 26 mil notas de 29 vendedores e ele aparecia
como "Campeão classe A" na Carteira.

### 3.5 Cobertura da carteira — a RÉGUA ÚNICA (desde 29/09/2026)

Até setembro havia três contas diferentes com o mesmo nome nas telas Gerencial, Vendedores e
Performance. Decisão do João Victor (28–29/09/2026): **uma conta só, em todas as telas e em todos os
níveis** (empresa, time, vendedor):

```
Base ativa   = clientes CADASTRADOS no vendedor (PCCLIENT.CODUSUR1) que compraram
               nos últimos 365 dias (de qualquer vendedor)
Positivado   = cliente da base que comprou nos últimos 60 dias, de QUALQUER vendedor
Cobertura %  = positivados ÷ base ativa
```

- **"De qualquer vendedor"** é proposital: força o vendedor a manter na própria base os clientes que
  ele de fato atende (cliente que só compra de outro deve ser transferido).
- **Empresa = Σ times = Σ vendedores** (as contagens somam; o % é recalculado).
- **Base < 5 clientes** → o % não é exibido (amostra pequena: caixa/balcão).
- **Onde muda a data de referência:**
  - **Gerencial** → janela móvel de **hoje** (60 dias por padrão, ajustável).
  - **Vendedores e Performance** → janela de 60 dias que termina no **último dia do mês FECHADO**
    (a nota não "anda" todo dia). Por isso os dois números do mesmo vendedor podem diferir.
- **Limiar oficial: 85%** (o piso da nota). Cores: vermelho < 85% · amarelo 85–99,9% · verde 100%.
- Medido no BI real em 29/09/2026: empresa **58,4%** (3.977 de 6.815); mediana dos vendedores 63%.
  36% da base não compra em 60 dias por natureza (comprou 1 vez no ano ou tem ciclo > 60 dias) — é
  por isso que a meta exige limpeza de carteira (ver "mês de limpeza", §16.3).

### 3.6 Positivação — os quatro sentidos

A palavra aparece em quatro lugares com contas diferentes. **Sempre dizer qual:**

| Onde | Conta |
|---|---|
| **Cobertura da base** (Gerencial, Vendedores, Performance) | régua única, §3.5 |
| **Positivação por classe ABC** (Carteira) | % dos clientes da classe que compraram no mês, de qualquer vendedor (§6.1) |
| **Alcance** (Vendedores) | tudo que o vendedor atendeu no mês ÷ base ativa — pode passar de 100% |
| **"Positivados" do cockpit** | clientes da carteira (24m) que compraram em 12m — quase sempre igual aos cadastrados; **não é taxa mensal** |

### 3.7 RBAC — duas réguas de isolamento

O acesso existe em **duas naturezas** (detalhe em §21):

- **Por VENDA** — filtra a transação (`CODUSUR` / `CODSUPERVISOR` na nota): quem **faturou**. Usado
  em Dashboard, Vendedores (venda/lucro), Curva ABC, Metas. Injetado no DAX por
  `aplicar_rbac_dax()`.
- **Por CADASTRO** — filtra pelo cliente registrado no vendedor (`PCCLIENT.CODUSUR1`): de quem o
  cliente **é**. Usado em Carteira, Categorias, Mix, Radar, Tendências, Gerencial, Recuperação,
  Evolução e e-mails. Recorte em Python por `_carteira_no_escopo()`.

Em times de campo as duas réguas concordam em 97–99,8%; em **Lojas e Diretoria divergem 40–66%**
(cliente cadastrado num time e faturado por outro). É a explicação mais comum para "o número do
meu time não bate entre duas telas".

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

### 4.3 Margem — duas réguas
```
Margem do Comercial (Dashboard, Categorias, Curva ABC, Carteira) = Lucro ÷ Receita LÍQUIDA
Margem das METAS                                               = Lucro ÷ Realizado BRUTO (com bonificação)
```
Nunca média de margens: sempre **Σ lucro ÷ Σ venda** do período (ponderada).

### 4.4 Métricas do mês (Dashboard)
```
Ticket Médio         = Receita Líquida ÷ nº de pedidos (DISTINCTCOUNT NUMTRANSVENDA)
Clientes Positivados = DISTINCTCOUNT(CODCLI) no mês
Mix Médio            = medida [TOTAL MIX]
Clientes Novos       = comprou no mês E nunca tinha comprado antes na empresa (§5)
Valor Médio / Kg     = medida [VALOR MEDIO PESO]
```

### 4.5 YoY — duas comparações
- **Cards do mês (Dashboard):** comparam o mês corrente com o **mesmo período** do ano anterior
  (ex.: 01–25/set/26 × 01–25/set/25). O tooltip mostra o período e a contagem de **dias úteis** de
  cada lado, que explica distorções de calendário.
- **Gráfico YoY (12m):** últimos 12 meses × os 12 meses anteriores. Recalculado no app — a medida
  nativa diverge e não aceita filtro de supervisor.
- ⚠️ São perguntas diferentes: "o mês está melhor que o mesmo mês do ano passado?" × "o último ano
  está melhor que o anterior?".

### 4.6 Ciclo pessoal e status de régua
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

### 4.7 Receita / Lucro perdido ACUMULADO
```
se dias_sem_comprar < ciclo:  perdido = 0
senão:
  meses_atrasado             = (dias_sem_comprar − ciclo) ÷ 30
  receita_perdida_acumulada  = (venda_12m ÷ 12) × meses_atrasado
  lucro_perdido_acumulado    = (lucro_12m ÷ 12) × meses_atrasado
```
É **acumulado**: cresce sozinho a cada mês de atraso. Por isso foi renomeado de "Receita em risco"
para **"Receita perdida acumulada"** (09/2026) — não confundir com o **valor mensal em risco** da
Recuperação (§15), que não acumula.

### 4.8 Próximo pedido e prioridade de contato
```
proximo_pedido_previsto = ultima_compra + ciclo_pessoal
prioridade_contato      = (venda_12m ÷ 12) × (dias_sem_comprar ÷ ciclo)   [0 se razão < 1]
```
Clientes sem ciclo não têm previsão e ficam de fora da lista do dia.

### 4.9 Quintis e segmentos RFM
Segmentos canônicos — **a primeira regra que casa vence**:
```
champions (Campeões)   : R=5 e F=5 e M=5
loyal (Fiéis)          : R≥4 e F≥4
cant_lose (Não Perder) : 2≤R≤3 e F≥4
at_risk (Em Risco)     : 2≤R≤3 e F≥3
new (Novos)            : R=5 e F=1
potential_loyalist     : R≥4 e 1≤F≤3
lost (Perdidos)        : R≤2 e F≤2 e M≤2
hibernating (Inativos) : todo o resto
```
`loyal`, `cant_lose` e `at_risk` **não exigem M**: alta R + alta F = fiel, independente do volume.
⚠️ O segmento RFM **"Em Risco"** não é o **"em risco"** da Recuperação (> 60 dias e além do ciclo).

### 4.10 Curva ABC (clientes e produtos)
```
Ordena por venda (desc) e acumula o % da venda total:
  A = até 80% acumulado (fronteira inclusiva)   B = até 95%   C = o resto (e venda ≤ 0)
```
A mesma régua do módulo de Estoque (gate de teste garante). Existem **duas curvas** diferentes no
Comercial: de **clientes** (Carteira, §6.1) e de **produtos** (aba Curva ABC, §9).

---

## 5. Página: Dashboard (`/`)

**Pergunta que responde:** "Como está o mês?"

**Filtro (admin/viewer):** "Supervisor (Time)" — aceita 1 ou vários; sem filtro = empresa toda.
Vendedor/supervisor logado já vê só o seu.

**Cards — linha 1:** Venda Líquida (mês atual) · Lucro Total (mês atual) · Margem (%) · Clientes
Positivados.
**Cards — linha 2:** Ticket Médio · Mix Médio · Clientes Novos · Valor Médio/Kg.

- Embaixo de Venda, Lucro e Clientes: a variação contra o **mesmo período do ano anterior**
  (↗ verde / ↘ vermelho), com o período e os dias úteis no tooltip (§4.5).
- **Clientes Novos** (corrigido em 26/09/2026): cliente que comprou no mês **e nunca tinha comprado
  antes** na empresa (histórico do fato desde jan/2024; se o cadastro tiver `DTPRIMCOMPRA`, ela não
  pode ser anterior ao mês). A medida do BI `[TOTAL CLIENTES NOVO]` contava todo cliente distinto do
  mês (~2.700); o número certo fica na casa das dezenas (66 em set/26, 84 em ago/26).

**Gráficos e tabelas:**
- **Série temporal — 12 meses** (linha dupla): Venda Líquida + Lucro Total por mês, alinhados RCA.
- **YoY — métricas-chave (12m)** (barras): Receita, Lucro, Positivação de Cliente e Mix contra os
  12 meses anteriores.
- **Top 10 departamentos por lucro (12m)** — clique abre os clientes do departamento.
- **Top 10 vendedores por lucro (12m)**.
- **Top 10 clientes por lucro (12m)** — clique abre a **ficha 360°** do cliente (§18.2).

⚠️ O mês corrente é **parcial**: compare sempre com o mesmo período (é o que os cards já fazem).

---

## 6. Página: Carteira (`/carteira`)

**Pergunta que responde:** "Para quem estamos vendendo, quem está esfriando e quem ligar hoje?"
Tem **duas abas**: **Visão Geral (RFM)** e **📞 Próximo Pedido**.

### 6.1 Aba Visão Geral (RFM)

**Universo:** clientes que compraram nos últimos **24 meses** (base RFM), com os **números totais**
do cliente (inclui os "Perdidos", que compraram há 12–24 meses — por isso o total, ~8.600, é maior
que a carteira ativa de ~6.800). Recorte **por cadastro** conforme o RBAC.

**Filtros:** Time · Vendedor · UF · Cidade · Busca livre (cliente, cidade, código, vendedor, time) ·
"Dias s/ comprar" mínimo e máximo (atalhos 10+/30+/60+/90+) · chips de **Segmento** (8) · chips de
**Classe ABC** (A/B/C). Os dropdowns fazem **cross-filter** e listam só quem tem cliente na carteira.

**Visualizações:**
- **8 cards de segmento** e **rosca RFM** (clicar num segmento filtra a tabela).
- **Carteira por praça** (09/2026): barras horizontais por **UF do cadastro**; com uma UF filtrada
  desce para **cidade**. Três medidas (clientes · venda 12m · receita perdida acumulada), top 12 +
  "outras". **Sai do mesmo conjunto filtrado** dos cards e da tabela: clicar em "Perdidos" mostra
  ONDE estão os perdidos. Clique na barra vira filtro.
- **Positivação por classe ABC de clientes** (card): % dos clientes A, B e C que compraram no **mês
  fechado**, de qualquer vendedor (ago/26: A 80,9% · B 56,1% · C 22,3%), e o mês corrente "até o dia
  N" contra o mesmo dia do mês anterior. Clicar numa classe filtra a tabela.
  - **Classe do cliente = a do INÍCIO do mês**: Pareto 80/95 da venda dos 12 meses fechados
    anteriores. (Usar o próprio mês inflaria a positivação em ~2,5–3 pontos.)
- **Receita líquida × Clientes positivados (12m)** — reage aos filtros; clique no mês abre o detalhe.

**Tabela acionável — colunas:** CodCli · Cliente · Cidade/UF · Vendedor · Time · **R (dias)** ·
**F (12m)** · Venda 12m · **Margem 12m** · Média Venda (= venda 12m ÷ 12) · **⚠ Receita Perdida
proj.** (acumulada, §4.7) · Segmento · **Classe ABC** · Telefone. Todas ordenáveis.

- **Margem 12m** (10/2026, sugestão do lead na apresentação): **lucro 12m ÷ venda líquida 12m** do
  cliente (régua do Dashboard e da Curva ABC). A Carteira **não tem seletor de período** — tudo nela
  é 12 meses móveis, recortado pelos filtros da tela. Sem venda no período → "—" (e fica sempre no
  fim da ordenação); margem negativa em vermelho (vendeu abaixo do custo — é informação, não erro).

**Ficha 360° do cliente** (clique na linha): ver §18.2.

**Exports:** CSV (com BOM UTF-8, abre no Excel com acento; inclui `Lucro12m` e `Margem12m(%)`) e PDF
(inclui Margem 12m). O nome do arquivo reflete os filtros ativos. **O export sai com os filtros da
tela.**

**Deep-link (vindo do Gerencial):** a Carteira lê da URL `dias_min`, `dias_max`, `vendedor`, `time`,
`uf`, `cidade`, `segmento`, `busca` — é assim que o clique numa faixa do Gerencial abre exatamente
aqueles clientes.

### 6.2 Aba Próximo Pedido (a "lista do dia")

Previsão de recompra = **última compra + ciclo pessoal** (§4.8).

- **Janela:** "A ligar hoje" · "A ligar nos próximos 7 dias" · "Vencidos (até 15 dias)".
  Cliente parado há mais tempo **não** está aqui: está na **Recuperação** (§15).
- **Cards:** A ligar hoje · Próximos 7 dias · Vencido +15 dias · Receita perdida acumulada ·
  **Com tratativa X de N** (clique = só os sem tratativa) · **⏰ Retornos p/ hoje** (quando houver).
- **Filtros:** Time, Vendedor, Busca, **Tratativa** (com / sem plano registrado).
- **Ordenação:** por **prioridade** (valor mensal × quão vencido está). **Retorno agendado para
  hoje (ou atrasado) aparece EM DESTAQUE no topo**, mesmo fora da previsão do ciclo.
- **Coluna Plano:** o selo do plano de ação do cliente (§18.1) — clique para registrar a ligação.
- Quando o cliente compra, ele **sai automaticamente** da lista (a recência muda).
- Clique na linha → **top produtos a oferecer** àquele cliente.
- **Export CSV/PDF** em formato de agenda.

---

## 7. Página: Vendedores (`/vendedores`) e Cockpit (`/vendedor/<codusur>`)

**Pergunta que responde:** "Quem vende e quem cobre a própria base?"

### 7.1 Ranking (`/vendedores`)

**Filtros:** Tipo (R = rota, I = interno, P) · Supervisor · UF · Busca · toggle "mostrar internos".
**Sempre excluídos:** vendedores técnicos/fictícios (**999** transferência, **900** jurídico,
**4** caixa, **272** Multpel, **34** prospecção) e os com `PCUSUARI[BLOQUEIO]='S'`.

**Colunas:** rank · Vendedor · Tipo · Time · UF · Venda 12m · Lucro 12m · Ticket · **Cobertura base
(mm/aa)** · **Fora da base** · **Mix** · **Deptos** · **Marcas** · **Alcance** · Clientes · YoY.
Ordena por **Lucro 12m** (o ranking **não** é a nota da Performance).

- **Cobertura base** = a régua única (§3.5) no fim do mês fechado. Tooltip: "X de Y clientes da base
  ativa". ⚠️ Corrigida em 24/09/2026: a conta anterior dividia clientes atendidos pelo **cadastro
  inteiro** (inclusive bloqueados) e deixava 37 de 97 vendedores acima de 100%. **O número mudou sem
  ninguém mexer na operação** (ex.: JOSE JUNIOR 22% → 64%).
- **Fora da base** = clientes de outras bases que ele atendeu no mês fechado, em 4 tipos (tooltip):
  - **fictício** — cliente parado em código fictício (cadastro a transferir);
  - **RCA inativo** — cliente de vendedor que não vende há 3 meses / bloqueado (cadastro a transferir);
  - **liberado** — o cliente estava há mais de 60 dias sem comprar quando ele vendeu (pela regra
    comercial, qualquer vendedor pode atender);
  - **colega** — cliente ativo de um vendedor ativo. É o único tipo que merece conversa.
- **Alcance** = tudo que ele atendeu no mês ÷ base ativa. Pode passar de 100%. É **informativo**:
  somar o "fora da base" na cobertura esconderia a base descoberta.
- **Mix · Deptos · Marcas** = SKUs, departamentos e marcas distintos **por cliente atendido** no mês
  fechado (régua de venda). Medianas do campo: ~11 SKUs · 4,7 deptos · 3,2 marcas.
- **Histograma** "Distribuição: Cobertura da base" (0–20% … 80–100%) e **Top 10 por Lucro 12m**.

⚠️ Na mesma linha convivem janelas diferentes: venda/lucro são **12 meses**; cobertura, mix e
alcance são do **mês fechado**.

### 7.2 Cockpit individual (`/vendedor/<codusur>`)

- **Header de perfil** (dados do PCUSUARI).
- **Cards:** Venda 12m · Lucro 12m (+ posição no ranking) · **Cobertura da base (mm/aaaa)** com "X de
  Y", fora da base e alcance · comparação com a média do time.
- **Alertas acionáveis:** clientes "At Risk" com o **lucro médio mensal** que eles davam e quanto já
  deixou de entrar desde o atraso (corrigido em 09/2026 — antes somava o acumulado e escrevia "/ano");
  top 3 Campeões clicáveis.
- **Gráficos:** série 12m (venda + lucro) · rosca RFM · distribuição de status de régua.
- **Tabela da carteira** do vendedor, com export.

Um vendedor só abre o próprio cockpit; tentar outro devolve **403**.

---

## 8. Página: Categorias (`/categorias`)

**Pergunta que responde:** "Quais departamentos vendem e lucram?"

Análise por **departamento** (`CODEPTO`). Usa CODEPTO — e não `CODCATEGORIA` — porque a categoria
vem **~92% nula** no faturamento.

- **Treemap:** tamanho ∝ venda, **cor ∝ margem** (verde = alta, vermelho = baixa). Clique → top
  clientes do departamento.
- **Top fornecedores** (barras).
- **Tabela:** Venda · Lucro · Margem % · Share · Clientes únicos · Produtos únicos, por depto.

```
por depto (janela 12m): [VENDA LIQUIDA], [LUCRO TOTAL], DISTINCTCOUNT(CODCLI), DISTINCTCOUNT(CODPROD)
Margem = Lucro ÷ Venda líquida        Share = venda_depto ÷ venda_total
```
Não-admin: recorte **por cadastro** — por isso o número de um time aqui não bate com a Curva ABC,
que é por venda.

---

## 9. Página: Curva ABC de produtos (`/abc`)

**Pergunta que responde:** "Quais produtos fazem 80% da venda do meu time / vendedor / cliente, e
com que margem?" (pedido do João Victor, 09/2026: *"uma gerente me pediu a curva ABC de produtos do
time dela"*).

### 9.1 Como é calculada
- Produtos do **escopo** ranqueados por **venda líquida** na janela escolhida; Pareto **A ≤ 80% ·
  B ≤ 95% · C** (mesma régua do Estoque).
- **Régua de VENDA** (quem vendeu), declarada na 1ª linha da tela. ⚠️ Em Lojas e Diretoria a curva
  do time pode diferir da Carteira (que é por cadastro) — por construção.
- **A curva do time é diferente da curva da empresa:** 17% a 40% dos produtos mudam de classe quando
  a curva é do time. É isso que dá sentido à aba.
- **Margem** por item e por classe = **Σ lucro ÷ Σ venda líquida** no período (régua do Comercial;
  nunca média de margens mensais). Item sem venda → "—"; negativa em vermelho.

### 9.2 Controles
- **Período:** 12 meses · 6 meses · 3 meses · **mês atual** (parcial — a tela mostra o intervalo, ex.:
  "mês atual · 01–17/09"). ⚠️ **Trocar o período muda quais itens são A/B/C** (a curva é o Pareto da
  janela).
- **Escopo:**
  - admin/viewer: empresa inteira, ou um **Supervisor** e/ou **Vendedor**;
  - **supervisor**: o próprio time; se tiver mais de uma área, escolhe uma; pode **estreitar para um
    vendedor do time**;
  - vendedor: só ele;
  - **Cliente** (type-ahead, todo perfil): a curva vira o Pareto **das compras daquele cliente**
    (sempre somado ao RBAC — o vendedor vê o que ELE vendeu ao cliente).
- **Filtros de tela:** classe · departamento · fornecedor · busca.

### 9.3 O que a tela mostra
- **KPIs por classe** (clicáveis): nº de itens, % da venda e margem de A, B e C.
- **Gráfico de Pareto** (top 100) e **tabela** ordenável (rank, classe, código, descrição, depto,
  fornecedor, venda, lucro, margem, clientes, % acumulado).
- **Drawer do item** (clique na linha): **série mensal de 12 meses** no escopo (sempre 12m, com a
  janela ativa em destaque) e **quem do time vende** o item.
- **Amostra pequena** (< 200 produtos ou venda < R$ 100 mil proporcional à janela): faixa amarela de
  aviso — a lista aparece, mas "curva A" de poucos itens é ruído. Com **cliente** filtrado o aviso
  não acende: a régua declara que é concentração do que ele compra.
- **Export CSV/PDF:** saem com os filtros da tela; o resumo do PDF é do escopo inteiro.

---

## 10. Página: Mix Abandonado (`/mix`)

**Pergunta que responde:** "Quem parou de comprar um DEPARTAMENTO?" (cross-sell perdido)

Lista pares **Cliente × Departamento**: quem comprava o departamento nos últimos 12m mas **parou**
nos últimos N dias — continuando a comprar outras coisas.

- **Filtros:** Período (30/60/90/180 dias) · Departamento · Fornecedor · Vendedor/Busca.
- **Cards:** nº de pares parados · Lucro 12m em risco · Top 5 maiores perdas.
- **Tabela:** Cliente · Cidade/UF · Departamento · Última compra · **Dias parado** (amarelo 30–60,
  laranja 60–90, vermelho 90+) · Venda/Lucro da categoria 12m · Vendedor.
- **Drills:** clique no cliente → **top 5 departamentos perdidos**; drill de **fornecedores
  abandonados por cliente**, com export CSV e PDF próprios.
- **Export** CSV/PDF da lista completa.

Cliente que parou **tudo** não está aqui: está na **Recuperação** (§15).

---

## 11. Página: Radar (`/radar`)

**Pergunta que responde:** "Quais PRODUTOS estão perdendo clientes, e o que cada cliente parou de
comprar?"

Compara a **janela recente** com a **janela anterior** do mesmo tamanho (padrão 60 × 60 dias).

### 11.1 Cards de total (10/2026)
**Queda de receita** · **Produtos em alta** · **Saldo do período** · **Concentração da queda** (top 10).
- **Queda de receita** é BRUTA (soma só os produtos que caíram). Por isso o **saldo** (crescimento −
  queda) vai ao lado: "R$ 150 mil em queda" num escopo que cresceu no total não é alarme.
- Somam a **lista inteira**, não só as linhas exibidas. O filtro de fornecedor recorta os quatro.
- **Queda e Em alta são um par de abas:** clicar em "Produtos em alta" troca a tabela para os itens
  que **cresceram** (receita ganha · % de alta · clientes antes → agora; venda anterior zero =
  "novo") — responde "o cliente parou ou trocou de item?". Ordenar por % de alta traz itens de base
  minúscula ao topo; a ordenação padrão é por receita ganha.

### 11.2 Board e drills
- **Board:** produtos ordenados por queda de receita.
- **"Clientes perdidos"** = quem comprou o produto na janela anterior e **cuja última compra ficou
  antes da janela recente** (diferença de conjuntos — corrigido em 08/2026; antes era um saldo que
  escondia quem parou quando entravam clientes novos).
- **Status do cliente** em relação ao produto: perdido · parou · esfriando (compra < 50% do que
  comprava) · ativo; e **"Trocou (outro do depto)"** quando migrou para um item similar — separa
  perda real de substituição.
- Drill por produto, por cliente e série produto × cliente. **Exports** CSV/PDF em todos os níveis.
- Escopo por **cadastro** (o board e o drill partem do mesmo conjunto de clientes).

⚠️ O board compara 60 × 60 dias; o drill olha 12 meses — são perguntas diferentes, e o tooltip
declara. A "receita em risco" do Radar (venda 12m dos produtos que o cliente parou) é um **terceiro**
sentido de "receita em risco" (§26).

**Mix × Radar:** Mix = parou de comprar um **departamento**; Radar = parou de comprar um **produto**.

---

## 12. Página: Tendências (`/tendencias`) — Cohort Retention

**Pergunta que responde:** "A base nova continua comprando?"

Cada **linha** é uma turma (cohort) que fez a **1ª compra** num mês; cada **coluna** é quantos meses
depois (M+0 … M+12); cada **célula** é o **% da turma que comprou naquele mês específico**.

⚠️ **Retenção não-cumulativa:** pular um mês não conta naquele mês.
```
mes_aquisicao(cliente) = min(meses com compra)
retido em M+N  ⇔  existe compra no mês (mes_aquisicao + N)
```

- **Cores:** 🟢 ≥70% · 🟡 40–70% · 🔴 <40% · **M+0 = 100%** por definição.
- **Filtros:** Período (12/18/24 meses) · Vendedor e Supervisor (em cascata).
- **Drills:** célula → clientes daquele bucket; rótulo da linha → cohort completo.
- **Cache:** 24 h (é a query mais pesada do app).

---

## 13. Página: Metas (`/metas`)

**Pergunta que responde:** "Estamos batendo a meta por vendedor/time?"

Réplica das **4 telas META** do BI do cliente: **Venda (valor)**, **Rentabilidade (lucro)**,
**Clientes** e **Mix**.

### 13.1 De onde vem cada número

| Peça | Fonte |
|---|---|
| **Meta (alvo)** | **Postgres** — tabela `multpel_metas`, digitada no app (o app é dono do alvo). |
| **Realizado — mês CORRENTE** | Dataset **META** (pedidos: `PCPEDC`/`PCPEDI`) — meta é sobre **PEDIDO**, não faturamento. |
| **Realizado — mês FECHADO** | Dataset **RCA** (faturamento) — o dataset META esvazia ao virar o mês. |
| **Dias úteis** | Calendário de meta do dataset (mês corrente) ou cálculo de dias úteis (mês fechado). |

### 13.2 Fórmulas (dias ÚTEIS, não corridos)
```
Projeção        = realizado × DiasMetaMes ÷ DiasMetaDecorridos
Falta           = max(0, Meta − Realizado)
Necessidade/dia = Falta ÷ DiasMetaRestantes
% Realizado     = Realizado ÷ Meta
% Proj. Meta    = Projeção ÷ Meta
```
No mês corrente a projeção usa a medida oficial `[Projecao]` do dataset; no fechado, o run-rate.

⚠️ **Margem das Metas = lucro ÷ realizado BRUTO** (com bonificação) — a régua da medida oficial
`[MARGEM(%)]` do dataset META, conferida nos 9 supervisores. **Não dividir pelo sem-bônus**: inflava
em até 6,3 pontos (o erro cresce com o bônus do time).

### 13.3 Aditividade
**Venda e rentabilidade somam** (vendedor → supervisor → total). **Clientes e mix NÃO somam** —
são contagem distinta: um cliente atendido por 2 vendedores conta 1× no total.

### 13.4 Universo do painel
Só entram **supervisores com pelo menos 1 vendedor com meta cadastrada** no mês. Times sem meta
(ex.: DIRETORIA, E-COMMERCE) não entram. Sem meta no mês → `%` nulo (não é 0%).

### 13.5 Editor de metas (Admin)
CRUD de meta por vendedor/mês, salvamento em lote e importação. **Sugestão de meta**: mesmo mês do
ano anterior × (1 + crescimento) ou média dos 3 meses anteriores × (1 + crescimento).

⚠️ As metas alimentam a **Performance** (§16): **sem meta cadastrada do mês fechado, a nota sai
parcial** (ou sem nota).

---

## 14. Página: Gerencial — Cobertura de Carteira (`/gerencial`)

**Pergunta que responde:** "A carteira está sendo atendida? Quem precisa de atenção?"

Placar de **cobertura** por **Empresa → Time → Vendedor**, na **régua única** (§3.5), calculado sobre
a carteira já carregada (sem query nova ao BI), respeitando o RBAC por cadastro.

### 14.1 Definições
```
Base (denominador) = carteira ATIVA: clientes do cadastro com compra em 365 dias
Cobertura clientes = positivados (compraram na janela, de qualquer vendedor) ÷ base
Cobertura valor    = venda 12m dos positivados ÷ venda 12m da base
Dentro do ciclo    = clientes com status personalizado ∈ {ok, normal} ÷ base   (a régua "justa")
Receita perdida acumulada = Σ receita_perdida_acumulada (§4.7)
Base morta         = clientes da base na faixa 91+ dias
```
- **Janela "em dia":** 30 / 45 / **60** dias (padrão 60 desde 09/2026; o valor salvo no Admin vence o
  padrão).
- **Cobertura por clientes × por valor** lado a lado: base atendida × faturamento protegido (ex.: 35%
  dos clientes e 82% do valor = os grandes estão em dia, a cauda sumiu).

### 14.2 Faixas de recência
Faixas **fixas** (não mudam com a janela), com nº de clientes e venda 12m:
`0–15 · 16–30 · 31–45 · 46–60 · 61–90 · 91+` (+ rollup 0–30 "em dia"). Clicar numa faixa abre a
**lista exata** daqueles clientes na Carteira (deep-link, §6.1).

### 14.3 Layout e drill-down
- **Banner:** "Hoje N time(s) e M vendedor(es) estão abaixo de X%" — é o que o alerta por e-mail
  dispara.
- **Placar do escopo:** Cobertura por clientes · Cobertura por valor · Receita perdida acumulada ·
  Dentro do ciclo (justo). **Cores por faixa:** vermelho < 85% · amarelo 85–99,9% · verde 100%.
- **Distribuição por faixa:** barra empilhada + tabela.
- **Ranking (pior → melhor):** Nome · Positivados / Carteira · Cobertura clientes · Cobertura valor ·
  Dentro do ciclo · Receita perdida acumulada · Base morta · ⚑. Linha de time → drilla nos
  vendedores; linha de vendedor → abre o cockpit.
- **Só PESSOAS entram no "abaixo do limiar"** (09/2026): base < 5, códigos fictícios e canais não
  contam (tela e e-mail com a mesma regra).
- **⬇ Lista de limpeza** (CSV, no drill de time/vendedor): clientes da carteira ativa que **não
  compraram na janela** — para vender ou transferir no "mês de limpeza" (§16.3).
- **Breadcrumb:** Empresa ▸ Time ▸ Vendedor.

### 14.4 Limiar e escopo
- **Limiar de baixa performance:** configurável no **Admin** (padrão **85%**), vale na hora.
- **Escopo:** admin/viewer veem a empresa; supervisor vê seus times; vendedor vê a própria carteira.

---

## 15. Página: Recuperação — Carteira em risco × recuperada (`/recuperacao`)

**Pergunta que responde:** "Quanto da carteira está em risco, quanto voltou a comprar, quem trouxe
de volta e quanto dinheiro está na mesa?" (09/2026, pedido do cliente).

### 15.1 Réguas (decididas pelo João Victor)
```
EM RISCO  = mais de 60 dias sem comprar  E  além do próprio ciclo de compra do cliente
            (o ciclo evita chamar de "sumido" quem compra naturalmente a cada 3 meses)
PERDIDO   = mais de 365 dias sem comprar
INATIVO NO ERP = 91+ dias — o Winthor marca sozinho; aqui é só uma marca na lista
RECUPERADO no mês = a 1ª compra do mês aconteceu com o cliente EM RISCO ou PERDIDO
VALOR MENSAL = venda dos 365 dias que terminam na última compra ÷ 12
```
- **60 dias é a regra comercial** da empresa (depois disso qualquer vendedor pode atender).
- **O crédito da recuperação é de QUEM VENDEU**; o dono do cadastro aparece ao lado.
- **Valor mensal** não acumula (≠ "receita perdida acumulada" do Gerencial).
- Os 60 e 365 dias são parâmetros (`/api/admin/config/recuperacao`, ajustados pelo time técnico).

### 15.2 Controles e régua
- **Mês:** os 12 meses fechados + o mês corrente (marcado "parcial" — os números ainda vão mudar).
- **Breadcrumb:** Empresa ▸ Time ▸ Vendedor (clique numa linha da tabela para descer).

### 15.3 Cards
- **Carteira em risco** — valor mensal dos clientes em risco no fim do mês + nº de clientes.
- **Dinheiro na mesa** — camada **a** (o valor mensal em risco) + camada **b** (positivação abaixo da
  referência, só de clientes ativos — §15.7). Nada conta duas vezes.
- **Venda recuperada** — venda feita no mês aos clientes que voltaram (do risco **e** da base perdida).
- **Recuperação que ficou** — dos recuperados de 2 meses atrás, quantos voltaram a comprar nos 2
  meses seguintes (medido: só ~53–59% ficam).

### 15.4 Ponte do mês e Saldo de recuperação
```
Em risco no início + Entraram em risco − Recuperados do risco − Viraram perdidos = Em risco no fim
```
- Cada caixa mostra **clientes** e, embaixo, o **valor mensal** (R$ por mês; o valor exato no
  tooltip).
- **A ponte fecha no zero** (só a da empresa fecha por construção; no recorte de time/vendedor a
  ponte é da **base** — dono do cadastro).
- Clientes que voltaram da **base perdida** (+365 dias) não passam pelo estoque em risco: aparecem
  numa nota à parte. Por isso o card "Venda recuperada" e a coluna "Recuperado da base" contam
  **recuperados do risco + resgatados da perdida**.
- **SALDO DE RECUPERAÇÃO = Recuperados do risco − Entraram em risco** (clientes e R$/mês).
  - **Positivo (verde) = recuperou mais do que perdeu** (bom). Negativo (vermelho) = perdeu mais do
    que recuperou.
  - ⚠️ Mudou de sinal em **06/10/2026** (pedido do João: "na nossa cabeça + é bom e − é ruim"). Antes
    era "entraram − recuperados" (positivo = ruim). **Mesma informação, sinal invertido.**
  - Os "viraram perdidos" **não** entram no saldo: saíram do risco por motivo ruim e têm caixa própria.
  - O R$ tem cor **própria**: clientes e R$ podem discordar (ex.: recuperou 7 e perdeu 6 clientes,
    mas os 6 valiam mais → +1 cliente e −R$ 818/mês).

### 15.5 Gráfico "12 meses — entraram em risco × recuperados"
- **Barra vermelha** = entraram em risco no mês · **barra verde** = recuperados do risco ·
  **linha** = saldo de recuperação (recuperados − entraram). **Linha acima do zero = recuperou mais
  do que perdeu.**
- Botão **Clientes | R$/mês** (mesma régua do card).
- **Balão:** os três números, o saldo por extenso, quantos ficaram em risco no fim e a venda feita
  aos recuperados.
- Segue o recorte da tela (empresa, time ou vendedor). Só meses fechados.

### 15.6 Tabelas de Times e de Vendedores (placar)

| Coluna | O que é |
|---|---|
| Em risco | clientes **da base** (dono do cadastro) em risco no fim do mês |
| Valor mensal em risco | valor mensal desses clientes |
| Potencial de positivação | camada b do dinheiro na mesa (§15.7) |
| **Entraram em risco** | clientes da base que entraram em risco no mês · valor mensal |
| **Recuperado da base** · venda no mês | clientes da base recuperados por **qualquer** vendedor; embaixo "X do risco + Y da perdida". O R$ é a **venda feita no mês** |
| **Recuperado pelo time / por ele** · venda no mês | o que os vendedores do time (ou ele) recuperaram, de **qualquer** base — é quem leva o crédito |
| De outra base | recuperações em clientes da base de **outro** time/vendedor |
| **Por outros** | clientes **da base** que **outros** recuperaram (ele/o time não vendeu) |
| **Saldo de recuperação** | recuperados do risco − entraram em risco (clientes · R$/mês) |

**A linha fecha:** `Recuperado da base = (Recuperado por ele − De outra base) + Por outros`.
O saldo se refaz com a linha: `Saldo = (do risco, na coluna "Recuperado da base") − Entraram`.
⚠️ Dois R$ diferentes na mesma linha: ao lado de "recuperado" é **venda no mês**; no saldo e na ponte
é **valor mensal**. A linha **"Sem time (código fictício/sem supervisor)"** fica por último — sem
ela a soma dos times não fecharia com a empresa.

### 15.7 Dinheiro na mesa — camada b (potencial de positivação)
Referência = **percentil 75** da positivação dos vendedores do **mesmo universo** (campo ≠ lojas ≠
telemarketing) em cada **classe ABC** — "o que 1 em cada 4 vendedores já faz". O gap de cada
vendedor até essa referência, × o ticket do cliente, é o potencial. Clientes em risco/perdidos ficam
fora (já estão na camada a). Medido em ago/26: ~R$ 0,5 mi/mês na empresa.

### 15.8 Listas (embaixo)
- **Em risco hoje** — lista na data de **hoje** (a ponte acima é a posição no fim do mês escolhido;
  a diferença é o que mudou desde então). Ordem: **valor mensal × chance de voltar no mês**, medida
  em 16.790 cliente-mês: 61–90 dias **32,5%** · 91–180 dias **15,2%** · 181–365 dias **4,6%**.
  Colunas: cliente, cidade, dono, última compra, dias (selo **ERP** se 91+), ciclo, valor mensal,
  chance, telefone, **Plano** (§18.1).
- **Recuperados no mês** — quem vendeu, dono, marca "outra base", voltou de (risco/perdido), dias
  parado, valor mensal, venda no mês, Plano.
- **Filtro "Plano"** (06/10/2026): Todos · Sem plano · Com plano · ou pela **última ação**
  registrada (Transferir, Retorno agendado, Pedido prometido, Não compra mais, Sem contato, Ligação
  feita). Ex.: o supervisor filtra "Transferir" para ver quais clientes precisam mudar de carteira. O
  filtro é aplicado no servidor, antes do limite de 300 linhas — não esconde ninguém.
- **⬇ CSV** com todas as linhas, **com o mesmo filtro** e as colunas "Plano (último)" e "Data do
  plano".

### 15.9 Escopo
Supervisor vê o próprio time; vendedor vê a própria base e o que ele recuperou. Os números são do
mês fechado escolhido; a primeira carga do dia leva ~20–40 s (26 meses de compras em blocos).

---

## 16. Página: Performance Comercial (`/performance`)

**Pergunta que responde:** "Qual a nota de cada vendedor no mês fechado, e onde ele perde pontos?"

### 16.1 A nota (0–10)
Média ponderada de 5 indicadores do **mês fechado**. **Pesos padrão do cliente** (editáveis por
competência — §16.4):

| Peso | Indicador | Medida |
|---|---|---|
| 35 | **Rentabilidade** | % de atingimento da meta de rentabilidade (Metas; margem pelo bruto) |
| 25 | **Cobertura** | % da régua única (§3.5) no fim do mês fechado; meta = 100% |
| 20 | **Mix** | % de atingimento da meta de mix |
| 10 | **Receita** | % de atingimento da meta de venda |
| 10 | **Frequência** | pedidos por cliente positivado no mês (mín. 10 clientes) |

### 16.2 Escalas
- **Atingimento de meta** (rentabilidade, receita, mix **e cobertura**), em faixas definidas pelo
  João (28/09/2026):
  ```
  < 85%          → 0
  85 a 89,99%    → rampa de 6 a 7
  90 a 99,99%    → 9
  ≥ 100%         → 10
  ```
  É régua de META: 90% e 99% valem o mesmo de propósito. ⚠️ O salto em 85% (0 → 6) é decisão dele.
  O % é **truncado** a 1 casa (nunca arredondado): 99,96% aparece 99,9%, não 100%.
- **Frequência:** escala linear p10 → 0 e p90 → 10, calibrada no histórico **por universo** e
  congelada com versão (`NOTA_VERSAO`, hoje 3).
- **Universos não se comparam:** ranking separado para **campo · lojas · telemarketing** (pelo
  TIPOVEND). Fora do ranking: base < 5 e canais de e-commerce.

### 16.3 Nota parcial, classificação e mês de limpeza
- **Sem meta cadastrada**, o indicador sai da conta e a nota fica **PARCIAL e renormalizada** (com o
  selo do peso medido e do que falta). É preciso medir pelo menos **35% do peso**.
- **Nota parcial fica SEM CLASSIFICAÇÃO** (28/09/2026): aparece em cinza, sem posição e fora do "de
  N".
- **Mês de limpeza da carteira:** até a competência configurada no Admin ("**Cobertura entra na nota
  a partir de**", padrão **11/2026**), a cobertura aparece mas é **informativa** — fora da nota e do
  peso. É o prazo para cada vendedor vender ou transferir os clientes não positivados (lista de
  limpeza no Gerencial, §14.3). Transferência só para vendedor **ativo**, nunca para código fictício.

### 16.4 Tela
- Colunas: Vendedor · Time · **Nota** · Rentabilidade · Cobertura · Mix · Receita · Frequência — em
  cada célula, em cima a **medida** e embaixo a **nota do indicador**.
- **Filtro de Time** (aparece com 2+ times): com time escolhido a posição é **dentro do time**, e a
  geral vai embaixo. Cabeçalho fixo na rolagem.
- **Pesos da nota (admin)**, no fim da página: valem **a partir da competência escolhida** (meses
  anteriores mantêm o peso da época). Auditoria em `multpel_log`.
- A **nota nunca é gravada**: recalcula do ingrediente.

⚠️ **A nota muda sem ninguém mexer na operação** quando muda a escala, o peso ou quando a meta é
cadastrada — avisar antes de publicar.

---

## 17. Página: Evolução da carteira (`/evolucao`)

**Pergunta que responde:** "A carteira está melhorando mês a mês — na empresa, em cada time e em
cada vendedor?" (10/2026: o histórico de performance que a Gestão de Estoque tem, para a carteira).

### 17.1 O que mostra
- **12 meses fechados**, por **Empresa → Time → Vendedor** (clique no time para ver os vendedores).
- **Cobertura** (régua única, §3.5, no fim de cada mês — o último mês bate com a Performance).
- **% da base em risco** (em risco no fim do mês ÷ base ativa; menor = melhor).
- **Saldo de recuperação** (recuperados do risco − entraram em risco; positivo = bom — §15.4).
- **Cards:** cobertura do último mês · % em risco · saldo médio por mês · **em risco no fim**
  (clientes e R$/mês).
- **Gráficos:** cobertura mês a mês e saldo de recuperação mês a mês (verde positivo / vermelho
  negativo), com uma linha marcando **08/2026**, o início do uso registrado da plataforma.
- **Tabela "antes × depois"** por time (ou por vendedor no drill): cobertura, % em risco e saldo
  médio, comparando a **média dos 3 meses antes de 08/2026** (mai–jul) com a **média de 08/2026 em
  diante**. É média do período — nunca o primeiro dia contra o último.
- **Filtro de Universo:** campo · lojas · telemarketing.
- **Clique no vendedor** (no drill do time): cards e os dois gráficos passam a mostrar **só aquele
  vendedor**, e o caminho fica Empresa ▸ Time ▸ Vendedor. Clicar de novo (ou no nome do time) volta
  para o time.

### 17.2 De onde vêm os números
- **Tudo é recalculado da venda** — nada é número gravado. Mudar uma régua refaz o passado inteiro,
  sem degrau no gráfico.
- O que o sistema **sobrescreve** é fotografado todo dia às 23h50: o **dono de cada cliente**
  (`carteira_foto`, desde 09/2026), o **time de cada vendedor** (`vendedor_foto`) e o **uso da
  plataforma por pessoa** (`uso_mensal`). Meses sem foto usam o dono e o time de **hoje** — a tela
  diz quais.

### 17.3 Como ler (cuidados)
- **Sazonalidade:** a cobertura cai de mai–jul para ago–set **todo ano** (medido: 2025 −2,0 pontos;
  2026 −1,4 ponto). Antes × depois sem olhar o mesmo período do ano anterior confunde estação com
  efeito.
- **Comparação "usa × não usa" a plataforma: fora da tela por enquanto** (06/10/2026). Um usuário com
  acesso a quase todas as áreas marcava todos os times como "usa". O uso continua sendo gravado e a
  comparação volta quando a classificação for corrigida.
- Times com base pequena (Diretoria, códigos de canal) oscilam dezenas de pontos com 1 cliente.

---

## 18. Componentes comuns

### 18.1 Plano de ação por cliente (CRM leve)

**Por que existe** (João Victor, 29/09/2026): a lista do dia diz **quem** contatar, mas não guardava
**o que foi feito**. Para o gestor, é saber quais clientes receberam tratativa; para o vendedor, o
histórico das ações.

- **Onde aparece:** coluna **Plano** do Próximo Pedido (§6.2), das listas da Recuperação (§15.8) e na
  ficha do cliente (§18.2). O selo mostra o último registro (ex.: "↪ Transferir · 05/10/2026 (2)").
- **6 status de um toque:** Ligação feita · Sem contato · Retorno agendado · Pedido prometido · Não
  compra mais · Transferir. **Descrição opcional** (até 500 caracteres).
- **Datas:** só o **Retorno agendado** aceita (e exige) data futura; os outros aceitam até 60 dias
  para trás (esqueceu de anotar). **Retorno de hoje ou atrasado aparece em destaque** no topo do
  Próximo Pedido.
- **Zera quando o cliente compra:** registro feito até o dia da última compra vira "acompanhamento
  anterior" (nada é apagado — no cliente recuperado é o histórico do que levou à volta). Um registro
  posterior a um retorno agendado resolve o agendamento.
- **Quem registra:** quem tem acesso à lista (mesma porta de escopo das telas); o autor é gravado.
- **Excluir (✕)** (06/10/2026): para corrigir registro preenchido errado.
  - o **autor** exclui o próprio registro em até **24 horas**;
  - o **admin** exclui qualquer um, a qualquer tempo;
  - a exclusão é **lógica**: some da tela e do selo, mas o rastro fica gravado (quem excluiu e
    quando, mais registro no log).
- **Horário "registrado em"** sai no horário de **Brasília** (corrigido em 06/10/2026 — antes saía
  3 h adiantado porque o banco roda em UTC).

### 18.2 Ficha 360° do cliente (drill-cliente)
Abre de várias telas (Carteira, Dashboard, Recuperação…): **Indicadores** (última compra, dias sem
comprar, ciclo pessoal, segmento), **histórico mensal (12m)**, **top departamentos (12m)** e o
**plano de ação** do cliente. (Os top produtos a oferecer ficam no clique da linha do Próximo
Pedido, §6.2.)

---

## 19. Agente de IA do Comercial (módulo opcional)

> Na **Multpel** o Agente está **desligado** (o botão nem aparece). Ele existe na **demo** e é venda
> adicional. O Agente da Gestão de Estoque tem manual próprio (`MANUAL_COMPRAS.md`).

- **Três estados por instância** (o que muda é env, não código):
  - `off` — sem `ia` no `MODULOS`: nada aparece, nenhuma requisição é feita;
  - `oferta` — `ia` no `MODULOS` sem chave da OpenAI: cadeado + o que o Agente faria;
  - `ativo` — `ia` + chave: o chat.
- **Público principal: gestão** (diretor/viewer e supervisor); o vendedor também usa, sempre no
  próprio escopo.
- **Lê as MESMAS rotas das telas**, com o mesmo RBAC e o mesmo cache — zero conta nova. Panorama de
  gestão + consultas sob demanda por **vendedor**, **time** e **cliente** (até 3 por pergunta).
  Supervisor perguntando de outro time recebe "fora do escopo".
- **Glossário obrigatório no prompt:** as quatro positivações, as três receitas em risco, as duas
  curvas ABC, margem das Metas pelo bruto, devolução por DTENT, réguas de venda × cadastro, régua
  única de cobertura. Sempre cita o **código**.
- **Modelo:** `gpt-4.1-mini` (não calcula — só narra números prontos). Teto diário por pessoa,
  timeout que protege o app, registro de uso no log.
- Usuário sem a área Comercial recebe 403 no chat.

---

## 20. Página: Admin (`/admin`)

Só para papel **admin**. A Administração é **neutra** — um admin que só tem a área de Estoque
consegue administrar o sistema.

### 20.1 Cobertura de carteira (Gerencial / Performance)
- **Limiar de baixa performance (%)** — padrão **85**.
- **Janela "em dia" (dias)** — 30 / 45 / **60** (padrão 60).
- **Cobertura entra na nota a partir de** (competência) — padrão **11/2026** (mês de limpeza, §16.3).

Salvos em `multpel_config`; valem para o Gerencial, o alerta por e-mail e a Performance. ⚠️ **O
valor salvo vence o padrão** — se o Gerencial ainda abre em 30 dias/60%, é porque o valor antigo está
salvo aqui.

### 20.2 Metas de margem (Gestão de Estoque)
Painel recolhido no topo: meta de margem por **comprador × competência** — indicador da Performance
do comprador. Ver `MANUAL_COMPRAS.md`.

### 20.3 Usuários cadastrados (CRUD)

| Campo | Observação |
|---|---|
| **Acesso ao sistema** (áreas) | Checkboxes **Comercial** / **Gestão de Estoque**. Default: só Comercial. |
| Nome · E-mail · Telefone | — |
| **Destinatários adicionais (CC)** | Até **5** e-mails; recebem os mesmos relatórios. |
| Função | admin · supervisor · vendedor · viewer |
| **Vendedor (codusur)** | Para papel vendedor — autocomplete por nome ou código. |
| **Supervisor / Áreas** | Um supervisor pode ter **várias** áreas (`codsupervisores`). |
| **Comprador vinculado** | Filtro **inicial** da Gestão de Estoque — **não trava**. Também recorta os relatórios de estoque por e-mail; mostra (só leitura) a meta de margem vigente. |
| Senha | Vazio = gerada automaticamente. |
| **Cron ativo** | Envia o relatório de carteira por e-mail. |
| **Incluir "Lista do Dia"** | Anexa o Próximo Pedido ao e-mail. |
| **Receber alerta de cobertura** | Opt-in do alerta gerencial. |
| **Usuário ativo** | Desmarcado = não consegue logar. |
| **Horário de envio** / **Frequência** | Diária · Semanal (segunda) · Semanal (sexta). |
| **Segmentos RFM** | Vazio = carteira completa; marcado = só aqueles segmentos no e-mail. |
| **Relatórios de Gestão de Estoque por e-mail** | Catálogo único (`estoque/relatorios.py`). |

**Ações por usuário:** editar · excluir · **desbloquear** · **enviar relatório agora**.

### 20.4 Uso da ferramenta (`/uso`, oculta)
Página **fora do menu**, só admin: quem entra, último acesso, **dias ativos** (dias distintos com
login, no fuso de Brasília) e downloads, nos últimos 30 dias / 90 dias / 12 meses. Mede **adoção**,
não presença (não existe evento de saída, então "tempo de uso" seria fictício). O log é expurgado aos
**12 meses**; o resumo mensal por pessoa é preservado em `uso_mensal`.

### 20.5 Pesos da Performance
Ficam na própria página Performance (§16.4), visíveis só para admin.

---

## 21. RBAC — quem vê o quê

| Papel | codusur | codsupervisor(es) | Enxerga |
|---|---|---|---|
| **admin** | — | — | Tudo, e administra |
| **viewer** | — | — | Tudo (só leitura) — perfil de diretoria |
| **supervisor** | — | 1 ou mais áreas | Só suas áreas (multi-área suportado) |
| **vendedor** | preenchido | — | Só a própria carteira / venda |

**As duas réguas (§3.7) na prática:**
- **Por VENDA** (Dashboard, Vendedores, Curva ABC, Metas): `CODUSUR IN {...}` / `CODSUPERVISOR IN
  {...}` injetado no DAX.
- **Por CADASTRO** (Carteira, Categorias, Mix, Radar, Tendências, Gerencial, Recuperação, Evolução,
  e-mails): recorte em Python por `PCCLIENT.CODUSUR1`, com os **números totais** do cliente.

**Consequências:**
- Admin filtrando uma área == supervisor daquela área, cliente por cliente.
- O filtro é aplicado **no servidor**: trocar a URL não amplia acesso (403 ou o parâmetro é
  ignorado). Filtros de supervisor/vendedor só **estreitam**.
- A **chave de cache inclui o RBAC**, então escopos não vazam entre si.

---

## 22. E-mails automáticos (Comercial)

Agendador interno (APScheduler) verifica **a cada 5 minutos** quem está na janela de
horário/frequência. Só envia de fato se `CRON_HABILITADO=true`. Entrega via **Resend API**.

1. **Relatório de Carteira** ("Cron ativo") — PDF + CSV da carteira do destinatário. Vendedor: 1 PDF
   ordenado por lucro; supervisor: 1 PDF por área + 1 CSV combinado. Respeita segmentos RFM e CCs.
2. **Lista do Dia** ("Incluir Lista do Dia") — clientes a contatar hoje + vencidos até 15 dias + top
   5 produtos a oferecer.
3. **Alerta de Cobertura** ("Receber alerta") — times e vendedores **abaixo do limiar** (padrão 85%,
   régua única), do pior para o melhor, no escopo do destinatário. Colunas: Nome · Cobertura ·
   Positivados · Carteira · Receita perdida acumulada. **Só pessoas** entram (base < 5, fictícios e
   canais ficam fora). **Só envia se houver alguém abaixo do limiar.**

Os relatórios da Gestão de Estoque têm catálogo próprio — ver `MANUAL_COMPRAS.md`.

---

## 23. Rotinas noturnas (fotos)

| Hora | Rotina | O que grava |
|---|---|---|
| 23h50 (todo dia) | Foto da carteira | `carteira_foto` (dono de cada cliente no mês) · `vendedor_foto` (time de cada vendedor no mês) · `uso_mensal` (uso por pessoa nos 12 meses que o expurgo ainda não tocou). A última gravação do mês é a foto dele. |
| 03h20 | Expurgo do log | apaga `multpel_log` com mais de 12 meses |

Não dependem de `CRON_HABILITADO` (não mandam nada para ninguém). Perder a foto de um mês é
irrecuperável: o ERP só guarda o estado de **hoje**.

---

## 24. Operação: atualização, cache e performance

### 24.1 "BI atualizado em …" (linha do topo)
Vem da **API REST do Power BI** (histórico de refresh): último refresh **Completed**, em horário de
Brasília. Refresh em andamento → **"🔄 atualizando"**. Cache de 5 min. "Atualização indisponível" =
permissão do Service Principal, não erro de dados.

### 24.2 Cache (Redis)

| O que | TTL |
|---|---|
| Carteira global, mapas mensais, ranking de vendedores, Recuperação, Performance, Evolução | **1 h** |
| Metadados (vendedores, supervisores, departamentos) | **24 h** |
| Cohort (Tendências) | **24 h** |
| Dashboard agregados / drills | **30 min** |
| Refresh do BI (linha do topo) | 5 min |
| Metas do mês corrente | atado à tag de refresh do dataset META |

Quando uma resposta muda de forma ou de significado num deploy, a **versão da chave** sobe (ex.:
`recuperacao:resumo:v4`), senão a resposta antiga seguiria por até 1 h.

### 24.3 Se a tela mostrar "Erro" ou ficar carregando
- O Power BI pode estar em **refresh** (rejeita queries). Aguarde 5–10 min, "Tentar agora" ou F5.
- **Primeira carga do dia** de Recuperação, Performance e Evolução leva **20–45 s** (até 26 meses de
  compras em blocos). A segunda visita é rápida.
- Persistindo por mais de 30 min, avisar o time técnico.

---

## 25. Multi-fonte e instância DEMO

O app roda de **Power BI OU Postgres** com o **mesmo código**, decidido por variável de ambiente.

| Env | Valores | O que faz |
|---|---|---|
| `DATA_SOURCE` | `powerbi` (default) \| `postgres` | de onde vêm os dados analíticos |
| `MEDIDAS` | `cliente` (default) \| `joga` | medidas do BI do cliente ou reconstrução própria |

- **`powerbi` + `cliente` = produção.** Validada centavo-a-centavo contra o BI real.
- **`postgres`** = sustenta a **demo** (`joga_demo`, sintético). A base **anda sozinha** até hoje
  (rotina diária desloca as datas) e foi calibrada nas proporções reais da Multpel.
- **Rede de segurança:** em modo `postgres` o caminho DAX levanta erro de propósito — um endpoint
  esquecido falha alto em vez de vazar dado real numa demonstração.

**Para o agente:** na demo, nomes e valores são **sintéticos**; fórmulas e telas são as mesmas.

---

## 26. Glossário (inclui as palavras com mais de um sentido)

| Termo | Significa |
|---|---|
| **CODUSUR** | Código do vendedor no Winthor |
| **CODSUPERVISOR** | Código do supervisor (agrupa vendedores num "time") |
| **CODUSUR1** | Vendedor de **cadastro** do cliente (`PCCLIENT`) — base do RBAC por cadastro |
| **CODEPTO** | Departamento de produto |
| **DTSAIDA / DTENT** | Data da venda / data de entrada da devolução no estoque |
| **Base ativa** | Clientes do cadastro com compra em 365 dias (§3.5) |
| **Cobertura** | Régua única: % da base ativa que comprou em 60 dias (§3.5) |
| **Positivação** | Quatro sentidos — cobertura da base, positivação por classe ABC, alcance, "positivados" do cockpit (§3.6) |
| **Alcance** | Tudo que o vendedor atendeu ÷ base ativa (pode passar de 100%) |
| **Fora da base** | Clientes de outras bases atendidos pelo vendedor, em 4 tipos (§7.1) |
| **Em risco** (Recuperação) | > 60 dias sem comprar **e** além do ciclo (≠ segmento RFM "Em Risco") |
| **Perdido** (Recuperação) | > 365 dias sem comprar (≠ segmento RFM "Perdidos", que é R/F/M baixos) |
| **Base morta** (Gerencial) | Clientes da base na faixa 91+ dias |
| **Valor mensal** | Venda dos 365 dias até a última compra ÷ 12 (não acumula) |
| **Receita perdida acumulada** | Valor mensal × meses de atraso além do ciclo (acumula) — Gerencial/Próximo Pedido |
| **"Receita em risco" do Radar** | Venda 12m dos produtos que o cliente parou — 3º sentido; nunca somar com os outros dois |
| **Saldo de recuperação** | Recuperados do risco − entraram em risco; positivo = bom (§15.4) |
| **Dinheiro na mesa** | Valor mensal em risco + potencial de positivação (§15.3) |
| **Curva ABC de clientes** | Pareto 80/95 da venda 12m dos clientes, no início do mês (Carteira) |
| **Curva ABC de produtos** | Pareto 80/95 da venda dos produtos na janela escolhida (aba Curva ABC) |
| **Ticket médio** | Dashboard = venda ÷ nº de pedidos; Vendedores = medida do BI; "por cliente/mês" é outro número — sempre dizer qual |
| **Margem** | Comercial = lucro ÷ venda LÍQUIDA; Metas = lucro ÷ realizado BRUTO |
| **Ciclo pessoal** | Mediana dos intervalos entre compras do cliente (12m), piso de 7 dias |
| **Recência (R)** | Dias desde a última compra |
| **Cohort** | Turma de clientes com 1ª compra no mesmo mês |
| **Mês fechado** | Último mês completo — base da Performance, Vendedores (cobertura), Recuperação e Evolução |
| **Universo** | Campo · Lojas · Telemarketing (pelo TIPOVEND) — não se comparam entre si |
| **Fictício** | Códigos que não são pessoa: 999 transferência, 900 jurídico, 4 caixa, 272 Multpel, 34 prospecção |
| **RBAC** | Controle de acesso por papel |
| **Área / Módulo** | O que a PESSOA acessa / o que a EMPRESA contratou (`MODULOS`) |
| **RCA** | Representante Comercial / o relatório do Totvs que é a referência dos números |

---

## 27. Perguntas frequentes (FAQ do agente)

### Números e alinhamento
- **"O número bate com o RCA?"** Sim. Receita e Lucro contam a devolução por **DTENT** (§3.1),
  validado centavo a centavo.
- **"Por que o % dos cards do Dashboard é diferente do gráfico YoY?"** Os cards comparam o mês com
  o **mesmo período** do ano anterior; o gráfico compara **12 meses** com os 12 anteriores (§4.5).
- **"Por que Clientes Novos caiu de ~2.700 para dezenas?"** Foi corrigido em 26/09/2026: a medida do
  BI contava todo cliente do mês; agora é quem nunca tinha comprado antes (§5).
- **"Metas: por que clientes/mix não somam?"** São contagem distinta (§13.3).
- **"Por que a meta não mostra o time X?"** Nenhum vendedor dele tem meta cadastrada no mês (§13.4).
- **"O número do meu time não bate entre a Curva ABC e a Carteira."** Curva ABC é por **venda**
  (quem faturou); Carteira é por **cadastro** (de quem o cliente é). Em Lojas/Diretoria divergem
  muito (§3.7).

### Cobertura e positivação
- **"Por que a cobertura do vendedor no Gerencial é diferente da Performance?"** É a mesma régua,
  em datas diferentes: Gerencial = janela de 60 dias até **hoje**; Performance/Vendedores = 60 dias
  até o **fim do mês fechado** (§3.5).
- **"A cobertura de todo mundo caiu em setembro."** Mudou a régua (29/09/2026): base de 12 meses,
  janela de 60 dias, positivado por qualquer vendedor, limiar 85% (§3.5). Não foi a operação.
- **"Por que o vendedor tem alcance de 150%?"** Alcance soma os clientes de outras bases que ele
  atendeu; é informativo (§7.1).
- **"Cliente meu que só compra de outro vendedor conta como positivado?"** Sim — positivado é de
  **qualquer** vendedor. A orientação é transferir o cliente para quem o atende.

### Recuperação e Evolução
- **"O saldo positivo é bom ou ruim?"** **Bom**: recuperou mais do que perdeu (desde 06/10/2026, §15.4).
- **"Por que o card diz 428 recuperados e a ponte 410?"** O card inclui os 18 que voltaram da base
  perdida (+365 dias), que não passam pelo estoque em risco (§15.4).
- **"Por que a lista diz 68 em risco e o card 64?"** A lista é de **hoje**; a ponte é o **fim do mês**
  escolhido (§15.8).
- **"Recuperado da base 67, pelo time 62, de outra base 1 — onde estão os outros 6?"** Na coluna
  **Por outros**: clientes da base recuperados por outros times. `67 = (62 − 1) + 6` (§15.6).
- **"O gráfico mostra que perdemos mais do que recuperamos?"** Olhe a **linha**: abaixo do zero =
  perdeu mais do que recuperou naquele mês (§15.5).
- **"A cobertura caiu de jul para set — a ferramenta não funciona?"** Cai todo ano nessa época
  (sazonalidade, §17.3). Compare com o mesmo período do ano anterior.

### Performance
- **"Por que a nota é parcial / sem posição?"** Faltam metas cadastradas do mês fechado; nota parcial
  não ranqueia (§16.3).
- **"Por que a cobertura aparece mas não conta na nota?"** Mês de limpeza: até a competência
  configurada no Admin ela é informativa (§16.3).
- **"Bati 100% da meta e tirei 10? E com 99%?"** 100% = 10; 90–99,99% = 9; abaixo de 85% = 0 (§16.2).

### Plano de ação
- **"Registrei errado — como apago?"** Clique no ✕ do registro (autor até 24 h; admin sempre) (§18.1).
- **"O plano do cliente sumiu."** Ele comprou: o acompanhamento zera e os registros vão para
  "acompanhamentos anteriores" (§18.1).
- **"O horário do registro está 3 horas adiantado."** Era o fuso do banco; corrigido em 06/10/2026.

### Acesso e configuração
- **"Como mudo o limiar do alerta de cobertura?"** Admin → Cobertura de carteira → Limiar (%) (§20.1).
- **"Liberei o usuário e ele não vê a Gestão de Estoque."** Verifique a **área** do usuário (Admin)
  **e** o `MODULOS` da instância (§2.1).
- **"Fixei a Gestão de Estoque e não consigo entrar no Comercial."** O "fixar" vale só para abrir o
  sistema; use o seletor de área ou o menu (§2.1).
- **"Errei a senha e travou."** 5 erros → 15 min, depois 1 h, 4 h; o admin libera pelo botão
  **desbloquear** (§2.4).

### Operação
- **"A tela está lenta / dando erro."** Refresh do BI ou primeira carga do dia (§24.3).
- **"O e-mail não chegou."** Confira `CRON_HABILITADO`, "Cron ativo", horário/frequência e — no
  alerta de cobertura — se havia alguém abaixo do limiar (§22).
- **"Os dados desta tela são reais?"** Em `demo.jogasolucoes.com.br`, **não**: são sintéticos (§25).

---

*Fim do manual do Comercial. Dúvidas de estoque/compras: `docs/MANUAL_COMPRAS.md`.
Quando o comportamento divergir deste texto, o código manda.*
