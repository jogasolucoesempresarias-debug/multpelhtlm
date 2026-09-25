# Agente de IA do Comercial — conteúdo aba por aba

> **Para o chat que vai construir o Agente do Comercial.** Complementa o §10 do
> [PLANO_MELHORIAS_COMERCIAL.md](PLANO_MELHORIAS_COMERCIAL.md) (pilares, glossário, como testar).
> Aqui está, para CADA aba: o que ela responde, de onde sai o dado (endpoint + campos reais),
> perguntas por perfil, armadilhas e o que vai para o contexto fixo × consulta sob demanda.
> Levantado em 25/09/2026 chamando todos os endpoints contra o BI real (números abaixo são reais:
> mês corrente = set/26 até o dia 25; mês fechado = ago/26).
> Modelo: **gpt-4.1-mini** (decisão tomada — não re-sugerir Claude).

---

## 0. Desenho (decidido na conversa de 25/09/2026)

**Um agente só para o Comercial, em 3 camadas** — não um por tela:
1. **Panorama por PERFIL** (sempre no contexto, curto), montado no servidor dentro do escopo:
   vendedor = a carteira dele · supervisor = o(s) time(s) · diretor/admin = empresa + times.
2. **Consultas sob demanda** (function calling do gpt-4.1-mini, teto de 3 por pergunta):
   `cliente`, `vendedor`, `time`, `produto`, `departamento` — executadas COM A SESSÃO do usuário
   (RBAC do app; nunca ampliam o escopo). Número vem pronto das funções; o modelo narra.
3. **Consciência de tela**: o widget manda `tela` + filtros (como no Compras); a tela escolhe
   as SUGESTÕES e o que vem primeiro no contexto, mas o agente responde sobre qualquer assunto.

**Por que não um agente por tela:** as perguntas reais cruzam abas. A do João em 25/09 ("por que
o JULIANO tem nota 10 se a cobertura dele é baixa?") passa por Performance + Vendedores +
Gerencial e só se responde explicando QUAL RÉGUA cada tela usa.

**4 usos que viram botão/sugestão:** (1) "Minha lista de hoje" · (2) "Preparar visita ao
cliente X" · (3) "Explique este número" · (4) "Onde meu time perde dinheiro".

**Decidido pelo Gabriel (25/09/2026):**
- **O VENDEDOR vai usar a IA** → o perfil vendedor é o público principal. "Minha lista de hoje" e
  "Preparar visita ao cliente X" deixam de ser a última fase e viram o NÚCLEO da 1ª entrega.
- **Só na DEMO por enquanto** (`demo.jogasolucoes.com.br`, `DATA_SOURCE=postgres`, base sintética).
  A Multpel segue no estado `off` (sem `ia` no `MODULOS` — nem o botão aparece). Consequências:
  - tudo que o agente consulta tem de funcionar no **modo postgres** (os endpoints do §3 já têm
    branch; qualquer função nova precisa do espelho no `provider_sql.py` e entrar no
    `test_comercial_endpoints_sweep_modo_postgres`). A rede de segurança (`execute_dax` levanta em
    postgres) protege contra vazar dado real na demo — NÃO remover;
  - o `docker-compose.demo.yml` hoje tem `MODULOS: "comercial,compras"` → acrescentar `ia` e a
    `OPENAI_API_KEY` na stack da demo (conferir se já está no Portainer; lembrar que reaplicar a
    stack pode voltar a imagem — rodar o `docker service update --image …:latest --force` depois);
  - a demo só tem o usuário ADMIN → para mostrar o agente como vendedor e supervisor, o seed da
    demo precisa criar **usuários de demonstração por perfil** (1 vendedor com carteira boa, 1
    supervisor) — pelo mesmo caminho do `bootstrap_demo.sh` (trava `DEMO_SEED=1`, recusa `multpel_db`);
  - nomes e números da demo são sintéticos: a bateria de perguntas (§6) precisa de uma versão
    com clientes/vendedores da demo; as respostas esperadas do §6 com dado real servem para o
    dia em que ligar na Multpel.

### Fases (reordenadas pelo público vendedor)
1. **Vendedor, na demo:** panorama do vendedor (carteira dele: em risco com chance de voltar,
   Próximo Pedido até 60 d, recuperados, nota e metas) + consulta `cliente` + os dois botões
   ("Minha lista de hoje", "Preparar visita ao cliente X") + glossário + usuários de demo.
2. **Supervisor e diretor:** panoramas por time/empresa + consultas `vendedor` e `time` +
   "Explique este número" e "Onde meu time perde dinheiro".
3. **Consultas `produto` e `departamento`** + sugestões de todas as telas (§5).
4. **Ligar na Multpel** (quando decidirem): antes, corrigir os defeitos do §1 e rodar a bateria
   do §6 com dado real.

---

## 1. ⚠️ Corrigir ANTES de o agente citar (ele repetiria o número errado com convicção)

| # | Onde | O problema (medido 25/09/2026) | Tratamento |
|---|---|---|---|
| 1 | Dashboard, card **"Clientes Novos"** | Usa a medida do BI `[TOTAL CLIENTES NOVO]`, que é **igual ao total de clientes distintos** (decodificada em 24/09: RCA 2 → 128 = 128 no mês; empresa → 6.889 = 6.889 em 12m). Hoje o card mostra 2.745 "novos" com 2.748 positivados. | Não levar para o contexto até corrigir (cliente novo = 1ª compra da vida no período; dá para calcular do `cohort`). Mesmo defeito da `[TAXA POSITIVACAO CLIENTE]`. |
| 2 | Cockpit do vendedor, alerta **"At Risk"** | Texto diz "R$ 482/**ano** de lucro em risco", mas o valor é `lucro_perdido_proj`, que é ACUMULADO (lucro mensal × meses de atraso), não anual. | Corrigir o rótulo ("acumulado"); o agente usa o valor mensal da Recuperação. |
| 3 | Gerencial, **"abaixo do limiar"** | 105 de 118 vendedores e 15 de 16 times abaixo de 60% (cobertura ≤ 30 d da empresa = 35,5%). Alerta que marca quase todo mundo não diferencia ninguém. E inclui canal (E-COMMERCE MARTINS, 0%). | Agente não deve anunciar "105 vendedores críticos"; recalibrar limiar com o João ou comparar com a mediana do universo. |
| 4 | Carteira › **Próximo Pedido**, ordenação | Ordena por `rfm.prioridade_contato` (valor × dias ÷ ciclo), que explode com ciclo curto: 1º da lista tem ciclo 7 e 142 dias de atraso (prioridade 164 mil). É o mesmo defeito corrigido na Recuperação. E a janela "vencidos" traz atrasos de 142 dias — cliente que já é "em risco" (> 60 d) aparece nas duas listas. | Para a "lista do dia" do agente: até 60 d de atraso → Próximo Pedido; > 60 d → Recuperação (ordem valor × chance de voltar). Nunca juntar as duas contagens. |
| 5 | Radar | "CONSUMIDOR FINAL" (codcli 1, vendido pelo caixa RCA 4) entra como cliente — é venda de balcão sem identificação. | Excluir codcli 1 (e equivalentes) de qualquer "cliente que parou". |

---

## 2. Glossário (resumo — completo no §10 do plano)

- **Quatro "positivações"** — o agente SEMPRE diz qual:
  1. *positivação da classe ABC* (Carteira): % dos clientes da classe que compraram no mês, de qualquer vendedor;
  2. *cobertura da base* (Vendedores/Performance): base ativa atendida POR ELE no mês fechado;
  3. *alcance*: tudo que ele atendeu ÷ base (passa de 100%);
  4. *"positivados" do cockpit*: clientes da carteira (24m) que compraram em 12m — quase sempre = cadastrados (MARCIO: 89 de 89). **Não é taxa mensal.**
- **Três "receitas em risco"**: valor mensal em risco (Recuperação) · receita perdida acumulada (Gerencial/Próximo Pedido) · "receita em risco" do Radar (venda 12m dos produtos que o cliente parou). Nunca somar nem comparar entre si.
- **Duas "coberturas"**: Gerencial = cliente em dia (≤ 30 d, configurável) sobre a carteira 24m; Vendedores = cobertura da base no mês. Performance usa a da base AJUSTADA pela classe ABC (índice; 1,00 = média).
- **Duas "curvas ABC"**: de CLIENTES (Carteira; classe do início do mês) × de PRODUTOS (aba Curva ABC; Pareto da janela escolhida, régua de venda).
- **Dois "ticket médio"**: Dashboard/Vendedores usam a medida do BI (R$ 725 no mês corrente; R$ 156 em ago por item) — não é ticket por cliente (R$ 2.122/mês em ago) nem por pedido (R$ 720).
- **Réguas de acesso**: VENDA (quem faturou) × CADASTRO (de quem o cliente é). Lojas/Diretoria divergem 40–66%.
- Inativo > 60 d **e** além do ciclo · perdido > 365 d · ERP marca inativo aos 91 d.
- Margem das Metas pelo BRUTO; do Dashboard/Categorias/ABC pela LÍQUIDA.
- Devolução por DTENT. Sempre o CÓDIGO de cliente/vendedor/produto na resposta.

---

## 3. Aba por aba

Formato: **Responde** · **Dado** (endpoint → campos reais) · **Perguntas** (V = vendedor,
S = supervisor, D = diretor/admin) · **Armadilhas** · **Contexto** (📌 fixo / 🔎 consulta).

### 3.1 Dashboard (`/`)
- **Responde:** como está o mês (venda, lucro, margem, clientes, mix) e contra o ano passado.
- **Dado:** `/api/dashboard/kpis` → `primarios{venda_liquida 5.453.163, lucro_total 998.209, margem 18,3%, ticket_medio}`, `secundarios{clientes_positivados 2.748, total_mix 13.804, valor_medio_peso}`, `yoy` (12m: receita −5,7%, lucro −6,0%, positivação −4,1%), `yoy_mes` (mesmos dias: receita −13,3%, positivação −21,2%) + `yoy_mes_info{periodo "01–25/set/26 vs 01–25/set/25", dias_uteis 19×19}` · `/api/dashboard/serie` (13 meses venda/lucro) · `/api/dashboard/top-clientes` (top 10 por lucro 12m; nº 1 L S NASCIMENTO DE ILHEUS #121154, R$ 568 mil).
- **Perguntas:** D "como estamos no mês?" · D "por que a receita caiu 13% contra set/25?" (→ consulta times/deptos) · S "quanto meu time representa?" · V "como está minha venda no mês?".
- **Armadilhas:** o mês corrente é PARCIAL (comparar sempre mesmo período — `yoy_mes` já faz); `yoy` (12m) e `yoy_mes` são perguntas diferentes; "Clientes Novos" com defeito (§1); ticket médio do BI (§2).
- **Contexto:** 📌 KPIs do mês + as duas variações declaradas com o período · 🔎 série e top clientes.

### 3.2 Carteira (`/carteira`) — RFM, praça, curva ABC de clientes, receita × positivados, Próximo Pedido
- **Responde:** a saúde da carteira (quem compra, quem sumiu, onde está o valor) e quem ligar.
- **Dado:** `/api/carteira/rfm` → `total_clientes 8.641` (universo 24m), `segmentos{champions 719, loyal 1.550, potential_loyalist 1.124, at_risk 550, cant_lose 456, new 66, hibernating 1.151, lost 3.025}`, `regua{ok, normal, atencao, urgente 4.194}`, `histograma_recencia`, `matriz_rf` · `/api/carteira/clientes` (linhas com `classe_abc, segmento, recencia_dias, ciclo_pessoal, dias_atraso, venda_12m, receita_perdida_proj, telefone, vendedor, time`) · `/api/carteira/positivacao-abc` (A 80,9% · B 56,1% · C 22,3% em ago; mês corrente até o dia N × mesmo dia do anterior) · `/api/carteira/receita-positivacao-12m` (12 meses venda × clientes únicos) · `/api/carteira/proximo-pedido` → `cards{hoje 72, proximos7 790, vencido15 591, receita_risco}` + linhas priorizadas.
- **Perguntas:** V "quem eu ligo hoje?" · V "quais clientes A meus não compraram este mês?" · S "qual segmento cresceu?" · D "onde estão os clientes perdidos?" (praça) · D "nossa positivação de clientes A está caindo?".
- **Armadilhas:** 8.641 inclui 3.025 "lost" (quem comprou 12–24m atrás) — não é a carteira ATIVA (≈ 6.900); segmento "at_risk" (RFM) ≠ "em risco" da Recuperação (> 60 d + ciclo); classe ABC é a do início do mês; Próximo Pedido — §1 item 4; `receita_perdida_proj` é acumulada.
- **Contexto:** 📌 segmentos + positivação por classe + cards do Próximo Pedido · 🔎 `cliente(x)` (ficha 360°: `/api/carteira/cliente/<cc>` → histórico 13 meses, top 5 deptos, ciclo, segmento, classe; `/produtos` → top 10 produtos 12m).

### 3.3 Vendedores (`/vendedores`) + cockpit (`/vendedor/<cod>`)
- **Responde:** quem vende mais, quem cobre a própria base, quem atende fora dela, e o detalhe de uma pessoa.
- **Dado:** `/api/vendedores` (90 RCAs tipo R) → `venda_liq, lucro, ticket_medio, yoy_receita, rank` (12m) + `taxa_positivacao` (= cobertura da base), `base_ativa, base_coberta, fora_base, fora_base_tipo{colega, liberado, ficticio, rca_inativo}, alcance, mix_medio, cross_medio, marca_medio, freq_media, positivacao_mes` · `/api/vendedor/<cod>` → `kpis` (os mesmos) + `carteira{cadastrados, positivados, champions, at_risk}` + `comparativo_equipe{sua_taxa, media_equipe, label}` + `perfil` · `/alertas`.
  Ex.: MARCIO #899 — cobertura 85% (74 de 87), fora da base 4 (3 colega, 1 fictício), alcance 90%, média do time 56%. JOSE JUNIOR #879 — 64% (49 de 77), mix 12,1 · deptos 4,3 · marcas 3,2.
- **Perguntas:** S "quem do meu time cobre menos a base?" · S "quem está atendendo cliente de colega?" · D "por que o fulano tem 150% de alcance?" · V "como estou contra o meu time?".
- **Armadilhas:** ranking ordena por LUCRO 12m (não é a nota da Performance); cobertura é do mês FECHADO, venda/lucro são 12m — janelas diferentes na mesma linha; "fora da base" de fictício/RCA inativo = cadastro a transferir, não desempenho; `positivados` do cockpit (§2); alerta "/ano" (§1).
- **Contexto:** 📌 (S/D) ranking resumido do escopo; (V) a própria linha · 🔎 `vendedor(x)`.

### 3.4 Categorias (`/categorias`)
- **Responde:** o que vendemos (departamentos, fornecedores) e com que margem.
- **Dado:** `/api/categorias` (32 deptos, 12m) → `venda, lucro, margem, share, clientes_unicos, produtos_unicos` (ex.: MATERIAL DE LIMPEZA 18,3% da venda, margem 11,7%, 4.429 clientes) · `/api/fornecedores` (top 30) · `/api/categorias/<cod>/clientes` (top 50 clientes do depto).
- **Perguntas:** D "qual departamento puxa a margem para baixo?" · S "quais clientes do meu time mais compram limpeza?" · V "quem compra descartáveis na minha carteira?".
- **Armadilhas:** régua de CADASTRO (carteira do escopo) — números de time não batem com Curva ABC (régua de venda); margem pela líquida.
- **Contexto:** 📌 (D) top deptos por venda e os de pior margem · 🔎 `departamento(x)`.

### 3.5 Curva ABC de produtos (`/abc`)
- **Responde:** quais produtos fazem 80% da venda do escopo (time, vendedor ou cliente) e com que margem.
- **Dado:** `/api/abc` → `resumo{total_produtos 3.638, concentracao_pct_itens 13,9, margem 18,53}` + linhas (`classe, rank, codprod, descricao, depto_nome, fornec_nome, venda, lucro, margem, clientes, pct_acum`) + `regua`, `periodo` (12m/6m/3m/mês atual), `amostra_ok` · `/api/abc/produto/<cod>` → `serie` 13 meses + `vendedores` que vendem. Nº 1: DETERGENTE LIMPOL 500ML #42253 (R$ 2,36 mi, margem 6,65%).
- **Perguntas:** S "quais os produtos A do meu time?" · S "qual a curva do cliente X?" · D "o produto nº 1 da empresa tem margem de 6%?" · V "o que eu mais vendo?".
- **Armadilhas:** régua de VENDA (≠ Carteira); trocar o período muda quais itens são A; amostra pequena (< 200 produtos ou < R$ 100 mil × meses/12) = aviso, não conclusão; com cliente filtrado é concentração do que ele compra.
- **Contexto:** 🔎 `produto(x)` e a curva do escopo sob demanda (é grande: 3.638 linhas).

### 3.6 Mix abandonado (`/mix`)
- **Responde:** clientes que PARARAM de comprar um departamento que compravam (há X dias).
- **Dado:** `/api/mix/abandonado` (dias=60) → 27.124 pares cliente × depto (`cliente, depto_nome, dias_sem_comprar_categoria, venda_cat_12m, venda_total_12m, vendedor, telefone`) · `/api/mix/abandonado/<cc>/deptos` (ex.: UNIMED SUL CAPIXABA #120367 — 11 deptos parados, BOBINA PICOTADA há 84 d, R$ 108 mil/12m) · `/api/mix/cliente/<cc>/fornecedores`.
- **Perguntas:** V "o que meus clientes pararam de comprar?" · V "o que oferecer para o cliente X?" · S "qual departamento meu time está perdendo?".
- **Armadilhas:** 27 mil pares — nunca listar, AGREGAR (por depto, por vendedor) e trazer top N; cliente que parou TUDO está na Recuperação, não aqui (aqui ele segue comprando outra coisa).
- **Contexto:** 🔎 no `cliente(x)` (deptos parados entram na "preparar visita") e agregado por depto do escopo.

### 3.7 Radar (`/radar`)
- **Responde:** produtos que estão PERDENDO clientes (janela recente × anterior) e, por cliente, os itens que ele parou.
- **Dado:** `/api/radar/board` (1.532 produtos; janela 60 × 60 d) → `clientes_perdidos` (conjunto: comprou antes e parou), `clientes_ant/rec`, `queda_receita`, `pct_queda`. Ex.: MILHO VERDE PREDILECTA 1,7KG #58702 — 147 clientes pararam, queda R$ 96 mil · `/api/radar/produto/<cod>` → `kpis{clientes 471, parados 460, trocaram 273, receita_em_risco}` + clientes · `/api/radar/cliente/<cc>` → produtos parados do cliente (L S NASCIMENTO: 140 produtos parados, "receita em risco" R$ 126 mil — e ele é CHAMPION, comprou há 7 dias).
- **Perguntas:** D "que produto estamos perdendo?" · S "por que o milho verde caiu?" (trocaram = migraram para outro item) · V "o cliente X parou de comprar o quê?".
- **Armadilhas:** board (60 × 60 d) e drill (12 m) são perguntas diferentes (tooltip declara); "receita em risco" do Radar é o 3º sentido; cliente ativo com produtos parados = erosão de MIX, não churn; `trocou` = migrou para similar (não é perda); CONSUMIDOR FINAL (§1).
- **Contexto:** 📌 (D/S) top 5 produtos em queda do escopo · 🔎 `produto(x)`, e produtos parados no `cliente(x)`.

### 3.8 Tendências (`/tendencias`)
- **Responde:** retenção por safra (coorte de aquisição): dos clientes que entraram em cada mês, quantos seguem comprando M+1…M+12.
- **Dado:** `/api/tendencias/cohort` (13 coortes; ex.: set/25 tamanho 198) → `retencao[]`, `absolutos[]` · `/api/tendencias/cohort/<aq>/<m>/clientes` (quem está no bucket; set/25 M+3: 71 clientes).
- **Perguntas:** D "os clientes novos estão ficando?" · D "a safra de 2026 retém melhor que a de 2025?".
- **Armadilhas:** retido = comprou NAQUELE mês (não acumulado); safras recentes têm poucas colunas; esta é a base certa para "clientes novos" (§1 item 1).
- **Contexto:** 📌 (D) retenção M+1/M+3/M+6 média das últimas safras · 🔎 lista do bucket.

### 3.9 Metas (`/metas`)
- **Responde:** meta × realizado × projeção de venda, rentabilidade, clientes e mix, por supervisor e vendedor.
- **Dado:** `/api/metas` → `dias{mes 21, decorridos 18, restantes 3}` + `supervisores[]` e `total` com, para cada métrica, `meta, realizado, falta, necessidade_dia, projecao, pct_realizado, pct_projecao` + `margem` · `/api/metas/vendedores?codsupervisor=` · `/api/metas/serie`.
  Set/26 (até dia 18 útil): venda realizada R$ 6,06 mi, projeção R$ 7,07 mi; margem 17,7%.
- **Perguntas:** V "quanto falta para eu bater a meta e quanto por dia?" · S "quem do meu time não vai bater?" · D "vamos bater o mês?".
- **Armadilhas:** dias ÚTEIS, não corridos; clientes e mix são contagem distinta — NÃO somam de vendedor para time; margem pelo BRUTO; mês corrente usa o dataset META (pedidos), mês fechado usa faturamento; sem meta cadastrada → `pct` nulo (não é 0%). **No banco local não há metas de set/26** — conferir em produção.
- **Contexto:** 📌 o mês corrente do escopo (V: a própria linha) · 🔎 `vendedor(x)` traz as 4 métricas.

### 3.10 Gerencial (`/gerencial`)
- **Responde:** cobertura da carteira (em dia ≤ 30/45/60 d) por empresa → time → RCA, com faixas de recência.
- **Dado:** `/api/gerencial/cobertura` → `empresa{cobertura_clientes 35,5%, cobertura_valor 82,3%, cobertura_ciclo 46,0%, base_morta 4.105, receita_em_risco (= receita perdida ACUMULADA) R$ 2,65 mi, buckets[6]}` + `times[]`, `vendedores[]` (pior → melhor), `abaixo_do_limiar{times 15, vendedores 105}`, `limiar_pct 60`.
- **Perguntas:** D "que time cobre menos a carteira?" · S "quais RCAs meus estão abaixo do limiar?" · D "por que a cobertura por valor é 82% e por cliente 35%?" (os clientes grandes estão em dia; a cauda sumiu).
- **Armadilhas:** universo = carteira 24m (inclui "lost") — por isso 35%; "base morta" 91+ ≠ "perdido" 365+ da Recuperação; alerta com limiar que pega quase todos (§1 item 3); canal e-commerce no ranking.
- **Contexto:** 📌 (D/S) cobertura clientes × valor do escopo · 🔎 `time(x)`.

### 3.11 Recuperação (`/recuperacao`)
- **Responde:** quanto da carteira está em risco, quanto voltou, quem trouxe de volta e o dinheiro na mesa.
- **Dado:** `/api/recuperacao` → `ponte{risco_ini 2.484, entraram 602, recuperados 383, viraram_perdidos 171, risco_fim 2.532, resgatados_perdidos 27}` + `valor_mensal` de cada etapa, `cards{em_risco_valor_mensal R$ 1,21 mi, venda_recuperada R$ 400 mil, dinheiro_na_mesa R$ 1,76 mi (a 1,21 + b 0,55), ficou 58,9%}`, `times[]`/`rcas[]` (em risco, recuperado da base × por ele, de outra base, potencial de positivação), `serie` 12 meses · `/api/recuperacao/listas?tipo=risco|recuperados` (+ `/csv`).
- **Perguntas:** V "quais clientes meus estão em risco e qual a chance de voltarem?" · V "quem eu recuperei?" · S "quanto meu time recuperou da própria base e quanto de outras?" · D "a carteira em risco está crescendo?" (sim: +24% jan→ago).
- **Armadilhas:** a ponte só fecha para a empresa; por time são duas leituras; crédito para quem vendeu; valor MENSAL (não acumulado); dinheiro na mesa já sem dupla contagem; lista ordenada por valor × chance (61–90 d 32,5% · 91–180 d 15,2% · 181–365 d 4,6%).
- **Contexto:** 📌 ponte + cards do escopo · 🔎 listas (top N) e `time(x)`.

### 3.12 Performance (`/performance`)
- **Responde:** nota 0–10 por vendedor no mês fechado, por universo (campo/lojas/telemarketing).
- **Dado:** `/api/performance` → `pesos` (+ `competencia_pesos`), `total_universo{campo 41, lojas 12, telemarketing 3}`, linhas com `valores{cobertura (índice), rentabilidade/receita/mix (% da meta), frequencia}`, `notas{}` (0–10 por indicador), `nota`, `parcial`, `peso_medido`, `faltando[]`, `posicao`, `detalhe{base_ativa, cobertos, esperado, realizado, meta}`.
- **Perguntas:** V "por que minha nota é X e o que faço para subir?" (indicador de pior nota × peso) · D "por que o JULIANO tem 10 de cobertura?" · S "quem do meu time está no fim do ranking e em quê?".
- **Armadilhas:** cobertura é RELATIVA ao mix A/B/C da carteira (JULIANO: 60–67% absoluto, índice 1,39 → 10; escala satura em 1,34) — decisão relativo × absoluto pendente com o João; nota parcial sem meta (35% do peso); escalas de atingimento provisórias; universos não se comparam; em cada coluna, em cima = medida, embaixo = nota do indicador; a NOTA final é a ponderada.
- **Contexto:** 📌 (V) a própria linha com o detalhe · (S/D) top e fundo do ranking por universo · 🔎 `vendedor(x)`.

---

## 4. Consultas sob demanda (camada 2) — contrato proposto

Todas executam com a SESSÃO do usuário (mesmo RBAC do endpoint; fora do escopo → resposta
"fora do seu escopo", sem dado). Devolvem JSON enxuto já renderizável; o índice do que existe é
derivado do texto renderizado (lição do Compras).

| Consulta | Entrada | Monta a partir de | Devolve (enxuto) |
|---|---|---|---|
| `cliente` | código ou nome (busca `/api/_internal/clientes-busca`) | `/api/carteira/cliente/<cc>` + `/produtos` + `/api/mix/abandonado/<cc>/deptos` + `/api/radar/cliente/<cc>` (top 10) + estado na Recuperação | segmento, classe ABC, ciclo, dias sem comprar, em risco?/chance de voltar, venda 12m, histórico 6 meses, top produtos, deptos que parou, produtos que parou |
| `vendedor` | código ou nome | `/api/vendedor/<cod>` + linha da `/api/performance` + `/api/metas/vendedores` + linha da Recuperação | cobertura da base, fora da base por tipo, alcance, mix/deptos/marcas, nota e cada indicador (medida + nota), metas do mês, em risco/recuperado |
| `time` | código ou nome | linha do time em `/api/recuperacao`, `/api/gerencial/cobertura`, `/api/metas` | ponte, recuperação, cobertura ≤ 30 d, metas |
| `produto` | código ou descrição (`/api/radar/produtos/busca`) | `/api/abc/produto/<cod>` + `/api/radar/produto/<cod>` (kpis) | classe, venda/margem 12m, série, quem vende, clientes que pararam / trocaram |
| `departamento` | código ou nome | linha de `/api/categorias` + `/api/categorias/<cod>/clientes` (top 10) + agregado do Mix abandonado | venda, margem, share, top clientes, clientes que pararam |

Teto: 3 consultas por pergunta; cada uma com timeout; log (quem, qual, duração) no `multpel_log`.

---

## 5. Sugestões de pergunta por tela e perfil (camada 3)

| Tela | Vendedor | Supervisor | Diretor |
|---|---|---|---|
| Dashboard | Como está minha venda no mês? | Quanto meu time pesa no mês? | Vamos bater o mês? Por que caímos contra set/25? |
| Carteira | Quem eu ligo hoje? | Qual segmento do meu time piorou? | Nossa positivação de clientes A está caindo? |
| Vendedores | Como estou contra o meu time? | Quem cobre menos a base? Quem atende cliente de colega? | Quem tem cadastro a transferir? |
| Categorias | Quem compra limpeza na minha carteira? | Que depto meu time vende pouco? | Que depto puxa a margem para baixo? |
| Curva ABC | O que eu mais vendo? | Quais os produtos A do meu time? | O nº 1 tem margem de 6% — é problema? |
| Mix | O que oferecer ao cliente X? | Que depto meu time está perdendo? | — |
| Radar | O cliente X parou de comprar o quê? | Por que o produto Y caiu no meu time? | Que produto estamos perdendo? |
| Tendências | — | — | Os clientes novos estão ficando? |
| Metas | Quanto falta e quanto por dia? | Quem não vai bater? | Vamos bater? Onde falta? |
| Gerencial | — | Quem está abaixo do limiar? | Por que 82% por valor e 35% por cliente? |
| Recuperação | Meus clientes em risco e a chance de voltarem | Quanto recuperamos da própria base? | A carteira em risco está crescendo? |
| Performance | Por que minha nota é X? O que sobe mais rápido? | Quem está no fim e em quê? | Por que o JULIANO tem 10? |

---

## 6. Bateria de perguntas reais (seed do teste de qualidade)

Toda pergunta real que aparecer entra aqui com a resposta esperada. Começa com:
1. **João, 25/09:** "Por que a cobertura do JULIANO (#29) dá nota 10 se ela é baixa?" → esperado:
   explicar as 3 réguas (60% Gerencial · 67% Vendedores · índice 1,39 Performance), o mix
   21 A/10 B/14 C com 19/21 A atendidos, esperado 21,6 × atendido 30, e a escala que satura
   em 1,34. NÃO pode dizer "a cobertura dele é alta" nem "é baixa" sem dizer qual régua.
2. "Quantos clientes novos tivemos no mês?" → enquanto o §1 item 1 não for corrigido, a resposta
   certa é não usar o card; usar a coorte do mês (`/api/tendencias/cohort`, tamanho do mês).
3. "Quanto dinheiro estamos deixando na mesa?" → R$ 1,76 mi/mês (ago) = R$ 1,21 mi em risco +
   R$ 0,55 mi de positivação abaixo do p75; NÃO somar com a receita perdida acumulada (R$ 2,65 mi).
4. "Quem eu ligo hoje?" (vendedor 879) → até 60 d de atraso do Próximo Pedido + os em risco dele
   por valor × chance; com código e telefone; sem duplicar cliente nas duas listas.
