# JOGA Analytics — Comercial + Compras (app fundido)

Sistema **JOGA** (a Multpel é a cliente) que une, num único Flask com um único login, dois módulos:
**Comercial** (dashboards, carteira RFM, vendedores, categorias, metas, cobertura) e **Compras**
(estoque, ruptura, reposição, validade, pedidos, orçamento — foco no comprador). Consome o
**Power BI** (modelo Totvs/Winthor) e alinha centavo-a-centavo com o RCA do ERP.

> ## 🧭 LEIA ISTO PRIMEIRO (orientação de contexto)
>
> - **Este README é a fonte única.** Ele **substitui** os dois READMEs antigos:
>   `Multpel HTML` (Comercial standalone) e `MultpelEstoque` (Compras standalone). O que estiver
>   naqueles dois descreve o mundo **pré-fusão** e pode enganar.
> - **O Compras deixou de ser um app à parte.** Virou o pacote `estoque/` montado como
>   **blueprint em `/estoque`** dentro deste app. Todo aquele "repo separado / servidor separado /
>   senha única / nunca juntar com o Multpel" do README antigo do estoque **está OBSOLETO** — foi
>   exatamente o que a fusão desfez (o cliente adquiriu o Compras).
> - **UMA branch, UMA imagem.** Todo o trabalho vive na **`main`**, que builda **`:latest`** e serve
>   **todas** as instâncias. O que muda entre elas é **env var**, nunca código (`DATA_SOURCE`,
>   `MODULOS`). Branch `feat/**` publica em **`:teste`** — valide lá e mergeie na `main`.
> - **🔀 Multi-fonte:** o app roda de **Power BI OU Postgres** com o mesmo código (`DATA_SOURCE`).
>   É o que permite a **DEMO** (dados sintéticos). Ver a seção **🔀 Multi-fonte**.
> - 🚫 **NÃO** edite o repo `MultpelEstoque/` (congelado).
> - 🗄️ *Histórico:* até 07/2026 existiam as branches `feat/fusao-estoque` (`:teste-fusao`) e
>   `feat/multi-fonte` (`:multi-fonte`). Foram unificadas na `main` — a produção tinha ficado presa
>   numa feature branch e uma correção precisou de cherry-pick entre as duas linhas. As tags velhas
>   seguem no GHCR só para rollback.
> - 📚 **Vai mexer no Compras especificamente?** Leia também **`docs/estoque/planilha_v3.md`** — é
>   onde estão as fórmulas do estoque decodificadas em detalhe (giro, cobertura, sugestão de compra,
>   vencidos, orçamento). Este README traz o resumo + as armadilhas; o `planilha_v3.md` traz o miolo.
>
> ### Estado atual do rollout (temporário — some quando as antigas forem desativadas)
> ### Instâncias no ar — mesma imagem `:latest`, o que muda é env
> | Domínio | Papel | Env que a diferencia | Banco |
> |---|---|---|---|
> | `painel.jogasolucoes.com.br` | **PRODUÇÃO** (Multpel) | *(sem `DATA_SOURCE`)* → Power BI | `painel_db` |
> | `demo.jogasolucoes.com.br` | demo comercial | `DATA_SOURCE=postgres` | `joga_demo` (sintético) |
>
> As stacks antigas (`analytics` / `estoque`, apps pré-fusão) foram **desativadas** em 07/2026.
> A `demo` roda **dados sintéticos** — **não toca o BI do cliente**.

---

## 🧩 Como os dois módulos convivem

- **Um login, duas áreas.** O usuário loga uma vez; conforme o que acessa, cai no Comercial, no
  Compras, ou num **portal** que deixa escolher. Um **seletor de área** fixo no topo troca entre
  eles. A **Administração** é um terceiro lugar neutro (não pertence a nenhuma área).
- **Módulo é o que a EMPRESA comprou; área é o que a PESSOA acessa.** O acesso efetivo é a
  **interseção** dos dois:
  - **`MODULOS`** (env var da stack): `comercial,compras` | `comercial` | `compras`. Uma instância
    por cliente. Se `compras` não estiver aqui, o blueprint `/estoque` **nem é registrado**.
  - **`multpel_users.areas`** (JSONB, ex.: `["comercial","compras"]`): o que cada pessoa acessa.
    **Default `["comercial"]`** — ninguém ganha Compras por acidente; o admin libera pessoa a pessoa.
- **Guardas:** o Comercial (rotas em `server.py`) é protegido por um `before_request` **deny-by-default**;
  o Compras (blueprint) tem sua própria guarda que exige `login` + a área `compras`. Módulo
  desligado → rota devolve **404** (não existe); usuário sem a área → **403**.

### Colunas de acesso em `multpel_users` (todas via `init_db.py`, idempotente)
`areas` (JSONB, default `["comercial"]`) · `area_padrao` (`portal`|`comercial`|`compras`) ·
`codcomprador` (filtro **default** do Compras, não trava) · `relatorios_estoque` (JSONB — quais
relatórios de Compras o usuário recebe por email) · `tema` (`escuro`|`claro`, default `escuro`) ·
`tentativas_falhas`/`bloqueado_ate`/`bloqueios_seguidos` (bloqueio de login).

---

## ✨ Módulo Comercial (features)

- **Dashboard executivo** — KPIs do mês + série 12m + YoY recalculado RCA + Top 10 deptos/vendedores + top clientes. Filtro multi-supervisor.
- **Carteira RFM** — 8 segmentos canônicos + receita/positivação 12m + drill mensal + drill 360° por cliente. Export CSV/PDF.
- **Vendedores** — ranking YoY, positivação, cockpit individual.
- **Categorias** — treemap de deptos (tamanho=venda, cor=margem) + top fornecedores + drill.
- **Mix abandonado** — clientes que pararam de comprar um depto há X dias; drill top 5 deptos perdidos; export CSV.
- **Tendências** — cohort retention heatmap (M+0..M+12) com filtros vendedor/supervisor em cascata.
- **Metas** — réplica das 4 telas META (Venda/Rentab/Clientes/Mix): meta própria (Postgres) × realizado (2º dataset META) × projeção, drill de vendedores, editor admin.
- **Admin** — CRUD usuários, cron de email, multi-CC, segmento RFM, editor de metas. **+ acesso por área, comprador vinculado e relatórios de Compras** (ver abaixo).

## 📦 Módulo Compras (features)

Navegação em 2 níveis: **Visão · Comprar · Pedidos · Estoque · Análise** (19 abas). Foco no comprador.
- **Visão** — Cockpit + Painel gerencial (5 pilares) + Meta de ruptura.
- **Comprar** — Abastecimento (sugestão de compra), Estoque zerado, Plano reposição.
- **Pedidos** — Orçamento (meta × realizado × pedidos), geração de pedido de compra (PDF + planilha Winthor).
- **Estoque** — Cobertura, Parado, Validade (FEFO), Vencidos, Ruptura por comprador, Ocupação.
- **Análise** — Desempenho comercial, Compras × Vendas, Fornecedores, ABC-XYZ, Produtos, Qualidade da base.

**Aba Fornecedores — ciclo de compras + lucro com verba** (07/2026). Quatro colunas novas:
`Compras` (quantas vezes compramos no período), `Ciclo 12m` (de quanto em quanto tempo),
`Lucro bruto` e `Lucro c/ verba`. Cruzar **Ciclo × Lead time** é o que a aba passa a responder:
ciclo menor que o lead = pedido novo antes do anterior chegar.
- ⚠️ **Janelas diferentes de propósito.** `Compras`/`Venda`/`Lucro`/`Verba` seguem o seletor
  **Venda** do topo (é o que permite somar lucro + verba na mesma régua — somar lucro de 1 mês com
  verba de 12 meses é o erro fácil e caro aqui). O **Ciclo** é sempre **12m**: é comportamento do
  fornecedor, e em janela curta quase todo fornecedor teria 1 pedido só.
- ⚠️ **Ciclo conta DATAS distintas, não pedidos** — o mesmo fornecedor recebe vários NUMPED no
  mesmo dia (um por filial/condição) e contá-los criaria intervalos de 0 dia. `Compras` conta
  pedidos porque a pergunta é outra. <2 compras em 12m → ciclo `—` (nunca 0).
- **Verba = NEGOCIADA** (`PCVERBA.VALOR` por emissão), não aplicada: aplicado é evento de
  caixa/acerto e descolaria da competência do lucro. Hoje entra **toda** verba, inclusive
  `200013 Premiações e campanhas` (decisão do diretor: refinar depois) — mas a parcela de campanha
  viaja separada (`verba_campanha`) e a tela **avisa o valor**; o opt-out é trocar
  `core.CONTAS_VERBA_CAMPANHA` de rótulo para filtro.
- ⚠️ **`Lucro bruto` NÃO se recalcula como `venda × margem`** (foi como o pedido veio escrito).
  `margem` é `lucro ÷ venda` arredondada a 1 casa — o caminho de volta reintroduz erro e faria a
  aba divergir do Comercial, que bate centavo-a-centavo com o RCA. O `lucro` já era agregado e só
  não era exibido.
- ⚠️ **Custo zero de query, mas fora do `/api/snapshot`.** Reusa os caches do Lead time
  (`_leadtime_raw`) e das Verbas (`_verbas_raw`). Fica no **`/api/fornecedores_extra`**, buscado só
  quando a aba abre — pendurar no snapshot (que TODA tela carrega) faria a tela inicial pagar por
  duas abas que a maioria não abre. Gate: `tests/test_fornecedores_ciclo_verba.py`.
- ⚠️ **A aba Fornecedores é calculada DUAS vezes** — no front (`renderFornecedores`, para os
  filtros responderem sem round-trip) e no back (`core.fornecedores`, para o export). Coluna nova
  entra nos dois, senão o Excel/PDF diverge da tela.
- ⚠️ **Enquanto o `/api/fornecedores_extra` não chega, o crescimento sai `—`, NUNCA o cálculo
  antigo.** A 1ª versão caía no somatório local como fallback — ou seja, mostrava o número
  **bugado** (ano anterior truncado) durante a carga, e **para sempre** se o endpoint falhasse,
  sem avisar. Número errado que parece plausível é pior que célula vazia, ainda mais um que chega a
  inverter o sinal. Hoje são 3 estados explícitos: carregando → `—` + aviso; falha → `—` + aviso em
  vermelho; pronto → valor correto.
- 🩹 **Crescimento (YoY): NÃO somar `venda_ano_ant` dos produtos da tela.** Bug achado pelo diretor
  em 07/2026 e corrigido em `core.yoy_fornecedor`. A tela só tem o que está no **snapshot de
  estoque ATUAL**: o numerador saía completo (o que vende hoje está no catálogo hoje) e o
  denominador perdia **todo item que saiu de linha nos últimos 12 meses** — universos diferentes
  nos dois lados da divisão. Medido no BI real: **18 fornecedores erravam >10 p.p. e 6 trocavam de
  SINAL** (o app dizia +21,9% num fornecedor que caíra 72,8%). Agora as duas janelas são somadas
  **completas**, sobre todos os produtos vendidos. Consequência aceita: a coluna passa a ser do
  fornecedor INTEIRO e não responde aos filtros de recorte — a tela avisa quando há filtro ativo
  (mesma política do card de Orçamento). Produto sem cadastro de revenda fica fora dos dois lados
  (0,49% da venda do ano anterior) — é o teto de precisão do método.
  **Validado contra a rotina 111 do ERP** ("Resumo de Faturamento por Fornecedor"): HIPERROLL
  17,63% × 17,63% (exato, bruta e devolução no centavo), Ind. Papéis −3,38% × −3,35%,
  GALVANOTEK 7,44% × 7,26%, BOMBRIL 3,31% × 2,35% (a sobra é ST/IPI — o 111 desconsidera ST).
  ⚠️ Ao conferir com o 111, **igualar a filial**: o relatório sai com todas e a tela usa a unidade.
  Gate: `tests/test_fornecedores_ciclo_verba.py` (caso da PEGON travado nos números reais).

**Meta de ruptura — uma meta por curva** (07/2026). Era A (2%) × B+C (5%); virou **A / B / C**
(2% / 5% / 10%), editáveis em ⚙ Parâmetros. ⚠️ Separar **afrouxa o placar sem ninguém mexer na
operação**: os itens C que estouravam o teto do bloco passam a ter orçamento próprio. Comparar
antes×depois uma vez, senão parece ganho operacional.

### Metodologia de dados do Compras (v3) — o essencial

Consome no dataset **Estoque**: **PCPEDIDO/PCITEM** (pedido real), **PCEMBALAGEM** (caixa/cubagem),
**PCEMPR** (comprador). Vencidos usa +4: **PCLANC/PCCONTA** (conta 200042) e **PCNFSAID/PCMOV**.
Doc completa das fórmulas em **`docs/estoque/planilha_v3.md`**.

- **Estoque (Disponível/QTDISP)** = gerencial líquido `QTESTGER − avaria(QTBLOQUEADA) − reserva(QTRESERV)`, filiais 3+5. Endereçado (`PCESTENDERECO`, RUA≠99) só na validade/FEFO.
- **Última entrada × última saída** (`PCEST[DTULTENT]`/`[DTULTSAIDA]`) viajam no produto e aparecem
  no drawer 360°. Pedido do diretor 07/2026: só com a saída dá pra ver que o item não gira, mas não
  se é **estoque velho parado** ou **compra recente errada** — a entrada separa os dois. Ex. real:
  cód 69174, entrada há 243d, saída há 15d, cobertura 753d → estoque velho, não compra ruim.
- **Giro** = média 3 meses (`QTVENDMES1..3`/3), toggle p/ forecast (RCA). **Custo** = `CUSTOFIN`. **Comprador** = `PCFORNEC.CODCOMPRADOR → PCEMPR.NOME`.
- **Sugestão de compra** desconta o **pedido REAL em aberto** (PCPEDIDO/PCITEM, últimos 180d) e sai **em caixas** (`QTUNIT`/PCEMBALAGEM); sem fator de caixa → em unidades (pendências em `estoque/itens_sem_fator_caixa.csv`).
- **Orçamento** = meta `65% da venda líq. 30d` por comprador × realizado do Winthor. **Transferência entre filiais NÃO é compra**: pedido cujo fornecedor tem a **mesma raiz de CNPJ** (8 díg., contra `MULTPEL_EMPRESA`) fica fora do orçamento.
- **Duas réguas de valor (IPI/ST).** O Orçamento mede o realizado por `PCPEDIDO[VLTOTAL]`, que é a
  **NF cheia** (mercadoria + IPI + ST). Então a sugestão de compra sai nas duas: `valor_sugerido_liq`
  (mercadoria — é ela que vira **preço na planilha do Winthor**) e **`valor_sugerido_nf`** (o que
  consome a meta). As alíquotas saem da **tributação de ENTRADA do ERP** (`TRIB_ENTRADA`, rotina
  212) por `produto × filial × UF de origem × tipo de fornecedor`, com cascata
  `isento_cadastro → trib_entrada → cadastro → histórico → 0`. A **fonte** e a **confiança**
  (`trib_firme`) viajam até a tela. Detalhe em **🧾 Tributação do pedido**.
- **Comprador vinculado** ao usuário no Admin é **filtro inicial**, não trava — ele pode ver os outros.
- **Lista de compradores** ≠ folha inteira: deriva da base (`compradores_reais()` — fornecedor com produto de revenda → `CODCOMPRADOR`). Usar `PCEMPR` cru traz vendedores/financeiro.

**Armadilhas de dados (landmines — não repita):**
- ⚠️ **Vencidos: join por `NUMTRANSVENDA`, NUNCA por `NUMNOTA`.** `NUMNOTA` repete ao longo dos anos e infla o resultado ~3,5× (o `SELECT DISTINCT` **não** corrige).
- ⚠️ **ABC-XYZ: as `<option>` do `#f-xyz` precisam de `value` explícito (`X`/`Y`/`Z`).** Sem ele o filtro casa nada e devolve **zero produtos em silêncio**.
- ⚠️ **Pedido de compra: o preço converte JUNTO com a quantidade** (o Winthor faz `B×C` literal). Converter só a qtd colocaria o pedido no ERP com valor ~50× menor. Fonte única `core.item_master` (PDF + planilha).
  Por isso o campo **"Caixas"** do modal de pedido (07/2026, pedido do diretor — o comprador raciocina
  em caixa) é só uma **view**: ele escreve `qtd = caixas × QTUNITCX` e **a unidade continua a única
  fonte de verdade** no modelo e no payload. **Nada em caixa sai para o backend** — se sair, cai
  exatamente nesta armadilha. Item sem fator de caixa mostra "—" e só aceita unidade.
- ⚠️ **Cobertura ideal:** fronteira **inclusiva** (`core.resumo_estoque_ideal`) — o item que pousa
  exatamente no limiar já é "ideal". Desde 07/2026 o limiar e a meta são **parâmetros**
  (`ideal_dias`=45 / `ideal_meta_pct`=90, ⚙ Parâmetros → "Estoque ideal") para o diretor calibrar
  antes de fixar o número. **Continuam desligados do "Cobertura alvo"** de propósito: um é o alvo de
  COMPRA, o outro é só a RÉGUA DE MEDIÇÃO do Painel gerencial — mexer num não mexe no outro.
  Clamp da querystring em `core.regua_estoque_ideal` (limiar 0 faria tudo virar "ideal" em silêncio).
  Gate: `tests/test_estoque_ideal.py` (11 testes, incluindo "sem params sai igual ao de antes").
  ⚠️ Os parâmetros são **por navegador** (`localStorage`): enquanto o valor não for fechado, o
  painel pode significar coisas diferentes para cada pessoa. Ao definir, promover a default do servidor.
- ⚠️ **A planilha de importação leva o preço LÍQUIDO — nunca com IPI.** O Winthor calcula o imposto
  sozinho na importação (foi assim que 132,05/caixa virou NF de R$ 44.982,01). Mandar preço com
  imposto faria o ERP aplicar **IPI sobre IPI**: pedido ~15% inflado e custo de entrada errado.
  Gate: `test_planilha_winthor_leva_preco_LIQUIDO_sem_imposto`.
- ⚠️ **Valor da sugestão usa o custo arredondado a 4 casas** (`_round(custofin, 4)`), o mesmo que
  vira preço no documento. Somar o `custofin` cru fazia a tela divergir do PDF em centavos
  (R$ 39.536,38 × R$ 39.536,28 num pedido de 49 itens) — e quem bate com o ERP é o PDF.

### 🧾 Tributação do pedido (IPI/ST) — por que existe e onde NÃO mexer

**O problema (07/2026, achado pelo diretor):** o comprador olhava a sugestão (R$ 39.536,38 no card
da GALVANOTEK), gerava o pedido, e o Winthor registrava **R$ 44.982,01**. Não era erro de conta —
o JOGA mostrava **mercadoria** e o ERP mostra a **NF**. Como o Orçamento lê `VLTOTAL` (NF cheia),
ele planejava numa régua e consumia a meta em outra.

**A fonte é a tributação de ENTRADA do próprio ERP** (rotina 212), publicada no dataset como
**`TRIB_ENTRADA`** (join `PCTRIBENTPROD × PCTRIBFIGURA`, filiais 3/5 + revenda, ~36k linhas).
Chave: **produto × filial × UF de origem × tipo de fornecedor** → figura → `PERIPI`/`PERCST`.

| # | Degrau da cascata | Cobertura | Acerto |
|---|---|---|---|
| 1 | `isento_cadastro` — `PCPRODUT[PERCIPI]` = 0 | 41% | 97% |
| 2 | **`trib_entrada`** — a figura do ERP | **54%** | **100%** |
| 3 | `cadastro` — item sem figura p/ aquela UF | 5% | 20% ⚠️ |
| 4 | `pedido_real` / `perfil_fornecedor` — histórico | resto | — |
| 5 | `sem_dado` → zero (nunca inventa imposto) | — | — |

**Total: 94,6% das linhas / 96,9% do valor** (medido nos pedidos reais pós-virada; era **53%**
com o histórico como primária). Os degraus 3-5 saem com **`trib_firme=False`** → a tela marca
com `≈` e o **% fica editável no pedido**: é ali que mora todo o erro residual.

- ⚠️ **`PCPRODUT[PERCIPI]` é o IPI de VENDA** (rotina 271), não o de compra — por isso ele diverge
  (dizia 6,75% num item que o ERP cobrou 10%). Serve só para os dois papéis em que é bom: dizer
  quem é **isento** (alíquota 0) e cobrir item sem figura.
- ⚠️ **Não volte a usar o histórico de `PCITEM[PERIPI]` como primária.** Ele parecia bom em janela
  de 30d (82%), mas isso mede o passado: quando o **redutor de 35% do IPI caiu (21/07/2026** —
  `9,75 = 15 × 0,65`), o histórico seguiu prevendo a alíquota velha por semanas e o acerto para o
  próximo pedido caiu a **53%**. Cadastro fiscal muda **antes** do histórico; é por isso que a
  figura ganha. Reprovadas também: `PCEST[PERCIPIULTENT]` (66%), pedido anterior (55%),
  `PCTRIBUTNCM`/`PCIMPORTTRIBUT`/`PCTABTRIBENT`/`PCEXCECAOIPI` (**vazias**), `PCTRIBUTCOMPRA`
  (não existe nesta base), `PCFIGURATRIBIPI` (só CST), `PCTRIBUT`/`PCNCM` (só ICMS).
- **ST sai como fator efetivo sobre a mercadoria** (`VLST ÷ preço`), não como `PERCST`: no
  fornecedor 113 o efetivo é **20,71%** contra `PERCST` 20,05% — a diferença é a majoração da base
  (MVA), que o fator já embute. Evita reconstruir MVA/base reduzida/crédito de ICMS.
- `PCITEM[VLIPI]`/`[VLST]` são **UNITÁRIOS** e `PCITEM[PTABELA]` vem **vazio** nesta base — por isso
  o preço se deriva de `VLIPI ÷ (PERIPI/100)`. Validado no 211 do pedido 565684:
  `Σ QTPEDIDA×VLIPI = 5.445,73`, o IPI impresso.
- **Custo zero de query:** as colunas entraram na `q_pedido_itens` que o já-pedido **já carregava**.
- **No fallback histórico, a alíquota do par é a MODA** da janela (não a do último pedido): ela
  oscila entre pedidos da mesma semana. Medido em 1.487 linhas: moda 86,4% × 85,7%. Empate → o maior.
- **`TRIB_ENTRADA` é publicada sob demanda** (como a `PEDIDO_ENTRADA`). Instância sem ela degrada
  para cadastro/histórico — tudo marcado como estimativa. Para publicar, ver `q_trib_entrada()`.
- **Backtest temporal** (mapa até D-60 prevendo 301 pedidos que ele nunca viu): desvio agregado
  **0,02%**, erro mediano por pedido **0,00%**, p90 2,32%, 96% dos pedidos < 5%. A régua antiga
  errava **8,05%**.

### O que a FUSÃO mudou no Compras (vs. o README antigo standalone)
- **Sem login próprio.** A senha única `ESTOQUE_SENHA` foi **removida** — usa a autenticação/sessão/RBAC do app principal.
- **Sem repo/servidor/banco separados.** Virou o pacote `estoque/` (blueprint `/estoque`); `store.py` aponta pro banco do app; `store.init()` saiu do import e foi pro `init_db.py`.
- **Rotas prefixadas** com `/estoque` (`/estoque/api/...`, `/estoque/static/...`) — resolve as colisões de `/`, `/health`, `/login`, `/static`.
- **Ganhou email** (não tinha): 16 relatórios, escolhidos por usuário no Admin (ver Fase Email).
- **Ainda "Multpel" de propósito** (dado da cliente, não marca): `MULTPEL_EMPRESA` (emitente do pedido), `logo-multpel-trofeu.png` (logo do comprador no PDF), `NOMES_FILIAL`.

---

## 🔀 Multi-fonte (produtização) — o app roda de Power BI **OU** Postgres

O app foi **produtizado** pra atender vários clientes Winthor com o **mesmo código, sem forkar**. Dois
eixos de config por instância (env vars), **não** dois caminhos de código:

| Env | Valores | O que faz |
|---|---|---|
| **`DATA_SOURCE`** | `powerbi` (default) \| `postgres` | de onde vêm os dados analíticos |
| **`MEDIDAS`** | `cliente` (default) \| `joga` | usa as medidas do BI do cliente ou a reconstrução própria |

- **`powerbi`+`cliente` (default) = a Multpel de hoje.** Sai **byte a byte igual** — validado
  **centavo-a-centavo** contra o BI real (código antes×depois idêntico nos KPIs do Dashboard). Todo
  caminho novo fica atrás de `if CONFIG['data_source']=='postgres'` e **nunca roda no default**.
- **`postgres` = lê de um Postgres analítico.** A **demo** aponta pro `joga_demo` (sintético); um
  cliente "só banco" apontaria pro Winthor dele. Providers em **`provider_sql.py`** (Comercial) e
  **`estoque/provider_sql.py`** (Compras) devolvem as **MESMAS formas** que o caminho DAX → a
  matemática pura (`rfm.py`/`cohort.py`/`metas.py`/`estoque/core.py`) roda **intacta**.
- **`joga`** (eixo independente): quando o BI do cliente **não tem** as medidas RCA, o `medidas_dax.py`
  as reconstrói (post-processor no `execute_dax`).

**🛡️ Rede de segurança (NÃO REMOVA):** em modo `postgres`, `server.execute_dax` e
`estoque.pbi._execute` **levantam RuntimeError**. Qualquer endpoint ainda não branchado falha alto e
**degrada pra vazio** (via try/except) em vez de **vazar dado REAL do cliente** numa demo. Foi assim
que se enumerou (via sweep HTTP) 10 endpoints que faltavam — todos corrigidos.

**Env do modo Postgres:**
```env
DATA_SOURCE=postgres
ANALYTICS_DB_NAME=joga_demo   # banco analítico (ANALYTICS_DB_* faz fallback pra DB_*)
# ANALYTICS_HOJE=2026-07-24   # opcional: fixa o "hoje"; senão ancora no max(dtsaida)
```
> **Ancoragem de data:** em modo BD o "hoje" é o **`max(dtsaida)`** do banco (ou `ANALYTICS_HOJE`) — a
> demo **não envelhece** sem regenerar. O default powerbi usa `TODAY()` normal.

**Gates:** `tests/test_provider_*.py` (Dashboard, Comercial, Metas, Mix, Radar, Estoque, RBAC) +
`test_medida_compat.py`. Baseline **272 passam / 3 falham** (as 3 conhecidas de fixture de data).

---

## 🛠 Stack

| Camada | Tecnologia |
|---|---|
| Backend | Flask 3.0 + Waitress (WSGI prod) |
| Frontend | HTML + Vanilla JS + Chart.js + CSS (tema claro/escuro por CSS vars) |
| Cache | Redis 7-alpine |
| Database | PostgreSQL (auth + log + metas + orçamento/pedidos/planos do estoque) |
| Fonte de dados | Power BI (executeQueries + DAX: RCA + META + Estoque) **OU** Postgres analítico (`joga_demo`) — ver 🔀 Multi-fonte |
| Auth Power BI | Service Principal Azure AD |
| Email | Resend API · Cron: APScheduler in-process |
| Deploy | Docker Swarm + Traefik (TLS Let's Encrypt) · GHCR · GitHub Actions |

---

## 📁 Estrutura (o que a fusão acrescentou está marcado 🆕)

```
Multpel HTML/                       ← repo multpelhtlm (branch feat/fusao-estoque)
├── server.py                       # Backend Comercial + registro do blueprint + auth/acesso/tema/segurança (~7,8k linhas)
├── rfm.py · cohort.py · metas.py   # Módulos puros do Comercial (matemática)
├── cobertura.py                    # Motor de cobertura (Gerencial)
├── init_db.py                      # Migrations Postgres (idempotente) — inclui as tabelas estoque_* 🆕
├── estoque/                        # 🆕 MÓDULO COMPRAS (ex-MultpelEstoque), blueprint /estoque
│   ├── __init__.py                 #   exporta bp
│   ├── routes.py                   #   ex-app.py (sem Flask()/CORS/login próprio)
│   ├── core.py queries.py pbi.py store.py   # regra/DAX/PBI/Postgres (quase intactos)
│   ├── relatorios.py               #   🆕 catálogo único dos relatórios (Admin + email)
│   ├── emails.py                   #   🆕 gera anexos PDF+XLSX p/ o email de Compras
│   └── index.html                  #   SPA do Compras (19 abas)
├── portal.html                     # 🆕 tela de escolha de área (+ Administração como faixa)
├── index/carteira/vendedores/…html # Páginas do Comercial
├── admin.html                      # CRUD + acesso por área + comprador + relatórios de Compras
├── login.html · trocar-senha.html
├── static/
│   ├── tema.css                    # 🆕 paleta única (escuro + [data-tema="claro"])
│   ├── joga-header.js              # 🆕 cabeçalho único: menu, seletor de área, toggle de tema
│   ├── chart-tema.js               # 🆕 Chart.js lê a cor do tema (carrega DEPOIS do tema.css!)
│   ├── joga-mark.svg / -claro.svg  # 🆕 marca troca com o tema (hexágono creme ↔ preto)
│   ├── joga-loader.svg / -claro.svg
│   ├── drill-cliente.* · fetch-resiliente.js · tooltips.*
│   └── estoque/ (estoque.css, estoque.js, logos)   # assets do Compras
├── docker-compose.prod.yml         # produção (Comercial antigo)
├── docker-compose.teste.yml        # 🆕 stack de validação (painel)
├── docs/DEPLOY_TESTE.md            # 🆕 runbook do painel + migração de dados
├── docs/estoque/                   # 🆕 metodologia do Compras (planilha_v3.md etc.)
├── provider_sql.py                 # 🆕 modo DATA_SOURCE=postgres do Comercial (lê do joga_demo)
├── medidas_dax.py                  # 🆕 reconstrução das medidas RCA (MEDIDAS=joga)
├── estoque/provider_sql.py         # 🆕 modo postgres do Compras
├── docker-compose.demo.yml         # 🆕 stack da instância DEMO (Portainer)
├── _seed_demo/                     # 🆕 base sintética reprodutível (joga_demo) + bootstrap + seeder
└── tests/                          # pytest (272 passam; 3 falham por fixture de data — não é regressão)
```

---

## 🔧 Setup local (dev)

```bash
cp .env.example .env        # preencher (ver variáveis abaixo)
docker compose -f docker-compose.dev.yml up -d redis
python -X utf8 init_db.py   # cria/migra schema + admin default (admin@multpel.com.br / admin123)
python -X utf8 server.py    # http://localhost:5000
pytest -q                   # 272 passam, 3 falham (fixture de data — não é regressão)
```

Variáveis novas da fusão no `.env` (além das do Power BI/DB/Redis/Resend):
```env
SECRET_KEY=<hex>            # OBRIGATÓRIA se FLASK_ENV=production (o app NÃO sobe sem ela)
MODULOS=comercial,compras   # o que esta instância serve
POWERBI_DATASET_ID_ESTOQUE=32fb60e1-...   # dataset do Compras (default embutido)
POWERBI_DATASET_ID_RCA=f2fbf288-...        # RCA (mesmo do Comercial)
CLIENTE_LOGO=               # opcional: logo do cliente no PDF de pedido (fallback = JOGA)
```

> **Rodar em modo DEMO/Postgres localmente** (sem BI): monte o `joga_demo` (`_seed_demo/setup_db.py` →
> `gerar*.py`), suba com `DATA_SOURCE=postgres ANALYTICS_DB_NAME=joga_demo`. Passo a passo completo
> (auth de demo, seeder, checklist de fumaça no navegador) em **`_seed_demo/FUMACA_DEMO.md`**.

---

## 🚀 Deploy

Fluxo: `git push` na branch → GitHub Action builda e publica no GHCR → redeploy no servidor.
**A `main` publica em `:latest`**, que é a imagem de TODAS as instâncias (produção e demo).

```bash
# 1) redeploy da stack de validação (painel)
docker service update \
  --image ghcr.io/jogasolucoesempresarias-debug/multpelhtlm:latest \
  --with-registry-auth --force painel-teste_painel-app

# 2) migration — OBRIGATÓRIA (o login quebra sem as colunas novas, ex.: tema)
docker exec $(docker ps -q -f name=painel-teste_painel-app) python -X utf8 init_db.py

# 3) conferir
curl -s https://painel.jogasolucoes.com.br/health   # informa os módulos ativos
```

Runbook completo (criar a stack, popular com dados reais, migrar o `estoque_db`) em
**`docs/DEPLOY_TESTE.md`**. Quando for promover à produção de vez, é um **cutover**: congelar o
uso, dump final dos dados, apontar os usuários — não é só "copiar uma vez".

### Instância DEMO (`demo.jogasolucoes.com.br`) — sintética, auto-contida

A **mesma imagem**, subida como outra stack só com env diferente (`DATA_SOURCE=postgres`). Pra
apresentações, sem depender do BI do cliente. **Não precisa de git/imagem separados** — um código,
muitas instâncias; as env vars são o interruptor.

- **`docker-compose.demo.yml`** (cola no Portainer, stack nome livre ex. `demo`): traz o **próprio
  Postgres + Redis + volume** e um serviço **`demo-seed`** que, no **1º deploy, monta a base sintética
  sozinho** (`_seed_demo/bootstrap_demo.sh`: schema → gera ~1,17M linhas → `init_db` → metas → libera
  admin → limpa o cache). **Idempotente** (redeploys pulam; ~3–4 min só no 1º boot).
- **Imagem `:latest`** — a mesma da produção. A demo não tem imagem própria: o que a torna demo é o `DATA_SOURCE=postgres`.
- **SEM `POWERBI_*` de propósito** (a demo não tem como tocar o BI do cliente). `CRON_HABILITADO=false`
  (sem emails). `SECRET_KEY` e senha do Postgres **exclusivas** da demo. **1 banco** `joga_demo` serve
  analytics **e** auth (as tabelas não colidem).
- **Login:** `admin@multpel.com.br / admin123`. Acompanhar os logs do serviço `demo-seed` até
  **`[bootstrap] DEMO PRONTA`** (durante o seed, o login falha — é o passo `init_db` que cria as tabelas de auth).
- Base reprodutível (SEED=42) em `_seed_demo/`; runbook local (fumaça no navegador) em
  **`_seed_demo/FUMACA_DEMO.md`**; seeder de metas com **trava** (`DEMO_SEED=1` + recusa `multpel_db`).

⚠️ **Deploy antes do build terminar** → serviços em erro de *pull* de `:latest`. Espere o
GitHub Actions ficar verde e dê **Update/Re-pull** na stack.

---

## 🎨 Tema (claro/escuro)

- Toda a cor vive em **`static/tema.css`** (`:root` = escuro; `:root[data-tema="claro"]` = claro,
  validado em contraste WCAG AA). As páginas apontam pras variáveis; nenhuma cor cravada de CSS.
- Fundos "vidro"/tints usam **triplas RGB** (`rgba(var(--accent-rgb), .15)`) pra o alpha ser
  preservado entre temas. Texto sobre o accent usa **`--sobre-accent`** (não `--bg`).
- **Botão ☀️/🌙** no cabeçalho (`joga-header.js`). **Padrão escuro** (opt-in). Persiste em
  `localStorage` (aplica sem piscar, via script inline no `<head>`) **e** no banco (coluna `tema`,
  segue a pessoa entre máquinas; no load, o banco vence).
- **Gráficos:** `chart-tema.js` alimenta `Chart.defaults` da cor do tema **e** repinta gráficos já
  renderizados na troca ao vivo. ⚠️ **Ele TEM de carregar depois do `tema.css` e do anti-piscada** —
  senão lê o fallback escuro e a grade sai escura no claro (foi um bug real).

---

## 🔒 Segurança do login

- **Bloqueio progressivo por conta** (Postgres: `tentativas_falhas`/`bloqueado_ate`/`bloqueios_seguidos`):
  5 erros → 15min, escalona (1h, 4h), zera no acerto. **+ limite por IP** (Redis, fail-open). Botão
  "desbloquear" no Admin. Limiares em `multpel_config`.
- **`SECRET_KEY` obrigatória em produção** — sem ela o app **aborta o boot** (antes subia com chave
  pública e cookie de admin forjável).
- Cookie `HttpOnly`+`SameSite=Lax`+expiração; `Secure` só em produção. Enumeração por tempo mitigada.
  Rastro de login com IP em `multpel_log`.

---

## 📧 Email de Compras

- **16 relatórios** (todos com PDF+XLSX já prontos via `_export_data`/`_gerar_pdf`). Catálogo único
  em **`estoque/relatorios.py`** (Admin e cron leem do mesmo lugar).
- Admin marca por usuário quais recebe; reusa horário/frequência do cron. ⚠️ O recorte por comprador
  vai na **query string** do contexto simulado (`estoque/emails.py`) — o estoque lê filtro de
  `request.args`, não da sessão (diferente do Comercial).

---

## 🔒 RBAC do Comercial (inalterado pela fusão)

Duas réguas: por **VENDA** (`aplicar_rbac_dax()` injeta `CODUSUR`/`CODSUPERVISOR IN {...}` no DAX —
Dashboard/Vendedores/agregados) e por **CADASTRO** (`_carteira_no_escopo()` recorta em Python por
`PCCLIENT.CODUSUR1` — Carteira/Categorias/Mix/Tendências, com números totais). Metas rodam no dataset
META por `CODUSUR` (`_metas_escopo_codusur()`). Supervisor pode ter várias áreas (`codsupervisores`).

## 📐 Alinhamento RCA
```
Receita Líquida = VENDA BRUTA(DTSAIDA) − TOTAL DEVOLUCAO(DTENT) − TOTAL DEVOLUCAO AVULSA(DTENT)
Lucro Total     = Receita Líquida − (CUSTO TOTAL − CUSTO DEVOLUCAO − CUSTO DEVOLUCAO AVULSA)
```
Devolução por **DTENT** (dia que entrou no estoque). Validado: Sup AFONSO ES-SUL Abr/26 bate centavo.

---

## 🗂 Endpoints (o que a fusão acrescentou)

- **Acesso/tema:** `GET /api/me` (devolve `areas`, `modulos`, `tema`, `codcomprador`) ·
  `PUT /api/me/area-padrao` · `PUT /api/me/tema` · `GET /portal`
- **Compras (blueprint):** tudo sob `/estoque/...` — `/estoque/`, `/estoque/api/snapshot`,
  `/estoque/api/filtros`, `/estoque/api/orcamento`, `/estoque/api/export/<view>.{csv,xlsx,pdf}`,
  `/estoque/api/pedidos`, `/estoque/api/fornecedores_extra` (ciclo + verba, lazy), etc.
- **Admin:** `POST /api/admin/users/<id>/desbloquear` · `GET /api/_internal/compradores-map` ·
  `GET /api/_internal/relatorios-estoque` · `POST /api/admin/enviar-relatorio/<id>?tipo=compras`
- (Os endpoints do Comercial — dashboard, carteira, vendedores, categorias, mix, tendências, metas —
  seguem iguais; ver seções acima.)

---

## ⚠️ Armadilhas para quem for editar (leia antes de mexer)

1. **`init_db.py` é OBRIGATÓRIO** após qualquer deploy que mexa no schema — o `SELECT` do login inclui
   as colunas novas; sem a migration, o login quebra. É idempotente (`ADD COLUMN IF NOT EXISTS`).
2. **`SECRET_KEY` em produção** — sem ela o app não inicia (proposital). 1º lugar a checar se o
   serviço fica reiniciando.
3. **`chart-tema.js` depois do `tema.css`** no `<head>` (senão gráficos com grade escura no claro).
4. **URLs do `estoque.js` são absolutas com `/estoque/...`** — nunca `/api/...` (cairia no Comercial → 404).
5. **Checagem de API usa `'/api/' in path`, não `startswith`** — por causa de `/estoque/api/...`.
6. **Não editar `MultpelEstoque/`** (repo congelado) nem publicar em `:latest` sem intenção.
7. **3 testes falham por fixture de data** (radar/mix/cohort) — pré-existentes, **não** são regressão.
   O baseline é **272 passam / 3 falham**.
8. **Verificação visual de tema não confia em captura** das telas de dados (Power BI muda o conteúdo
   entre capturas) — comparar cor computada (`getComputedStyle`), não pixels.
9. **Base nova ganha `areas=["comercial"]` por default** — libere `compras` no Admin (ou via UPDATE),
   pessoa a pessoa.

### Cuidados da multi-fonte (produtização)
10. **Toda mudança nova fica atrás de `if CONFIG['data_source']=='postgres'`** — **nunca** altere o
    caminho DAX no default. A Multpel (`powerbi+cliente`) tem que sair **idêntica**; a prova é o
    baseline de pytest (roda o caminho powerbi mockado) + a amostra **centavo-a-centavo** no BI real.
11. **A rede de segurança (`execute_dax`/`_execute` levantam em postgres) NÃO se remove** — é o que
    impede **vazar dado real** por um endpoint esquecido. E **"Fase X completa" só depois de um SWEEP
    HTTP de TODOS os endpoints em modo BD** (não só das telas principais): foi assim que se achou 10
    endpoints comerciais sem branch. Ver gate `test_comercial_endpoints_sweep_modo_postgres`.
12. **Segredo: NUNCA commitar `.env` nem colar o `POWERBI_CLIENT_SECRET`.** Se exposto → rotacionar no
    Azure AD. `.dockerignore` já exclui `.env` da imagem. **A demo NÃO leva credencial Power BI** (de propósito).
13. **`seed_metas_demo.py` escreve no auth DB** — trava: exige `DEMO_SEED=1` e **recusa `multpel_db`**
    (produção). Não semear meta sintética na produção por acidente.
14. **`.sh` sempre em LF** (`.gitattributes`) — bash no Linux quebra com CRLF (quebraria o `bootstrap_demo.sh` da demo).
15. **Depois de patchar dado da demo ao vivo, `_R.flushall()`** — o Redis cacheia entre restarts (ex.:
    ranking com `tipovend` antigo). O `bootstrap_demo.sh` já faz isso ao fim do seed.
16. **Risco de contrato em Compras é loader→core**, não frontend: o SQL do provider tem que devolver
    as MESMAS chaves que o `core.py` lê (as que o `clean_rows` encurta: `PCEST[QTBLOQUEADA]→qtbloq`,
    `QTVENDMES1→giro_m1`). Chave errada → `core` lê `None` e a tela **zera em silêncio**.

---

## 📜 Histórico

Pré-fusão (Comercial): Ondas A–N (auth, RFM, dashboards, drill, RCA alignment, supervisor multi-área,
módulo Metas). Detalhe em `_PROGRESSO.md` (não versionado) e no histórico git.

**Fusão Comercial + Compras** (branch `feat/fusao-estoque`): blueprint `/estoque`; modelo de acesso
por área; portal + seletor + cabeçalho único; Admin com acesso/comprador/relatórios; email de Compras
(16 relatórios); segurança do login (bloqueio + SECRET_KEY + cookie); modularidade (`MODULOS`); tema
claro/escuro (paleta única, WCAG, toggle+persistência, marca que troca). Verificado por navegador
(Playwright) + `pytest`.

**Produtização multi-fonte** (branch `feat/multi-fonte`): app roda de Power BI **ou** Postgres com o
mesmo código (`DATA_SOURCE`/`MEDIDAS`), providers `provider_sql.py`/`estoque/provider_sql.py`
espelhando o DAX (Comercial + Compras + drills + exports), reconstrução das medidas RCA
(`medidas_dax.py`), rede de segurança contra vazamento, base sintética `joga_demo` + stack DEMO
auto-contida. Zero regressão na Multpel **provada centavo-a-centavo** no BI real (antes×depois idêntico);
2 sweeps HTTP (100% dos endpoints branchados); baseline 242 testes.

---

## 📄 Licença / Suporte
Privado. Uso interno Multpel + parceiros JOGA autorizados.
Logs: `docker service logs painel-teste_painel-app --tail 200` · Health: `/health` (lista módulos).
