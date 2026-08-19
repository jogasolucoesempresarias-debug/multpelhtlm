# Manual Completo — Multpel Analytics

> ⚠️ **SUBSTITUÍDO — NÃO ALIMENTAR O AGENTE COM ESTE ARQUIVO.**
> Manuais vigentes: **`docs/MANUAL_COMERCIAL.md`** e **`docs/MANUAL_COMPRAS.md`** (v3.0,
> 28/07/2026). Este texto é de 13/07/2026 e descreve o mundo pré-fusão — não conhece portal,
> acesso por área, tema claro/escuro, segurança de login nem multi-fonte/demo.
> Mantido porque é a fonte do `MANUAL.pdf` legado.

> Documento de referência para consulta (inclusive por agente de IA). Descreve **cada página**, **cada opção** e **como cada conta é feita**. Sempre que possível, a fórmula exata é apresentada.

**Versão**: 2.0 · **Atualizado em**: 2026-07-13 · **Público**: diretoria, gerência, suporte e agente de IA de dúvidas.

---

## 0. Como usar este manual

O Multpel Analytics é um painel web comercial que lê dados do **Power BI** (modelo Totvs/Winthor Oracle) e entrega dashboards, análise de carteira (RFM), cobertura, ranking de vendedores, mix, radar de produtos, metas e relatórios por e-mail. Este manual está organizado por **conceitos → fórmulas centrais → páginas → e-mails → acesso**. Para responder uma dúvida, procure a página correspondente e depois a fórmula na seção de cálculos.

Regra de ouro dos números: **tudo é alinhado ao RCA do ERP** (o app não usa cegamente as medidas nativas do Power BI quando elas divergem do que o vendedor vê no Winthor). Ver seção 2.

---

## 1. Visão geral e navegação

O menu superior dá acesso às páginas:

| Página | Rota | Pergunta que responde |
|---|---|---|
| Dashboard | `/` | "Como está o mês?" |
| Carteira | `/carteira` | "Para quem estamos vendendo e quem está esfriando?" |
| Vendedores | `/vendedores` | "Quem está vendendo?" |
| Cockpit | `/vendedor/<id>` | "Como está UM vendedor?" |
| Categorias | `/categorias` | "Quais departamentos vendem/lucram?" |
| Mix | `/mix` | "Quem parou de comprar um departamento?" |
| Radar | `/radar` | "Quem parou de comprar um PRODUTO?" |
| Tendências | `/tendencias` | "A base nova continua comprando (retenção)?" |
| Metas | `/metas` | "Estamos batendo a meta por vendedor?" |
| **Gerencial** | `/gerencial` | "Qual a cobertura da carteira por Empresa/Time/RCA?" |
| Admin | `/admin` | Cadastro de usuários, cron de e-mail, limiar de cobertura |

No topo de todas as páginas aparece a linha **"BI atualizado em dd/mm/aaaa hh:mm"** — a data/hora da última atualização concluída do dataset Power BI (ver seção 15). Se houver refresh em andamento, mostra "🔄 atualizando".

---

## 2. Conceitos-base

### 2.1 Alinhamento com o RCA (a regra dos números)
O RCA do Totvs e o Power BI calculam vendas/lucro de forma sutilmente diferente. O app usa **fórmulas customizadas** para bater **centavo a centavo** com o RCA:

- A devolução é contada pela **data de entrada no estoque (DTENT)**, não pela data da venda original (DTSAIDA). Sem isso há divergência média de 1–2%.
- **Validação oficial**: Sup. AFONSO ES-SUL, Abr/26 → Venda Líquida R$ 2.385.853,77 / Lucro R$ 520.326,87 (bate com o RCA).

### 2.2 RFM (Recência, Frequência, Monetário)
Classifica cada cliente em 3 dimensões dos últimos 12 meses, com nota **1 a 5** (quintis):
- **R (Recência)**: dias desde a última compra. Menos dias = melhor (nota maior).
- **F (Frequência)**: nº de notas/compras. Mais = melhor.
- **M (Monetário)**: lucro gerado. Mais = melhor.

Por que quintis (e não valores fixos)? Porque "muita compra" varia por perfil. Quintis normalizam: sempre 20% da base no topo.

### 2.3 Ciclo pessoal e régua personalizada
Cada cliente tem um **ciclo pessoal** = mediana dos intervalos entre compras nos últimos 12m, com piso de 7 dias. A "régua personalizada" mede o atraso **relativo ao próprio padrão** do cliente (dias ÷ ciclo), não a um número fixo. Uma padaria que compra a cada 7 dias e está há 14 sem comprar está pior (2× o ciclo) que um hotel que compra a cada 90 e está há 30.

### 2.4 Positivação
Cliente **positivado** = comprou no período analisado (≠ cliente cadastrado mas inativo). "Cobertura" (página Gerencial) é positivação numa janela móvel (ex.: ≤30 dias).

### 2.5 Cobertura de carteira
% da carteira que está **em dia** (comprou dentro da janela, padrão 30 dias). É o mesmo conceito em dois zooms: o **índice %** (placar) e a **distribuição por faixa** de recência. Detalhado na seção 12.

### 2.6 RBAC — duas réguas de isolamento
O acesso é por papel (admin/supervisor/vendedor/viewer) e existe em **duas naturezas** (seção 14):
- Por **venda** (Dashboard, Vendedores): filtra a transação (CODUSUR/CODSUPERVISOR na nota).
- Por **cadastro** (Carteira, Categorias, Mix, Tendências, Radar, Gerencial): filtra pelo cliente registrado no vendedor (PCCLIENT.CODUSUR1).

---

## 3. Fórmulas centrais (cálculos que se repetem)

### 3.1 Receita Líquida (alinhada RCA)
```
Receita Líquida = VENDA BRUTA(DTSAIDA)
                − TOTAL DEVOLUCAO(DTENT)
                − TOTAL DEVOLUCAO AVULSA(DTENT)
```

### 3.2 Lucro Total (alinhado RCA)
```
Lucro Total = Receita Líquida
            − ( CUSTO TOTAL
              − CUSTO TOTAL DEVOLUCAO(DTENT)
              − CUSTO TOTAL DEVOLUCAO AVULSA(DTENT) )
```

### 3.3 Métricas derivadas do mês
```
Margem (%)          = Lucro Total ÷ Receita Líquida
Ticket Médio        = Receita Líquida ÷ DISTINCTCOUNT(NUMTRANSVENDA)   (nº de pedidos)
Clientes Positivados= DISTINCTCOUNT(CODCLI) no período
Mix Médio           = medida [TOTAL MIX]
Clientes Novos      = medida [TOTAL CLIENTES NOVO]  (1ª compra no mês)
Valor Médio / Kg    = medida [VALOR MEDIO PESO]
```

### 3.4 YoY (Year-over-Year)
Recalculado no app (não usa a medida nativa, que diverge e não aceita filtro de supervisor). Janela de **12 meses vs os 12 meses anteriores**:
```
YoY(métrica) = (valor_12m − valor_12m_anterior) ÷ valor_12m_anterior
```
Aplicado a: Receita Líquida, Lucro, Positivação de Cliente (DISTINCTCOUNT CODCLI) e Mix. O ranking de vendedores usa janela de **365 dias exatos** (não EDATE) para bater com o BI do cliente.

### 3.5 Ciclo pessoal e status de régua
```
ciclo_pessoal = max(7, mediana(intervalos entre compras nos últimos 12m))

Régua PERSONALIZADA (padrão do app):  razao = dias_sem_comprar ÷ ciclo
  razao < 1  → ok        (dentro do ciclo)
  razao < 2  → normal
  razao < 3  → atenção
  senão      → urgente

Régua FIXA (planilha original, ainda usada em cálculos internos):
  ≤10 → ok · ≤30 → normal · ≤45 → atenção · >45 → urgente
```

### 3.6 Receita/Lucro perdido projetado
```
se dias_sem_comprar < ciclo:  perdido = 0
senão: meses_atrasado = (dias_sem_comprar − ciclo) ÷ 30
       lucro_perdido_proj   = (lucro_12m ÷ 12) × meses_atrasado
       receita_perdida_proj = (venda_12m ÷ 12) × meses_atrasado
```
Exemplo: cliente que dá R$ 1.200/mês, ciclo 30d, 90d parado → 2 meses atrasado → R$ 2.400 projetados como perdidos.

### 3.7 Próximo pedido e prioridade de contato
```
proximo_pedido_previsto = ultima_compra + ciclo_pessoal
prioridade_contato = (venda_12m ÷ 12) × (dias_sem_comprar ÷ ciclo)   [só se dias ≥ ciclo]
```
Clientes sem ciclo (só 1 compra) não têm previsão e ficam de fora da lista do dia.

### 3.8 Quintis e segmentos RFM
- Cutoffs de **R** calculados sobre **toda a base** (24m). Cutoffs de **F** e **M** só sobre clientes **ativos** (≥1 compra em 12m); inativos recebem F=1, M=1 direto.
- Segmentos canônicos (primeira regra que casa vence):
```
champions           : R=5 e F=5 e M=5
loyal (Fiéis)       : R≥4 e F≥4
cant_lose (Não Perder): 2≤R≤3 e F≥4
at_risk (Em Risco)  : 2≤R≤3 e F≥3
new (Novos)         : R=5 e F=1
potential_loyalist  : R≥4 e 1≤F≤3
lost (Perdidos)     : R≤2 e F≤2 e M≤2
hibernating (Inativos): todo o resto
```

---

## 4. Página: Dashboard (`/`)

Panorama do **mês corrente vs ano anterior**.

**Filtro (admin/viewer)**: "Supervisor (Time)" — aceita 1 ou vários; sem filtro = empresa toda. Vendedor/supervisor logado já vê só o seu (o parâmetro é ignorado para eles).

**Cards linha 1**: Venda Líquida (mês), Lucro Total (mês), Margem (%), Clientes Positivados. **Cards linha 2**: Ticket Médio, Mix Médio, Clientes Novos, Valor Médio/Kg. Todas as fórmulas na seção 3.3; venda/lucro alinhados RCA (3.1/3.2). Os cards primários mostram o indicador **YoY** (↗ verde / ↘ vermelho).

**Gráficos**:
- **Série temporal 12m** (linha dupla): Venda Líquida + Lucro Total por mês (merge de 3 queries — vendas, devolução, devolução avulsa — por AnoMes, alinhado RCA).
- **YoY — 4 métricas** (barras): % de Receita, Lucro, Positivação de Cliente e Mix vs ano anterior.
- **Top 10 Departamentos por Lucro (12m)** e **Top 10 Vendedores por Lucro (12m)** (tabelas com drill nos clientes).
- **Top 10 clientes por lucro 12m** (com clique → drill 360°).

Mês corrente = `MONTH(DTSAIDA)=MONTH(HOJE) && YEAR(DTSAIDA)=YEAR(HOJE)`. Cache Redis 30min–1h.

---

## 5. Página: Carteira (`/carteira`)

A tela mais rica. Tem **duas abas**: **Visão Geral** (RFM) e **📞 Próximo Pedido**.

### 5.1 Aba Visão Geral
**Universo**: clientes ativos nos últimos **24 meses** (base RFM), com **números totais** do cliente (a carteira é carregada global e recortada por cadastro conforme o RBAC).

**Filtros**: Time (supervisor), Vendedor, UF, Cidade, Busca livre (cliente/cidade/código/vendedor/time), "Dias s/ comprar (mín.)" com atalhos 10+/30+/60+/90+, e chips de **Segmento** (8). Os dropdowns fazem **cross-filter** (escolher um estreita os outros) e listam só quem realmente tem cliente na carteira.

**Cards (8 segmentos)** + **donut** de distribuição + **gráfico Receita líquida × Clientes positivados (12m)** que reage aos filtros e é clicável (drill do mês).

**Tabela acionável** — colunas: CodCli, Cliente, Cidade/UF, Vendedor, Time, R (dias), F (12m), Venda 12m, Média Venda (=venda_12m÷12), **⚠ Receita Perdida proj.**, Segmento, Telefone. Clique na linha → **drill 360°** (última compra, ciclo, R/F/M, histórico mensal 12m, top 5 departamentos comprados).

**Exports**: CSV (BOM UTF-8, abre no Excel com acento) e PDF; o nome do arquivo reflete os filtros ativos.

**Deep-link por faixa (vindo do Gerencial)**: a Carteira lê parâmetros da URL no carregamento — `dias_min`, `dias_max`, `vendedor`, `time`, `uf`, `cidade`, `segmento`, `busca`. Assim o clique numa faixa do painel Gerencial abre exatamente aqueles clientes (ex.: `?dias_min=31&dias_max=45&time=18`).

### 5.2 Aba Próximo Pedido (lista do dia)
Previsão de recompra = última compra + ciclo (seção 3.7). Opções de **Janela**: "A ligar hoje", "A ligar nos próximos 7 dias", "Vencido há +15 dias". Filtros Time/Vendedor/Busca. Ordena por prioridade (valor × atraso). Quando o cliente compra, ele **sai automaticamente** da lista. Clique na linha → top produtos a oferecer. Export CSV/PDF (agenda cronológica por data de previsão).

---

## 6. Página: Vendedores (`/vendedores`) e Cockpit (`/vendedor/<id>`)

### 6.1 Ranking
Filtros: Tipo (R rota / I interno / P), Supervisor, UF, Busca, "mostrar internos". Exclui sempre técnicos (códigos 999/900/4/272) e bloqueados. Colunas: rank, Vendedor, Tipo, Time, UF, Venda 12m, Lucro 12m, Ticket, **Positivação**, Clientes únicos, YoY %. Ordena por Lucro 12m.

**Taxa de Positivação (fórmula do app, não a medida nativa que estava bugada)**:
```
taxa_positivacao = clientes_que_compraram_12m ÷ carteira_oficial
carteira_oficial = DISTINCTCOUNT(PCCLIENT[CODCLI]) por PCCLIENT[CODUSUR1]
```
YoY calculado em Python (janela 365 dias), pois a medida nativa retorna NULL quando filtrada por CODUSUR.

### 6.2 Cockpit individual
Header de perfil (PCUSUARI) + 4 cards (Venda 12m, Lucro 12m + posição no ranking, Carteira X cadastrados/Y positivados, Positivação vs time/empresa) + alertas acionáveis (At Risk somando R$/ano; top 3 Champions clicáveis) + 3 gráficos (série 12m, donut RFM, distribuição de status) + tabela da carteira restrita ao CODUSUR com export.

---

## 7. Página: Categorias (`/categorias`)

Análise por **departamento** (CODEPTO — 33 valores com nomes textuais). Usa CODEPTO porque CODCATEGORIA vem ~92% nulo no faturamento.

- **Treemap** (shelf): largura ∝ venda, **cor ∝ margem** (verde alta → vermelho baixa). Clique → top clientes do depto.
- **Top 10 fornecedores** (barras).
- **Tabela**: Venda, Lucro, Margem %, Share, Clientes únicos, Produtos únicos por depto.
```
por depto: [VENDA LIQUIDA], [LUCRO TOTAL],
           DISTINCTCOUNT(CODCLI), DISTINCTCOUNT(CODPROD), janela 12m
Margem = Lucro ÷ Venda · Share = venda_depto ÷ venda_total
```
Não-admin: recorte por cadastro (CODCLI IN {...} do escopo).

---

## 8. Página: Mix Abandonado (`/mix`)

Pares **Cliente × Departamento** de cross-sell perdido: quem comprava o depto nos últimos 12m mas **parou** nos últimos N dias.

**Filtros**: Período (30/60/90/180 dias), Departamento, Fornecedor, Vendedor/Busca (locais). **Cards**: nº de pares parados, Lucro 12m em risco, Top 5 maiores perdas. **Tabela**: Cliente, Cidade/UF, Departamento, Última compra, **Dias parado** (badge: amarelo 30-60, laranja 60-90, vermelho 90+), Venda/Lucro Cat 12m, Vendedor. Clique no cliente → top 5 departamentos perdidos. Export CSV/PDF da lista completa.

---

## 9. Página: Radar (`/radar`)

Como o Mix, mas no nível de **PRODUTO** (não departamento): quem comprava um produto e parou, comparando **janela recente vs anterior** (queda de volume).

**Status do cliente em relação ao produto** (janela = N dias):
```
perdido   : dias_parado ≥ 2×N  (ou nunca)
parou     : dias_parado ≥ N
esfriando : ainda compra, mas venda recente < 50% da anterior
ativo     : demais
```
Board lista os produtos "sangrando" (ordenados por queda de receita). Marca **troca vs abandono** ("Trocou (outro do depto)" quando o cliente migrou para outro produto do mesmo departamento). Filtros por fornecedor; exports CSV/PDF. Escopo por cadastro (não-admin).

---

## 10. Página: Tendências (`/tendencias`) — Cohort Retention

Mede se a base **nova continua comprando**. Cada linha = "turma" (cohort) que fez a 1ª compra num mês; cada coluna = meses depois (M+0, M+1, …, M+12); cada célula = % daquele cohort que **comprou naquele mês específico** (retenção não-cumulativa: pular o mês não conta).

**Cores**: 🟢 ≥70% · 🟡 40–70% · 🔴 <40% · M+0 = 100% (referência). **Filtros**: Período (12/18/24m), Vendedor e Supervisor (cascata). Clique na célula → clientes do bucket; clique no rótulo da linha → cohort completo.
```
mes_aquisicao(cliente) = min(meses com compra)
retido em M+N ⇔ existe compra no mês (mes_aquisicao + N)
```

---

## 11. Página: Metas (`/metas`)

Acompanha **meta por vendedor/mês** vs realizado. A **meta (alvo)** é digitada no app (tabela `multpel_metas`); o app é dono do alvo. Métricas de meta: valor (faturamento), rentabilidade (lucro), clientes, mix.

- **Realizado — mês corrente**: vem do dataset **META** (pedidos PCPEDC/PCPEDI — meta é sobre PEDIDO, não faturamento).
- **Realizado — mês fechado**: vem do dataset **RCA** (faturamento), pois o META só guarda o mês corrente.
- **Projeção**: extrapola o realizado pelos **dias úteis** decorridos/restantes do mês.
- **Sugestão de meta** (Admin): a partir do histórico de faturamento (proxy), com método/percentual de crescimento; o admin ajusta.

Telas replicam as 4 visões de meta (valor, rentabilidade, clientes, mix) por supervisor/vendedor/total.

---

## 12. Página: Gerencial — Cobertura de Carteira (`/gerencial`)

Placar de **cobertura** (eficiência de atendimento) por **Empresa / Time / RCA**. Responde "quem está dando conta da carteira e quem precisa de atenção". Calculada 100% sobre a carteira já carregada (sem query nova), respeitando o RBAC por cadastro.

### 12.1 Definições e fórmulas
```
Coberto            = cliente com recência ≤ coberto_dias (padrão 30; toggle 30/45/60)
Cobertura clientes = nº clientes cobertos ÷ total da carteira
Cobertura valor    = valor (venda_12m) dos cobertos ÷ valor total
Cobertura ciclo    = clientes com status personalizada ∈ {ok, normal} ÷ total   (régua justa)
Receita em risco   = Σ receita_perdida_proj                                     (seção 3.6)
Base morta         = nº de clientes na faixa 91+
```
- **Denominador** = carteira ativa 24m (mesma base RFM). "Base morta" aparece como faixa 91+.
- **Cobertura por clientes** e **por valor** são mostradas lado a lado (base atendida × faturamento protegido).
- **Cobertura dentro do ciclo** é a régua justa para ranquear times/RCAs de perfis diferentes (não pune ciclo longo).

### 12.2 Faixas de recência (relatório por faixa)
Faixas fixas (dias sem comprar), com nº de clientes e valor vendido (12m) em cada:
```
0–15 · 16–30 · 31–45 · 46–60 · 61–90 · 91+     (+ rollup 0–30 = "em dia")
```
O índice de cobertura (30d) é exatamente a fatia 0–30 ÷ total. Clicar numa faixa (ou no rollup 0–30) abre a **lista exata** daqueles clientes na Carteira (deep-link, seção 5.1).

### 12.3 Layout e navegação (drill-down)
- **Banner** no topo: "Hoje N time(s) e M RCA(s) estão abaixo de X%" — é exatamente o que o alerta por e-mail dispara.
- **Controles**: toggle janela em dia (30/45/60), export CSV/PDF.
- **Placar (KPIs) do escopo atual**: Cobertura por clientes, Cobertura por valor, Receita em risco (R$), Dentro do ciclo (%). Cores semáforo vs o limiar.
- **Distribuição por faixa**: barra empilhada + tabela (linha 0–30 destacada).
- **Ranking dos filhos** (Times no nível Empresa; RCAs ao entrar num Time), **pior → melhor**. Colunas: Nome, **Positivados / Carteira**, Cobertura clientes, Cobertura valor, Dentro do ciclo, Receita em risco, Base morta, ⚑ (abaixo do limiar). Linha de Time → drilla para seus RCAs; linha de RCA → abre o cockpit. RCAs com poucos clientes são marcados "amostra pequena" (o % pode não ser representativo).
- **Breadcrumb**: Empresa ▸ Time ▸ RCA (clique para subir).

### 12.4 Limiar e escopo
- **Limiar de baixa performance** (corte vermelho + gatilho do alerta): configurável no Admin (padrão **60%**), sem redeploy.
- **Escopo**: admin/viewer veem a empresa toda; supervisor vê só seus times e os RCAs deles; vendedor vê só a própria carteira.

---

## 13. Página: Admin (`/admin`)

Só para papel **admin**. Duas áreas:

**Cobertura de carteira (Gerencial)**: campo **Limiar (%)** (padrão 60) e **Janela "em dia"** (30/45/60). Salvo em `multpel_config`, vale para o painel e para o alerta.

**Usuários cadastrados** — CRUD com: Nome, E-mail, **Destinatários adicionais (CC)** (até 5), Função (admin/supervisor/vendedor/viewer), Vendedor (codusur) ou Áreas (supervisor multi-área), Telefone, e opções de e-mail:
- **Cron ativo** (envia o relatório de carteira por e-mail), **Horário** e **Frequência**.
- **Incluir "Lista do Dia"** (Próximo Pedido) no e-mail.
- **Receber alerta de baixa performance de cobertura** (opt-in do alerta gerencial).
- Filtro de **segmento RFM** por usuário (vazio = carteira completa).

---

## 14. RBAC — quem vê o quê

| Papel | codusur | codsupervisor(es) | Vê |
|---|---|---|---|
| admin | — | — | Tudo |
| viewer | — | — | Tudo (leitura) |
| supervisor | — | 1+ áreas | Só suas áreas (pode ser multi-área) |
| vendedor | preenchido | — | Só a própria carteira |

**Duas réguas** (seção 2.6): por **venda** (Dashboard, Vendedores) e por **cadastro** (Carteira, Categorias, Mix, Tendências, Radar, Gerencial, e-mails). Resultado prático: **admin filtrando uma área == supervisor daquela área**, cliente por cliente. O filtro é aplicado **no servidor** (fragmentos DAX / recorte em Python); um vendedor não amplia acesso trocando a URL (recebe 403). A chave de cache inclui o RBAC (role + codusur + lista de supervisores), então escopos não vazam entre si.

---

## 15. Última atualização do BI (linha do topo)

A linha "BI atualizado em …" no cabeçalho vem da **API REST do Power BI** (histórico de refresh do dataset): pega o último refresh com status **Completed**, converte o horário de UTC para **America/São_Paulo** e exibe. Se um refresh estiver rodando, mostra "🔄 atualizando". Cache de 5 min. Se aparecer "atualização indisponível", é permissão de refresh history do Service Principal no Power BI.

---

## 16. E-mails automáticos

Três tipos, disparados pelo agendador interno (verifica a cada 5 min quem está na janela de horário/frequência). Só enviam de fato se `CRON_HABILITADO=true` no ambiente.

1. **Relatório de Carteira** (opção "Cron ativo"): PDF + CSV da carteira do destinatário (vendedor: 1 PDF por lucro; supervisor: 1 PDF por área + 1 CSV combinado). Respeita o filtro de segmento do usuário e os CCs.
2. **Lista do Dia** (opção "Incluir Lista do Dia"): anexo com os clientes a contatar hoje + vencidos até 15 dias + top 5 produtos a oferecer.
3. **Alerta de Cobertura** (opção "Receber alerta"): digest dos **Times e RCAs abaixo do limiar** (padrão 60%), do pior para o melhor, no escopo do destinatário. Colunas: Nome, Cobertura, **Positivados (≤Nd)**, **Carteira (total)**, Receita em risco. Só envia se houver alguém abaixo do limiar. Traz uma legenda "Como ler" para evitar dúvida (Positivados = compraram na janela; Carteira = universo).

---

## 17. Cache, atualização e performance

- **Power BI**: refresh ~1×/dia (definido pelo cliente).
- **Cache Redis**:
  - Carteira global, mapas mensais, ranking de vendedores: **1h**.
  - Metadados (vendedores/supervisores/deptos): **24h**. Cohort: **24h**.
  - Dashboard agregados / drills: **30min**. Refresh do BI: **5min**. Token PBI: ~50min.
- **Escopo**: carteira/cohort/mapas são **globais** (1 entrada para todos) e recortados por usuário em Python; endpoints agregados incluem o RBAC na chave de cache.
- 2ª visita de qualquer tela dentro do TTL: < 500ms (cache hit).

**Se a tela mostrar "Erro"/"Carregando" demais**: o Power BI pode estar em refresh diário (rejeita queries). Aguarde 5–10 min, use "Tentar agora" ou F5. Persistindo >30 min, avisar o time técnico.

---

## 18. Glossário

| Termo | Significa |
|---|---|
| CODUSUR | Código do vendedor no Winthor |
| CODSUPERVISOR | Código do supervisor (agrupa vendedores em "time") |
| CODUSUR1 | Vendedor de **cadastro** do cliente (PCCLIENT) — base do RBAC por cadastro |
| CODEPTO | Departamento de produto (33 valores) |
| Ciclo pessoal | Mediana de intervalos entre compras do cliente (12m), piso 7 dias |
| Recência (R) | Dias desde a última compra |
| Positivação | Cliente que comprou no período |
| Cobertura | % da carteira em dia (comprou ≤ N dias) |
| Base morta | Clientes na faixa 91+ (dormentes) |
| Receita/Lucro perdido projetado | Estimativa do que se deixa de faturar/lucrar se o cliente atrasado não for resgatado |
| Cohort | Turma de clientes com 1ª compra no mesmo mês |
| YoY | Comparação com os 12 meses anteriores |
| Drill 360° | Painel lateral com todos os detalhes de uma entidade |
| RBAC | Controle de acesso por papel |
| DTSAIDA / DTENT | Data da venda / Data de entrada da devolução no estoque |

---

## 19. Perguntas frequentes (para o agente de IA)

- **"O número bate com o RCA?"** Sim — Receita e Lucro usam devolução por DTENT (seção 2.1/3.1/3.2), validado centavo a centavo.
- **"Cobertura é positivado ou não positivado?"** Cobertura % = fatia **positivada** (comprou ≤ N dias). A coluna "Carteira (total)" é o universo; "Positivados" são os que compraram na janela.
- **"Por que meu RCA aparece com 0% e poucos clientes?"** É "amostra pequena": carteira pequena distorce o %. Olhe o número absoluto e a receita em risco.
- **"Qual a diferença entre Mix e Radar?"** Mix = parou de comprar um **departamento**; Radar = parou de comprar um **produto** específico.
- **"Como mudo o limiar do alerta?"** Admin → Cobertura de carteira → Limiar (%). Vale na hora, sem redeploy.
- **"Por que a régua fixa e a personalizada dão status diferentes?"** A fixa usa dias absolutos (10/30/45); a personalizada usa o ciclo do próprio cliente (dias ÷ ciclo).

---

*Fim do manual. Dúvidas técnicas ou novas funcionalidades: time de desenvolvimento.*
