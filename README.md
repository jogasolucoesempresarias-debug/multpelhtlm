# Multpel Analytics

Painel comercial analítico para atacado Multpel — consome dados em tempo real do **Power BI** (modelo Totvs Oracle) e entrega dashboards executivos, análise RFM da carteira, drill 360° de clientes, ranking de vendedores e relatórios automatizados por email.

Construído como solução SaaS multi-tenant com RBAC (admin / supervisor / vendedor / viewer), cache Redis em camadas e queries DAX customizadas para alinhamento **centavo-a-centavo** com o RCA do ERP.

---

## ✨ Features

### Dashboards
- **Dashboard executivo** — KPIs do mês (venda líquida, lucro, margem, ticket médio, mix, clientes positivados, novos, valor/kg) + série temporal 12m + YoY + Pareto + sazonalidade + top 10 clientes por lucro
- **Carteira RFM** — 8 segmentos canônicos (Campeões, Fiéis, Em Risco, Não Perder, Promissores, Novos, Inativos, Perdidos) com chart de receita+positivação 12m + drill mensal contextualizado
- **Vendedores** — ranking com YoY, taxa de positivação (vs carteira oficial), distribuição de produtividade, cockpit individual
- **Categorias** — treemap de departamentos (tamanho=venda, cor=margem) + top fornecedores
- **Mix** — análise de deptos abandonados (não comprados há X dias)
- **Tendências** — cohort retention heatmap (M+0 a M+12)
- **Admin** — CRUD de usuários, cron de email, multi-CC, filtro de segmento RFM

### Relatórios automatizados
- Cron de email (APScheduler) a cada 5min — dispara PDF + CSV filtrado por usuário
- Múltiplos destinatários (email principal + até 5 CCs)
- Filtro de segmentos RFM por usuário (vazio = carteira completa)
- Disparo manual via botão Admin

### Análises por usuário logado
- **Vendedor** vê só sua carteira (filtrada via RBAC nas 3 tabelas: FAT_VENDAS, FAT_DEVOLUCAO, FAT_DEVOLUCAO_AVULSA)
- **Supervisor** vê todo seu time
- **Admin** vê tudo com filtros livres

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
├── server.py              # Backend Flask (~3900 linhas)
├── rfm.py                 # Módulo puro RFM (calcular_clientes, histograma_recencia)
├── cohort.py              # Lógica de cohort retention
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
├── admin.html             # CRUD usuários
├── login.html             # Tela de login
├── trocar-senha.html      # Reset 1º acesso
└── tests/                 # pytest suite (54 testes)
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

Cria tabelas `multpel_users`, `multpel_log` + admin default (`admin@multpel.com.br` / `admin123`).

### 5. Subir servidor

```bash
python -X utf8 server.py
```

Acessa `http://localhost:5000`.

### 6. Rodar testes

```bash
pytest -q
```

54 testes cobrindo auth, RBAC, RFM, cohort, endpoints, dax, cache.

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
# No servidor:
docker pull ghcr.io/<owner>/multpelhtlm:latest
docker service update --image ghcr.io/<owner>/multpelhtlm:latest --force multpel_multpel-app

# Limpar caches se houver mudança em query DAX/cálculo
docker exec $(docker ps -q -f name=multpel-redis) redis-cli FLUSHDB
```

---

## 🔒 Arquitetura RBAC

A função [`aplicar_rbac_dax()`](server.py#L216) injeta fragmento DAX no FILTER conforme role do usuário logado:

| Role | Fragmento DAX |
|---|---|
| `admin` | (vazio) — vê tudo |
| `vendedor` | `FATURAMENTO_VENDAS[CODUSUR] = X` |
| `supervisor` | `FATURAMENTO_VENDAS[CODSUPERVISOR] = Y` |
| `viewer` | (vazio) — read-only |

Funções gêmeas `rbac_devol_dax()` e `rbac_devol_av_dax()` aplicam o mesmo filtro nas tabelas de devolução (Patch G).

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
- `GET /api/dashboard/kpis`
- `GET /api/dashboard/serie?periodo=12m`
- `GET /api/dashboard/yoy`
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
- `GET /api/categorias`
- `GET /api/categorias/<codepto>/clientes`
- `GET /api/fornecedores?top=50`
- `GET /api/mix/abandonado?dias=60`
- `GET /api/tendencias/cohort?periodo=12m`

### Admin
- `GET /api/admin/users` / `POST` / `PUT /<id>` / `DELETE /<id>`
- `POST /api/admin/enviar-relatorio/<user_id>`

### Sistema
- `GET /health` (liveness check pro Docker/Traefik)

---

## 🧪 Cache Redis (TTLs)

| Tipo de dado | Chave prefix | TTL |
|---|---|---|
| Metadata (vendedores/supervisores/deptos) | `multpel:*_map:*` | 24h |
| Carteira full | `multpel:carteira:full:*` | 1h |
| Dashboard agregados | `multpel:dashboard:*` | 30min |
| Drill mensal | `multpel:carteira:mes:*` | 30min |
| Ranking vendedores | `multpel:vendedores:ranking:*` | 1h |
| Cohort | `multpel:cohort:full:*` | 24h |

Chaves incluem RBAC do usuário (role+codusur+codsupervisor) pra cache não vazar entre roles.

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
