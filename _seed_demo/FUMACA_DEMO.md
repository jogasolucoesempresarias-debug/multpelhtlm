# Roteiro de fumaça — instância DEMO (modo DATA_SOURCE=postgres)

Valida a produtização multi-fonte (Fases 1–4) subindo o app lendo 100% do `joga_demo`, sem tocar o BI do
cliente. Ao final, a checklist do que conferir tela a tela. **Tudo local; nada commitado.**

> Convenção: rodar sempre com o venv → `& ".venv\Scripts\python.exe" -X utf8 <script>` (PowerShell).
> Dois bancos Postgres no MESMO servidor: **`joga_demo`** (analítico) + **`multpel_demo`** (auth da DEMO,
> SEPARADO da produção `multpel_db`).

---

## 0. Pré-requisitos
- Postgres de pé; venv do app pronto (psycopg2, flask, dotenv…).
- Banco analítico **`joga_demo`** já populado. Conferir:
  ```
  & ".venv\Scripts\python.exe" -X utf8 -c "import provider_sql as p; c=p.analytics_conn().cursor(); c.execute('SELECT max(dtsaida), count(*) FROM faturamento_vendas'); print(c.fetchone())"
  ```
  Esperado: uma data recente + ~1,17M linhas. Se vazio, (re)gerar: `setup_db.py` → `gerar.py` → `gerar_fato.py`
  → `gerar_estoque.py` (ver seção 7 do HANDOFF).
- Redis é **opcional** — sem ele o cache degrada pra no-op, o app sobe igual (só um pouco mais lento).

## 1. Criar o banco de AUTH da demo (`multpel_demo`)
Separado da produção (o seeder de metas RECUSA `multpel_db`). Criar o banco vazio:
```
& ".venv\Scripts\python.exe" -X utf8 -c "import os,psycopg2; from dotenv import load_dotenv; load_dotenv(); p=dict(host=os.getenv('DB_HOST','localhost'),port=os.getenv('DB_PORT','5432'),user=os.getenv('DB_USER','postgres'),password=os.getenv('DB_PASSWORD','')); c=psycopg2.connect(dbname='postgres',**p); c.autocommit=True; c.cursor().execute('CREATE DATABASE multpel_demo'); print('multpel_demo criado')"
```

## 2. Criar o schema + admin na auth da demo
`init_db.py` cria as tabelas `multpel_*` e `estoque_*` e semeia um admin. Rodar apontando `DB_NAME` pra demo:
```
$env:DB_NAME="multpel_demo"; & ".venv\Scripts\python.exe" -X utf8 init_db.py
```
Isso cria o admin **`ADMIN_EMAIL` / `ADMIN_SENHA`** (com troca de senha forçada e SÓ área comercial).
Liberar a área **compras** e dispensar a troca de senha pra fumaça (parametrizado, só aspas simples internas):
```
$env:DB_NAME="multpel_demo"; & ".venv\Scripts\python.exe" -X utf8 -c "import os,json,psycopg2; from dotenv import load_dotenv; load_dotenv(); c=psycopg2.connect(host=os.getenv('DB_HOST','localhost'),port=os.getenv('DB_PORT','5432'),dbname='multpel_demo',user=os.getenv('DB_USER','postgres'),password=os.getenv('DB_PASSWORD','')); cur=c.cursor(); cur.execute('UPDATE multpel_users SET areas=%s::jsonb, must_change_password=false WHERE email=%s',(json.dumps(['comercial','compras']),'admin@multpel.com.br')); c.commit(); print('admin liberado (comercial+compras)')"
```

## 3. (Opcional, recomendado) Semear metas pra as barras de atingimento
Sem isto o painel de Metas mostra só o realizado (meta=0). Com a trava de segurança:
```
$env:DEMO_SEED="1"; $env:DB_NAME="multpel_demo"; & ".venv\Scripts\python.exe" -X utf8 _seed_demo\seed_metas_demo.py
```
Esperado: `~114 metas semeadas`. (Recusa se `DB_NAME=multpel_db`.)

## 4. Subir o app em modo BD
Definir as env vars da sessão (o switch `DATA_SOURCE=postgres` liga Comercial **e** Compras):
```
$env:DATA_SOURCE="postgres"      # <- o switch principal
$env:ANALYTICS_DB_NAME="joga_demo"   # (já é o default; explícito por clareza)
$env:DB_NAME="multpel_demo"      # auth da demo (NÃO a produção)
# opcional: $env:ANALYTICS_HOJE="2026-07-24"  # ancora o "hoje" numa data fixa
& ".venv\Scripts\python.exe" -X utf8 server.py
```
Abrir **http://localhost:5000**, logar com o admin criado acima, escolher a área no portal.

> Se aparecer "Vars Power BI faltando" no log: tudo bem em modo postgres — o app não bate no BI.

---

## 5. Checklist de telas (o que conferir)

### Comercial
- [ ] **Dashboard** — KPIs não-zero (venda líq, lucro, margem, ticket, mix, clientes, peso). ⚠️ **Os 3 badges
      de YoY sob os cards PREENCHIDOS** (ex.: "↗ 22% vs jul/25"). *Se vierem em branco, houve regressão no
      `dashboard_kpis`* (foi o bug pego no smoke). Gráfico YoY, sazonalidade, pareto.
- [ ] **Top-clientes** (no Dashboard) — lista com cliente/UF/vendedor/venda/lucro; alternar métrica lucro↔venda.
- [ ] **Carteira** — 8 segmentos RFM, matriz R×F, histograma de recência.
- [ ] **Vendedores** — ranking por lucro (desc), com nome/ticket/positivação/YoY.
- [ ] **Categorias** — ⚠️ **nomes de depto REAIS** ("Higiene Pessoal", "Bebidas"…), NÃO "Depto N"; shares ~100%.
- [ ] **Tendências** — cohort com retenção M+0 = 100%.
- [ ] **Metas** — painel por supervisor + drill de vendedores; **barras de atingimento** (se rodou o §3);
      série diária do mês; trocar mês (inclusive um mês FECHADO, ex.: junho); **Admin → sugestão de meta** (bota
      um codusur, escolhe media_3m/ano_anterior → sugere valor > 0).
- [ ] **Mix** — board de clientes×depto abandonados; drill de deptos e de fornecedores de um cliente.
- [ ] **Radar** — busca de produto (type-ahead); board de produtos sangrando; drill 360° do produto (clientes,
      status, canibalização); drill invertido por cliente.

### Compras (/estoque)
- [ ] **Cockpit/Cobertura** — placar ruptura/crítico/saudável/excesso + valor de estoque; ⚠️ **colunas de
      venda/lucro/margem e a curva ABC PREENCHIDAS** (Inc.2 — se vierem 0, o branch de venda regrediu).
- [ ] **Abastecimento** — sugestão de compra em caixas; **Ruptura** por comprador.
- [ ] **Desempenho** — receita por comprador (venda líq/lucro/margem/positivação/YoY), ordenado por lucro.
- [ ] **Validade/FEFO** — lotes vencendo por faixa; **Vencidos** — perda por validade + % sobre venda.
- [ ] **Ocupação/WMS** — KPIs de posições (ocupadas/livres), por rua e por tipo (AP/AE); conferência de rua.
- [ ] **Lead time** — por fornecedor; **Verbas** — negociado × aplicado × saldo.
- [ ] **Drawer 360°** de um produto — lotes, endereços WMS, gráfico de venda 12m.
- [ ] **Export** (CSV/XLSX/PDF) de uma tela qualquer — baixa sem erro.

### RBAC (opcional, se criar users de teste)
- [ ] Logar como um **vendedor** (codusur real, ex.: 213) → os números recortam pro escopo dele (< total).
- [ ] Logar como **supervisor** → recorta pras áreas dele.

---

## 6. Sanidade do DEFAULT (a Multpel segue intacta)
Numa outra sessão, subir **sem** o switch (modo cliente real) e confirmar que as telas seguem idênticas:
```
$env:DATA_SOURCE="powerbi"; $env:DB_NAME="multpel_db"; & ".venv\Scripts\python.exe" -X utf8 server.py
```
(precisa das credenciais Power BI no `.env` e acesso ao BI do cliente).

## 7. Prova final pré-merge — amostra centavo-a-centavo (sobre o DEFAULT)
Antes do merge, escolher ~5 rotas no modo **`powerbi+cliente`** e conferir o número contra o BI do cliente
diretamente (o mesmo de antes das mudanças). Objetivo: provar que os branches `if data_source=='postgres'`
**não alteraram 1 centavo** do caminho Multpel. (No modo postgres o dado é sintético; centavo não se aplica.)
Só depois dessa prova → liberar o merge da branch `feat/multi-fonte` (decisão do Gabriel).

---

## Manter a demo em dia (08/2026)

A base **anda sozinha**: o job `demo_avancar` (4h05) roda o `avancar_demo.py`, que desloca todas
as datas até hoje. Para ligar na stack, basta a env:

```env
DEMO_AUTO_AVANCAR=true
```

À mão, quando quiser:

```bash
python -X utf8 _seed_demo/avancar_demo.py --dry-run   # mostra quantos dias faltam
python -X utf8 _seed_demo/avancar_demo.py             # avança até hoje
```

⚠️ O script **recusa** banco que não tenha "demo" no nome — ele reescreve TODAS as datas, e
apontá-lo para a base de um cliente seria irreversível.

**Quando REGERAR em vez de avançar:** quando quiser conteúdo novo (outros produtos, outros
números) ou depois de mexer no `perfil.py`. Aí é o ciclo completo — `gerar.py` → `gerar_fato.py`
→ `gerar_estoque.py` → `seed_metas_demo.py` → `seed_historico_demo.py` → limpar o Redis.
⚠️ **Limpe o Redis com o servidor PARADO.** Com ele no ar, o processo repõe o cache velho entre
o flush e a sua conferência — perdi uma rodada inteira achando que o gerador não tinha aplicado.
⚠️ Em modo debug o Flask sobe **dois** processos (reloader); matar um só deixa o antigo servindo
dados velhos na mesma porta.
