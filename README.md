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
  - ⚠️ **A `% Margem` divide pelo realizado BRUTO** (com bonificação), nunca por `venda_sb`
    (`[Realizado Sem Bonus]`). É a régua da medida oficial `[MARGEM(%)]` do dataset META
    (`[LUCRO TOTAL] ÷ [VENDA TOTAL]`, com `[VENDA TOTAL]` ≡ `[Tem Pedido]`) — conferida ao vivo nos
    9 supervisores, batendo na 4ª casa. Dividir pelo sem-bônus inflava em até **6,3 p.p.**
    (FABIANE BA 30,48% contra 24,22% do BI) e passou 4 semanas em produção porque o erro **cresce
    com o bônus do time**: em jun/2026 a bonificação era 1% da venda e a conta errada parecia certa;
    em ago/2026 é 7,5%. Regra geral pra qualquer % novo: se ele **não se reproduz com os números
    que a própria tela mostra**, ele está errado. Gate: `tests/test_margem_bonus.py`.
  - ⚠️ **Mock de metas sem `[venda_sb]` não testa nada de margem** — o `or` do fallback usa o bruto
    e o teste passa mesmo com a fórmula errada. Foi assim que o gate ficou cego. Todo fixture de
    metas leva `[venda_sb]` **diferente** de `[venda]`.
- **Admin** — CRUD usuários, cron de email, multi-CC, segmento RFM, editor de metas. **+ acesso por área, comprador vinculado e relatórios de Compras** (ver abaixo).

## 📦 Módulo Compras (features)

Navegação em 2 níveis: **Visão · Comprar · Pedidos · Estoque · Análise** (22 abas) + a tela de
CAMPO da pesquisa de preço, que fica **fora** do painel (`/estoque/pesquisa`). Foco no comprador.
- **Visão** — Cockpit + Painel gerencial (5 pilares) + Meta de ruptura + **Evolução do estoque** (ADM).
- **Comprar** — Abastecimento (sugestão de compra), Estoque zerado, Plano reposição.
- **Pedidos** — Orçamento (meta × realizado × pedidos), geração de pedido de compra (PDF + planilha Winthor).
- **Estoque** — Cobertura, Parado, Validade (FEFO), Vencidos, Ruptura por comprador, Ocupação.
- **Análise** — Desempenho comercial, Compras × Vendas, Fornecedores, ABC-XYZ, Produtos, Qualidade da base.

**Aba Evolução do estoque — a única série HISTÓRICA do módulo** (08/2026, pedido do diretor:
"acompanhar de fato se estamos tendo evolução positiva ou não… é gestão comprovada"). Valor de
estoque, capital parado, cobertura e ruptura ao longo do tempo. **Restrita ao ADM** enquanto a
série não amadurece.
- ⚠️ **O histórico NÃO pode ser gerado para trás, e isso não é limitação de esforço.** `PCEST` é
  **posição**: o `QTESTGER` de ontem foi sobrescrito e não existe no BI nem no Winthor. A aba
  Vencidos consegue mostrar mês a mês desde sempre porque perda por validade é **evento datado**
  (fica no livro); saldo é **estado**. Mesmo app, mesma pergunta, respostas opostas — e é essa
  distinção que decide o que dá para reconstruir. A **cobertura** é a mais irrecuperável: depende
  de `QTVENDMES1..3`, que são janelas móveis regravadas todo mês.
- **Guarda-se o INGREDIENTE, não o resultado** (`estoque_foto_item`: qtdisp, custo, giro, datas).
  Gravar "parado = R$ X" congelaria a série na régua daquele dia, e aí **corrigir uma régua vira
  degrau no gráfico** — numa aba feita para provar gestão, degrau de definição é lido como
  resultado de operação. Com o cru, mexer em `novo_dias`/`ideal_dias`/`eh_parado` **recalcula o
  passado inteiro**. Gate: `tests/test_historico_estoque.py`.
- **A ruptura é CONTRAPESO, não enfeite.** Estoque caindo, sozinho, pode ser desabastecimento.
  Por isso `valor_estoque` é a **única métrica sem cor** na tela (`_EVO_DIRECAO`): só parado ↓,
  ruptura ↑ e % ideal ↑ têm direção inequívoca. Pintar queda de estoque de verde faria a aba um
  dia comemorar uma ruptura.
- ⚠️ **Duas convenções de faixa, e as duas espelham a tela de propósito**: as faixas de cobertura
  seguem o Painel gerencial (`resumo_cobertura`, giro≤0 cai no 121+, então Σ faixas = valor de
  estoque e o empilhado fecha com o KPI); o trio ideal/risco/sem-giro segue `resumo_estoque_ideal`
  (sem-giro à parte). Unificar faria a série discordar de uma das telas que ela reproduz.
- **Robô** (`estoque/historico.py` + job no `server.py`): **18h-22h**, minuto 40 (ver acima). Janela de 5 passagens
  para um restart no horário exato não custar o dia — e a foto **só sai depois do refresh do dia**,
  senão grava a posição de ontem com a data de hoje. `ja_fotografado` impede refazer nas passagens seguintes;
  upsert porque o Swarm pode ter réplica. **Fora do `CRON_HABILITADO`**: aquele interruptor é para
  não mandar e-mail, e perder um dia de foto é irrecuperável.
- **Grão `data × unidade × produto`** (~6k linhas/dia, ~265 MB/ano na base real da Multpel: 4.519
  produtos de revenda, 286 fornecedores). `unidade` inclui "todas" e se sobrepõe às demais de
  propósito — faixa de cobertura não é decomponível por filial (o giro soma junto com o saldo).
- **Demo**: o robô não roda lá (`ANALYTICS_HOJE` fixo ⇒ a base não envelhece). O histórico vem do
  `_seed_demo/seed_historico_demo.py` (90 dias, SEED 42), senão a aba abriria vazia na
  apresentação comercial. Trava igual à do `seed_metas_demo` (`DEMO_SEED=1` + recusa `multpel_db`).
- **Recorte: comprador · fornecedor · curva · XYZ · unidade** — os quatro primeiros são gravados
  NA FOTO, então o passado não se reclassifica quando um item muda de curva. ⚠️ A curva ABC é o
  Pareto da venda do PERÍODO, e o robô fotografa numa janela MÓVEL de **90 dias**, não no default
  "mes" (que é o ACUMULADO do mês — no dia 1º a curva sairia de um dia de venda, e a série ganharia
  dente de serra em toda virada de mês). A tela declara a janela. **Depto e Buscar produto a aba NÃO honra** e avisa
  na tela — filtro que não responde em silêncio é a falha clássica do módulo.
  ⚠️ **Recorte novo tem de entrar na condição do rollup**, senão a aba serve o agregado da
  empresa para quem pediu a curva A — sem erro, só o número errado.
  Gate: `test_qualquer_recorte_desvia_do_rollup`.
- **"Itens em ruptura" é CONTAGEM de SKUs** (disponível ≤ 0 e giro > 0), não o catálogo: o KPI
  mostra `309 de 2.877`. Medido na base real: curva A 17/366 (4,6%) × curva C 268/2.061 (13%).
- **Ruptura por CURVA + %** (08/2026, pedido do diretor). É a **ruptura REAL** — item zerado com
  giro, tenha ou não pedido em aberto ("esquece a ruptura da meta; o objetivo é medir a evolução
  da real, o que tem ou não tem de fato no estoque"). ⚠️ Portanto **não é o número do placar da
  Meta de ruptura**, que só conta o que está sem providência e é sempre menor — a tela declara
  isso, senão alguém lê 11% aqui contra a meta de 2% de lá. Sai em **%** porque a curva C tem ~6x
  mais SKUs que a A e a contagem crua a mostraria sempre no topo. Deu para atender **sem perder
  história** porque a `curva_abc` já era gravada por item desde o 1º dia. Item sem curva → **C**,
  como no placar. Gates: `test_ruptura_por_curva_sai_na_foto_do_dia` e
  `test_a_ordem_do_select_casa_com_o_agregar` (a foto viaja como tupla POSICIONAL do SQL até o
  `agregar`: coluna nova num lado só desloca todos os campos seguintes, sem erro).
- 🩹 **1ª foto em produção: os gráficos saíam EM BRANCO** (19/08, "PQ ficou em branco?"). Não
  faltava dado — o Chart.js pinta **segmentos entre pontos**, e com `pointRadius:0` uma série de
  um dia não tem o que desenhar. O eixo já vinha escalado no valor certo (R$ 7.000k), que é a
  prova de que o dado chegou; a barra do 1º gráfico aparecia porque barra se desenha **por**
  ponto, o que fez parecer que só a cobertura falhara. Junto, os 4 KPIs diziam "R$ 0,00 (0%) na
  janela" — com uma foto o delta é zero por construção, e "0%" afirma uma medição que não foi
  feita (hoje sai `—`). O sintoma só existe no **primeiro dia de cada instância nova**, por isso
  virou gate: `tests/test_evolucao_acesso.py`.
- 🩹 **Capital parado: a série passou a usar a régua do Cockpit (60+ dias)** — antes usava a do
  `parado_faixa_de` (piso **15**), e no BI real, no mesmo dia e na mesma base, a série dizia
  **R$ 433.647** onde o Cockpit dizia **R$ 181.182**, as duas com o rótulo "Capital parado".
  **Não eram conceitos diferentes:** somando só as faixas de 61 dias para cima, a régua antiga
  dava R$ 181.155 — os R$ 26,79 de resto eram o item com exatamente 60 dias (uma banda começa em
  60, a outra em 61). Era a mesma conta concordando por sorte. Item sem vender há 20 dias é
  **rotação** num distribuidor, não dead stock: a faixa 15-30 sozinha eram R$ 151.699, e
  chamá-la de capital parado fazia o KPI gritar lobo. Fonte única: **`core.status_parado_de`**,
  chamada pelo `construir_produtos` e pelo `agregar`. A **aba Estoque parado segue em 15+** de
  propósito — lá o papel é mostrar o gradiente. ⚠️ Muda o número **sem ninguém mexer na
  operação** (a série inteira cai ~50%, sem degrau, porque a foto guarda o ingrediente):
  comparar antes×depois uma vez. Gate: `test_a_serie_da_evolucao_usa_a_MESMA_regua_do_cockpit`.
- **O rollup se auto-invalida** (`_rollup_atual`): payload gravado por uma versão anterior do
  `agregar` é recusado e a leitura cai no cru. Antes, métrica nova exigia lembrar do
  `rebuild_rollup` no deploy — e esquecer **não dava erro**, servia o agregado velho em silêncio.
  ⚠️ A checagem por CHAVES só pega métrica nova; **não vê métrica que muda de significado** — foi
  o caso do parado acima (mesmas chaves, número diferente). Por isso o payload leva um selo
  **`_v` (`_ROLLUP_VERSAO`)**: suba o número sempre que mudar o resultado do `agregar`.
  O rebuild continua valendo, mas agora por **performance**, não por correção.
- 🚧 **Ainda não tem export** (CSV/XLSX/PDF) nem entra no catálogo de e-mail — enquanto for
  ADM-only isso não faz falta, mas é o que falta para ela virar aba normal.

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

**Drawer 360° do FORNECEDOR** (07/2026, pedido do diretor: "venda mês a mês do fornecedor, igual
tem a do produto"). Clique na linha da aba Fornecedores → venda 12m **com o mesmo mês do ano
anterior sobreposto**, ciclo × lead, pedidos em aberto, a comprar (c/ impostos) e top produtos.
- ⚠️ **A série agrega no FATO por `CODFORNEC`** (`q_venda_fornecedor_mensal_rca`), **não** somando
  os produtos da tela — somar produtos é o bug do YoY já corrigido (item que saiu de linha some do
  histórico). Provado equivalente com o mesmo filtro: **R$ 74.636,87 nas duas agregações, R$ 0,00
  de diferença** — e agregar no fato custa **~6k linhas contra ~54k** da série por produto em 24
  meses (o `executeQueries` corta em **100.000**, armadilha que já mordeu no PCEST).
- **24 meses** de janela porque o gráfico sobrepõe o mesmo mês do ano anterior: sem isso a coluna
  `Cresc. AA` diz que caiu 20% e não diz **quando** nem se é tendência ou mês pontual.
- KPIs seguem o seletor **Venda** do topo; a **série é sempre 12m** exibidos (24m carregados) — é
  histórico, não recorte de tela (mesma política do Ciclo).
- `lead_confiavel=False` → a tela mostra `~26d (amostra fraca)`, não o número seco. Gate:
  `tests/test_fornecedor_360.py`.

**Aba Verbas — a página inteira fala UMA janela** (08/2026). O rodapé da aba sempre prometeu
"negociado/aplicado = últimos 12 meses · saldo = posição atual", mas dois elementos não cumpriam.
Hoje vale a **invariante**: `KPI Negociado 12m` = Σ coluna Negociado da tabela = Σ barras azuis do
gráfico = Σ do "Por conta". Validado no BI: 819.001,73 (empresa) e 37.586,43 (RAZZO), nos quatro
lugares, diferença 0,00.
- ⚠️ **Filtro de fornecedor recorta no SERVIDOR** (`core.verbas_fornecedores(fornec=…)`), junto do
  comprador e em **interseção**. Filtrar só as linhas no cliente deixava tabela de 1 fornecedor ao
  lado de gráfico da empresa toda (a barra de julho dizia ~R$ 63k onde a verba da RAZZO era
  R$ 10.054,80). O parâmetro é **`fornec`** (não `fornec_cod`) e entra **nas duas chaves de cache**
  — servidor e cliente; sem isso o 1º fornecedor consultado é servido aos demais por 30 min.
- ⚠️ **O eixo do gráfico é de CALENDÁRIO, montado do PERÍODO — não dos dados.** Listar "os meses
  que tiveram movimento" colava barras não consecutivas: 27 meses de calendário em 14 barras, e
  **96% dos fornecedores têm buraco**. Mês zerado é informação.
- ⚠️ **Rótulo de mês por extenso (`ago/26`), nunca `26-08`** — em pt-BR aquilo se lê como *26 de
  agosto*. O app já tinha a convenção (`MES_ABREV`/`mesLbl12`); só as Verbas ficaram de fora.
- ⚠️ **As DUAS pontas são parciais** e as duas avisam com `*`: a 1ª porque a janela são 365 dias
  corridos (começa no meio do mês — R$ 13.019,12 ficam de fora), a última porque o mês corrente
  ainda corre. Não dá para mudar para meses fechados: o `Negociado 12m` tem de casar com o
  `Compra 12m` (também 365d) que forma o **% V/C**.
- **Saldo é exceção proposital**: continua **posição** (qualquer emissão), porque é estoque, não
  fluxo. Por isso o saldo de uma conta pode ser maior que o negociado dela. O **drawer** segue
  2024+ (auditoria), com a janela escrita na tela. Gate: `tests/test_verbas.py`.

**Orçamento — "Buscar produto" recorta os pedidos em aberto** (08/2026, pedido do diretor:
"saber se existe pedido para aquele item, a quantidade e quando foi feito"). O termo vai ao
servidor (`?busca=`, na chave de cache junto do comprador e do arraste) e o corte acontece em
`core.logistica_pedidos(busca=…)`, que já percorria as linhas de PCITEM — **nenhuma query nova**.
- ⚠️ **O recorte vale para a LISTA e para os AGREGADOS** (`core.recorta_abertos_por_produto`).
  Os cards de prazo leem a **contagem** do resumo e o **valor** da lista no cliente: recortar só
  um deixaria "15 entregas atrasadas" ao lado de uma tabela com um pedido.
- ⚠️ **Os KPIs de orçamento NÃO entram** — meta/comprado/saldo são do comprador no mês, não do
  item. A tela escreve isso quando há filtro ativo.
- **Duas quantidades por linha**: `pedida` responde "eu já pedi?", `a chegar` responde "está
  chegando?". Item **já entregue** dentro de pedido ainda aberto aparece com "a chegar" zerado
  em vez de sumir — sumir levaria o comprador a pedir de novo o que já recebeu.
- ⚠️ Busca por **descrição casa a FAMÍLIA** (`EMB.GALV.G65` → 5 pedidos, 28.701 un contra 24 un
  do código exato); a janela é de **180 dias**. As duas coisas estão escritas na tela.
  Gate: `tests/test_orcamento_filtro_produto.py`.

**Qualidade da base — 2 blocos, 2 universos, cada um declarado** (08/2026, pedido do diretor:
"a lista dos itens com erro de cadastro não dá para deixar numa aba, para consultar tudo que
está errado no sistema?"). Substitui o CSV gerado à mão: duas fontes da mesma lista divergem no
primeiro cadastro que o TI corrigir.
- **Bloco 1 (saldo)** — as 5 checagens antigas (`_QUAL_CHECKS`). Dependem de estoque, então
  rodam sobre o **snapshot**, recortado por FILIAL.
- **Bloco 2 (cadastro logístico)** — `core.qualidade_cadastro`, endpoint `/api/qualidade-cadastro`,
  sobre a **BASE INTEIRA**. Sem query nova: cadastro e embalagem já estão em cache.
- ⚠️ **É por isso que os dois números não batem, e a tela escreve os dois escopos.** Medido:
  **72** cadastros impossíveis na base contra **21** dentro do snapshot do Atacado. Ligar a
  checagem no snapshot faria a tela dizer 21 enquanto a planilha enviada ao cliente dizia 70.
- Categorias: `cadastro_impossivel` (72 — caixa implicada acima de `MAX_M3_CAIXA`/`MAX_KG_CAIXA`,
  sinal de dado do máster gravado na unidade) e `sem_cubagem` (241). Os limiares **viajam do core
  para a tela**, senão a explicação do critério mentiria ao mudar a guarda.
- ❌ **Não** virou card: "sem peso" (**0** produtos) e "sem fator de caixa" (1.744 = 38,7%, mas
  **todos** com `QTUNITCX = 1` explícito — venda em unidade é legítima, e um card com 1.744
  ensinaria a ignorar a aba). Gate: `tests/test_qualidade_cadastro.py`.

**Estoque parado — card "Novos"** (08/2026, achado pelo diretor: "os produtos novos estão caindo
como itens parados, sem venda… hoje eles vão para 121+"). Causa: em `core.parado_faixa_de`, quem
**nunca vendeu** (`DTULTSAIDA` vazia) entrava como "parado há infinito" e caía direto no 121+,
mesmo tendo chegado ontem.
- **A régua: quem nunca vendeu conta os dias a partir da ENTRADA** (`dias_sem_entrada`), não do
  infinito. Nunca vendeu + entrada dentro de `novo_dias` (⚙ Parâmetros, default **15**) → faixa
  **`novo`**; entrada mais velha → a faixa VERDADEIRA da chegada. Medido no BI: dos 272 itens em
  121+, **41 nunca venderam** e só 23 tinham entrada de fato antiga — os outros 18 estavam
  rotulados "parado 121+ dias" com chegada de 3 a 90 dias. **Total da aba intacto**: 923 itens /
  R$ 437.164,45 antes e depois, sobre os mesmos dados; **18 itens moveram, todos saindo do 121+**
  (10 para `novo`, 8 para a faixa real) e **nenhum** deles tinha venda.
- ⚠️ **NÃO basta "chegou há menos de 15 dias"** — foi a leitura literal do pedido, e ela pega **85**
  itens dos quais **75 já venderam antes** (é reposição de item normal). Pior: o cód. 57071
  (última venda há **1.249 dias**, chegado há 9, R$ 2.607) sairia do 121+ para um card chamado
  "Produtos novos", escondendo exatamente a compra que precisa aparecer. A regra exige
  **nunca ter vendido**, não só entrada recente.
- ⚠️ **São DOIS eixos e os dois mudaram**: `parado_faixa` (aba Estoque parado) e `status_parado`
  (Cockpit). Corrigir só um é a armadilha da Ruptura de novo. `status_parado` ganhou o valor
  **`novo`**, então **`status_parado` deixou de ser booleano** — quem testar a verdade do campo
  volta a somar produto recém-chegado como dead stock. Fonte única: **`core.eh_parado(p)`**
  (`ehParado` no JS), usada no capital parado, no alerta 120+, nos "maiores ofensores" e na coluna
  de valor parado da aba Fornecedores.
- Efeito no **Cockpit**: alerta "Parado 120+ dias" 278 → **260** itens (R$ 119.812 → 105.747) e
  capital parado R$ 188.435 → **174.494**. ⚠️ Isso **afrouxa o placar sem ninguém mexer na
  operação** — comparar antes×depois uma vez, senão parece ganho de gestão.
- Item que nunca vendeu e **não tem data de entrada** continua em 121+ (0 casos hoje) — lado
  conservador: na dúvida ele aparece, não some atrás de "novo". `novo_dias` tem clamp em
  `merge_params` (0 esvaziaria o card em silêncio, sem erro).
- Detalhes de tela: a faixa `novo` é a 1ª do bloco e **entra na soma** (não é bloco à parte — ela
  não acrescenta itens, TIRA do 121+ os rotulados errado); `FX_PARADO` virou `fxParado()` porque o
  rótulo carrega a janela em dias; a coluna "Dias parado" mostra **"chegou há Xd"** em vez de
  "nunca" nesses itens, e ganhou a coluna ordenável **"Chegou há"**.
  Gate: `tests/test_parado_novos.py` (18 testes, incluindo a partição das faixas).

**Horário da foto: 18h-22h, minuto 40** (era 6h-12h até 08/2026). A mudança veio de **medição**,
não de preferência: o dataset Estoque atualiza **7x por dia** — 06:26 · 08:28 · 10:24 · 12:25 ·
14:24 · 16:23 · **17:44** (BRT, apurado no *refresh history* da API). Fotografando de manhã, a 1ª
passagem pegava o refresh das **06:26**, que é a posição **antes de qualquer movimento do dia** —
ou seja, o fechamento de ontem carimbado com a data de hoje. Às 18h40 a foto sai depois do
**último** refresh e passa a valer o fechamento **real** do dia.
- ⚠️ **Continua sendo JANELA (5 passagens), não disparo único.** Perder um dia é irrecuperável, então
  um deploy às 18h40 não pode custar a foto. Teto em 22h para nunca cruzar a meia-noite e gravar a
  posição de um dia na data do outro.
- ⚠️ **Muda o significado da série uma vez**: cada ponto passa de "início do dia" para "fim do dia",
  um deslocamento de ~1 dia de movimento. Feito em 20/08/2026, com **2 fotos** no banco — foi o
  momento mais barato possível para fazer isso, e depois de 90 dias de série teria custado um degrau.

**Ocupação do depósito na série** (08/2026, pedido do diretor: *"será que dá para colocar o gráfico
de ocupação ali também? Só o percentual de ocupação"*). KPI + gráfico próprio na Evolução.
- **Tabela nova `estoque_foto_estado`** (`data × unidade → payload JSONB`). ⚠️ **NÃO foi para a
  `estoque_foto_dia`**, que tem exatamente a mesma forma e está ao lado: aquela é o **rollup**, isto
  é, *cache* de `historico.agregar`, descartável e reconstruível por `rebuild_rollup`. A ocupação é
  dado **primário e irrecuperável** — a posição do WMS de ontem não existe em lugar nenhum do
  Winthor. Misturar as duas faria um `rebuild_rollup` de rotina **apagar histórico que não se
  refaz**, sem erro nenhum.
- **`payload` é JSONB e não colunas** de propósito: a lista de métricas de estado vai crescer, e com
  colunas cada uma seria uma migration. O UPSERT faz **merge** (`payload || EXCLUDED.payload`), então
  métrica nova não apaga as que já foram medidas naquele dia.
- **Só os escalares** (`posicoes`/`ocupadas`/`livres`/`bloqueados`/`pct` + picking×pulmão). Rua e
  vaga vazia são retrato de **endereço**; a série responde "o depósito está enchendo?".
- ⚠️ **Try próprio, depois do `gravar`**: a ocupação vem de outra fonte (PCENDERECO). WMS fora do ar
  não pode custar a foto do estoque, que é o dado principal.
- ⚠️ **Com recorte ativo ela NÃO viaja** (`historico._com_estado`). O grão é **posição do WMS**, que
  não se decompõe por comprador, fornecedor, curva ou XYZ — servir o número do depósito inteiro ao
  lado de um gráfico filtrado pela curva A é a falha clássica do módulo. O KPI mostra `—` e diz por quê.
- ⚠️ **Sem cor** (`_EVO_DIRECAO["ocupacao_pct"] = None`), mesma regra do valor de estoque: subir pode
  ser depósito enchendo (ruim) ou giro entrando (bom). Quem dá o sentido é a linha ao lado do valor
  e da ruptura. E o **eixo não começa em zero** — ocupação vive numa faixa estreita (hoje 84,0%) e
  forçar o zero achataria a linha; é o oposto do gráfico de valor, onde truncar exageraria.
- **Medido hoje:** Atacado **84,0%** (4.446 de 5.290 posições). ⚠️ As unidades `am`/`ac`/`jid` não
  têm endereçamento no WMS (0 posições) e saem com `—` — degradação correta, não defeito.
- **O merge vive em `serie()`, não em `agregar()`**: o `agregar` é PURO (recebe linhas, não toca
  banco), e é essa pureza que permite recalcular o passado. Fazê-lo ler outra tabela mataria isso.

**Watchlist "Em desaceleração" — o aviso ANTES do capital parado** (08/2026). Card no Cockpit
(drill para Análise → Produtos), painel dos maiores ofensores, e métrica na série da Evolução.
Nasceu de um pedido para **derrubar o piso do capital parado de 60 para 20 dias** — e a medição
é que mostrou por que isso seria caro:

| | SKUs | valor | % do estoque |
|---|---:|---:|---:|
| capital parado hoje (60+ dias) | 420 | R$ 179.395 | 2,9% |
| se o piso caísse para 20 dias | 833 | R$ 365.920 | 6,0% |

- ⚠️ **A faixa 20-59 dias é 100% curva C.** Não é aproximação: o item de **curva A** que está há
  mais tempo sem vender está há **14 dias**, e o de **curva B**, há **16** — nenhum dos dois
  alcança 20. E **392 dos 413** itens da faixa **têm giro**: eles vendem, só não venderam nas
  últimas semanas. Baixar o piso **dobraria o KPI** com rotação normal de curva C, num placar cuja
  função é apontar dead stock. Item que não vende há 3 semanas num distribuidor é o ciclo dele.
- **A saída foi uma lista à parte, não uma régua nova.** Três condições
  (`core.em_desaceleracao`): parou de vender há **20-59 dias** · cobertura **> 90 dias** ·
  valor **≥ R$ 200**. Hoje: **146 SKUs / R$ 128.368**, também 100% curva C.
- ⚠️ **É DISJUNTA do capital parado por construção**, não por coincidência de parâmetro: a 1ª
  linha de `em_desaceleracao` é `if eh_parado(p): return False`. Sem ela, subir `desacel_ate`
  acima de 60 faria os dois cards contarem o mesmo item, e "parado + desacelerando" poderia
  passar do valor de estoque. Gate: `test_disjuncao_resiste_a_parametro_que_invade_a_faixa_do_parado`.
- ⚠️ **90 dias de cobertura é PISO, não preferência.** A cobertura **mediana** de um item de curva
  C **que está vendendo normal** é de **84 dias** nesta base (p25 40 · p75 195 · p90 463) — ter 3
  meses de estoque é o comportamento normal do item C. Em 90 o filtro ainda separa (deixa passar
  61% da janela); em 45 deixaria passar 82% e a condição viraria enfeite. **Descer daqui exige
  refazer a medição**, não é ajuste de gosto. O corte foi negociado de 120 → 90 com o diretor: a
  diferença são 27 SKUs e R$ 11.119 (+9% do valor), e "3 meses" é regra que a pessoa lembra.
- ⚠️ **O piso de R$ 200 é PISO DE VALOR, e não um "top 50"** — a alternativa que estava na mesa.
  Lista de tamanho fixo **nunca melhora**: mostraria 50 itens hoje e 50 depois do problema
  resolvido pela metade. Como a métrica entra na Evolução, que existe para provar gestão, número
  constante por construção é pior que não ter métrica. O piso corta 41% das linhas levando só
  6,8% do dinheiro. Gate: `test_a_lista_ENCOLHE_quando_o_problema_diminui`.
- **Na Evolução ela nasce com o histórico inteiro**, não com um ponto: a régua sai de `dias sem
  venda` + `cobertura_dias` + `valor`, os três já gravados na foto desde o 1º dia. É o segundo
  pagamento da decisão de guardar o **ingrediente** em vez do resultado. No gráfico ela é a linha
  **tracejada no mesmo eixo do parado** (grandezas parecidas: R$ 128k × R$ 179k) — eixo próprio a
  faria parecer maior que o parado, e a leitura da aba é qual dos dois alimenta o outro.
  `_ROLLUP_VERSAO` subiu para **3**.
- ⚠️ **Item com giro 0 e venda recente ENTRA** de propósito: `_giro_mensal` arredonda para inteiro,
  então quem vendeu 1 unidade em 3 meses tem giro 0 e cobertura 9999. Item que vendeu há 30 dias e
  cuja média trimestral zerou é exatamente o alvo. São 14 itens; eles também aparecem no "sem giro"
  do Cockpit, e essa sobreposição entre LENTES é aceita — a proibida é com o capital parado.
- **4 parâmetros** em ⚙ Parâmetros (`desacel_de`/`_ate`/`_cob`/`_valor_min`), com clamp em
  `merge_params` — nenhum valor de entrada erra alto, todos apenas esvaziariam o card, e card
  vazio se lê como "não há problema". Eles viajam no `serverQS()` **e** no `exportQS()`: o card sai
  do backend e o filtro do export é refeito lá com os mesmos params, senão mexer no painel moveria
  a tela e não moveria o Excel.
- 🩹 **De quebra, o campo que provavelmente originou o pedido:** ⚙ Parâmetros tinha
  **"Parado: dias parados (≥)" = 60**, que parece o botão da régua mas é só **filtro de listagem**
  da aba Estoque parado (`core.py`, "não desloca faixa"). Quem o mudasse para 20 não veria nada
  acontecer. Relabelado para **"Parado: mín. dias p/ listar"** com o escopo no `title`.
- Gate: `tests/test_desaceleracao.py` (25 testes). ⚠️ O 1º deles trava que **o capital parado não
  se mexe** — se ele cair, a decisão inteira foi revertida sem querer.

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
- **Peso e cubagem: fonte única `core.medidas_unitarias`, e a fonte é o `PCPRODUT`.** Os três
  valores (`VOLUME`, `PESOBRUTO`, `PESOLIQ`) são **por UNIDADE**; a caixa é `× fator`, e o total
  do pedido é `quantidade em UNIDADES × unitário`. Validado contra o **rodapé do 211** no pedido
  565848 (fornec. 9406, 22 itens): líquido **14.482,02**, bruto **14.497,64**, volume **23,50 m³**
  — os três exatos. O PDF traz os três, iguais ao ERP, e por isso deixou de dizer "estimado".
  ⚠️ **Fator de caixa**: `PCEMBALAGEM[QTUNIT]` **só quando > 1**, senão `PCPRODUT[QTUNITCX]`
  (`core.py`, `caixa = qtunit_emb if qtunit_emb > 1 else qtunitcx`). Os dois **divergem em 152
  produtos** e 120 têm `QTUNIT = 1` — usar `emb or cad` derruba esses 120 para fator 1.
  Qual dos dois está certo nos 152 **não está resolvido**: `QTFATORCONVERSAO` é campo morto
  (zerado) e `QTPEDIDA/QTDIGITADA` acerta só 36% dos casos conhecidos.
- **Guarda de plausibilidade** (`MAX_M3_CAIXA`=1,5 · `MAX_KG_CAIXA`=50): caixa implicada
  impossível → `medidas_confiaveis=False`, a tela mostra `—` e diz **quantos itens ficaram de
  fora**. São cadastros em que o dado do **máster** foi gravado no registro da **unidade**
  (66919: 5,3 kg e 0,09879 m³ na unidade ⇒ 530 kg/caixa). **70 produtos** listados em
  `cubagem_a_corrigir.csv` (fora do repo — o repo é público). ⚠️ Os limiares são calibração,
  não fato: 33 itens estão 5× acima deles (indiscutíveis), 15 a menos de 1,5×.
- **Orçamento** = meta `65% da venda líq. 30d` por comprador × realizado do Winthor. **Transferência entre filiais NÃO é compra**: pedido cujo fornecedor tem a **mesma raiz de CNPJ** (8 díg., contra `MULTPEL_EMPRESA`) fica fora do orçamento.
  - **Mês fechado** (08/2026, pergunta do diretor "quando vira o mês o orçamento zera; quem
    estourou não deveria arrastar?"): o mês anterior passa a ser **apurado e exibido sempre**
    (`meta_ant`/`comprado_ant`/`saldo_ant`) — era ele ser invisível o problema. **Descontar** o
    estouro da meta é **opt-in** (`?arrastar=1`, checkbox na tela): a meta é 65% da venda dos
    ÚLTIMOS 30 DIAS, régua de **fluxo**, não budget anual — ligado por default puniria duas vezes
    quem estourou porque a venda subiu. ⚠️ **Sobra NÃO vira crédito** e o arraste **não cascateia**
    (estouro maior que a meta zera o mês e morre ali). A base da meta do mês passado é a venda de
    30d medida **naquele fechamento**, não a de hoje — reconstruir com a venda atual produziria
    estouro contra uma meta que nunca existiu; sem ela o bloco sai vazio, não errado.
    Gate: `tests/test_orcamento_mes_anterior.py` (antes disso o Orçamento não tinha teste nenhum).
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
- ⚠️ **NÃO tire peso nem cubagem da `PCEMBALAGEM`.** Ela parece a fonte certa (é a tabela de
  embalagens) e está publicada com todas as colunas, mas **`PESOBRUTO` vem vazio em 75,6%** dos
  produtos de revenda e **`VOLUME` em 100%** — 13 linhas preenchidas em 191.804. Foi ela a fonte
  do peso até 08/2026: o PDF dizia **6.758 kg** onde o ERP dizia **14.497,64** (−53%), porque o
  item mais pesado do pedido (49447, 350 caixas, metade do valor) simplesmente **contava zero**.
  Ninguém percebeu porque o total *parecia* completo. Segundo defeito da mesma fonte: `qtunit` e
  `pesobruto` vinham de **dois `MAX()` INDEPENDENTES** de uma tabela com uma linha por embalagem,
  então o fator de uma linha casava com o peso de outra (cód. 46661: fator 24 × peso do pacote
  de 12). Gate: `tests/test_peso_cubagem.py`.
- ⚠️ **O rótulo da unidade sai do TEXTO da embalagem; o FATOR, não.** `FD/8X192/UN` imprime
  **FD** (`core._rotulo_master`), mas o número segue o `QTUNITCX` — os dois divergem em cadastros
  reais (cód. 57474: texto diz `CX/0100/UN`, fator real 10). Antes saía "CX" em tudo que tivesse
  fator, e o comprador **confere o PDF contra o 211 linha a linha**.
- ⚠️ **Coluna nova de cadastro tem de entrar TAMBÉM no `estoque/provider_sql.py`** e no
  `_seed_demo/` (schema + gerador). Chave que o `core` lê e o provider não devolve **zera a tela
  em silêncio** no modo BD. E coluna nova referenciada direto no SQL **derruba o módulo inteiro**
  nas bases sintéticas já criadas (`column does not exist`) — por isso `pesoliq` é lido via
  `to_jsonb(pcprodut) ->> 'pesoliq'`, que devolve NULL em vez de estourar.
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
  Gate: `tests/test_estoque_ideal.py` (16 testes, incluindo "sem params sai igual ao de antes").
  ⚠️ Os parâmetros são **por navegador** (`localStorage`): enquanto o valor não for fechado, o
  painel pode significar coisas diferentes para cada pessoa. Ao definir, promover a default do servidor.
  - **O card "Em risco" é um DRILL** (08/2026, pedido do diretor: "clicar aqui e já trazer os itens
    que estão abaixo dessa cobertura"). Leva à aba **Análise → Produtos** com o filtro
    `Cobertura ≤ limiar−1` — que é o `cob_max`, filtro que **já existia e já viajava no
    `exportQS()`**, então o Excel/PDF da lista sai com o mesmo recorte sem código novo. Convenção
    do clique: `kpiGo()` emite `data-view`/`data-filt` igual ao `alertCard`, e o `wireAlerts`
    passou a casar `[data-view][data-filt]` — um fio só para os dois, em vez de dois que saem de
    sincronia. O `goView` **zera o `cobMax`** quando ele não vem no filtro, senão o próximo drill
    herdaria o recorte do anterior.
  - ⚠️ **O resumo lê o `cobertura_dias` do produto — NÃO recalcule `ceil(qtdisp ÷ giro_dia)` aqui.**
    O `construir_produtos` calcula com os valores **crus**; o dict guarda `qtdisp`/`giro_dia` já
    **arredondados**, e refazer a conta a partir deles muda o `ceil`. Medido no BI real com limiar
    25: o card dizia **789 SKUs** e a lista (que sempre leu `cobertura_dias`, como o export e a aba
    Cobertura) tinha **791** — cód. 44398, 104 un ÷ giro cru 4,3333… = 24d exatos, mas ÷ 4,333 =
    24,0018 → 25d. No limiar 45 os dois coincidiam **por sorte**, e foi por isso que passou
    despercebido até o card virar clicável. Conferido depois do fix em 7 limiares (10→90), SKUs e
    valor iguais nos dois lados, e o fechamento ideal+risco+sem giro = universo.
    ⚠️ **O mesmo recálculo continua no `core.resumo_cobertura`** (tabela "Cobertura de estoque",
    mesmo painel) — conhecido, **não corrigido** por decisão de escopo; corrigir muda os números
    daquela tabela. Se for mexer, é a mesma linha.
  - Só o "Em risco" é clicável. "Cobertura ideal" exigiria um filtro `cobertura ≥ X` que não
    existe, e "Sem giro" tem universo próprio: o painel conta **346** (todo item com giro 0) e o
    filtro `status_abast='sem_giro'` conta **303** (só com estoque) — ligar sem resolver isso daria
    card e lista com números diferentes, que é exatamente o que este drill veio consertar.
- ⚠️ **Mercadoria em PRÉ-ENTRADA conta no estoque projetado** (`core.qt_em_transicao`). Na
  pré-entrada o Winthor baixa o `PCITEM[QTENTREGUE]` (o item sai de "já pedido") e joga a
  quantidade em `QTESTGER` **e** `QTBLOQUEADA` (disponível = 0) — some das duas contas e o app
  sugeria **comprar de novo o que já está no armazém**. Medido: 130 linhas, **R$ 198.683**.
  Entra no **projetado**, nunca no `qtdisp`: a mercadoria não é vendável, então segue contando
  como ruptura e fora do valor de estoque. Status próprio `aguardando_liberacao`.
  ⚠️ É **heurística** (bloqueio com entrada ≤7d): o Winthor usa o MESMO `QTBLOQUEADA` p/ avaria
  e pré-entrada e o `MOTIVOBLOQESTOQUE` vem vazio. Validada contra 2ª fonte (`PEDIDO_ENTRADA`):
  21/21 nos ≤3d, 2/122 no bloqueio >30d. **Trocar por `PCMOVPREENT`** (107.566 linhas no Oracle)
  quando publicada — só o corpo da função muda. `PCNFENT.CONFERIDO` é campo morto ('N' em 2.287,
  nulo em 26, nenhum 'S').
  - 🩹 **AVARIA aparecendo como pré-entrada** (08/2026, achado pelo diretor — "na teoria o que é
    avaria não deveria aparecer aí"). **Não era mudança de rotina no ERP: eram dois furos do
    cálculo**, os dois resolvidos com a posição **por filial** (`q_bloqueio_filial` →
    `core.qt_em_transicao(linhas=…)`):
    1. o snapshot agrega `SUM(QTBLOQUEADA)` com `MAX(DTULTENT)` — **cruza o bloqueio de uma
       filial com a data de outra**. No Atacado (3+5), uma entrada recente na Matriz carimbava
       como "chegando" uma avaria velha do Depósito. Nenhum limiar de dias conserta isso;
    2. `DTULTENT` é a última entrada de **qualquer natureza**. Agora `QTULTENT` é **teto**: 200 un
       bloqueadas com entrada de 12 un = 12 em pré-entrada, 188 avaria. É **cap, não filtro** —
       descartar a linha traria de volta o BO original.
    A query é **separada do snapshot de propósito** (colunas novas no coração do módulo
    arriscariam todas as telas) e **degrada** para o modo agregado se falhar. Sintoma na tela que
    denunciava tudo: a coluna **Avaria** e o **"+N" de já-pedido** mostravam o MESMO número.
    Gate: `tests/test_pre_entrada.py`. Pista ainda não explorada: no WMS a **RUA 99** é o endereço
    de avaria (o app já a exclui do endereçado) — bloqueio ali é avaria por definição.
- ⚠️ **"Sem pedido" ≠ "sem pedido em aberto"** (`core._sem_providencia` / `semProvidencia` no JS).
  Item em **pré-entrada** não conta: já está no armazém e o Winthor baixou o pedido ao receber.
  Achado pelo diretor em 08/2026 — a aba Ruptura dizia **4** na curva A e o drill (Estoque zerado
  → "Ruptura s/ pedido") mostrava **3**, porque `status_exec` já tratava pré-entrada como estado
  exclusivo e as agregações de ruptura nunca foram atualizadas. ⚠️ São **três** implementações do
  mesmo conceito: `core.ruptura_por_comprador` (export), `agrupa()` (tela) e `metaAgrega()`
  (placar da Meta de ruptura) — mexeu numa, mexa nas três. O item **continua em `n_ruptura`**:
  falta estoque de fato, a venda perdida é real; o que ele deixa de ser é risco de omissão.
  ⚠️ Isso **afrouxa o placar da meta** sem ninguém mexer na operação — comparar antes×depois uma
  vez, senão parece ganho operacional. Gate: `tests/test_pre_entrada.py`.
- ⚠️ **Todo lugar que mostra "quanto vou gastar" fala a régua da NF** (c/ impostos): card do
  fornecedor, Cockpit ("A comprar"), Estoque zerado ("Custo de reposição"), Ruptura por comprador,
  drawer 360°, aba Fornecedores e o relatório de Reposição. A **única exceção proposital** é a
  coluna `Valor sug.` do Abastecimento, que segue em **mercadoria** — é o preço que vai na planilha
  de importação. Fonte única no front: `valReporNF()` (estoque.js); no back: `_valor_sugerido_compra`.
- ⚠️ **Filtro de tela tem de viajar no `exportQS()`.** `margem`, `cob_max` e `sem_ped` existiam só
  no front: a tela mostrava 116 itens e o PDF saía com o universo inteiro. Ao criar filtro novo,
  espelhe em `_aplicar_filtros_cliente` (e no `_margem_bucket`, que replica o `margemBucket` do JS).
  Gate: `tests/test_export_filtros_reposicao.py`.
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
- **Dado da cliente virou ENV** (08/2026): o emitente do pedido sai de `EMPRESA_*` (`EMPRESA_RAZAO`,
  `EMPRESA_CNPJ`, …), com o default apontando para a Multpel — produção não precisa de env, e a
  **demo define os seus** no compose. O logo (`CLIENTE_LOGO`) cai para "sem logo" quando a
  instância declara `EMPRESA_RAZAO`: quem troca a empresa do pedido não quer a marca da outra.
  `NOMES_FILIAL` perdeu a marca no rótulo (`"Multpel Matriz"` → `"Matriz"`) — aquela linha aparece
  no topo de toda tela, inclusive na apresentação. `ADMIN_EMAIL` idem, pela tela de login.
- 🗑️ **Removidos os mascaradores de demo** `COMPRADOR_DEMO` (Compras) e `TIME_DEMO` (Comercial):
  existiam de quando a apresentação rodava sobre a base REAL. Hoje a demo tem base própria
  (`joga_demo`, sintética) — não há nome real a esconder, e um flag global desses só volta como
  risco de alguém esquecê-lo ligado em produção.

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
`test_medida_compat.py`. Baseline **701 passam / 3 falham** (as 3 são fixture de data do
Comercial: radar/mix/cohort).
⚠️ Até 19/08/2026 o baseline dizia "5 falham", contando 2 do `test_provider_estoque` como
*"dependem de um `joga_demo` local com venda no mês corrente"*. **Nunca foi ambiente:** elas
falhavam porque o módulo perguntava pelo mês do CALENDÁRIO a um banco cujo dado terminava antes.
Ancorado o `_hoje()` no dado, passaram — ver a armadilha nº 17.

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
│   ├── pesquisa.html               #   🆕 tela de CAMPO (mobile, autocontida) — NÃO é aba
│   └── index.html                  #   SPA do módulo (22 abas)
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
└── tests/                          # pytest (701 passam; 3 falham por fixture de data — não é regressão)
```

---

## 🔧 Setup local (dev)

```bash
cp .env.example .env        # preencher (ver variáveis abaixo)
docker compose -f docker-compose.dev.yml up -d redis
python -X utf8 init_db.py   # cria/migra schema + admin default (ADMIN_EMAIL / ADMIN_SENHA)
python -X utf8 server.py    # http://localhost:5000
pytest -q                   # 701 passam, 3 falham (fixture de data do Comercial — não é regressão)
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
- **Login:** o `ADMIN_EMAIL` / `ADMIN_SENHA` definidos na stack. Acompanhar os logs do `demo-seed` até
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

- **17 relatórios** (todos com PDF+XLSX já prontos via `_export_data`/`_gerar_pdf`). Catálogo único
  em **`estoque/relatorios.py`** (Admin e cron leem do mesmo lugar).
- Admin marca por usuário quais recebe; reusa horário/frequência do cron. ⚠️ O recorte por comprador
  vai na **query string** do contexto simulado (`estoque/emails.py`) — o estoque lê filtro de
  `request.args`, não da sessão (diferente do Comercial).
- ⚠️ **O parâmetro é `comprador_cod`, não `comprador`.** Até 07/2026 o email mandava `comprador`,
  que ninguém lê no export: o comprador vinculado recebia relatório **rotulado com o nome dele e
  com os dados da empresa toda** (inclusive o desempenho dos colegas). Todas as views recortam por
  `comprador_cod` — via `_aplicar_filtros_cliente` ou filtro próprio (vencidos/leadtime/verbas/
  **desempenho**). Exceções legítimas: `conferencia` e `vazias` (grão é endereço, não tem
  comprador). Gate: `tests/test_email_comprador.py`.

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
   O baseline é **701 passam / 3 falham**. As 2 do `test_provider_estoque` que constavam aqui
   como dependência de ambiente eram, na verdade, o bug de ancoragem de data (armadilha nº 17).
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
2 sweeps HTTP (100% dos endpoints branchados); baseline 293 testes.

---

## 📄 Licença / Suporte
Privado. Uso interno Multpel + parceiros JOGA autorizados.
Logs: `docker service logs painel-teste_painel-app --tail 200` · Health: `/health` (lista módulos).
