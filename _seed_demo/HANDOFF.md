# HANDOFF — Produtização multi-fonte do JOGA Analytics (Multpel HTML)

> Documento de passagem pra outro chat continuar. Lê isto INTEIRO antes de mexer.
> Memórias relacionadas (já carregadas no contexto de qualquer chat via MEMORY.md):
> `multpel_demo_base.md`, `multpel_rca_medidas_reconstruidas.md`, `multpel_tabelas_dax.md`.

---

## 1. OBJETIVO (o "porquê")

O app JOGA Analytics (cliente = Multpel) hoje lê **tudo** do Power BI do cliente via DAX. Estamos
produtizando pra atender **3 tipos de cliente Winthor** com a MESMA base de código, **sem forkar** e
**sem alterar 1 centavo** do que a Multpel entrega hoje:

| Cenário do cliente | Como atendemos | Config |
|---|---|---|
| BI COM as medidas (a Multpel) | invoca as medidas do modelo dele | `powerbi` + `cliente` |
| BI SEM as medidas | levamos a reconstrução das medidas | `powerbi` + `joga` |
| Só banco (sem BI) | lemos SQL do banco analítico | `postgres` |

**Dois eixos de config (env vars por instância), não 3 caminhos de código:**
- `DATA_SOURCE` = `powerbi` (default) | `postgres`
- `MEDIDAS` = `cliente` (default) | `joga`

A matemática pura do app (`rfm.py`, `cohort.py`, `estoque/core.py`) é **agnóstica de fonte** e fica
**intacta** — os dois caminhos entregam linhas pra ela.

## 2. DECISÕES DO SÓCIO (jbinaildesigner / Gabriel) — respeitar

- Trabalhar na branch **`feat/multi-fonte`** (repo `Multpel HTML`, base era `feat/fusao-estoque`).
- **NADA de commit** ainda — merge só depois da validação completa. (Já pediu explícito.)
- Entrega **faseada com gate por tela** (número bate antes de dar a tela como pronta).
- **Multpel intocada:** o caminho `powerbi`+`cliente` (o default) tem que sair byte a byte igual.
  Todos os branches são `if CONFIG['data_source']=='postgres'` / `if CONFIG['medidas']=='joga'` →
  no default NUNCA rodam.
- Demo = **sintético calibrado nas distribuições** do real (nada de dado real do cliente viaja).
- Havia sensibilidade com a **assinatura da Multpel** (apresentação "semana que vem"). Por isso o
  isolamento total. Confirmar com ele antes de qualquer deploy/promoção.

## 3. AMBIENTE / COMO RODAR

- **Dir do app:** `c:\Phyton-Projetos\Multpel HTML`
- **Python (venv):** `c:\Phyton-Projetos\Multpel HTML\.venv\Scripts\python.exe` (tem psycopg2, requests,
  dotenv, flask, fakeredis, pytest). Rodar com `-X utf8` (Windows).
- **`.env`** (na raiz do app): credenciais Power BI (Service Principal) + Postgres (`DB_*`).
  ⚠️ Tem o `POWERBI_CLIENT_SECRET` em texto — NÃO vazar/colar; se exposto, rotacionar no Azure AD.
- **Banco de auth do app:** Postgres `DB_NAME` (default `multpel_db`) — tabelas `multpel_users`, `multpel_log`, etc.
- **Banco analítico da DEMO:** Postgres **`joga_demo`** (mesmo servidor). Criado por `_seed_demo/setup_db.py`.
  Em modo postgres o provider lê dele via env `ANALYTICS_DB_NAME` (default já é `joga_demo`).
- **Client BI (pra reverse-engineering / modo joga):** dataset **`RCA`** id `f2fbf288-611a-4b17-aeb3-a6f77ef04e3b`,
  workspace/grupo `84fbf8f2-688a-4d52-a5ae-543520fbbd27`. Dataset META `801d7d87-...`. Dataset Estoque `32fb60e1-...`.
- **Testes:** `cd "c:/Phyton-Projetos/Multpel HTML" && ./.venv/Scripts/python.exe -m pytest -q` (~4min).
  **Baseline: 194 passam / 4 falham** = 3 conhecidas (radar/mix/cohort, fixture de data, no README) +
  1 FLAKY (`test_carteira_export_nome` — "Popped wrong request context", teardown do Flask, pula de
  teste a cada run). NENHUMA é regressão nossa (rodam em `powerbi`+mock, que nossos branches não tocam).
- **Rodar 1 script de teste/sonda:** `& ".venv\Scripts\python.exe" -X utf8 "_seed_demo\<script>.py"`.

## 4. FASE 1 — "BI sem medidas" (MEDIDAS=joga) — ✅ COMPLETA (13/13 medidas)

**Arquivo central: `medidas_dax.py`** (na raiz do app). Tem `RECONSTRUCOES` (dict token→expressão DAX)
e `reconstruir_medidas(query)` — um **post-processor** que troca os tokens `[VENDA BRUTA]` etc. pela
expressão em coluna crua. É aplicado no executor SÓ quando `MEDIDAS=='joga'`:
- `server.py` `execute_dax()`: `if CONFIG['medidas']=='joga': query = reconstruir_medidas(query)`
- `estoque/pbi.py` `_execute()`: idem (import defensivo).
Em `cliente` (default) nada é chamado → DAX idêntico. Não editamos as ~65 ocorrências inline (de propósito).

**As 13 medidas reconstruídas** (chave: coluna `CODOPER` segmenta o fato; `S`=venda, `SB`=bonificação,
`ST`/`SR`=excluídas). Detalhe completo na memória `multpel_rca_medidas_reconstruidas.md`. Resumo:
- **VENDA BRUTA** = `SUM(VLVENDA − ICMSRETIDO − VLFECP)` where `CODOPER='S'` (99,99%; cauda de ST ~0,01%)
- **CUSTO TOTAL** = `SUMX(VLCUSTOFIN+VLCUSTOFINBONIF)` where `CODOPER IN ('S','SB')` (exato)
- **TOTAL/CUSTO DEVOLUCAO** = `SUM(VLDEVOLUCAO)/SUM(VLCUSTOFIN)` excl `CODATIV=37 AND CODDEVOL<>9` (exato)
- **TOTAL/CUSTO DEVOLUCAO AVULSA** = `SUM(VLDEVOLUCAO)/SUM(VLCUSTOFIN)` (exato)
- **VENDA LIQUIDA / LUCRO TOTAL** = compostas das 6
- **TOTAL CLIENTES NOVO** = `DISTINCTCOUNT(CODCLI)` where `CODOPER='S'` (exato)
- **TOTAL MIX** = `SUMX` por dia de `DISTINCTCOUNT(CODPROD)` where `VLCUSTOFINB>0 && CONDVENDA<>10` (EXATO)
- **TICKET MEDIO** = `ROUND(AVERAGEX(FILTER(FV,CODOPER='S'), VLVENDA-ICMSRETIDO-VLFECP),2)` (~0,6%)
- **TAXA POSITIVACAO CLIENTE** = `ROUND(DIVIDE(DISTINCTCOUNT(CODCLI 'S'), COUNT(CODCLI CONDVENDA<>10),0),2)` (exato)
- **VALOR MEDIO PESO** = `VENDA LIQUIDA / (SUM(PESOBRUTO CONDVENDA<>10) − SUM(FD.TOTPESO CONDVENDA<>10) − SUM(FA.TOTPESO))` (~1,8%)

As defs das auxiliares (MIX/TICKET/PESO/POSITIVACAO) vieram do **.pbix do cliente** (o Gabriel copiou do
Power BI Desktop) — pela nossa conexão não davam (INFO.VIEW.MEASURES traz Expression=null; INFO.MEASURES cru=400).

**Gate Fase 1 (verde):** `tests/test_medida_compat.py` (5/5); pytest baseline; BI real `cliente×joga`
(custo exato, venda −0,003%). Scripts de RE em `_seed_demo/`: probe*/compare_medidas/diagnose*/validate_final*/test_*_recon/localizar_medidas.

## 5. FASE 2 — modo BD (DATA_SOURCE=postgres) — 🔄 EM ANDAMENTO

**Arquivo central: `provider_sql.py`** (raiz do app). Lê do banco analítico (`analytics_conn()` →
`ANALYTICS_DB_*` com fallback `DB_*`, dbname default `joga_demo`) e devolve as MESMAS formas do caminho DAX.
Helpers-espelho: `periodo_sql(tipo)` (= `filtro_periodo`), `escopo_where(rbac, tab)` (= `aplicar_rbac_dax`;
rbac dict `{role, codusur, supervisores}`), `resultado(cur,d0,d1,rbac)` (alinhamento RCA). Medidas em SQL:
`VB`, `CT`, `DEV`, `CDEV` (constantes no topo).

**Padrão de fiação (importante):** interceptar nos "**pontos-mãe**" (funções por onde muitos endpoints
passam), e SEMPRE extrair o pós-processamento compartilhado pra um helper, deixando o caminho DAX intocado.

**Telas JÁ LIGADAS + gateadas (verde):**
| Tela | Ponto de branch em server.py | Provider | Reuso |
|---|---|---|---|
| Dashboard KPIs | `api_dashboard_kpis` | `dashboard_kpis(rbac)` | — |
| Dashboard série | `api_dashboard_serie` | `serie_mensal(rbac,d0,d1)` | — |
| Dashboard sazonalidade | `_carregar_sazonalidade` | `sazonalidade(rbac)` | — |
| Dashboard pareto | `api_dashboard_pareto` | `pareto_clientes(rbac,top)` | — |
| Dashboard YoY | `api_dashboard_yoy` | `yoy_dashboard(rbac)` | — |
| Carteira (~10 endpoints) | `_carregar_carteira_full` → helper `_finalizar_carteira` | `carteira_dados()` | **rfm.py** |
| Vendedores | `_carregar_ranking_vendedores` → helper `_montar_ranking_vendedores` | `ranking_vendedores_dados(rbac)` | — |
| Dimensões (nomes) | `_carregar_vendedores_map`, `_carregar_supervisores_map` | `vendedores_map()/supervisores_map()` | — |
| Categorias | `api_categorias` (DAX envolto em `else`) + `_carregar_deptos_map`→`{}` | `categorias_dados(rbac,codclis)` | — |
| Tendências (cohort) | `_carregar_cohort_compras_global` | `cohort_compras(periodo)` | **cohort.py** |
| Top-clientes | `api_dashboard_top_clientes` | `top_clientes(rbac,metrica,limit)` | — |
| Metas (painel+drill+série) | `_carregar_metas_realizado`, `_carregar_dias_uteis_meta`, `api_metas_serie` | `metas_realizado(ano,mes,escopo)`, `metas_serie` | **metas.py** |
| Mix abandonado (+2 drills) | `_mix_abandonado_rows`, `api_mix_cliente_deptos`, `_mix_cliente_fornecedores_rows` | `mix_abandonado_raw`, `mix_cliente_deptos_raw`, `mix_cliente_fornecedores_raw` | — |
| Radar (busca/board/produto/cliente) | `_carregar_produtos_map`, `_radar_board_full`, `_radar_detalhe_rows`, `_radar_cliente_rows` | `produtos_map`, `radar_board`, `radar_produto_detalhe`, `radar_cliente_raw` | — |

**Âncora de data (modo BD):** `provider_sql.hoje_analitico()` = `max(dtsaida)` do joga_demo (override
`ANALYTICS_HOJE=YYYY-MM-DD`). Usado em `periodo_sql` e em todas as janelas relativas (12m, recente×anterior,
mês corrente) + espelhado nos poucos `hoje = _date.today()` do server nas telas Mix/Radar (só em postgres),
pra o "parado há X dias" bater com a janela SQL. Mantém a demo viva sem regenerar o fato. DAX/powerbi usa TODAY() (intocado).

**Calibrações de Metas na demo:** o realizado da meta vem de `pcpedc` (venda por `vlatend`, clientes distinct);
**rentabilidade(lucro R$) e mix vêm de `faturamento_vendas`** — o `pcpedc` da demo não tem custo (`vlcustofin`=NULL)
nem `codprod` (grão=pedido) e a `pcpedi` está vazia. Coerente (mesmo mês/escopo). `posicao` é toda 'F' na demo.
`proj_venda=None` → `metas.linha_metrica` recalcula o run-rate. Dias úteis = seg-sex (sem feriado).

**Seeder de metas da demo:** `_seed_demo/seed_metas_demo.py` popula `multpel_metas` no **auth DB da DEMO**
(meta = 0.95×realizado → atingimento ~105%). **Trava:** exige `DEMO_SEED=1` e RECUSA se `DB_NAME=='multpel_db'`
(produção). Sem seed, o painel mostra só o realizado (fallback). Não é pré-requisito dos gates.

**Helpers novos no server.py:** `_rbac_sql()` (session→dict pro provider), `_finalizar_carteira(clientes,key)`,
`_montar_ranking_vendedores(atual,anterior_idx,metricas_idx,carteira_idx,key)`. `_meta_refresh_tag()` retorna
tag estática `'postgres'` em modo BD (não bate no dataset PBI).

**Gates:** `tests/test_provider_dashboard.py` (4: kpis, subendpoints, top-clientes, default-powerbi),
`tests/test_provider_comercial.py` (4: carteira, vendedores, categorias, cohort), `tests/test_provider_metas.py`
(3), `tests/test_provider_mix.py` (2), `tests/test_provider_radar.py` (2). Todos passam. Autenticam via
`login_as` + `usuario_admin` (conftest cria user no `multpel_db`), monkeypatch `CONFIG['data_source']='postgres'`
+ `_R.flushall()`. **Baseline completo: 203 passam / 4 falham** (as mesmas 3 conhecidas mix/radar/cohort de
fixture-de-data + 1 flaky de teardown Flask; nenhuma é regressão nossa — rodam em powerbi+mock).

## 6. O QUE FALTA

### Fase 2 — Comercial ✅ COMPLETA
top-clientes, Metas (painel+drill+série), Mix e Radar ligados e gateados (ver tabela na seção 5). O que
ficou de fora do Comercial, **de propósito**, virou gate de Fase 4:
- **Metas — Admin (sugestão) + meses fechados:** `api_admin_metas_sugestao` e `_realizado_rca_mes` ainda leem
  do dataset RCA via DAX (quebram em modo postgres). Decisão do sócio: baixo valor de demo (sugestão é config;
  mês fechado é histórico) → portar depois. Provider: sugestão = histórico do `pcpedc`/faturamento por codusur;
  meses fechados = mesma `metas_realizado` (o `pcpedc` da demo tem histórico 2024→hoje).

### Fase 3 — Compras (blueprint /estoque) — 🔄 EM ANDAMENTO (Inc.1 ✅)
O módulo Compras (`estoque/`) puxa **tabelas cruas** (island tables) via DAX (`estoque/queries.py`) e faz a
conta em Python (`estoque/core.py`). Executor: `estoque/pbi.py` `run_dax` / `run_dax_rca`. Pro modo BD, um
provider devolve as MESMAS chaves (o `core.py` roda intacto). Protótipos de SQL em `_seed_demo/compras_sql.py`.

**Inc.1 — Cockpit/Cobertura + Abastecimento + Ruptura ✅ (ligado + gateado):**
- **`estoque/provider_sql.py`** (NOVO): 9 funções espelhando os `q_*` do estoque, MESMAS chaves que o core lê
  (aliases-armadilha reproduzidos: `qtbloqueada→qtbloq`, `qtpendente→qtpend`, `qtvendmes1..3→giro_m1..3`; datas
  ISO string, números float). Reusa `provider_sql.analytics_conn()` da raiz.
- **`estoque/pbi.py`**: `CONFIG['data_source']` (env DATA_SOURCE) + `get_dataset_refresh`→None em postgres.
- **`estoque/routes.py`**: branch `if pbi.CONFIG['data_source']=='postgres'` nos loaders de estoque
  (`_snapshot_rows`/`_endereco_map`/`_cadastro_produtos`/`_cadastro_fornecedores`/`_compradores_map`/
  `_embalagem_map`/`_pedidos_data`/`_posicoes_map`/`_filiais_disponiveis`). **RCA branchado pra `{}` explícito**
  (`_vendas_liquidas`→cobre `_vendas_map`/`_vendas_ano_ant_map`; `_preco_venda_map`; `_vendas_mensal_map`;
  `_vendas_mensal_rs_map`; `_desempenho_data`) — NÃO confiar no degrade-por-exceção (o `.env` da demo pode ter
  credencial PBI válida → run_dax_rca puxaria dado REAL do cliente).
- Gate `tests/test_provider_estoque.py` (3): filtros, snapshot+cockpit (contagens reais), cross-check loader→core
  (giro/qtdisp/cobertura não-nulos). Login concede área 'compras' via session_transaction.
**Inc.2 — venda por produto (RCA→joga_demo) ✅ (Cockpit cheio):** `estoque/provider_sql.vendas_por_produto(ini,
fim,filiais)` = {cod:{venda,custo,qtd}} líquida (VB/CT/qtd − devoluções − avulsa, medidas importadas da raiz).
Branch em `_vendas_liquidas` (cobre `_vendas_map` + `_vendas_ano_ant_map`) e `_preco_venda_map`. venda/lucro/
margem, curva ABC e por-comprador reais no Cockpit (venda 30d ~R$2,0M, margem ~15,5%).

**Inc.3 — Desempenho + séries mensais (360°/forecast) ✅:** provider `receita_comprador`/`devol_comprador`/
`venda_comprador_periodo` (→ `_desempenho_data`→`core.desempenho_comprador`) + `vendas_mensal_qt`(key **AM**)/
`venda_produto_mensal`+`devol_produto_mensal`(key **AnoMes**) (→ `_vendas_mensal_map`/`_vendas_mensal_rs_map`).
Aba Desempenho real (8 compradores, positivação/margem/YoY) e o gráfico 12m do drawer 360°.
- **🛡️ Rede de segurança (importante p/ o rollout incremental):** `estoque/pbi._execute` agora **levanta
  RuntimeError em modo postgres** — qualquer loader ainda NÃO branchado (telas de Inc.4) falha alto e degrada
  pra vazio (via os try/except) em vez de vazar dado REAL do cliente no BI. Gate cobre isso.

**Inc.4 — telas island (Validade/FEFO, Vencidos, Ocupação/WMS, Lead time, Verbas, drawer 360°, exports) ✅:**
+16 funções no `estoque/provider_sql.py` (validade/prox_venc/lotes_produto/desc_de/vencidos/produto_enderecos/
ocupacao_kpis/ocupacao_por_rua/ocupacao_por_tipo/ocupacao_vazias/rua_itens/pedido_entrada/verbas/verba_aplic/
pedido_itens_um + venda/devol_comprador_mensal). Branch por `_pg()` em TODOS os call sites `run_dax`/`run_dax_rca`
das rotas (validade/vencidos/resumos/produto/rua/ocupacao/leadtime/verbas + exports + drills). Gate hita as 5
telas (ok=True, dados reais). **Fase 3 essencialmente COMPLETA em modo BD** (orçamento usa store no auth DB +
_vendas_map branchado; exports reusam os loaders branchados). `ocupacao_vazias`=0 na demo (posições 'O' têm
estoque) — resultado válido, não erro.

Obs: `estoque/routes._hoje()` usa `date.today()` (não ancorado no max(dtsaida) como o Comercial) — ok hoje, tunar se a demo envelhecer.
Falta só **Fase 4** (endurecimento): RBAC SQL por papel, Metas Admin sugestão + meses fechados, nomes de depto,
fumaça no navegador + amostra centavo-a-centavo no BI. Fórmulas em `docs/estoque/planilha_v3.md` e memória `multpel_estoque_v3.md`.

### Fase 4 — endurecimento + validação final — 🔄 (código ✅, falta só validação manual)
- **RBAC SQL por papel ✅:** `tests/test_provider_rbac.py` (3) prova o recorte end-to-end via sessão —
  vendedor (codusur real 213) e supervisor (codsupervisor 14) recortam < total e batem centavo com o provider
  direto; supervisor sem área → venda 0. (Os codusur do conftest são 573/inexistentes → o teste injeta um real
  via `session_transaction`.) O recorte por CADASTRO (carteira/mix/radar via `_carteira_no_escopo`) já é
  agnóstico de fonte (filtra a lista em Python nos 2 modos).
- **Metas Admin (sugestão) ✅:** `api_admin_metas_sugestao` branchado → `provider_sql.metas_sugestao_historico(codusur)`
  (histórico mensal do faturamento, proxy que o admin ajusta). **Meses fechados JÁ funcionavam** (o branch postgres
  do `_carregar_metas_realizado` roda antes do ramo `_realizado_rca_mes`; o `pcpedc` tem 2024→hoje). Gate em `test_provider_metas.py`.
- **Nomes de depto ✅:** `provider_sql.deptos_map_sintetico()` (nomes temáticos de atacadista por codepto, 33
  únicos) ligado no `_carregar_deptos_map` — Categorias/Mix/Radar mostram nome real em vez de "Depto N".
- **Fumaça FEITA (nível HTTP, instância viva) ✅ 23/23.** 📄 Roteiro: `_seed_demo/FUMACA_DEMO.md` (cria auth
  `multpel_demo`, libera áreas, seeder, sobe `DATA_SOURCE=postgres`, checklist tela a tela). Achados corrigidos:
  1. **Vendedores zerava** — demo gerava `tipovend='1'`, tela filtra 'R' por padrão → `gerar.py` agora 'R'
     (+ UPDATE no joga_demo vivo; gate `?tipovend=R`).
  2. **Drawer 360° da Carteira vazio** — `api_carteira_cliente` era endpoint da Fase 2 **não branchado** →
     `provider_sql.carteira_cliente_detalhe` + helper `_finalizar_carteira_cliente` + gate.
  3. **🛡️ Rede de segurança na RAIZ:** `server.execute_dax` levanta RuntimeError em modo postgres (igual à do
     estoque). Com ela, um **sweep HTTP de TODOS os endpoints** (server vivo) revelou que a Fase 2 tinha **10
     endpoints/drills SEM branch** (davam 500/vazariam): carteira/receita-positivacao-12m, carteira/mes,
     carteira/evolucao, carteira/cliente/produtos, vendedor/<id>(+/serie), categorias/<depto>/clientes, marcas,
     fornecedores, radar/produto/<p>/cliente/<c>/serie. **Todos branchados** (providers no provider_sql) + gate
     `test_comercial_endpoints_sweep_modo_postgres`. Sweep final: **38/38 comerciais + estoque = 200/ok**. Após
     patchar dado vivo: `_R.flushall()`. "Qualidade da base" zerada = esperado (base limpa). O sweep é o cheque
     definitivo — "Fase X completa" só depois dele, não só das telas principais.
- **FALTA (manual, precisa do BI do cliente):** **amostra centavo-a-centavo** no `powerbi+cliente` (antes/depois)
  — prova de que os branches não mexeram no caminho Multpel. Só então liberar o merge.

## 7. A BASE SINTÉTICA (joga_demo) — como (re)gerar

Pasta `_seed_demo/` (isolada, NÃO vai pra imagem do cliente). Ordem:
1. `setup_db.py` — cria o banco `joga_demo` + aplica `schema.sql` (23 tabelas Winthor).
2. `gerar.py` — dimensões (7k clientes c/ perfil RFM, 3,8k produtos c/ margem/ABC, 114 vend., 245 forn., calendário).
3. `gerar_fato.py` — fato de vendas (~1,17M linhas) + pcpedc (metas, 203k) + devoluções (46k). Streaming COPY.
4. `gerar_estoque.py` — 12 tabelas de Compras (pcest c/ giro do fato, WMS, pedidos em aberto, verbas, vencidos).
Calibrado por `perfil.py` (SEED=42, reprodutível). Molde estatístico do real por `extract_molde.py`.
Validadores: `validate_demo.py` (Comercial), `validate_estoque.py` (Compras). Protótipos SQL das telas:
`app_sql.py`, `rfm_sql.py`, `comercial_sql.py`, `compras_sql.py`.

Calibragens conhecidas (tunar quando quiser): base com ~40% "perdidos" e YoY negativo (churn embutido no perfil);
vendedores com `tipovend='1'` (real usa 'R'); orçamento realizado > meta; devol avulsa com codusur nulo.

## 8. ARQUIVOS TOCADOS/CRIADOS (na branch feat/multi-fonte)

**Criados (raiz app):** `medidas_dax.py`, `provider_sql.py`.
**Criados (tests):** `test_medida_compat.py`, `test_provider_dashboard.py`, `test_provider_comercial.py`.
**Editados (cirúrgico):** `server.py` (CONFIG +2 flags, +import provider_sql/medidas_dax, +helpers `_rbac_sql`/
`_finalizar_carteira`/`_montar_ranking_vendedores`, +branches nas telas acima), `estoque/pbi.py` (CONFIG 'medidas'
+ import + branch no `_execute`).
**Intactos:** `rfm.py`, `cohort.py`, `estoque/core.py`, `estoque/queries.py`, toda a lógica de negócio.
**Tooling (não vai pro cliente):** todo o `_seed_demo/`.

## 9. COMO CONTINUAR (receita p/ cada tela nova)

1. Ler a rota (ou a função-mãe) no `server.py`, achar o contrato de saída e o ponto onde as `rows`/estruturas
   são montadas a partir do DAX.
2. Escrever a função no `provider_sql.py` que devolve as MESMAS estruturas do `joga_demo` (RBAC via `escopo_where`,
   período via `periodo_sql`). Reusar `rfm.py`/`cohort.py`/`core.py` sempre que a lógica pura já existir.
3. No `server.py`, adicionar `if CONFIG['data_source']=='postgres':` no ponto-mãe (após o cache check), extraindo
   o pós-processamento compartilhado pra um helper se necessário. O caminho DAX fica idêntico (no default nunca roda).
4. Gate: teste em `tests/test_provider_*.py` (monkeypatch data_source=postgres, login admin, bate a rota, confere
   estrutura+coerência). Rodar `import server` pra pegar erro de sintaxe cedo.
5. ⚠️ **Contrato frontend↔provider** (ver [[multpel-smoke-contrato-frontend]]): se o provider monta a **resposta
   inteira** (ex.: `dashboard_kpis`), o gate tem que assertar o **shape INTERNO**, não só a presença das chaves de
   topo — e cruzar com as chaves que o `.html`/JS lê (grep dos `d.campo`). Bug real pego assim: `dashboard_kpis`
   devolvia `yoy_mes={atual,anterior,variacao}` mas o front lê `yoy_mes.receita_liquida/lucro_bruto/...` (4 pct do
   `_yoy_parse`) → badges YoY em branco. Corrigido (helpers `_yoy4`/`_janelas_yoy_mes`/`_yoy_mes_info`). Provider
   **data-feed** (alimenta pós-processamento do server) não tem esse risco: a resposta é montada igual nos 2 modos.
6. Ao fim de cada bloco, rodar o baseline completo (`pytest -q`) e confirmar 4 falhas conhecidas/flaky, nada novo.

Nunca commitar sem o ok do Gabriel. Merge só na validação final da Fase 4.
