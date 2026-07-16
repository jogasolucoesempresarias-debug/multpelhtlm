# Multpel Analytics

Painel comercial analítico para atacado Multpel — consome dados em tempo real do **Power BI** (modelo Totvs Oracle) e entrega dashboards executivos, análise RFM da carteira, drill 360° de clientes, ranking de vendedores e relatórios automatizados por email.

Construído como solução SaaS multi-tenant com RBAC (admin / supervisor / vendedor / viewer), cache Redis em camadas e queries DAX customizadas para alinhamento **centavo-a-centavo** com o RCA do ERP.

---

## ✨ Features

### Dashboards
- **Dashboard executivo** — KPIs do mês (venda líquida, lucro, margem c/ 2 casas, ticket médio, mix, clientes positivados, novos, valor/kg) + série temporal 12m + **YoY recalculado RCA com % nas barras** + **Top 10 departamentos** e **Top 10 vendedores** por lucro (tabelas, com drill) + top 10 clientes por lucro. **Filtro multi-supervisor** (admin/viewer)
- **Carteira RFM** — 8 segmentos canônicos (Campeões, Fiéis, Em Risco, Não Perder, Promissores, Novos, Inativos, Perdidos) com chart de receita+positivação 12m + drill mensal contextualizado + drill 360° por cliente. Export CSV/PDF com nome pelos filtros ativos
- **Vendedores** — ranking com YoY, taxa de positivação (vs carteira oficial), distribuição de produtividade, cockpit individual
- **Categorias** — treemap de departamentos (tamanho=venda, cor=margem) + top fornecedores + drill de clientes por depto
- **Mix abandonado** — clientes que pararam de comprar um depto há X dias; **clique no cliente → top 5 departamentos perdidos** (painel lateral); **export CSV da lista completa**
- **Tendências** — cohort retention heatmap (M+0 a M+12) com **filtros de vendedor e supervisor** (cascata supervisor→vendedor)
- **Metas** — réplica das 4 telas META do cliente (Venda / Rentabilidade / Clientes / Mix): **meta (alvo) × realizado × projeção** por supervisor + total, com **drill de vendedores**, gauge, KPIs, série diária de venda e **necessidade/dia** em dias úteis. A **meta é nossa** (Postgres, por vendedor/mês; editor admin com sugestão automática e importação do BI); o **realizado vem de um 2º dataset Power BI** (dataset META — pedidos `PCPEDC/PCPEDI` + calendário `EhDiaMeta`), batendo centavo com as medidas oficiais no mês corrente
- **Admin** — CRUD de usuários (**supervisor multi-área**), cron de email, multi-CC, filtro de segmento RFM (rótulo "Função"), **editor de metas por vendedor** (sugestão `ano_anterior`/`media_3m` + importar do dataset META)

### Relatórios automatizados
- Cron de email (APScheduler) a cada 5min — dispara PDF + CSV filtrado por usuário
- **Supervisor multi-área**: 1 PDF por área (vendedores em ordem alfabética) + 1 CSV combinado; vendedor: 1 PDF ordenado por lucro 12m desc
- Múltiplos destinatários (email principal + até 5 CCs)
- Filtro de segmentos RFM por usuário (vazio = carteira completa); o corpo do email lista os segmentos e as áreas
- Disparo manual via botão Admin

### Escopo por usuário logado (RBAC)
- **Carteira, Categorias, Mix e Tendências** recortam por **CADASTRO** do cliente (`PCCLIENT.CODUSUR1` → vendedor → supervisor), com **números totais** do cliente. Resultado: admin filtrando uma área == supervisor daquela área (mesma régua)
- **Dashboard e Vendedores** recortam por **venda** (`CODSUPERVISOR`/`CODUSUR` na própria transação)
- **Supervisor multi-área** — um usuário pode cuidar de várias áreas (coluna `codsupervisores`); RBAC vira `CODSUPERVISOR IN {...}`
- **Admin/viewer** veem tudo (com filtros livres)

---

## 🛠 Stack

| Camada | Tecnologia |
|---|---|
| Backend | Flask 3.0 + Waitress (WSGI prod) |
| Frontend | HTML + Vanilla JS + Chart.js + CSS dark theme |
| Cache | Redis 7-alpine |
| Database | PostgreSQL 18 (auth + log de uso) |
| BI source | Power BI executeQueries API + DAX |
| Auth Power BI | Service Principal Azure AD |
| Email | Resend API |
| Cron | APScheduler in-process |
| Deploy | Docker Swarm + Traefik (TLS Let's Encrypt automático) |
| Registry | GitHub Container Registry (GHCR) |
| CI/CD | GitHub Actions (build + push imagem em push pra `main`) |

---

## 📁 Estrutura

```
Multpel HTML/
├── server.py              # Backend Flask (~4300 linhas)
├── rfm.py                 # Módulo puro RFM (calcular_clientes, histograma_recencia)
├── cohort.py              # Lógica de cohort retention
├── metas.py               # Módulo puro Metas (projeção, necessidade/dia, sugestão) — só matemática
├── init_db.py             # Migrations Postgres (idempotente)
├── requirements.txt       # Dependências Python
├── Dockerfile             # Imagem prod (python:3.13-slim + waitress)
├── docker-compose.prod.yml # Stack Swarm com Traefik labels
├── docker-compose.dev.yml # Redis local pra dev
├── .env.example           # Template de variáveis (sem secrets)
├── .github/workflows/
│   └── deploy.yml         # Build + push pra GHCR no push main
├── static/
│   ├── drill-cliente.css  # CSS painel lateral compartilhado
│   ├── drill-cliente.js   # JS drill 360° cliente
│   └── fetch-resiliente.js # Fetch com retry/timeout
├── index.html             # Dashboard executivo
├── carteira.html          # Carteira RFM + drill mensal
├── vendedores.html        # Ranking vendedores
├── vendedor.html          # Cockpit individual
├── categorias.html        # Treemap deptos
├── mix.html               # Mix abandonado
├── tendencias.html        # Cohort heatmap
├── metas.html             # Painel de metas (4 métricas + drill + editor admin)
├── admin.html             # CRUD usuários
├── login.html             # Tela de login
├── trocar-senha.html      # Reset 1º acesso
└── tests/                 # pytest suite (~76 testes)
```

---

## 🔧 Setup local (dev)

### 1. Pré-requisitos
- Python 3.13+
- Docker (pra Redis em container)
- PostgreSQL local OU acesso a um Postgres remoto

### 2. Configurar `.env`

Copia `.env.example` pra `.env` e preenche:

```env
SECRET_KEY=<gerar com: python -c "import secrets; print(secrets.token_hex(32))">

# Postgres local
DB_HOST=localhost
DB_PORT=5432
DB_NAME=multpel_db
DB_USER=postgres
DB_PASSWORD=<sua senha>

# Redis (via docker-compose.dev.yml)
REDIS_HOST=localhost
REDIS_PORT=6379

# Power BI Service Principal (Azure AD)
POWERBI_TENANT_ID=...
POWERBI_CLIENT_ID=...
POWERBI_CLIENT_SECRET=...
POWERBI_GROUP_ID=...
POWERBI_DATASET_ID=...
POWERBI_META_DATASET_ID=...  # 2º dataset (telas de Meta); default embutido no server.py

# Resend (pra testar email)
RESEND_API_KEY=re_...
RESEND_FROM=seu@email.com
CRON_HABILITADO=false  # safety net em dev
```

### 3. Subir Redis local

```bash
docker compose -f docker-compose.dev.yml up -d redis
```

### 4. Inicializar banco

```bash
python -X utf8 init_db.py
```

Cria/migra tabelas `multpel_users` (inclui colunas de cron, `email_cc`, `segmentos_rfm` e **`codsupervisores`** JSONB p/ supervisor multi-área), **`multpel_metas`** (alvo por vendedor/mês — `UNIQUE(ano,mes,codusur)`) e `multpel_log` + admin default (`admin@multpel.com.br` / `admin123`). **Idempotente** — rode também após cada deploy que mexa no schema.

### 5. Subir servidor

```bash
python -X utf8 server.py
```

Acessa `http://localhost:5000`.

### 6. Rodar testes

```bash
pytest -q
```

~76 testes cobrindo auth, RBAC (venda + cadastro multi-área), RFM, cohort, endpoints, dax, cache, exports. _(1 teste de cohort falha por data fixa no fixture — não é regressão.)_

---

## 🚀 Deploy produção

**Stack-alvo**: Docker Swarm com Traefik (TLS Let's Encrypt automático), Portainer pra gestão, Postgres compartilhado.

### Fluxo de deploy

```
Local                  GitHub                    GHCR              Servidor
─────                  ──────                    ────              ────────
git push main  ─────►  Actions roda deploy.yml
                       ↓
                       docker build
                       ↓
                       push pra ghcr.io/.../multpelhtlm:latest ──►  (imagem disponível)
                                                                    ↓
                                                                    Portainer ou CLI:
                                                                    docker pull + service update
```

### Primeira vez

1. **PostgreSQL**: criar database `multpel_db` no Postgres do servidor
2. **DNS Cloudflare**: CNAME `multpel.seudominio.com.br` → IP servidor (DNS only)
3. **Portainer**: criar stack com `docker-compose.prod.yml`, preencher env vars
4. **Inicializar tabelas**:
   ```bash
   docker exec $(docker ps -q -f name=multpel_multpel-app) python -X utf8 init_db.py
   ```

### Updates futuros

```bash
# No servidor (ajuste os nomes de serviço/container ao seu Swarm — confira com `docker service ls`):
docker pull ghcr.io/jogasolucoesempresarias-debug/multpelhtlm:latest
docker service update --image ghcr.io/jogasolucoesempresarias-debug/multpelhtlm:latest --force multpel_multpel-app

# Migration (idempotente) — OBRIGATÓRIA se a release mexeu no schema (ex.: coluna codsupervisores).
# Sem ela o login pode quebrar (o SELECT de login passou a incluir colunas novas).
docker exec $(docker ps -q -f name=multpel_multpel-app) python -X utf8 init_db.py

# Limpar caches se houver mudança em query DAX/cálculo ou bump de chave de cache
docker exec $(docker ps -q -f name=multpel-redis) redis-cli FLUSHDB
```

---

## 🔒 Arquitetura RBAC

Existem **duas réguas** de isolamento, por natureza diferente:

### 1. Por VENDA (Dashboard, Vendedores, Categorias/Mix agregados)
A função `aplicar_rbac_dax()` (+ gêmeas `rbac_devol_dax()`/`rbac_devol_av_dax()` p/ devolução) injeta um fragmento DAX no FILTER conforme o role logado:

| Role | Fragmento DAX |
|---|---|
| `admin` / `viewer` | (vazio) — vê tudo |
| `vendedor` | `FATURAMENTO_VENDAS[CODUSUR] = X` |
| `supervisor` (multi-área) | `FATURAMENTO_VENDAS[CODSUPERVISOR] IN {a, b, c}` |

O supervisor pode ter **várias áreas** (`codsupervisores`, lido por `_session_supervisores()`). A sessão guarda a lista + o 1º elemento em `codsupervisor` (compatibilidade).

### 2. Por CADASTRO (Carteira, Categorias, Mix, Tendências + email)
A carteira é carregada **global** (sem filtro de venda → números totais) e recortada **em Python** pelo gateway **`_carteira_no_escopo()`**, que filtra pelo cliente registrado na área (`PCCLIENT.CODUSUR1` → vendedor → supervisor):

| Role | Recorte |
|---|---|
| `admin` / `viewer` | tudo |
| `vendedor` | clientes com `CODUSUR1 == seu codusur` |
| `supervisor` | clientes cujo `CODUSUR1` pertence a uma de suas áreas |

Assim, **admin filtrando uma área == supervisor daquela área**, cliente por cliente. Para agregados que não dá pra filtrar em memória (Categorias), usa-se `CODCLI IN {...}` com fallback IN/NOT-IN dinâmico. Drills têm guarda de escopo (404 fora do cadastro).

### 3. Metas (por CODUSUR, no 2º dataset)
As telas de Meta rodam contra o **dataset META** (não o RCA). O escopo do usuário é resolvido em `_metas_escopo_codusur()` para um conjunto de `CODUSUR` (vendedor = o próprio; supervisor = vendedores das suas áreas; admin/viewer = tudo ou `?supervisor=`), e injetado como `PCPEDC[CODUSUR] IN {...}` nas queries. **Clientes/Mix são DISTINCTCOUNT** — nunca somados vendedor→supervisor→total; são sempre medidos no grão certo via DAX. O painel só mostra times **com meta cadastrada** (alinha com o BI). No mês corrente o realizado vem das medidas oficiais do META (bate centavo); em mês fechado (o META só guarda o mês atual) cai no dataset RCA como proxy.

---

## 📐 Alinhamento com o ERP (RCA)

Como o RCA do Totvs e o Power BI calculam vendas/lucro de forma sutilmente diferente, o sistema usa **fórmulas customizadas** ao invés das medidas nativas do PBI:

```
Receita Líquida = VENDA BRUTA(DTSAIDA) − TOTAL DEVOLUCAO(DTENT) − TOTAL DEVOLUCAO AVULSA(DTENT)

Lucro Total    = Receita Líquida − (CUSTO TOTAL − CUSTO TOTAL DEVOLUCAO − CUSTO TOTAL DEVOLUCAO AVULSA)
```

**Por que DTENT pra devolução?** O RCA conta a devolução pelo dia em que entrou no estoque, não pela data da venda original. Sem isso, divergência média de 1-2% nos valores.

**Validado**: Sup AFONSO ES-SUL Abr/26 → VL R$ 2.385.853,77 / Lucro R$ 520.326,87 **(bate centavo a centavo com RCA)**.

---

## 🗂 API endpoints principais

### Auth
- `POST /api/login`
- `POST /api/logout`
- `POST /api/trocar-senha`

### Dashboard
_(aceitam `?supervisor=18,19` — multi-supervisor, admin/viewer)_
- `GET /api/dashboard/kpis`
- `GET /api/dashboard/serie?periodo=12m`
- `GET /api/dashboard/yoy` (recalculado RCA 12m vs 12m anterior)
- `GET /api/dashboard/pareto?top=50`
- `GET /api/dashboard/sazonalidade`
- `GET /api/dashboard/top-clientes?metrica=lucro&limit=10`

### Carteira RFM
- `GET /api/carteira/rfm?modo=personalizada`
- `GET /api/carteira/clientes?segmento=X&vendedor=Y&...`
- `GET /api/carteira/receita-positivacao-12m`
- `GET /api/carteira/mes/<anomes>` (drill mensal)
- `GET /api/carteira/cliente/<codcli>` (drill 360°)
- `GET /api/carteira/csv` / `GET /api/carteira/pdf` (exports)

### Vendedores
- `GET /api/vendedores?tipovend=R&supervisor=X`
- `GET /api/vendedor/<codusur>` (cockpit)
- `GET /api/vendedor/<codusur>/serie`
- `GET /api/vendedor/<codusur>/carteira`

### Categorias / Mix / Tendências
- `GET /api/categorias` / `GET /api/categorias/<codepto>/clientes`
- `GET /api/fornecedores?top=50`
- `GET /api/mix/abandonado?dias=60&codepto=&fornecedor=`
- `GET /api/mix/abandonado/<codcli>/deptos?dias=60` (drill: top 5 deptos perdidos do cliente)
- `GET /api/mix/abandonado/csv` (export da lista completa)
- `GET /api/tendencias/cohort?periodo=12m&vendedor=&supervisor=`
- `GET /api/tendencias/cohort/<aquisicao>/<mes_relativo>/clientes` (drill por bucket)

### Metas
_(aceitam `?ano=&mes=` — default mês corrente; `?supervisor=` p/ admin/viewer)_
- `GET /api/metas` (painel: supervisores + total, 4 métricas c/ meta/realizado/projeção)
- `GET /api/metas/vendedores?codsupervisor=X` (drill: vendedores de um time)
- `GET /api/metas/serie` (realizado diário de venda p/ gráfico)

### Internos (datalists)
- `GET /api/_internal/vendedores-map` / `GET /api/_internal/supervisores-map`

### Admin
- `GET /api/admin/users` / `POST` / `PUT /<id>` / `DELETE /<id>`
- `POST /api/admin/enviar-relatorio/<user_id>`
- `GET /api/admin/metas` (lista vendedores p/ edição; `?todos=1` traz a força toda)
- `POST /api/admin/metas` / `POST /api/admin/metas/bulk` (salva 1 meta / lote atômico)
- `GET /api/admin/metas/sugestao?codusur=&metodo=ano_anterior|media_3m&crescimento=`
- `POST /api/admin/metas/importar` (semeia do dataset META; `?todos=1` = todos os meses)

### Sistema
- `GET /health` (liveness check pro Docker/Traefik)

---

## 🧪 Cache Redis (TTLs)

| Tipo de dado | Chave prefix | TTL |
|---|---|---|
| Metadata (vendedores/supervisores/deptos) | `multpel:*_map:*` | 24h |
| **Carteira full (GLOBAL, compartilhada)** | `multpel:carteira:full:global:v2` | 1h |
| Mapas mensais (venda/devolução, GLOBAIS) | `multpel:*_mensal_por_cliente:global:*` | 1h |
| Dashboard agregados | `multpel:dashboard:*` | 30min |
| Drill mensal | `multpel:carteira:mes:*` | 30min |
| Ranking vendedores | `multpel:vendedores:ranking:*` | 1h |
| Cohort (compras GLOBAIS) | `multpel:cohort:compras_global:*` | 24h |
| Metas: realizado/série (por escopo) | `multpel:metas:realizado:*` / `metas:serie:*` | agregado |
| Metas: dias úteis do mês | `multpel:metas:dias:*` | lista |

A carteira/cohort/mapas mensais são **globais** (1 entrada p/ todos) — o recorte por usuário é feito em Python. As chaves dos endpoints agregados incluem o RBAC do usuário (role + codusur + **lista de codsupervisores**) pra não vazar entre escopos.

---

## 📜 Histórico de patches relevantes

| Patch | Data | Mudança |
|---|---|---|
| Ondas A-F | mai/26 | Build inicial: auth, RFM, dashboards, drill, admin, cron email |
| F.3 | 29/05/26 | Receita líquida alinhada RCA (DTSAIDA vs DTENT) |
| F.4 | 29/05/26 | Lucro alinhado RCA |
| F.5 | 29/05/26 | Drill mensal: top 20 produtos + painel mais largo (730px) |
| G | 04/06/26 | Fix RBAC nas devoluções (vendedor/supervisor logado) |
| G (Onda) | 04/06/26 | Deploy produção: Docker Swarm + GHCR + Traefik |
| I | 04/06/26 | Trocar SUM(VLDEVOLUCAO) por [TOTAL DEVOLUCAO] (medida nativa) |
| J | 04/06/26 | Múltiplos destinatários CC no email |
| K | 04/06/26 | Filtro de segmento RFM no envio |
| L | 07/06/26 | Fix taxa de positivação (medida nativa PBI estava bugada) |
| L.2 | 07/06/26 | YoY com janela 365 dias exata |
| M | 09/06/26 | Dashboard: filtro multi-supervisor + YoY recalculado RCA c/ % nas barras |
| M | 09/06/26 | Dashboard: Pareto/Sazonalidade → Top 10 Departamentos/Vendedores (tabelas) |
| M | 09/06/26 | Carteira: nome do PDF/CSV pelos filtros ativos (RFC 5987) |
| M | 09/06/26 | **Supervisor multi-área** (coluna `codsupervisores`, RBAC `IN {...}`, N PDFs por área no email) |
| M | 09/06/26 | Admin: rótulo "Role" → "Função" |
| M | 09/06/26 | **Carteira/Categorias/Mix/Tendências por CADASTRO (CODUSUR1) + números totais** (`_carteira_no_escopo`) |
| M | 09/06/26 | Tendências: filtro de supervisor + cascata; dropdowns só do liberado; limpar filtro |
| M | 09/06/26 | Mix: drill top 5 deptos perdidos + export CSV completo + limpar filtro + busca por código |
| M | 09/06/26 | Email: corpo mostra segmentos/áreas; ordenação de vendedor insensível a acento/maiúscula |
| N | — | **Módulo Metas**: 4 telas META (Venda/Rentab/Clientes/Mix) — meta própria (Postgres `multpel_metas`) × realizado (2º dataset META, DAX centavo-a-centavo) + projeção/necessidade em dias úteis, drill de vendedores, editor admin (sugestão + importar do BI), série diária, `metas.py` puro |

Detalhes completos em `_PROGRESSO.md` (não versionado).

---

## 🤝 Contribuindo

1. Branch a partir de `main`
2. Commits descritivos no padrão `Patch X: <descrição>`
3. PR com testes verdes (`pytest -q`)
4. Após merge em `main`, GitHub Action publica imagem nova em GHCR

---

## 📄 Licença

Privado. Uso interno Multpel + parceiros autorizados.

---

## 📞 Suporte

Em caso de dúvida:
1. Checar logs do container: `docker service logs multpel_multpel-app --tail 200`
2. Validar healthcheck: `curl https://multpel.seudominio.com.br/health`
3. Limpar caches: `docker exec $(docker ps -q -f name=multpel-redis) redis-cli FLUSHDB`
