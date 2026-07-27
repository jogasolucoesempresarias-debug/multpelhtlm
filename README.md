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
> - **Trabalho ativo:** branch **`feat/fusao-estoque`**, que builda a imagem **`:teste-fusao`** e
>   roda em **`painel.jogasolucoes.com.br`** (stack de validação). A `main` ainda descreve/serve o
>   mundo antigo.
> - **🔀 Produtização multi-fonte:** branch **`feat/multi-fonte`** (builda **`:multi-fonte`**) faz o
>   app rodar de **Power BI OU Postgres** com o mesmo código (env `DATA_SOURCE`). É o que permite a
>   **instância DEMO** (`demo.jogasolucoes.com.br`, dados sintéticos). Ver a seção **🔀 Multi-fonte**.
> - 🚫 **NÃO** edite o repo `MultpelEstoque/` (congelado) nem publique em `:latest` sem intenção —
>   `:latest` é a produção antiga que ainda está no ar.
> - 📚 **Vai mexer no Compras especificamente?** Leia também **`docs/estoque/planilha_v3.md`** — é
>   onde estão as fórmulas do estoque decodificadas em detalhe (giro, cobertura, sugestão de compra,
>   vencidos, orçamento). Este README traz o resumo + as armadilhas; o `planilha_v3.md` traz o miolo.
>
> ### Estado atual do rollout (temporário — some quando as antigas forem desativadas)
> | Domínio | O que roda | Imagem/tag | Banco |
> |---|---|---|---|
> | `analytics.jogasolucoes.com.br` | app ANTIGO (Comercial) | `multpelhtlm:latest` | `multpel_db` |
> | `estoque.jogasolucoes.com.br` | app ANTIGO (Compras) | `multpel-estoque:latest` | `estoque_db` |
> | `painel.jogasolucoes.com.br` | **app FUNDIDO (validação)** | `multpelhtlm:teste-fusao` | `painel_db` |
> | `demo.jogasolucoes.com.br` | **app DEMO (multi-fonte)** | `multpelhtlm:multi-fonte` | `joga_demo` (sintético) |
>
> O `painel` substituirá os dois de cima após a validação do sócio. Enquanto isso, coexistem.
> A `demo` roda **dados sintéticos** (`DATA_SOURCE=postgres`), pra apresentações — **não toca o BI do cliente**.

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

### Metodologia de dados do Compras (v3) — o essencial

Consome no dataset **Estoque**: **PCPEDIDO/PCITEM** (pedido real), **PCEMBALAGEM** (caixa/cubagem),
**PCEMPR** (comprador). Vencidos usa +4: **PCLANC/PCCONTA** (conta 200042) e **PCNFSAID/PCMOV**.
Doc completa das fórmulas em **`docs/estoque/planilha_v3.md`**.

- **Estoque (Disponível/QTDISP)** = gerencial líquido `QTESTGER − avaria(QTBLOQUEADA) − reserva(QTRESERV)`, filiais 3+5. Endereçado (`PCESTENDERECO`, RUA≠99) só na validade/FEFO.
- **Giro** = média 3 meses (`QTVENDMES1..3`/3), toggle p/ forecast (RCA). **Custo** = `CUSTOFIN`. **Comprador** = `PCFORNEC.CODCOMPRADOR → PCEMPR.NOME`.
- **Sugestão de compra** desconta o **pedido REAL em aberto** (PCPEDIDO/PCITEM, últimos 180d) e sai **em caixas** (`QTUNIT`/PCEMBALAGEM); sem fator de caixa → em unidades (pendências em `estoque/itens_sem_fator_caixa.csv`).
- **Orçamento** = meta `65% da venda líq. 30d` por comprador × realizado do Winthor. **Transferência entre filiais NÃO é compra**: pedido cujo fornecedor tem a **mesma raiz de CNPJ** (8 díg., contra `MULTPEL_EMPRESA`) fica fora do orçamento.
- **Duas réguas de valor (IPI/ST).** O Orçamento mede o realizado por `PCPEDIDO[VLTOTAL]`, que é a
  **NF cheia** (mercadoria + IPI + ST). Então a sugestão de compra sai nas duas: `valor_sugerido_liq`
  (mercadoria — é ela que vira **preço na planilha do Winthor**) e **`valor_sugerido_nf`** (o que
  consome a meta). As alíquotas saem do **pedido real** por `(fornecedor, produto)`
  (`core.montar_tributacao`), com cascata `pedido_real → perfil_fornecedor → cadastro → 0` e a
  **fonte** visível na tela. Detalhe em **🧾 Tributação do pedido**.
- **Comprador vinculado** ao usuário no Admin é **filtro inicial**, não trava — ele pode ver os outros.
- **Lista de compradores** ≠ folha inteira: deriva da base (`compradores_reais()` — fornecedor com produto de revenda → `CODCOMPRADOR`). Usar `PCEMPR` cru traz vendedores/financeiro.

**Armadilhas de dados (landmines — não repita):**
- ⚠️ **Vencidos: join por `NUMTRANSVENDA`, NUNCA por `NUMNOTA`.** `NUMNOTA` repete ao longo dos anos e infla o resultado ~3,5× (o `SELECT DISTINCT` **não** corrige).
- ⚠️ **ABC-XYZ: as `<option>` do `#f-xyz` precisam de `value` explícito (`X`/`Y`/`Z`).** Sem ele o filtro casa nada e devolve **zero produtos em silêncio**.
- ⚠️ **Pedido de compra: o preço converte JUNTO com a quantidade** (o Winthor faz `B×C` literal). Converter só a qtd colocaria o pedido no ERP com valor ~50× menor. Fonte única `core.item_master` (PDF + planilha).
- ⚠️ **Cobertura ideal:** fronteira **≥45d inclusiva** (`core.resumo_estoque_ideal`); `limiar_dias=45` está fixo, não ligado ao "Cobertura alvo" do ⚙ Parâmetros.
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
`test_medida_compat.py`. Baseline **242 passam / 3 falham** (as 3 conhecidas de fixture de data).

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
└── tests/                          # pytest (242 passam; 3 falham por fixture de data — não é regressão)
```

---

## 🔧 Setup local (dev)

```bash
cp .env.example .env        # preencher (ver variáveis abaixo)
docker compose -f docker-compose.dev.yml up -d redis
python -X utf8 init_db.py   # cria/migra schema + admin default (admin@multpel.com.br / admin123)
python -X utf8 server.py    # http://localhost:5000
pytest -q                   # 242 passam, 3 falham (fixture de data — não é regressão)
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
**A branch publica em `:teste-fusao`** (a `main` moveria `:latest`, que é a produção antiga).

```bash
# 1) redeploy da stack de validação (painel)
docker service update \
  --image ghcr.io/jogasolucoesempresarias-debug/multpelhtlm:teste-fusao \
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
- **Imagem `:multi-fonte`** — o CI (`deploy.yml`) builda a branch `feat/multi-fonte` nessa tag.
- **SEM `POWERBI_*` de propósito** (a demo não tem como tocar o BI do cliente). `CRON_HABILITADO=false`
  (sem emails). `SECRET_KEY` e senha do Postgres **exclusivas** da demo. **1 banco** `joga_demo` serve
  analytics **e** auth (as tabelas não colidem).
- **Login:** `admin@multpel.com.br / admin123`. Acompanhar os logs do serviço `demo-seed` até
  **`[bootstrap] DEMO PRONTA`** (durante o seed, o login falha — é o passo `init_db` que cria as tabelas de auth).
- Base reprodutível (SEED=42) em `_seed_demo/`; runbook local (fumaça no navegador) em
  **`_seed_demo/FUMACA_DEMO.md`**; seeder de metas com **trava** (`DEMO_SEED=1` + recusa `multpel_db`).

⚠️ **Deploy antes do build terminar** → serviços em erro de *pull* de `:multi-fonte`. Espere o
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
  `/estoque/api/pedidos`, etc. (25 rotas).
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
   O baseline é **242 passam / 3 falham**.
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
