# Manual do Módulo GESTÃO DE ESTOQUE — JOGA Analytics

**Versão 4.0 · Atualizado em 19/08/2026 · Base de conhecimento do agente de IA de dúvidas.**

> ⚠️ **O módulo mudou de nome em 19/08/2026: "Compras" → "Gestão de Estoque".** Mudou só o
> RÓTULO. A chave interna continua `compras` — está na env `MODULOS`, na coluna
> `multpel_users.areas` (por pessoa, já gravada em produção) e na URL `/estoque`. Se alguém
> perguntar "onde fica o módulo Compras", é este. Gate: `tests/test_nome_gestao_estoque.py`.
>
> **Escopo:** este manual cobre o módulo — as **22 abas**, a tela de campo da pesquisa de
> preço, todos os cálculos, o
> pedido de compra, a tributação, os relatórios e as armadilhas de dados. O **módulo Comercial**
> (dashboard, carteira RFM, vendedores, metas, cobertura de carteira) tem manual próprio:
> **`docs/MANUAL_COMERCIAL.md`**.
>
> **Este documento substitui**, para fins do agente, os antigos `docs/estoque/MANUAL.md`,
> `MANUAL_TECNICO.md` e `MANUAL_AGENTE.md`. Eles descrevem o mundo pré-fusão e não conhecem:
> tributação de IPI/ST, pré-entrada, ciclo de compras, verbas, lead time, meta de ruptura por
> curva, estoque ideal parametrizável, pedido em caixa nem o login único.
>
> **Regra de precedência:** quando o comportamento divergir deste manual, **o código manda**.
> Fontes da verdade: `estoque/core.py` (motor de cálculo), `estoque/routes.py` (rotas/dados),
> `estoque/queries.py` (DAX), `static/estoque/estoque.js` (telas),
> `estoque/relatorios.py` (catálogo de e-mail). Detalhe de fórmulas de estoque também em
> `docs/estoque/planilha_v3.md`.

---

## 1. Visão geral

Painel de **gestão de compras × estoque** sobre o Power BI do cliente (Winthor), focado no
**comprador**: o que comprar, o que vai vencer, o que está parado, ruptura, cobertura/giro,
orçamento, fornecedores, verbas e lead time.

- **Endereço:** `/estoque` dentro do JOGA Analytics (produção:
  `painel.jogasolucoes.com.br/estoque`). **Não tem login próprio** — usa a sessão e o RBAC do app
  principal (ver §2).
- **Fontes de dados:** dataset Power BI **"Estoque"** (PCEST, PCPRODUT, PCFORNEC, PCEMPR,
  PCEMBALAGEM, PCESTENDERECO, PCPEDIDO, PCITEM, PCVERBA, PCLANC/PCCONTA, PCNFSAID/PCMOV,
  TRIB_ENTRADA, PEDIDO_ENTRADA) + dataset **"RCA"** (faturamento/venda/devolução) + **Postgres**
  (estado editável: orçamento, pedidos da plataforma, planos de ação).
- **Atualização:** o cabeçalho mostra "BI atualizado …". O app cacheia os dados pesados por
  **30 min**.

---

## 2. Acesso ao módulo

- **Área "compras"**: o usuário precisa dela liberada no Admin. Sem ela → **403**. Se a empresa
  não contratou o módulo (`MODULOS`), a rota **nem existe** → **404**.
- **Comprador vinculado** (`codcomprador` no Admin): é o **filtro inicial** do painel — **não
  trava**, o usuário pode ver os outros compradores. Ele também recorta os **relatórios por
  e-mail** (§13).
- **Preferências por navegador:** unidade, período de venda, filtros, aba e **⚙ Parâmetros**
  ficam no `localStorage` — o painel lembra as escolhas no próximo acesso.
  ⚠️ Isso significa que **os parâmetros são por pessoa/navegador**: enquanto um valor não for
  fechado como padrão do servidor, o painel pode significar coisas diferentes para cada um.

---

## 3. Unidades de negócio e filiais

O seletor **UNIDADE** define quais filiais entram no estoque e na venda. **Estoque** (posição
física) e **venda** (faturamento) vivem em filiais **diferentes** por unidade — por isso os dois
conjuntos.

| Unidade | Filiais de ESTOQUE | Filiais de VENDA |
|---|---|---|
| **Atacado** (padrão) | **3, 5** | 3, 7, 8 |
| A&M | 4 | 4 |
| AC | 14 | 14 |
| JID | 9 | 9 |
| Todas | 3, 5, 4, 14, 9 | 3, 7, 8, 4, 14, 9 |

Nomes: 3 = Matriz · 4 = A&M · 5 = Depósito · 7 = Telemarketing · 8 = Atacado · 9 = JID ·
14 = AC. No Atacado, a 5 é depósito e as 7/8 vendem sem estoque próprio.

O rótulo do cabeçalho (ex.: "3,5,7,8 – Atacado") mostra a **união** de estoque + venda; o estoque
em si é só **3 e 5** no Atacado.

⚠️ **Ao conferir com relatórios do ERP, iguale a filial.** Vários relatórios do Winthor (ex.: o
111) saem com **todas** as filiais, enquanto a tela usa a unidade selecionada.

---

## 4. Barra de filtros (vale para o painel inteiro)

| Filtro | O que faz |
|---|---|
| **Curva** | Filtra pela classe **ABC de venda** do produto. Nas abas **Fornecedores** e **Compras × Vendas "por fornecedor"**, filtra pela ABC **do fornecedor**. |
| **XYZ** | Previsibilidade da demanda (X/Y/Z). |
| **Fornecedor** | Por código ou razão social. |
| **Depto** | Por departamento. |
| **Comprador** | Recorta tudo pelo comprador (inicializa com o comprador vinculado do usuário). |
| **Buscar produto** | Por código ou descrição. |
| **Venda** | Período do faturamento usado em venda/lucro/margem: **Mês atual · Últimos 90d · 6 meses · 12 meses**. |
| **⚙ Parâmetros** | Lead time, estoque de segurança, cobertura-alvo, horizonte de validade, metas de ruptura A/B/C, estoque ideal, janela de produto novo, base de giro e arredondamento por caixa. |
| **✕ Limpar** | Zera os filtros. |

**Duas coisas NÃO seguem o filtro de período, de propósito:**
- a **curva ABC** *segue* (é o ABC do que vende no período escolhido);
- a **venda perdida** *não segue* (usa janelas fixas — §5.11);
- o **ciclo de compras** *não segue* (é sempre 12m — §5.20);
- o **crescimento AA da aba Fornecedores** *não responde aos filtros de recorte* (§5.21);
- a **Meta de ruptura** *não responde a nenhum filtro do topo* (§7.3).

---

## 5. Glossário de cálculos — as "contas" do sistema

Esta é a seção de referência. Valores-padrão dos parâmetros entre parênteses.

### 5.1 Estoque disponível (QTDISP)
> **Disponível = QTESTGER − avaria (QTBLOQUEADA) − reserva (QTRESERV)**, somado nas filiais de
> estoque da unidade.

- É o **gerencial líquido**: item em avaria ou reservado **não** está disponível para venda
  (decisão do diretor, 07/2026). Ex.: item 44094 = 86 − 81 − 5 = **0**.
- Usado em **tudo**, exceto na **Validade/FEFO**, que usa o estoque **endereçado**
  (`PCESTENDERECO`, RUA ≠ 99) por lote.
- **Valor de estoque** = `max(0, disponível) × CUSTOFIN` — estoque negativo é erro de saldo, não
  vale R$ negativo (mas a quantidade negativa continua visível na tela).

### 5.2 Giro (demanda) — e os 5 caminhos possíveis
> **Giro mensal (oficial) = média dos 3 últimos meses fechados = (QTVENDMES1+QTVENDMES2+QTVENDMES3) ÷ 3**
> **Giro diário = giro mensal ÷ 30**

O app tenta, **nesta ordem**:

| # | Fonte (`giro_fonte`) | Quando entra |
|---|---|---|
| 1 | `media3` | padrão — oficial do TI |
| 2 | `novo_item` | média-3m deu 0, mas houve venda real (RCA) nos últimos 6 meses fechados. Média só dos meses **desde o lançamento** — não dilui pelos meses em que o item não existia. |
| 3 | `mes_corrente` | ainda 0: item que só começou a girar no **mês em andamento** → usa a venda crua do mês (decisão do sócio 07/2026). Não anualiza: o número sobe conforme o mês avança. |
| 4 | `forecast` | opção do ⚙ Parâmetros: média móvel simples da venda real do RCA (janela configurável, padrão 6 meses). |
| 5 | `sazonal` | opção do ⚙: forecast + fator sazonal ano-a-ano (24m de histórico, fator clampado entre 0,3 e 3,0). |

O **360° do produto informa qual fonte está sendo usada.**

### 5.3 Cobertura (dias)
> **Cobertura = ARREDONDA.CIMA(disponível ÷ giro diário)**
> Giro ≤ 0 → **não calculável** (vale 9999 e cai na faixa 121+). Disponível ≤ 0 com giro → 0.

Faixas fixas (métrica oficial da planilha):
**0-30 (risco ruptura) · 31-60 (OK) · 61-90 (atenção) · 91-120 (urgente) · 121+ (crítico)**.

### 5.4 Estoque-alvo, ROP e estoque de segurança
```
estoque de segurança = giro_diário × dias_seguranca            (25 dias)
ROP                  = giro_diário × lead + estoque_segurança
estoque-alvo         = giro_diário × (lead + cobertura_alvo)   (10 + 45 = 55 dias de giro)
```
- **lead** = o parâmetro **"Lead time (dias)"** da tela (padrão **10**), válido para **todos** os
  fornecedores. Decisão 07/2026: antes priorizava o `PRAZOENTREGA` cadastrado, e mexer no slider
  não afetava ~95% dos itens — parecia "não funcionar". O comprador controla manual.
  *(A previsão de **entrega** do Orçamento continua usando o prazo real do fornecedor — é outra
  finalidade.)*
- O lead entra no alvo porque **o estoque continua caindo até a mercadoria chegar**. Sem isso a
  compra assume "entrega hoje" e sub-dimensiona.
- O **estoque de segurança** e o **ROP** entram na **classificação de status**, não na quantidade
  sugerida.

### 5.5 Já pedido (pedido de compra real em aberto)
> **Já pedido = Σ max(0, QTPEDIDA − QTENTREGUE)** dos pedidos ativos do Winthor
> (`PCPEDIDO × PCITEM`), últimos **180 dias**.

É o que **já foi comprado e ainda não chegou**. Como o gerencial já reflete o recebido, só o
**aberto** entra na projeção — senão contaria em dobro.

### 5.6 Pré-entrada (mercadoria que chegou e aguarda liberação)
> **Em transição = QTBLOQUEADA, quando a última entrada foi há ≤ 7 dias.**

**Por que existe:** na pré-entrada o Winthor **baixa o `QTENTREGUE`** (o item sai de "já pedido")
e lança a quantidade em `QTESTGER` **e** em `QTBLOQUEADA` — o disponível fica 0. Sem tratar isso,
a mercadoria **some das duas contas** e o app sugere **comprar de novo o que já está no
armazém**. Medido em 07/2026: **130 linhas, R$ 198.683** (22 viravam ruptura falsa, 108 inflavam
a sugestão em silêncio).

- Entra no **estoque projetado**, **nunca** no disponível: a mercadoria ainda não é vendável,
  então continua contando como ruptura e fora do valor de estoque — mas não se compra de novo.
- Status próprio: **`aguardando_liberacao`**, que ganha da rotulagem de ruptura.
- ⚠️ **É heurística.** O Winthor usa o **mesmo** `QTBLOQUEADA` para avaria e pré-entrada, e o
  `MOTIVOBLOQESTOQUE` vem vazio. Discrimina-se pela **data**: bloqueio recente = transição;
  bloqueio velho = avaria de verdade (e essa deve mesmo continuar sugerindo compra). Validado
  contra 2ª fonte (`PEDIDO_ENTRADA`): **21/21** nos ≤3 dias, **2/122** no bloqueio >30 dias.

### 5.7 Estoque projetado
> **Projetado = disponível + já pedido + em transição (pré-entrada)**
> **Cobertura projetada = projetado ÷ giro diário**

### 5.8 Sugestão de compra
> **Sugestão = max(0, estoque-alvo − estoque projetado)**

- Sai **em caixas fechadas** (arredonda pra cima pelo fator `QTUNIT` do PCEMBALAGEM; fallback
  `QTUNITCX` do cadastro). Item **sem fator de caixa** sai em **unidades ("un")** — não força
  "1 cx"; normaliza sozinho quando o TI cadastrar o fator. Pendências em
  `estoque/itens_sem_fator_caixa.csv`.
- Desconta o já-pedido → o "quanto comprar" fica **menor** que o buraco até o alvo. É melhoria,
  não divergência.
- **Compra suspensa:** item com giro no histórico mas **sem vender há ≥ 60 dias** → não sugere
  comprar (estoque morto; o giro está "preso" no histórico defasado). A tela mostra quanto ele
  *sugeriria*, só para conferência.

### 5.9 As DUAS réguas de valor: mercadoria × NF
Esta é a distinção que mais gera dúvida.

```
valor_sugerido_liq = caixas sugeridas × custo (CUSTOFIN, arredondado a 4 casas)   → MERCADORIA
valor_sugerido_nf  = valor_sugerido_liq × (1 + IPI% + ST%)                        → NOTA FISCAL
```

- **`valor_sugerido_nf` é a régua da NF** — a mesma que o **Orçamento** mede
  (`PCPEDIDO[VLTOTAL]` é a NF cheia: mercadoria + IPI + ST).
- **Todo lugar que mostra "quanto vou gastar" fala a régua da NF:** card do fornecedor no
  Abastecimento, Cockpit ("A comprar"), Estoque zerado ("Custo de reposição"), Ruptura por
  comprador, drawer 360°, aba Fornecedores e o relatório de Reposição.
- **A única exceção proposital** é a coluna **"Valor sug."** da aba Abastecimento, que segue em
  **mercadoria** — é o preço que vai na **planilha de importação do Winthor**.
- ⚠️ **O valor usa o custo arredondado a 4 casas** (o mesmo que vira preço no documento). Somar
  o `custofin` cru fazia a tela divergir do PDF em centavos (R$ 39.536,38 × R$ 39.536,28 num
  pedido de 49 itens) — e quem bate com o ERP é o **PDF**.

### 5.10 Curva ABC (de VENDA)
> Pareto do **faturamento do período selecionado**. **A** = itens que somam até **80%** da venda ·
> **B** = de 80% a **95%** · **C** = os 5% restantes.

- Classifica **o que mais vende** (leitura clássica) e **muda com o período de VENDA** escolhido.
- Existe também **ABC do fornecedor** (Pareto da venda por fornecedor), usado nas abas
  Fornecedores e Compras × Vendas "por fornecedor" — lá o filtro Curva age por ele.
- Há ainda uma segunda lente, `curva_giro` (Pareto por giro em unidades), e no cockpit um
  **toggle "Vendas | Estoque"** que mostra a concentração de **capital em estoque**.

### 5.11 Venda perdida (na ruptura)
> **Venda perdida = dias em ruptura × giro diário × preço de venda**

- **dias em ruptura** = dias desde a última venda, com **teto de 60 dias** (30 se não houver data).
- **preço de venda** = **realizado médio dos últimos 3 meses** — **janela FIXA**, não muda com o
  filtro de período (o preço de tabela `PCPRODUT[PVENDA]` está vazio nesta base). **Fallback no
  custo** se o item não teve venda em 3m.
- Só para item **em ruptura** (disponível ≤ 0 **e** giro > 0); senão é 0.
- Aparece nas abas **Estoque zerado** e **Ruptura por comprador**.

### 5.12 Custo de reposição / sugestão de compra em R$
> **= Σ `valor_sugerido_nf`** dos itens a comprar (sugestão > 0, giro > 0, não suspensos).

⚠️ **Venda perdida e custo de reposição NÃO batem, e não deviam:** um é o que se deixou de
vender no período parado, **a preço de venda**; o outro é o que falta comprar até o alvo, **a
custo (com imposto)** e já **descontando o já-pedido**.

### 5.13 XYZ (previsibilidade da demanda)
> **Coeficiente de variação (CV)** da série de venda dos 3 meses.
> **X** = CV < 0,5 (estável) · **Y** = 0,5 a 1,0 (variável) · **Z** = ≥ 1,0 (errático).

### 5.14 Ruptura, Parado e Dias sem venda
- **Ruptura (critério oficial)** = **disponível ≤ 0 E giro > 0** (vende, mas acabou).
- **Dias sem venda** = dias desde a última saída (`DTULTSAIDA`).
- **Dias sem entrada** = dias desde a última entrada (`DTULTENT`). Serve para separar **estoque
  velho parado** de **compra recente errada** — pedido do diretor 07/2026. *Exemplo real:* cód.
  69174, entrada há 243d, saída há 15d, cobertura 753d → **estoque velho**, não compra ruim.
- **Parado** = com estoque (> 0) e **≥ 15 dias** parado. Faixas: **Novos · 15-30 · 31-60 · 61-90 ·
  91-120 · 121+**. As faixas **somam o total** (partição sem buraco/sobreposição).
- **Quem NUNCA vendeu conta os dias a partir da ENTRADA, não do infinito** (correção 08/2026).
  Antes, `DTULTSAIDA` vazia virava "parado há infinito" e caía direto em 121+ mesmo que a
  mercadoria tivesse chegado ontem — reclamação do diretor ("os produtos novos estão caindo como
  itens parados, sem venda"). Hoje:
  - nunca vendeu **+ entrada há menos de `novo_dias`** (⚙ Parâmetros, padrão **15**) → faixa
    **`novo`**, e ele **não conta como capital parado**;
  - nunca vendeu **+ entrada mais antiga** → a faixa **verdadeira** da chegada;
  - nunca vendeu **e sem data de entrada** → segue em **121+** (lado conservador: na dúvida ele
    aparece, não some).
- ⚠️ **Entrada recente NÃO basta para ser "novo" — tem de nunca ter vendido.** Item que já vendeu
  e foi **reposto** continua na faixa dos dias sem venda. *Exemplo real:* cód. 57071, última venda
  há **1.249 dias**, chegado há 9 — é exatamente a compra que precisa aparecer no 121+, e a regra
  "chegou há menos de 15 dias" a esconderia num card chamado "Produtos novos".
- Status de parado do Cockpit (bandas fixas, sobre a **mesma** régua acima): `novo` · atenção ≥60d
  · crítico ≥90d · muito crítico ≥120d.
  ⚠️ **`status_parado` NÃO é booleano** desde que ganhou o `novo`. Para perguntar "isto é capital
  parado?" use **`core.eh_parado(p)`** (`ehParado` no JS) — testar a verdade do campo volta a somar
  produto recém-chegado como dead stock.

### 5.15 Status de abastecimento e status executivo
**Status de abastecimento** (sobre o estoque **projetado**):
```
sem_giro : giro ≤ 0 e disponível > 0
urgente  : projetado ≤ giro_dia × lead
alta     : projetado ≤ giro_dia × (lead + seguranca)
atencao  : projetado < estoque-alvo
excesso  : cobertura projetada > 120 dias
ok       : demais
```
**Status executivo** (o que a tela mostra ao comprador):
`aguardando_liberacao` (pré-entrada) · `ruptura_sem_pedido` · `ruptura_pedido_parcial` ·
`ruptura_pedido_cobre` · `compra_urgente` · `compra_alta` · `compra_complementar` ·
`programar_compra` · `pedido_cobre` · `estoque_ok`.
**Ação recomendada:** `comprar_imediato` · `negociar_pedido` · `programar_compra` ·
`acompanhar_entrega` · `sem_compra`.

### 5.16 Cubagem (m³) e peso (kg)
- **Cubagem da caixa** = `PCEMBALAGEM[VOLUME]`; se vazio, deriva de `PCPRODUT[VOLUME]` × fator de
  caixa.
- **Cubagem do pedido** = Σ (caixas sugeridas × volume da caixa).
- **Peso da caixa** = `PCEMBALAGEM[PESOBRUTO]`; **peso do pedido** = Σ (caixas × peso da caixa).

### 5.17 Orçamento de compras
```
Meta        = 65% da venda líquida de 30 dias do comprador (RCA)   [override manual possível]
Comprado    = Σ PCPEDIDO[VLTOTAL] dos pedidos emitidos no mês       (NF cheia)
Aberto      = Σ max(0, VLTOTAL − VLENTREGUE) dos pedidos do mês
Saldo       = Meta − Comprado
% consumido = Comprado ÷ Meta
```
- **Recebido** = o que falta entregar é desprezível (≤ 0,5% do total ou ≤ R$ 1,00) — tolera
  resíduo de centavos.
- ⚠️ **Transferência entre filiais NÃO é compra.** Pedido cujo fornecedor tem a **mesma raiz de
  CNPJ (8 dígitos)** da empresa fica **fora** do orçamento. Os excluídos voltam num contador
  ("transferências") — sem isso o valor do card cai sem explicação.
- ⚠️ **Pedidos criados na plataforma NÃO somam no realizado.** Só contam quando forem lançados no
  Winthor e voltarem pela base oficial (evita contagem dupla).
- **Previsão de entrega (híbrida):** usa a `DTPREVENT` do Winthor **quando ela é previsão real**
  (posterior à emissão); senão = data do pedido + `PRAZOENTREGA` do fornecedor (ou 10 dias).
  Isso evita marcar como atrasado um pedido em que o Winthor só repetiu a data de emissão.
- **Status de prazo:** `recebido` · `atrasado` (previsão no passado) · `chega_7` (≤ 7 dias) ·
  `no_prazo` · `sem_prev`.

### 5.18 Estoque ideal (régua do Painel gerencial)
```
Em risco = giro > 0 e cobertura <  ideal_dias        (padrão 45)
Ideal    = giro > 0 e cobertura ≥  ideal_dias        (fronteira INCLUSIVA)
Sem giro = giro ≤ 0  → reportado à parte, NÃO entra no %
% ideal  = ideais ÷ (ideais + em risco)              → alerta se < ideal_meta_pct (90%)
```
- **Fronteira inclusiva de propósito:** o item que pousa exatamente no limiar já atingiu a meta.
  Contá-lo como "em risco" punia justamente quem comprou certo (28 SKUs pousavam exatamente em
  45d, ~2× os dias vizinhos).
- ⚠️ **`ideal_dias` é INDEPENDENTE da "Cobertura alvo"** — de propósito. Um é o **alvo de
  COMPRA** (até onde comprar); o outro é a **RÉGUA DE MEDIÇÃO** do Painel gerencial (a partir de
  quanto o SKU conta como coberto). **Mexer num não mexe no outro.**
- Limiar 0 é clampado para o default 45 — zero faria tudo virar "ideal" e o painel diria 100%
  sempre (mentira silenciosa).
- **O card "Em risco" é clicável** (08/2026): leva à aba **Análise → Produtos** com o filtro
  `Cobertura ≤ limiar−1` já aplicado, de onde saem o Excel e o PDF da lista.
- ⚠️ **A cobertura do painel é a `cobertura_dias` do produto, não um recálculo.** Recalcular
  `ceil(disponível ÷ giro)` a partir dos campos já arredondados dava outro dia: com limiar 25 o
  card dizia **789 SKUs** e a lista tinha **791** (cód. 44398 — 104 un ÷ giro cru 4,3333… = 24d,
  mas ÷ 4,333 arredondado = 25d). No limiar 45 os dois coincidiam por sorte.

### 5.19 Meta de ruptura — uma meta por curva
```
% sem pedido (por curva) = itens zerados AINDA SEM pedido de compra em aberto
                           ÷ TOTAL de produtos do comprador naquela curva
Metas padrão: curva A = 2% · curva B = 5% · curva C = 10%   (editáveis em ⚙ Parâmetros)
Fora da meta = QUALQUER uma das três curvas estourar o seu limite
```
- Base **fixa de 90 dias** para apurar a curva; **o placar não responde aos filtros do topo** —
  meta que muda de valor conforme o filtro não é meta.
- A célula mostra o **% e o absoluto** (`3/150`): 2% sobre base pequena vira 1 item.
- ⚠️ **Separar A/B/C afrouxa o placar sem ninguém mexer na operação** (era A 2% × B+C 5%): os
  itens C que estouravam o teto do bloco passaram a ter orçamento próprio. Compare antes×depois
  uma vez, senão parece ganho operacional.

### 5.20 Ciclo de compras e nº de compras (aba Fornecedores)
```
Compras   = nº de PEDIDOS distintos do fornecedor dentro do período do seletor "Venda"
Ciclo 12m = média dos intervalos entre DATAS distintas de compra = (última − primeira) ÷ (n−1)
            sempre nos ÚLTIMOS 12 MESES
```
- **Janelas diferentes de propósito.** `Compras` segue o filtro (é "quantas vezes comprei no
  período"); o **Ciclo é sempre 12m** porque é **comportamento** do fornecedor — em janela curta
  quase todo fornecedor teria 1 pedido só e o ciclo deixaria de ser comparável.
- ⚠️ **O ciclo conta DATAS distintas, não pedidos.** O mesmo fornecedor costuma receber vários
  `NUMPED` no mesmo dia (um por filial/condição) e contá-los criaria intervalos de 0 dia.
- **Menos de 2 compras em 12m → ciclo `—`**, nunca 0 (zero mentiria "compra todo dia").
- **Leitura de negócio:** cruze **Ciclo × Lead time**. Ciclo menor que o lead = pedido novo antes
  de o anterior chegar.
- Transferência entre filiais (mesma raiz de CNPJ) fica fora.

### 5.21 Crescimento ano-a-ano do fornecedor (YoY)
> Venda líquida do fornecedor nas **duas janelas**, somada sobre **TODOS os produtos vendidos** —
> não só os que ainda estão em estoque hoje.

⚠️ **Não somar `venda_ano_ant` dos produtos da tela.** Bug achado pelo diretor em 07/2026: a tela
só tem o que está no **snapshot de estoque ATUAL**, então o numerador saía completo (o que vende
hoje está no catálogo hoje) e o denominador perdia **todo item que saiu de linha nos últimos 12
meses** — universos diferentes nos dois lados da divisão. Medido no BI real: **18 fornecedores
erravam mais de 10 p.p. e 6 trocavam de SINAL** (o app dizia +21,9% num fornecedor que caíra
72,8%).

**Consequência aceita:** a coluna passa a ser do **fornecedor inteiro** e **não responde aos
filtros de recorte** — a tela avisa quando há filtro ativo. Produto sem cadastro de revenda fica
fora dos dois lados (0,49% da venda do ano anterior) — é o teto de precisão do método.

**Validado contra a rotina 111 do ERP** ("Resumo de Faturamento por Fornecedor"): HIPERROLL
17,63% × 17,63% (exato), Ind. Papéis −3,38% × −3,35%, GALVANOTEK 7,44% × 7,26%, BOMBRIL
3,31% × 2,35% (a sobra é ST/IPI — o 111 desconsidera ST).

**Enquanto o dado não chega, a coluna sai `—` com aviso, nunca o cálculo antigo.** São 3 estados
explícitos: carregando → `—` + aviso; falha → `—` + aviso vermelho; pronto → valor. *Número errado
que parece plausível é pior que célula vazia* — ainda mais um que chega a inverter o sinal.

### 5.22 Verba e lucro com verba
```
Verba          = PCVERBA.VALOR NEGOCIADO por data de emissão, na MESMA janela do lucro
Lucro bruto    = venda líquida − custo   (já era calculado; a coluna só o exibe)
Lucro c/ verba = lucro bruto + verba negociada no período
Margem c/ verba= (lucro bruto + verba) ÷ venda líquida
```
- **NEGOCIADA, não aplicada:** aplicado é evento de caixa/acerto, pode cair meses depois e
  descolaria da competência do lucro.
- ⚠️ **A janela da verba é a do seletor "Venda" do topo**, igual à do lucro. Somar lucro de 1 mês
  com verba de 12 meses é o erro fácil e caro aqui.
- Hoje entra **toda** verba, inclusive `200013 Premiações e campanhas` (decisão do diretor:
  refinar depois) — mas essa parcela viaja separada e **a tela avisa o valor**.
- ⚠️ **`Lucro bruto` NÃO se recalcula como `venda × margem`.** A margem é `lucro ÷ venda`
  arredondada a 1 casa; o caminho de volta reintroduz erro e faria a aba divergir do Comercial,
  que bate centavo-a-centavo com o RCA.

### 5.23 Lead time por fornecedor
```
lead do pedido = data da 1ª entrada (PEDIDO_ENTRADA) − data de emissão (PCPEDIDO)
Lead todos = MÉDIA de TODOS os pedidos recebidos em 12m (inclui os digitados na hora)
Lead real  = MEDIANA só dos pedidos com lead ≥ 2 dias, com no mínimo 5 pedidos
             (menos que isso → "sem lead confiável", e o PRAZOENTREGA manual segue valendo)
Δ          = Prazo manual (PRAZOENTREGA) − Lead real
```
- **"0–1 dia" = pedido digitado na hora da entrega** — o pedido real nasceu fora do ERP
  (telefone/WhatsApp) e foi lançado junto com a NF. Decisão do diretor: **não esconder**; o
  **% na hora** vira o medidor do processo melhorando ao longo do tempo.
- **Média para "todos", mediana para "real"**, de propósito: com >50% de pedidos "na hora" a
  mediana colapsa para 0 e vira número binário; já a mediana do lead ≥2d é o número de
  planejamento, imune a caudas de 100+ dias.
- **Δ positivo** = cadastro inflado (capital parado a mais); **Δ negativo** = prazo otimista
  (risco de ruptura).
- Faixas do histograma: 0-1 · 2-3 · 4-7 · 8-15 · 16-30 · 31+.

---

## 6. Tributação do pedido (IPI/ST) — por que existe e onde NÃO mexer

**O problema (07/2026, achado pelo diretor):** o comprador olhava a sugestão (R$ 39.536,38 no
card da GALVANOTEK), gerava o pedido, e o Winthor registrava **R$ 44.982,01**. Não era erro de
conta — o JOGA mostrava **mercadoria** e o ERP mostra a **NF**. Como o Orçamento lê `VLTOTAL`
(NF cheia), ele planejava numa régua e consumia a meta em outra.

**A fonte é a tributação de ENTRADA do próprio ERP** (rotina 212), publicada no dataset como
**`TRIB_ENTRADA`** (join `PCTRIBENTPROD × PCTRIBFIGURA`, filiais 3/5 + revenda, ~36 mil linhas).
Chave: **produto × filial × UF de origem × tipo de fornecedor** → figura → `PERIPI` / `PERCST`.

### 6.1 A cascata

| # | Degrau | Cobertura | Acerto |
|---|---|---|---|
| 1 | `isento_cadastro` — `PCPRODUT[PERCIPI]` = 0 | 41% | 97% |
| 2 | **`trib_entrada`** — a figura fiscal do ERP | **54%** | **100%** |
| 3 | `cadastro` — item sem figura para aquela UF | 5% | 20% ⚠️ |
| 4 | `pedido_real` / `perfil_fornecedor` — histórico | resto | — |
| 5 | `sem_dado` → **zero** (nunca inventa imposto) | — | — |

**Total: 94,6% das linhas / 96,9% do valor** (medido nos pedidos reais pós-virada; era **53%**
com o histórico como primária).

Os degraus 3–5 saem com **`trib_firme = False`** → a tela marca com **`≈`** e o **% fica editável
no pedido**. É ali que mora todo o erro residual. As telas informam **quanto do valor** está
apoiado em alíquota estimada — em R$, não em contagem de itens (5% dos itens pode ser 30% do
dinheiro).

### 6.2 Regras e proibições
- ⚠️ **`PCPRODUT[PERCIPI]` é o IPI de VENDA** (rotina 271), não o de compra — por isso diverge
  (dizia 6,75% num item que o ERP cobrou 10%). Serve só para os dois papéis em que é bom: dizer
  quem é **isento** (alíquota 0) e cobrir item sem figura.
- ⚠️ **NÃO volte a usar o histórico de `PCITEM[PERIPI]` como fonte primária.** Ele parecia bom em
  janela de 30d (82%), mas isso mede o passado: quando o **redutor de 35% do IPI caiu em
  21/07/2026** (`9,75 = 15 × 0,65`), o histórico seguiu prevendo a alíquota velha por semanas e o
  acerto para o próximo pedido caiu a **53%**. **Cadastro fiscal muda ANTES do histórico** — é
  por isso que a figura ganha.
- Fontes **reprovadas**: `PCEST[PERCIPIULTENT]` (66%), pedido anterior (55%), `PCTRIBUTNCM` /
  `PCIMPORTTRIBUT` / `PCTABTRIBENT` / `PCEXCECAOIPI` (**vazias**), `PCTRIBUTCOMPRA` (não existe
  nesta base), `PCFIGURATRIBIPI` (só CST), `PCTRIBUT` / `PCNCM` (só ICMS).
- **ST sai como fator efetivo sobre a mercadoria** (`VLST ÷ preço`), não como `PERCST`: no
  fornecedor 113 o efetivo é **20,71%** contra `PERCST` 20,05% — a diferença é a majoração da
  base (MVA), que o fator já embute. Evita reconstruir MVA / base reduzida / crédito de ICMS.
- `PCITEM[VLIPI]` / `[VLST]` são **UNITÁRIOS** e `PCITEM[PTABELA]` vem **vazio** nesta base — por
  isso o preço se deriva de `VLIPI ÷ (PERIPI/100)`. Validado no relatório 211 do pedido 565684:
  `Σ QTPEDIDA × VLIPI = 5.445,73`, exatamente o IPI impresso.
- No fallback histórico, a alíquota do par é a **MODA** da janela (não a do último pedido) — ela
  oscila entre pedidos da mesma semana. Medido em 1.487 linhas: moda 86,4% × último 85,7%.
  Empate → o maior.
- **Backtest temporal** (mapa até D-60 prevendo 301 pedidos que ele nunca viu): desvio agregado
  **0,02%**, erro mediano por pedido **0,00%**, p90 2,32%, 96% dos pedidos < 5%. A régua antiga
  errava **8,05%**.
- **`TRIB_ENTRADA` é publicada sob demanda.** Instância sem ela degrada para cadastro/histórico —
  tudo marcado como estimativa.

---

## 7. Navegação — 5 grupos, 22 abas (+ 1 tela de campo fora do painel)

**Visão · Comprar · Pedidos · Estoque · Análise**

| Grupo | Abas |
|---|---|
| **Visão** | Cockpit · Painel gerencial · Meta de ruptura · **Evolução do estoque** *(só ADM)* |
| **Comprar** | Abastecimento · Estoque zerado · Plano reposição |
| **Pedidos** | Orçamento *(+ Logística, oculta)* |
| **Estoque** | Cobertura · Parado · Validade · Vencidos · Ruptura · Ocupação |
| **Análise** | Desempenho comercial · Compras × Vendas · Fornecedores · Lead time · Verbas · ABC-XYZ · Produtos · Qualidade da base |

### 7.1 Cockpit (Visão)
Visão executiva do dia.
- **KPIs:** Valor em estoque · Venda (período) · Margem · Em ruptura (qtd) · **A comprar** (qtd +
  valor **na régua da NF**) · Capital parado (valor + % do estoque).
- **Alertas de ação (cards clicáveis):** Em ruptura (estoque ≤ 0) · Cobertura crítica (≤15d) ·
  Comprar (cobertura baixa) · **Vencimento ≤7 dias** (valor de risco só dos lotes ≤7d) ·
  Parado 120+ dias. Cada card leva à tela já filtrada.
- **Curva ABC (vendas):** gráfico + tabela (A/B/C com nº de itens, valor, % dos itens, % da
  venda) e **toggle Vendas | Estoque**.
- **Maiores ofensores:** capital parado e risco de vencimento (top 6 cada).

### 7.2 Painel gerencial (Visão)
Réplica dos blocos-resumo do relatório gerencial do diretor:
- **Cobertura de estoque por faixa** — 0-30 (RISCO RUPTURA) · 31-60 (OK) · 61-90 (ATENÇÃO) ·
  91-120 (URGENTE) · 121+ (CRÍTICO), com nº de produtos, valor e %.
- **Itens a vencer por faixa de validade** — 0-15 (URGENTE) · 16-30 (ALTO) · 31-60 (ATENÇÃO) ·
  61-90 (BAIXO) · 90+ (OK).
- **Ruptura de produtos** — critério oficial `estoque ≤ 0 E giro > 0`, com % sobre o universo.
- **Estoque ideal — cobertura mínima** (§5.18): donut Ideal × Em risco, % dos SKUs que giram,
  "sem giro" à parte, e alerta quando fica abaixo da meta. **Clicar em "Em risco" abre a lista
  dos itens abaixo da cobertura** (aba Análise → Produtos, já filtrada, com Excel e PDF).

### 7.3 Meta de ruptura (Visão)
Placar da meta por comprador, **uma meta por curva** (§5.19).
- **KPIs:** Compradores acima da meta (basta uma curva estourar) · Empresa curva A · curva B ·
  curva C.
- **Tabela:** Comprador · Curva A (meta X%) · Curva B · Curva C · Status, com linha **EMPRESA**.
  Cada célula mostra `%` + `(sem pedido / total)`.
- **Escopo fixo:** unidade atual, curva apurada sobre 90 dias, **sem responder aos filtros do
  topo**. Para investigar item a item, use **Estoque → Ruptura**.

### 7.4 Evolução do estoque (Visão) — **restrita ao ADM**

A **única série histórica** do módulo: valor de estoque, capital parado, cobertura e ruptura ao
longo do tempo. Pedido do diretor (08/2026): *"acompanhar de fato se estamos tendo evolução
positiva ou não… é gestão comprovada"*.

⚠️ **O histórico NÃO pode ser gerado para trás, e isso não é falta de esforço.** O `PCEST` é
**posição**: o `QTESTGER` de ontem foi sobrescrito e não existe no BI nem no Winthor. A aba
Vencidos mostra mês a mês desde sempre porque perda por validade é **evento datado**; saldo é
**estado**. A cobertura é a mais irrecuperável — depende de `QTVENDMES1..3`, janelas móveis
regravadas todo mês.

**Como nasce:** um robô fotografa o estoque todo dia (6h-12h, e só **depois do refresh do BI do
dia** — antes disso gravaria a posição de ontem com a data de hoje). A primeira leitura útil sai
em ~4 semanas; tendência firme em ~90 dias. Enquanto enche, a tela mostra o contador de fotos e
explica — não diz "sem dados".

- **KPIs:** Valor em estoque · Capital parado · Itens em ruptura · Cobertura ideal, cada um com a
  **variação na janela** (é a pergunta da aba; o nível o Cockpit já respondia).
- **Gráficos:** barras de estoque com o parado em linha (eixo próprio à direita, senão a linha
  fica esmagada) · composição da cobertura empilhada · itens em ruptura.
- **Tabela:** a foto dia a dia, mais recente primeiro.
- **Recorte:** unidade · comprador · fornecedor · curva · XYZ. **Depto e Buscar produto a aba NÃO
  honra**, e avisa na tela.

⚠️ **Guarda-se o INGREDIENTE, não o resultado.** A foto grava `qtdisp`, custo, giro, datas, curva
e XYZ por item — não "parado = R$ X". Gravar o número pronto congelaria a série na régua daquele
dia, e aí **corrigir uma régua viraria degrau no gráfico**. Numa aba feita para provar gestão,
degrau de definição é lido como resultado de operação. Com o cru, mexer em `novo_dias`,
`ideal_dias` ou no `eh_parado` recalcula o passado inteiro.

⚠️ **A ruptura é CONTRAPESO, não enfeite.** Estoque caindo, sozinho, pode ser desabastecimento.
Por isso o **valor de estoque é a única métrica sem cor** — só parado ↓, ruptura ↑ e % ideal ↑
têm direção inequívoca. Pintar queda de estoque de verde faria a aba um dia comemorar uma ruptura.

- ⚠️ **Duas convenções de faixa, ambas espelhando a tela:** as faixas de cobertura seguem o Painel
  gerencial (giro ≤ 0 cai no 121+, então Σ faixas = valor de estoque); o trio ideal/risco/sem-giro
  segue o Estoque ideal (sem-giro à parte).
- ⚠️ **A curva gravada usa janela MÓVEL de 90 dias**, não o "mês atual". O default é o acumulado
  do mês: no dia 1º a ABC sairia de UM dia de venda — dente de serra em toda virada de mês.
- **Na demo** o robô não roda (o "hoje" é ancorado no dado); o histórico vem de um seeder de 90
  dias, senão a aba abriria vazia na apresentação comercial.
- 🚧 **Ainda não tem export.** Enquanto for ADM-only não faz falta.

### 7.5 Abastecimento (Comprar) — "o que comprar, por fornecedor"
A tela principal de compra: itens com sugestão > 0, **agrupados por fornecedor**.
- **Cabeçalho de cada fornecedor:** nº de itens · **m³** · **kg** · **valor total (régua da NF)** ·
  botão **"Gerar pedido"** (abre o construtor já preenchido).
- **Colunas:** Cód · Produto · Embalagem (com fator un/cx) · Disp. · Já ped. · **Cob.proj** ·
  Giro/mês · **Sugerido (cx)** · m³ · **Valor sug. (mercadoria)** · **Imp.** (IPI+ST previstos) ·
  Status.
- Itens com alíquota estimada saem marcados com **`≈`**, e o rodapé informa quanto do valor está
  apoiado em estimativa.

### 7.6 Estoque zerado (Comprar)
Todos os produtos com estoque gerencial ≤ 0 (inclusive negativos).
- **KPIs:** Zerados/negativos · Com giro (ruptura real) · Já com pedido · **Venda perdida** ·
  **Custo de reposição** (régua da NF).
- **Colunas:** Cód · Produto · Fornecedor · ABC · Comprador · Estoque · **Dias s/ venda** ·
  Já ped. · Giro/mês · Sugerido (cx) · Status. Filtro por status.

### 7.7 Plano reposição / DRP (Comprar)
Grade **semanal** de um produto: projeta o saldo semana a semana (12 semanas), gera **pedidos
planejados** quando cruza o estoque de segurança, e calcula **quando o pedido precisa sair**
(= data de recebimento − lead time). Como o BI não tem dados de trânsito, o reabastecimento é
**planejado**, não rastreado.

### 7.8 Orçamento (Pedidos)
Meta de compras do mês × realizado (§5.17).
- **KPIs:** Meta do mês · Comprado (Winthor) · Saldo · % Consumido, com barra de progresso.
- **Alertas:** entregas atrasadas · chegam em ≤7 dias · transferências entre filiais excluídas.
- **Orçamento por comprador:** Meta / Comprado / Aberto / Saldo / % Consumido (só na visão
  "Empresa toda"). Ordenável.
- **Acompanhamento de pedidos em aberto:** pedidos reais do Winthor ainda não recebidos, com
  previsão de entrega e status. **Clicar num pedido abre os itens** (Pedida / Entregue /
  A entregar); clicar num item abre o 360°.
- **Pedidos da nossa plataforma:** criados no app, pendentes de envio ao Winthor. **Não somam na
  meta.** Cada um tem PDF, planilha e remover.

> **Logística — cubagem & ocupação** existe no código mas está **oculta do menu** a pedido do
> diretor. Calculava, dos pedidos em aberto, a cubagem (Σ qtd × volume unitário) e a ocupação
> (cubagem ÷ 60 m³/veículo), marcando baixa ocupação como candidato a consolidar carga.

### 7.9 Cobertura (Estoque)
Distribuição do capital por faixa de cobertura (métrica oficial, §5.3).
- **Cards por faixa** (0-30 … 121+) com valor de estoque + gráfico + visão "por comprador".
- **Filtro de faixa multi-seleção.** No **121+** há sub-filtro **"sem giro × excesso real"** —
  separa estoque morto (liquidar) de cobertura alta (reduzir compra).
- **Colunas:** Cód · Produto · Fornecedor · ABC · Comprador · Disp. · Disp. cx · Valor estoque ·
  Cob. · Já ped. · Giro/mês · Giro cx · Sugerido · Faixa.

### 7.10 Parado (Estoque) — "o que liquidar"
Itens com estoque e **≥15 dias** parados. **Reconciliado com a Cobertura:** as faixas
**somam o total**.
- **Cards por faixa** (**Novos** · 15-30 … 121+) + gráfico + "por comprador".
- **Card "Novos (<15d)"** (08/2026, §5.14) — itens que **nunca venderam** e **entraram há menos de
  `novo_dias`**. Ele não acrescenta itens à aba: **tira do 121+** os que estavam rotulados errado,
  por isso entra na mesma soma. *Medido na virada:* 18 itens saíram do 121+ (10 para Novos, 8 para
  a faixa real), total da aba intacto. Consequência: o **121+ e o capital parado afrouxam sem
  ninguém mexer na operação** — comparar antes×depois uma vez, senão parece ganho de gestão.
- **Colunas:** Cód · Produto · Fornecedor · ABC · Última venda · **Dias parado** · **Chegou há** ·
  Disp. · Disp. cx · Valor · Saída (recente ≤30d / média ≤90d / antiga >90d) · Faixa ·
  **Ação** (plano).
  Em item novo, "Dias parado" mostra **"chegou há Xd"** em vez de "nunca" — dizer "nunca" ao lado
  de um card que acabou de chamá-lo de recém-chegado era contradição na mesma linha.

### 7.11 Validade / FEFO (Estoque)
Controle de vencimento **por lote**, sobre o estoque **endereçado** (`PCESTENDERECO`, RUA ≠ 99),
com horizonte configurável.
```
saldo projetado = quantidade do lote − (giro diário × dias até vencer)
valor em risco  = max(0, saldo projetado) × custo
```
- **Cards por faixa** (0-15, 16-30, 31-60, 61-90, 90+) + gráfico + "vencimento por comprador".
- **Colunas:** Cód · Produto · ABC · **XYZ** · Lote · Validade · Dias · Qtd · Saldo proj. ·
  Valor risco · Classe · Ação. **Classes:** crítico (≤7d) · atenção (≤15d) · planejar.
- ⚠️ **A coluna XYZ (19/08/2026) é o qualificador da própria estimativa desta tela.** O
  `saldo proj.` e o `valor risco` saem do giro MÉDIO; num item **Z** (demanda errática) essa
  média é justamente o número menos confiável. Item **X** com 30 dias de validade é
  administrável; item **Z** com os mesmos 30 dias não se projeta.
  *Medido na base real (578 lotes, horizonte 600d):* os **Z são 13% dos lotes e 27% do valor em
  risco** — mais de um quarto do risco está onde a previsão vale menos. A leitura da aba deixa
  de ser só "ordenar por dias a vencer" e passa a ser "olhar primeiro os Z de validade curta".
  O XYZ é do PRODUTO e a linha é do LOTE: vem vazio (`—`) para item sem série de 3 meses ou fora
  do snapshot da unidade — 3% dos lotes. Gate: `tests/test_validade_xyz.py`.
- O **nome do produto vem do próprio lote**, então item zerado no gerencial (que só tem lote)
  aparece com o nome certo, não "PRODUTO {código}".

### 7.12 Vencidos (Estoque) — perda REALIZADA
Contraponto da Validade: lá é risco futuro, **aqui é perda que já aconteceu** (conta **200042**
do Winthor), mês a mês.
- **Colunas:** Vezes (reincidência ao longo de todo o histórico) · Qt perdida · **Já perdido** ·
  Em estoque (o que ainda pode vencer de novo) · Próx. venc. · Última perda · P. unit. · Total ·
  Part. · **% venda** (perda ÷ venda líquida do comprador, all-time, só na visão "Tudo").
- ⚠️ **Join por `NUMTRANSVENDA`, NUNCA por `NUMNOTA`.** O `NUMNOTA` repete ao longo dos anos e
  infla o resultado **~3,5×** — e o `SELECT DISTINCT` **não** corrige.

### 7.13 Ruptura por comprador (Estoque)
Ruptura agregada em **duas tabelas**: **por comprador** e **por curva ABC**.
- **KPIs:** Itens em ruptura (+ sem pedido) · Venda perdida · Sugestão de compra · Compradores.
- **Colunas:** Produtos · Em ruptura · **% Rupt.** · **Dias rupt. méd** (média de dias sem venda
  dos itens em ruptura — quem demora mais a reagir) · Sem pedido · **% s/ ped.** (base da meta —
  todo item conta, não só os em ruptura) · **Venda perdida** · **Sugestão de compra**.
  Cada tabela tem **linha de TOTAL**.
- ⚠️ A coluna **Sugestão de compra** é o **mesmo valor da aba Abastecimento**: a soma de **todos**
  os itens a comprar do comprador, **não só os zerados** (decisão do diretor 07/2026).
- **Clicar numa curva (A/B/C)** abre os itens daquela curva — mostra quanto da ruptura está em
  cada curva de venda (A = os campeões).

### 7.14 Ocupação (Estoque)
Ocupação das posições do depósito segundo o WMS (bate com a consulta **1772** do Winthor).
- **Colunas:** Posições (slots com estoque) · **% ocup.** · m³ endereçado · Qtd (sistema) ·
  Situação (com estoque / vazia reservada). Tipos: AP = Picking, AE = Pulmão.
- Suporta **conferência por rua** e listagem de **vagas vazias**.

### 7.15 Desempenho comercial (Análise)
Ranking dos **compradores** pelo resultado comercial dos produtos que eles compram.
```
Venda líquida = venda bruta − devoluções
Lucro bruto   = venda líquida − (custo − custo da mercadoria devolvida)   [alinhamento RCA]
Margem        = lucro ÷ venda líquida
```
- **Colunas:** Ranking · Comprador · Fornec. · **Positivação** (clientes distintos) · Qtd ·
  Venda líq. · Devolução · Lucro bruto · Margem · **% Lucro** (participação) · **AA Venda** ·
  **AA Lucro**.
- Status de lucro: negativo · alta (≥30% do lucro) · boa (≥8%) · baixa.

### 7.16 Compras × Vendas (Análise)
Cruzamento **estoque (compras) × venda × lucro × margem**, em 3 visões: **por comprador · por
fornecedor · por produto**.
- **Venda/Estoque** = quantas vezes o capital girou no período.
- Por fornecedor: Itens · Estoque · Venda · Lucro · Margem · Venda/Estoque · Ruptura · % Rupt. ·
  Parado + **ABC do fornecedor** (e o filtro Curva age por ele).

### 7.17 Fornecedores (Análise)
Compara **quanto o fornecedor vende × quanto pesa em estoque**, e agora também o **ciclo de
compras e a rentabilidade com verba**.
```
Índice = % na venda (R$) ÷ % no estoque (R$)      (> 1 = vende mais do que pesa)
```
**Classificação:** `critico_sem_giro` (sem giro e sem venda) → `ruptura` (gira, mas cobertura <
lead) → `alta_performance` (índice ≥ 1,2) → `equilibrado` (≥ 0,8) → `estoque_alto` (< 0,8).

**Colunas:** Cód · Fornecedor *(as duas travadas no scroll lateral)* · ABC · Itens · Estoque ·
% est. · Giro/mês · Cob. · Venda · % venda · **Crescimento AA** · **Índice** · Classe ·
**Compras** · **Ciclo 12m** · **Lucro bruto** · **Verba** · **Lucro c/ verba** ·
**Margem c/ verba**.

Fórmulas em §5.20 (compras/ciclo), §5.21 (crescimento) e §5.22 (verba/lucro).

⚠️ **Notas de implementação que explicam comportamentos:**
- As colunas de ciclo/verba/crescimento chegam por um endpoint separado
  (`/api/fornecedores_extra`), buscado **só quando a aba abre** — pendurá-las no snapshot faria
  toda tela do painel pagar por duas abas que a maioria não abre. Enquanto não chegam, saem `—`
  com aviso.
- A aba é calculada **duas vezes** — no front (para os filtros responderem sem round-trip) e no
  back (para o export). Coluna nova precisa entrar nos dois, senão o Excel/PDF diverge da tela.

### 7.18 Lead time (Análise)
Quanto cada fornecedor demora entre o pedido ser emitido e a NF entrar no estoque (12 meses).
- **Colunas:** Fornecedor · Comprador · Pedidos · **% na hora** · **Lead todos** · **Lead real** ·
  **Prazo manual** · **Δ** · Situação (cadastro OK / inflado / otimista / sem lead confiável).
- **Drill de auditoria:** clicar no fornecedor abre os pedidos que formaram o número.
- Fórmulas e o porquê da média × mediana em §5.23.

### 7.19 Verbas (Análise)
Verbas/bonificações negociadas com fornecedores (**rotina 1801** do Winthor).
- **Contas mapeadas:** 250009 Rebaixa de custo · 250008 Conta corrente · 200013 Premiações e
  campanhas · 200042 Perda validade.
- **Regras:** canceladas (`DTCANCEL`) e estornos (`DTESTORNO`) ficam **fora**;
  `saldo = VALOR − Σ aplicações`. O **saldo é posição ATUAL** (qualquer data de emissão — saldo é
  estoque, não fluxo); o **placar de negociado/aplicado é de 12 meses**, para casar com a compra
  12m do %V/C.
- **Colunas:** Fornecedor · Comprador · Verbas 12m · **Negociado** · **Aplicado** ·
  **Saldo aberto** · **Idade** (dias do saldo mais antigo) · **Compra 12m** · **% V/C** (a "taxa
  de devolução" do fornecedor — o argumento de negociação) · **Lead** · Situação.
- **Situação:** aplicada (sem saldo) · saldo em aberto · **saldo PARADO** (aberto há mais de
  **120 dias**). Alerta para fornecedor com **compra 12m > R$ 300 mil e nenhuma verba**.
- **O tripé:** compra × lead × verba — quanto compro, quanto demora, quanto devolve.
- Conferida contra o relatório **1826** (BOMBRIL centavo a centavo no recorte 2024+).

### 7.20 ABC-XYZ (Análise)
Matriz **curva de vendas (ABC) × variabilidade da demanda (XYZ)** — 9 células com nº de itens e
venda do período. Estratégias: **AX** = campeão previsível (controle rígido, estoque enxuto) ·
**AZ** = alto valor e imprevisível (foco do comprador) · **CZ** = candidato a descontinuar.
Clique numa célula lista os produtos.
⚠️ As opções do filtro XYZ precisam de `value` explícito (`X`/`Y`/`Z`) — sem isso o filtro casa
nada e devolve **zero produtos em silêncio**.

### 7.21 Produtos (Análise) — o explorador
Tabela completa de todos os produtos: Cód · Produto · Fornecedor · ABC · XYZ · Disp. · Disp. cx ·
**Avaria** · Giro/mês · Giro cx · Cob. · Dias s/V · Venda · Lucro · Margem · Estoque · Abast.
- **Colunas Cód + Produto congeladas** ao rolar lateralmente.
- Filtros locais de **Abastecimento** (status, multi-seleção), **Margem** (Negativa · 0–10% ·
  10–20% · 20–30% · 30%+ · Sem venda), **Cobertura ≤ (dias)** e **Só sem pedido**.
- É o **destino do drill** do card "Em risco" do Painel gerencial (§5.18): ele chega com
  `Cobertura ≤ limiar−1` já preenchido, e o Excel/PDF saem com o mesmo recorte.

### 7.22 Qualidade da base (Análise)
Lista itens com problema de cadastro para o TI/diretor corrigir: sem custo · sem fornecedor ·
sem comprador · sem giro com estoque · estoque negativo.

---

### 7.23 Pesquisa de preço — **tela de CAMPO, fora do painel**

Pedido do diretor (19/08/2026): *"podemos ir para as visitas de pesquisas e ir preenchendo o
preço concorrente direto nos itens"*. É a **primeira feature em que a ferramenta vira FONTE** de
um dado que o Winthor não tem — todo o resto do módulo é leitura derivada do ERP.

**O que ela mede:** o comprador vai a **atacados CONCORRENTES** ver por quanto **eles vendem** o
mesmo item, e compara com o **NOSSO preço de venda** — *"para saber se o meu preço de venda está
dentro da prática usada no mercado em geral ou não; meu preço está abaixo, igual ou acima"*. É
trabalho de compra porque a margem se calcula sobre o custo: custo alto entra, preço de venda sai
fora da praça.

⚠️ **A 1ª versão comparava com o nosso CUSTO (`CUSTOFIN`) — o pedido foi lido errado**, e o
estrago era duplo e silencioso: (1) como o custo é sempre MENOR que o preço de venda, o gap saía
enviesado para o **verde** — a tela dizia "estamos bem" mesmo quando vendíamos acima do mercado,
o que **inverte a conclusão** na direção que não gera ação; (2) o Excel/PDF que vai ao
**fornecedor** levava o nosso custo de aquisição na coluna "Nosso preço". Corrigido em 08/2026.
O sintoma que denunciou: o diretor perguntou *"eu marco qual?"* no seletor de imposto — quando o
usuário-alvo não consegue responder ao próprio formulário, o campo está modelando outra coisa.

⚠️ **NÃO é uma aba, de propósito.** A captura acontece em outro contexto — pessoa em pé, uma mão,
sinal ruim — e toda tela do painel pressupõe o oposto (sentada, tabela de 20 colunas sobre o
snapshot inteiro). É uma página própria em **`/estoque/pesquisa`**, que não entra no menu. Chega-se
por ela pelo botão **🔎 Pesquisa de preço** na barra de filtros do painel.

**Quem usa:** time de compras (reusa a área `compras`; sem ela, 403).

#### Capturar
Fornecedor da visita *(opcional)* → busca de produto → preço → un/cx → **onde foi pesquisado** →
salvar. Os seletores de *"origem do preço"* (fornecedor/concorrente) e *"tem imposto?"* saíram:
a aba é só pesquisa de concorrente (*"sobre fornecedor, não vamos usar ele"*) e o preço é sempre
o **cheio** de gôndola, comparado com o nosso preço de venda, que também é cheio. As colunas
seguem gravadas no banco — histórico não se regenera —, o que saiu foi a **pergunta**.

- **Roteiro por fornecedor:** escolhido o fornecedor, a lista dele aparece **sem digitar nada** —
  é ela que é o trabalho da visita. Vai até 200 itens (a busca livre corta em 30) e sai ordenada
  por **nome**, porque na prateleira se procura pelo nome. A busca passa a recortar dentro dela.
- **Fila offline:** o registro entra no `localStorage` **antes** de tentar a rede e sincroniza
  sozinho quando a conexão volta. Sem isso, 40 itens digitados numa loja sem sinal viram nada — e
  não há segunda visita. Erro 4xx (dado inválido) não volta para a fila, senão ela nunca esvazia.
- ⚠️ **Busca precisa de rede.** A fila protege a queda de sinal **depois** de escolher o item, não
  a visita inteira offline. Busca offline exigiria cachear o catálogo no celular — outra feature.

#### Consultar e enviar
Botão **Pesquisas** no cabeçalho da mesma tela: lista do que foi levantado, **com quem
preencheu**, filtro de período, recorte pelo fornecedor selecionado, e **Excel + PDF**.

⚠️ **O documento leva o NOSSO PREÇO DE VENDA junto do pesquisado** — decisão explícita do
diretor (*"pro fornecedor, poderia mandar nosso preço atual e o preço pesquisado"*). Preço de
venda é público: está na gôndola e na tabela. Até 08/2026 a coluna era o **custo de aquisição**,
que é a única coisa que não se manda a quem negocia conosco.

#### Onde o preço reaparece
- **360° do produto:** uma linha dentro de "Venda no período", com a diferença vs o nosso custo.
- **Modal de gerar pedido:** uma linha sob o nome do item — é o **momento da decisão**, o que
  transforma digitar em campo numa alavanca em vez de tarefa.

#### As armadilhas que decidem se o dado presta
1. **Unidade.** Quem pesquisa lê a etiqueta da CAIXA e o nosso preço é por unidade. A linha
   guarda `un`/`cx` e o servidor divide pelo fator. **Sem fator de caixa não se chuta:** devolve
   incomparável e a tela mostra `—`. É a mesma família do pedido que saía ~50× errado por
   converter quantidade sem preço.
2. **Imposto: NÃO se desconta.** Os dois lados são preço de venda cheio (*"o nosso preço de venda
   é com imposto"*). A conversão para mercadoria existia por causa do `CUSTOFIN` e morreu com
   ele — reintroduzi-la faria a tela dizer que estamos baratos justamente onde estamos caros.
3. **Data e lugar.** Preço sem data apodrece em silêncio; preço sem origem não se confere nem se
   volta a negociar. Quando o lugar falta, a lista escreve **"local não informado"**.
4. ⚠️ **Não usar o `preco_venda` do produto.** Em `core.construir_produtos` esse campo cai em
   `custofin` quando o item não vendeu em 3 meses (a venda perdida precisa do fallback). Aqui
   devolveria o **custo rotulado como preço de venda** — o vazamento de volta, com etiqueta
   errada. Sem preço realizado, a coluna sai **vazia**.

**Nosso preço = realizado médio dos últimos 3 meses** (`_preco_venda_map`, cache 6h). O preço de
tabela do BI (`PCPRODUT[PVENDA]`) está vazio nesta base; a régua foi aprovada pelo diretor
(*"pode pegar a média de preço dos últimos 3 meses"*).

⚠️ **A COR é pela perspectiva de quem compra** (decisão do diretor): concorrente mais **barato**
= o nosso preço está acima do mercado = **vermelho**. É o inverso da leitura de "oportunidade" —
para quem compra, vermelho significa *"estou fora da praça, aja"*. A convenção **não mudou**
quando a referência passou de custo para preço de venda, porque o sinal significa a mesma coisa.

**Fonte única:** `_pesquisa_enriquecida` monta a comparação para a tela de campo, o **drawer
360°** e os **exports**. O drawer recalculava o gap por conta própria no JS e as duas versões já
tinham divergido — hoje ele só renderiza o que o servidor mandou.

Gates: `tests/test_pesquisa_preco.py` (normalização) e `tests/test_pesquisa_acesso.py`
(acesso, forma da tela, fila offline, cor, local).

### 7.24 Agente de IA — o analista que conversa *(recurso adicional)*

> ℹ️ **É um MÓDULO OPCIONAL.** O Agente só existe na instância que o contratou
> (`MODULOS=comercial,compras,ia` na stack). Onde não está ligado, **o botão nem aparece** — não
> há nada a explicar ao usuário. Se você está lendo isto e não vê o botão no seu painel, é isso.


Botão flutuante no canto do painel. Ele lê os números **do recorte que está na sua tela** e
responde em texto. Não é uma aba: acompanha você em qualquer tela do módulo.

**O que ele enxerga:** o placar consolidado (estoque, capital parado, ruptura, cobertura ideal),
a compra sugerida e o orçamento do mês, as faixas de cobertura, os maiores ofensores de capital
parado, as rupturas que mais custam e — para o administrador — a série da Evolução.

**Os 14 pilares.** O agente recebe um ranking por tema, com os 10 maiores (e os menores, onde a
pergunta é essa):

| Pilar | Responde |
|---|---|
| Estoque parado | maiores valores parados, quem está há mais tempo sem vender |
| Cobertura | maior cobertura, o que vai faltar primeiro, capital preso acima de 120 dias |
| Ruptura | por curva, por comprador, as que mais custam, as sem providência |
| Meta de ruptura | o placar por curva contra a meta (régua sem-providência) |
| Fornecedores | maior estoque, maior/menor lucro, mais capital parado, maior compra sugerida |
| Compradores | estoque, parado, ruptura e a comprar de cada um |
| Desempenho | venda, lucro, margem e crescimento AA por comprador |
| Validade | o que vence primeiro e quanto vale |
| Vencidos | perda **já consumada** por validade, mês a mês |
| ABC-XYZ | a matriz inteira |
| Ocupação | posições, m³ e **espaço morto** |
| Lead time | quem demora mais e menos para entregar |
| Verbas | negociado, aplicado e saldo por fornecedor |
| Qualidade | problemas de cadastro (nos dois universos) |

**O que ele NÃO enxerga:** o catálogo item a item fora dos rankings, e o **Plano de reposição**
(que é simulação semana a semana de um produto, não pergunta de panorama).

⚠️ **Todo item vem com o CÓDIGO do produto.** Pedido do diretor: *"sempre trazer o cod, pois
assim conseguimos validar — por exemplo, essa semana chegou copo, pode ser que isso tenha
implicado nessa avaliação"*. É para você conferir no ERP: um item pode aparecer como maior espaço
morto justamente na semana em que chegou carga dele, e só com o código dá para ir ver o que
aconteceu. Vale para fornecedor também (código + nome).

⚠️ **Quando o recorte não está nos pilares, ele NÃO diz apenas "não tenho".** Como os números
dele respeitam os filtros do topo, ele pede para você aplicar o filtro (um fornecedor, um depto)
e refazer a pergunta — aí responde no seu recorte.

**Recorte:** os números respeitam os filtros do topo. Ele é obrigado a dizer o recorte na primeira
frase, e se você mudar o filtro com o chat aberto a conversa avisa que as respostas anteriores
eram de outro recorte.

⚠️ **Ele não calcula nada** — narra números que o painel já produziu. É por isso que bate com a
tela sempre: se somasse por conta própria, divergiria no primeiro arredondamento.

⚠️ **A série da Evolução tem régua própria.** Ela conta como parado o item sem venda há **15 dias
ou mais**; o placar conta a partir de **60**. Por isso o valor da série é sempre bem maior — são
medições diferentes, não uma variação. O agente é instruído a comparar pontos da série entre si e
nunca com o placar. *(A divergência entre as duas telas é anterior ao agente.)*

**Sem o recurso contratado**, o botão continua aparecendo e mostra o que o Agente faria — é
recurso adicional, e o servidor recusa qualquer pergunta (nenhum consumo indevido).

Gate: `tests/test_ia_compras.py` · motor em `estoque/ia.py`.

## 8. Componentes: drawers e modais

### 8.1 360° do produto (clique em qualquer produto, de qualquer lista)
Ficha completa do item:
- **Cabeçalho:** descrição · cód · fornecedor · badges **ABC** e **XYZ** · comprador.
- **KPIs:** Disponível · Valor em estoque · Giro/mês (com mini-gráfico) · Cobertura (+ barra).
- **Fonte do giro:** informa se é média 3m, forecast, sazonal, item novo ou mês corrente (§5.2).
- **Venda no período:** Venda · Lucro (com margem) · Qtd vendida · gráfico de venda 12m em R$
  (com o mês corrente marcado como parcial).
- **Situação:** Abastecimento · Ruptura · Parado · **Última saída** e **última entrada** (datas +
  dias) — é o par que separa estoque velho de compra recente errada.
- **Abastecimento:** Embalagem · Já pedido · **Em pré-entrada** (quando houver) · Estoque
  projetado (+ cobertura proj.) · Estoque alvo · **Sugestão de compra** (qtd + valor) · Status.
- **Plano de ação** e **Plano no tempo (12 semanas)** — gráfico DRP com saldo projetado,
  recebimentos e a linha do estoque de segurança; indica o próximo pedido e **quando ele precisa
  sair**.
- **Lotes / validade** endereçados do item.
- Botão **"Registrar pedido"**.

### 8.2 Construtor de pedido de compra
Modal para montar um pedido **da plataforma** (pendente de envio ao Winthor).
- Cabeçalho: Data · Fornecedor · Nº pedido · Prazo · Valor.
- Tabela de itens: Cód · Produto · **Qtd (un)** · **Caixas** · Cx · **Custo editável** ·
  **IPI/ST % editável** (quando a alíquota é estimada) · Valor · remover.
- O botão **"Gerar pedido"** da aba Abastecimento abre o construtor **já preenchido** com os
  itens sugeridos daquele fornecedor.
- **Lançar** salva o pedido; ele aparece em "Pedidos da nossa plataforma" e **não soma na meta**
  até ser lançado no Winthor.

⚠️ **O campo "Caixas" é só uma VIEW.** O comprador raciocina em caixa (pedido do diretor
07/2026), mas o campo apenas escreve `qtd = caixas × QTUNITCX`: **a unidade continua a única
fonte de verdade** no modelo e no payload. **Nada em caixa sai para o backend.** Item sem fator
de caixa mostra "—" e só aceita unidade.

### 8.3 Documentos do pedido
- **PDF (estilo relatório 211 do Winthor):** logo + bloco **Emitente** + bloco **Fornecedor**
  (CNPJ/IE/endereço do PCFORNEC) + tabela **Cód · Cód fábrica · Produto · Un · Qtde · Custo un. ·
  IPI% · Vlr. Total**, em retrato, ordenado por código, com **total do pedido + peso total**.
  Arquivo nomeado pelo fornecedor.
- **Planilha de importação do Winthor (XLSX).**

⚠️ **Duas armadilhas que já custaram caro:**
1. **O preço converte JUNTO com a quantidade.** O Winthor faz `B × C` literal — converter só a
   quantidade colocaria o pedido no ERP com valor ~50× menor. Fonte única: `core.item_master`
   (usada pelo PDF **e** pela planilha), que garante `qtd × preço` constante na conversão
   un ↔ cx.
2. **A planilha leva o preço LÍQUIDO — nunca com IPI.** O ERP calcula o imposto sozinho na
   importação (foi assim que 132,05/caixa virou NF de R$ 44.982,01). Mandar preço com imposto
   faria o ERP aplicar **IPI sobre IPI**: pedido ~15% inflado e custo de entrada errado.

### 8.4 Itens de um pedido do Winthor (drill do Orçamento)
Clicar num pedido em aberto abre os itens **reais**: Cód · Produto · Pedida · Entregue ·
A entregar. Clicar num item abre o 360°.

### 8.5 Plano de ação
Registro por item ou lote (usado principalmente em **Parado** e **Validade**):
**Responsável · Ação** (ex.: ENCARTE, DEVOLUÇÃO, BONIFICAÇÃO) **· Prazo · Status**
(PENDENTE / EM ANDAMENTO / CONCLUÍDO). Salvo no Postgres, aparece como **badge** na linha do
item; pode ser editado ou excluído.

---

## 9. Parâmetros (⚙) — valores padrão

| Parâmetro | Padrão | Onde entra |
|---|---|---|
| Lead time (dias) | **10** | estoque-alvo, status de abastecimento |
| Estoque de segurança (dias) | **25** | status de abastecimento, DRP |
| **Cobertura alvo (dias)** | **45** | **estoque-alvo (a COMPRA)** |
| Horizonte de validade (dias) | **30** *(o diretor costuma usar 120)* | aba Validade |
| Parado: dias parados (≥) | 60 | filtro de exibição da aba Parado |
| Meta % s/ ped. curva A / B / C | **2 / 5 / 10** | Meta de ruptura |
| **Estoque ideal: mínimo (dias)** | **45** | **só MEDE o Painel gerencial** |
| **Estoque ideal: meta (%)** | **90** | gatilho de alerta do Painel gerencial |
| **Produto novo: até (dias)** | **15** | faixa `novo` da aba Parado (§5.14) — item que nunca vendeu e acabou de entrar |
| Demanda (giro) | Média 3m (oficial) | giro — alternativas: Forecast, Forecast sazonal |
| Janela do forecast (meses) | 6 | forecast |
| Arredondar por caixa | Caixa fechada | sugestão em caixas fechadas |
| Curva ABC (A / B) | 80% / 95% | classificação ABC |
| XYZ (X / Y) | CV 0,5 / 1,0 | classificação XYZ |

⚠️ **"Cobertura alvo" e "Estoque ideal: mínimo" são independentes.** Um diz **até onde comprar**;
o outro diz **a partir de quanto o SKU conta como coberto** no placar gerencial. Mexer num não
mexe no outro.

⚠️ Os parâmetros ficam no **navegador** (`localStorage`). Enquanto um valor não for promovido a
padrão do servidor, o painel pode significar coisas diferentes para cada pessoa.

---

## 10. Exportações

- **Todas as tabelas** exportam **CSV, Excel (XLSX) e PDF**, respeitando **os filtros da tela**.
- **PDF de Produtos:** sai **agrupado por fornecedor**, com cabeçalho por grupo (nº de itens ·
  Σ estoque · Σ já pedido) e coluna "Já ped.".
- ⚠️ **Filtro de tela tem de viajar no export.** `margem`, `cob_max` e `sem_ped` existiam só no
  front: a tela mostrava 116 itens e o PDF saía com o universo inteiro. Ao criar filtro novo,
  espelhe no backend.

---

## 11. Relatórios por e-mail (Compras)

- **16 relatórios**, todos com PDF + XLSX prontos. Catálogo **único** em
  `estoque/relatorios.py` — Admin e cron leem do mesmo lugar (evita "aparece no Admin mas o cron
  não sabe gerar").
- **Regra de corte:** só entra tela em formato de **tabela**. Painéis (Cockpit, Painel gerencial,
  Meta de ruptura, Plano reposição, Orçamento) **não são relatórios** e ficam de fora por
  definição.

| Grupo | Relatórios |
|---|---|
| **Comprar** | Abastecimento (o que comprar) · Estoque zerado · Cobertura · Ruptura por comprador |
| **Risco** | Estoque parado · Validade (FEFO) · Vencidos (perda realizada) |
| **Análise** | Desempenho comercial · Compras × Vendas · Fornecedores · Compradores · Produtos · ABC-XYZ · Qualidade da base |
| **Ocupação** | Conferência de endereços · Vagas vazias |

- O admin marca **por usuário** quais ele recebe; reusa o horário/frequência do cron do Comercial.
- ⚠️ **O recorte por comprador vai na query string** do contexto simulado — o módulo Compras lê
  filtro de `request.args`, **não da sessão** (diferente do Comercial).
- ⚠️ **O parâmetro é `comprador_cod`, não `comprador`.** Até 07/2026 o e-mail mandava
  `comprador`, que ninguém lê no export: o comprador vinculado recebia relatório **rotulado com o
  nome dele e com os dados da empresa toda** — inclusive o desempenho dos colegas. Exceções
  legítimas: **Conferência de endereços** e **Vagas vazias**, cujo grão é o endereço e não têm
  comprador.

---

## 12. Perguntas frequentes (FAQ do agente)

### Estoque e giro
- **"O estoque mostrado é o total do sistema?"** Não — é o **gerencial líquido**
  (`QTESTGER − avaria − reserva`) das filiais da unidade selecionada. Só a **Validade** usa o
  endereçado por lote (§5.1).
- **"Por que a cobertura deste item é '∞' / não calculável?"** Giro 0. Sem demanda não há dias de
  cobertura; o item cai na faixa 121+ (§5.3).
- **"O item vende, mas o giro está 0."** Provável item novo: os campos `QTVENDMES1..3` são os 3
  meses **fechados** anteriores e ainda estão zerados. O app cobre isso com o giro de item novo e,
  em último caso, com a venda do mês corrente (§5.2). O 360° informa a fonte usada.
- **"A curva ABC muda quando troco o período de venda?"** Sim — é o ABC do faturamento do período
  (§5.10). Já a **venda perdida não muda** (janela fixa de 3m, §5.11).

### Compra e valores
- **"Por que a sugestão é menor que o buraco do estoque?"** Porque ela **desconta o já-pedido em
  aberto** e a mercadoria em pré-entrada (§5.5–5.8).
- **"O app sugeriu comprar algo que já chegou."** Não deveria mais: a pré-entrada entra no
  projetado e o item fica com status `aguardando_liberacao` (§5.6). Se ainda acontecer, o
  bloqueio pode ser antigo (>7 dias) e estar sendo lido como avaria.
- **"Por que o total do card difere do 'Valor sug.' da tabela?"** São as **duas réguas**: o card
  fala **NF** (com IPI/ST) e a coluna "Valor sug." fala **mercadoria** — porque é ela que vai na
  planilha do Winthor (§5.9).
- **"Gerei o pedido e o Winthor registrou um valor maior."** É a diferença mercadoria × NF. Desde
  07/2026 o app prevê IPI e ST pela tributação de entrada do ERP e mostra os dois valores (§6).
- **"O que significa o `≈` ao lado do imposto?"** A alíquota é **estimativa** (o item não tem
  figura fiscal para a UF daquele fornecedor). O % fica **editável** antes de gerar o pedido
  (§6.1).
- **"Por que a venda perdida e o custo de reposição não batem?"** Coisas diferentes: uma é a preço
  de **venda** no período parado; o outro é o que falta comprar até o alvo, **a custo com
  imposto** e descontando o já-pedido (§5.12).
- **"O pedido criado no app conta na meta do comprador?"** Não — só quando for lançado no Winthor
  e voltar pela base oficial (§5.17). Evita contar duas vezes.
- **"Por que um pedido do Winthor não aparece?"** Provável cache (30 min) ou o BI do cliente sem
  atualizar.
- **"O valor do orçamento caiu sem explicação."** Confira o contador de **transferências entre
  filiais**: pedido cujo fornecedor tem a mesma raiz de CNPJ da empresa é transferência, não
  compra, e fica fora do orçamento (§5.17).

### Fornecedores, verba e lead time
- **"O crescimento AA do fornecedor não responde ao meu filtro."** Correto, e é proposital: a
  coluna é do **fornecedor inteiro**, porque somar só os produtos da tela quebrava o denominador
  (§5.21). A tela avisa quando há filtro ativo.
- **"O ciclo de compras não mudou quando troquei o período."** Também proposital: o ciclo é sempre
  **12 meses** — é comportamento do fornecedor (§5.20).
- **"Ciclo saiu '—'."** Menos de 2 compras em 12 meses. Nunca sai 0.
- **"A verba inclui campanha?"** Sim, hoje **toda** verba entra, inclusive "Premiações e
  campanhas" — mas essa parcela é informada em separado na tela (§5.22).
- **"Por que 'Lead todos' é média e 'Lead real' é mediana?"** Porque com muitos pedidos digitados
  na hora a mediana de todos colapsaria para 0; e a mediana do lead ≥2d é imune às caudas longas
  (§5.23).
- **"Fornecedor com '% na hora' alto é ruim?"** Não é o fornecedor — é o **processo**: o pedido
  nasceu fora do ERP e foi lançado junto com a NF. O indicador serve para acompanhar isso caindo.

### Placares e metas
- **"A Meta de ruptura ignora meus filtros."** De propósito — meta que muda de valor conforme o
  filtro não é meta (§5.19).
- **"O placar de ruptura melhorou sem ninguém mudar nada."** Provável efeito da separação das
  curvas A/B/C: os itens C ganharam orçamento próprio. Compare antes×depois uma vez (§5.19).
- **"Mudei a Cobertura alvo e o Painel gerencial não mexeu."** Correto: o painel usa
  **"Estoque ideal: mínimo"**, que é uma régua independente (§5.18, §9).
- **"O 'Estoque ideal' está em 100%."** Verifique o limiar — valores muito baixos fariam tudo
  virar ideal (o app clampa 0 para 45 justamente por isso).
- **"'Sem giro' entra no % ideal?"** Não — fica reportado à parte para não distorcer o percentual.

### Dados e cadastro
- **"Item sem fator de caixa."** A sugestão sai em **unidades** e o campo "Caixas" do pedido
  mostra "—". Normaliza sozinho quando o TI cadastrar o `QTUNIT` no Winthor (§5.8).
- **"O nome do produto aparece como 'PRODUTO 12345'."** Item sem descrição no cadastro de revenda.
  Na aba Validade o nome é buscado pelo lote, então lá costuma sair certo (§7.10).
- **"A lista de compradores está diferente do RH."** É proposital: ela deriva da **base**
  (fornecedor com produto de revenda → `CODCOMPRADOR`), não da folha inteira. Usar `PCEMPR` cru
  traria vendedores e financeiro.
- **"Este painel mostra dado real?"** No endereço `demo.jogasolucoes.com.br`, não — é base
  sintética. As fórmulas e telas são as mesmas.

---

## 13. Armadilhas de dados (resumo — não repita)

1. **Vencidos: join por `NUMTRANSVENDA`, nunca `NUMNOTA`** — infla ~3,5× (§7.11).
2. **Pedido de compra: o preço converte junto com a quantidade** — senão o ERP recebe valor ~50×
   menor (§8.3).
3. **Planilha de importação leva preço LÍQUIDO** — senão o ERP aplica IPI sobre IPI (§8.3).
4. **Nada em caixa sai para o backend** — o campo "Caixas" é view (§8.2).
5. **Valor da sugestão usa o custo arredondado a 4 casas** — o mesmo do documento (§5.9).
6. **Pré-entrada é heurística de 7 dias** — trocar por `PCMOVPREENT` quando publicada (§5.6).
7. **Não usar histórico de `PCITEM[PERIPI]` como fonte primária de IPI** (§6.2).
8. **Crescimento AA: não somar `venda_ano_ant` dos produtos da tela** (§5.21).
9. **Filtro de tela tem de viajar no export** (§10).
10. **ABC-XYZ: as opções do filtro XYZ precisam de `value` explícito** — senão zero produtos em
    silêncio (§7.19).
11. **Cobertura ideal: fronteira inclusiva; limiar 0 é clampado** (§5.18).
11b. **Estoque ideal: a cobertura vem do `cobertura_dias` do produto, nunca de um recálculo** —
    recalcular a partir dos campos arredondados fazia o card divergir da lista em 2 SKUs (§5.18).
11c. **"Produto novo" exige NUNCA TER VENDIDO, não só entrada recente** — a regra literal
    esconderia item morto reposto atrás do card "Novos"; e **`status_parado` não é booleano**:
    use `core.eh_parado` (§5.14).
12. **E-mail: o parâmetro é `comprador_cod`** — `comprador` não é lido por nenhum export (§11).
13. **Ao conferir com relatório do ERP, igualar a filial** (§3).
14. **Coluna nova na aba Fornecedores precisa entrar no front E no back** — senão o export diverge
    da tela (§7.16).
15. **O módulo ancora o "hoje" NO DADO em modo BD** (`_hoje()` → `provider_sql.hoje_analitico`).
    O Comercial sempre fez; o Compras usava `date.today()` puro. Na demo, com o fato terminando
    em 24/07 e o relógio em 18/08, TODA janela caía 25 dias à frente: venda "mês atual" = R$ 0,
    meta do orçamento = 0, `dias_sem_venda` +25, e o catálogo INTEIRO virando curva **C** (é o
    que `_aplicar_curva` faz quando o total da janela é zero). Só o modo `postgres` muda — o
    caminho Power BI segue no calendário. Gate: `tests/test_hoje_ancorado.py`.
16. **Evolução do estoque: guarda-se o INGREDIENTE, não o resultado** — senão a série congela na
    régua do dia e corrigir um critério vira degrau no gráfico (§7.4).
17. **Pesquisa de preço compara com o nosso PREÇO DE VENDA, nunca com o custo** — e sem fator de
    caixa não se chuta comparação (§7.23). A régua de custo enviesava o gap para o verde e ainda
    mandava o nosso custo de aquisição ao fornecedor.
18. **Pesquisa de preço: a cor é pela perspectiva de quem compra** — concorrente mais barato =
    vermelho. O inverso é a leitura intuitiva e está errado para quem compra (§7.23).
19. **XYZ na Validade não é enfeite:** qualifica a confiabilidade do `saldo proj.`/`valor risco`
    que a própria tela calcula com o giro médio (§7.11).
20. **`input[type=number]` DESCARTA a vírgula** do teclado pt-BR. Em campo, a pessoa digita
    "12,50" e o campo fica vazio. Use `type=text` + `inputmode=decimal` (§7.23).
21. **`CUSTOFIN` vive no SNAPSHOT (PCEST), não no cadastro de produto.** Lê-lo do `PCPRODUT`
    devolve 0 em tudo — e o documento que vai ao fornecedor sairia dizendo que pagamos R$ 0,00.

---

*Fim do manual de Gestão de Estoque. Dúvidas comerciais (carteira, vendedores, metas, cobertura de
carteira): `docs/MANUAL_COMERCIAL.md`. Quando o comportamento divergir deste texto, o código
manda.*
