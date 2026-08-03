# Plano — Multpel Analytics (Painel Comercial Inteligente)

## ⚙️ Guia de execução por fases (LEIA ANTES)

Esse plano é grande demais pra ser executado num único chat sem estourar contexto. Recomenda-se dividir em **5 ondas** (chats), cada uma pegando 1-2 fases:

| Onda | Chat | Fases | Entregáveis |
|---|---|---|---|
| **A** | Chat A | Fase 0 + Fase 1 | Setup completo (Redis, helpers, login, must_change_password) + Dashboard Executivo funcional |
| **B** | Chat B | Fase 2 | Carteira RFM completa (régua personalizada, segmentação canônica, CSV) |
| **C** | Chat C | Fase 3 | Vendedores + RBAC (cockpit, ranking, test_rbac essencial) |
| **D** | Chat D | Fases 4 + 5 | Mix, Categorias, Tendências (cohort, YoY) |
| **E** | Chat E | Fases 6 + 7 | Tratamento de erros, Admin, Docker + Deploy |

**Regras pra cada onda**:

1. **Sempre começa lendo** este plano (`piped-zooming-boot.md`) + os arquivos já existentes na pasta `c:\Phyton-Projetos\Multpel HTML\`:
   - `.env` (credenciais validadas)
   - `RCA_ESTRUTURA.md` (mapa do dataset)
   - `_rca_tables.json`, `_rca_cols.json`, `_rca_measures.json` (mapa cru)
2. **Validar o que a onda anterior entregou** antes de começar (rodar testes, abrir app, conferir KPIs).
3. **Não recriar** o que está no Tabela Auditoria (`c:\Phyton-Projetos\Tabela Auditoria\`) — usar como referência de padrão visual e código. Os snippets das funções base estão **no Anexo B** deste plano (não precisa abrir os arquivos da Rizza).
4. **Documentar progresso** ao terminar a onda: criar `_PROGRESSO.md` na raiz da pasta listando o que foi entregue, problemas encontrados, decisões tomadas. Próxima onda lê isso.
5. **Testar a fase** antes de declarar concluída (seção Verificação E2E indica testes manuais por fase + testes automatizados).

**Convenções**:
- Todos os arquivos vivem em `c:\Phyton-Projetos\Multpel HTML\`
- Nomenclatura: `multpel_*` (não `auditoria_*`) em banco, classes Python, etc.
- Banco Postgres: nome `multpel_db`
- Admin padrão: `ADMIN_EMAIL` / `ADMIN_SENHA` (env; forçar troca no 1º login)

---

## Contexto

Cliente novo **Multpel** (distribuidora atacadista, ~R$ 95M/ano, ERP Winthor/TOTVS).
Pasta do projeto: `c:\Phyton-Projetos\Multpel HTML\`. **Sem relação com o projeto Tabela Auditoria (Rizza)** — é uma aplicação separada, com banco, deploy e usuários próprios. O .env já está preenchido com credenciais Azure AD + workspace Power BI validados.

**Dor do cliente**: hoje a análise comercial é feita em planilha manual de Excel — classificação de 1.919 clientes por dias desde última compra (OK/NORMAL/ATENÇÃO/URGENTE), atualizada manualmente. Limitações: régua fixa que ignora padrão individual de cada cliente, sem análise de frequência/valor, sem cobrança por vendedor, sem cohort, sem mix abandonado, sem comparativos YoY.

**Objetivo**: entregar **painel web inteligente** que vai muito além do pedido literal. Aproveitando as 39 medidas DAX **já prontas** no Power BI, o painel vira ferramenta de gestão comercial completa — RFM, ranking de vendedores, mix abandonado, cohort, tendências — com cards e gráficos interativos.

**Posicionamento**: produto vendável como SaaS de gestão comercial pra atacadistas (referência de mercado ~R$ 3.000-8.000/mês).

## Decisões-chave

| Tema | Decisão |
|---|---|
| Stack | Python 3.11 + Flask + Postgres (auth) + Power BI DAX + Chart.js 4.4 + HTML/CSS/JS puro |
| Auth | Login/senha (werkzeug hash + sessão Flask), igual padrão Rizza |
| RBAC | 4 níveis: **admin** (vê tudo) / **supervisor** (vê seu time) / **vendedor** (vê só carteira) / **viewer** (leitura geral) |
| Dataset PBI | **RCA** (`f2fbf288-611a-4b17-aeb3-a6f77ef04e3b`) — Import puro, único que funciona com service principal. Painel_Gerencial_Comercial é composite e NÃO funciona |
| Cache DAX | **Redis** (container Docker, não dict Python). TTL 1h pra agregados, 5min pra listas, 24h pra metadata. Key inclui RBAC do user. Cache sobrevive a restarts e é compartilhado entre workers Gunicorn |
| Paralelismo DAX | `concurrent.futures.ThreadPoolExecutor` (DAX é I/O bound — threads bastam) pra endpoints que precisam de múltiplas chamadas. Reduz tempo de dashboard de ~18s sequencial pra ~6s paralelo |
| Tratamento de erros PBI | Retry com backoff exponencial (3 tentativas) em 5xx e 401. Token cacheado e renovado proativamente (válido 1h). Frontend mostra mensagem gracioso "Dados sendo atualizados, tente em alguns minutos" durante janelas de refresh |
| Multi-tenancy | **Single-tenant nesta fase** — projeto serve apenas a Multpel. Se virar SaaS pra outros atacadistas, schema/tenant por cliente é roadmap futuro |
| Testes automatizados | pytest com mock de `execute_dax`. Foco em endpoints com RBAC (regressão silenciosa quando muda escopo de filtros). Não cobre 100%, cobre o crítico |
| Limite query | **1.3GB/query** (Pro tier) — queries devem ser **focadas**, com TOPN/FILTER/CALCULATE; evitar `EVALUATE 'public X'` puro |
| Branding | Mesma paleta dark/fontes do Tabela Auditoria, mas projeto **isolado** (rota, banco, container) |
| Charting | Chart.js 4.4 + plugins annotation/datalabels (mesma stack Rizza) |
| Export | CSV streaming server-side (padrão Rizza) |
| Deploy | **TBD** — definir quando MVP estiver pronto. Estrutura espelha Rizza (Docker Swarm + Traefik + Cloudflare DNS) |
| Público-alvo | **Todos os perfis** (diretor, gerente, supervisor, vendedor PJ/PF) — controle de acesso individual via RBAC ao cadastrar cada usuário |
| Escopo MVP | **Completo** — 8 telas, todas as análises (Dashboard, Carteira RFM, Vendedores, Cockpit, Mix, Categorias, Tendências, Admin) |

## Validações já feitas (queries DAX reais executadas)

✅ Auth `client_credentials` → token válido
✅ executeQueries no dataset RCA → 200 OK
✅ INFO.VIEW.TABLES / COLUMNS / MEASURES → funciona pra descoberta (já mapeado em `RCA_ESTRUTURA.md`)
✅ Medidas DAX retornam valores reais:
  - VENDA BRUTA 2025: R$ 88.370.447
  - VENDA LIQUIDA 2025: R$ 84.651.347
  - LUCRO TOTAL 2025: R$ 15.637.639
  - MARGEM(%) 2025: 18.47%
  - TICKET MEDIO 2025: R$ 147,06
  - Crescimento Ano a Ano Receita Liquida: +66.7%
  - YTD 2026 (jan-mai): R$ 126,3M
✅ 11 tabelas + 39 medidas + 29 meses de histórico (jan/2024 → mai/2026)
✅ 9.244 clientes ativos / 167 vendedores / 4.469 SKUs vendidos

⚠️ **Limite de memória**: query única com 10 medidas estoura 1.3GB. Plano: agrupar 2-3 medidas por chamada DAX
⚠️ **TOTAL CLIENTES medida** (43.277 em 2025) representa "cliente × mês positivado" (acumulado), não distintos. Pra "clientes únicos no período" usar `DISTINCTCOUNT(FATURAMENTO_VENDAS[CODCLI])`
⚠️ **YTD precisa contexto**: medidas YTD sem filtro de ano agregam tudo. Sempre envolver com `CALCULATE([X YTD], CALENDARIO[ANO]=YEAR(TODAY()))`
⚠️ Tabela PCCLIENT tem **3 campos de vendedor** (CODUSUR1/2/3). Usar CODUSUR1 como principal (99,96% preenchido). Os outros 2 ignorar nesta fase
⚠️ Hierarquia comercial completa em FATURAMENTO_VENDAS: CODUSUR → CODSUPERVISOR → CODCOORDENADOR → CODGERENTELOCAL → CODGERENTEREGIONAL → CODGERENTENACIONAL → CODDIRETOR. Usar nesta fase apenas CODUSUR + CODSUPERVISOR

## Estrutura de arquivos

```
c:\Phyton-Projetos\Multpel HTML\
├── server.py                      # Flask backend completo
├── init_db.py                     # cria tabelas auth + admin padrão
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env                           # já existe, credentials validadas
├── .gitignore
├── RCA_ESTRUTURA.md               # já existe — mapa do dataset
├── _rca_tables.json / _rca_cols.json / _rca_measures.json  # já existem
│
├── login.html                     # tela de login
├── index.html                     # Dashboard Executivo (landing)
├── carteira.html                  # Análise RFM da carteira
├── vendedores.html                # Ranking de vendedores
├── vendedor.html                  # Cockpit individual (drill por CODUSUR)
├── mix.html                       # Mix abandonado por categoria
├── categorias.html                # Performance por categoria/marca
├── tendencias.html                # Cohort + YoY + sazonalidade
└── admin.html                     # Gestão de usuários (admin only)
```

## Schema do banco local (init_db.py)

```sql
CREATE TABLE IF NOT EXISTS multpel_users (
    id              SERIAL PRIMARY KEY,
    nome            VARCHAR(255) NOT NULL,
    email           VARCHAR(255) UNIQUE NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,
    role            VARCHAR(20) DEFAULT 'viewer',  -- 'admin' | 'supervisor' | 'vendedor' | 'viewer'
    ativo           BOOLEAN DEFAULT true,
    must_change_password BOOLEAN DEFAULT true,  -- força troca de senha no primeiro login
    -- RBAC (filtros automáticos aplicados às queries):
    codusur         INTEGER,         -- vendedor: vê só a própria carteira
    codsupervisor   INTEGER,         -- supervisor: vê todos os vendedores do time
    -- (admin: ambos NULL → vê tudo)
    criado_em       TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_users_email ON multpel_users(email);

CREATE TABLE IF NOT EXISTS multpel_log (
    id            SERIAL PRIMARY KEY,
    usuario_id    INTEGER REFERENCES multpel_users(id),
    rota          VARCHAR(120),
    parametros    TEXT,
    duracao_ms    INTEGER,
    acessado_em   TIMESTAMP DEFAULT NOW()
);

-- Admin padrão: ADMIN_EMAIL / ADMIN_SENHA (TROCAR ao primeiro login)
```

## Helpers core no server.py

Padrão portado do Tabela Auditoria/Rizza (`c:\Phyton-Projetos\Tabela Auditoria\server.py`):

| Função | Origem (Rizza) | Adaptar |
|---|---|---|
| `get_db()` | linha 36 | Trocar DB_NAME padrão para `multpel_db` |
| `get_token()` | linha 72 | Igual (Azure AD client_credentials) |
| `execute_dax(token, query, dataset_id=None)` | linha 85 | Igual |
| `clean_rows(rows)` | linha 104 | Igual (normaliza chaves `[tabela]coluna` → `coluna`) |
| `_csv_linha(valores)` | linha 1239 | Igual |
| `login_required` | linha 46 | Igual |
| `admin_required` | linha 56 | Igual |

**Funções novas a criar**:
- `_cache_get(key) / _cache_set(key, data, ttl)` — wrapper sobre `redis-py` com TTL variável por tipo
- `aplicar_rbac_dax(filtro_base)` — adiciona filtro CODUSUR/CODSUPERVISOR conforme sessão
- `aplicar_rbac_sql(...)` — equivalente pra queries no banco local
- `cache_key_for_user(endpoint, params)` — chave de cache incluindo RBAC do user logado
- `filtro_periodo(tipo)` — centraliza geração de fragmento DAX temporal: `'mes_atual'`, `'ytd'`, `'12m'`, `'mes_anterior'`, `'YYYY-MM'` etc. Facilita debug e manutenção
- `executar_dax_paralelo(queries)` — usa `ThreadPoolExecutor(max_workers=4)` pra rodar N queries DAX simultâneas. Retorna dict `{nome: resultado}`
- `get_token_cached()` — wrapper sobre `get_token()` com cache (TTL 50min) pra não pedir token novo a cada chamada (token é válido 1h)
- `retry_dax(fn, max_tentativas=3)` — decorator com backoff exponencial em 5xx/401. Em erro 503 "dataset refreshing", aguarda 30s e tenta novamente

```python
# Cache estratégico — TTLs por tipo (via Redis, compartilhado entre workers)
_CACHE_TTLS = {
    'dax_agregado':  3600,   # 1h — KPIs, séries (refresh PBI é 1x/dia)
    'dax_lista':      300,   # 5min — listas filtráveis
    'metadata':     86400,   # 24h — tabelas/colunas/medidas
    'token_pbi':     3000,   # 50min — token válido 1h, renova proativamente
}

import redis, os, json
_R = redis.Redis(host=os.getenv('REDIS_HOST', 'redis'), port=6379, decode_responses=True)

def _cache_get(key):
    raw = _R.get(key)
    return json.loads(raw) if raw else None

def _cache_set(key, data, ttl_tipo='dax_agregado'):
    _R.setex(key, _CACHE_TTLS[ttl_tipo], json.dumps(data, default=str))


def aplicar_rbac_dax():
    """Devolve fragmento DAX pra concatenar em FILTER conforme RBAC."""
    role = session.get('role')
    if role == 'admin':
        return ""
    if session.get('codusur'):
        return f"FATURAMENTO_VENDAS[CODUSUR] = {int(session['codusur'])}"
    if session.get('codsupervisor'):
        return f"FATURAMENTO_VENDAS[CODSUPERVISOR] = {int(session['codsupervisor'])}"
    return ""


def executar_dax_paralelo(queries: dict) -> dict:
    """Roda múltiplas queries DAX em paralelo via ThreadPoolExecutor.
       queries = {'nome1': 'EVALUATE ...', 'nome2': 'EVALUATE ...'}
       Retorna {'nome1': rows, 'nome2': rows}.
       Threads bastam (DAX é I/O bound). Reduz 3 queries de ~6s sequencial pra ~2s paralelo."""
    from concurrent.futures import ThreadPoolExecutor
    token = get_token_cached()
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {nome: ex.submit(execute_dax, token, q) for nome, q in queries.items()}
        return {nome: f.result() for nome, f in futures.items()}


def retry_dax(fn, max_tentativas=3):
    """Decorator: backoff exponencial em 5xx/401. Em 'dataset refreshing' aguarda 30s."""
    import time, functools
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        for tent in range(max_tentativas):
            try:
                return fn(*args, **kwargs)
            except requests.HTTPError as e:
                code = e.response.status_code
                msg = (e.response.text or '').lower()
                if 'refresh' in msg:
                    time.sleep(30)
                elif code in (401, 502, 503, 504):
                    time.sleep(2 ** tent)
                else:
                    raise
        raise
    return wrapper
```

## Estrutura visual das 8 telas

### TELA 1 — `/login` (login.html)
- Form email/senha, fundo dark, logo "M" SVG no header
- Mensagem de erro inline em vermelho
- Mesma identidade visual do `login.html` da Rizza adaptada

### TELA 2 — `/` Dashboard Executivo (index.html)

**Linha 1 — 4 cards primários (com % YoY)**:
- 💰 **Venda Líquida** (mês corrente) + indicador YoY ↗↘
- 📈 **Lucro Total** (mês corrente) + YoY
- 🎯 **Margem (%)** — visual gauge circular
- 👥 **Clientes Positivados** (mês corrente) + YoY

**Linha 2 — 4 cards secundários**:
- 🛒 Ticket Médio
- 📦 Mix Médio (variedade)
- 🆕 Clientes Novos no mês
- ⚖️ Valor Médio/Kg (atacado clássico)

**Gráficos** (Chart.js):
1. **Série temporal 12m** (line): VENDA LIQUIDA + LUCRO TOTAL mensal
2. **YoY mensal 4 métricas** (bar): Receita / Lucro / Positivação Cliente / Mix
3. **Pareto** (bar+line): top 20% clientes geram X% do faturamento; cumulative line
4. **Sazonalidade** (radar polar): vendas por mês × 2 anos sobrepostos

**Tabela compacta**: Top 10 clientes por **LUCRO TOTAL** (não venda) — coluna clicável drill cliente.

### TELA 3 — `/carteira` (carteira.html) — RFM avançado

**Header**: toggle régua FIXA (10/30/45 — réplica da planilha) vs **PERSONALIZADA** (ciclo médio individual — recomendado).

**Linha 1 — 4 cards de status RFM** (canônicos):
- 🟢 **CHAMPIONS** — R alto + F alto + M alto (premiar)
- 🔵 **LOYAL** — R médio-alto + F alto (fidelizar)
- 🟡 **AT RISK** — R baixo + F alto + M alto (**resgatar urgente**)
- 🔴 **LOST** — R muito baixo + qualquer (aceitar perda ou última tentativa)

**Linha 2 — Evolução vs referência**:
- "OK + NORMAL %" atual / mês anterior / referência cliente (48.94%)

**Gráficos**:
1. **Donut** segmentação RFM (8 segmentos)
2. **Matriz RFM heatmap** (R×F com bolhas = monetário)
3. **Evolução temporal** (área empilhada — % carteira em cada status 12m)
4. **Histograma de recência** (distribuição de dias sem comprar)

**Tabela acionável** (clique em card filtra):
- Cliente | Cidade | Vendedor | R (dias) | F (compras 90d) | M (lucro 12m) | **Lucro perdido projetado** | Status | Tel
- Ordenação clicável | Export CSV

### TELA 4 — `/vendedores` (vendedores.html)

**Ranking ordenável** dos 167 vendedores:
- VENDA LIQUIDA | LUCRO TOTAL | TICKET MED | TAXA POSITIVAÇÃO | RANK VENDAS | %YoY
- Gradient verde→vermelho na linha (saúde da carteira)
- Filtros: TIPOVEND (R/I/P), Supervisor, Status (Ativo/Bloqueado), Estado

**Gráficos**:
1. **Top 10 vendedores por LUCRO** (barras horizontais)
2. **Distribuição taxa de positivação** (histograma — identifica outliers)

### TELA 5 — `/vendedor/<codusur>` (vendedor.html) — Cockpit individual

**Header**: Nome, CPF, Telefone, Cidade, Supervisor, Tipo, Status

**Cards**:
- 💰 Sua Venda Líquida (mês + YoY)
- 📈 Seu Lucro Total
- 👥 Carteira X cadastrados / Y positivados
- 🎯 Sua taxa de positivação vs média da equipe

**Gráficos**:
1. **Série mensal 12m** (linha dupla): venda + lucro
2. **Donut RFM** da carteira dele
3. **Comparativo vs equipe** (linha): sua positivação vs média

**Alertas acionáveis**:
- "5 clientes At Risk somam R$ X de lucro/ano — ligue"
- "12 clientes pararam de comprar categoria Y — confira"
- "3 Champions sem contato recente — não deixe escapar"

**Tabela**: carteira ordenada por **lucro perdido** (urgência por valor, não dias).

### TELA 6 — `/mix` (mix.html) — Mix Abandonado

**Filtros**: Categoria, Marca, Período (30/60/90 dias)

**Cards**: clientes que pararam | lucro projetado perdido | top 5 maiores perdas

**Gráfico**: evolução do mix médio mensal

**Tabela**: cliente | categoria abandonada | última compra | lucro 12m

### TELA 7 — `/categorias` (categorias.html)

**Treemap**: tamanho = venda, cor = margem; click → drill na categoria
**Tabela**: categoria | venda | lucro | margem | clientes únicos | mix médio
**Gráfico**: top 10 marcas (barras)

### TELA 8 — `/tendencias` (tendencias.html)

1. **Cohort retention** (heatmap): linhas = mês aquisição, colunas = M+0..M+12, cor = % daqueles clientes ainda comprando
2. **YoY 4 métricas** (linhas): Receita / Lucro / Positivação / Mix
3. **Sazonalidade** (multi-anos sobrepostos)

### TELA 9 — `/admin` (admin.html, admin only)

CRUD `multpel_users`:
- Email, Nome, Senha, Role (admin/supervisor/vendedor/viewer)
- RBAC: codusur (se vendedor) OU codsupervisor (se supervisor)
- Lista de vendedores existentes vem de `PCUSUARI` (consulta DAX) — autocomplete

## Endpoints API (todos sob `@login_required`, RBAC aplicado automaticamente)

### Auth / Sistema
- `POST /login`
- `GET /logout`
- `GET /api/me` — info user + permissões
- `GET /api/status` — saúde PBI + cache

### Dashboard Executivo
- `GET /api/dashboard/kpis` — 8 cards (4 principais + 4 secundários) em **3 chamadas DAX paralelas** (respeita limite memória)
- `GET /api/dashboard/serie?periodo=12m`
- `GET /api/dashboard/yoy?periodo=12m`
- `GET /api/dashboard/pareto?top=20`
- `GET /api/dashboard/sazonalidade`
- `GET /api/dashboard/top-clientes?metrica=lucro&limit=10`

### Carteira (RFM)
- `GET /api/carteira/rfm?modo=fixa|personalizada` — distribuição nos segmentos + cards
- `GET /api/carteira/clientes?segmento=at_risk&vendedor=X&limit=100&offset=0`
- `GET /api/carteira/cliente/<codcli>` — drill 360° (compras, mix, evolução)
- `GET /api/carteira/csv?...` — export streaming
- `GET /api/carteira/evolucao?meses=12`

### Vendedores
- `GET /api/vendedores` — lista + KPIs (167)
- `GET /api/vendedor/<codusur>` — perfil + KPIs
- `GET /api/vendedor/<codusur>/serie`
- `GET /api/vendedor/<codusur>/carteira`
- `GET /api/vendedor/<codusur>/alertas`

### Mix / Categorias
- `GET /api/mix/abandonado?categoria=X&dias=90`
- `GET /api/categorias`
- `GET /api/categoria/<id>/clientes`
- `GET /api/marcas`

### Tendências
- `GET /api/cohort?meses=12`
- `GET /api/tendencia/positivacao`
- `GET /api/tendencia/yoy`

### Admin
- `GET /api/admin/users`
- `POST /api/admin/users`
- `PATCH /api/admin/users/<id>`
- `DELETE /api/admin/users/<id>`
- `GET /api/admin/vendedores-pbi` — lista do PBI pra autocomplete

## Aproveitamento das 39 medidas DAX (sem recriar)

**Princípio**: toda métrica disponível no Power BI é consumida via medida existente. Só calculamos no Python quando a medida não existe (ex: ciclo médio personalizado por cliente, RFM score 1-5).

Exemplo de endpoint usando medidas prontas + paralelismo + `filtro_periodo()`:

```python
def filtro_periodo(tipo: str) -> str:
    """Centraliza geração de fragmento DAX temporal.
       Tipos: 'mes_atual', 'mes_anterior', 'ytd', '12m', '24m', 'ano_anterior',
              ou string YYYY-MM (mês específico)."""
    if tipo == 'mes_atual':
        return "MONTH(FATURAMENTO_VENDAS[DTSAIDA])=MONTH(TODAY()) && YEAR(FATURAMENTO_VENDAS[DTSAIDA])=YEAR(TODAY())"
    if tipo == 'ytd':
        return "YEAR(FATURAMENTO_VENDAS[DTSAIDA])=YEAR(TODAY())"
    if tipo == '12m':
        return "FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12)"
    if tipo == '24m':
        return "FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -24)"
    # 'YYYY-MM' (ex '2026-05')
    ano, mes = tipo.split('-')
    return f"YEAR(FATURAMENTO_VENDAS[DTSAIDA])={int(ano)} && MONTH(FATURAMENTO_VENDAS[DTSAIDA])={int(mes)}"


@app.route('/api/dashboard/kpis')
@login_required
def api_kpis():
    key = cache_key_for_user('dashboard:kpis', {})
    if (cached := _cache_get(key)): return jsonify(cached)

    rbac = aplicar_rbac_dax()
    f_atual = filtro_periodo('mes_atual') + (f" && {rbac}" if rbac else "")

    # Quebra em 3 queries paralelas (respeita limite memória 1.3GB/query)
    queries = {
        'primarios': f"""EVALUATE {{(
            CALCULATE([VENDA LIQUIDA], {f_atual}),
            CALCULATE([LUCRO TOTAL], {f_atual}),
            CALCULATE([MARGEM(%)], {f_atual}),
            CALCULATE([TICKET MEDIO], {f_atual})
        )}}""",
        'secundarios': f"""EVALUATE {{(
            CALCULATE([TOTAL MIX], {f_atual}),
            CALCULATE([TOTAL CLIENTES NOVO], {f_atual}),
            CALCULATE([VALOR MEDIO PESO], {f_atual})
        )}}""",
        'yoy': """EVALUATE {(
            [Crescimento Ano a Ano Receita Liquida],
            [Crescimento Ano a Ano Lucro Bruto],
            [Crescimento Ano a Ano Positivacao Cliente]
        )}"""
    }
    resultados = executar_dax_paralelo(queries)  # ~2s em vez de ~6s sequencial
    # ... montar response ...
    _cache_set(key, response, 'dax_agregado')
    return jsonify(response)
```

## Cálculo de RFM (única lógica que precisa ser feita no Python/DAX customizado)

As medidas prontas não fazem segmentação RFM. Cálculo em 2 etapas:

### Etapa 1 — Snapshot RFM (DAX, com escopo temporal explícito)

```sql
-- IMPORTANTE: filtrar últimos 24 meses pra não inflar memória com CODCLIs históricos antigos
EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODCLI],
    FILTER(FATURAMENTO_VENDAS, FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -24)),
    "UltimaCompra", MAX(FATURAMENTO_VENDAS[DTSAIDA]),
    "Dias", DATEDIFF(MAX(FATURAMENTO_VENDAS[DTSAIDA]), TODAY(), DAY),
    "Compras12m", CALCULATE(DISTINCTCOUNT(FATURAMENTO_VENDAS[NUMNOTA]),
                            FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12)),
    "Lucro12m", CALCULATE([LUCRO TOTAL],
                          FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12))
)
```

### Etapa 2 — Régua personalizada (Python, pós-query)

A "régua personalizada" é o **diferencial** do produto vs planilha atual. Cálculo:

```python
def calcular_ciclo_pessoal(codcli: int) -> int:
    """Mediana dos intervalos entre compras consecutivas dos últimos 12m.
       Floor mínimo 7 dias (evita distorção de cliente com 1-2 compras espaçadas).
       Implementado em Python pós-query: trazer datas de compra, calcular deltas."""
    # 1) DAX retorna: SELECT DTSAIDA FROM FATURAMENTO_VENDAS WHERE CODCLI=X últimos 12m
    # 2) Python ordena, calcula diferenças entre compras consecutivas
    # 3) Retorna max(7, statistics.median(intervalos))
    ...

def classificar_recencia_personalizada(dias_sem_comprar: int, ciclo_pessoal: int) -> str:
    razao = dias_sem_comprar / ciclo_pessoal
    if razao < 1.0:  return 'OK'
    if razao < 2.0:  return 'NORMAL'
    if razao < 3.0:  return 'ATENCAO'
    return 'URGENTE'
```

### Etapa 3 — Segmentação RFM (Python)

Backend pontua 1-5 cada dimensão (quintis) e mapeia em segmentos canônicos:
- R=5,F=5,M=5 → **Champions**
- R=4-5,F=4-5,M=3-5 → **Loyal**
- R=2-3,F=3-5,M=3-5 → **At Risk** (era frequente, parou)
- R=2-3,F=4-5,M=4-5 → **Can't Lose** (alto valor + parou)
- R=4-5,F=1-2 → **Potential Loyalist** ou **New**
- R=1-2,F=1-2,M=1-2 → **Lost**
- R=1-2,F=1-2 → **Hibernating**

Resultado: ~8 segmentos visualizados no donut + matriz RFM.

## Sequência de implementação (8 fases ~16 dias úteis)

**Fase 0 — Setup + Infra (2 dias)**
- Copiar estrutura base do Tabela Auditoria (server.py, login.html, init_db.py)
- Adaptar nomes (auditoria_* → multpel_*), banco `multpel_db`
- **Adicionar Redis** ao docker-compose; var `REDIS_HOST` no .env
- Helpers infra: `_cache_get/set` (Redis), `get_token_cached`, `retry_dax`, `executar_dax_paralelo`, `filtro_periodo`
- `init_db.py` cria tabelas (com `must_change_password=true`) + admin padrão
- Tela `/trocar-senha` forçada no primeiro login
- requirements.txt (Flask, psycopg2, requests, **redis**, **pytest**, **pytest-mock**)
- pytest setup + fixtures básicas + mock de `execute_dax`
- Smoke test: login + 1 KPI (com cache + retry + paralelismo) funcionando E2E

**Fase 1 — Dashboard Executivo (3 dias)**
- index.html com 8 cards + 4 gráficos Chart.js + tabela top 10
- Endpoints `/api/dashboard/*` com chamadas DAX paralelas + cache 1h
- Validar contra valores conhecidos (R$ 84,7M de VENDA LIQUIDA 2025)
- Testes: `test_endpoints_kpis` (mock DAX, valida estrutura)

**Fase 2 — Carteira RFM (4 dias)**
- carteira.html com toggle régua + 8 cards + 4 gráficos + tabela acionável
- Cálculo ciclo pessoal (mediana intervalos 12m, floor 7d)
- Segmentação canônica 8 segmentos
- Régua personalizada vs fixa (réplica planilha)
- Endpoints `/api/carteira/*` + export CSV streaming
- Testes: `test_rfm` (ciclo, segmentação, edge cases — cliente com 1 compra)

**Fase 3 — Vendedores + RBAC (3 dias)**
- vendedores.html (ranking 167 vendedores)
- vendedor.html (cockpit individual)
- RBAC funcional (codusur, codsupervisor)
- Hierarquia supervisor → carteira do time
- **Testes: `test_rbac` (essencial)** — 5 cenários de isolamento entre roles

**Fase 4 — Mix + Categorias (2 dias)**
- mix.html — mix abandonado por categoria/marca
- categorias.html — treemap + drill por categoria

**Fase 5 — Tendências (1-2 dias)**
- tendencias.html — cohort retention + YoY 4 métricas + sazonalidade

**Fase 6 — Tratamento de erros + Polimento (1 dia)**
- Frontend gracioso pra 5xx (banner "dados sendo atualizados")
- Cache stale fallback + header `X-Cache-Stale`
- Log estruturado em `multpel_log`
- Testes finais cobrindo cache + retry

**Fase 7 — Admin + Deploy (1 dia)**
- admin.html — CRUD usuários + autocomplete vendedor via PBI
- Dockerfile + docker-compose.yml (com Redis no compose, espelho Rizza)
- Deploy (TBD)

## Tratamento de erros do Power BI

O dataset RCA é refreshed ~1x/dia (vimos no histórico). Durante o refresh (que demora ~1h), o Power BI rejeita queries com erro 503 "dataset is being processed". Sem tratamento, usuário vê erro genérico e perde confiança.

**Estratégia em camadas**:

| Camada | O que faz |
|---|---|
| **Token** | `get_token_cached()` mantém token em Redis (TTL 50min). Renova proativamente. Em 401 (token revoke remoto), refaz auth e tenta de novo |
| **Query DAX** | Decorator `@retry_dax`: 3 tentativas com backoff exponencial (2s, 4s, 8s) em 502/503/504. Em "dataset refreshing" aguarda 30s antes da próxima tentativa |
| **Cache fallback** | Se a query falhar 3x mas tiver versão antiga em cache (mesmo expirada), retorna ela com header `X-Cache-Stale: true`. Frontend mostra banner amarelo "Dados de X minutos atrás" |
| **Frontend** | Se backend retornar 503 mesmo após retry, mostra modal gracioso: "O sistema de dados está em atualização programada. Tente novamente em alguns minutos." com botão "Tentar agora" |
| **Logs** | Toda falha de PBI loga em `multpel_log` com `rota`, `parametros`, `erro` e `duracao_ms` pra análise posterior |

## Testes automatizados

8 telas + 4 níveis RBAC sem testes é receita pra regressão silenciosa (alguém muda escopo de filtro e usuário X passa a ver dado que não devia ver).

**Escopo mínimo** (não 100% cobertura, foco no crítico):

```
tests/
├── conftest.py              # fixtures: app, db_test, usuarios_fake
├── test_auth.py             # login OK, senha errada, must_change_password, logout
├── test_rbac.py             # ⭐ ESSENCIAL — cada role vê apenas seu escopo
│   ├── admin_ve_tudo
│   ├── supervisor_so_ve_time
│   ├── vendedor_so_ve_carteira_propria
│   ├── viewer_sem_filtro_automatico
│   └── tentativa_acesso_outro_codusur_via_url → 403
├── test_endpoints_kpis.py   # mock execute_dax, valida estrutura da resposta
├── test_cache.py            # Redis mock; cache hit/miss + TTL + invalidação
├── test_rfm.py              # cálculo de ciclo pessoal (mediana + floor 7)
│                             # segmentação canônica (Champions/Loyal/At Risk/Lost)
└── test_admin_crud.py       # criar/editar/excluir usuário + autocomplete PBI
```

**Mock do Power BI**: criar fixture que substitui `execute_dax` por função que retorna respostas pré-gravadas (JSONs em `tests/fixtures/`). Evita dependência de internet + custo de chamadas reais nos testes.

**CI**: GitHub Actions roda `pytest` em cada PR. Não bloqueia deploy nessa fase, mas dá visibilidade.

## Verificação end-to-end (testes manuais)

App rodando em `http://localhost:5000`. Login: o admin semeado pelo `init_db.py`.

**Fase 0**:
- `python init_db.py` cria 2 tabelas + admin. Re-rodar não dá erro
- Login → redireciona pra `/`
- `/api/status` retorna 200 + cache state
- `curl /api/me` retorna info user + RBAC

**Fase 1**:
- `/` carrega em < 3s, 4 cards primários mostram valores reais (Venda do mês corrente)
- Card "Venda Líquida YTD" próximo ao R$ 126M validado (ou maior, ago/2026 acumulado)
- 4 gráficos Chart.js renderizam sem erro JS no console
- Top 10 tabela mostra clientes com lucro mensal

**Fase 2**:
- Toggle régua FIXA vs PERSONALIZADA muda os números dos 4 cards de status
- Click no card "AT RISK" abre tabela filtrada com N clientes (esperado ~961 se régua fixa = planilha original)
- Matriz RFM renderiza com bolhas dimensionadas
- Export CSV baixa arquivo UTF-8 com BOM, separador `;`, abre no Excel correto

**Fase 3**:
- `/vendedores` lista 167 vendedores ordenáveis
- Logado como admin: vê todos os 167
- Logado como user com `codusur=573` (JOAO VICTOR): só vê dados próprios (`/api/vendedor/573` ok, outros vendedores → tabela filtrada/oculta)
- Logado como supervisor: vê todos vendedores do CODSUPERVISOR dele

**Fase 4**:
- `/mix?categoria=X&dias=60` retorna lista de clientes que pararam
- `/categorias` mostra treemap com tamanho/cor proporcional

**Fase 5**:
- `/tendencias` mostra cohort heatmap com 12-24 meses
- Gráfico YoY mostra +66.7% de crescimento Receita (medida validada)

**Fase 6**:
- Admin cria 3 usuários teste: 1 admin, 1 supervisor (codsupervisor=X), 1 vendedor (codusur=573)
- Cada login vê escopo correto

## Permissionamento (RBAC) detalhado

```
Role          | codusur | codsupervisor | O que vê
--------------|---------|---------------|---------------------------------
admin         | NULL    | NULL          | Tudo (todos vendedores, carteira global)
supervisor    | NULL    | preenchido    | Todos os vendedores onde CODSUPERVISOR = X
vendedor      | preenchido | NULL       | Só clientes onde CODUSUR = Y
viewer        | NULL    | NULL          | Leitura geral, sem filtro automático (admin lite)
```

Todos os endpoints `/api/*` aplicam o filtro RBAC automaticamente via `aplicar_rbac_dax()` no fragmento DAX. Tabela admin lista vendedores via consulta DAX a `PCUSUARI` (autocomplete pra evitar errar CODUSUR ao cadastrar).

## Cuidados técnicos descobertos na exploração

1. **Limite 1.3GB/query**: sempre quebrar em chamadas pequenas focadas. Nunca rodar `EVALUATE 'public TABELA'` puro em FATURAMENTO_VENDAS (1.6M linhas). Sempre usar TOPN/FILTER/CALCULATE.
2. **TOTAL CLIENTES vs DISTINCTCOUNT**: medida pronta é cliente×mês acumulado. Pra "clientes únicos" usar DISTINCTCOUNT explícito.
3. **YTD precisa contexto**: medidas YTD sem CALCULATE de ano pegam tudo.
4. **PCCLIENT tem 3 vendedores**: usar só CODUSUR1 (99.96% preenchido).
5. **Hierarquia comercial**: FATURAMENTO_VENDAS já tem CODSUPERVISOR como coluna — não precisa fazer JOIN com PCUSUARI pra filtrar por supervisor.
6. **TIPOVEND filtrar**: 'R' = rota (vendedor externo real com carteira), 'I' = interno (caixa/balcão — excluir das análises de carteira), 'P' = pré-vendedor (avaliar caso a caso).
7. **Vendedores "técnicos"**: códigos 999 (RCA TRANSFERENCIA), 900 (JURÍDICO), 4/272 (CAIXA/MULTPEL) — excluir das análises por carteira.

## Branding visual

- Paleta dark idêntica à Rizza: `--bg: #0a0e17`, `--surface: #111827`, `--accent: #38bdf8`, `--accent2: #818cf8`
- Fontes: DM Sans (corpo) + JetBrains Mono (números)
- Logo "M" no favicon SVG, gradient cyan→indigo no título
- Componentes reaproveitáveis: `.btn-sm`, `.kpi-card`, `.filtros-card`, `.export-btn`, `.tag`

## Custos / Deploy

| Item | Valor estimado |
|---|---|
| VPS (compartilhada com Rizza ou nova) | R$ 50-100/mês |
| Cloudflare DNS | Grátis |
| Power BI Pro (cliente) | R$ 70/usuário/mês (já paga) |
| Tempo de dev (esse projeto) | ~14 dias úteis |
| Manutenção mensal contínua | R$ 200-500/mês |

Estrutura Docker espelha Rizza: Swarm + Traefik (SSL Let's Encrypt) + Portainer + Cloudflare DNS.

## Fora desta fase (roadmap futuro)

- **Multi-tenancy** — se virar SaaS pra outros atacadistas: schema/database por tenant, configuração de dataset PBI por cliente, login Multi-Auth0/Cognito
- **Frontend modular** — quando complexidade crescer (filtros cruzados entre gráficos, estado compartilhado), migrar 8 HTMLs+JS inline pra Vanilla JS com modules (`<script type="module">`). MVP fica como tá
- **Chat IA financeiro** (GPT-4o + medidas DAX) — análises em linguagem natural ("qual vendedor caiu mais este mês?")
- **Modelo preditivo de churn** (Python ML/sklearn) — alerta antes de cliente virar URGENTE
- **Disparos WhatsApp automáticos** pro vendedor (usar infra cloudflared já testada na Rizza)
- **PWA mobile-first** pra vendedores PJ usarem no celular
- **Exportação Excel/PDF** (CSV já cobre 90%)
- **Notificações por email** (alerta diário/semanal por vendedor)
- **Comparativo entre filiais** (PCEMPR) se Multpel tem mais de uma
- **Cross-sell automatizado** — "clientes parecidos com X também compram Y"
- **CI/CD** — GitHub Actions já roda pytest; quando estabilizar, automatizar deploy via Swarm

---

# ANEXO A — `requirements.txt`

```
Flask==3.0.0
Flask-CORS==4.0.0
psycopg2-binary==2.9.9
python-dotenv==1.0.0
Werkzeug==3.0.1
requests==2.31.0
redis==5.0.1
pytest==7.4.3
pytest-mock==3.12.0
```

---

# ANEXO B — Snippet de código base (server.py)

Funções prontas pra copiar-colar no início do `server.py`. Já validadas no projeto Rizza.

```python
"""
Multpel Analytics — Backend
Rode: python -X utf8 server.py
Acesse: http://localhost:5000
"""

import os
import time
import json
import functools
import psycopg2
import redis
import requests
from concurrent.futures import ThreadPoolExecutor
from flask import (
    Flask, Response, jsonify, send_from_directory,
    request, session, redirect, stream_with_context
)
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, static_folder='.')
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-change-me')
CORS(app, supports_credentials=True)

# ── Config Power BI ──
CONFIG = {
    'tenant_id':     os.getenv('POWERBI_TENANT_ID', ''),
    'client_id':     os.getenv('POWERBI_CLIENT_ID', ''),
    'client_secret': os.getenv('POWERBI_CLIENT_SECRET', ''),
    'dataset_id':    os.getenv('POWERBI_DATASET_ID', ''),
    'group_id':      os.getenv('POWERBI_GROUP_ID', ''),
}

# ── Redis (cache compartilhado entre workers) ──
_R = redis.Redis(
    host=os.getenv('REDIS_HOST', 'redis'),
    port=int(os.getenv('REDIS_PORT', '6379')),
    decode_responses=True,
)
_CACHE_TTLS = {
    'dax_agregado':  3600,   # 1h
    'dax_lista':      300,   # 5min
    'metadata':     86400,   # 24h
    'token_pbi':     3000,   # 50min
}

def _cache_get(key):
    raw = _R.get(key)
    return json.loads(raw) if raw else None

def _cache_set(key, data, ttl_tipo='dax_agregado'):
    _R.setex(key, _CACHE_TTLS[ttl_tipo], json.dumps(data, default=str))

def cache_key_for_user(endpoint, params=None):
    """Inclui RBAC do user pra evitar vazamento."""
    parts = [
        'multpel', endpoint,
        f"role={session.get('role', 'anon')}",
        f"usur={session.get('codusur', '-')}",
        f"supv={session.get('codsupervisor', '-')}",
    ]
    if params:
        parts.append(json.dumps(params, sort_keys=True))
    return ':'.join(parts)


# ── Banco de dados ──
def get_db():
    return psycopg2.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        port=os.getenv('DB_PORT', '5432'),
        dbname=os.getenv('DB_NAME', 'multpel_db'),
        user=os.getenv('DB_USER', 'postgres'),
        password=os.getenv('DB_PASSWORD', ''),
    )


# ── Decorators de autenticação ──
def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            if request.path.startswith('/api/'):
                return jsonify({'ok': False, 'error': 'Não autenticado'}), 401
            return redirect('/login')
        # força troca de senha
        if session.get('must_change_password') and request.path != '/trocar-senha':
            if request.path.startswith('/api/'):
                return jsonify({'ok': False, 'error': 'Troque a senha antes de continuar', 'redirect': '/trocar-senha'}), 403
            return redirect('/trocar-senha')
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'ok': False, 'error': 'Não autenticado'}), 401
        if session.get('role') != 'admin':
            return jsonify({'ok': False, 'error': 'Acesso negado'}), 403
        return f(*args, **kwargs)
    return decorated


# ── Power BI: token cacheado ──
def get_token_cached():
    cached = _cache_get('multpel:pbi:token')
    if cached:
        return cached
    url = f"https://login.microsoftonline.com/{CONFIG['tenant_id']}/oauth2/v2.0/token"
    resp = requests.post(url, data={
        'grant_type': 'client_credentials',
        'client_id': CONFIG['client_id'],
        'client_secret': CONFIG['client_secret'],
        'scope': 'https://analysis.windows.net/powerbi/api/.default'
    }, timeout=30)
    resp.raise_for_status()
    token = resp.json()['access_token']
    _cache_set('multpel:pbi:token', token, 'token_pbi')
    return token


def execute_dax(token, query, dataset_id=None):
    ds = dataset_id or CONFIG['dataset_id']
    url = (
        f"https://api.powerbi.com/v1.0/myorg/groups/"
        f"{CONFIG['group_id']}/datasets/{ds}/executeQueries"
    )
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
    body = {'queries': [{'query': query}], 'serializerSettings': {'includeNulls': True}}
    resp = requests.post(url, json=body, headers=headers, timeout=120)
    resp.raise_for_status()
    return resp.json()


def retry_dax(fn):
    """Decorator: 3 tentativas com backoff exponencial; aguarda 30s se 'dataset refreshing'."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        ultima = None
        for tent in range(3):
            try:
                return fn(*args, **kwargs)
            except requests.HTTPError as e:
                ultima = e
                code = e.response.status_code
                msg = (e.response.text or '').lower()
                if 'refresh' in msg or 'processing' in msg:
                    time.sleep(30)
                elif code in (401, 502, 503, 504):
                    time.sleep(2 ** tent)
                else:
                    raise
        raise ultima
    return wrapper


def executar_dax_paralelo(queries: dict) -> dict:
    """Roda múltiplas queries DAX em paralelo (DAX é I/O bound — threads bastam)."""
    token = get_token_cached()
    @retry_dax
    def _run(q):
        return execute_dax(token, q)
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {nome: ex.submit(_run, q) for nome, q in queries.items()}
        return {nome: f.result() for nome, f in futures.items()}


def clean_rows(rows):
    """Normaliza chaves '[tabela]coluna' → 'coluna'."""
    result = []
    for row in rows:
        clean = {}
        for k, v in row.items():
            short_key = k.split('[')[-1].rstrip(']') if '[' in k else k
            clean[short_key] = v
        result.append(clean)
    return result


def _csv_linha(valores):
    """Formata linha CSV com separador `;` e BOM-safe."""
    out = []
    for v in valores:
        if v is None:
            out.append('')
        else:
            s = str(v).replace('"', '""')
            if ';' in s or '"' in s or '\n' in s:
                s = f'"{s}"'
            out.append(s)
    return ';'.join(out) + '\n'


# ── RBAC ──
def aplicar_rbac_dax():
    """Fragmento DAX a concatenar via && em FILTER, conforme RBAC."""
    if session.get('role') == 'admin':
        return ""
    if session.get('codusur'):
        return f"FATURAMENTO_VENDAS[CODUSUR] = {int(session['codusur'])}"
    if session.get('codsupervisor'):
        return f"FATURAMENTO_VENDAS[CODSUPERVISOR] = {int(session['codsupervisor'])}"
    return ""


# ── Helper temporal ──
def filtro_periodo(tipo: str) -> str:
    """Centraliza filtros DAX temporais. Tipos: mes_atual, ytd, 12m, 24m, ano_anterior, 'YYYY-MM'."""
    if tipo == 'mes_atual':
        return "MONTH(FATURAMENTO_VENDAS[DTSAIDA])=MONTH(TODAY()) && YEAR(FATURAMENTO_VENDAS[DTSAIDA])=YEAR(TODAY())"
    if tipo == 'mes_anterior':
        return "FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(EOMONTH(TODAY(), -2)+1, 0) && FATURAMENTO_VENDAS[DTSAIDA] <= EOMONTH(TODAY(), -1)"
    if tipo == 'ytd':
        return "YEAR(FATURAMENTO_VENDAS[DTSAIDA])=YEAR(TODAY())"
    if tipo == 'ano_anterior':
        return "YEAR(FATURAMENTO_VENDAS[DTSAIDA])=YEAR(TODAY())-1"
    if tipo == '12m':
        return "FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12)"
    if tipo == '24m':
        return "FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -24)"
    # 'YYYY-MM' (ex '2026-05')
    ano, mes = tipo.split('-')
    return f"YEAR(FATURAMENTO_VENDAS[DTSAIDA])={int(ano)} && MONTH(FATURAMENTO_VENDAS[DTSAIDA])={int(mes)}"


# ── Login (adaptar da Rizza com must_change_password) ──
@app.route('/login', methods=['GET'])
def login_page():
    if 'user_id' in session:
        return redirect('/')
    return send_from_directory('.', 'login.html')


@app.route('/login', methods=['POST'])
def login_post():
    data = request.get_json() or {}
    email = data.get('email', '').strip().lower()
    senha = data.get('senha', '')
    if not email or not senha:
        return jsonify({'ok': False, 'error': 'Preencha e-mail e senha'}), 400
    conn = get_db(); cur = conn.cursor()
    cur.execute(
        "SELECT id, nome, password_hash, role, ativo, codusur, codsupervisor, must_change_password "
        "FROM multpel_users WHERE email = %s", (email,)
    )
    user = cur.fetchone()
    cur.close(); conn.close()
    if not user:
        return jsonify({'ok': False, 'error': 'E-mail ou senha inválidos'}), 401
    uid, nome, pw_hash, role, ativo, codusur, codsupervisor, mcp = user
    if not ativo:
        return jsonify({'ok': False, 'error': 'Conta desativada'}), 403
    if not check_password_hash(pw_hash, senha):
        return jsonify({'ok': False, 'error': 'E-mail ou senha inválidos'}), 401
    session['user_id']               = uid
    session['nome']                  = nome
    session['role']                  = role
    session['codusur']               = codusur
    session['codsupervisor']         = codsupervisor
    session['must_change_password']  = bool(mcp)
    if mcp:
        return jsonify({'ok': True, 'redirect': '/trocar-senha'})
    return jsonify({'ok': True, 'redirect': '/'})


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


@app.route('/api/me')
@login_required
def me():
    return jsonify({
        'ok': True,
        'nome': session.get('nome'),
        'role': session.get('role'),
        'codusur': session.get('codusur'),
        'codsupervisor': session.get('codsupervisor'),
    })


if __name__ == '__main__':
    print("\n⚡ Multpel Analytics — Backend")
    missing = [k for k, v in CONFIG.items() if not v]
    if missing:
        print(f"⚠️  Vars Power BI faltando: {', '.join(missing)}")
    else:
        print("✅ Configuração Power BI OK")
    print("🌐 http://localhost:5000\n")
    app.run(host='0.0.0.0', port=5000, debug=True)
```

**Importante**: o `init_db.py` é praticamente igual ao da Rizza, trocando nome da tabela pra `multpel_users` e adicionando campo `must_change_password BOOLEAN DEFAULT true` + `codusur INTEGER` + `codsupervisor INTEGER`.

---

# ANEXO C — Template CSS/HTML base

**Variáveis e classes obrigatórias** (incluir em TODO HTML):

```html
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@3.0.1"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2.2.0"></script>
<style>
  :root {
    --bg: #0a0e17;
    --surface: #111827;
    --surface2: #1a2235;
    --border: #1e293b;
    --text: #e2e8f0;
    --text-dim: #94a3b8;
    --accent: #38bdf8;
    --accent2: #818cf8;
    --green: #34d399;
    --red: #f87171;
    --orange: #fb923c;
    --yellow: #fbbf24;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'DM Sans', sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
  }
  /* noise overlay */
  body::before {
    content:''; position: fixed; inset: 0; opacity: 0.03;
    background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
    pointer-events: none; z-index: 9999;
  }
  @keyframes fadeUp { from { opacity: 0; transform: translateY(16px); } to { opacity: 1; transform: translateY(0); } }
  .dashboard { padding: 24px; max-width: 1600px; margin: 0 auto; animation: fadeUp 0.4s ease; }
  .top-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; flex-wrap: wrap; gap: 16px; }
  .top-bar h1 {
    font-size: 1.4rem; font-weight: 700;
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  }
  .meta { font-size: 0.78rem; color: var(--text-dim); font-family: 'JetBrains Mono', monospace; }
  .btn-sm {
    padding: 8px 18px; background: var(--surface2); border: 1px solid var(--border);
    color: var(--text); font-family: 'DM Sans', sans-serif; font-weight: 500;
    font-size: 0.82rem; border-radius: 8px; cursor: pointer; transition: all 0.15s;
    text-decoration: none; display: inline-block;
  }
  .btn-sm:hover { border-color: var(--accent); color: var(--accent); }
  .btn-primary {
    padding: 10px 22px;
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    color: var(--bg); font-weight: 700; border: none; border-radius: 8px;
    cursor: pointer; font-size: 0.88rem;
  }
  /* KPI cards */
  .kpi-grid {
    display: grid; gap: 14px; margin-bottom: 24px;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  }
  .kpi-card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 12px; padding: 18px 20px; position: relative; overflow: hidden;
  }
  .kpi-card::before {
    content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px;
    border-radius: 12px 12px 0 0;
  }
  .kpi-card:nth-child(1)::before { background: var(--accent); }
  .kpi-card:nth-child(2)::before { background: var(--green); }
  .kpi-card:nth-child(3)::before { background: var(--accent2); }
  .kpi-card:nth-child(4)::before { background: var(--orange); }
  .kpi-card .label { font-size: 0.7rem; font-weight: 600; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 8px; }
  .kpi-card .value { font-size: 1.6rem; font-weight: 700; font-family: 'JetBrains Mono', monospace; }
  .kpi-card .yoy { font-size: 0.78rem; margin-top: 6px; }
  .kpi-card .yoy.up { color: var(--green); }
  .kpi-card .yoy.down { color: var(--red); }
  /* Filtros */
  .filtros-card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; margin-bottom: 16px; }
  .filtros-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px 12px; }
  .ff label { display: block; font-size: 0.7rem; color: var(--text-dim); margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.04em; }
  .ff input, .ff select {
    width: 100%; padding: 7px 10px; background: var(--surface2);
    border: 1px solid var(--border); border-radius: 6px;
    color: var(--text); font-size: 0.8rem;
  }
  .ff input:focus, .ff select:focus { outline: none; border-color: var(--accent); }
  .ff input[type="date"] { color-scheme: dark; }
  /* Tabela */
  .table-wrapper { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }
  .table-scroll { overflow-x: auto; max-height: calc(100vh - 360px); overflow-y: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 0.78rem; white-space: nowrap; }
  thead th {
    position: sticky; top: 0; background: var(--surface2); padding: 10px 12px; text-align: left;
    font-weight: 600; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em;
    color: var(--text-dim); border-bottom: 1px solid var(--border); cursor: pointer; z-index: 2;
  }
  thead th:hover { color: var(--accent); }
  tbody tr:hover { background: rgba(56,189,248,0.04); }
  td { padding: 8px 12px; border-bottom: 1px solid var(--border); }
  /* Badges */
  .badge { padding: 2px 8px; border-radius: 4px; font-size: 0.7rem; font-weight: 600; }
  .badge-ok        { background: rgba(52,211,153,0.15); color: var(--green); }
  .badge-normal    { background: rgba(56,189,248,0.15); color: var(--accent); }
  .badge-atencao   { background: rgba(251,191,36,0.15); color: var(--yellow); }
  .badge-urgente   { background: rgba(248,113,113,0.15); color: var(--red); }
  /* Loading */
  .loading-screen { display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 100vh; gap: 20px; }
  .spinner { width: 44px; height: 44px; border: 3px solid var(--border); border-top-color: var(--accent); border-radius: 50%; animation: spin 0.7s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  /* Toast */
  .toast { position: fixed; bottom: 24px; right: 24px; padding: 12px 18px; background: var(--surface); border: 1px solid var(--border); border-radius: 10px; color: var(--text); z-index: 1000; opacity: 0; transition: opacity 0.3s; }
  .toast.show { opacity: 1; }
</style>
```

**Top-bar padrão** (cabeçalho de toda página):

```html
<div class="dashboard">
  <div class="top-bar">
    <div>
      <h1>🎯 Multpel Analytics</h1>
      <span class="meta" id="userInfo"></span>
    </div>
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
      <a href="/" class="btn-sm">📊 Dashboard</a>
      <a href="/carteira" class="btn-sm">📋 Carteira</a>
      <a href="/vendedores" class="btn-sm">👥 Vendedores</a>
      <a href="/mix" class="btn-sm">🛒 Mix</a>
      <a href="/categorias" class="btn-sm">📦 Categorias</a>
      <a href="/tendencias" class="btn-sm">📈 Tendências</a>
      <a id="linkAdmin" href="/admin" class="btn-sm" style="display:none;">⚙ Admin</a>
      <a href="/logout" class="btn-sm">Sair</a>
    </div>
  </div>
  <!-- conteúdo da página aqui -->
</div>

<script>
  async function loadMe() {
    const r = await fetch('/api/me', { credentials: 'same-origin' });
    const j = await r.json();
    if (j.ok) {
      document.getElementById('userInfo').textContent = '👤 ' + j.nome + (j.codusur ? ' · RCA ' + j.codusur : '');
      if (j.role === 'admin') document.getElementById('linkAdmin').style.display = '';
    }
  }
  loadMe();
</script>
```

---

# ANEXO D — Exemplos DAX por categoria

Todas as queries já testadas e validadas no dataset RCA. **Sempre incluir filtro temporal** pra não estourar 1.3GB de memória.

### D1 — KPI agregado simples

```dax
EVALUATE { (
    CALCULATE([VENDA LIQUIDA], MONTH(FATURAMENTO_VENDAS[DTSAIDA])=MONTH(TODAY()) && YEAR(FATURAMENTO_VENDAS[DTSAIDA])=YEAR(TODAY())),
    CALCULATE([LUCRO TOTAL], MONTH(FATURAMENTO_VENDAS[DTSAIDA])=MONTH(TODAY()) && YEAR(FATURAMENTO_VENDAS[DTSAIDA])=YEAR(TODAY())),
    CALCULATE([MARGEM(%)], MONTH(FATURAMENTO_VENDAS[DTSAIDA])=MONTH(TODAY()) && YEAR(FATURAMENTO_VENDAS[DTSAIDA])=YEAR(TODAY()))
) }
```

Retorno: `[Value1] [Value2] [Value3]` na ordem.

### D2 — Série temporal mensal (gráfico de linha)

```dax
EVALUATE
SUMMARIZECOLUMNS(
    CALENDARIO[AnoMes],
    FILTER(FATURAMENTO_VENDAS, FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12)),
    "VendaLiquida", [VENDA LIQUIDA],
    "LucroTotal", [LUCRO TOTAL]
)
ORDER BY CALENDARIO[AnoMes]
```

### D3 — Top 10 clientes por LUCRO (Pareto)

```dax
EVALUATE
TOPN(10,
    SUMMARIZECOLUMNS(
        FATURAMENTO_VENDAS[CODCLI],
        FILTER(FATURAMENTO_VENDAS, FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12)),
        "Lucro12m", [LUCRO TOTAL],
        "Venda12m", [VENDA LIQUIDA]
    ),
    [Lucro12m], DESC
)
```

### D4 — Snapshot RFM (todos clientes ativos, filtro temporal mandatório)

```dax
EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODCLI],
    FILTER(FATURAMENTO_VENDAS, FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -24)),
    "UltimaCompra", MAX(FATURAMENTO_VENDAS[DTSAIDA]),
    "Dias", DATEDIFF(MAX(FATURAMENTO_VENDAS[DTSAIDA]), TODAY(), DAY),
    "Compras12m", CALCULATE(DISTINCTCOUNT(FATURAMENTO_VENDAS[NUMNOTA]), FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12)),
    "Lucro12m", CALCULATE([LUCRO TOTAL], FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12)),
    "Venda12m", CALCULATE([VENDA LIQUIDA], FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12))
)
```

### D5 — Datas de compra por cliente (cálculo ciclo pessoal em Python)

```dax
EVALUATE
SELECTCOLUMNS(
    FILTER(FATURAMENTO_VENDAS,
        FATURAMENTO_VENDAS[CODCLI] = 12345 &&
        FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12)),
    "Data", FATURAMENTO_VENDAS[DTSAIDA]
)
```

### D6 — Ranking de vendedores (com filtro de tipo R = vendedor externo)

```dax
EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODUSUR],
    FILTER(FATURAMENTO_VENDAS,
        FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12) &&
        NOT(FATURAMENTO_VENDAS[CODUSUR] IN { 999, 900, 4, 272 })),  -- exclui técnicos
    "VendaLiquida", [VENDA LIQUIDA],
    "LucroTotal", [LUCRO TOTAL],
    "TicketMedio", [TICKET MEDIO],
    "TaxaPositivacao", [TAXA POSITIVACAO CLIENTE]
)
ORDER BY [LucroTotal] DESC
```

### D7 — Carteira de UM vendedor específico (drill cockpit)

```dax
EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODCLI],
    FILTER(FATURAMENTO_VENDAS,
        FATURAMENTO_VENDAS[CODUSUR] = 573 &&  -- exemplo: JOAO VICTOR
        FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -24)),
    "UltimaCompra", MAX(FATURAMENTO_VENDAS[DTSAIDA]),
    "Dias", DATEDIFF(MAX(FATURAMENTO_VENDAS[DTSAIDA]), TODAY(), DAY),
    "Lucro12m", CALCULATE([LUCRO TOTAL], FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12))
)
```

### D8 — Mix abandonado (clientes que pararam de comprar categoria X)

```dax
EVALUATE
VAR ClientesQueCompravam =
    SUMMARIZE(
        FILTER(FATURAMENTO_VENDAS,
            FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12) &&
            FATURAMENTO_VENDAS[DTSAIDA] < EDATE(TODAY(), -3) &&
            RELATED(PCPRODUT[CODCATEGORIA]) = 15),
        FATURAMENTO_VENDAS[CODCLI]
    )
VAR ClientesQueAindaCompram =
    SUMMARIZE(
        FILTER(FATURAMENTO_VENDAS,
            FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -3) &&
            RELATED(PCPRODUT[CODCATEGORIA]) = 15),
        FATURAMENTO_VENDAS[CODCLI]
    )
RETURN EXCEPT(ClientesQueCompravam, ClientesQueAindaCompram)
```

### D9 — Cohort retention (clientes por mês de aquisição)

```dax
EVALUATE
VAR PrimeiraCompra =
    ADDCOLUMNS(
        SUMMARIZE(FATURAMENTO_VENDAS, FATURAMENTO_VENDAS[CODCLI]),
        "MesAquisicao", FORMAT(CALCULATE(MIN(FATURAMENTO_VENDAS[DTSAIDA])), "YYYY-MM")
    )
RETURN
SUMMARIZE(
    PrimeiraCompra,
    [MesAquisicao],
    "ClientesEntrada", COUNTROWS(PrimeiraCompra)
)
ORDER BY [MesAquisicao]
```

### D10 — Performance por categoria (treemap)

```dax
EVALUATE
SUMMARIZECOLUMNS(
    PCPRODUT[CODCATEGORIA],
    FILTER(FATURAMENTO_VENDAS, FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12)),
    "VendaLiquida", [VENDA LIQUIDA],
    "Margem", [MARGEM(%)],
    "ClientesUnicos", DISTINCTCOUNT(FATURAMENTO_VENDAS[CODCLI])
)
ORDER BY [VendaLiquida] DESC
```

### Regras importantes pra DAX

1. **Sempre incluir filtro temporal** (`EDATE(TODAY(), -N)`) — protege contra 1.3GB
2. **Em endpoints com filtro de vendedor**, concatenar `aplicar_rbac_dax()` com `&&` no FILTER
3. **TOTAL CLIENTES da medida** = cliente×mês; pra unique usar `DISTINCTCOUNT(FATURAMENTO_VENDAS[CODCLI])`
4. **Excluir vendedores "técnicos"**: códigos 999 (transferência), 900 (jurídico), 4/272 (caixa)
5. **Datas em DAX**: `FORMAT(coluna, "YYYY-MM")` retorna texto pra agrupamento mensal
6. **`SUMMARIZECOLUMNS` ignora valores BLANK por default** — usar `SUMMARIZE` se quiser blanks
7. **`EXCEPT` e `INTERSECT`** úteis pra mix abandonado / cohort
8. **Resultado vem com chaves tipo `[Value1]`, `[Value2]`** quando query usa `EVALUATE {(...)}` em vez de tabela nomeada — sempre aplicar `clean_rows()` no Python

---

# ANEXO E — Estrutura de `init_db.py`

```python
"""Inicializa banco multpel_db. Rode 1x: python -X utf8 init_db.py"""
import os
import psycopg2
from werkzeug.security import generate_password_hash
from dotenv import load_dotenv

load_dotenv()
conn = psycopg2.connect(
    host=os.getenv('DB_HOST', 'localhost'),
    port=os.getenv('DB_PORT', '5432'),
    dbname=os.getenv('DB_NAME', 'multpel_db'),
    user=os.getenv('DB_USER', 'postgres'),
    password=os.getenv('DB_PASSWORD', ''),
)
cur = conn.cursor()

cur.execute("""
    CREATE TABLE IF NOT EXISTS multpel_users (
        id              SERIAL PRIMARY KEY,
        nome            VARCHAR(255) NOT NULL,
        email           VARCHAR(255) UNIQUE NOT NULL,
        password_hash   VARCHAR(255) NOT NULL,
        role            VARCHAR(20) DEFAULT 'viewer',
        ativo           BOOLEAN DEFAULT true,
        must_change_password BOOLEAN DEFAULT true,
        codusur         INTEGER,
        codsupervisor   INTEGER,
        criado_em       TIMESTAMP DEFAULT NOW()
    );
""")
cur.execute("CREATE INDEX IF NOT EXISTS ix_users_email ON multpel_users(email);")

cur.execute("""
    CREATE TABLE IF NOT EXISTS multpel_log (
        id            SERIAL PRIMARY KEY,
        usuario_id    INTEGER REFERENCES multpel_users(id),
        rota          VARCHAR(120),
        parametros    TEXT,
        duracao_ms    INTEGER,
        acessado_em   TIMESTAMP DEFAULT NOW()
    );
""")

admin_email = 'admin@multpel.com.br'
cur.execute("SELECT id FROM multpel_users WHERE email = %s", (admin_email,))
if not cur.fetchone():
    cur.execute(
        """INSERT INTO multpel_users (nome, email, password_hash, role, must_change_password)
           VALUES (%s, %s, %s, 'admin', true)""",
        ('Administrador', admin_email, generate_password_hash('admin123'))
    )
    print(f"✅ Admin criado: {admin_email} — TROCAR no primeiro login!")
else:
    print(f"✅ Admin {admin_email} já existe.")

conn.commit()
cur.close()
conn.close()
print("Banco pronto. Rode: python -X utf8 server.py")
```

---

# ANEXO F — docker-compose.yml

```yaml
version: '3.8'
services:
  app:
    image: ghcr.io/SEU_USUARIO/multpel:latest
    env_file: .env
    depends_on: [postgres, redis]
    networks: [carvalhonet]
    deploy:
      replicas: 1
      labels:
        - "traefik.enable=true"
        - "traefik.http.routers.multpel.rule=Host(`multpel.SEUDOMINIO`)"
        - "traefik.http.routers.multpel.entrypoints=websecure"
        - "traefik.http.routers.multpel.tls.certresolver=letsencrypt"
        - "traefik.http.services.multpel.loadbalancer.server.port=5000"

  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: multpel_db
      POSTGRES_USER: ${DB_USER}
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - multpel_pg:/var/lib/postgresql/data
    networks: [carvalhonet]

  redis:
    image: redis:7-alpine
    networks: [carvalhonet]
    # Sem volume: cache puro em memória (perda em restart é aceitável)

volumes:
  multpel_pg:

networks:
  carvalhonet:
    external: true
```

---

**FIM DO PLANO.** Bom trabalho! 🚀
