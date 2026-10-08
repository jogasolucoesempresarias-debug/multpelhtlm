# Plano — Melhorias do Comercial (set/2026)

> **▶ RETOMAR PELO §13** (rodada 05–06/10/2026: estado de produção, o que está local sem commit, o
> pedido pendente do João e o backlog). §0–§12 são o histórico das decisões.

> **Handoff. Leia inteiro antes de retomar.** Todos os números foram MEDIDOS no BI real em
> 24/09/2026. A extração foi conferida no centavo contra `[VENDA LIQUIDA]` de jul/26
> (R$ 8.022.966) e ago/26 (R$ 6.625.712). As decisões marcadas **✅ João** vieram do João Victor
> por WhatsApp em 24/09/2026. Sem essa marca, é recomendação nossa, não aprovada.
> O que muda o número de uma tela sem ninguém mexer na operação está marcado ⚠️.

---

## 0. Linha do tempo (como chegamos aqui)

1. **Pedido original** (print do WhatsApp), 7 itens:
   1. taxa de positivação por curva ABC de clientes;
   2. potencial da carteira = nº de clientes × ticket médio × % de positivação média;
   3. potencial pela Receita Federal (faturamento via CNPJ);
   4. taxa média de cross-selling, marca e mix;
   5. "não atendendo esses clientes você deixa R$ X na mesa";
   6. ranking de vendedores com pesos editáveis: Cobertura 25 · Rentabilidade 35 · Receita 10 · Mix 20 · Frequência 10;
   7. colocar IA para rodar.
2. **Avaliação + medição no BI** (Fase 0). Achados que mudaram o desenho:
   - a fórmula do item 2 é identidade (§3.2);
   - a Receita Federal não publica faturamento (só porte, CNAE, capital social, situação);
   - o ranking literal mede "atividade" três vezes e a margem mede território (§3.4);
   - **a positivação da tela Vendedores estava errada** (§2).
3. **Gabriel:** a Receita Federal **fica adiada** até chegar a planilha com a metodologia de quem pediu.
   O resto segue.
4. **Gabriel:** o cross-selling "por pares" (mesmo ramo) **não foi pedido**: é sugestão nossa.
   Fica fora por ora, e também depende de tabela (`PCATIVI`).
5. **Positivação.** O Gabriel lembrou que "validamos contra o BI". A investigação mostrou que o
   Patch L (07/06/2026) **não validou, substituiu**: a medida do BI estava errada, e a conta que
   entrou no lugar também estava. ✅ João: base = *"quem comprou em 12 meses, pelo cadastro, e não
   o filtro de bloqueio"*.
6. ✅ João: *"LARISSA e WAGNER não são parâmetros, a Larissa é caixa"*.
7. **João propôs** clientes atendidos ÷ base (podendo passar de 100%) para ver quem "dá conta dos
   clientes dos outros". A medição mostrou que acima de 100% é, na maioria, **cadastro
   desatualizado**. Contraproposta: três números (cobertura da base na nota; fora da base e
   alcance como informação). ✅ João: *"vou tentar assim"*.
8. **João pediu:** clientes sem compra há mais de 69 dias que foram reativados, por time e
   vendedor, com a venda recuperada no mês.
9. ✅ João, 17:19:
   - reativação exige ter passado também do **ciclo pessoal** do cliente;
   - crédito para **quem vendeu**;
   - **dono do cadastro** aparece ao lado;
   - "recuperação que ficou" ok;
   - **página própria "Carteira em risco × carteira recuperada"**: *"desempenho de carteira,
     venda perdida, venda recuperada em um único lugar, isso é fantástico"*.
10. ✅ João, último retorno:
    - (1) venda perdida no **valor mensal** do cliente, a mesma régua da recuperada: ok;
    - (2) por time a ponte não fecha e ganha duas colunas (da sua base / por você): ok;
    - (3) **60 dias é a regra COMERCIAL** (não está no ERP): depois disso qualquer vendedor pode
      atender cliente de outra base. A medição sugeriu 69 como ciclo ideal, mas ele quer
      **inativo a partir de 61 dias**. **91 dias o ERP marca inativo automaticamente.**
11. **Gabriel:** implementar também os itens 1, 2/5, 4 e 6, **menos o que precisa de tabela**
    (Receita Federal e cross-selling por pares/`PCATIVI`).

---

## 1. Decisões em vigor

| Tema | Decisão | Origem |
|---|---|---|
| Base da carteira | cadastro (`PCCLIENT.CODUSUR1`) com compra em 12m; sem filtro de bloqueio | ✅ João |
| Cobertura da base | base atendida **por ele** no mês fechado ÷ base; 0–100%; entra na nota | ✅ João ("vou tentar assim") |
| Fora da base / alcance | informativos; fora da base em 4 tipos (colega · liberado · fictício · RCA inativo) | ✅ João + regra dos 60 dias |
| Base pequena | base ativa < 5 → sem %, fora da média do time (caixa/balcão) | ✅ João (LARISSA/WAGNER) |
| Inativo | **mais de 60 dias** sem comprar **e** além do ciclo pessoal | ✅ João (último retorno) |
| Liberado | mais de 60 dias sem comprar → qualquer vendedor pode atender (regra comercial) | ✅ João |
| Inativo no ERP | 91+ dias: só uma marca na lista | ✅ João (informação) |
| Perdido | **mais de 365 dias**: parâmetro, **ainda não confirmado** | aberto |
| Crédito da recuperação | quem vendeu; dono do cadastro ao lado; coluna "de outra base" | ✅ João |
| Régua da venda perdida | valor mensal do cliente (venda 12m até a última compra ÷ 12) | ✅ João |
| Ponte por time | não fecha, por construção; colunas "da sua base" e "por você" | ✅ João |
| Mês da nota/positivação | mês fechado; o corrente só como informação | nossa (medido) |
| Receita Federal | adiada até chegar a planilha | Gabriel |
| Cross-selling por pares | fora (é sugestão nossa e depende do `PCATIVI`) | Gabriel |

---

## 2. Correção da positivação — EM PRODUÇÃO (`494a6f5`)

### Defeito
- A medida `[TAXA POSITIVACAO CLIENTE]` do BI = clientes ÷ `[TOTAL CLIENTES TOTAL]`, que vale
  43.484 num mês e 570.152 em 12m. Não é carteira. O Patch L (`e6a7fab`) acertou ao descartá-la.
- A conta do Patch L: clientes que compraram **do RCA** em 12m (régua de VENDA) ÷ **cadastro
  inteiro** dele (42 mil, inclusive 33,9 mil bloqueados). Resultado: **37 de 97 RCAs acima de
  100%** (máx. LARISSA 8.600%); o JOSE JUNIOR aparecia com 22% (426 cadastrados, ~90 ativos).
- **965 clientes bloqueados compraram em 12m**: bloqueio é de crédito, não inatividade.

### Régua nova (mês fechado)
| Número | Conta | Nota? |
|---|---|---|
| **Cobertura da base** | clientes da base atendidos por ele no mês ÷ base ativa | sim |
| **Fora da base** | clientes de outras bases que ele atendeu, em 4 tipos (ordem de precedência): fictício · RCA inativo · **liberado** (> 60 d sem comprar quando ele vendeu) · colega (cliente ativo de RCA ativo) | não |
| **Alcance** | tudo que ele atendeu ÷ base ativa (pode passar de 100%) | não |

- **"Liberado" considera o mesmo mês:** se o dono vendeu dia 3, quem vende dia 20 não pegou
  cliente parado (`positivacao.liberados_no_mes`).
- **Fictícios:** 999 RCA TRANSFERÊNCIA · 900 JURÍDICO · 4 CAIXA · 272 MULTPEL · 34 PROSPECÇÃO.
- **RCA inativo:** não vendeu nos últimos 3 meses, está bloqueado ou fora do cadastro.
- **Por que separar por tipo:** o VALDELI atende 90 clientes fora da base, 64 deles do MATEUS
  CUSTÓDIO (1476), parado há mais de 3 meses e sem bloqueio; o IAGO atende 37 com base zero.
  Em ago/26: 141 atendimentos a clientes de RCA inativo e 153 a clientes em códigos fictícios.
  É cadastro a transferir, não desempenho.

### Arquivos
- `positivacao.py` (puro): `base_ativa`, `liberados_no_mes`, `tipo_dono`, `positivacao_por_rca`.
- `server.py`:
  - `CODIGOS_FICTICIOS`;
  - `_carregar_atendimentos_rca`: RCA × cliente × mês + 1ª data, 3 meses;
  - `_carregar_ultima_compra_antes`;
  - `_positivacao_rcas`;
  - `_montar_ranking_vendedores` (saíram o `carteira_idx` e a query `CarteiraOficial`);
  - `_taxa_comparavel`;
  - caches `vendedores:ranking:v2`, `vendedor:full:v2`, `atendimentos_rca…:v2`.
- `provider_sql.py`: `atendimentos_rca`, `ultima_compra_antes`; o ranking devolve 3 conjuntos.
- `vendedores.html`: colunas Cobertura base (mm/aa) · Fora da base (tooltip com os 4 tipos) ·
  Alcance; histograma de 0–20% a 80–100%.
- `vendedor.html`: card "Cobertura da base (mm/aaaa)" com "X de Y", fora da base e alcance.
- Testes: `tests/test_positivacao.py` (novo); ajustes em `test_endpoints_vendedores.py`,
  `test_dashboard_supervisor.py` e `test_provider_comercial.py`.

### ⚠️ Antes do deploy
- Avisar o diretor: **o número muda sem ninguém mexer na operação** (JOSE JUNIOR 22% → ~64%).
- A validação no BI real está na §9.

---

## 3. Itens do pedido

### 3.1 Item 1 — Positivação por curva ABC de clientes → CARTEIRA
- **Medido:** A = 1.393 clientes (80% da venda) · B = 1.852 · C = 3.618. Positivação mensal estável
  em 12m: **A ≈ 82% · B ≈ 59% · C ≈ 25%**.
- **Desenho:**
  - a classe vira coluna e filtro na Carteira;
  - card "Positivação por classe · mm/aaaa": A x% · B y% · C z%, no mês fechado;
  - o mês corrente aparece "até o dia N" contra o mesmo dia do mês anterior (no dia 5 o
    ranking parcial concorda só 0,64 com o final; no dia 15, 0,93).
- **Regras:**
  - classe = `curva_abc.classificar(chave='venda_12m')` sobre a carteira já carregada;
  - positivado = comprou no mês, **de qualquer vendedor**: é visão de carteira, não de vendedor;
  - reusa o cache `{cliente: {mês: venda}}`: **zero query nova**.

### 3.2 Itens 2 + 5 — Potencial e "dinheiro na mesa"
- **A fórmula como veio é identidade:** 6.935 × 43,2% × R$ 2.164 = R$ 6.476.352 = a venda exata.
- **"Ticket médio" tem 3 sentidos:** medida do BI R$ 156 · por pedido R$ 720 · por cliente/mês
  R$ 2.122. A página usa o **por cliente/mês**.
- **Com referência** (gap por classe, ticket próprio do cliente, ago/26): média R$ 0,54 mi ·
  **p75 R$ 1,06 mi** · p90 R$ 1,46 mi por mês.
  - **Recomendado p75** ("o que 1 em cada 4 RCAs já faz"), **dentro do mesmo universo**
    (campo ≠ lojas). ⚠️ Referência ainda não aprovada; implementada como parâmetro.
- **Camadas sem dupla contagem** (cada real entra em uma só, nesta ordem):

  | Camada | O que é | Estado |
  |---|---|---|
  | a) Recuperação | cliente em risco/perdido (§4), pelo valor mensal | **implementar** |
  | b) Positivação | gap até a referência p75, por classe, **só dos clientes que não estão em a** | **implementar** |
  | c) Share of wallet | pares do mesmo ramo e porte | **fora**: precisa da Receita |
  | d) Mix por pares | departamento que os pares compram e ele não | **fora**: precisa do `PCATIVI` |
- **Onde:** na página da §4. É o "dinheiro na mesa" ao lado da "venda recuperada".

### 3.3 Item 4 — Taxas de cross-selling, marca e mix → VENDEDORES
- **Definições,** por cliente positivado no mês (média do mês fechado):
  - **mix** = SKUs distintos;
  - **cross-selling** = departamentos distintos;
  - **marca** = marcas distintas.
- **Medianas no campo:** 11 SKUs · 4,7 departamentos · 3,2 marcas. Correlação entre as três:
  0,87 a 0,94. As três viram colunas; na nota só entra o mix.
- **Régua de VENDA** (quem vendeu). Contagem distinta **não se soma** de vendedor para time: cada
  grão é medido na própria query.

### 3.4 Item 6 — Ranking "Performance Comercial" → VENDEDORES
**Defeitos medidos no pedido literal:**
- receita YoY mês a mês é ruído (estabilidade 0,24; com 3 meses, 0,88);
- margem com efeito território (BA ~+4 p.p.);
- mix, frequência e cobertura são um eixo só (na simulação literal a nota segue mix 0,86 e
  cobertura 0,85, e a margem só 0,54);
- três universos: campo (5 times, 41 elegíveis) · lojas (13, balcão) · telemarketing.

**Desenho (os pesos do cliente ficam como estão):**

| Peso | Indicador | Medida |
|---|---|---|
| 25 | Cobertura | cobertura da base (§2) **ajustada pela classe ABC** (positivados ÷ esperados pela positivação do universo em cada classe) |
| 35 | Rentabilidade | % de atingimento da meta de rentabilidade (Metas; margem **pelo bruto**, a régua das Metas) |
| 10 | Receita | % de atingimento da meta de venda |
| 20 | Mix | % de atingimento da meta de mix |
| 10 | Frequência | pedidos por cliente atendido no mês (mín. 10 clientes) |

**Sem meta cadastrada, o indicador sai da conta** e a nota fica **parcial e renormalizada**
(padrão do Compras). Os fallbacks que eu havia previsto (margem − mediana do time, YoY de 3 meses,
SKUs/cliente) ficaram de fora: indicador com régua diferente na mesma escala de 0 a 10 parece
comparável sem ser. A nota só ranqueia com **pelo menos 35% do peso medido**, que é cobertura +
frequência.

- **Escalas lineares p10 → 0 e p90 → 10**, calibradas no histórico de 12m **por universo** e
  congeladas em `metas_comercial.py`/constantes, com versão (`NOTA_VERSAO`). Nada de percentil.
- **Pesos no Admin por competência**, com auditoria em `multpel_log` (padrão da meta de margem do Compras).
- **Nota parcial renormalizada**, com selo do peso medido (padrão do Compras).
- **Mês fechado.** A nota nunca é gravada: recalcula do ingrediente.
- Um ranking **por universo**; campo primeiro. Base < 5 e contas da casa ficam fora.
- **Pendente:** medir a distribuição do atingimento de metas no banco de **produção** (local só
  tem jun/26). Até lá as escalas de atingimento ficam em 70% → 0 e 110% → 10 (provisórias,
  marcadas na tela).

### 3.5 Item 7 — IA → por último
Os itens acima são os pilares do Agente Comercial. Na Multpel o `ia` está desligado (venda
adicional). Glossário obrigatório:
- margem das Metas pelo bruto;
- duas réguas de RBAC (venda × cadastro);
- devolução por DTENT;
- cobertura da base × alcance;
- inativo > 60 d + ciclo;
- venda perdida = valor mensal.

---

## 4. Página "Carteira em risco × carteira recuperada" (+ dinheiro na mesa)

### Réguas (✅ João)
- **Em risco:** mais de 60 dias sem comprar **e** além do ciclo pessoal. **Perdido:** mais de
  365 dias (a confirmar). **Inativo no ERP:** 91+ (só marca).
- **Recuperado no mês:** a 1ª compra do mês aconteceu com o cliente em risco ou perdido. O crédito
  é de **quem vendeu**, com o dono do cadastro e a marca "de outra base" ao lado.
- **Valor:** o valor mensal do cliente é a régua da venda perdida. ⚠️ A "receita em risco" da
  Carteira/Gerencial (`rfm.receita_perdida_proj`) **acumula** meses de atraso: **renomear** para
  "receita perdida acumulada", senão teremos dois números com o mesmo nome.

### Medição com a regra de 61 dias (jan–ago/26)

| Mês | Em risco no início (clientes / R$ mensal) | Entraram | Recuperados (clientes / venda no mês) | Viraram perdidos |
|---|---|---|---|---|
| jan | 2.324 / R$ 933 mil | 517 | 331 / R$ 282 mil | 177 |
| mar | 2.351 / R$ 1,04 mi | 472 | 365 / R$ 251 mil | 138 |
| mai | 2.387 / R$ 1,10 mi | 630 | 297 / R$ 251 mil | 170 |
| jul | 2.523 / R$ 1,22 mi | 470 | 363 / R$ 412 mil | 144 |
| ago | 2.486 / R$ 1,16 mi | 469 | 252 / R$ 236 mil | 172 |

**A carteira em risco cresceu 24% em 8 meses**: entram ~490 clientes por mês e ~310 são
recuperados. Só **53%** dos recuperados voltam a comprar nos 2 meses seguintes.

### Conteúdo da página
1. **Ponte do mês:** em risco no início + entraram − recuperados − viraram perdidos = em risco
   no fim. Os resgatados da base perdida aparecem à parte. Só a da empresa fecha.
2. **Cards:**
   - em risco (clientes / R$ mensal);
   - dinheiro na mesa (camadas a + b, §3.2);
   - venda recuperada no mês;
   - recuperação que ficou (coorte M-2).
3. **Placar Time → RCA:**
   - em risco da base;
   - recuperado **da sua base** (por qualquer vendedor);
   - recuperado **por você** (de qualquer base), com "de outra base".
4. **Listas:**
   - em risco, priorizada por valor × atraso, com marca de "inativo no ERP";
   - recuperados, com quem vendeu, dono, de outra base e dias parado.
5. **Série de 12 meses** da ponte. Compra é evento datado, então o histórico sai completo desde o dia 1.

### Implementação — EM PRODUÇÃO (`494a6f5`, ajustes de layout `e810ae1`/`66c444d`)
- `recuperacao.py` (puro), 12 testes: `estado_em`, `movimento_cliente`, `movimentos` (a parte
  cara, 1× por mês), `ponte_de`, `placar_de`, `ficou_de`, `em_risco_em`, `recuperados_de`.
  `potencial.py` (camada b), 6 testes. Endpoints em `test_recuperacao_endpoint.py` (5 testes).
- **Achados da validação no BI real, já corrigidos:**
  - a ponte errava por 1 cliente: quem começa o mês em risco, passa de 365 dias no meio do mês e
    compra saía do estoque sem ser contado. Hoje conta como recuperado do risco. As 12 pontes da
    série fecham no zero;
  - a lista de risco usava a `rfm.prioridade_contato` (valor × dias ÷ ciclo), que **explode com
    ciclo curto**: o 1º da lista era um cliente parado há 311 dias com ciclo de 7. Hoje a ordem é
    **valor mensal × chance de voltar no mês**, medida em 16.790 cliente-mês (jan–jul/26):
    61–90 d **32,5%** · 91–180 d **15,2%** · 181–365 d **4,6%** (`recuperacao.CHANCE_VOLTA`).
- **Dado:** cliente × vendedor × dia de 24 meses, em 6 blocos de 4 meses (~170 mil linhas no
  total; o `executeQueries` corta em 100 mil **em silêncio**, por isso os blocos). Cache de 1h.
- **Endpoints:** `/api/recuperacao` (ponte + cards + placar + série) e `/api/recuperacao/listas`,
  com CSV. Página `/recuperacao`, no menu do Comercial.
- **RBAC:** supervisor vê o time; vendedor vê a própria base e o que ele recuperou.
- **Parâmetros no Admin:** `inativo_dias` = 60 e `perdido_dias` = 365.
- **Postgres (demo):** `provider_sql.compras_dia`, e o endpoint entra no sweep do modo postgres.

---

## 5. Ordem de execução e estado

| # | Entrega | Estado |
|---|---|---|
| 1 | Correção da positivação + regra dos 60 dias | ✅ código, testes, validado no BI (§9) |
| 2 | Página Recuperação (carteira em risco × recuperada + dinheiro na mesa a+b) | ✅ código, testes, validado no BI |
| 3 | ABC de clientes + positivação por classe na Carteira | ✅ código, testes (`test_carteira_positivacao_abc.py`), validado no BI |
| 4 | Taxas de mix, cross e marca no Vendedores | ✅ código, validado no BI |
| 5 | Performance Comercial (ranking) | ✅ código e testes; ⚠️ escalas de atingimento provisórias; conferir em produção com metas |
| 6 | Renomear "Receita em risco" (projeção acumulada) → "Receita perdida acumulada" | ✅ Gerencial, Próximo Pedido, e-mail e PDF da cobertura, dicas |
| 7 | IA | depois |

**Em produção desde 25/09/2026** — commits na `main`: `494a6f5` (tudo), `e810ae1` e `66c444d`
(layout da ponte do mês). O João já usa `painel.jogasolucoes.com.br/performance`.
Em 26/09/2026 entrou `309c2a2`, com 4 defeitos achados no levantamento da IA: Clientes Novos do
Dashboard, alerta "/ano" do cockpit, "abaixo do limiar" do Gerencial só com pessoas e CONSUMIDOR
FINAL fora da análise por cliente — detalhe em [IA_COMERCIAL_CONTEUDO.md §1](IA_COMERCIAL_CONTEUDO.md).

Arquivos novos: `positivacao.py`, `recuperacao.py`, `potencial.py`, `performance_comercial.py`,
`recuperacao.html`, `performance.html`, os testes
`test_positivacao.py`, `test_recuperacao.py`, `test_recuperacao_endpoint.py`, `test_potencial.py`,
`test_performance_comercial.py` e este plano. Alterados: `server.py`, `provider_sql.py`,
`carteira.html`, `vendedores.html`, `vendedor.html`, `gerencial.html`, `static/joga-header.js`
(menu: Recuperação e Performance), `static/tooltips.js`, `README.md` e 3 testes antigos.

**Suíte completa (24/09/2026, código atual): 1.077 passam, 3 falham — as 3 pré-existentes** de
fixture de data (radar board CSV, mix abandonado, cohort), que falham igual no código do último
commit (conferido com `git stash`). Depois disso entrou `test_carteira_positivacao_abc.py` (2 passam).
(O `conftest.py` passou a trocar também o `_R_LOGIN` por fakeredis — sem isso, numa máquina sem
Redis, a suíte levava horas em vez de 6 minutos.)

### Para ir a produção (checklist)
- [x] Commit + merge na `main` + deploy (25/09/2026, a pedido do Gabriel direto na `main`).
- [ ] Rodar o sweep do modo postgres (demo): os endpoints novos entraram em `test_comercial_endpoints_sweep_modo_postgres`.
- [ ] **Performance:** conferir no banco de produção se há metas do mês fechado (o local só tem
  jun/26) e medir a distribuição real de atingimento para trocar as escalas provisórias
  (70% → 0, 110% → 10) e subir o `NOTA_VERSAO`.
- [ ] Avisar o diretor: a **positivação muda sem ninguém mexer** na operação (JOSE JUNIOR 22% → 64%).
- [ ] Primeiro acesso a `/recuperacao` e `/performance` depois de um deploy leva ~20–30 s
  (carga diária de 26 meses em blocos). Avaliar pôr no prewarm (`_prewarm`) antes de divulgar.
- [ ] Abrir as três telas no navegador com dado real (aqui só houve checagem de sintaxe do JS e
  chamadas diretas aos endpoints).

**Nada é commitado nem deployado sem o OK do Gabriel.** Cada entrega sai com testes e a suíte
completa (baseline de **968 passam / 5 falham**, as 5 pré-existentes).

## 6. Perguntas em aberto (implementado com o default indicado; muda por parâmetro/constante)
1. **Perdido = mais de 365 dias?** Default 365, parâmetro no Admin (`/api/admin/config/recuperacao`).
   Hoje o Gerencial chama de "base morta" quem está com 91+ e o ERP marca inativo aos 91.
2. **Referência do potencial = p75** do mesmo universo? Default p75 (`potencial.PERCENTIL_REF`).
3. **Frequência** = pedidos por cliente atendido no mês? Implementado assim.
4. **Lojas no ranking:** hoje têm ranking e escala próprios (13 → 12 elegíveis).
5. **Exclusões do ranking:** implementado = base < 5, códigos fictícios, bloqueados e nomes com
   "COMMERCE". O 573 JOAO VICTOR (telemarketing) é conta de venda ou só usuário?
6. **"Fora da base" de campo é quase todo cliente ATIVO de colega** (a regra dos 60 dias quase não
   aparece). Vale virar alerta para o supervisor?
7. **Cobertura da Performance: relativa ou absoluta?** (25/09, aguardando o João — por ora FICA COMO
   ESTÁ, decisão do Gabriel). O João estranhou nota 10 de cobertura para o JULIANO (#29), "que tem
   cobertura baixa". Medido em ago/26:

   | Régua | JULIANO |
   |---|---|
   | Gerencial (em dia ≤ 30 d) | 60% (28/47) |
   | Vendedores (cobertura da base no mês) | 67% (30/45) |
   | Performance (índice ajustado pela ABC) | 1,39 → nota 10 |

   A carteira dele é 21 A · 10 B · 14 C; ele atendeu 19/21 A, 5/10 B, 6/14 C. O esperado pela
   positivação média do campo em cada classe era 21,6 — ele atendeu 30. Absoluto é mediano
   (MARCIO 85%, JOSE ATILIO 79%); relativo ao mix da carteira é dos melhores. E a escala satura:
   acima de 1,34 já é 10 (vários empatados em 10).
   **Opções levadas ao João:** (a) cobertura ABSOLUTA — ex.: 40% → 0 e 90% → 10 (JULIANO cairia
   para ~5,4 nesse indicador); (b) manter a relativa com teto mais alto (1,60 → 10).
   **Quem configura o quê:** os PESOS e a janela "em dia" do Gerencial o João muda no Admin; a
   ESCALA (0,76 → 0, 1,34 → 10) é constante em `performance_comercial.ESCALAS` — mudá-la é subir o
   `NOTA_VERSAO`.

## 7. Pendências de dados/TI (não bloqueiam)
- Publicar o `PCATIVI` (nome do ramo), só se o cross-selling por pares for aprovado.
- Transferir no cadastro os clientes de RCA inativo e de códigos fictícios já atendidos por outro
  vendedor. A lista sai do "fora da base".
- Código duplicado GABRIEL BOREL (1581/1484).

## 8. Lições desta rodada (para não repetir)
- **"Validado" precisa dizer contra o quê.** O Patch L foi lembrado como validado contra o BI;
  na verdade o BI foi descartado, e o número novo nunca foi conferido com dado real (a fixture
  tinha 124 ÷ 200, com os dois universos coincidindo).
- **Numerador e denominador do mesmo universo.** Todo % novo precisa de um teste que garanta
  ≤ 100% quando o conceito exige.
- **Mais de 100% "explicável" merece medição antes de virar métrica.** O "dar conta dos clientes
  dos outros" era cadastro desatualizado.

## 9. Validação no BI real

**Positivação (24/09/2026):** `_positivacao_rcas()` rodado contra o BI de produção (só leitura;
Redis local desligado, com cache em memória). Referência: ago/26. 116 RCAs, 18 s com o cache
frio. Bate com a medição independente da Fase 0 (script próprio, sem o código do app).
- **Nenhuma cobertura acima de 100%** (antes: 37 de 97 RCAs).
- 86 RCAs com base ≥ 5: p10 0% · mediana 42% · p90 65%. O p10 zero são donos de base que não
  venderam no mês (RCA parado ou canal).

| RCA | Base | Cobertura | Fora da base (colega · liberado · fictício · inativo) | Alcance |
|---|---|---|---|---|
| 899 MARCIO | 87 | 85% | 4 (3 · 0 · 1 · 0) | 90% |
| 879 JOSE JUNIOR | 77 | 64% (antes 22%) | 3 (3 · 0 · 0 · 0) | 68% |
| 1183 PAULA | 58 | 60% | 0 | 60% |
| 2 JOSE ATILIO | 159 | 79% | 2 (1 · 1 · 0 · 0) | 81% |
| 1449 VALDELI | 10 | 50% | 90 (24 · 2 · 0 · **64**) | 950% |
| 750 ANTONIO CARLOS (loja) | 204 | 44% | 56 (30 · 3 · 20 · 3) | 71% |
| 950 IGOR (loja) | 166 | 48% | 76 (36 · 1 · **38** · 1) | 93% |
| 1479 ANDRE COSTA (loja) | 246 | 27% | 17 (12 · 5 · 0 · 0) | 34% |
| 1523 LARISSA (caixa) | 0 | — | 10 | — |
| 927 WAGNER | 1 | — | 0 | — |
| 1578 IAGO | 0 | — | 37 (34 · 2 · 0 · 1) | — |

- **"Liberado" é raro** (a regra dos 60 dias quase não aparece): o "fora da base" de campo é
  quase todo cliente ATIVO de colega. Isso é conversa para o supervisor.
- **IAGO:** base zero e 34 clientes ativos de colegas. Cadastro não transferido para ele (a
  confirmar com o João).

**Carteira — positivação por classe (ago/26):** A **80,9%** (1.135 de 1.403) · B **56,1%** · C
**22,3%**, idêntico à medição independente da Fase 0 com a classe do início do mês. Mês corrente
até o dia 24: A 75,2% × 74,8% no mesmo dia de ago (em linha). Filtro `?classe=A` na tabela: 1.393
clientes (a classe do início do mês corrente).

**Vendedores — taxas (ago/26):** medianas de 9,4 SKUs e 4,3 departamentos por cliente atendido
(a Fase 0 mediu 11 e 4,7 só no campo, que tem mix maior que lojas e telemarketing).
Ex.: PAULA 24,4 SKUs · 7,1 deptos · 4,9 marcas; JOSE JUNIOR 12,1 · 4,3 · 3,2.

**Recuperação (ago/26, regra de 61 dias + ciclo):** ponte 2.484 + 602 − 383 − 171 = 2.532 ✔
(e as 12 da série fecham). Valor mensal em risco R$ 1,21 mi; venda recuperada R$ 400 mil
(410 clientes, 65 por vendedor de outra base); recuperação que ficou (jun) **58,9%**. Dinheiro na
mesa R$ 1,76 mi = R$ 1,21 mi em risco + R$ 0,55 mi de positivação abaixo do p75. Resumo em 27 s
com cache frio e 0,1–0,4 s depois. RBAC: vendedor 879 vê só a própria base e o que ele recuperou.

**Performance (ago/26):** 41 elegíveis no campo (os mesmos 41 da Fase 0), 12 em lojas, 3 no
telemarketing. Sem metas de ago no banco local, todas as notas saem parciais (cobertura +
frequência = 35% do peso). LARISSA e e-commerce fora do ranking.


---

## 10. Handoff para o próximo chat — Agente de IA no COMERCIAL

> 📌 **Conteúdo aba por aba (endpoints e campos reais, perguntas por perfil, armadilhas, contrato
> das consultas, sugestões, bateria de perguntas reais e 5 defeitos a corrigir antes):
> [IA_COMERCIAL_CONTEUDO.md](IA_COMERCIAL_CONTEUDO.md).** O desenho (1 agente, 3 camadas) está lá no §0.
> **Decidido 25–26/09:** quem MAIS vai usar é o **gerencial** (João, diretor, supervisores) — a
> Fase 1 é de gestão; o vendedor pode usar também, no escopo dele, numa fase seguinte. O agente
> roda **só na demo** por enquanto (Multpel segue `off`).

> Pedido do Gabriel (25/09/2026): o próximo chat trabalha na IA. Leia também a memória
> `multpel_chat_ia_analista` e, no README, as seções **🤖 Agente de IA** e **🔜 Próximo passo**.
> O modelo é **gpt-4.1-mini** (OpenAI) — decisão de custo já tomada, **não re-sugerir Claude**.
> Não mexer no chat da DRE (é de outro projeto).

### O que já existe (Compras) e se reusa
- Motor `estoque/ia.py`: 3 estados por `MODULOS` (`off` · `oferta` sem chave · `ativo`),
  prompt-base, teto diário por pessoa (`IA_LIMITE_DIA`), timeout (`IA_TIMEOUT`), log em
  `multpel_log`, rastro do prompt em `estoque_ia_contexto`, conferência de números citados
  (`estoque/ia_conferencia.py`), SSE. Pilares em `estoque/ia_pilares.py`. Widget
  `static/estoque/estoque-ia.js`. Endpoints `/estoque/api/ia/{status,contexto,chat}`.
- O que precisa de versão própria no Comercial: os **pilares**, o **glossário** e o endpoint que
  monta o contexto (ver README § Próximo passo).

### Pilares do Comercial — de onde sai cada um (TUDO já calculado; zero conta nova)
| Pilar | Fonte no `server.py` / módulo puro |
|---|---|
| Carteira RFM (8 segmentos, receita perdida acumulada) | `_carteira_no_escopo()` + `rfm.py` |
| Curva ABC de clientes + positivação por classe | `_classes_abc_clientes()`, `positivacao.positivacao_por_classe` (endpoint `/api/carteira/positivacao-abc`) |
| Positivação dos vendedores (cobertura da base, fora da base, alcance) | `_positivacao_rcas()` / `positivacao.py` |
| Mix · deptos · marcas por cliente, frequência | `positivacao.taxas_atendimento` (vem no `_positivacao_rcas`) |
| Carteira em risco × recuperada + dinheiro na mesa | `_recup_base()` + `recuperacao.ponte_de/placar_de/em_risco_em/recuperados_de` + `_recup_potencial` |
| Performance Comercial (nota por vendedor) | `_performance_dados()` / `performance_comercial.py` |
| Metas (venda/rentab/clientes/mix × realizado × projeção) | `_montar_metas_resposta`, `_carregar_metas_realizado`, `_metas_buscar` |
| Vendedores (ranking, YoY) | `_carregar_ranking_vendedores()` |
| Curva ABC de produtos do time | `curva_abc.py` + endpoints `/api/abc*` |
| Mix abandonado, Radar, Tendências/cohort, Gerencial | endpoints existentes (`/api/mix/*`, `/api/radar/*`, `/api/tendencias/*`, `/api/gerencial/cobertura`) |

### Glossário obrigatório (onde o modelo VAI errar se ninguém disser)
- **Três "positivações"**: positivação da classe ABC (Carteira: comprou de QUALQUER vendedor);
  cobertura da base (Vendedores: atendido POR ELE); alcance (tudo que ele atendeu ÷ base, pode
  passar de 100%). E "positivação ≠ nº de pedidos".
- **Duas réguas de RBAC**: VENDA (quem faturou — Dashboard, Vendedores, Curva ABC) × CADASTRO
  (de quem o cliente é — Carteira, Recuperação, Mix, Radar). Em Lojas/Diretoria divergem 40–66%.
  O contexto tem de sair do escopo do usuário (`_carteira_no_escopo`, `aplicar_rbac_dax`).
- **Classe ABC de cliente = a do INÍCIO do mês** (12 meses fechados anteriores). Não é a da Curva
  ABC de PRODUTOS (outra tela, outra coisa).
- **Inativo/em risco = mais de 60 dias sem comprar E além do próprio ciclo**; perdido > 365; o ERP
  marca inativo aos 91 (só marca). A regra dos 60 dias é COMERCIAL (qualquer vendedor pode atender).
- **Duas "receitas de risco"**: *valor mensal em risco* (página Recuperação, R$/mês) × *receita
  perdida acumulada* (Gerencial/Próximo Pedido — cresce enquanto o cliente não volta). Nunca somar
  nem comparar uma com a outra. O Radar tem um 3º sentido ("receita em risco" = queda de receita).
- **Dinheiro na mesa = camada a (em risco) + camada b (positivação abaixo do p75)** — já sem
  dupla contagem; o modelo não pode somar de novo com "receita perdida".
- **Crédito da recuperação é de QUEM VENDEU**; o dono do cadastro é outra coluna. A ponte só fecha
  para a empresa; por time existem "da sua base" × "por você".
- **Nota da Performance**: parcial quando falta meta (declarar o peso medido); escalas de
  atingimento provisórias; cobertura é RELATIVA ao mix da carteira (ver §6 item 7 — pode mudar).
- **Margem das Metas divide pelo BRUTO** (com bonificação); a do Dashboard/Categorias pela líquida.
- **Devolução conta por DTENT** (data de entrada), não pela data da venda.
- **Mês fechado × mês corrente**: positivação/nota usam o fechado; o corrente só "até o dia N ×
  mesmo dia do mês anterior" (no dia 5 não tem sinal: concordância 0,64).
- Sempre citar o **código** do cliente/vendedor/produto (lição do Compras: sem código ninguém
  confere no ERP).

### Lições do Compras que valem aqui (README § Agente de IA)
- Recusar demais mata a adoção tão rápido quanto inventar (a 1ª versão recusou 8 de 9 perguntas).
- O índice de pilares sai do texto RENDERIZADO, não da estrutura (pilar vazio não pode ser anunciado).
- Contexto consolidado do servidor, não da tela; o recorte (filtros) viaja na pergunta e a
  resposta declara o recorte na 1ª frase.
- Timeout protege o app inteiro (Waitress `threads=8`).

### Como testar localmente (sem Redis nesta máquina)
- Suíte: `python -X utf8 -m pytest tests -q` (~6 min). O `tests/conftest.py` troca o `_R` E o
  `_R_LOGIN` por fakeredis — antes só o `_R`, e sem Redis local cada login esperava o timeout
  (a suíte levava horas). Baseline 25/09: 1.079 passam, 3 falhas pré-existentes
  (radar/mix/cohort, fixture de data).
- Subir o app: lançador que importa `server`, troca `_R`/`_R_LOGIN` por fakeredis e roda
  `app.run(127.0.0.1)` SEM `_start_scheduler`/prewarm (não dispara e-mail). Login local de teste:
  `abc-smoke@teste.local` (senha definida no banco local em 24/09; só existe local).
- Agente ligado: `MODULOS=comercial,compras,ia` + `OPENAI_API_KEY` no `.env`. A bateria real
  (`tests/smoke_ia_real.py`) precisa de instância em modo dev (cookie `Secure` em produção).

---

## 11. Régua ÚNICA de cobertura da carteira — decidida pelo João (28–29/09/2026) · IMPLEMENTADA 29/09 (não commitada)

Origem: "a positivação do Juliano na Performance está nota 10, mas no Gerencial está menos de 60% —
estamos usando medidas diferentes?" Sim: eram três (Gerencial = carteira 24m, em dia ≤ 30 d, qualquer
vendedor, janela móvel · Vendedores = base ativa 12m, atendidos POR ELE no mês fechado · Performance =
índice relativo ao mix ABC). JULIANO #29: 61,7% · 66,7% · índice 1,38 → 10.

**Decisões (João):**
- UMA métrica em todas as abas (Gerencial, Vendedores, Performance) e em todos os níveis (empresa,
  time, vendedor): **% da base ativa positivada na janela**.
- **Base** = clientes cadastrados no vendedor/time que compraram nos últimos 12 meses (≈ 6.815 na
  Multpel — os "7 mil ativos" dele). Sai a carteira de 24 meses.
- **Janela = 60 dias.** Na Performance, a janela que termina no último dia do mês fechado (a nota não
  anda todo dia); no Gerencial, 60 d por padrão (móvel).
- **Positivado = comprou de QUALQUER vendedor** ("força o vendedor a colocar os clientes na sua base").
  Empresa = Σ times = Σ vendedores.
- **Nota da Performance** = mesmas faixas das metas aplicadas ao % (meta de cobertura = **100%** para
  todos): < 85% → 0 · 85–89,99% → rampa 6→7 · 90–99,99% → 9 · 100% → 10. Sai o índice ABC.
- **Limiar do Gerencial = 85%** (o piso da nota), com cor por faixa (vermelho < 85 · amarelo 85–99,9 ·
  verde 100). Não 100%: pegaria 79 de 81 no alerta.
- **3 regras** aceitas ("ok, mete bala"): (1) mês de LIMPEZA — cobertura informativa, fora da nota, e
  cada vendedor recebe a lista dos não positivados para vender ou transferir; (2) transferência só para
  vendedor ATIVO, nunca para código fictício (senão o cliente some da meta de todos e a empresa segue
  igual); (3) relatório mensal de clientes retirados da carteira por vendedor.

**Medido no BI real (29/09/2026):**
- 60 d até hoje: empresa 58,4% (3.977 de 6.815); vendedores mediana 63%, p90 84%; 75 de 81 < 85%.
- 36% da base ativa não compra em 60 d por natureza: 24% compraram 1 vez no ano, 12% ciclo > 60 d. Teto
  do vendedor mediano (atendendo todo cliente de ciclo ≤ 60 d) = 69%. Chegar a 85% sem vender mais =
  soltar 1.454 clientes (mediana 12 por vendedor).
- Simulação da nota (ago/26, janela jul+ago, base 12m): campo 5,66 → 1,42 de média; 37 de 41 com
  nota 0 na cobertura; JULIANO 10 → 2,86; MARCIO #899 (96,5%) 10 → 9,29. ⚠️ No banco LOCAL não há
  metas de ago/26: a nota é parcial e a cobertura pesa 71% dela. Com metas cadastradas o impacto
  máximo da cobertura é 2,5 pontos — conferir as metas de produção antes de ligar.

**Implementado (29/09/2026, não commitado; testes escritos antes de cada etapa):**
- `cobertura.py`: `na_base` (365 d) · `positivado` (60 d) · `cobertura_por_dono(ultima, dono, data)` — a MESMA
  conta para o Gerencial (hoje) e para o mês fechado · `lista_limpeza` · `linhas_foto`. `agregar_niveis` filtra a
  base de 12 m por dentro (tela, CSV, PDF e e-mail de alerta usam ele). Padrões: janela 60, limiar 85.
- Gerencial: abre em 60 d (antes abria FIXO em 30 e ignorava o Admin); cor = faixas da nota (< 85 vermelho ·
  < 100 amarelo · 100 verde); botão "⬇ Lista de limpeza" (CSV) só no drill de time/vendedor.
- Performance: `NOTA_VERSAO` 3; cobertura = % da régua única nos 60 d até o fim do mês fechado, meta 100% nas
  faixas (`ATINGIMENTO_COBERTURA`); sai o índice ABC (`cobertura_ajustada` e `por_classe` removidos). Mês de
  limpeza: config `cobertura_na_nota_desde` (padrão 202611, campo novo no Admin da cobertura) — antes dela a
  cobertura é INFORMATIVA (aparece, sai da nota e do peso total, para ninguém virar "parcial").
- Vendedores: "cobertura da base" = a mesma régua (`_cobertura_mes_fechado`, insumo comum com a Performance).
- Foto do cadastro: tabela `carteira_foto (anomes, codcli, codusur, codsupervisor)` gravada todo dia às 23h50
  (a última do mês vale), sem tela; `init_db` e o próprio job criam a tabela.
- Agente: glossário/consultas com a régua única (Gerencial = hoje × Performance = fim do mês), limiar 85%,
  faixas oficiais, "ninguém tem nota" declarado quando for o caso.
- Validado no BI real: JULIANO 35 de 45 = 77,8% (fim de agosto, Performance = Vendedores) e 63% hoje
  (Gerencial); empresa hoje 58,4%; suíte 1.163 passando (+ as 3 falhas de data de sempre).

**⚠️ Deploy (Gabriel):**
1. `init_db` (cria `carteira_foto`; o job também cria).
2. **Admin → cobertura: limiar 85 e janela 60.** Os valores SALVOS (60 e 30 hoje) vencem os padrões novos —
   medido no banco local: sem trocar, o Gerencial segue em 30 d / 60%.
3. Conferir "Cobertura entra na nota a partir de" = 11/2026.
4. **Metas de setembro cadastradas no app ANTES de subir.** Sem metas e com a cobertura informativa, só a
   frequência é medida (13% < 35%) e a Performance fica SEM NOTA para todos em outubro.
5. Avisar os supervisores: os números de cobertura mudam nas três telas.

---

## 12. Plano de ação por cliente (CRM leve) + saldo da Recuperação — pedidos do João (29/09/2026) · IMPLEMENTADOS (não commitados)

### CRM leve — `plano_cliente.py` (puro) + `static/plano-cliente.js` (componente único)
- **Por que:** a lista do dia diz QUEM contatar, mas não guardava O QUE foi feito. A loja da Matriz já usa o
  painel no dia a dia (as outras lojas estão em implantação) — o registro acontece onde eles já trabalham.
- **Decisões dele:** 6 status de um toque (Ligação feita · Sem contato · Retorno agendado · Pedido prometido ·
  Não compra mais · Transferir), descrição opcional; só o RETORNO AGENDADO aceita data futura (e exige);
  retorno de hoje (ou atrasado) aparece EM DESTAQUE no topo de "A ligar hoje", mesmo fora da previsão do
  ciclo; quem tem acesso à lista registra (porta de escopo = `_carteira_no_escopo`, autor gravado);
  "zera quando compra" pela DATA — registro até o dia da última compra vira "acompanhamento anterior"
  (nada é apagado: no recuperado, é o histórico do que levou à volta).
- **Onde está:** Próximo Pedido (coluna Plano, destaque do retorno, card "Com tratativa X de N" — clique =
  só os sem tratativa —, card "Retornos p/ hoje", filtro Tratativa), Recuperação (listas em risco e
  recuperados) e a ficha do cliente (drill 360°, que abre de várias telas). Próximas: Mix, Radar,
  Carteira › visão geral e o CSV da lista de limpeza — é só chamar `PlanoCliente.selo(...)` na linha.
- **Banco:** tabela `cliente_plano` no banco de LOGIN (init_db + a própria rota criam). Rotas
  `GET/POST /api/plano/<codcli>` e `POST /api/plano/resumo` (selos em lote, uma chamada por lista).
- **Validado:** 11 testes (`tests/test_plano_cliente.py`) e ponta a ponta no navegador como supervisor da
  demo (registrar, histórico, retorno no topo, card 0→2 de 13, filtro, Recuperação, ficha).
- ⚠️ Na demo LOCAL a base está parada em 21/08: a data da ação sai 21/08 com o registro em 29/09. Na
  Multpel e na demo de produção (base anda sozinha) o "hoje" é o dia.

### Saldo do mês na Recuperação
- **Conta (confirmada pelo João):** clientes que ENTRARAM em risco no mês − RECUPERADOS do risco no mês (e o
  mesmo em R$/mês), na carteira do DONO. Positivo = carteira diminuindo; negativo = avançando. Mesmos
  critérios da ponte da empresa (resgatado de perdido e "virou perdido" ficam fora), então fecha com ela.
- **Onde (ele marcou no print):** faixa "Saldo do mês" DENTRO do quadro da ponte e coluna "Saldo" no FIM das
  tabelas de times e vendedores. Para quem vê a empresa, a tabela de times ganhou a linha "Sem time
  (código fictício/sem supervisor)", por último — sem ela a soma dos times não fechava (BI real ago/26:
  169 × 219; o 999 sozinho é +43).
- **BI real (ago/26):** empresa +219 clientes / +R$ 136.062/mês (entraram 602 − recuperados 383); piores
  times G. VITORIA +31, AFONSO +27; entre vendedores, 29 JULIANO −4 e 1422 LEANDRO −6 avançaram.
- Testes: `tests/test_saldo_recuperacao.py` (4). Motor: `recuperacao.placar_de` ganhou entraram,
  rec_risco_da_base, saldo e valor_saldo.


---

## 13. Rodada 05–06/10/2026 — ONDE PARAMOS (handoff para o próximo chat)

> **Estado:** tudo abaixo está na `main` e **em produção** até `7b23034` (o deploy foi confirmado
> pelo print do Gabriel: clique no vendedor funcionando na Evolução). **Única coisa local, não
> commitada:** a correção do "saldo zero" (item 13.3). Tudo tem teste; suíte completa em
> 06/10/2026: **1.246 passam, 3 falham** (as 3 de sempre: radar CSV, mix, cohort — fixture de data).

### 13.1 O que entrou (commits na `main`)
| Commit | O quê |
|---|---|
| `1926a70` | Carteira: coluna **Margem 12m** (lucro÷venda líquida 12m; sem venda = "—", fim da ordenação; CSV/PDF). Recuperação: leitura (colunas Entraram em risco, Por outros, "do risco + da perdida", R$ rotulado "venda no mês", datas da ponte e da lista). Plano: **fuso** (`criado_em` TIMESTAMPTZ, hora de Brasília) e **✕ excluir** (autor 24 h, admin sempre, exclusão lógica + log). |
| `9cd5599` / `ef3175a` | Aba **Evolução da carteira** (`/evolucao`, `evolucao_carteira.py`) + fotos `vendedor_foto` e `uso_mensal` no job das 23h50; gráfico da Recuperação vira **entraram × recuperados** (Clientes / R$/mês). `ef3175a` é commit vazio para redisparar o Actions (incidente do GitHub). |
| `1257811` | **Saldo de recuperação = recuperados − entraram** (positivo = bom; pedido do João: "na nossa cabeça + é bom"). Evolução: `variacao` → `saldo`/`saldo_medio`; caches `recuperacao:resumo:v4`, `evolucao:…:v2`. Coluna/filtro/card "usa × não usa" **fora da tela**. |
| `7b23034` | Estoque › Evolução: **⬇ Excel** da "Foto dia a dia" (`/estoque/api/evolucao.xlsx`, ADM-only, `_evolucao_serie` = fonte única). Evolução da carteira: **clique no vendedor** troca cards e gráficos. Recuperação: **filtro Plano** nas listas (sem/com/último status), no SERVIDOR antes do corte de 300; CSV com o filtro + colunas do plano. **Manuais** `docs/MANUAL_COMERCIAL.md` v4.0 (reescrito) e `docs/MANUAL_COMPRAS.md` v5.0. |

### 13.2 Pedido PENDENTE do João (Gabriel decidiu juntar pedidos antes de mexer)
**Botão Clientes | R$/mês no gráfico "Saldo de recuperação" da Evolução** (print 06/10 17:19).
Análise crítica já feita e combinada como desenho (não implementado):
- o dado já existe (`valor_saldo` por mês) — zero query;
- o botão troca **gráfico + card de saldo + coluna de saldo da tabela antes×depois** (senão a tela
  se contradiz) → `evolucao_carteira._periodo` precisa da média em R$ (`saldo_medio_valor`);
- **cor pelo PRÓPRIO sinal** (clientes e R$ discordam no mesmo mês — lição `clsSinal` da Recuperação);
- balão mostra **os dois** números (em carteira pequena um cliente grande pinta o mês inteiro);
- **o gráfico de cobertura NÃO ganha o botão** (cobertura em R$ = outra métrica, a "cobertura por
  valor" do Gerencial; o João circulou só o de saldo).
O Gabriel vai esperar o João olhar a aba e juntar com o que mais ele pedir.

### 13.3 Local, NÃO commitado
**Saldo zero vira traço cinza** no gráfico de saldo da Evolução (`minBarLength:3`, cinza quando
`x.saldo === 0`). Origem: IGOR #? em 12/25 (8−8) e 08/26 (13−13) aparecia como buraco e parecia dado
faltando. Arquivos: `evolucao.html` + `test_saldo_zero_aparece_no_grafico` em
`tests/test_evolucao_carteira.py`. Commitar junto com o 13.2.

### 13.8 Lote de 08/10/2026 — implementado LOCAL (não commitado), junto com o 13.2 e o 13.3
1. **Botão Clientes | R$/mês na Evolução** (o 13.2, João reforçou: *"o de receita é tão ou mais
   importante"*). Troca gráfico + card + coluna de saldo da tabela; `saldo_medio_valor` no
   `_periodo`/`delta`; cache `evolucao:…:v3`; `?modo=valor` na URL; balão traz os dois números; a
   régua avisa que R$ é **valor mensal**, não faturamento. Cobertura não ganha o botão.
2. **"Pedido feito" no Plano de ação** (João: *"tem rota que leva tipo 3 dias para ser faturada"*).
   7º status. Até `PRAZO_FATURAR` = **5 dias** o cliente sai de TODAS as janelas e dos cards
   ("aguardando faturar" — card próprio e opção no filtro Tratativa); passou disso sem NF, volta **em
   destaque** no topo de "A ligar hoje" (card "Pedido não faturou"): pedido travado em crédito/corte
   não pode sumir. Quando a NF sai, o `separar` arquiva sozinho. `_proximo_elegiveis` virou fonte
   única da tela, CSV e PDF (os exports ignoravam retorno/tratativa e saíam diferentes da tela).
   Recuperação: "Pedido feito" no filtro Plano. ⚠️ Não usamos o `PCPEDC` do dataset META para
   detectar sozinho porque ele esvazia na virada do mês (pedido do dia 30 faturado no dia 2 sumiria).
3. **Estoque › Parado sem barra lateral** (a pedido do Gabriel, conforme combinado com o João):
   sem coluna Faixa; Última venda virou tooltip do Dias parado; Disp. e Disp. cx numa célula
   (`236 · 20 cx`); fornecedor sem LTDA/S.A./EIRELI/EPP/"INDUSTRIA E COMERCIO DE" e com 200 px
   (nome completo no title); produto em 280 px. Medido a 1450 px de largura: sem rolagem lateral.
Testes: `test_plano_cliente.py` (+2), `test_evolucao_carteira.py` (+2).

### 13.4 Backlog da Evolução da carteira (medido, não pedido ainda)
1. **Classificação "usa × não usa" está CONTAMINADA** — por isso saiu da tela. Em produção, 10 times
   com exatamente "11 dias em 09/26": um usuário supervisor com quase todas as áreas marca todos.
   Para voltar: usuário com ≥ 4 áreas = gestão (não marca time); amostra mínima de base (~50); canais
   (DIRETORIA, BIMBO, E-COMMERCE, E COMMERCE MARTINS, PROSPECÇÃO) fora do universo campo. Antes de
   mexer, rodar no servidor a consulta de `uso_mensal` por usuário × nº de áreas (está no chat de
   06/10; o backend segue gravando `uso_mensal` e aceitando `?uso=`).
2. **Sazonalidade** (medido no BI, régua da tela, empresa): cobertura cai de mai–jul para ago–set
   TODO ano — 2025: 68,2% → 66,2% (−2,0 p.p.); 2026: 60,7% → 59,4% (−1,4 p.p.). Nível 2026 ~7 p.p.
   abaixo de 2025; recuperados ago–set/26 = 384/410 × 435/489 em 2025. Próximo passo: série de 24
   meses e coluna "Δ no mesmo período do ano anterior".
3. Fase 2: receita/margem e nota da Performance mês a mês (query nova por mês).

### 13.5 Concorrente Systock (pesquisa de 06/10) — o que absorver
Resumo na memória `joga_concorrente_systock`. Medido no BI da Multpel (Atacado, régua oficial lead 10
+ cobertura 30 = 40 dias para todo item):
- itens Z: 22,5% ruptura **e** 48,6% excesso; curva C: 42,5% dos itens > 120 dias (R$ 810 mil de
  R$ 1,0 mi); fornecedor com lead real > 21 d: 21% de ruptura;
- **lead real por fornecedor** na sugestão: +3,8% de capital, resolve os 62 itens de lead longo →
  **absorver** (já medimos o lead na aba Lead time);
- estoque de segurança estatístico ("IA" deles): +85% de capital → **não absorver**;
- transferência entre filiais: só R$ 19 mil (1,6% da compra) → **não construir para a Multpel**;
- popularidade: 97% dos itens mais populares já são A/B → absorver só **ruptura ranqueada por
  clientes atingidos** (40 itens populares em ruptura, 3.321 relações cliente-produto em 90 d);
- **o excesso não vem da fórmula:** 508 itens C em excesso (R$ 752 mil), só 11 com pedido aberto →
  é liquidação/processo, não parâmetro.
Scripts da análise (só leitura) ficaram no scratchpad do chat de 06/10 (não versionados).

### 13.6 Relatório de performance do estoque do João (análise de 05/10)
Os números batiam com a foto (unidade **Atacado**), mas o dia escolhido inflava 3 claims. Versão
defensável (média de período, dias úteis): ruptura geral 11,0% → 10,0% (−9%, não −37%: os ~16% de
maio não foram medidos); curva A 9,3% → 7,7% (set) → 6,8% (out); capital parado set **subiu 2%**
(170,3 mil × 166,9 mil) — queda só de 29→30/09; pedidos abertos −46% é efeito de fim de mês (fim×fim
= −11,6%; o número bom é atrasados 34,9 → 23,9); "0 vencidos em setembro" = nenhuma baixa LANÇADA
(junho R$ 360, agosto R$ 8.078) e o valor A VENCER subiu 2,28 → 2,90 mi. História forte e verdadeira:
estoque +R$ 600 mil com ruptura caindo e parado estável (2,7% → 2,2%). Comprador 47 (João): curva A
3,4% → 1,3%, parado −12%; 101 e 100 aumentaram o parado.

### 13.7 Lições operacionais da rodada
- **GitHub Actions travou (incidente do GitHub, 05/10):** job cancelado sem runner → `:latest` não
  mudou e o deploy subiu a imagem antiga. Diagnóstico em 30 s: `/evolucao` 404 com `/admin` 401 =
  imagem velha. Conferir o Actions verde ANTES do `service update`. Plano B: build no servidor
  (como o Tabela Auditoria) ou local + push (o push ao GHCR pela sessão do Claude é bloqueado pelo
  classificador — o Gabriel roda o `docker push`).
- **Docker Desktop local desligado = Redis fora** → todo request local espera ~27 s. Scripts de
  análise contra o BI: trocar `server._R`/`_R_LOGIN` por `fakeredis` no topo (padrão do conftest).
- Respostas ao João: o Gabriel prefere que o João **pense** — mandar a pista, não a resposta inteira.
