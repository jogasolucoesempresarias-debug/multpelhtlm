'use strict';
/* Painel de Estoque JOGA v2 — foco no comprador. Baixa snapshot 1x e deriva tudo client-side.
   ATENÇÃO: este módulo é montado sob /estoque pelo server.py (Blueprint). Toda URL de API
   precisa do prefixo — use '/estoque/api/...', nunca '/api/...', que cairia no módulo
   Comercial e devolveria 404. */

// Eixo/grade dos gráficos leem do tema (antes '#94a3b8'/'#1e293b' cravados — ignoravam o tema
// e ficariam escuros no claro). Fallback mantém o valor de antes se a variável não resolver.
// Mesma estratégia do chart-tema.js do Comercial: além dos defaults (valem p/ gráficos
// futuros), REPINTA os já renderizados na troca de tema ao vivo. O SPA do estoque não carrega
// o chart-tema.js, então expomos window.aplicarTemaChart aqui — o joga-header.js chama essa
// função no toggle. Sem isto, quem abria no escuro e trocava p/ claro ficava com a grade
// escura (linhas de grade pesadas no branco).
window.aplicarTemaChart = function () {
  if (typeof Chart === 'undefined') return;
  const cor = (v, f) => getComputedStyle(document.documentElement).getPropertyValue(v).trim() || f;
  const txt = cor('--text-dim', '#94a3b8');
  const grade = cor('--border', '#1e293b');
  Chart.defaults.color = txt;
  Chart.defaults.borderColor = grade;
  Chart.defaults.font.family = 'DM Sans, sans-serif';
  const insts = Chart.instances || {};
  Object.keys(insts).forEach(function (id) {
    const c = insts[id];
    if (!c || !c.options) return;
    try {
      const scales = c.options.scales || {};
      Object.keys(scales).forEach(function (k) {
        const s = scales[k];
        if (!s) return;
        if (s.grid) s.grid.color = grade;
        if (s.ticks) s.ticks.color = txt;
      });
      c.update('none');
    } catch (e) { /* um chart problemático não pode travar os outros */ }
  });
};
window.aplicarTemaChart();

const C = { green:'#34d399', red:'#f87171', orange:'#fb923c', yellow:'#fbbf24',
            accent:'#38bdf8', accent2:'#818cf8', purple:'#c084fc', dim:'#64748b' };
const FAIXAS = [['0-30',0,30],['31-60',31,60],['61-90',61,90],['91-120',91,120],['121+',121,Infinity]];
const PREF = 'multpel_estoque_prefs';

const S = {
  meta:null, produtosAll:[], validade:null, planos:{}, orcamento:null, view:'cockpit',
  filiaisAll:[], filiaisSel:new Set(), base:'gerencial', vperiodo:'mes', cvDim:'comprador', abcLens:'venda',
  unidade:'atacado', unidadeNome:'Atacado', nomesFilial:{},
  compradorNome:'',
  cli:{comprador:'',curva:[],xyz:[],fornec:'',depto:'',busca:'',abast:[],margem:[],parado:'',ruptura:'',valDias:'',cobFaixa:[],parFaixa:[]},
  // idealDias/idealMeta = régua do "Estoque ideal" do Painel gerencial (só mede; a compra usa `cob`)
  params:{lead:10,seg:25,cob:45,hor:30,parado:60,forecast:0,sazonal:0,fcmeses:6,arredondacx:1,metaA:2,metaB:5,metaC:10,idealDias:45,idealMeta:90},
  charts:{}, sort:{}, valFaixa:null,
  orcArrastar:false,   // Orçamento: descontar o estouro do mês anterior da meta (opt-in)
  vencidos:null, vencidosQS:'', venMes:null, venPer:'2026',   // aba Vencidos: cache por QS, mês selecionado, período (2026|12m|tudo)
  // Lead time / Verbas: cache com CHAVE do comprador — as duas agregam no servidor e o recorte
  // por comprador é feito lá (mediana e totais não se recalculam a partir das linhas visíveis).
  leadtime:null, leadtimeKey:null, ltMin:5, ltOpen:new Set(), ltDet:{},
  verbas:null, verbasKey:null, vbOpen:new Set(), vbDet:{},
  // aba Abastecimento: fornecedores expandidos, "abrir tudo" e ordenação da lista.
  // A tela abre FECHADA (pedido do diretor 07/2026): a leitura de entrada é macro — quais
  // fornecedores preciso comprar e quanto — e os itens são o drill.
  repOpen:new Set(), repAll:false, repOrd:'valor',
  // linha secundária do gráfico 12m do produto: 'unidades' (default) | 'clientes' (positivação)
  d12:'unidades',
};

/* ───────── helpers ───────── */
const $ = s => document.querySelector(s);
const moneyF = new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'});
const money = v => v==null ? '—' : moneyF.format(v);
const moneyK = v => v==null ? '—' : (Math.abs(v)>=1000 ? 'R$ '+new Intl.NumberFormat('pt-BR',{maximumFractionDigits:1}).format(v/1000)+'k' : moneyF.format(v));
const int = v => v==null ? '—' : new Intl.NumberFormat('pt-BR',{maximumFractionDigits:0}).format(v);
const dec = (v,d=1) => v==null ? '—' : new Intl.NumberFormat('pt-BR',{maximumFractionDigits:d}).format(v);
const pct = v => v==null ? '—' : new Intl.NumberFormat('pt-BR',{style:'percent',maximumFractionDigits:1}).format(v);
const cob = v => v==null ? '∞' : dec(v,0)+'d';
// sugestão em caixas fechadas quando há QTUNITCX>1: "4 cx · 48 un"
const sugCx = (un, qtcx) => un==null ? '—' : ((qtcx>1 && un>0) ? `${int(Math.ceil(un/qtcx))} cx · ${int(un)} un` : int(un));
const dt = s => s ? s.split('-').reverse().join('/') : '—';
const esc = s => (s==null?'':String(s)).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const badge = (v,txt) => v==null||v===''?'':`<span class="badge b-${String(v).replace(/[^a-z0-9_-]/gi,'')}">${esc(txt!=null?txt:v)}</span>`;
// status executivo do abastecimento (metodologia v3) → rótulo + cor
const STAT_EXEC={aguardando_liberacao:['Recebido · aguard. liberação','#38bdf8'],ruptura_sem_pedido:['Ruptura s/ pedido','#ef4444'],ruptura_pedido_parcial:['Ruptura · pedido parcial','#f97316'],ruptura_pedido_cobre:['Ruptura · pedido cobre','#eab308'],compra_urgente:['Compra urgente','#ef4444'],compra_alta:['No prazo','#eab308'],compra_complementar:['Compra complementar','#38bdf8'],programar_compra:['Programar compra','#a78bfa'],pedido_cobre:['Pedido cobre','#22c55e'],estoque_ok:['Estoque OK','#22c55e']};
const statExec = v => { const s=STAT_EXEC[v]; return s?`<span class="badge" style="background:${s[1]}22;color:${s[1]}">${s[0]}</span>`:'—'; };
// sugestão em caixas a partir do campo já calculado no servidor (sugestao_cx) + unidades
const sugCxN = p => { if(!(p.sugestao_cx>0)) return '—';
  return (p.caixa>1) ? `${int(p.sugestao_cx)} cx · ${int(p.sugestao_cx*p.caixa)} un` : `${int(p.sugestao_cx)} un`; };
// embalagem do produto (caixa do PCEMBALAGEM) + fator un/cx — p/ validar a conversão unid→caixa
const embCell = p => { const e=esc(p.embalagem_caixa||''); const cx=p.caixa||1;
  return cx>1 ? `${e||'cx'} <small class="muted">· ${int(cx)} un/cx</small>` : `<span class="muted">${e||'avulso'} · 1 un</span>`; };
// navegação em 2 níveis: grupo → telas
const NAV={visao:['cockpit','gerencial','meta_ruptura'],comprar:['reposicao','estoque_zero','plano'],pedidos:['orcamento'],estoque:['ruptura','parado','validade','vencidos','ruptura_comprador','ocupacao'],analise:['desempenho','comprasvendas','fornecedores','leadtime','verbas','abcxyz','produtos','qualidade']};
// aba 'logistica' oculta a pedido do diretor (não usa p/ análise) — reversível: re-adicionar em pedidos
const GROUP_OF=v=>Object.keys(NAV).find(g=>NAV[g].includes(v))||'visao';
// filtro Curva (global, topo) = MULTI-seleção (ex.: ver ruptura de B+C juntas)
const CURVA_LABEL=arr=>(!arr||!arr.length||arr.length===3)?'Todas':arr.slice().sort().join(' · ');
const XYZ_LABEL=arr=>(!arr||!arr.length||arr.length===3)?'Todas':arr.slice().sort().join(' · ');
function syncCurvaUI(){ const d=$('#f-curva'); if(!d) return; const arr=S.cli.curva||[];
  const sum=d.querySelector('summary'); if(sum) sum.textContent=CURVA_LABEL(arr);
  d.querySelectorAll('input[type=checkbox]').forEach(c=>c.checked=arr.includes(c.value)); }
function syncXyzUI(){ const d=$('#f-xyz'); if(!d) return; const arr=S.cli.xyz||[];
  const sum=d.querySelector('summary'); if(sum) sum.textContent=XYZ_LABEL(arr);
  d.querySelectorAll('input[type=checkbox]').forEach(c=>c.checked=arr.includes(c.value)); }
// filtro Abast. multi-seleção — agora LOCAL da aba Produtos (não é mais global)
const ABAST_LABELS={urgente:'Urgente',alta:'Alta',atencao:'Atenção',excesso:'Excesso',ok:'OK',sem_giro:'Sem giro'};
const abastLabel=arr=>!arr.length?'Todos':(arr.length===1?(ABAST_LABELS[arr[0]]||arr[0]):`${arr.length} status`);
// filtro de margem (aba Produtos) — faixas multi-seleção; margem null (sem venda) vira bucket próprio
const MARGEM_LABELS={neg:'Negativa (<0%)',b0:'0–10%',b10:'10–20%',b20:'20–30%',b30:'30%+',sv:'Sem venda'};
const margemLabel=arr=>!arr.length?'Todas':(arr.length===1?(MARGEM_LABELS[arr[0]]||arr[0]):`${arr.length} faixas`);
const margemBucket=p=>{const m=p.margem; return m==null?'sv':(m<0?'neg':(m<10?'b0':(m<20?'b10':(m<30?'b20':'b30'))));};
// ───────── valor a comprar: FONTE ÚNICA de todas as telas ─────────
// Régua da NF (mercadoria + IPI/ST previstos) sobre a sugestão em CAIXA FECHADA — a mesma da aba
// Abastecimento. Antes o Cockpit e o Estoque zerado calculavam `sugestao_compra × custo_unit` na
// mão: divergiam do Abastecimento em DUAS dimensões (caixa fechada e imposto). Decisão do diretor
// 07/2026: todo lugar que mostra "quanto vou gastar" fala a régua do Orçamento (PCPEDIDO[VLTOTAL]).
const valReporNF=p=>(p.valor_sugerido_nf!=null?p.valor_sugerido_nf:(p.valor_sugerido_liq||0));
const valReporMerc=p=>(p.valor_sugerido_liq||0);
// quanto do valor está apoiado em alíquota ESTIMADA (item sem regra fiscal p/ aquela origem).
// Em R$, não em contagem de itens: 5% dos itens pode ser 0,5% ou 30% do dinheiro.
const valReporIncerto=p=>(p.trib_firme===false?valReporNF(p):0);
// ───────── venda perdida por ruptura: FONTE ÚNICA ─────────
// Item zerado COM giro (a ruptura oficial), somando `venda_perdida` (dias parados × giro/dia ×
// preço de venda, já calculado no servidor). Usado pelo card do Abastecimento e pelo do Estoque
// zerado — se cada tela filtrasse por conta própria, os dois cards diriam valores diferentes para
// a mesma coisa, que é como o app já se queimou antes com o "quanto vou gastar".
const emRuptura=p=>(p.qtdisp||0)<=0&&(p.giro_dia||0)>0;
const vendaPerdidaTotal=P=>P.filter(emRuptura).reduce((s,p)=>s+(p.venda_perdida||0),0);
// rodapé padrão da incerteza — some quando tudo é firme (só aparece quando importa)
function notaIncerteza(vNF,vInc){
  if(!(vInc>0)||!(vNF>0)) return '';
  const pct=vInc/vNF*100;
  return ` <small class="muted" title="Itens sem regra fiscal cadastrada para a UF deste fornecedor — a alíquota é estimativa. Confira o IPI ao gerar o pedido.">· ${money(vInc)} c/ imposto estimado (${dec(pct,1)}%)</small>`;
}
// valor em "N cx · M un" (só unidades quando não há caixa ou ≤0) — colunas de estoque em caixa
const cxUn=(v,caixa)=>{ if(v==null) return '—'; const c=caixa||1; return (c>1&&v>0)?`${int(Math.round(v/c))} cx · ${int(v)} un`:int(v); };
function spark(serie){ // mini sparkline SVG de 3 meses
  if(!serie||!serie.length) return '';
  const mx=Math.max(...serie,1), w=46,h=16, st=w/(serie.length-1||1);
  const pts=serie.map((v,i)=>`${(i*st).toFixed(1)},${(h-(v/mx)*(h-2)-1).toFixed(1)}`).join(' ');
  const up=serie[serie.length-1]>=serie[0];
  return `<svg width="${w}" height="${h}" class="spark"><polyline points="${pts}" fill="none" stroke="${up?C.green:C.red}" stroke-width="1.5"/></svg>`;
}
function toast(msg,err){ const t=document.createElement('div'); t.className='toast'+(err?'':' ok'); t.textContent=msg; document.body.appendChild(t); setTimeout(()=>t.remove(),3500); }
async function getJSON(u){ const r=await fetch(u); if(!r.ok) throw new Error('HTTP '+r.status); return r.json(); }
async function postJSON(u,body,method){ const r=await fetch(u,{method:method||'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})}); if(!r.ok) throw new Error('HTTP '+r.status); return r.json(); }

/* ───────── tooltips de ajuda (hover nos títulos e cabeçalhos calculados) ─────────
   Textos auditados contra core.py/queries.py (catálogo validado). COMMON = colunas que se
   repetem; cada view pode sobrescrever um rótulo. `_title` = tooltip do título da aba (head()).
   Colunas óbvias (Cód, Produto, Fornecedor, Data, Nº…) ficam de fora de propósito. */
const TIPS = {
  COMMON: {
    'ABC':'Curva de venda (Pareto): A ≈ 80% do valor de venda, B ≈ 15%, C o restante.',
    'XYZ':'Variabilidade da demanda: X estável, Y variável, Z errático (pelo desvio da venda dos 3 meses).',
    'Disp.':'Estoque disponível (gerencial): QTESTGER − avaria − reserva, nas filiais de estoque. É o que está livre para vender.',
    'Disp. cx':'Disponível convertido em caixas (disp. ÷ fator un/cx da caixa).',
    'Já ped.':'Pedido de compra REAL em aberto no Winthor (quantidade pedida − entregue, últimos 180 dias).',
    'Giro/mês':'Venda média por mês — média dos 3 últimos meses fechados (ou forecast / item novo, quando aplicável).',
    'Giro cx':'Giro mensal em caixas (giro ÷ fator un/cx da caixa).',
    'Cob.':'Cobertura em dias = disponível ÷ giro diário. Giro 0 = não calculável (∞). Na aba Cobertura o cálculo usa ARREDONDA.CIMA (regra oficial da planilha).',
    'Valor estoque':'Valor do estoque disponível a custo (disponível × custo do produto).',
    'Estoque R$':'Valor do estoque disponível a custo (disponível × custo do produto).',
    'Sugerido':'Compra sugerida em caixas fechadas (e o equivalente em unidades) para repor até o alvo.',
    'Sugerido (cx)':'Compra sugerida em caixas fechadas (e o equivalente em unidades) para repor até o alvo.',
    'Venda':'Venda líquida no período (venda − devoluções).',
    'Lucro':'Venda líquida − custo.',
    'Margem':'Lucro ÷ venda líquida.',
  },
  gerencial:{ 'Status':'Nível de atenção da faixa (urgente, alto, atenção, baixo, OK…).' },
  meta_ruptura:{
    _title:'Placar da meta de ruptura: % de itens zerados que ainda NÃO têm pedido de compra em aberto, separado por curva de venda. Curva ABC apurada sobre os últimos 90 dias; o placar não responde aos filtros do topo (meta que muda com o filtro não é meta).',
    // rótulos são dinâmicos ("Curva A (meta 2,0%)"), então a busca usa a chave estável de `tipk`
    'Curva A':'Itens da curva A (≈80% do faturamento) zerados e ainda sem pedido de compra em aberto ÷ TOTAL de produtos do comprador na curva A. Entre parênteses, o absoluto — base pequena faz 1 item virar um % alto.',
    'Curva B':'Mesma conta na curva B (≈15% do faturamento). Separada da C a pedido do diretor 07/2026: B e C num bloco só escondiam comportamentos diferentes.',
    'Curva C':'Mesma conta na curva C — a cauda longa (item sem curva entra aqui). O limite é o mais frouxo das três porque é onde a ruptura custa menos.',
    'Status':'Acima da meta se QUALQUER uma das três curvas estourar o seu limite. Limites editáveis em ⚙ Parâmetros.',
  },
  ruptura:{
    _title:'Distribuição do estoque por dias de cobertura. Cobertura = ARREDONDA.CIMA(estoque ÷ giro diário); giro 0 = não calculável (cai em 121+). Métrica oficial da planilha.',
    'Faixa':'Faixa de cobertura em que o item cai (0-30, 31-60, 61-90, 91-120, 121+).',
    'Tipo':'No 121+, distingue “sem giro” (estoque morto, liquidar) de “excesso real” (cobertura alta, reduzir compra).',
  },
  estoque_zero:{
    _title:'Todos os produtos com estoque gerencial ≤ 0, com e sem pedido de compra em aberto.',
    'Estoque':'Estoque disponível (gerencial) em unidades — aqui, sempre ≤ 0.',
    'Dias s/ venda':'Dias desde a última venda (“nunca” = sem saída registrada).',
    'Status':'Situação executiva: ruptura sem pedido, ruptura com pedido parcial/coberto, etc.',
  },
  reposicao:{
    _title:'Sugestão de compra agrupada por fornecedor, com a lista fechada: cada linha é um fornecedor a comprar e o valor do pedido sugerido; clique no cabeçalho para ver os itens. Sugestão = estoque-alvo − (disponível + pedido já em aberto), arredondada em caixas.',
    'Embalagem':'Embalagem da caixa e o fator de conversão unidade↔caixa (un/cx).',
    'Cob.proj':'Cobertura projetada em dias = (disponível + já pedido) ÷ giro diário.',
    'm³':'Cubagem do pedido sugerido (caixas sugeridas × volume da caixa).',
    'Valor sug.':'Valor da compra sugerida a custo de MERCADORIA (caixas sugeridas × custo) — é este preço que vai na planilha de importação do Winthor, sem imposto (o ERP calcula o dele).',
    'Imp.':'IPI + ICMS-ST previstos para a linha, na alíquota que ESTE fornecedor pratica (tirada dos pedidos reais dele; sem histórico, cai no cadastro do produto). O total do fornecedor já sai com estes impostos — é a régua da NF, a mesma que o Orçamento mede.',
    'Status':'Situação executiva do item (compra urgente, no prazo, ruptura, pedido cobre…).',
    'Sugeria':'Quanto a regra sugeriria comprar — mostrado só para conferência (o item parou de vender).',
    'Dias s/ venda':'Dias desde a última venda do produto.',
  },
  plano:{
    'Qtd pedir':'Quantidade a liberar naquela semana (em caixas quando há fator de caixa).',
    'Valor':'Valor da liberação a custo.',
  },
  orcamento:{
    'Meta':'Meta do comprador = 65% da sua venda líquida dos últimos 30 dias.',
    'Comprado':'Total comprado no mês (pedidos reais do Winthor).',
    'Aberto':'Valor comprometido ainda não entregue.',
    'Saldo':'Meta − comprado.',
    'Consumido':'% da meta já consumida (comprado ÷ meta).',
    'A entregar':'Valor do pedido ainda não recebido.',
    'Previsão entrega':'Data prevista de entrega (previsão do Winthor, ou emissão + prazo do fornecedor).',
    'Status':'Situação do prazo: no prazo, chega em ≤7 dias, atrasado ou já recebido.',
    'Valor':'Valor total do pedido.',
  },
  parado:{
    _title:'Itens com estoque e 15 dias ou mais sem venda, por faixa de dias parados. As faixas somam o total.',
    'Última venda':'Data da última saída do produto.',
    'Dias parado':'Dias desde a última venda (“nunca” = sem saída registrada).',
    'Valor':'Valor do estoque parado a custo.',
    'Saída':'Recência da última saída: recente (≤30d), média (≤90d) ou antiga (>90d).',
    'Faixa':'Faixa de dias sem venda em que o item cai.',
    'Ação':'Plano de ação cadastrado para o item (clique para criar ou editar).',
  },
  validade:{
    _title:'Lotes que vencem no horizonte, com saldo projetado e risco (FEFO: sai primeiro o que vence antes).',
    'Lote':'Número do lote (ou “N lotes” quando vários do mesmo produto/validade foram somados).',
    'Dias':'Dias até a data de validade do lote.',
    'Saldo proj.':'Saldo projetado no vencimento = quantidade − consumo estimado (giro × dias até vencer).',
    'Valor risco':'Valor do saldo que deve sobrar no vencimento, a custo — o risco de perda.',
    'Classe':'Urgência pela proximidade do vencimento: crítico (≤7d), atenção (≤15d) ou planejar.',
    'Ação':'Plano de ação cadastrado para o lote.',
    'Estoque':'Valor do estoque desses lotes a custo (quantidade × custo).',
    'Risco':'Valor em risco de vencer (saldo projetado × custo).',
  },
  vencidos:{
    _title:'Perda REALIZADA por validade (conta 200042 do Winthor), mês a mês. Contraponto da aba Validade (lá é risco futuro; aqui é perda que já aconteceu).',
    'Vezes':'Quantas vezes o produto já venceu (reincidência ao longo de todo o histórico).',
    'Qt perdida':'Quantidade total já baixada por validade.',
    'Já perdido':'Valor total já perdido por validade (ao preço da baixa).',
    'Em estoque':'Saldo atual do produto — o que ainda pode vencer de novo.',
    'Próx. venc.':'Data em que o estoque atual endereçado vence (menor validade futura).',
    'Última perda':'Data da última baixa por validade do produto.',
    'P. unit.':'Preço unitário registrado na baixa da nota.',
    'Total':'Valor total da linha (quantidade × preço unitário).',
    'Part.':'Participação da perda desse comprador/fornecedor no total do período.',
    '% venda':'Perda por validade ÷ venda líquida do comprador (all-time; aparece só em “Tudo”).',
  },
  ruptura_comprador:{
    _title:'Ruptura (estoque ≤ 0 e giro > 0) agregada por comprador: itens sem pedido, venda perdida e sugestão de compra.',
    'Em ruptura':'Nº de itens do comprador em ruptura (estoque ≤ 0 e giro > 0).',
    '% Rupt.':'Itens em ruptura ÷ total de produtos do comprador.',
    'Dias rupt. méd':'Média de dias sem venda dos itens em ruptura (há quanto tempo, em média, estão zerados).',
    'Sem pedido':'Itens em ruptura ainda sem pedido de compra em aberto (risco real).',
    '% s/ ped.':'Itens sem pedido ÷ total de produtos do comprador (base da meta — todo item conta, não só os em ruptura).',
    'Venda perdida':'Dias em ruptura (desde a última venda, teto 60) × giro/dia × preço de venda (realizado 3m).',
    'Sugestão de compra':'MESMO valor da aba Comprar → Abastecimento: soma da compra sugerida (caixa fechada × custo) de TODOS os itens a comprar do comprador — não só os zerados. Considera Lead time + Cobertura alvo dos ⚙ Parâmetros.',
  },
  ocupacao:{
    _title:'Ocupação das posições do depósito segundo o WMS (bate com a consulta 1772 do Winthor).',
    'Posições':'Nº de posições (slots) COM estoque ocupadas pelo item.',
    '% ocup.':'Participação do item nas posições com estoque do depósito.',
    'm³ end.':'Volume endereçado do item (quantidade endereçada × volume unitário).',
    'Qtd (sist.)':'Quantidade que o sistema (WMS) mostra na posição — para comparar com a prateleira.',
    'Situação':'Situação da posição: com estoque ou vazia (reservada).',
  },
  desempenho:{
    'Fornec.':'Nº de fornecedores atendidos pelo comprador.',
    'Positivação':'Nº de clientes distintos que compraram (positivaram) no período.',
    'Venda líq.':'Venda líquida = venda bruta − devoluções.',
    'Lucro bruto':'Venda líquida − custo (com o custo da mercadoria devolvida estornado).',
    'Devolução':'Valor devolvido no período.',
    '% Lucro':'Fatia do lucro total (participação do comprador).',
    'AA Venda':'Variação da venda vs. o mesmo período do ano anterior.',
    'AA Lucro':'Variação do lucro vs. o mesmo período do ano anterior.',
  },
  comprasvendas:{
    'Estoque R$':'Valor do estoque a custo (capital em compras).',
    'Venda R$':'Venda líquida no período (venda − devoluções).',
    'Lucro R$':'Venda líquida − custo.',
    'Venda/Estoque':'Quantas vezes o capital girou no período (venda ÷ estoque).',
    'Ruptura':'Nº de itens em ruptura (estoque ≤ 0 e giro > 0).',
    '% Rupt.':'Itens em ruptura ÷ total de itens do grupo.',
    'Parado R$':'Valor de estoque parado a custo.',
  },
  leadtime:{
    _title:'Quanto tempo cada fornecedor demora entre o pedido ser emitido e a NF entrar no estoque (últimos 12 meses).',
    'Pedidos':'Nº de pedidos do fornecedor já recebidos nos últimos 12 meses (transferência entre filiais fica fora).',
    '% na hora':'Pedidos digitados junto com a entrega (lead 0–1 dia): o pedido real nasceu fora do ERP e foi lançado na hora da NF. Quanto menor, melhor o processo.',
    'Lead todos':'MÉDIA de TODOS os pedidos recebidos, incluindo os digitados na hora — a visão "como está no sistema". Quanto mais perto do Lead real, mais limpo o processo.',
    'Lead real':'Mediana só dos pedidos emitidos ANTES da entrega (lead ≥ 2 dias) — o tempo de resposta real do fornecedor. Precisa de ≥ 5 pedidos para ser confiável.',
    'Prazo manual':'PRAZOENTREGA cadastrado hoje no fornecedor (preenchido à mão) — é o que a sugestão de compra usa.',
    'Δ':'Prazo manual − lead real. Positivo = cadastro inflado (estoque de segurança além do necessário, capital parado). Negativo = prazo otimista (risco de ruptura).',
    'Situação':'Cadastro OK, inflado, otimista, ou "sem lead confiável" (quase tudo digitado na hora — o prazo manual segue valendo).',
  },
  verbas:{
    _title:'Verbas/bonificações negociadas com fornecedores (rotina 1801 do Winthor): quanto foi negociado, quanto já virou abatimento e quanto está parado sem aplicar.',
    'Verbas 12m':'Nº de verbas emitidas nos últimos 12 meses (canceladas fora).',
    'Negociado':'Valor das verbas emitidas nos últimos 12 meses.',
    'Aplicado':'Quanto dessas verbas já virou abatimento de fato (estornos fora).',
    'Saldo aberto':'Posição ATUAL: valor negociado que ainda não foi aplicado — qualquer data de emissão (saldo antigo não some daqui).',
    'Idade':'Há quantos dias o saldo mais antigo do fornecedor está parado sem aplicar.',
    'Compra 12m':'Volume comprado do fornecedor nos últimos 12 meses (transferência entre filiais fora).',
    '% V/C':'Verba negociada ÷ compra no mesmo período — a "taxa de devolução" do fornecedor. Compare fornecedores parecidos: é o argumento de negociação.',
    'Lead':'Lead time real do fornecedor (mediana ≥2d, da aba Lead time) — fecha o tripé: quanto compro · quanto demora · quanto devolve.',
    'Situação':'Aplicada (sem saldo), saldo em aberto, ou saldo PARADO (aberto há mais de 120 dias).',
  },
  fornecedores:{
    _title:'Compara quanto o fornecedor vende com quanto pesa em estoque.',
    'ABC':'Curva do fornecedor por venda (Pareto do faturamento).',
    'Estoque':'Valor do estoque do fornecedor a custo.',
    'Giro/mês':'Giro mensal somado dos itens do fornecedor (unidades).',
    'Cob.':'Cobertura média do fornecedor em dias (disponível ÷ giro diário).',
    'Venda':'Venda líquida do fornecedor no período.',
    '% est.':'Participação do fornecedor no valor total de estoque.',
    '% venda':'Participação do fornecedor na venda total.',
    'Índice':'% na venda ÷ % no estoque (> 1 = vende mais do que pesa em estoque).',
    'Classe':'Classificação: alta performance, equilibrado, estoque alto, ruptura ou crítico sem giro.',
    'Compras':'Quantas vezes compramos deste fornecedor no período do seletor "Venda" (pedidos de compra distintos no Winthor). Transferência entre filiais não conta como compra.',
    'Ciclo 12m':'De quanto em quanto tempo compramos deste fornecedor — média dos intervalos entre datas de compra, sempre nos ÚLTIMOS 12 MESES (não segue o filtro do topo: ciclo é comportamento do fornecedor, e numa janela curta quase todo fornecedor teria 1 pedido só). Compare com o Lead time: ciclo menor que o lead significa pedido novo antes do anterior chegar. "—" = menos de 2 compras em 12m.',
    'Lucro bruto':'Venda líquida − custo dos produtos deste fornecedor no período. É o mesmo lucro que alimenta a coluna Margem (não é recalculado a partir dela).',
    'Verba':'Verba NEGOCIADA com o fornecedor no mesmo período (PCVERBA, canceladas e estornos fora). Inclui todas as contas — inclusive "Premiações e campanhas", que não é redução de custo; refinar isso ficou para depois, por decisão do diretor.',
    'Lucro c/ verba':'Lucro bruto + verba negociada no período. É o que o fornecedor realmente deixou. Sem verba no período, é igual ao lucro bruto.',
    'Margem c/ verba':'(Lucro bruto + verba) ÷ venda líquida. A diferença para a Margem normal é o quanto a negociação de verba melhora o fornecedor.',
  },
  produtos:{
    _title:'Tabela completa de produtos com todos os indicadores. Filtre por abastecimento e por margem.',
    'Avaria':'Quantidade bloqueada/avariada (QTBLOQUEADA) — não entra no disponível para venda.',
    'Dias s/v':'Dias desde a última venda.',
    'Abast.':'Status de abastecimento: urgente, alta, atenção, OK, excesso ou sem giro.',
  },
  qualidade:{
    'Estoque':'Estoque disponível (gerencial) em unidades.',
    'Custo':'Custo unitário do produto (CUSTOFIN).',
    'Problemas':'Inconsistências detectadas: sem custo, sem fornecedor, sem comprador, sem giro com estoque ou estoque negativo.',
  },
};
// span do ícone ⓘ com o texto no data-tip (a caixinha singleton lê daí no hover)
function tipSpan(txt){ return txt ? `<span class="ttip" data-tip="${esc(txt)}" tabindex="0" aria-label="ajuda" role="img">i</span>` : ''; }
// tooltip de coluna/título pelo registro (view + rótulo). Sobrescrita da view vence a COMMON.
function tip(view, label){ const v=(TIPS[view]&&TIPS[view][label]); const t=(v!=null?v:TIPS.COMMON[label]); return tipSpan(t); }
// tooltip com texto literal (títulos escritos à mão)
function tipT(txt){ return tipSpan(txt); }

/* ───────── prefs ───────── */
// `repAll`/`repOrd` são preferência de EXIBIÇÃO da aba Abastecimento (abrir tudo, ordem da lista).
// Ficam no localStorage sem cerimônia porque não mudam nenhum número — ao contrário dos ⚙ Parâmetros,
// onde "por navegador" significa que o painel pode dizer coisas diferentes para cada pessoa.
function savePrefs(){ try{ localStorage.setItem(PREF, JSON.stringify({comprador:S.cli.comprador,base:S.base,vperiodo:S.vperiodo,unidade:S.unidade,params:S.params,view:S.view,repAll:S.repAll,repOrd:S.repOrd,orcArrastar:S.orcArrastar})); }catch(e){} }
function loadPrefs(){ try{ return JSON.parse(localStorage.getItem(PREF))||{}; }catch(e){ return {}; } }

/* ───────── querystring p/ servidor ───────── */
function serverQS(){
  const p=new URLSearchParams();
  p.set('unidade', S.unidade);
  p.set('base_estoque', S.base);
  p.set('venda_periodo', S.vperiodo);
  p.set('lead_time', S.params.lead); p.set('dias_seguranca', S.params.seg);
  p.set('cobertura_total', S.params.cob); p.set('horizonte_val', S.params.hor);
  p.set('parado_atencao', S.params.parado);
  p.set('forecast', S.params.forecast?1:0); p.set('forecast_meses', S.params.fcmeses);
  p.set('forecast_sazonal', S.params.sazonal?1:0); p.set('arredonda_cx', S.params.arredondacx?1:0);
  // régua do Estoque ideal (Painel gerencial) — vai no serverQS porque o filtrosQS() do /api/resumos
  // é construído em cima dele; a meta viaja em % (0-100) e o servidor converte p/ fração.
  p.set('ideal_dias', S.params.idealDias); p.set('ideal_meta_pct', S.params.idealMeta);
  return p.toString();
}

/* ───────── carga ───────── */
async function loadData(){
  $('#loader').style.display='block'; $('#content').style.display='none';
  try{
    const qs=serverQS();
    const [snap,val,planos]=await Promise.all([
      getJSON('/estoque/api/snapshot?'+qs), getJSON('/estoque/api/validade?'+qs), getJSON('/estoque/api/planos').catch(()=>({planos:{}}))]);
    S.produtosAll=snap.produtos; S.meta=snap; S.validade=val; S.planos=planos.planos||{};
    if(snap.unidade_nome) S.unidadeNome=snap.unidade_nome;
    const br=snap.bi_refresh;
    $('#meta-gerado').textContent = (br&&br.end_fmt)
      ? ('BI atualizado '+br.end_fmt+(br.in_progress?' · atualizando…':''))
      : ('Atualizado em '+snap.gerado_em);
    const fnome=f=>S.nomesFilial[f]||f, fils=Array.isArray(snap.filiais)?snap.filiais.map(fnome).join(' + '):snap.filiais;
    $('#meta-filiais').textContent=(snap.unidade_nome||'')+' · '+fils+' · '+snap.n+' itens · gerencial';
  }catch(e){ toast('Falha ao carregar: '+e.message,true); console.error(e); }
  $('#loader').style.display='none'; $('#content').style.display='block';
  render();
}

/* ───────── filtros client-side ───────── */
function filtered(skipCurva){
  const f=S.cli, b=f.busca.trim().toLowerCase();
  return S.produtosAll.filter(p=>{
    if(f.comprador && String(p.codcomprador)!==f.comprador) return false;
    if(!skipCurva && f.curva.length && !f.curva.includes(p.curva_abc)) return false;
    if(f.xyz.length && !f.xyz.includes(p.xyz)) return false;
    if(f.fornec && String(p.codfornec)!==f.fornec) return false;
    if(f.depto && String(p.codepto)!==f.depto) return false;
    if(f.parado && p.status_parado!==f.parado) return false;
    if(f.ruptura && !p.status_ruptura) return false;
    if(b && !(String(p.codprod).includes(b)||(p.descricao||'').toLowerCase().includes(b))) return false;
    return true;
  });
}
function lotesFiltrados(){
  // Recorta os lotes pelos PRODUTOS VISÍVEIS (filtered()) — assim vale TODO filtro do topo
  // (comprador, fornecedor, curva, XYZ, depto, busca) com uma regra só.
  // ⚠️ Tem de casar com o export, que usa `_aplicar_filtros_cliente` (todos os filtros): quando
  // esta função aplicava só comprador/fornecedor/busca, filtrar por Curva fazia a tela mostrar
  // um conjunto e o Excel outro. Mesmo defeito já reportado 2x pelo diretor (07/2026).
  const cods=new Set(filtered().map(p=>p.codprod));
  return (S.validade?.lotes||[]).filter(l=>cods.has(l.codprod));
}

/* ───────── agregação cockpit ───────── */
function agg(P){
  const sum=(a,fn)=>a.reduce((s,p)=>s+(fn(p)||0),0);
  const valor_total=sum(P,p=>p.valor);
  const comGiro=P.filter(p=>(p.giro_dia||0)>0);
  const semGiro=P.filter(p=>(p.giro_dia||0)<=0&&(p.qtdisp||0)>0);
  const parados=P.filter(p=>p.status_parado);
  const repor=P.filter(p=>(p.sugestao_compra||0)>0&&(p.giro_dia||0)>0&&!p.compra_suspensa);
  const rupt=P.filter(p=>p.status_ruptura);
  const zerados=P.filter(p=>p.estoque_zero&&(p.giro_dia||0)>0);   // ruptura real (estoque ≤ 0 e giro > 0)
  const faixas=FAIXAS.map(([n,lo,hi])=>{const it=comGiro.filter(p=>p.cobertura!=null&&Math.ceil(p.cobertura)>=lo&&Math.ceil(p.cobertura)<=hi);return{faixa:n,qt:it.length,valor:sum(it,p=>p.valor)};});
  faixas.push({faixa:'sem giro',qt:semGiro.length,valor:sum(semGiro,p=>p.valor)});
  const abc={}; ['A','B','C'].forEach(c=>{const it=P.filter(p=>p.curva_abc===c);abc[c]={qt:it.length,valor:sum(it,p=>p.valor),venda:sum(it,p=>p.venda)};});
  const matriz={}; P.forEach(p=>{if(p.abc_xyz){(matriz[p.abc_xyz]=matriz[p.abc_xyz]||{qt:0,valor:0,venda:0});matriz[p.abc_xyz].qt++;matriz[p.abc_xyz].valor+=(p.valor||0);matriz[p.abc_xyz].venda+=(p.venda||0);}});
  const cnt=(fld,v)=>{const it=P.filter(p=>p[fld]===v);return{qt:it.length,valor:sum(it,p=>p.valor)};};
  const venda_total=sum(P,p=>p.venda), lucro_total=sum(P,p=>p.lucro);
  return {valor_total,venda_total,lucro_total,margem_total: venda_total?lucro_total/venda_total*100:null,
    n:P.length,com_estoque:P.filter(p=>(p.qtdisp||0)>0).length,com_giro:comGiro.length,sem_giro:semGiro.length,
    valor_parado:sum(parados,p=>p.valor),valor_sem_giro:sum(semGiro,p=>p.valor),faixas,abc,matriz,
    parado:{atencao:cnt('status_parado','atencao'),critico:cnt('status_parado','critico'),muito_critico:cnt('status_parado','muito_critico')},
    ruptura:{total:rupt.length,valor:sum(rupt,p=>p.valor),f0_15:rupt.filter(p=>p.status_ruptura==='0-15').length,
      zerados:zerados.length,valor_zerados:sum(zerados,p=>p.valor)},
    repor:{n:repor.length,valor:sum(repor,valReporNF),merc:sum(repor,valReporMerc),incerto:sum(repor,valReporIncerto),qt:sum(repor,p=>p.sugestao_compra)}};
}

/* ───────── charts / tabela ───────── */
function chart(id,cfg){ if(S.charts[id]) S.charts[id].destroy(); const c=document.getElementById(id); if(c) S.charts[id]=new Chart(c,cfg); }
function renderTable(P,cols,view,onClickRow){
  const sk=S.sort[view]||{key:cols[0].key,dir:-1};
  const rows=[...P].sort((a,b)=>{let x=a[sk.key],y=b[sk.key]; if(x==null)x=-Infinity; if(y==null)y=-Infinity;
    if(typeof x==='string'||typeof y==='string')return sk.dir*String(x).localeCompare(String(y)); return sk.dir*(x-y);});
  const head=cols.map(c=>`<th class="${c.num?'num':''}" data-k="${c.key}">${c.label}${tip(view,c.label)}${sk.key===c.key?(sk.dir<0?' ↓':' ↑'):''}</th>`).join('');
  const body=rows.slice(0,400).map(p=>`<tr data-cod="${p.codprod}">`+cols.map(c=>{
    let v=p[c.key]; if(c.badge)return`<td>${badge(v,c.map?c.map(v,p):v)}</td>`;
    if(c.html)return`<td class="${c.num?'num':''}">${c.html(p)}</td>`;
    if(c.fmt)v=c.fmt(v,p); return`<td class="${c.num?'num':''}">${v==null?'—':v}</td>`;}).join('')+'</tr>').join('');
  const note=`<div class="count-line">${int(rows.length)} itens${rows.length>400?' (mostrando 400)':''}</div>`;
  setTimeout(()=>{ const cont=$('#v-'+view);
    cont.querySelectorAll('thead th').forEach(th=>th.onclick=()=>{const k=th.dataset.k,cur=S.sort[view]||{};S.sort[view]={key:k,dir:cur.key===k?-cur.dir:-1};render();});
    cont.querySelectorAll('tbody tr[data-cod]').forEach(tr=>tr.onclick=e=>{ if(e.target.closest('.rowact'))return; (onClickRow||openProduto)(tr.dataset.cod);});
  },0);
  return note+`<div class="tbl-wrap${view==='produtos'?' freeze2':''}"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}
const colCod={key:'codprod',label:'Cód',num:true};
const colProd={key:'descricao',label:'Produto',fmt:v=>`<span class="prod" title="${esc(v)}">${esc(v)}</span>`};
const colForn={key:'fornecedor',label:'Fornecedor',fmt:v=>`<span class="prod" title="${esc(v)}">${esc(v||'—')}</span>`};
const colGiroSpark={key:'giro_mes',label:'Giro/mês',num:true,html:p=>`${int(p.giro_mes)} ${spark(p.serie_giro)}`};
// crescimento vs. mesmo período do ano anterior. null = sem base no ano passado (item novo ou
// período anterior a 2024, início do RCA) → "—", nunca −100%.
const crescCell=v=>v==null?'<span class="muted" title="sem venda no mesmo período do ano anterior">—</span>'
  :`<span style="color:${v>=0?C.green:C.red}">${v>=0?'+':''}${dec(v,1)}%</span>`;
// usa fmt (não html) p/ funcionar também na tabela de Fornecedores, que só suporta fmt/badge
const colCresc={key:'crescimento',label:'Cresc. AA',num:true,fmt:v=>crescCell(v)};

// ── ordenação clicável p/ tabelas montadas na mão (headers com data-k) ──
function _sortArr(rows,sk){ return [...rows].sort((a,b)=>{let x=a[sk.key],y=b[sk.key];if(x==null)x=-Infinity;if(y==null)y=-Infinity;
  if(typeof x==='string'||typeof y==='string')return sk.dir*String(x).localeCompare(String(y)); return sk.dir*(x-y);}); }
function sortTh(cols,sk,view){ view=view||S.view; return cols.map(c=>`<th class="${c.num?'num':''}" data-k="${c.k}">${c.label}${tip(view,c.label)}${sk.key===c.k?(sk.dir<0?' ↓':' ↑'):''}</th>`).join(''); }
function wireSortTbl(container,skKey,onChange){ if(!container)return; container.querySelectorAll('thead th[data-k]').forEach(th=>th.onclick=()=>{const k=th.dataset.k,cur=S.sort[skKey]||{};S.sort[skKey]={key:k,dir:cur.key===k?-cur.dir:-1};onChange();}); }

// QS p/ endpoints que agregam no servidor (Painel gerencial): serverQS + os filtros GLOBAIS do
// topo. Não usa exportQS() de propósito — aquele carrega estado específico de aba (val_faixa,
// ven_mes, par_faixa…) que não faz sentido nos resumos.
function filtrosQS(){
  const p=new URLSearchParams(serverQS()), f=S.cli;
  if(f.comprador) p.set('comprador_cod',f.comprador);
  if(f.curva&&f.curva.length) p.set('curva',f.curva.join(','));
  if(f.xyz && f.xyz.length) p.set('xyz',f.xyz.join(','));
  if(f.fornec) p.set('fornec',f.fornec);
  if(f.depto) p.set('depto',f.depto);
  if((f.busca||'').trim()) p.set('busca',f.busca.trim());
  return p.toString();
}

function exportQS(){
  const p=new URLSearchParams(serverQS()), f=S.cli;
  if(f.comprador) p.set('comprador_cod',f.comprador);
  if(f.curva && f.curva.length) p.set('curva',f.curva.join(','));
  if(f.xyz && f.xyz.length) p.set('xyz',f.xyz.join(','));
  if(f.fornec) p.set('fornec',f.fornec);
  if(f.depto) p.set('depto',f.depto);
  // filtros do Explorador de produtos — os 4 têm de viajar, senão o PDF sai com o universo
  // inteiro enquanto a tela mostra o recorte (era o caso de margem/cobMax/semPed).
  if(S.view==='produtos'){
    if(f.abast.length) p.set('abast',f.abast.join(','));
    if((f.margem||[]).length) p.set('margem',f.margem.join(','));
    if(f.cobMax!==''&&f.cobMax!=null&&!isNaN(+f.cobMax)) p.set('cob_max',f.cobMax);
    if(f.semPed) p.set('sem_ped','1');
  }
  if(S.view==='vencidos'){ if(S.venMes) p.set('ven_mes',S.venMes); if(S.venPer&&S.venPer!=='tudo') p.set('ven_per',S.venPer); }
  if(S.view==='leadtime'&&S.ltMin) p.set('lt_min',S.ltMin);
  if(f.valDias && S.view==='validade') p.set('val_dias',f.valDias);
  if(S.valFaixa && S.view==='validade'){ p.set('val_faixa_lo',S.valFaixa[0]); p.set('val_faixa_hi',S.valFaixa[1]); }
  if((f.busca||'').trim()) p.set('busca',f.busca.trim());
  if(f.ezStatus) p.set('ez_status',f.ezStatus);
  if(f.cobFaixa && f.cobFaixa.length) p.set('cob_faixa',f.cobFaixa.join(','));
  if(f.parFaixa && f.parFaixa.length && S.view==='parado') p.set('par_faixa',f.parFaixa.join(','));
  if(f.cobSub) p.set('cob_sub',f.cobSub);
  if(f.cobPed) p.set('cob_ped',f.cobPed);
  if(f.parClasse) p.set('par_classe',f.parClasse);
  if(f.fornClasse) p.set('forn_classe',f.fornClasse);
  return p.toString();
}
function exportBtns(view){ const qs=exportQS(); return `<span class="exp"><a class="btn sm" href="/estoque/api/export/${view}.xlsx?${qs}">⬇ Excel</a><a class="btn sm" href="/estoque/api/export/${view}.pdf?${qs}">⬇ PDF</a></span>`; }
function head(title,view){ return `<h2 class="section"><span>${title}${view?tip(view,'_title'):''}</span>${view?exportBtns(view):''}</h2>`; }

/* ───────── VIEWS ───────── */
function kpi(l,v,sub,dot){ return `<div class="card kpi"><div class="k-label">${dot?`<span class="dot" style="background:${dot}"></span>`:''}${l}</div><div class="k-value">${v}</div>${sub?`<div class="k-sub">${sub}</div>`:''}</div>`; }
function alertCard(qt,label,valor,color,view,filt){ return `<div class="alert" style="--c:${color}" data-view="${view}" data-filt='${esc(JSON.stringify(filt||{}))}'><div class="a-top"><div class="a-qt">${int(qt)}</div><div class="a-valor">${moneyK(valor)}</div></div><div class="a-label">${label}</div><div class="a-go">ver →</div></div>`; }
function wireAlerts(el){ el.querySelectorAll('.alert').forEach(a=>a.onclick=()=>goView(a.dataset.view,JSON.parse(a.dataset.filt||'{}'))); }

// cores por SEMÂNTICA de cobertura: ruptura(vermelho) → saudável(verde) → excesso(roxo)
const COR_FAIXA={'0-30':C.red,'31-60':C.green,'61-90':'#22c55e','91-120':C.yellow,'121+':C.purple,'sem giro':C.dim};
function renderCockpit(P){
  const k=agg(P);
  // Resumo de vencimento recalculado sobre os lotes FILTRADOS. O `/api/validade` devolve o FEFO
  // inteiro e o `resumo` dele é global — usá-lo direto fazia o alerta "Vencimento ≤7 dias" e os
  // "Maiores ofensores" ignorarem o filtro de fornecedor (reclamação do diretor 07/2026), enquanto
  // todo o resto do Cockpit acompanhava. Sem filtro ativo, isto bate igual ao resumo do servidor.
  const _LF=lotesFiltrados(), _crit=_LF.filter(l=>l.classificacao==='critico');
  const v={critico:_crit.length, valor_risco_critico:_crit.reduce((s,l)=>s+(l.valor_risco||0),0),
           valor_risco:_LF.reduce((s,l)=>s+(l.valor_risco||0),0), n:_LF.length};
  const el=$('#v-cockpit');
  const totItens=P.length||1;
  const periodoLbl={mes:'no mês','90d':'90 dias','6m':'6 meses','12m':'12 meses'}[S.vperiodo];
  el.innerHTML=`
   <div class="kpi-grid">
     ${kpi('Valor em estoque',money(k.valor_total),int(k.com_estoque)+' itens (compras)',C.accent)}
     ${kpi('Venda '+periodoLbl,money(k.venda_total),'lucro '+moneyK(k.lucro_total),C.green)}
     ${kpi('Margem',k.margem_total!=null?dec(k.margem_total,1)+'%':'—','venda × custo',C.accent2)}
     ${kpi('Em ruptura',int(k.ruptura.zerados),'estoque ≤ 0 c/ giro',C.red)}
     ${kpi('A comprar',int(k.repor.n),'sug. '+moneyK(k.repor.valor),C.orange)}
     ${kpi('Capital parado',moneyK(k.valor_parado),dec(k.valor_total?k.valor_parado/k.valor_total*100:0,1)+'% do estoque',C.purple)}
   </div>
   <h2 class="section"><span>Alertas de ação${tipT('Ações prioritárias do dia — rupturas, cobertura crítica, compras a fazer, vencimentos e estoque parado. Clique num card para ir direto à aba.')}</span></h2>
   <div class="alerts">
     ${alertCard(k.ruptura.zerados,'Em ruptura (estoque ≤ 0)',k.ruptura.valor_zerados,C.red,'estoque_zero',{})}
     ${alertCard(k.ruptura.f0_15,'Cobertura crítica (≤15d)',k.ruptura.valor,C.orange,'ruptura',{cobFaixa:'0-30'})}
     ${alertCard(k.repor.n,'Comprar (cobertura baixa)',k.repor.valor,C.orange,'reposicao',{})}
     ${alertCard(v.critico||0,'Vencimento ≤7 dias',v.valor_risco_critico!=null?v.valor_risco_critico:v.valor_risco,C.yellow,'validade',{})}
     ${alertCard(k.parado.muito_critico.qt,'Parado 120+ dias',k.parado.muito_critico.valor,C.purple,'parado',{parado:'muito_critico'})}
   </div>
   <div class="row">
     <div class="panel grow"><h3><span>Curva ABC (${S.abcLens==='estoque'?'estoque':'vendas'})${tipT('Classificação de Pareto por venda (ou por valor de estoque): A ≈ 80% do total, B ≈ 15%, C o restante. Alterne a base no botão.')}</span> <span class="seg" style="display:inline-flex;vertical-align:middle;margin-left:8px"><span class="seg-opt ${S.abcLens!=='estoque'?'on':''}" data-abclens="venda">Vendas</span><span class="seg-opt ${S.abcLens==='estoque'?'on':''}" data-abclens="estoque">Estoque</span></span></h3>
       <div style="display:flex;gap:24px;align-items:center;flex-wrap:wrap">
         <div class="chart-box sm" style="height:190px;flex:2 1 300px;min-width:0"><canvas id="ch-abc"></canvas></div>
         <div style="flex:1 1 240px;min-width:220px">
           <div class="count-line" style="margin:0 0 8px">Participação dos itens (quantidade)</div>
           <div style="display:flex;align-items:center;gap:16px">
             <div style="position:relative;height:150px;width:150px;flex:none">
               <canvas id="ch-abc-itens"></canvas>
               <div style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;pointer-events:none">
                 <div style="font-size:1.3rem;font-weight:700;color:var(--text);line-height:1">${int(totItens)}</div>
                 <div style="font-size:.6rem;color:var(--text-dim);text-transform:uppercase;letter-spacing:.6px">itens</div>
               </div>
             </div>
             <div id="abc-itens-leg" style="display:flex;flex-direction:column;gap:10px"></div>
           </div>
         </div>
       </div>
       <table class="mini" style="margin-top:10px">${['A','B','C'].map(c=>{const _v=S.abcLens==='estoque'?k.abc[c].valor:k.abc[c].venda,_t=S.abcLens==='estoque'?k.valor_total:k.venda_total;return `<tr><td>Curva ${c}</td><td class="num">${int(k.abc[c].qt)} itens</td><td class="num">${money(_v)}</td><td class="num">${dec(k.abc[c].qt/totItens*100,0)}% dos itens</td><td class="num">${dec(_t?_v/_t*100:0,0)}% ${S.abcLens==='estoque'?'do estoque':'da venda'}</td></tr>`;}).join('')}</table>
     </div>
   </div>
   <div class="row">
     <div class="panel grow"><h3><span>Maiores ofensores — capital parado${tipT('Os produtos com mais dinheiro parado (estoque sem giro ou sem venda recente).')}</span></h3><div id="cp-parado"></div></div>
     <div class="panel grow"><h3><span>Maiores ofensores — risco de vencimento${tipT('Os produtos com maior valor em risco de perder a validade no horizonte configurado.')}</span></h3><div id="cp-venc"></div></div>
   </div>`;
  chart('ch-abc',{type:'bar',data:{labels:['A','B','C'],datasets:[{data:['A','B','C'].map(c=>S.abcLens==='estoque'?k.abc[c].valor:k.abc[c].venda),backgroundColor:[C.green,C.accent,C.dim],borderRadius:6}]},options:{plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>money(c.raw)+' · '+k.abc[['A','B','C'][c.dataIndex]].qt+' itens'}}},scales:{y:{ticks:{callback:v=>moneyK(v)}}}}});
  // rosca de participação dos itens por curva (quantidade) — cores fixas A/B/C (verde/azul/cinza), borda = surface p/ respiro
  chart('ch-abc-itens',{type:'doughnut',data:{labels:['Curva A','Curva B','Curva C'],datasets:[{data:['A','B','C'].map(c=>k.abc[c].qt),backgroundColor:[C.green,C.accent,C.dim],borderColor:getComputedStyle(document.documentElement).getPropertyValue('--surface').trim()||'#111827',borderWidth:2,hoverOffset:4}]},options:{cutout:'64%',plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>c.label+': '+int(c.raw)+' itens ('+dec(totItens?c.raw/totItens*100:0,1)+'%)'}}}}});
  const _abcLeg=$('#abc-itens-leg'); if(_abcLeg) _abcLeg.innerHTML=['A','B','C'].map((c,i)=>`<div style="display:flex;align-items:center;gap:8px;font-size:.82rem;white-space:nowrap"><span style="width:11px;height:11px;border-radius:3px;background:${[C.green,C.accent,C.dim][i]};flex:none"></span><b>Curva ${c}</b> <span style="color:var(--text-dim)">${int(k.abc[c].qt)} · ${dec(k.abc[c].qt/totItens*100,0)}%</span></div>`).join('');
  document.querySelectorAll('[data-abclens]').forEach(b=>b.onclick=()=>{S.abcLens=b.dataset.abclens;render();});
  const topPar=P.filter(p=>p.status_parado).sort((a,b)=>b.valor-a.valor).slice(0,6);
  const topVen=_LF.slice().sort((a,b)=>b.valor_risco-a.valor_risco).slice(0,6);
  $('#cp-parado').innerHTML=topPar.map(p=>`<div class="lote-row" data-cod="${p.codprod}" style="cursor:pointer"><span class="prod">${esc(p.descricao)}</span><span class="lr-r">${money(p.valor)}<br><small class="muted">${p.dias_sem_venda==null?'sem saída':p.dias_sem_venda+'d s/ venda'}</small></span></div>`).join('')||'<div class="empty">Nada parado 🎉</div>';
  $('#cp-venc').innerHTML=topVen.map(l=>`<div class="lote-row" data-cod="${l.codprod}" style="cursor:pointer"><span class="prod">${esc(l.descricao)}</span><span class="lr-r">${money(l.valor_risco)}<br><small class="muted">vence ${l.dias_para_vencer}d</small></span></div>`).join('')||'<div class="empty">Sem risco no horizonte 🎉</div>';
  el.querySelectorAll('.lote-row[data-cod]').forEach(r=>r.onclick=()=>openProduto(r.dataset.cod));
  wireAlerts(el);
}

// faixas de cobertura — métrica OFICIAL da planilha (GRAFICO COBERTURA ESTOQUE), faixas fixas
const FX_COB=[{key:'0-30',label:'0-30 · risco ruptura',color:C.red},{key:'31-60',label:'31-60 · OK',color:C.green},
  {key:'61-90',label:'61-90 · atenção',color:C.yellow},{key:'91-120',label:'91-120 · urgente',color:C.orange},
  {key:'121+',label:'121+ · crítico',color:C.purple}];
const cobDiasFmt = v => v==null?'—':(v>=9999?'∞':int(v)+'d');
// filtro de faixa de cobertura é MULTI-seleção (pode marcar várias faixas de uma vez)
const cobFaixaLabel=arr=>!arr.length?'Todas':(arr.length===1?((FX_COB.find(f=>f.key===arr[0])||{}).label||arr[0]):`${arr.length} faixas`);
function cobToggle(k){ const a=S.cli.cobFaixa||[]; S.cli.cobFaixa=a.includes(k)?a.filter(x=>x!==k):[...a,k]; S.cli.cobSub=''; render(); }

function renderRuptura(P){
  // distribuição da cobertura sobre a base inteira (igual à planilha), com valor por faixa
  const faixas=FX_COB.map(f=>{const it=P.filter(p=>p.cobertura_faixa===f.key);
    return{...f,valor:it.reduce((s,p)=>s+(p.valor||0),0),qt:it.length};});
  // colunas em caixa (mesmo padrão da aba Produtos): mantém unidade e ACRESCENTA cx
  P.forEach(p=>{const cx=p.caixa||1; p._giroCx=cx>1?Math.round((p.giro_mes||0)/cx):null; p._dispCx=cx>1?Math.round((p.qtdisp||0)/cx):null;});
  const cols=[colCod,colProd,colForn,{key:'curva_abc',label:'ABC',badge:true},{key:'codcomprador',label:'Comprador',fmt:(v,p)=>esc((p.comprador||'').split(' ')[0]||'—')},
    {key:'qtdisp',label:'Disp.',num:true,fmt:int},
    {key:'_dispCx',label:'Disp. cx',num:true,fmt:v=>v==null?'—':int(v)},
    {key:'valor',label:'Valor estoque',num:true,fmt:money},
    {key:'cobertura_dias',label:'Cob.',num:true,fmt:cobDiasFmt},
    {key:'qtd_ja_pedida',label:'Já ped.',num:true,fmt:v=>v>0?int(v):'—'},
    colGiroSpark,{key:'_giroCx',label:'Giro cx',num:true,fmt:v=>v==null?'—':int(v)},
    {key:'sugestao_cx',label:'Sugerido',num:true,html:p=>sugCxN(p)},
    {key:'cobertura_faixa',label:'Faixa',badge:true}];
  // no 121+ mostra a natureza (sem giro × excesso real) — separa estoque morto de excesso de compra
  const cf=S.cli.cobFaixa||[];
  const is121=cf.length===1&&cf[0]==='121+';
  if(is121) cols.push({key:'_tipo',label:'Tipo',html:p=>p.sem_giro
    ?'<span class="badge" style="background:#64748b22;color:#94a3b8">sem giro</span>'
    :'<span class="badge" style="background:#c084fc22;color:#c084fc">excesso</span>'});
  let rf=P;
  if(cf.length) rf=rf.filter(p=>cf.includes(p.cobertura_faixa));
  if(is121&&S.cli.cobSub==='semgiro') rf=rf.filter(p=>p.sem_giro);
  if(is121&&S.cli.cobSub==='excesso') rf=rf.filter(p=>p.excesso_real);
  if(S.cli.cobPed==='com') rf=rf.filter(p=>(p.qtd_ja_pedida||0)>0);
  if(S.cli.cobPed==='sem') rf=rf.filter(p=>(p.qtd_ja_pedida||0)<=0);
  rf=[...rf].sort((a,b)=>(b.valor||0)-(a.valor||0));  // maior estoque primeiro (P1)
  const semG=P.filter(p=>p.cobertura_faixa==='121+'&&p.sem_giro), exc=P.filter(p=>p.excesso_real);
  const el=$('#v-ruptura');
  el.innerHTML=head('Cobertura de estoque por faixa','ruptura')+
    resumoFaixasBlock('Por faixa de cobertura (valor de estoque)'+tipT('Valor de estoque em cada faixa de cobertura. Clique numa faixa para filtrar a tabela.'),faixas,rf,p=>p.valor,cf,'ch-cob')+
    `<div class="row" style="gap:14px;margin-bottom:4px">
       <div class="fb-group"><label>Faixa <small class="muted">(marque várias)</small></label>
         <details class="ms" id="cob-faixa"><summary class="fb-control" style="width:auto">${cobFaixaLabel(cf)}</summary>
           <div class="ms-menu">${FX_COB.map(f=>`<label><input type="checkbox" value="${f.key}" ${cf.includes(f.key)?'checked':''}>${f.label}</label>`).join('')}</div>
         </details></div>
       ${is121?`<div class="fb-group"><label>121+ · natureza</label>
         <select id="cob-sub" class="fb-control" style="width:auto">
           <option value="">Tudo (${int(semG.length+exc.length)})</option>
           <option value="semgiro" ${S.cli.cobSub==='semgiro'?'selected':''}>Sem giro (${int(semG.length)})</option>
           <option value="excesso" ${S.cli.cobSub==='excesso'?'selected':''}>Excesso real (${int(exc.length)})</option>
         </select></div>`:''}
       <div class="fb-group"><label>Pedido em aberto</label>
         <select id="cob-ped" class="fb-control" style="width:auto">
           <option value="">Todos</option>
           <option value="sem" ${S.cli.cobPed==='sem'?'selected':''}>Sem pedido (risco real)</option>
           <option value="com" ${S.cli.cobPed==='com'?'selected':''}>Já comprado</option>
         </select></div>
     </div>
     <div class="count-line">Cobertura = <b>ARREDONDA.CIMA(estoque ÷ giro diário)</b>; giro 0 → não calculável (cai em 121+). Métrica oficial da planilha. Ordene por <b>Valor estoque</b> p/ atacar maior capital. No <b>121+</b>, "sem giro" é estoque morto (liquidar) e "excesso real" é cobertura alta (reduzir compra).</div>`+renderTable(rf,cols,'ruptura');
  drawFaixaChart('ch-cob',faixas,f=>cobToggle(f.key));
  el.querySelectorAll('.vfx[data-fkey]').forEach(d=>d.onclick=()=>cobToggle(d.dataset.fkey));
  wirePorComprador(el);
  const fx=$('#cob-faixa'); if(fx) fx.addEventListener('change',()=>{S.cli.cobFaixa=[...fx.querySelectorAll('input[type=checkbox]:checked')].map(c=>c.value);S.cli.cobSub='';render();});
  const sb=$('#cob-sub'); if(sb) sb.onchange=e=>{S.cli.cobSub=e.target.value;render();};
  const pd=$('#cob-ped'); if(pd) pd.onchange=e=>{S.cli.cobPed=e.target.value;render();};
}

function renderEstoqueZero(P){
  const z=P.filter(p=>(p.qtdisp||0)<=0);
  const neg=z.filter(p=>(p.qtdisp||0)<0), comGiro=z.filter(p=>(p.giro_dia||0)>0), comPed=z.filter(p=>(p.qtd_ja_pedida||0)>0);
  // impacto financeiro da ruptura (a custo): volume parado/mês + custo de repor até o alvo
  const vendaPerdida=vendaPerdidaTotal(P);   // fonte única — mesmo número do card da Abastecimento
  // c/ impostos e em caixa fechada — mesma régua da aba Abastecimento (ver valReporNF)
  const custoRepor=comGiro.reduce((s,p)=>s+valReporNF(p),0);
  const custoReporInc=comGiro.reduce((s,p)=>s+valReporIncerto(p),0);
  const cols=[colCod,colProd,colForn,{key:'curva_abc',label:'ABC',badge:true},
    {key:'codcomprador',label:'Comprador',fmt:(v,p)=>esc((p.comprador||'').split(' ')[0]||'—')},
    {key:'qtdisp',label:'Estoque',num:true,html:p=>cxUn(p.qtdisp,p.caixa)},
    {key:'dias_sem_venda',label:'Dias s/ venda',num:true,fmt:v=>v==null?'nunca':int(v)},
    {key:'qtd_ja_pedida',label:'Já ped.',num:true,html:p=>p.qtd_ja_pedida>0?cxUn(p.qtd_ja_pedida,p.caixa):'—'},
    // mercadoria que JÁ CHEGOU e está em pré-entrada (aguardando liberação) — sem esta coluna o
    // comprador vê "Estoque 0" e não entende por que a sugestão caiu
    {key:'qt_transicao',label:'Recebido',num:true,html:p=>p.qt_transicao>0?`<b title="Chegou e está em pré-entrada, aguardando conferência/liberação. Já conta na projeção — não precisa comprar de novo.">${cxUn(p.qt_transicao,p.caixa)}</b>`:'—'},
    {key:'giro_mes',label:'Giro/mês',num:true,html:p=>`${cxUn(p.giro_mes,p.caixa)} ${spark(p.serie_giro)}`},
    {key:'sugestao_cx',label:'Sugerido (cx)',num:true,html:p=>sugCxN(p)},
    {key:'status_exec',label:'Status',html:p=>statExec(p.status_exec)}];
  const statuses=[...new Set(z.map(p=>p.status_exec))];
  const zf=S.cli.ezStatus?z.filter(p=>p.status_exec===S.cli.ezStatus):z;
  $('#v-estoque_zero').innerHTML=head('Estoque zerado e negativo','estoque_zero')+
    `<div class="kpi-grid" style="grid-template-columns:repeat(6,1fr)">
       ${kpi('Zerados / negativos',int(z.length),int(neg.length)+' negativos',C.red)}
       ${kpi('Com giro (ruptura real)',int(comGiro.length),'precisam repor',C.orange)}
       ${kpi('Já com pedido',int(comPed.length),'aguardando entrega',C.accent)}
       ${kpi('Recebido · aguard. liberação',int(z.filter(p=>(p.qt_transicao||0)>0).length),'já no armazém, em pré-entrada',C.accent)}
       ${kpi('Venda perdida (ruptura)',money(vendaPerdida),'dias em ruptura × giro × preço de venda',C.purple)}
       ${kpi('Custo de reposição',money(custoRepor),'repor até o alvo · c/ impostos',C.accent2)}
     </div>
     <div class="fb-group" style="margin:0 0 6px"><label>Filtrar status</label>
       <select id="ez-status" class="fb-control" style="width:auto">
         <option value="">Todos</option>
         ${statuses.map(s=>`<option value="${s}" ${S.cli.ezStatus===s?'selected':''}>${STAT_EXEC[s]?STAT_EXEC[s][0]:s}</option>`).join('')}
       </select></div>
     <div class="count-line">Todos os produtos com estoque (gerencial) ≤ 0. "Já ped." = pedido de compra real em aberto (Winthor). <b>"Recebido"</b> = mercadoria que já chegou e está em <b>pré-entrada</b> (bloqueada, aguardando liberação) — ela já entra na projeção, então não aparece mais como compra a fazer.</div>`+
    renderTable(zf,cols,'estoque_zero');
  const sel=$('#ez-status'); if(sel) sel.onchange=e=>{S.cli.ezStatus=e.target.value;render();};
}

const QUAL_CHK={
  sem_custo:{lbl:'Sem custo',cor:'red',fn:p=>(p.custo_unit||0)<=0},
  sem_fornecedor:{lbl:'Sem fornecedor',cor:'orange',fn:p=>p.codfornec==null},
  sem_comprador:{lbl:'Sem comprador',cor:'purple',fn:p=>p.codcomprador==null},
  sem_giro:{lbl:'Sem giro c/ estoque',cor:'yellow',fn:p=>(p.giro_dia||0)<=0&&(p.qtdisp||0)>0},
  estoque_negativo:{lbl:'Estoque negativo',cor:'red',fn:p=>(p.qtdisp||0)<0},
};
function renderQualidade(P){
  const keys=Object.keys(QUAL_CHK);
  const probs=p=>keys.filter(k=>QUAL_CHK[k].fn(p));
  const cat=S.cli.qualCat||'';
  let flagged=P.map(p=>({p,probs:probs(p)})).filter(x=>x.probs.length);
  if(cat) flagged=flagged.filter(x=>x.probs.includes(cat));
  flagged.sort((a,b)=>b.probs.length-a.probs.length);
  const counts={}; keys.forEach(k=>counts[k]=P.filter(QUAL_CHK[k].fn).length);
  const badge1=k=>`<span class="badge" style="background:${C[QUAL_CHK[k].cor]}22;color:${C[QUAL_CHK[k].cor]}">${QUAL_CHK[k].lbl}</span>`;
  const card=k=>`<div class="card kpi" data-cat="${k}" style="cursor:pointer;outline:${cat===k?'2px solid '+C[QUAL_CHK[k].cor]:'none'}">
    <div class="k-label"><span class="dot" style="background:${C[QUAL_CHK[k].cor]}"></span>${QUAL_CHK[k].lbl}</div>
    <div class="k-value">${int(counts[k])}</div></div>`;
  const qqs=exportQS()+(cat?'&cat='+encodeURIComponent(cat):'');
  $('#v-qualidade').innerHTML=head('Qualidade da base — produtos com cadastro/saldo inconsistente'+tipT('Produtos com cadastro ou saldo inconsistente. Corrigir na origem (Winthor) melhora todas as telas.'))+
    `<div style="display:flex;gap:8px;margin:0 0 10px"><a class="btn sm" href="/estoque/api/export/qualidade.xlsx?${qqs}">⬇ Excel</a><a class="btn sm" href="/estoque/api/export/qualidade.pdf?${qqs}">⬇ PDF</a></div>
     <div class="kpi-grid">${keys.map(card).join('')}</div>
     <div class="count-line">${int(flagged.length)} produtos${cat?` na categoria <b>${QUAL_CHK[cat].lbl}</b> · <a href="#" id="qual-clear">limpar</a>`:' com ao menos um problema'}. Corrigir na origem (Winthor) melhora todas as telas.</div>
     <div class="tbl-wrap"><table><thead><tr><th>Cód</th><th>Produto</th><th>Fornecedor</th><th>Comprador</th><th class="num">Estoque${tip('qualidade','Estoque')}</th><th class="num">Custo${tip('qualidade','Custo')}</th><th class="num">Giro/mês${tip('qualidade','Giro/mês')}</th><th>Problemas${tip('qualidade','Problemas')}</th></tr></thead>
     <tbody>${flagged.slice(0,400).map(({p,probs})=>`<tr data-cod="${p.codprod}"><td class="num">${p.codprod}</td><td><span class="prod">${esc(p.descricao)}</span></td><td><span class="prod">${esc(p.fornecedor||'—')}</span></td><td>${esc((p.comprador||'').split(' ')[0]||'—')}</td><td class="num">${int(p.qtdisp)}</td><td class="num">${p.custo_unit?money(p.custo_unit):'—'}</td><td class="num">${int(p.giro_mes)}</td><td>${probs.map(badge1).join(' ')}</td></tr>`).join('')}</tbody></table>
     ${flagged.length>400?`<div class="count-line">Mostrando 400 de ${int(flagged.length)}.</div>`:''}</div>`;
  const el=$('#v-qualidade');
  el.querySelectorAll('[data-cat]').forEach(c=>c.onclick=()=>{const k=c.dataset.cat;S.cli.qualCat=(cat===k)?'':k;render();});
  const qc=$('#qual-clear'); if(qc)qc.onclick=e=>{e.preventDefault();S.cli.qualCat='';render();};
  el.querySelectorAll('tbody tr').forEach(tr=>tr.onclick=()=>openProduto(tr.dataset.cod));
}

function renderReposicao(P){
  const rep=P.filter(p=>(p.sugestao_compra||0)>0&&(p.giro_dia||0)>0&&!p.compra_suspensa);
  const suspensos=P.filter(p=>p.compra_suspensa).sort((a,b)=>(b.sugestao_compra*b.custo_unit)-(a.sugestao_compra*a.custo_unit));
  // agrupa por fornecedor (+ cubagem do pedido sugerido = Σ caixas sugeridas × volume da caixa)
  const cubItem=p=>(p.sugestao_cx||0)*(p.cubagem_caixa_m3||0);
  const pesoItem=p=>(p.sugestao_cx||0)*(p.peso_caixa_kg||0);
  // IPI+ST previstos da linha. O title revela a FONTE: alíquota tirada do pedido real daquele
  // fornecedor (confiável) x perfil do fornecedor x cadastro (estimativa) — o comprador precisa
  // saber quando o número é praticado e quando é palpite.
  const FONTE_IMP={trib_entrada:'tributação de entrada do Winthor (rotina 212) — é a alíquota que o ERP vai aplicar',
    isento_cadastro:'produto isento de IPI no cadastro',
    cadastro:'ESTIMATIVA — este produto não tem regra fiscal cadastrada para a UF deste fornecedor; usando o % do cadastro',
    pedido_real:'ESTIMATIVA — sem regra fiscal; usando o praticado nas últimas compras deste fornecedor',
    perfil_fornecedor:'ESTIMATIVA — sem regra fiscal; usando o perfil deste fornecedor',
    sem_dado:'sem informação fiscal'};
  const impCell=p=>{const t=(p.perc_ipi||0)+(p.perc_st||0);
    if(t<=0) return p.trib_fonte==='isento_cadastro'?`<span class="muted" title="${FONTE_IMP.isento_cadastro}">isento</span>`:'—';
    const det=[(p.perc_ipi>0?`IPI ${dec(p.perc_ipi,2)}%`:''),(p.perc_st>0?`ST ${dec(p.perc_st,2)}%`:'')].filter(Boolean).join(' + ');
    // estimativa fica marcada: é nela que mora praticamente todo o erro residual (~5% dos itens)
    const fraca=p.trib_firme===false;
    return `<span title="${det} · ${FONTE_IMP[p.trib_fonte]||''}"${fraca?' class="muted"':''}>${dec(t,1)}%${fraca?' <b title="estimativa — confira antes de pedir">≈</b>':''}</span>`;};
  // duas réguas: `valor` = mercadoria (vira preço na planilha do Winthor) e `valorNF` =
  // mercadoria + IPI + ST previstos, que é o que o Orçamento mede (PCPEDIDO[VLTOTAL]).
  // O card mostra a NF em destaque: era aí que o comprador planejava R$ 39,5k e consumia R$ 45,0k.
  const g={}; rep.forEach(p=>{(g[p.codfornec]=g[p.codfornec]||{cod:p.codfornec,forn:p.fornecedor||('Forn '+p.codfornec),itens:[],valor:0,valorNF:0,incerto:0,cub:0,peso:0,zerados:0}); g[p.codfornec].itens.push(p); g[p.codfornec].valor+=valReporMerc(p); g[p.codfornec].valorNF+=valReporNF(p); g[p.codfornec].incerto+=valReporIncerto(p); g[p.codfornec].cub+=cubItem(p); g[p.codfornec].peso+=pesoItem(p); if(emRuptura(p)) g[p.codfornec].zerados++;});
  // ordenação da LISTA de fornecedores. Default = maior pedido primeiro (era a ordem fixa antiga).
  // Com a lista fechada, "vejo todos" só vira "sei por onde começar" se der para reordenar.
  const ORD={valor:['Valor sugerido (maior)',(a,b)=>b.valorNF-a.valorNF],
             zerados:['Itens zerados (mais)',(a,b)=>(b.zerados-a.zerados)||(b.valorNF-a.valorNF)],
             itens:['Nº de itens (mais)',(a,b)=>(b.itens.length-a.itens.length)||(b.valorNF-a.valorNF)],
             nome:['Fornecedor (A–Z)',(a,b)=>String(a.forn).localeCompare(String(b.forn))]};
  const ordK=ORD[S.repOrd]?S.repOrd:'valor';
  const grupos=Object.values(g).sort(ORD[ordK][1]);
  // totais da tela (respeitam os filtros do topo, como todo o resto da aba)
  const totNF=grupos.reduce((s,x)=>s+x.valorNF,0), totMerc=grupos.reduce((s,x)=>s+x.valor,0);
  const totInc=grupos.reduce((s,x)=>s+x.incerto,0), totItens=grupos.reduce((s,x)=>s+x.itens.length,0);
  const vendaPerdida=vendaPerdidaTotal(P), nRupt=P.filter(emRuptura).length;
  // Sem "abre sozinho quando sobra 1 fornecedor": qualquer regra do tipo torna o cabeçalho
  // inclicável naquele estado (o clique fecha, o re-render reabre) — clique morto é pior que
  // um clique a mais.
  const aberto=gr=>S.repAll||S.repOpen.has(gr.cod);
  // ───── lead REAL do fornecedor na linha (pedido do diretor 07/2026) ─────
  // "quando for fazer o pedido, não preciso entrar na aba lead time para verificar o tempo de
  // entrega do fornecedor, só olhar ali e ajustar no parâmetro".
  // ⚠️ Este é o lead MEDIDO (mediana das entradas ≥2d, igual à aba Lead time) — NÃO é o que a
  // sugestão usou. Desde 07/2026 a sugestão calcula com o parâmetro global da tela para TODOS os
  // fornecedores (core.py: "o slider vale p/ todos"). Os dois na mesma linha se pareceriam, então
  // o valor sai em LARANJA quando diverge do parâmetro: é o sinal de que há ajuste a fazer antes
  // de gerar o pedido. Sem isso a linha diria "14d" ao lado de um valor calculado com 10d.
  const _ex=fornExtra(), EX=_ex||{}, exLoading=(_ex===null);
  const leadParam=+S.params.lead||10;
  const leadCell=cod=>{
    // 3 estados explícitos, como na coluna de crescimento: NUNCA um número provisório ou velho
    if(exLoading) return `<span class="muted" title="Carregando o lead time real do fornecedor…">lead —</span>`;
    if(_fx.erro) return `<span style="color:${C.red}" title="Não foi possível carregar o lead time real. Recarregue a página.">lead —</span>`;
    const ex=EX[cod]||{}, lr=ex.lead_real;
    if(lr==null) return `<span class="muted" title="Sem entrada medida para este fornecedor — a sugestão usa o parâmetro de ${int(leadParam)}d.">lead —</span>`;
    const fraca=ex.lead_confiavel===false, difere=Math.abs(lr-leadParam)>=1;
    const nd=dec(lr,lr%1?1:0);
    const tit=`Lead real medido: ${nd}d${ex.lead_n?` (${int(ex.lead_n)} entradas)`:''}.`
      +(fraca?' AMOSTRA FRACA — poucas entradas medidas, confira antes de usar.':'')
      +(difere?` A sugestão foi calculada com ${int(leadParam)}d (⚙ Parâmetros). Ajuste antes de gerar o pedido.`
              :` Igual ao parâmetro em uso (${int(leadParam)}d).`);
    return `<span title="${tit}" style="${difere?`color:${C.orange}`:''}">lead <b>${fraca?'~':''}${nd}d</b>${difere?' ⚠':''}</span>`;
  };
  // ───── saldo de orçamento: quanto de verba ainda cabe ─────
  const _o=orcResumo(), orcLoading=(_o===null), O=_o||{};
  const orcCard=(()=>{
    if(orcLoading) return kpi('Saldo de orçamento','—','carregando a meta do mês…',C.dim);
    if(_orc.erro) return kpi('Saldo de orçamento','—','indisponível — recarregue a página',C.red);
    if(O.meta==null||!(O.meta>0)) return kpi('Saldo de orçamento','—','sem meta no mês para este escopo',C.dim);
    const saldo=+O.saldo||0, cons=O.pct_consumido!=null?O.pct_consumido*100:null;
    // cor pela mesma régua da aba Orçamento (≥100% vermelho, ≥85% laranja) — duas telas, um critério
    const cor=saldo<=0?C.red:(cons!=null&&cons>=85?C.orange:C.green);
    // o confronto que motivou o card: a sugestão da tela cabe no que sobrou?
    const cabe=totNF<=saldo;
    const sub=`meta ${moneyK(O.meta)} · comprado ${moneyK(O.comprado)}${cons!=null?` (${dec(cons,1)}%)`:''}`
      +(saldo>0&&!cabe?` · <b style="color:${C.orange}">a sugestão acima excede em ${moneyK(totNF-saldo)}</b>`:'')
      +(O.valor_aberto>0?` · ${moneyK(O.valor_aberto)} já comprometido em pedidos abertos`:'');
    return kpi('Saldo de orçamento',money(saldo),sub,cor);
  })();
  // filtros que recortam a sugestão mas NÃO o orçamento — a tela avisa em vez de fingir que fecham
  const _rotOrc={curva:'curva',xyz:'XYZ',fornec:'fornecedor',depto:'depto',busca:'busca'};
  const orcIgnora=Object.keys(_rotOrc).filter(k=>S.cli[k]&&S.cli[k].length).map(k=>_rotOrc[k]);
  const el=$('#v-reposicao');
  el.innerHTML=head('Abastecimento — o que comprar (por fornecedor)','reposicao')+
    `<div class="kpi-grid" style="grid-template-columns:repeat(4,1fr)">
       ${kpi('Total a comprar',money(totNF),`${int(grupos.length)} fornecedores · ${int(totItens)} itens · merc. ${moneyK(totMerc)}${notaIncerteza(totNF,totInc)}`,C.orange)}
       ${orcCard}
       ${kpi('Venda perdida (ruptura)',money(vendaPerdida),`${int(nRupt)} itens zerados com giro`,C.purple)}
       ${kpi('Fornecedores a comprar',int(grupos.length),'clique num deles para ver os itens',C.accent)}
     </div>
     <div class="count-line"><b>Saldo de orçamento</b> = meta do mês (65% da venda líq. 30d) − comprado no Winthor, do <b>comprador selecionado</b> (${esc(S.compradorNome||'empresa toda')}). É o mesmo número da aba <b>Pedidos → Orçamento</b>. ⚠️ Ele é do <b>mês inteiro</b> e <b>não responde aos filtros de tela</b>${orcIgnora.length?` — <b>filtro ativo (${esc(orcIgnora.join(', '))}): o “Total a comprar” encolheu, o saldo não</b>`:''}. É de propósito: a pergunta é quanta verba ainda existe, não quanta verba corresponde ao recorte.</div>
     <div class="count-line">Sugestão líquida = estoque-alvo (giro/dia × (lead + ${int(S.params.cob)}d)) − estoque projetado (disponível + <b>pedido real em aberto</b>), arredondada em <b>caixas</b>. <b>m³</b> = cubagem do pedido sugerido (caixas × volume da caixa). O <b>lead</b> entra na conta (o estoque cai até a mercadoria chegar) e usa o prazo do fornecedor quando houver. O total do fornecedor sai <b>com impostos (IPI/ST)</b> — é a mesma régua do Orçamento, que lê o valor da NF do Winthor; a coluna <b>Valor sug.</b> segue em mercadoria, que é o preço que vai na planilha de importação.</div>
     <div class="count-line">⚠️ O card <b>Venda perdida</b> conta os itens <b>zerados com giro</b> — conjunto parecido, mas não idêntico ao da lista abaixo (item suspenso entra num e não no outro). Os dois números não fecham entre si de propósito.</div>
     <div class="count-line"><b>lead</b> na linha = <b>lead real medido</b> do fornecedor (mediana das entradas, mesma régua da aba Lead time) — <b>não</b> é o que a sugestão usou. A sugestão calcula com o parâmetro <b>${int(leadParam)}d</b>, igual para todos. Lead <b style="color:${C.orange}">em laranja ⚠</b> = diferente do parâmetro: ajuste em <b>⚙ Parâmetros</b> antes de gerar o pedido daquele fornecedor. <b>~</b> antes do número = amostra fraca (poucas entradas medidas). <b>—</b> = fornecedor sem entrada medida.</div>
     <div class="row" style="gap:14px;margin-bottom:8px;align-items:flex-end">
       <div class="fb-group" style="margin:0"><label>Ordenar por</label>
         <select id="rep-ord" class="fb-control" style="width:auto">
           ${Object.entries(ORD).map(([k,v])=>`<option value="${k}" ${k===ordK?'selected':''}>${v[0]}</option>`).join('')}
         </select></div>
       <div class="fb-group" style="margin:0"><label>&nbsp;</label>
         <button class="btn sm" id="rep-toggle-all">${S.repAll?'⊟ Fechar todos':'⊞ Expandir todos'}</button></div>
     </div>`+
    (grupos.length?'':'<div class="empty">Nenhuma compra sugerida no filtro atual 🎉</div>')+
    grupos.map(gr=>{
      const op=aberto(gr);
      // O corpo só entra no DOM quando aberto. É isso que permitiu tirar o corte antigo de 40
      // fornecedores (`slice(0,40)`), que escondia o resto SEM AVISAR — justamente na tela cujo
      // pedido era "ver todos os fornecedores que eu preciso comprar".
      const corpo=op?`<div class="tbl-wrap"><table><thead><tr><th>Cód</th><th>Produto</th><th>Embalagem${tip('reposicao','Embalagem')}</th><th class="num">Disp.${tip('reposicao','Disp.')}</th><th class="num">Já ped.${tip('reposicao','Já ped.')}</th><th class="num">Cob.proj${tip('reposicao','Cob.proj')}</th><th class="num">Giro/mês${tip('reposicao','Giro/mês')}</th><th class="num">Sugerido (cx)${tip('reposicao','Sugerido (cx)')}</th><th class="num">m³${tip('reposicao','m³')}</th><th class="num">Valor sug.${tip('reposicao','Valor sug.')}</th><th class="num">Imp.${tip('reposicao','Imp.')}</th><th>Status${tip('reposicao','Status')}</th></tr></thead>
        <tbody>${gr.itens.sort((a,b)=>(a.cobertura_proj||0)-(b.cobertura_proj||0)).map(p=>`<tr data-cod="${p.codprod}"><td class="num">${p.codprod}</td><td><span class="prod">${esc(p.descricao)}</span></td><td>${embCell(p)}</td><td class="num">${int(p.qtdisp)}</td><td class="num">${p.qtd_ja_pedida>0?int(p.qtd_ja_pedida):'—'}${p.qt_transicao>0?` <b title="+${int(p.qt_transicao)} recebido, em pré-entrada (aguardando liberação)">+${int(p.qt_transicao)}</b>`:''}</td><td class="num">${cob(p.cobertura_proj)}</td><td class="num">${int(p.giro_mes)}</td><td class="num">${sugCxN(p)}</td><td class="num">${cubItem(p)>0?dec(cubItem(p),3):'—'}</td><td class="num">${money(p.valor_sugerido_liq)}</td><td class="num">${impCell(p)}</td><td>${statExec(p.status_exec)}</td></tr>`).join('')}</tbody></table></div>`:'';
      return `<div class="panel forn-grp${op?' on':''}">
        <h3 class="forn-hd" data-forngrp="${gr.cod}" style="cursor:pointer" title="${op?'Fechar':'Abrir'} os itens deste fornecedor">
          <span><span class="muted" style="display:inline-block;width:1em">${op?'▾':'▸'}</span>${esc(gr.forn)} <small class="muted">· ${gr.cod} · ${gr.itens.length} itens${gr.zerados?` · <b style="color:${C.red}">${int(gr.zerados)} zerado${gr.zerados>1?'s':''}</b>`:''}${gr.cub>0?` · ${dec(gr.cub,2)} m³`:''}${gr.peso>0?` · ${dec(gr.peso,1)} kg`:''} · ${leadCell(gr.cod)}</small></span>
          <span>${gr.valorNF>gr.valor+0.005?`${money(gr.valorNF)} <small class="muted">previsto c/ impostos · merc. ${money(gr.valor)}</small>`:money(gr.valor)}${notaIncerteza(gr.valorNF,gr.incerto)} <button class="btn sm primary rowact" data-fornped="${gr.cod}">Gerar pedido</button></span></h3>
        ${corpo}
      </div>`;}).join('')+
    (suspensos.length?`<div class="panel" style="border-color:var(--orange)">
      <h3><span>⚠ Rever antes de comprar — pararam de vender (${suspensos.length})${tipT('Itens com giro na média de 3 meses mas sem venda há 60 dias ou mais — confira antes de pedir (o giro pode estar “preso” no histórico).')}</span></h3>
      <div class="count-line">Têm giro na média de 3 meses, mas <b>sem venda há ≥60 dias</b> → a sugestão pode estar comprando estoque que travou. Confira antes de pedir.</div>
      <div class="tbl-wrap"><table><thead><tr><th>Cód</th><th>Produto</th><th>Fornecedor</th><th class="num">Disp.${tip('reposicao','Disp.')}</th><th class="num">Dias s/ venda${tip('reposicao','Dias s/ venda')}</th><th class="num">Giro/mês${tip('reposicao','Giro/mês')}</th><th class="num">Sugeria${tip('reposicao','Sugeria')}</th></tr></thead>
      <tbody>${suspensos.slice(0,100).map(p=>`<tr data-cod="${p.codprod}"><td class="num">${p.codprod}</td><td><span class="prod">${esc(p.descricao)}</span></td><td><span class="prod">${esc(p.fornecedor||'—')}</span></td><td class="num">${int(p.qtdisp)}</td><td class="num">${p.dias_sem_venda==null?'—':int(p.dias_sem_venda)}</td><td class="num">${int(p.giro_mes)}</td><td class="num">${int(p.sugestao_compra)}</td></tr>`).join('')}</tbody></table></div>
    </div>`:'');
  el.querySelectorAll('tbody tr').forEach(tr=>tr.onclick=e=>{if(!e.target.closest('.rowact'))openProduto(tr.dataset.cod);});
  // "Gerar pedido" vive DENTRO do cabeçalho que abre/fecha: sem o stopPropagation o clique no botão
  // também alternaria o grupo, e o comprador voltaria do modal com a lista aberta/fechada sozinha.
  el.querySelectorAll('[data-fornped]').forEach(b=>b.onclick=e=>{ e.stopPropagation(); const gr=grupos.find(x=>String(x.cod)===b.dataset.fornped); modalPedidoFornecedor(gr); });
  el.querySelectorAll('.forn-hd').forEach(h=>h.onclick=e=>{
    if(e.target.closest('.rowact')) return;
    const cod=+h.dataset.forngrp;
    // com "expandir todos" ligado, o 1º clique num cabeçalho fecha aquele grupo: sai do modo
    // global e mantém abertos os outros, que é o que o gesto quer dizer.
    if(S.repAll){ S.repAll=false; grupos.forEach(x=>{ if(x.cod!==cod) S.repOpen.add(x.cod); }); S.repOpen.delete(cod); }
    else if(S.repOpen.has(cod)) S.repOpen.delete(cod);
    else S.repOpen.add(cod);
    render();
  });
  const ro=$('#rep-ord'); if(ro) ro.onchange=e=>{ S.repOrd=e.target.value; render(); };
  const rt=$('#rep-toggle-all'); if(rt) rt.onclick=()=>{ S.repAll=!S.repAll; S.repOpen.clear(); render(); };
}

async function renderPlano(){
  const el=$('#v-plano');
  el.innerHTML=`<div class="loader"><div class="spinner"></div>Calculando plano de reposição…</div>`;
  let j; try{ j=await getJSON('/estoque/api/plano_reposicao?'+serverQS()); }
  catch(e){ el.innerHTML=`<div class="empty">Falha ao montar o plano: ${esc(e.message)}</div>`; return; }
  // aplica TODOS os filtros client (fornecedor, comprador, curva, XYZ, depto, busca) via filtered()
  const allow=new Set(filtered().map(p=>p.codprod));
  let itens=(j.itens||[]).filter(p=>allow.has(p.codprod));
  // explode liberações em buckets por semana
  const buckets={};
  itens.forEach(p=>p.liberacoes.forEach(l=>{(buckets[l.semana]=buckets[l.semana]||[]).push({...p,...l});}));
  const semanas=Object.keys(buckets).map(Number).sort((a,b)=>a-b);
  const fonteLbl=S.params.sazonal?'forecast sazonal (RCA, 24m)':(S.params.forecast?`forecast (RCA, ${S.params.fcmeses}m)`:'média 3m (oficial)');
  const totAgora=(buckets[0]||[]).reduce((s,x)=>s+(x.valor||0),0);
  const totFuturo=semanas.filter(w=>w>0).reduce((s,w)=>s+buckets[w].reduce((a,x)=>a+(x.valor||0),0),0);
  let html=`<h2 class="section"><span>Plano de reposição no tempo${tipT('Projeção do saldo semana a semana e QUANDO cada pedido precisa sair (recebimento − lead time). Sem trânsito no BI, todo reabastecimento é planejado.')}</span></h2>
    <div class="count-line">Saldo projetado semana a semana (demanda = giro/dia · ${fonteLbl}). Mostra <b>quando o pedido precisa sair</b> = recebimento − lead time. Sem dados de trânsito no BI → todo reabastecimento é planejado.</div>
    <div class="kpi-grid" style="grid-template-columns:repeat(3,1fr)">
      ${kpi('Liberar agora (esta semana)',money(totAgora),int((buckets[0]||[]).length)+' itens',C.orange)}
      ${kpi('Liberações futuras (12 sem.)',money(totFuturo),int(semanas.filter(w=>w>0).reduce((s,w)=>s+buckets[w].length,0))+' itens',C.accent)}
      ${kpi('Itens no plano',int(itens.length),'com giro e sugestão',C.accent2)}
    </div>`;
  if(!semanas.length){ el.innerHTML=html+'<div class="empty">Nenhuma reposição necessária no horizonte 🎉</div>'; return; }
  const hoje=new Date();
  html+=semanas.map(w=>{
    const lib=buckets[w].sort((a,b)=>b.valor-a.valor);
    const tot=lib.reduce((s,x)=>s+(x.valor||0),0);
    const dataLbl=new Date(hoje.getTime()+w*7*864e5).toLocaleDateString('pt-BR');
    const titulo=w===0?'⚡ Sair agora (esta semana)':`Semana +${w} · a partir de ${dataLbl}`;
    return `<div class="panel forn-grp">
      <h3><span>${titulo} <small class="muted">· ${lib.length} itens</small></span><span>${money(tot)}</span></h3>
      <div class="tbl-wrap"><table><thead><tr><th>Cód</th><th>Produto</th><th>Fornecedor</th><th class="num">Disp.${tip('plano','Disp.')}</th><th class="num">Cob.${tip('plano','Cob.')}</th><th class="num">Giro/mês${tip('plano','Giro/mês')}</th><th class="num">Qtd pedir${tip('plano','Qtd pedir')}</th><th class="num">Valor${tip('plano','Valor')}</th></tr></thead>
      <tbody>${lib.map(x=>`<tr data-cod="${x.codprod}"><td class="num">${x.codprod}</td><td><span class="prod">${esc(x.descricao)}</span></td><td><span class="prod">${esc(x.fornecedor||'—')}</span></td><td class="num">${int(x.qtdisp)}</td><td class="num">${cob(x.cobertura)}</td><td class="num">${int(x.giro_mes)}</td><td class="num">${sugCx(x.qt,x.qtunitcx)}</td><td class="num">${money(x.valor)}</td></tr>`).join('')}</tbody></table></div>
    </div>`;
  }).join('');
  el.innerHTML=html;
  el.querySelectorAll('tbody tr[data-cod]').forEach(tr=>tr.onclick=()=>openProduto(tr.dataset.cod));
}

function renderValidade(){
  const L=lotesFiltrados();
  const cols=[colCod,{key:'descricao',label:'Produto',fmt:v=>`<span class="prod" title="${esc(v)}">${esc(v)}</span>`},
    {key:'curva_abc',label:'ABC',badge:true},
    {key:'numlote',label:'Lote'},{key:'dtval',label:'Validade',fmt:dt},{key:'dias_para_vencer',label:'Dias',num:true},
    {key:'qt',label:'Qtd',num:true,fmt:int},{key:'saldo_proj',label:'Saldo proj.',num:true,fmt:int},
    {key:'valor_risco',label:'Valor risco',num:true,fmt:money},{key:'classificacao',label:'Classe',badge:true},
    {key:'_plano',label:'Ação',html:l=>planoCell('validade',l.codprod+'|'+l.dtval,l.codprod,l.descricao,l.dtval)}];
  // faixas — por faixa: valor de estoque (bruto qtd×custo), valor em risco (projetado) e nº lotes
  const faixas=[['0-15',0,15],['16-30',16,30],['31-60',31,60],['61-90',61,90],['90+',91,1e9]];
  const fd=faixas.map(([n,lo,hi])=>{const it=L.filter(l=>l.dias_para_vencer>=lo&&l.dias_para_vencer<=hi);
    return{n,qt:it.length,valor:it.reduce((s,l)=>s+(l.valor_risco||0),0),
      bruto:it.reduce((s,l)=>s+(l.qt||0)*(l.custo_unit||0),0)};});
  // filtro pelo gráfico/cards: clicar numa faixa filtra a tabela por aquela faixa de dias
  const Lf=S.valFaixa?L.filter(l=>l.dias_para_vencer>=S.valFaixa[0]&&l.dias_para_vencer<=S.valFaixa[1]):L;
  const baseCols=[C.red,C.orange,C.yellow,C.accent,C.dim];
  const barCols=baseCols.map((c,i)=>(!S.valFaixa||S.valFaixa[2]===faixas[i][0])?c:'rgba(100,116,139,.28)');
  const cards=fd.map((f,i)=>`<div class="vfx ${S.valFaixa&&S.valFaixa[2]===f.n?'on':''}" data-i="${i}" style="--c:${baseCols[i]}">
      <div class="vfx-h">${f.n} dias</div>
      <div class="vfx-v">${money(f.valor)}</div>
      <div class="vfx-s">risco · ${int(f.qt)} lotes · estoque ${moneyK(f.bruto)}</div></div>`).join('');
  // vencimento por comprador (respeita a faixa selecionada) — clicável p/ filtrar
  const compMap={}; (S.produtosAll||[]).forEach(p=>{if(p.comprador&&p.codcomprador!=null)compMap[p.comprador]=p.codcomprador;});
  const cg={}; Lf.forEach(l=>{const nome=l.comprador||'Sem comprador';const g=cg[nome]=cg[nome]||{nome,bruto:0,risco:0,n:0};
    g.bruto+=(l.qt||0)*(l.custo_unit||0); g.risco+=(l.valor_risco||0); g.n++;});
  const compRows=Object.values(cg).sort((a,b)=>b.bruto-a.bruto);
  const compTbl=`<h3><span>Vencimento por comprador${tipT('Valor de estoque e valor em risco de vencimento, por comprador.')}</span></h3>
    <div class="tbl-wrap"><table><thead><tr><th>Comprador</th><th class="num">Estoque${tip('validade','Estoque')}</th><th class="num">Risco${tip('validade','Risco')}</th><th class="num">Lotes</th></tr></thead>
    <tbody>${compRows.map(g=>{const cod=compMap[g.nome],sel=cod!=null&&String(cod)===S.cli.comprador;
      return `<tr data-comp="${cod!=null?cod:''}" style="${cod!=null?'cursor:pointer;':'opacity:.65;'}${sel?'background:var(--surface3);':''}"><td><span class="prod">${esc(g.nome)}</span></td><td class="num">${money(g.bruto)}</td><td class="num">${moneyK(g.risco)}</td><td class="num">${int(g.n)}</td></tr>`;}).join('')||'<tr><td colspan="4" class="muted">—</td></tr>'}</tbody></table></div>`;
  const el=$('#v-validade');
  el.innerHTML=head(`Validade / FEFO — próximos ${S.params.hor} dias`,'validade')
    +`<div class="panel"><h3><span>Por faixa de validade${tipT('Lotes por dias até vencer — separa estoque parado de risco real. Clique para filtrar.')}</span> <small class="muted">· estoque parado vs. risco · clique p/ filtrar</small></h3>
        <div class="vfx-row">${cards}</div>
        <div class="row" style="align-items:flex-start">
          <div style="flex:0 0 340px;max-width:340px"><div class="chart-box sm" style="height:170px"><canvas id="ch-val"></canvas></div></div>
          <div class="grow">${compTbl}</div>
        </div></div>
      <div class="panel" id="val-tbl"></div>`;
  const Ld=S.cli.valDias?Lf.filter(l=>l.dias_para_vencer<=S.cli.valDias):Lf;
  $('#val-tbl').innerHTML=
    `<div class="row" style="gap:14px;margin:0 0 8px;align-items:flex-end">
       <div class="fb-group"><label>Dias para vencer (≤)</label><input type="number" id="val-dias" value="${S.cli.valDias||''}" min="0" step="5" placeholder="todos" style="width:120px"></div>
       ${S.valFaixa?`<div class="count-line" style="margin:0">Filtrando faixa <b>${S.valFaixa[2]} dias</b> · <a href="#" id="val-clear">limpar</a></div>`:''}
     </div>`
    +renderTableInline(Ld,cols,'validade');
  const vd=$('#val-dias'); if(vd) vd.onchange=e=>{ S.cli.valDias=e.target.value!==''?Math.max(0,+e.target.value):''; render(); };
  if(S.valFaixa){const c=$('#val-clear'); if(c)c.onclick=e=>{e.preventDefault();S.valFaixa=null;render();};}
  el.querySelectorAll('.vfx').forEach(d=>d.onclick=()=>{const i=+d.dataset.i,f=faixas[i];S.valFaixa=(S.valFaixa&&S.valFaixa[2]===f[0])?null:[f[1],f[2],f[0]];render();});
  el.querySelectorAll('tr[data-comp]').forEach(tr=>{const cod=tr.dataset.comp; if(!cod)return;
    tr.onclick=()=>{ S.cli.comprador=(S.cli.comprador===cod)?'':cod; const sel=$('#f-comprador'); if(sel){sel.value=S.cli.comprador; S.compradorNome=S.cli.comprador?(sel.selectedOptions[0]?.textContent||''):'';} render(); };});
  chart('ch-val',{type:'bar',data:{labels:fd.map(f=>f.n),datasets:[{data:fd.map(f=>f.valor),backgroundColor:barCols,borderRadius:6}]},options:{
    onClick:(ev,els)=>{if(!els||!els.length)return;const i=els[0].index,f=faixas[i];S.valFaixa=(S.valFaixa&&S.valFaixa[2]===f[0])?null:[f[1],f[2],f[0]];render();},
    plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>'risco '+money(c.raw)+' · '+fd[c.dataIndex].qt+' lotes'}}},scales:{y:{ticks:{callback:v=>moneyK(v)}}}}});
  wirePlanoCells();
}
/* ───────── Vencidos (perda de validade — conta 200042) ─────────
   Contraponto do Validade/FEFO: lá é risco futuro, aqui é perda REALIZADA.
   Fonte: /estoque/api/vencidos (PCLANC 200042 → PCNFSAID → PCMOV, join por NUMTRANSVENDA). */
async function renderVencidos(){
  const el=$('#v-vencidos'), qs=serverQS();
  if(!S.vencidos || S.vencidosQS!==qs){          // cache por QS: clicar num mês não refaz o fetch
    el.innerHTML=`<div class="loader"><div class="spinner"></div>Carregando vencidos…</div>`;
    try{ S.vencidos=await getJSON('/estoque/api/vencidos?'+qs); S.vencidosQS=qs; }
    catch(e){ el.innerHTML=`<div class="empty">Falha ao carregar vencidos: ${esc(e.message)}</div>`; return; }
  }
  const J=S.vencidos;
  // filtros do topo (mesma semântica das outras abas): comprador + fornecedor
  const fc=S.cli.comprador, ff=S.cli.fornec;
  // período (2026 | 12m | tudo) — o "já venceu e ainda está em estoque" NÃO usa isto
  // (é risco atual, precisa do histórico completo p/ a reincidência).
  const per=S.venPer||'2026';
  const allMonths=[...new Set((J.itens||[]).map(i=>i.mes).filter(Boolean))].sort();
  const perMonths = per==='tudo' ? null
    : per==='2026' ? new Set(allMonths.filter(m=>m.startsWith('2026')))
    : new Set(allMonths.slice(-12));
  const perOK=m=>!perMonths||perMonths.has(m);
  const perTudo=per==='tudo';
  const keep=r=>(!fc||String(r.codcomprador)===fc) && (!ff||String(r.codfornec)===ff) && perOK(r.mes);
  let itens=(J.itens||[]).filter(keep);
  if(S.venMes) itens=itens.filter(i=>i.mes===S.venMes);

  // Denominador do %: sem filtro de comprador é a venda do mês (J.meses); COM filtro é a venda
  // daquele comprador naquele mês (J.venda_comp_mes, chave "cc|YYYY-MM").
  // Até 07/2026 o cruzado não existia — a query já trazia comprador × mês, mas o servidor
  // colapsava as duas dimensões em mapas separados. Consequência: com comprador filtrado a linha
  // de % sumia do gráfico e o card caía no % all-time, ignorando o seletor de período em silêncio.
  const jm={}; (J.meses||[]).forEach(m=>{jm[m.mes]={venda:m.venda,pct:m.pct};});
  const vcm=J.venda_comp_mes||{};
  const vendaDoMes=mes=>fc?(vcm[fc+'|'+mes]??null):(jm[mes]?.venda??null);
  const _r3=v=>Math.round(v*1000)/1000;   // mesma precisão do _round(...,3) do servidor
  // meses recalculados a partir das linhas visíveis → respeitam o filtro de comprador
  const mm={}; (J.itens||[]).filter(keep).forEach(i=>{ if(!i.mes)return;
    const g=mm[i.mes]=mm[i.mes]||{mes:i.mes,itens:0,qt:0,valor:0}; g.itens++; g.qt+=i.qt||0; g.valor+=i.total||0; });
  const meses=Object.values(mm).map(g=>{const v=vendaDoMes(g.mes);
    return {...g, venda:v, pct:(v?_r3(g.valor/v*100):null)};}).sort((a,b)=>a.mes<b.mes?1:-1);
  const tot=(k)=>itens.reduce((s,i)=>s+(i[k]||0),0);
  const emEst=(J.em_estoque||[]).filter(p=>(!fc||String(p.codcomprador)===fc)&&(!ff||String(p.codfornec)===ff));
  const pior=meses.length?meses.reduce((a,b)=>b.valor>a.valor?b:a):null;
  const mesLbl=m=>{const[a,b]=m.split('-');return ['jan','fev','mar','abr','mai','jun','jul','ago','set','out','nov','dez'][+b-1]+'/'+a.slice(2);};
  // % global do PERÍODO: perda ÷ venda líquida dos meses visíveis que têm venda (RCA ≥2024).
  // Agora vale TAMBÉM com comprador filtrado — antes caía no % all-time daquele comprador, então
  // o card respondia a um período diferente do que estava selecionado na tela, sem avisar.
  // ⚠️ E TAMBÉM com um mês clicado no gráfico (S.venMes): `meses` não pode ser filtrado (alimenta
  // o gráfico e a tabela lateral, que precisam da série inteira), então o recorte entra AQUI.
  // Sem isto, clicar em abr/26 mudava "Valor perdido" e deixava o "% da venda" no total do
  // período — dois cards lado a lado falando de recortes diferentes, sem nada indicando.
  const mesesCV=meses.filter(m=>m.venda!=null && (!S.venMes||m.mes===S.venMes));
  const perdaCV=mesesCV.reduce((s,m)=>s+m.valor,0), vendaCV=mesesCV.reduce((s,m)=>s+m.venda,0);
  const pctGlobal=vendaCV?perdaCV/vendaCV*100:null;
  const perLbl={'2026':'2026','12m':'12 meses','tudo':'Tudo'};

  el.innerHTML=head('Vencidos — perda por validade','vencidos')
    +`<div class="row" style="margin:2px 0 12px"><div class="fb-group"><label>Período</label>
        <div class="seg" id="ven-per">${['2026','12m','tudo'].map(p=>
          `<span class="seg-opt ${per===p?'on':''}" data-per="${p}">${perLbl[p]}</span>`).join('')}</div></div></div>
      <div class="kpi-grid">
        ${kpi('Valor perdido',money(tot('total')),`${int(itens.length)} itens · ${int(tot('qt'))} un`,C.red)}
        ${kpi('% da venda',pctGlobal!=null?pctGlobal.toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2})+'%':'—',
              'perda ÷ venda líquida'+(S.venMes?' · '+mesLbl(S.venMes):''),C.accent)}
        ${kpi('Produtos afetados',int(new Set(itens.map(i=>i.codprod)).size),
              S.venMes?`em ${mesLbl(S.venMes)}`:`em ${int(meses.length)} meses`,C.orange)}
        ${kpi('Pior mês',pior?mesLbl(pior.mes):'—',pior?money(pior.valor):'',C.yellow)}
        ${kpi('Ainda em estoque',int(emEst.length),`${money(emEst.reduce((s,p)=>s+(p.valor||0),0))} já perdidos · histórico`,C.purple)}
      </div>
      <div class="panel"><h3><span>Por mês${tipT('Perda por validade em cada mês. Clique num mês para filtrar o detalhe abaixo.')}</span> <small class="muted">· clique num mês p/ filtrar o detalhe</small></h3>
        <div class="row" style="align-items:flex-start">
          <div class="grow"><div class="chart-box sm" style="height:190px"><canvas id="ch-ven"></canvas></div></div>
          <div style="flex:0 0 300px;max-width:300px" id="ven-meses"></div>
        </div></div>
      <div class="row" style="align-items:flex-start">
        <div class="panel grow" id="ven-comp"></div>
        <div class="panel grow" id="ven-forn"></div>
      </div>
      <div class="panel" id="ven-tbl"></div>
      <div class="panel" id="ven-estoque"></div>`;
      // ↑ "ainda em estoque" fica POR ÚLTIMO de propósito: é a única visão que ignora o mês
      //   (olha o histórico todo p/ medir reincidência). Junto do gráfico de meses, dava a
      //   impressão errada de que deveria filtrar ao clicar num mês.

  // ── tabela de meses (o pedido do diretor) ──
  const mrows=[...meses].sort((a,b)=>a.mes<b.mes?1:-1);
  $('#ven-meses').innerHTML=`<div class="tbl-wrap" style="max-height:190px;overflow:auto"><table>
    <thead><tr><th>Mês</th><th class="num">Itens</th><th class="num">Valor</th></tr></thead>
    <tbody>${mrows.map(m=>`<tr data-mes="${m.mes}" style="cursor:pointer;${S.venMes===m.mes?'background:var(--surface3);':''}">
      <td>${mesLbl(m.mes)}</td><td class="num">${int(m.itens)}</td><td class="num">${money(m.valor)}</td></tr>`).join('')
      ||'<tr><td colspan="3" class="muted">—</td></tr>'}</tbody></table></div>`;
  el.querySelectorAll('tr[data-mes]').forEach(tr=>tr.onclick=()=>{
    S.venMes=(S.venMes===tr.dataset.mes)?null:tr.dataset.mes; render(); });
  // seletor de período (2026 | 12m | tudo) — troca some o mês selecionado p/ não ficar preso
  el.querySelectorAll('#ven-per .seg-opt').forEach(o=>o.onclick=()=>{
    S.venPer=o.dataset.per; S.venMes=null; render(); });

  // ── MELHORIA: já venceu e AINDA está em estoque = risco de vencer de novo ──
  const ecols=[{k:'codprod',label:'Cód',num:1},{k:'descricao',label:'Produto'},{k:'fornecedor',label:'Fornecedor'},
    {k:'vezes',label:'Vezes',num:1},{k:'qt',label:'Qt perdida',num:1},{k:'valor',label:'Já perdido',num:1},
    {k:'qtdisp',label:'Em estoque',num:1},{k:'prox_venc',label:'Próx. venc.',num:1},{k:'ultima',label:'Última perda',num:1}];
  const esk=S.sort['vencidos_est']||{key:'valor',dir:-1};
  // célula do próximo vencimento: data + dias, colorida por urgência (≤30d vermelho, ≤60d laranja)
  const proxCell=p=>{ if(!p.prox_venc) return '<td class="num muted">—</td>';
    const d=Math.round((new Date(p.prox_venc+'T00:00:00')-new Date(new Date().toDateString()))/864e5);
    const c=d<=30?C.red:(d<=60?C.orange:'');
    return `<td class="num" style="${c?`color:${c};font-weight:600`:''}">${dt(p.prox_venc)} <small class="muted">${d}d</small></td>`;};
  $('#ven-estoque').innerHTML=`<h3><span>⚠️ Já venceu e ainda está em estoque${tipT('Produtos que já geraram perda e ainda têm saldo — risco de vencer de novo. Usa o histórico completo (ignora o filtro de mês).')}</span>
      <small class="muted">· risco de vencer de novo · ${S.venMes
        ? `<b>histórico completo — não filtra por ${mesLbl(S.venMes)}</b>`
        : 'considera todo o histórico, independe do mês'}</small></h3>`
    +(emEst.length?`<div class="count-line">${int(emEst.length)} produtos · ${money(emEst.reduce((s,p)=>s+(p.valor||0),0))} já perdidos · <b style="color:${C.red}">próx. venc.</b> = quando o estoque atual vence</div>
      <div class="tbl-wrap"><table><thead><tr>${sortTh(ecols,esk)}</tr></thead><tbody>${
        _sortArr(emEst,esk).slice(0,100).map(p=>`<tr data-cod="${p.codprod}" style="cursor:pointer">
          <td class="num">${p.codprod}</td><td><span class="prod" title="${esc(p.descricao)}">${esc(p.descricao)}</span></td>
          <td><span class="prod" title="${esc(p.fornecedor)}">${esc(p.fornecedor)}</span></td>
          <td class="num">${int(p.vezes)}</td><td class="num">${int(p.qt)}</td><td class="num">${money(p.valor)}</td>
          <td class="num">${int(p.qtdisp)}</td>${proxCell(p)}<td class="num">${dt((p.ultima||'').slice(0,10))}</td></tr>`).join('')
      }</tbody></table></div>`
    :`<div class="empty">Nenhum item vencido continua em estoque. 👏</div>`);
  wireSortTbl($('#ven-estoque'),'vencidos_est',render);
  $('#ven-estoque').querySelectorAll('tr[data-cod]').forEach(tr=>tr.onclick=()=>openProduto(tr.dataset.cod));

  // ── rankings ──
  // pctMap (só comprador): cod → % da perda sobre a venda líquida (do servidor, all-time).
  // Como é all-time, só mostra sem filtro de mês; com mês selecionado a coluna vira "—".
  const pctComp={}; (J.por_comprador||[]).forEach(g=>{if(g.cod!=null)pctComp[String(g.cod)]=g.pct;});
  // attr: 'data-comp' (comprador) ou 'data-forn' (fornecedor) — ambos clicáveis p/ filtrar
  const rank=(arr,titulo,attr,selCod,pctMap)=>{const t=arr.reduce((s,g)=>s+(g.valor||0),0);
    const pcol=!!pctMap, semMes=!S.venMes&&!ff&&perTudo;   // % venda é all-time do comprador: só em "Tudo", sem filtro de mês/fornecedor
    return `<h3><span>${titulo}${tipT('Ranking da perda por validade. Clique para filtrar.')}</span> <small class="muted">· clique p/ filtrar</small></h3>
      <div class="tbl-wrap" style="max-height:280px;overflow:auto"><table>
      <thead><tr><th>Nome</th><th class="num">Valor</th><th class="num">Part.${tip('vencidos','Part.')}</th>${pcol?`<th class="num">% venda${tip('vencidos','% venda')}</th>`:''}<th class="num">Itens</th></tr></thead>
      <tbody>${arr.slice(0,15).map(g=>{const sel=g.cod!=null&&String(g.cod)===selCod;
        const pv=pcol&&semMes?pctMap[String(g.cod)]:null;
        return `<tr ${attr}="${g.cod!=null?g.cod:''}" style="${g.cod!=null?'cursor:pointer;':'opacity:.65;'}${sel?'background:var(--surface3);':''}">
        <td><span class="prod" title="${esc(g.nome)}">${esc(g.nome)}</span></td><td class="num">${money(g.valor)}</td>
        <td class="num">${t?pct(g.valor/t):'—'}</td>${pcol?`<td class="num">${pv!=null?pv.toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2})+'%':'—'}</td>`:''}<td class="num">${int(g.itens)}</td></tr>`;}).join('')
        ||`<tr><td colspan="${pcol?5:4}" class="muted">—</td></tr>`}</tbody></table></div>`;};
  const rk=(cod_key,nome_key,base)=>{const g={}; base.forEach(i=>{const d=g[i[nome_key]]=g[i[nome_key]]||{nome:i[nome_key],cod:i[cod_key],itens:0,valor:0};
    d.itens++; d.valor+=i.total||0;}); return Object.values(g).sort((a,b)=>b.valor-a.valor);};
  $('#ven-comp').innerHTML=rank(rk('codcomprador','comprador',itens),'Perda por comprador','data-comp',S.cli.comprador,pctComp);
  $('#ven-forn').innerHTML=rank(rk('codfornec','fornecedor',itens),'Perda por fornecedor','data-forn',S.cli.fornec);
  wirePorComprador($('#ven-comp'));
  // clique no fornecedor = mesmo efeito de digitar no filtro do topo (mantém a UI em sincronia)
  $('#ven-forn').querySelectorAll('tr[data-forn]').forEach(tr=>{const cod=tr.dataset.forn; if(!cod)return;
    tr.onclick=()=>{ S.cli.fornec=(S.cli.fornec===cod)?'':cod;
      const inp=$('#f-fornec');
      if(inp){ const m=(S.fornecedores||[]).find(x=>String(x.codfornec)===S.cli.fornec);
               inp.value=m?`${m.codfornec} · ${m.fornecedor}`:''; }
      render(); };});

  // ── detalhe: igual a planilha VENCIDOS do diretor ──
  const dcols=[{k:'dtsaida',label:'Data',num:1},{k:'numnota',label:'Nota',num:1},{k:'codfornec',label:'Cód forn.',num:1},
    {k:'fornecedor',label:'Fornecedor'},{k:'codprod',label:'Cód',num:1},{k:'descricao',label:'Produto'},
    {k:'qt',label:'Qt',num:1},{k:'punit',label:'P. unit.',num:1},{k:'total',label:'Total',num:1},{k:'comprador',label:'Comprador'}];
  const dsk=S.sort['vencidos']||{key:'dtsaida',dir:-1};
  $('#ven-tbl').innerHTML=`<h3><span>Detalhe${S.venMes?` — ${mesLbl(S.venMes)}`:''}${tipT('Itens das notas de baixa por validade (espelha a planilha VENCIDOS do diretor).')}</span>
      ${S.venMes?`<small class="muted">· <a href="#" id="ven-clear">limpar filtro do mês</a></small>`:''}</h3>
    <div class="count-line">${int(itens.length)} itens · ${money(tot('total'))}${itens.length>300?' (mostrando 300)':''}</div>
    <div class="tbl-wrap"><table><thead><tr>${sortTh(dcols,dsk)}</tr></thead><tbody>${
      _sortArr(itens,dsk).slice(0,300).map(i=>`<tr data-cod="${i.codprod}" style="cursor:pointer">
        <td class="num">${dt((i.dtsaida||'').slice(0,10))}</td><td class="num">${i.numnota}</td>
        <td class="num">${i.codfornec??'—'}</td><td><span class="prod" title="${esc(i.fornecedor)}">${esc(i.fornecedor)}</span></td>
        <td class="num">${i.codprod}</td><td><span class="prod" title="${esc(i.descricao)}">${esc(i.descricao)}</span></td>
        <td class="num">${int(i.qt)}</td><td class="num">${money(i.punit)}</td><td class="num">${money(i.total)}</td>
        <td><span class="prod" title="${esc(i.comprador)}">${esc((i.comprador||'').split(' ')[0]||'—')}</span></td></tr>`).join('')
      ||'<tr><td colspan="10" class="muted">—</td></tr>'}</tbody></table></div>`;
  wireSortTbl($('#ven-tbl'),'vencidos',render);
  $('#ven-tbl').querySelectorAll('tr[data-cod]').forEach(tr=>tr.onclick=()=>openProduto(tr.dataset.cod));
  const vc=$('#ven-clear'); if(vc) vc.onclick=e=>{e.preventDefault();S.venMes=null;render();};

  // ── gráfico: últimos 18 meses, mês selecionado destacado ──
  // TODOS os meses com perda (não só 18) — senão o card "N meses / R$ total" não bate com o
  // gráfico e parece contradição (o diretor reparou). Cap em 48m só p/ não explodir no futuro.
  const g18=[...meses].sort((a,b)=>a.mes<b.mes?-1:1).slice(-48);
  // combo: barras = R$ perdido (eixo esq) · linha = % da venda (eixo dir próprio, senão some no zero)
  const temPct=g18.some(m=>m.pct!=null);
  const dsBar={type:'bar',label:'Perdido',yAxisID:'y',data:g18.map(m=>m.valor),
    backgroundColor:g18.map(m=>(!S.venMes||S.venMes===m.mes)?C.red:'rgba(100,116,139,.28)'),borderRadius:6};
  const dsLine={type:'line',label:'% da venda',yAxisID:'y1',data:g18.map(m=>m.pct),
    borderColor:C.accent,backgroundColor:C.accent,borderWidth:2,pointRadius:2,tension:.35,spanGaps:true};
  chart('ch-ven',{data:{labels:g18.map(m=>mesLbl(m.mes)),datasets:temPct?[dsBar,dsLine]:[dsBar]},
    options:{maintainAspectRatio:false,   // sem isso o Chart.js trava em 2:1 e sobra vão à direita
      onClick:(ev,els)=>{if(!els||!els.length)return;const m=g18[els[0].index].mes;S.venMes=(S.venMes===m)?null:m;render();},
      plugins:{legend:{display:temPct,labels:{boxWidth:12,font:{size:10}}},
        tooltip:{callbacks:{label:c=>c.dataset.yAxisID==='y1'
          ?'% da venda: '+(c.raw!=null?c.raw.toLocaleString('pt-BR',{maximumFractionDigits:3})+'%':'—')
          :money(c.raw)+' · '+g18[c.dataIndex].itens+' itens'}}},
      scales:{y:{ticks:{callback:v=>moneyK(v)}},
        y1:{display:temPct,position:'right',grid:{drawOnChartArea:false},
          ticks:{callback:v=>v.toLocaleString('pt-BR',{maximumFractionDigits:2})+'%'}}}}});
}

function renderTableInline(P,cols,view){ // tabela sem o wrapper de section (usada dentro de painel)
  const sk=S.sort[view]||{key:'dias_para_vencer',dir:1};
  const rows=[...P].sort((a,b)=>{let x=a[sk.key],y=b[sk.key];if(x==null)x=Infinity;if(y==null)y=Infinity;if(typeof x==='string')return sk.dir*String(x).localeCompare(String(y));return sk.dir*(x-y);});
  const headr=cols.map(c=>`<th class="${c.num?'num':''}" data-k="${c.key}">${c.label}${tip(view,c.label)}${sk.key===c.key?(sk.dir<0?' ↓':' ↑'):''}</th>`).join('');
  const body=rows.slice(0,300).map(p=>`<tr data-cod="${p.codprod}">`+cols.map(c=>{let v=p[c.key];if(c.badge)return`<td>${badge(v,c.map?c.map(v):v)}</td>`;if(c.html)return`<td>${c.html(p)}</td>`;if(c.fmt)v=c.fmt(v,p);return`<td class="${c.num?'num':''}">${v==null?'—':v}</td>`;}).join('')+'</tr>').join('');
  setTimeout(()=>{const cont=$('#val-tbl');if(!cont)return;
    cont.querySelectorAll('thead th').forEach(th=>th.onclick=()=>{const k=th.dataset.k,cur=S.sort[view]||{};S.sort[view]={key:k,dir:cur.key===k?-cur.dir:-1};render();});
    cont.querySelectorAll('tbody tr').forEach(tr=>tr.onclick=e=>{if(!e.target.closest('.rowact'))openProduto(tr.dataset.cod);});
  },0);
  return `<div class="count-line">${int(rows.length)} lotes</div><div class="tbl-wrap"><table><thead><tr>${headr}</tr></thead><tbody>${body}</tbody></table></div>`;
}

/* ───────── resumo de faixas reutilizável (cards + gráfico + por comprador) ───────── */
function porCompradorHTML(items,valorFn){
  const cg={}; items.forEach(p=>{const nome=p.comprador||'Sem comprador';const cod=p.codcomprador;
    const g=cg[nome]=cg[nome]||{nome,cod,valor:0,n:0}; g.valor+=valorFn(p)||0; g.n++;});
  const rows=Object.values(cg).sort((a,b)=>b.valor-a.valor);
  return `<h3>Por comprador</h3><div class="tbl-wrap"><table><thead><tr><th>Comprador</th><th class="num">Valor</th><th class="num">Itens</th></tr></thead>
    <tbody>${rows.map(g=>{const sel=g.cod!=null&&String(g.cod)===S.cli.comprador;
      return `<tr data-comp="${g.cod!=null?g.cod:''}" style="${g.cod!=null?'cursor:pointer;':'opacity:.65;'}${sel?'background:var(--surface3);':''}"><td><span class="prod">${esc(g.nome)}</span></td><td class="num">${money(g.valor)}</td><td class="num">${int(g.n)}</td></tr>`;}).join('')||'<tr><td colspan="3" class="muted">—</td></tr>'}</tbody></table></div>`;
}
function wirePorComprador(el){
  el.querySelectorAll('tr[data-comp]').forEach(tr=>{const cod=tr.dataset.comp; if(!cod)return;
    tr.onclick=()=>{S.cli.comprador=(S.cli.comprador===cod)?'':cod; const sel=$('#f-comprador'); if(sel){sel.value=S.cli.comprador;S.compradorNome=S.cli.comprador?(sel.selectedOptions[0]?.textContent||''):'';} render();};});
}
/* `items` = conjunto que alimenta a tabela "Por comprador". Tem de ser o RECORTE VISÍVEL, não o
   universo: os cards e o gráfico são o seletor (mostram todas as faixas de propósito), mas a
   tabela embaixo responde à seleção, igual à lista de itens. Passar o universo aqui fazia o
   "Por comprador" mostrar R$ 48,8k / 82 itens enquanto a tela dizia 31 itens / R$ 11,1k —
   reportado pelo diretor 07/2026 na aba Parado; a Cobertura tinha o mesmo defeito. */
function resumoFaixasBlock(titulo,faixas,items,valorFn,active,chartId){
  const cards=faixas.map(f=>`<div class="vfx ${(Array.isArray(active)?active.includes(f.key):f.key===active)?'on':''}" data-fkey="${f.key}" style="--c:${f.color}">
      <div class="vfx-h">${f.label}</div><div class="vfx-v">${money(f.valor)}</div>
      <div class="vfx-s">${int(f.qt)} itens</div></div>`).join('');
  return `<div class="panel"><h3><span>${titulo}</span> <small class="muted">· clique p/ filtrar</small></h3>
      <div class="vfx-row">${cards}</div>
      <div class="row" style="align-items:flex-start">
        <div style="flex:0 0 340px;max-width:340px"><div class="chart-box sm" style="height:170px"><canvas id="${chartId}"></canvas></div></div>
        <div class="grow">${porCompradorHTML(items,valorFn)}</div>
      </div></div>`;
}
function drawFaixaChart(id,faixas,onPick){
  chart(id,{type:'bar',data:{labels:faixas.map(f=>f.label),datasets:[{data:faixas.map(f=>f.valor),backgroundColor:faixas.map(f=>f.color),borderRadius:6}]},
    options:{
      onClick:(ev,els)=>{ if(onPick&&els&&els.length) onPick(faixas[els[0].index]); },
      onHover:(ev,els)=>{ if(ev.native) ev.native.target.style.cursor=(onPick&&els.length)?'pointer':'default'; },
      plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>money(c.raw)+' · '+faixas[c.dataIndex].qt+' itens'}}},scales:{y:{ticks:{callback:v=>moneyK(v)}}}}});
}

// "dias parados" = dias sem venda; nunca-vendeu (null) conta como o pior (infinito → cai em 121+)
function paradoDias(p){ return (p.dias_sem_venda==null) ? Infinity : p.dias_sem_venda; }
// faixas FIXAS do gráfico-indicador (partição inteira, ≥ início, sem gap nem sobreposição)
const FX_PARADO=[{label:'15-30',lo:15,hi:30,color:C.green},{label:'31-60',lo:31,hi:60,color:C.yellow},
  {label:'61-90',lo:61,hi:90,color:C.orange},{label:'91-120',lo:91,hi:120,color:C.red},
  {label:'121+',lo:121,hi:Infinity,color:C.purple}];
function paradoFaixaLabel(p){ const d=paradoDias(p); const f=FX_PARADO.find(f=>d>=f.lo&&d<=f.hi); return f?f.label:null; }
function setParMin(v){ S.params.parado=v; const i=$('#p-parado'); if(i)i.value=v; render(); savePrefs(); }

const parFaixaLabel=arr=>!arr.length?'Todas':(arr.length===1?arr[0]:`${arr.length} faixas`);
function parToggle(k){ const a=S.cli.parFaixa||[]; S.cli.parFaixa=a.includes(k)?a.filter(x=>x!==k):[...a,k]; render(); }
function renderParado(P){
  // universo do PARADO = itens com estoque e ≥15 dias sem venda (parado_faixa != null, nunca-vendeu
  // em 121+), partido nas faixas 15-30…121+. As faixas SOMAM o total (reconcilia como a Cobertura).
  const universo=P.filter(p=>p.parado_faixa);
  const faixas=FX_PARADO.map(f=>{const it=universo.filter(p=>p.parado_faixa===f.label);
    return{...f,key:f.label,valor:it.reduce((s,p)=>s+(p.valor||0),0),qt:it.length};});
  const pf=S.cli.parFaixa||[];
  const par=pf.length?universo.filter(p=>pf.includes(p.parado_faixa)):universo;
  const totItens=par.length, totVal=par.reduce((s,p)=>s+(p.valor||0),0);
  if(!S.sort.parado) S.sort.parado={key:'valor',dir:-1};   // maior valor parado primeiro
  P.forEach(p=>{const cx=p.caixa||1; p._dispCx=cx>1?Math.round((p.qtdisp||0)/cx):null;});
  const cols=[colCod,colProd,colForn,{key:'curva_abc',label:'ABC',badge:true},{key:'dtultsaida',label:'Última venda',fmt:v=>dt(v)},
    {key:'dias_sem_venda',label:'Dias parado',num:true,fmt:v=>v==null?'nunca':int(v)},
    {key:'qtdisp',label:'Disp.',num:true,fmt:int},
    {key:'_dispCx',label:'Disp. cx',num:true,fmt:v=>v==null?'—':int(v)},
    {key:'valor',label:'Valor',num:true,fmt:money},
    {key:'status_saida',label:'Saída',badge:true},{key:'parado_faixa',label:'Faixa',badge:true},
    {key:'_plano',label:'Ação',html:p=>planoCell('parado',String(p.codprod),p.codprod,p.descricao,null)}];
  const el=$('#v-parado');
  el.innerHTML=head('Estoque parado — o que liquidar','parado')
    +resumoFaixasBlock('Valor parado por faixa (dias sem venda)'+tipT('Valor de estoque parado em cada faixa de dias sem venda. Clique para filtrar.'),faixas,par,p=>p.valor,pf,'ch-parado')
    +`<div class="row" style="gap:14px;margin:6px 0;align-items:flex-end">
        <div class="fb-group"><label>Faixa <small class="muted">(marque várias)</small></label>
          <details class="ms" id="par-faixa"><summary class="fb-control" style="width:auto">${parFaixaLabel(pf)}</summary>
            <div class="ms-menu">${FX_PARADO.map(f=>`<label><input type="checkbox" value="${f.label}" ${pf.includes(f.label)?'checked':''}>${f.label} dias</label>`).join('')}</div>
          </details></div>
        <div class="count-line" style="margin:0"><b>${int(totItens)} itens</b> · ${money(totVal)} parados${pf.length?' na(s) faixa(s) marcada(s)':' (≥15 dias, nunca vendidos incluídos)'}. <b>As faixas somam o total.</b> Clique num card/barra ou marque várias faixas.</div>
      </div>`
    +renderTable(par,cols,'parado');
  drawFaixaChart('ch-parado',faixas,f=>parToggle(f.key));
  el.querySelectorAll('.vfx[data-fkey]').forEach(d=>d.onclick=()=>parToggle(d.dataset.fkey));
  const fx=$('#par-faixa'); if(fx) fx.addEventListener('change',()=>{S.cli.parFaixa=[...fx.querySelectorAll('input[type=checkbox]:checked')].map(c=>c.value);render();});
  wirePorComprador(el);
  wirePlanoCells();
}

// zona de ação de cada célula ABC×XYZ: 1=automatizar(verde) 2=monitorar(âmbar) 3=sob demanda(vermelho)
const AXZONE={AX:1,AY:1,BX:1, AZ:2,BY:2,BZ:2,CX:2, CY:3,CZ:3};
function renderABCXYZ(P){
  const m={}; P.forEach(p=>{if(p.abc_xyz){(m[p.abc_xyz]=m[p.abc_xyz]||{qt:0,venda:0});m[p.abc_xyz].qt++;m[p.abc_xyz].venda+=(p.venda||0);}});
  const cell=(a,x)=>m[a+x]||{qt:0,venda:0};
  const rowT=a=>['X','Y','Z'].reduce((o,x)=>{const d=cell(a,x);return{qt:o.qt+d.qt,venda:o.venda+d.venda};},{qt:0,venda:0});
  const colT=x=>['A','B','C'].reduce((o,a)=>{const d=cell(a,x);return{qt:o.qt+d.qt,venda:o.venda+d.venda};},{qt:0,venda:0});
  const totVenda=['A','B','C'].reduce((s,a)=>s+rowT(a).venda,0)||1, totQt=P.length||1;
  // grid com totais nas margens
  let g=`<div class="axm"><div class="axm-corner"><span style="font-size:.58rem;color:var(--text-mute);text-transform:uppercase;letter-spacing:.5px;line-height:1.3">ABC↓<br>XYZ→</span></div>`+
    `<div class="axm-h"><b>X</b> estável</div><div class="axm-h"><b>Y</b> variável</div><div class="axm-h"><b>Z</b> errático</div><div class="axm-h">Total<br>curva</div>`;
  ['A','B','C'].forEach(a=>{
    g+=`<div class="axm-rh">${a}</div>`;
    ['X','Y','Z'].forEach(x=>{const k=a+x,d=cell(a,x),z=AXZONE[k];
      g+= d.qt
        ? `<div class="axm-cell z${z}" data-key="${k}" title="${k} · clique para listar os produtos"><span class="k">${k}</span><span class="p">${dec(d.venda/totVenda*100,0)}%</span><span class="q">${int(d.qt)}</span><span class="v">${moneyK(d.venda)}</span></div>`
        : `<div class="axm-cell empty"><span class="k">${k}</span><span class="q">0</span></div>`;});
    const rt=rowT(a);
    g+=`<div class="axm-tot"><span class="q">${int(rt.qt)}</span><span class="v">${moneyK(rt.venda)}</span></div>`;
  });
  g+=`<div class="axm-rh" style="font-size:.72rem;color:var(--text-mute)">Σ</div>`+
    ['X','Y','Z'].map(x=>{const ct=colT(x);return `<div class="axm-tot"><span class="q">${int(ct.qt)}</span><span class="v">${moneyK(ct.venda)}</span></div>`;}).join('')+
    `<div class="axm-tot grand"><span class="q">${int(totQt)}</span><span class="v">${moneyK(totVenda)}</span></div></div>`;
  // legenda por zona (mesma cor das células)
  const zones=[
    {z:'var(--green)',t:'Automatizar · nunca faltar',d:'Alto/médio valor e demanda previsível. Reposição no automático, controle rígido.',c:'AX · AY · BX'},
    {z:'var(--yellow)',t:'Monitorar · estoque de segurança',d:'Valor alto porém errático, ou giro baixo previsível. Acompanhar de perto e proteger com margem de segurança.',c:'AZ · BY · BZ · CX'},
    {z:'var(--red)',t:'Sob demanda · descontinuar',d:'Baixo valor e demanda imprevisível. Comprar sob pedido ou tirar de linha.',c:'CY · CZ'}];
  const leg=zones.map(o=>`<div class="axm-zone" style="--z:${o.z}"><div><div class="zt">${o.t}</div><div class="zd">${o.d}</div><div class="zc">${o.c}</div></div></div>`).join('');
  // leitura (insights automáticos)
  const redV=cell('C','Y').venda+cell('C','Z').venda, redQ=cell('C','Y').qt+cell('C','Z').qt, az=cell('A','Z'), cT=rowT('C');
  const read=`<div class="axm-read">
    <div class="ri"><span>Zona vermelha (CY+CZ) — candidatos a sair</span><b style="color:var(--red)">${moneyK(redV)} · ${int(redQ)} itens</b></div>
    <div class="ri"><span>AZ — alto valor, demanda errática (risco de ruptura)</span><b style="color:var(--yellow)">${int(az.qt)} itens · ${moneyK(az.venda)}</b></div>
    <div class="ri"><span>Curva C: ${dec(cT.qt/totQt*100,0)}% dos itens, só ${dec(cT.venda/totVenda*100,0)}% da venda</span><b>${int(cT.qt)} itens</b></div></div>`;
  $('#v-abcxyz').innerHTML=`<h2 class="section"><span>Matriz ABC-XYZ${tipT('Curva de vendas (ABC) × variabilidade da demanda (XYZ). Define a estratégia de reposição de cada item.')}</span></h2>
    <div class="row">
      <div class="panel" style="flex:1.7 1 540px"><h3><span>Curva de vendas (ABC) × Variabilidade da demanda (XYZ)${tipT('Cada célula = itens naquela combinação; a cor é a zona de ação. Clique numa célula para listar os produtos.')}</span></h3>${g}
        <div class="count-line" style="margin-top:14px">Cor = zona de ação · número = itens · valor = <b>venda</b> do período · % = fatia da venda. Clique numa célula para listar os produtos.</div></div>
      <div class="panel grow" style="flex:1 1 300px"><h3><span>Estratégia por zona${tipT('O que fazer em cada zona: automatizar (nunca faltar), monitorar (estoque de segurança) ou tirar de linha.')}</span></h3>${leg}
        <h3 style="margin-top:18px">Leitura</h3>${read}</div>
    </div>`;
  $('#v-abcxyz').querySelectorAll('.axm-cell[data-key]').forEach(c=>c.onclick=()=>{const k=c.dataset.key;S.cli.curva=[k[0]];S.cli.xyz=[k[1]];syncCurvaUI();syncXyzUI();goView('produtos',{});});
}

/* Ciclo de compras + verba por fornecedor: vêm de PCPEDIDO/PCVERBA, não da posição de estoque.
   Buscados SÓ quando a aba abre (não no snapshot — ver api_snapshot no routes.py) e cacheados por
   unidade+período. Enquanto não chegam, devolve null e a aba renderiza sem as colunas; quando
   chegam, dispara um render(). Falha → {} (a aba nunca quebra por causa disto). */
const _fx={key:null,map:null,loading:null,erro:false};

// ───────── saldo de orçamento p/ a aba Abastecimento (busca preguiçosa) ─────────
// Pedido do diretor 07/2026: "quanto falta de orçamento a usar, iria completar essa tela".
// Mesmo padrão do fornExtra: 3 estados, cache por comprador, nunca um número velho.
// ⚠️ O saldo é do MÊS e do COMPRADOR — não responde a curva/XYZ/fornecedor/busca. Isso é
// intencional e confirmado pelo diretor: ele quer saber quanta verba ainda tem, não quanta
// verba corresponde ao recorte da tela. A tela avisa quando há filtro que o card ignora.
const _orc={key:null,resumo:null,loading:null,erro:false};
function orcResumo(){
  const key=(S.compradorNome||'TODOS')+'|'+S.unidade;
  if(_orc.key===key) return _orc.resumo;
  if(_orc.loading!==key){
    _orc.loading=key;
    getJSON('/estoque/api/orcamento?comprador='+encodeURIComponent(S.compradorNome||'TODOS'))
      .then(o=>{_orc.key=key;_orc.resumo=o.resumo||{};_orc.erro=false;})
      .catch(()=>{_orc.key=key;_orc.resumo={};_orc.erro=true;})
      .finally(()=>{_orc.loading=null; if(S.view==='reposicao') render();});
  }
  return null;
}
function fornExtra(){
  const key=S.unidade+'|'+S.vperiodo;
  if(_fx.key===key) return _fx.map;
  if(_fx.loading!==key){
    _fx.loading=key;
    getJSON('/estoque/api/fornecedores_extra?'+serverQS())
      .then(o=>{_fx.key=key;_fx.map=o.extra||{};_fx.erro=false;})
      .catch(()=>{_fx.key=key;_fx.map={};_fx.erro=true;})
      // a Abastecimento também consome este extra (lead real na linha do fornecedor), então
      // precisa repintar quando ele chega — senão a coluna fica em "—" até o próximo render
      .finally(()=>{_fx.loading=null; if(S.view==='fornecedores'||S.view==='reposicao') render();});
  }
  return null;
}

/* Curva ABC do FORNECEDOR sobre o UNIVERSO (todo o snapshot), não sobre a lista filtrada.
   Bug achado pelo diretor 07/2026: filtrando só a BOMBRIL ela aparecia como C, e voltava a A com
   todos os fornecedores na tela. Pareto sobre recorte é matematicamente sem sentido — com um
   fornecedor só, o acumulado dele é 100%, o que cai direto na faixa C.
   É a mesma política que os PRODUTOS já seguem (curva atribuída no servidor sobre o conjunto
   inteiro; filtro de tela só recorta a lista). Só UNIDADE e PERÍODO redefinem a curva. */
function abcFornecedorMap(){
  const g={};
  (S.produtosAll||[]).forEach(p=>{ if(p.codfornec==null) return;
    g[p.codfornec]=(g[p.codfornec]||0)+(p.venda||0); });
  const arr=Object.entries(g).map(([cod,venda])=>({cod,venda})).sort((a,b)=>b.venda-a.venda);
  const tot=arr.reduce((s,o)=>s+o.venda,0)||1;
  const m={}; let ac=0;
  arr.forEach(o=>{ ac+=o.venda; const p=ac/tot*100; m[o.cod]=p<=80?'A':(p<=95?'B':'C'); });
  return m;
}

function renderFornecedores(P){
  // Opção A: nesta aba o filtro "Curva" age pela ABC do FORNECEDOR (não do produto).
  // Agrega ignorando a curva do produto (filtered(true)) e filtra os fornecedores por ABC no fim.
  const base=filtered(true);
  const tv=base.reduce((s,p)=>s+(p.valor||0),0)||1,tvenda=base.reduce((s,p)=>s+(p.venda||0),0)||1,g={};
  base.forEach(p=>{if(p.codfornec==null)return;const o=g[p.codfornec]=g[p.codfornec]||{codfornec:p.codfornec,fornecedor:p.fornecedor||('FORN '+p.codfornec),n_produtos:0,valor:0,giro:0,venda:0,lucro:0,disp:0,girodia:0,vendaAnt:0};o.n_produtos++;o.valor+=(p.valor||0);o.giro+=(p.giro_mes||0);o.venda+=(p.venda||0);o.vendaAnt+=(p.venda_ano_ant||0);o.lucro+=(p.lucro||0);o.disp+=(p.qtdisp||0);o.girodia+=(p.giro_dia||0);});
  const lead=S.params.lead||10;
  // índice = % na VENDA (R$) ÷ % no ESTOQUE (R$) — "vende mais do que pesa". Antes usava giro em
  // unidades, distorcendo fornecedor de alto valor/baixo volume.
  const _ex=fornExtra(), EX=_ex||{}, exLoading=(_ex===null);   // null = ainda carregando
  const F=Object.values(g).map(o=>{const pv=o.venda/tvenda*100,pe=o.valor/tv*100,idx=pe>0?pv/pe:(pv>0?999:0),cobertura=o.girodia>0?o.disp/o.girodia:null;
    let cl=(o.giro<=0&&o.venda<=0)?'critico_sem_giro':(cobertura!=null&&cobertura<lead?'ruptura':(idx>=1.2?'alta_performance':(idx>=0.8?'equilibrado':'estoque_alto')));
    const ex=EX[o.codfornec]||{}, verba=+ex.verba||0;
    // crescimento: régua COMPLETA do servidor (todos os produtos vendidos nas duas janelas).
    // O somatório local só enxerga o que está em estoque HOJE — o ano anterior sairia truncado
    // e o crescimento inflado (era o bug: 6 fornecedores chegavam a inverter o sinal).
    // 3 estados explícitos. NUNCA cair no somatório local como fallback: ele é justamente o
    // cálculo bugado (ano anterior truncado pelo snapshot). Mostrar "—" enquanto não há a régua
    // completa é honesto; mostrar o número velho é pior que não mostrar nada — ele chega a
    // inverter o sinal, e o usuário não tem como saber que está olhando um valor provisório.
    const temYoY=ex.venda_ant_yoy!=null&&+ex.venda_ant_yoy>0;
    const cresc=temYoY?((+ex.venda_yoy-+ex.venda_ant_yoy)/+ex.venda_ant_yoy*100):null;
    return{...o,pv,pe,idx,cobertura,margem:o.venda?o.lucro/o.venda*100:null,
      n_pedidos:ex.n_pedidos||0, ciclo_dias:ex.ciclo_dias==null?null:ex.ciclo_dias,
      verba, verba_campanha:+ex.verba_campanha||0,
      // lucro já existia agregado e só não era exibido; NUNCA recalcular como venda×margem
      // (margem vem de lucro÷venda arredondado — o caminho de volta reintroduz erro).
      lucro_verba:o.lucro+verba, margem_verba:o.venda?((o.lucro+verba)/o.venda*100):null,
      crescimento:cresc, yoyCompleto:temYoY, cl};}).sort((a,b)=>b.valor-a.valor);
  // curva ABC do fornecedor — do UNIVERSO, nunca do recorte (ver abcFornecedorMap)
  {const _m=abcFornecedorMap(); F.forEach(o=>{o.curva_abc=_m[o.codfornec]||'C';});}
  const cols=[{key:'codfornec',label:'Cód',num:true},{key:'fornecedor',label:'Fornecedor',fmt:v=>`<span class="prod">${esc(v)}</span>`},
    {key:'curva_abc',label:'ABC',badge:true},
    {key:'n_produtos',label:'Itens',num:true},{key:'valor',label:'Estoque',num:true,fmt:money},{key:'giro',label:'Giro/mês',num:true,fmt:int},
    {key:'cobertura',label:'Cob.',num:true,fmt:cob},
    {key:'venda',label:'Venda',num:true,fmt:money},colCresc,{key:'margem',label:'Margem',num:true,fmt:v=>v==null?'—':dec(v,1)+'%'},
    {key:'n_pedidos',label:'Compras',num:true,fmt:v=>v?int(v)+'×':'—'},
    {key:'ciclo_dias',label:'Ciclo 12m',num:true,fmt:v=>v==null?'—':dec(v,0)+'d'},
    {key:'lucro',label:'Lucro bruto',num:true,fmt:money},
    {key:'verba',label:'Verba',num:true,fmt:v=>v?money(v):'—'},
    {key:'lucro_verba',label:'Lucro c/ verba',num:true,fmt:money},
    {key:'margem_verba',label:'Margem c/ verba',num:true,fmt:v=>v==null?'—':dec(v,1)+'%'},
    {key:'pe',label:'% est.',num:true,fmt:v=>dec(v,1)+'%'},{key:'pv',label:'% venda',num:true,fmt:v=>dec(v,1)+'%'},
    {key:'idx',label:'Índice',num:true,fmt:v=>dec(v,2)},{key:'cl',label:'Classe',badge:true}];
  const CLS={alta_performance:'Alta performance',equilibrado:'Equilibrado',estoque_alto:'Estoque alto',ruptura:'Ruptura',critico_sem_giro:'Crítico s/ giro'};
  const Fabc=(S.cli.curva&&S.cli.curva.length)?F.filter(r=>S.cli.curva.includes(r.curva_abc)):F;   // filtro Curva = ABC do fornecedor
  const Ff=S.cli.fornClasse?Fabc.filter(r=>r.cl===S.cli.fornClasse):Fabc;
  const sk=S.sort['fornecedores']||{key:'valor',dir:-1};
  const rows=[...Ff].sort((a,b)=>{let x=a[sk.key],y=b[sk.key];if(typeof x==='string')return sk.dir*x.localeCompare(y);return sk.dir*((x||0)-(y||0));});
  const headr=cols.map(c=>`<th class="${c.num?'num':''}" data-k="${c.key}">${c.label}${tip('fornecedores',c.label)}</th>`).join('');
  const body=rows.slice(0,300).map(r=>`<tr data-forn="${r.codfornec}" style="cursor:pointer" title="Ver 360° do fornecedor (venda mês a mês, ciclo, pedidos)">`+cols.map(c=>{let v=r[c.key];if(c.badge)return`<td>${badge(v)}</td>`;if(c.fmt)v=c.fmt(v);return`<td class="${c.num?'num':''}">${v==null?'—':v}</td>`;}).join('')+'</tr>').join('');
  // quanto da verba do período é campanha (não é redução de custo) — o diretor pediu p/ incluir
  // tudo por ora, então o aviso mostra o tamanho do que ainda falta refinar em vez de escondê-lo
  const vbCamp=Ff.reduce((s,r)=>s+(r.verba_campanha||0),0);
  // o crescimento é do fornecedor INTEIRO (as duas janelas completas), então não responde aos
  // filtros de recorte — mesma política do card de Orçamento, que também avisa em vez de mentir.
  const _rot={curva:'curva',xyz:'XYZ',depto:'depto',busca:'busca'};
  const crIgnora=Object.keys(_rot).filter(k=>S.cli[k]&&S.cli[k].length).map(k=>_rot[k]);
  $('#v-fornecedores').innerHTML=head('Desempenho por fornecedor — giro × estoque','fornecedores')+
    `<div class="fb-group" style="margin:0 0 6px"><label>Filtrar classe</label>
       <select id="forn-cl" class="fb-control" style="width:auto">
         <option value="">Todas</option>
         ${Object.keys(CLS).map(k=>`<option value="${k}" ${S.cli.fornClasse===k?'selected':''}>${CLS[k]}</option>`).join('')}
       </select></div>
     <div class="count-line">💡 <b>Clique numa linha</b> para ver o 360° do fornecedor (venda mês a mês vs ano anterior, ciclo × lead, pedidos em aberto). Índice = % na <b>venda (R$)</b> ÷ % no <b>estoque (R$)</b> (&gt;1 = vende mais do que pesa em estoque). <b>Ruptura</b> = vende mas cobertura &lt; ${lead}d (quase sem estoque) — não é performance.</div>
     <div class="count-line">${exLoading?'<b>Carregando ciclo de compras, verba e crescimento…</b> essas colunas aparecem em instantes. ':(_fx.erro?'<b style="color:'+C.red+'">⚠ Não foi possível carregar ciclo de compras, verba e crescimento</b> — as colunas ficam vazias em vez de mostrar número desatualizado. Recarregue a página. ':'')}<b>Compras</b>, <b>Venda</b>, <b>Lucro</b> e <b>Verba</b> seguem o período do seletor <b>Venda</b> do topo (hoje: ${({mes:'mês atual',['90d']:'últimos 90d',['6m']:'6 meses',['12m']:'12 meses'})[S.vperiodo]||'período'}) — por isso lucro e verba somam na mesma régua. O <b>Ciclo</b> é sempre apurado em <b>12 meses</b>: é comportamento do fornecedor, não recorte de tela. O <b>Cresc. AA</b> compara as duas janelas <b>completas</b> do fornecedor (todo produto vendido, inclusive o que saiu de linha) — sem isso o ano anterior sairia truncado e o crescimento inflado.${crIgnora.length?` <b>⚠ Filtro ativo (${esc(crIgnora.join(', '))}): o Cresc. AA continua sendo o do fornecedor inteiro</b>, não do recorte.`:''}${vbCamp>0?` ⚠ ${money(vbCamp)} da verba do período é <b>“Premiações e campanhas”</b> (não é redução de custo) e <b>está incluída</b> no “Lucro c/ verba” — refinamento pendente.`:''}</div>
     <!-- freeze2: Cód + Fornecedor ficam presos ao rolar lateralmente. Virou necessário quando a
          aba ganhou as 6 colunas de ciclo/verba: sem isso o nome sai da tela antes do "Lucro c/
          verba" e a leitura da linha se perde. Mesmo mecanismo já usado na aba Produtos. -->
     <div class="tbl-wrap freeze2"><table><thead><tr>${headr}</tr></thead><tbody>${body}</tbody></table></div>`;
  $('#v-fornecedores').querySelectorAll('thead th').forEach(th=>th.onclick=()=>{const k=th.dataset.k,cur=S.sort['fornecedores']||{};S.sort['fornecedores']={key:k,dir:cur.key===k?-cur.dir:-1};render();});
  $('#v-fornecedores').querySelectorAll('tbody tr[data-forn]').forEach(tr=>tr.onclick=()=>openFornecedor(tr.dataset.forn));
  const fc=$('#forn-cl'); if(fc) fc.onchange=e=>{S.cli.fornClasse=e.target.value;render();};
}

/* "Sem pedido" = ruptura sem NENHUMA providência. Espelha `core._sem_providencia` — as duas
   implementações existem porque a tela agrega no cliente (filtro sem round-trip) e o export
   agrega no servidor; divergir faz o Excel contradizer a tela.
   ⚠️ Pré-entrada (`qt_transicao>0`) NÃO conta: a mercadoria está no armazém aguardando liberação
   e o Winthor já baixou o pedido ao recebê-la. Era a origem do "4 na aba Ruptura × 3 no Estoque
   zerado" que o diretor achou em 08/2026 — `status_exec` já tratava pré-entrada como estado
   exclusivo, estas duas agregações não. O item segue contando em RUPTURA: falta estoque de fato. */
function semProvidencia(p){ return (p.qtd_ja_pedida||0)<=0 && (p.qt_transicao||0)<=0; }

function renderRupturaComprador(P){
  // agrega métricas de ruptura por uma chave (comprador OU curva ABC de venda)
  function agrupa(keyFn,nomeFn){
    const g={};
    P.forEach(p=>{const kk=keyFn(p); const o=g[kk]=g[kk]||{k:kk,nome:nomeFn(p,kk),n:0,rupt:0,semped:0,perdida:0,repor:0,reporInc:0,diasSum:0,diasN:0};
      o.n++;
      // sugestão de compra = MESMA da aba Abastecimento: valor_sugerido_liq (caixa fechada) de
      // TODO item a comprar (sugestao_cx>0, giro>0, não suspenso), não só os zerados.
      if((p.sugestao_cx||0)>0&&(p.giro_dia||0)>0&&!p.compra_suspensa){ o.repor+=valReporNF(p); o.reporInc+=valReporIncerto(p); }
      if((p.qtdisp||0)<=0&&(p.giro_dia||0)>0){o.rupt++; if(semProvidencia(p))o.semped++;
        o.perdida+=(p.venda_perdida||0);
        if(p.dias_sem_venda!=null){o.diasSum+=p.dias_sem_venda; o.diasN++;}}});
    return Object.values(g).map(o=>({...o,pct:o.n?o.rupt/o.n*100:0,pctSemPed:o.n?o.semped/o.n*100:0,diasrup:o.diasN?Math.round(o.diasSum/o.diasN):0})).filter(o=>o.n>0);
  }
  const ckBase=[{k:'n',label:'Produtos',num:1},{k:'rupt',label:'Em ruptura',num:1},{k:'pct',label:'% Rupt.',num:1},
    {k:'diasrup',label:'Dias rupt. méd',num:1},{k:'semped',label:'Sem pedido',num:1},{k:'pctSemPed',label:'% s/ ped.',num:1},{k:'perdida',label:'Venda perdida',num:1},{k:'repor',label:'Sugestão de compra',num:1}];
  function tabela(rows0,skk,lbl0,nav){
    const sk=S.sort[skk]||{key:'rupt',dir:-1};
    const rows=_sortArr(rows0,sk);
    const ck=[{k:'nome',label:lbl0},...ckBase];
    const T=rows.reduce((s,r)=>({n:s.n+r.n,rupt:s.rupt+r.rupt,semped:s.semped+r.semped,perdida:s.perdida+r.perdida,repor:s.repor+r.repor,diasSum:s.diasSum+r.diasSum,diasN:s.diasN+r.diasN}),{n:0,rupt:0,semped:0,perdida:0,repor:0,diasSum:0,diasN:0});
    const totRow=rows.length?`<tr style="border-top:2px solid var(--border);font-weight:700"><td>TOTAL</td><td class="num">${int(T.n)}</td><td class="num">${int(T.rupt)}</td><td class="num">${T.n?dec(T.rupt/T.n*100,1):'0'}%</td><td class="num">${T.diasN?int(Math.round(T.diasSum/T.diasN))+'d':'—'}</td><td class="num">${int(T.semped)}</td><td class="num">${T.n?dec(T.semped/T.n*100,1):'0'}%</td><td class="num">${money(T.perdida)}</td><td class="num">${money(T.repor)}</td></tr>`:'';
    return `<div class="tbl-wrap"><table><thead><tr>${ck.map(c=>`<th class="${c.num?'num':''}" data-k="${c.k}">${c.label}${tip(S.view,c.label)}${sk.key===c.k?(sk.dir<0?' ↓':' ↑'):''}</th>`).join('')}</tr></thead>
      <tbody>${rows.map(r=>`<tr${nav?` data-curva="${esc(r.k)}" style="cursor:pointer"`:''}><td><span class="prod">${esc(r.nome)}</span></td><td class="num">${int(r.n)}</td><td class="num">${int(r.rupt)}</td><td class="num">${dec(r.pct,1)}%</td><td class="num">${r.diasrup?int(r.diasrup)+'d':'—'}</td><td class="num">${int(r.semped)}</td><td class="num">${dec(r.pctSemPed,1)}%</td><td class="num">${money(r.perdida)}</td><td class="num">${money(r.repor)}</td></tr>`).join('')||'<tr><td colspan="9" class="muted">Sem ruptura 🎉</td></tr>'}${totRow}</tbody></table></div>`;
  }
  const porComp=agrupa(p=>p.codcomprador==null?0:p.codcomprador, p=>p.comprador||'Sem comprador');
  const porCurva=agrupa(p=>p.curva_abc||'C', (p,k)=>'Curva '+k);
  const totR=porComp.reduce((s,r)=>s+r.rupt,0),totSem=porComp.reduce((s,r)=>s+r.semped,0),
    totP=porComp.reduce((s,r)=>s+r.perdida,0),totC=porComp.reduce((s,r)=>s+r.repor,0);
  $('#v-ruptura_comprador').innerHTML=head('Ruptura por comprador','ruptura_comprador')+
    `<div class="kpi-grid" style="grid-template-columns:repeat(4,1fr)">
       ${kpi('Itens em ruptura',int(totR),int(totSem)+' sem pedido',C.red)}
       ${kpi('Venda perdida (ruptura)',money(totP),'acumulada · a preço de venda',C.orange)}
       ${kpi('Sugestão de compra',money(totC),'igual à aba Abastecimento',C.accent)}
       ${kpi('Compradores',int(porComp.length),'',C.accent2)}
     </div>
     <div class="count-line">Ruptura = estoque ≤ 0 e giro > 0. <b>"Dias rupt. méd"</b> = média de dias sem venda dos itens em ruptura (há quanto tempo, em média, estão zerados). "Sem pedido" = ruptura sem NENHUMA providência: sem pedido de compra em aberto <b>e</b> sem mercadoria em pré-entrada (essa já está no armazém, aguardando liberação — conta como ruptura, não como omissão); <b>"% s/ ped."</b> = sem pedido ÷ total de produtos do comprador (base da meta — todo item conta). <b>"Venda perdida"</b> = dias em ruptura (desde a última venda, teto 60d) × giro/dia × <b>preço de venda</b> (realizado 3m) — o que se deixou de vender no período parado. <b>"Sugestão de compra"</b> = mesmo valor da aba <b>Comprar → Abastecimento</b> (caixa fechada × custo de todos os itens a comprar), considerando Lead time + Cobertura alvo.</div>
     <div class="panel" id="rc-comp"><h3>Por comprador</h3>${tabela(porComp,'ruptcomp','Comprador')}</div>
     <div class="panel" id="rc-curva"><h3>Por curva ABC <small class="muted">· quanto da ruptura está em cada curva de venda (A = campeões) · clique p/ ver os itens</small></h3>${tabela(porCurva,'ruptcurva','Curva ABC',true)}</div>`;
  wireSortTbl($('#rc-comp'),'ruptcomp',render);
  wireSortTbl($('#rc-curva'),'ruptcurva',render);
  $('#rc-curva').querySelectorAll('tr[data-curva]').forEach(tr=>tr.onclick=()=>goView('estoque_zero',{curva:tr.dataset.curva}));
}

/* ───────── Meta de ruptura (placar executivo) ─────────
   Aba NOVA e isolada (decisão do diretor 07/2026): Cockpit, Painel gerencial e a aba Ruptura
   ficam intocados — o placar não mexe no que já funciona.
   Meta: "% s/ ped." ≤ metaA/metaB/metaC na respectiva curva (uma meta por curva desde 07/2026).
   "% s/ ped." = itens em ruptura SEM pedido de compra em aberto ÷ TOTAL de produtos do grupo —
   mesma definição da aba Ruptura (core.ruptura_por_comprador), só que quebrada por curva.
   Itens sem giro seguem no denominador de propósito (decisão do diretor: a cobertura tem de
   existir independente do tamanho do catálogo) — terão meta própria no futuro.
   Base ABC FIXADA em 90d: a curva é atribuída no servidor sobre o conjunto inteiro
   (core._aplicar_curva), então só a UNIDADE e o PERÍODO DE VENDA a redefinem — os filtros de
   tela apenas recortam a lista. Fixando o período, a meta para de andar quando alguém mexe no
   seletor "Venda" do topo. Pelo mesmo motivo o placar ignora os filtros de tela. */
const META_PER='90d';
const _metaBase={key:null,produtos:null};

function metaBaseQS(){ const p=new URLSearchParams(serverQS()); p.set('venda_periodo',META_PER); return p.toString(); }

async function metaBaseProdutos(){
  if(S.vperiodo===META_PER) return S.produtosAll;          // seletor já está em 90d → evita 2ª chamada
  const key=metaBaseQS();
  if(_metaBase.key===key&&_metaBase.produtos) return _metaBase.produtos;
  const snap=await getJSON('/estoque/api/snapshot?'+key);
  _metaBase.key=key; _metaBase.produtos=snap.produtos||[];
  return _metaBase.produtos;
}

// agrega o % s/ pedido por comprador, uma coluna por curva (A, B e C)
// B e C eram um bloco só (meta 5%). Separados a pedido do diretor 07/2026: "B+C junto não
// funciona bem" — C é cauda longa e comporta uma meta mais frouxa que B.
// ⚠️ Separar AFROUXA o placar sem ninguém mexer na operação: os itens C que estouravam o teto
// de 5% do bloco passam a ter orçamento próprio de 10%. Comparar antes×depois uma vez.
function metaAgrega(P){
  const g={};
  P.forEach(p=>{
    const cod=p.codcomprador==null?0:p.codcomprador;
    const o=g[cod]=g[cod]||{cod,nome:p.comprador||'Sem comprador',nA:0,sA:0,nB:0,sB:0,nC:0,sC:0};
    // mesma régua da aba Ruptura, pré-entrada inclusive (semProvidencia): o placar não pode
    // punir o comprador por mercadoria que já está no armazém aguardando liberação
    const semPed=(p.qtdisp||0)<=0&&(p.giro_dia||0)>0&&semProvidencia(p);
    const cv=(p.curva_abc==='A'||p.curva_abc==='B')?p.curva_abc:'C';   // sem curva → C (regra da aba Ruptura)
    o['n'+cv]++; if(semPed) o['s'+cv]++;
  });
  return Object.values(g).map(o=>({...o,
    pctA:o.nA?o.sA/o.nA*100:null, pctB:o.nB?o.sB/o.nB*100:null, pctC:o.nC?o.sC/o.nC*100:null
  })).filter(o=>o.nA+o.nB+o.nC>0);
}

async function renderMetaRuptura(){
  const _p=(v,d)=>v==null||!isFinite(+v)?d:+v;              // meta 0 é válida (tolerância zero)
  const el=$('#v-meta_ruptura'), mA=_p(S.params.metaA,2), mB=_p(S.params.metaB,5), mC=_p(S.params.metaC,10);
  // só mostra "carregando" em cache miss — senão a tabela pisca a cada clique de ordenação
  const _cache=(S.vperiodo===META_PER)?S.produtosAll:(_metaBase.key===metaBaseQS()?_metaBase.produtos:null);
  if(!_cache) el.innerHTML='<div class="count-line">Carregando a base de 90 dias…</div>';
  let P;
  try{ P=await metaBaseProdutos(); }
  catch(e){ el.innerHTML=`<div class="count-line">Falha ao carregar a base da meta: ${esc(e.message)}</div>`; return; }
  if(S.view!=='meta_ruptura') return;                      // trocou de aba durante o fetch

  // fora da meta = QUALQUER uma das três curvas estourar o seu limite (regra de antes, com 3)
  const fora=r=>(r.pctA!=null&&r.pctA>mA)||(r.pctB!=null&&r.pctB>mB)||(r.pctC!=null&&r.pctC>mC);
  const rows0=metaAgrega(P).map(r=>({...r,st:fora(r)?1:0}));   // `st` numérico p/ a coluna Status ordenar
  const T=rows0.reduce((s,r)=>({nA:s.nA+r.nA,sA:s.sA+r.sA,nB:s.nB+r.nB,sB:s.sB+r.sB,
                                nC:s.nC+r.nC,sC:s.sC+r.sC}),{nA:0,sA:0,nB:0,sB:0,nC:0,sC:0});
  const tA=T.nA?T.sA/T.nA*100:null, tB=T.nB?T.sB/T.nB*100:null, tC=T.nC?T.sC/T.nC*100:null;
  const foraEmpresa=(tA!=null&&tA>mA)||(tB!=null&&tB>mB)||(tC!=null&&tC>mC);
  const nFora=rows0.filter(fora).length;

  // célula: % + absoluto (2% sobre base pequena vira 1 item — o absoluto evita leitura errada)
  const cel=(pctv,semp,n,meta)=>{
    if(!n) return '<td class="num muted">—</td>';
    const ok=pctv<=meta, c=ok?C.green:C.red;
    return `<td class="num"><b style="color:${c}">${dec(pctv,1)}%</b> <span class="muted">(${int(semp)}/${int(n)})</span> ${ok?'✓':'⚠'}</td>`;
  };
  const selo=(ok,txt)=>`<span class="badge" style="background:${ok?C.green:C.red}22;color:${ok?C.green:C.red}">${ok?'✓':'⚠'} ${txt}</span>`;

  // `tipk` = chave estável no catálogo TIPS (o label carrega a meta e muda com o parâmetro)
  const cols=[{k:'nome',label:'Comprador'},{k:'pctA',label:`Curva A (meta ${dec(mA,1)}%)`,tipk:'Curva A',num:1},
    {k:'pctB',label:`Curva B (meta ${dec(mB,1)}%)`,tipk:'Curva B',num:1},
    {k:'pctC',label:`Curva C (meta ${dec(mC,1)}%)`,tipk:'Curva C',num:1},{k:'st',label:'Status',tipk:'Status'}];
  const sk=S.sort['metarupt']||{key:'pctC',dir:-1};
  const headr=cols.map(c=>`<th class="${c.num?'num':''}" data-k="${c.k}">${c.label}${c.tipk?tip('meta_ruptura',c.tipk):''}${sk.key===c.k?(sk.dir<0?' ↓':' ↑'):''}</th>`).join('');
  const rows=_sortArr(rows0,sk);
  const body=rows.map(r=>`<tr>
      <td><span class="prod">${esc(r.nome)}</span></td>
      ${cel(r.pctA,r.sA,r.nA,mA)}${cel(r.pctB,r.sB,r.nB,mB)}${cel(r.pctC,r.sC,r.nC,mC)}
      <td>${selo(!fora(r),fora(r)?'Acima da meta':'Dentro da meta')}</td></tr>`).join('')
    ||'<tr><td colspan="5" class="muted">Sem produtos no escopo.</td></tr>';
  const totRow=rows.length?`<tr style="border-top:2px solid var(--border);font-weight:700">
      <td>EMPRESA</td>${cel(tA,T.sA,T.nA,mA)}${cel(tB,T.sB,T.nB,mB)}${cel(tC,T.sC,T.nC,mC)}
      <td>${selo(!foraEmpresa,foraEmpresa?'Acima da meta':'Dentro da meta')}</td></tr>`:'';

  el.innerHTML=`<h2 class="section"><span>Meta de ruptura — % sem pedido${tip('meta_ruptura','_title')}</span></h2>
    <div class="kpi-grid" style="grid-template-columns:repeat(4,1fr)">
      ${kpi('Compradores acima da meta',int(nFora)+' de '+int(rows0.length),'basta uma das curvas estourar',nFora?C.red:C.green)}
      ${kpi(`Empresa · curva A (meta ${dec(mA,1)}%)`,tA!=null?dec(tA,1)+'%':'—',int(T.sA)+' de '+int(T.nA)+' itens',tA!=null&&tA>mA?C.red:C.green)}
      ${kpi(`Empresa · curva B (meta ${dec(mB,1)}%)`,tB!=null?dec(tB,1)+'%':'—',int(T.sB)+' de '+int(T.nB)+' itens',tB!=null&&tB>mB?C.red:C.green)}
      ${kpi(`Empresa · curva C (meta ${dec(mC,1)}%)`,tC!=null?dec(tC,1)+'%':'—',int(T.sC)+' de '+int(T.nC)+' itens',tC!=null&&tC>mC?C.red:C.green)}
    </div>
    <div class="count-line"><b>% s/ ped.</b> = itens zerados (estoque ≤ 0 com giro) <b>ainda sem pedido de compra em aberto</b> ÷ total de produtos do comprador naquela curva. Entre parênteses, o absoluto. Limites editáveis em <b>⚙ Parâmetros</b>.</div>
    <div class="count-line">Escopo fixo: <b>${esc(S.unidadeNome||'unidade atual')}</b> · curva ABC apurada sobre os <b>últimos 90 dias</b> de venda. O placar <b>não responde aos filtros do topo</b> de propósito — meta que muda de valor conforme o filtro não é meta. Para investigar item a item, use <b>Estoque → Ruptura</b>.</div>
    <div class="panel" id="mr-tab"><div class="tbl-wrap"><table>
      <thead><tr>${headr}</tr></thead>
      <tbody>${body}${totRow}</tbody></table></div></div>`;
  wireSortTbl($('#mr-tab'),'metarupt',renderMetaRuptura);
}

function renderProdutos(P){
  // colunas em caixa (mantém unidade e ACRESCENTA cx) — fator un/cx de cada item
  P.forEach(p=>{const cx=p.caixa||1; p._giroCx=cx>1?Math.round((p.giro_mes||0)/cx):(p.giro_mes||0); p._dispCx=cx>1?Math.round((p.qtdisp||0)/cx):(p.qtdisp||0);});
  const cols=[colCod,colProd,colForn,{key:'curva_abc',label:'ABC',badge:true},{key:'xyz',label:'XYZ',badge:true},
    {key:'qtdisp',label:'Disp.',num:true,fmt:int},{key:'_dispCx',label:'Disp. cx',num:true,fmt:v=>v==null?'—':int(v)},
    {key:'qtbloq',label:'Avaria',num:true,fmt:v=>v?int(v):'—'},
    {key:'qtd_ja_pedida',label:'Já ped.',num:true,fmt:v=>v>0?int(v):'—'},
    colGiroSpark,{key:'_giroCx',label:'Giro cx',num:true,fmt:v=>v==null?'—':int(v)},
    // Cob. = cobertura_dias oficial (igual às outras abas): item em ruptura mostra "0d" (não "∞"),
    // batendo com o filtro local de cobertura ≤ X dias abaixo.
    {key:'cobertura_dias',label:'Cob.',num:true,fmt:cobDiasFmt},
    {key:'dias_sem_venda',label:'Dias s/v',num:true,fmt:v=>v==null?'—':int(v)},
    {key:'venda',label:'Venda',num:true,fmt:money},colCresc,{key:'lucro',label:'Lucro',num:true,fmt:money},
    {key:'margem',label:'Margem',num:true,fmt:v=>v==null?'—':dec(v,1)+'%'},
    {key:'valor',label:'Estoque R$',num:true,fmt:money},{key:'status_abast',label:'Abast.',badge:true}];
  // filtros LOCAIS desta aba (não afetam as outras abas)
  const abn=S.cli.abast||[], mgn=S.cli.margem||[];
  const cobMax=S.cli.cobMax, semPed=!!S.cli.semPed;
  let rows=P;
  if(abn.length) rows=rows.filter(p=>abn.includes(p.status_abast));
  if(mgn.length) rows=rows.filter(p=>mgn.includes(margemBucket(p)));
  // cobertura ≤ X dias: usa cobertura_dias oficial → inclui ruptura (0d) e exclui sem-giro (9999)
  if(cobMax!==''&&cobMax!=null&&!isNaN(+cobMax)) rows=rows.filter(p=>p.cobertura_dias!=null&&p.cobertura_dias<=+cobMax);
  if(semPed) rows=rows.filter(p=>(p.qtd_ja_pedida||0)<=0);
  const abastCtl=`<div class="fb-group" style="margin:0"><label>Abastecimento</label>
      <details class="ms" id="pr-abast"><summary class="fb-control">${abastLabel(abn)}</summary>
        <div class="ms-menu">${Object.entries(ABAST_LABELS).map(([v,l])=>`<label><input type="checkbox" value="${v}" ${abn.includes(v)?'checked':''}>${l}</label>`).join('')}</div>
      </details></div>`;
  const margemCtl=`<div class="fb-group" style="margin:0"><label>Margem</label>
      <details class="ms" id="pr-margem"><summary class="fb-control">${margemLabel(mgn)}</summary>
        <div class="ms-menu">${Object.entries(MARGEM_LABELS).map(([v,l])=>`<label><input type="checkbox" value="${v}" ${mgn.includes(v)?'checked':''}>${l}</label>`).join('')}</div>
      </details></div>`;
  const cobCtl=`<div class="fb-group" style="margin:0"><label>Cobertura ≤ (dias)</label>
      <input type="number" id="pr-cobmax" class="fb-control" min="0" placeholder="todos" value="${cobMax!=null?cobMax:''}" style="width:110px"></div>`;
  const sempedCtl=`<div class="fb-group" style="margin:0"><label>Pedido</label>
      <label style="display:flex;align-items:center;gap:7px;height:32px;cursor:pointer;color:var(--text)"><input type="checkbox" id="pr-semped" ${semPed?'checked':''}> Só sem pedido</label></div>`;
  $('#v-produtos').innerHTML=head('Explorador de produtos','produtos')+`<div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:8px">${abastCtl}${margemCtl}${cobCtl}${sempedCtl}</div>`+renderTable(rows,cols,'produtos');
  const d=$('#pr-abast');
  if(d) d.addEventListener('change',()=>{ S.cli.abast=[...d.querySelectorAll('input[type=checkbox]:checked')].map(c=>c.value); render(); });
  const dm=$('#pr-margem');
  if(dm) dm.addEventListener('change',()=>{ S.cli.margem=[...dm.querySelectorAll('input[type=checkbox]:checked')].map(c=>c.value); render(); });
  const ci=$('#pr-cobmax');
  if(ci) ci.onchange=()=>{ const v=ci.value.trim(); S.cli.cobMax = v===''?'':Math.max(0,+v||0); render(); };
  const sp=$('#pr-semped');
  if(sp) sp.onchange=()=>{ S.cli.semPed=sp.checked; render(); };
}

const STAT_LUCRO={alta:['Alta entrega',C.green],boa:['Boa entrega',C.accent],baixa:['Entrega baixa',C.dim],negativo:['Lucro negativo',C.red]};
async function renderDesempenho(){
  const el=$('#v-desempenho');
  el.innerHTML=`<div class="loader"><div class="spinner"></div>Carregando desempenho comercial…</div>`;
  let j; try{ j=await getJSON('/estoque/api/desempenho?venda_periodo='+encodeURIComponent(S.vperiodo)); }
  catch(e){ el.innerHTML=`<div class="empty">Falha ao carregar desempenho: ${esc(e.message)}</div>`; return; }
  let rows=j.compradores||[];
  // filtro de comprador do topo
  if(S.cli.comprador) rows=rows.filter(p=>String(p.codcomprador)===S.cli.comprador);
  // resumo (cards) recalculado a partir das linhas visíveis → respeita o filtro de comprador.
  // Sem filtro, bate igual ao resumo do servidor (soma das mesmas linhas).
  const _sum=k=>rows.reduce((a,p)=>a+(+p[k]||0),0);
  const _tv=_sum('venda_liquida'), _tl=_sum('lucro_bruto');
  const r={ venda_liquida:_tv, lucro_bruto:_tl, margem:_tv?(_tl/_tv*100):null,
            clientes_pos:_sum('clientes_pos'), devolucao:_sum('devolucao') };
  const perLbl=({mes:'mês atual',['90d']:'últimos 90d',['6m']:'6 meses',['12m']:'12 meses'})[S.vperiodo]||'período';
  const ck=[{k:'ranking',label:'#',num:1},{k:'comprador',label:'Comprador'},{k:'fornecedores',label:'Fornec.',num:1},
    {k:'clientes_pos',label:'Positivação',num:1},{k:'venda_liquida',label:'Venda líq.',num:1},{k:'lucro_bruto',label:'Lucro bruto',num:1},
    {k:'margem',label:'Margem',num:1},{k:'devolucao',label:'Devolução',num:1},{k:'part_lucro',label:'% Lucro',num:1},
    {k:'yoy',label:'AA Venda',num:1},{k:'yoy_lucro',label:'AA Lucro',num:1},{k:'status_lucro',label:'Status'}];
  const sk=S.sort['desempenho']||{key:'lucro_bruto',dir:-1};
  rows=[...rows].sort((a,b)=>{let x=a[sk.key],y=b[sk.key];if(x==null)x=-Infinity;if(y==null)y=-Infinity;
    if(typeof x==='string'||typeof y==='string')return sk.dir*String(x).localeCompare(String(y));return sk.dir*(x-y);});
  const yoyCell=v=>v==null?'<span class="muted">—</span>':`<span style="color:${v>=0?C.green:C.red}">${v>=0?'+':''}${dec(v,1)}%</span>`;
  const statCell=v=>{const s=STAT_LUCRO[v];return s?`<span class="badge" style="background:${s[1]}22;color:${s[1]}">${s[0]}</span>`:'—';};
  el.innerHTML=`<h2 class="section"><span>Desempenho comercial por comprador${tipT('Venda líquida, lucro, margem, positivação e comparativo ano-a-ano por comprador (dados do RCA).')}</span>${exportBtns('desempenho')}</h2>
    <div class="count-line">Receita/lucro/positivação dos últimos <b>${perLbl}</b> (venda líquida = bruta − devoluções) · ano×ano = vs. mesmo período do ano anterior. Espelha a aba RECEITA COMPRADOR da planilha.</div>
    <div class="kpi-grid" style="grid-template-columns:repeat(5,1fr)">
      ${kpi('Venda líquida',money(r.venda_liquida),'',C.green)}
      ${kpi('Lucro bruto',money(r.lucro_bruto),'',C.accent2)}
      ${kpi('Margem',r.margem!=null?dec(r.margem,1)+'%':'—','',C.purple)}
      ${kpi('Positivação',int(r.clientes_pos),'clientes distintos',C.accent)}
      ${kpi('Devolução',money(r.devolucao),'',C.red)}</div>
    <div class="tbl-wrap"><table><thead><tr>${ck.map(c=>`<th class="${c.num?'num':''}" data-k="${c.k}">${c.label}${tip(S.view,c.label)}${sk.key===c.k?(sk.dir<0?' ↓':' ↑'):''}</th>`).join('')}</tr></thead>
    <tbody>${rows.map(p=>`<tr><td class="num">${int(p.ranking)}</td><td><span class="prod">${esc(p.comprador)}</span></td><td class="num">${int(p.fornecedores)}</td><td class="num">${int(p.clientes_pos)}</td><td class="num">${money(p.venda_liquida)}</td><td class="num">${money(p.lucro_bruto)}</td><td class="num">${p.margem==null?'—':dec(p.margem,1)+'%'}</td><td class="num">${money(p.devolucao)}</td><td class="num">${dec(p.part_lucro||0,1)}%</td><td class="num">${yoyCell(p.yoy)}</td><td class="num">${yoyCell(p.yoy_lucro)}</td><td>${statCell(p.status_lucro)}</td></tr>`).join('')||'<tr><td colspan="12" class="muted">Sem dados de venda no período.</td></tr>'}</tbody></table></div>
    <div class="count-line">${rows.length} compradores · positivação = clientes distintos atendidos no período (DISTINCTCOUNT cliente).</div>`;
  el.querySelectorAll('thead th[data-k]').forEach(th=>th.onclick=()=>{const k=th.dataset.k,cur=S.sort['desempenho']||{};S.sort['desempenho']={key:k,dir:cur.key===k?-cur.dir:-1};render();});
}

/* ── Lead time por fornecedor (pedido → 1ª entrada da NF) ──
   DOIS leads lado a lado (decisão do diretor 07/2026): "todos" inclui os pedidos digitados
   na hora da entrega (lead 0–1d: o pedido real nasceu fora do ERP e foi lançado junto com a
   NF); "real" = mediana só dos ≥2d. O % na hora é o medidor do processo melhorando.
   Base própria (/api/leadtime, 12m de PCPEDIDO × ponte PEDIDO_ENTRADA) — não usa filtered();
   respeita os filtros GLOBAIS de comprador e fornecedor do topo. */
async function renderLeadtime(){
  const el=$('#v-leadtime');
  // ⚠️ O comprador viaja para o SERVIDOR (e entra na chave de cache local). Filtrar só as linhas
  // aqui deixava os cards no número da empresa inteira — e "Lead real" é MEDIANA DE PEDIDOS:
  // recalcular pelas linhas visíveis daria mediana de medianas, um número parecido e errado.
  const ltKey=(S.cli.comprador||'TODOS');
  if(!S.leadtime||S.leadtimeKey!==ltKey){
    el.innerHTML=`<div class="loader"><div class="spinner"></div>Calculando lead time dos fornecedores…</div>`;
    const p=new URLSearchParams(); if(S.cli.comprador) p.set('comprador_cod',S.cli.comprador);
    try{ S.leadtime=await getJSON('/estoque/api/leadtime'+(p.toString()?'?'+p:'')); S.leadtimeKey=ltKey; }
    catch(e){ el.innerHTML=`<div class="empty">Falha ao carregar lead time: ${esc(e.message)}</div>`; return; }
  }
  const J=S.leadtime, R=J.resumo||{};
  const minPed=S.ltMin||5;
  let rows=(J.fornecedores||[]);
  if(S.cli.fornec)    rows=rows.filter(f=>String(f.codfornec)===S.cli.fornec);
  rows=rows.filter(f=>(f.n||0)>=minPed);

  // situação do cadastro: Δ = prazo manual − lead real (cores de STATUS, com rótulo — nunca só cor)
  const SIT={inflado:['Cadastro inflado',C.orange],otimista:['Prazo otimista',C.red],ok:['Cadastro OK',C.green],
             sem_manual:['Sem prazo no cadastro',C.yellow],sem_lead:['Sem lead confiável',C.dim]};
  const sitKey=f=>!f.confiavel?'sem_lead':(f.prazo_manual==null?'sem_manual':(f.delta>=3?'inflado':(f.delta<=-3?'otimista':'ok')));
  const sitCell=k=>{const s=SIT[k];return `<span class="badge" style="background:${s[1]}22;color:${s[1]}">${s[0]}</span>`;};
  const naHoraCell=v=>{const c=v>=60?C.red:(v>=30?C.orange:C.dim);return `<span style="color:${c};font-weight:600">${dec(v,0)}%</span>`;};
  const dCell=v=>v==null?'<span class="muted">—</span>':`<b>${dec(v,v%1?1:0)}d</b>`;
  const deltaCell=v=>v==null?'<span class="muted">—</span>':`<span style="color:${v>=3?C.orange:(v<=-3?C.red:C.green)};font-weight:600">${v>0?'+':''}${dec(v,v%1?1:0)}d</span>`;

  const ck=[{k:'fornecedor',label:'Fornecedor'},{k:'comprador',label:'Comprador'},{k:'n',label:'Pedidos',num:1},
    {k:'pct_na_hora',label:'% na hora',num:1},{k:'lead_todos',label:'Lead todos',num:1},
    {k:'lead_real',label:'Lead real',num:1},{k:'prazo_manual',label:'Prazo manual',num:1},
    {k:'delta',label:'Δ',num:1},{k:'sit',label:'Situação'}];
  const sk=S.sort['leadtime']||{key:'n',dir:-1};
  const sorted=_sortArr(rows.map(f=>({...f,sit:sitKey(f)})),sk);

  el.innerHTML=head('Lead time por fornecedor — pedido → entrada','leadtime')+
    `<div class="count-line">Últimos <b>12 meses</b> de pedidos recebidos · lead = 1ª entrada da NF − emissão do pedido · <b>mediana</b> (imune a extremos) · transferências entre filiais fora do cálculo.</div>
    <div class="kpi-grid" style="grid-template-columns:repeat(5,1fr)">
      ${kpi('Lead real (mediana)',R.mediana_real!=null?dec(R.mediana_real,0)+' dias':'—','pedidos emitidos antes da entrega',C.accent)}
      ${kpi('Digitado na hora',R.pct_na_hora!=null?dec(R.pct_na_hora,0)+'%':'—','pedido lançado junto com a NF',C.orange)}
      ${kpi('Pedidos analisados',int(R.n_pedidos),`+ ${int(R.n_sem_entrada)} abertos/sem entrada`,C.accent2)}
      ${kpi('Fornecedores',`${int(R.n_confiavel)} / ${int(R.n_fornec)}`,'com lead confiável (≥5 pedidos reais)',C.green)}
      ${kpi('Cadastros defasados',int(R.n_defasados),'|Δ| ≥ 3 dias vs. prazo manual',C.red)}</div>
    <div class="panel"><h3><span>Distribuição do lead — nº de pedidos por faixa${tipT('Quantos pedidos caem em cada faixa de lead. A barra 0–1 dia são os pedidos digitados na hora da entrega — a meta é ela encolher com o tempo.')}</span>
      <small class="muted">· <span style="color:${C.orange}">■</span> 0–1d = digitado na hora · <span style="color:${C.accent}">■</span> pedido emitido antes da entrega</small></h3>
      <div class="chart-box sm" style="height:170px"><canvas id="ch-leadtime"></canvas></div></div>
    <div class="count-line" style="display:flex;gap:14px;align-items:center;flex-wrap:wrap">
      <span>Mín. de pedidos no período: <select id="lt-min" class="fb-control" style="width:auto">${[3,5,10,20].map(v=>`<option value="${v}" ${v===minPed?'selected':''}>${v}</option>`).join('')}</select></span>
      <span class="muted">Use os filtros de <b>Comprador</b> e <b>Fornecedor</b> do topo para recortar a tabela.</span></div>
    <div class="tbl-wrap"><table><thead><tr>${sortTh(ck,sk)}</tr></thead>
    <tbody>${sorted.map(f=>{const open=S.ltOpen.has(f.codfornec);
      return `<tr class="lt-row" data-cod="${f.codfornec}" style="cursor:pointer${open?';background:var(--surface3)':''}">
      <td><span class="muted" style="display:inline-block;width:1em">${open?'▾':'▸'}</span><span class="prod" title="${esc(f.fornecedor)}">${esc(f.fornecedor)}</span> <small class="muted">· ${f.codfornec}</small></td>
      <td>${esc((f.comprador||'—'))}</td>
      <td class="num">${int(f.n)}</td>
      <td class="num">${naHoraCell(f.pct_na_hora)}</td>
      <td class="num">${dCell(f.lead_todos)}</td>
      <td class="num">${dCell(f.lead_real)}</td>
      <td class="num">${f.prazo_manual==null?'<span class="muted">—</span>':dec(f.prazo_manual,0)+'d'}</td>
      <td class="num">${deltaCell(f.delta)}</td>
      <td>${sitCell(f.sit)}</td></tr>`+(open?ltDetRow(f.codfornec):'');}).join('')
      ||`<tr><td colspan="9" class="muted">Nenhum fornecedor com ≥ ${minPed} pedidos recebidos no período — reduza o mínimo acima (ou confira a ponte PEDIDO_ENTRADA no dataset).</td></tr>`}</tbody></table></div>
    <div class="count-line">${sorted.length} fornecedores · clique na linha para <b>auditar</b> os pedidos que compõem o número · "Lead todos" = <b>média</b> com os digitados na hora; "Lead real" = <b>mediana</b> dos emitidos antes (≥2d) · <b style="color:${C.orange}">Δ positivo</b> = prazo manual acima do real (capital parado) · <b style="color:${C.red}">Δ negativo</b> = prazo otimista (risco de ruptura).</div>`;

  // histograma: série única de magnitude → 1 matiz (accent); a faixa 0–1d usa cor de STATUS
  // (laranja) porque é outra coisa — digitação na entrega — e está nomeada no rótulo e no h3.
  const fx=J.faixas||[];
  chart('ch-leadtime',{type:'bar',
    data:{labels:fx.map(f=>f.faixa==='0-1'?'0–1d (na hora)':f.faixa+'d'),
      datasets:[{data:fx.map(f=>f.qtd),backgroundColor:fx.map(f=>f.faixa==='0-1'?C.orange:C.accent),
        borderRadius:4,maxBarThickness:46}]},
    options:{maintainAspectRatio:false,plugins:{legend:{display:false},
      tooltip:{callbacks:{label:c=>` ${int(c.parsed.y)} pedidos`}}},
      scales:{y:{beginAtZero:true,ticks:{precision:0}}}}});

  wireSortTbl(el,'leadtime',render);
  $('#lt-min').onchange=e=>{S.ltMin=parseInt(e.target.value,10)||5;render();};
  // drill: clique na linha abre/fecha a auditoria do fornecedor (fetch preguiçoso, cacheado)
  el.querySelectorAll('tr.lt-row').forEach(tr=>tr.onclick=async()=>{
    const cod=parseInt(tr.dataset.cod,10);
    if(S.ltOpen.has(cod)){ S.ltOpen.delete(cod); render(); return; }
    S.ltOpen.add(cod);
    render();                                  // mostra o loader da linha já aberta
    if(!S.ltDet[cod]){
      try{ S.ltDet[cod]=await getJSON('/estoque/api/leadtime/pedidos?fornec='+cod); }
      catch(e){ S.ltDet[cod]={ok:false,error:e.message}; }
      if(S.view==='leadtime') render();
    }
  });
}

/* linha expandida da auditoria de UM fornecedor (drill do Lead time) */
function ltDetRow(cod){
  const d=S.ltDet[cod];
  const wrap=inner=>`<tr class="lt-det"><td colspan="9" style="background:var(--surface2);padding:14px 18px">${inner}</td></tr>`;
  if(!d) return wrap(`<div class="loader" style="padding:8px"><div class="spinner"></div>Carregando pedidos do fornecedor…</div>`);
  if(d.ok===false) return wrap(`<div class="empty">Falha ao carregar o detalhe: ${esc(d.error||'?')}</div>`);
  const st=d.stats||{}, tris=d.trimestres||[], fx=d.faixas||[], pr=d.promessa||{};
  const chip=(l,v)=>`<span style="margin-right:16px"><span class="muted">${l}</span> <b>${v}</b></span>`;
  const chips=`<div style="margin-bottom:10px">
    ${chip('Lead real (mín–p90–máx):',st.lead_real!=null?`${dec(st.lead_min,0)} – ${dec(st.lead_p90,0)} – ${dec(st.lead_max,0)}d`:'—')}
    ${chip('Comprado 12m:',moneyK(st.valor_12m))}
    ${chip('Abertos:',st.n_abertos?`${int(st.n_abertos)} (${moneyK(st.valor_aberto)})`:'0')}
    ${st.n_negativos?chip('<span style="color:'+C.red+'">NF antes do pedido:</span>',int(st.n_negativos)):''}
  </div>`;
  // barras CSS (série única, sem canvas por linha): trilho surface + preenchimento por valor
  const bar=(v,max,cor)=>`<span style="display:inline-block;width:90px;height:8px;background:var(--surface3);border-radius:4px;vertical-align:middle"><span style="display:block;height:8px;width:${max?Math.round(100*v/max):0}%;background:${cor};border-radius:4px"></span></span>`;
  const fxMax=Math.max(1,...fx.map(f=>f.qtd));
  const blocoFaixas=`<div class="grow"><h4 style="margin:0 0 6px">Distribuição do lead</h4>
    ${fx.map(f=>`<div style="display:flex;gap:8px;align-items:center;margin:3px 0;font-size:.85em">
      <span style="width:88px" class="${f.faixa==='0-1'?'':'muted'}">${f.faixa==='0-1'?'0–1d (na hora)':f.faixa+'d'}</span>
      ${bar(f.qtd,fxMax,f.faixa==='0-1'?C.orange:C.accent)}<span class="num">${int(f.qtd)}</span></div>`).join('')}</div>`;
  const triMax=Math.max(1,...tris.map(t=>t.lead_real||0));
  const blocoTri=`<div class="grow"><h4 style="margin:0 0 6px">Evolução por trimestre <small class="muted">· o processo está melhorando?</small></h4>
    <table style="font-size:.85em"><thead><tr><th>Tri</th><th class="num">Pedidos</th><th class="num">% na hora</th><th class="num">Lead real</th><th></th></tr></thead>
    <tbody>${tris.map(t=>`<tr><td>${esc(t.tri)}</td><td class="num">${int(t.n)}</td>
      <td class="num" style="color:${t.pct_na_hora>=60?C.red:(t.pct_na_hora>=30?C.orange:'inherit')}">${dec(t.pct_na_hora,0)}%</td>
      <td class="num">${t.lead_real==null?'—':dec(t.lead_real,0)+'d'}</td>
      <td>${t.lead_real==null?'':bar(t.lead_real,triMax,C.accent)}</td></tr>`).join('')||'<tr><td colspan="5" class="muted">—</td></tr>'}</tbody></table></div>`;
  const blocoProm=`<div class="grow"><h4 style="margin:0 0 6px">Promessa de entrega <small class="muted">· DTPREVENT real × entrada</small></h4>
    ${pr.n_avaliaveis?`<div style="font-size:.9em">
      <div>Entregou na data prometida: <b style="color:${(pr.pct_no_prazo||0)>=80?C.green:((pr.pct_no_prazo||0)>=50?C.orange:C.red)}">${dec(pr.pct_no_prazo,0)}%</b> <span class="muted">de ${int(pr.n_avaliaveis)} pedidos com promessa</span></div>
      ${pr.atraso_medio!=null?`<div>Atraso médio quando falha: <b style="color:${C.red}">${dec(pr.atraso_medio,1)}d</b></div>`:''}
      ${pr.n_auto?`<div class="muted" style="margin-top:4px">${int(pr.n_auto)} pedidos com previsão automática (emissão+1) ficaram fora.</div>`:''}
    </div>`:`<div class="muted" style="font-size:.9em">Sem pedidos com promessa real de data no período${pr.n_auto?` (${int(pr.n_auto)} só com a previsão automática emissão+1)`:''}.</div>`}</div>`;
  const stBadge=p=>{
    if(p.tipo==='real')     return `<span class="badge" style="background:${C.green}22;color:${C.green}">✓ conta na mediana</span>`;
    if(p.tipo==='na_hora')  return `<span class="badge" style="background:${C.orange}22;color:${C.orange}">digitado na hora</span>`;
    if(p.tipo==='negativo') return `<span class="badge" style="background:${C.red}22;color:${C.red}">NF antes do pedido</span>`;
    return `<span class="badge" style="background:${(p.atrasado?C.red:C.dim)}22;color:${p.atrasado?C.red:C.dim}">aberto há ${int(p.dias_aberto)}d${p.atrasado?' · atrasado':''}</span>`;};
  const peds=(d.pedidos||[]);
  const tbl=`<div style="margin-top:12px"><h4 style="margin:0 0 6px">Pedidos do período <small class="muted">· ${int(peds.length)} pedidos, do mais recente ao mais antigo — a prova do número</small></h4>
    <div class="tbl-wrap" style="max-height:280px;overflow:auto"><table style="font-size:.85em">
    <thead><tr><th>Pedido</th><th>Filial</th><th>Emissão</th><th>Entrada</th><th class="num">Lead</th><th class="num">Valor</th><th>Promessa</th><th>Status</th></tr></thead>
    <tbody>${peds.map(p=>`<tr>
      <td class="num">${p.numped}</td><td>${esc(p.codfilial||'—')}</td>
      <td>${dt(p.emissao)}</td><td>${p.entrada?dt(p.entrada):'<span class="muted">—</span>'}</td>
      <td class="num">${p.lead==null?'<span class="muted">—</span>':`<b>${int(p.lead)}d</b>`}</td>
      <td class="num">${moneyK(p.valor)}</td>
      <td>${p.dtprevent?`${dt(p.dtprevent)}${p.atraso_promessa?` <small style="color:${C.red}">+${int(p.atraso_promessa)}d</small>`:''}`:'<span class="muted">—</span>'}</td>
      <td>${stBadge(p)}</td></tr>`).join('')||'<tr><td colspan="8" class="muted">—</td></tr>'}</tbody></table></div></div>`;
  return wrap(chips+`<div class="row" style="align-items:flex-start;gap:22px">${blocoTri}${blocoFaixas}${blocoProm}</div>`+tbl);
}

/* ── Verbas por fornecedor (rotina 1801: negociado × aplicado × saldo) ──
   Fecha o TRIPÉ do fornecedor: quanto compro (12m) · quanto demora (lead real) · quanto
   devolve em verba (%V/C). Saldo em aberto é POSIÇÃO (qualquer emissão); negociado/aplicado
   do placar são 12m (casam com a compra do %V/C). Base própria (/api/verbas); respeita os
   filtros GLOBAIS de comprador e fornecedor do topo. */
async function renderVerbas(){
  const el=$('#v-verbas');
  // ⚠️ Comprador E fornecedor recortados no SERVIDOR (a chave de cache local carrega os dois):
  // os cards leem J.resumo, o gráfico lê J.meses e o "por conta" lê J.contas — os três são
  // agregados lá. Filtrando só as linhas aqui, eles continuavam no total da empresa ao lado de
  // uma tabela já recortada — dois universos na mesma tela. Foi o que aconteceu com o fornecedor
  // até 08/2026: RAZZO selecionada, tabela com 1 fornecedor e gráfico com todos.
  // ⚠️ Se entrar filtro novo aqui, ele TEM de entrar no vbKey também, senão a 1ª resposta fica
  // cacheada e é servida para os outros recortes.
  const vbKey=(S.cli.comprador||'TODOS')+'|'+(S.cli.fornec||'TODOS');
  if(!S.verbas||S.verbasKey!==vbKey){
    el.innerHTML=`<div class="loader"><div class="spinner"></div>Calculando verbas dos fornecedores…</div>`;
    const p=new URLSearchParams();
    if(S.cli.comprador) p.set('comprador_cod',S.cli.comprador);
    if(S.cli.fornec)    p.set('fornec',S.cli.fornec);
    try{ S.verbas=await getJSON('/estoque/api/verbas'+(p.toString()?'?'+p:'')); S.verbasKey=vbKey; }
    catch(e){ el.innerHTML=`<div class="empty">Falha ao carregar verbas: ${esc(e.message)}</div>`; return; }
  }
  const J=S.verbas, R=J.resumo||{};
  // sem refiltro local: o servidor já devolveu o recorte (dois filtros no mesmo dado = convite
  // a divergir, e o de cá só alcançaria a tabela)
  let rows=(J.fornecedores||[]);
  let grandes=(J.grandes_sem_verba||[]);

  const sitKey=f=>f.saldo>0?((f.idade_saldo||0)>120?'parado':'aberto'):'ok';
  const SIT={parado:['Saldo PARADO',C.red],aberto:['Saldo em aberto',C.orange],ok:['Aplicada',C.green]};
  const sitCell=k=>{const s=SIT[k];return `<span class="badge" style="background:${s[1]}22;color:${s[1]}">${s[0]}</span>`;};
  const pctCell=v=>v==null?'<span class="muted">—</span>':`<b style="color:${v>=10?C.green:(v>=3?'inherit':C.orange)}">${dec(v,1)}%</b>`;
  const saldoCell=f=>f.saldo>0?`<span style="color:${(f.idade_saldo||0)>120?C.red:C.orange};font-weight:600">${money(f.saldo)}</span>`:'<span class="muted">—</span>';

  const ck=[{k:'fornecedor',label:'Fornecedor'},{k:'comprador',label:'Comprador'},{k:'n_verbas',label:'Verbas 12m',num:1},
    {k:'negociado',label:'Negociado',num:1},{k:'aplicado',label:'Aplicado',num:1},{k:'saldo',label:'Saldo aberto',num:1},
    {k:'idade_saldo',label:'Idade',num:1},{k:'compra_12m',label:'Compra 12m',num:1},{k:'pct_vc',label:'% V/C',num:1},
    {k:'lead_real',label:'Lead',num:1},{k:'sit',label:'Situação'}];
  const sk=S.sort['verbas']||{key:'negociado',dir:-1};
  const sorted=_sortArr(rows.map(f=>({...f,sit:sitKey(f)})),sk);

  // declarados ANTES do innerHTML: o aviso de mês parcial é montado dentro do template
  const ms=J.meses||[];
  const parciais=ms.filter(m=>m.parcial);
  const temParcial=parciais.length>0;
  // ⚠️ Mês por EXTENSO no eixo (mesma convenção do resto do app: MES_ABREV / mesLbl12).
  // O eixo saía "26-08" e o diretor leu "26 de agosto" — em pt-BR aquilo é uma DATA, não um
  // mês. A série inteira virava uma sequência de dias sem sentido. Não voltar ao YYYY-MM cru.
  const mesRot=k=>MES_ABREV[+k.slice(5)-1]+'/'+k.slice(2,4);

  el.innerHTML=head('Verbas por fornecedor — negociado × aplicado × saldo','verbas')+
    `<div class="count-line">Rotina <b>1801</b> do Winthor · negociado/aplicado = últimos <b>12 meses</b> · saldo em aberto = posição atual (qualquer emissão) · canceladas e estornos fora · alinhado ao extrato <b>1826</b>.</div>
    <div class="kpi-grid" style="grid-template-columns:repeat(5,1fr)">
      ${kpi('Saldo a aplicar',money(R.saldo_aberto),`${int(R.n_abertas)} verbas em aberto`,C.orange)}
      ${kpi('Idade do saldo',R.idade_mediana!=null?dec(R.idade_mediana,0)+'d':'—',`mediana · mais antiga ${int(R.idade_max)}d`,C.red)}
      ${kpi('Negociado 12m',money(R.negociado_12m),`${int(R.n_verbas_12m)} verbas`,C.accent)}
      ${kpi('Aplicado 12m',money(R.aplicado_12m),'abatimentos efetivados',C.green)}
      ${kpi('Compram sem dar verba',int(R.n_grandes_sem_verba),`compra 12m > ${moneyK(R.compra_min_alerta)}`,C.purple)}</div>
    <div class="row" style="align-items:flex-start">
      <div class="panel grow"><h3><span>Negociado × aplicado por mês <small class="muted">· 12 meses</small>${tipT('Mesma janela dos cards: somar as barras azuis dá exatamente o "Negociado 12m". Barras azuis = verbas emitidas no mês; verdes = valor aplicado no mês. Mês sem barra = mês sem verba (o eixo é calendário, não pula mês). Aplicação abaixo da negociação por vários meses = saldo acumulando.')}</span></h3>
        <div class="chart-box sm" style="height:190px"><canvas id="ch-verbas"></canvas></div>
        ${temParcial?`<div class="count-line" style="margin-top:2px">* <b>mês parcial</b> — ${parciais.map(m=>`${mesRot(m.mes)} (${int(m.dias_cobertos)} de ${int(m.dias_mes)} dias)`).join(' · ')}. A janela são 365 dias corridos: começa no meio do mês e termina hoje. Não compare estas barras com o mês fechado do ERP.</div>`:''}</div>
      <div class="panel" style="flex:0 0 340px;max-width:340px" id="vb-contas"></div>
    </div>
    <div class="panel" id="vb-grandes"></div>
    <div class="tbl-wrap"><table><thead><tr>${sortTh(ck,sk)}</tr></thead>
    <tbody>${sorted.map(f=>{const open=S.vbOpen.has(f.codfornec);
      return `<tr class="vb-row" data-cod="${f.codfornec}" style="cursor:pointer${open?';background:var(--surface3)':''}">
      <td><span class="muted" style="display:inline-block;width:1em">${open?'▾':'▸'}</span><span class="prod" title="${esc(f.fornecedor)}">${esc(f.fornecedor)}</span> <small class="muted">· ${f.codfornec}</small></td>
      <td>${esc(f.comprador||'—')}</td>
      <td class="num">${int(f.n_verbas)}</td>
      <td class="num">${money(f.negociado)}</td>
      <td class="num">${money(f.aplicado)}</td>
      <td class="num">${saldoCell(f)}</td>
      <td class="num">${f.idade_saldo!=null?int(f.idade_saldo)+'d':'<span class="muted">—</span>'}</td>
      <td class="num">${moneyK(f.compra_12m)}</td>
      <td class="num">${pctCell(f.pct_vc)}</td>
      <td class="num">${f.lead_real!=null?dec(f.lead_real,0)+'d':'<span class="muted">—</span>'}</td>
      <td>${sitCell(f.sit)}</td></tr>`+(open?vbDetRow(f.codfornec):'');}).join('')
      ||'<tr><td colspan="11" class="muted">Nenhum fornecedor com verba no recorte (confira PCVERBA/PCAPLICVERBA no dataset).</td></tr>'}</tbody></table></div>
    <div class="count-line">${sorted.length} fornecedores · clique na linha para <b>auditar</b> as verbas uma a uma · <b>% V/C</b> = verba ÷ compra (compare pares: é o argumento de negociação) · o tripé completo: compra × lead × verba.</div>`;

  // gráfico mensal: 2 séries → legenda presente; cores fixas por entidade (negociado/aplicado)
  // A série vem do servidor já na janela dos cards (12m) e com o eixo de CALENDÁRIO contínuo —
  // somar as barras azuis TEM de dar o "Negociado 12m". Mês parcial (o do corte) leva "*".
  chart('ch-verbas',{type:'bar',
    data:{labels:ms.map(m=>mesRot(m.mes)+(m.parcial?'*':'')),
      datasets:[
        {label:'Negociado',data:ms.map(m=>m.negociado),backgroundColor:C.accent,borderRadius:4,maxBarThickness:26},
        {label:'Aplicado',data:ms.map(m=>m.aplicado),backgroundColor:C.green,borderRadius:4,maxBarThickness:26}]},
    options:{maintainAspectRatio:false,plugins:{legend:{display:true,position:'bottom'},
      tooltip:{callbacks:{label:c=>` ${c.dataset.label}: ${money(c.parsed.y)}`,
        footer:it=>{const m=ms[it[0].dataIndex];
          return m&&m.parcial?`mês parcial: ${int(m.dias_cobertos)} de ${int(m.dias_mes)} dias`:'';}}}},
      scales:{y:{beginAtZero:true,ticks:{callback:v=>moneyK(v)}}}}});

  // painel: por conta (a "campanha")
  const cts=J.contas||[], ctMax=Math.max(1,...cts.map(c=>c.negociado));
  // Negociado aqui é 12m (fecha com o card); o SALDO de cada conta continua POSIÇÃO — é
  // estoque, não fluxo, e verba velha em aberto não pode sumir por causa da janela. Por isso
  // a linha de saldo pode ser maior que o valor negociado da própria conta.
  $('#vb-contas').innerHTML=`<h3><span>Por conta <small class="muted">· 12 meses</small>${tipT('Composição do "Negociado 12m" por tipo de verba — a soma das contas dá exatamente o card. Já o "saldo em aberto" de cada linha é POSIÇÃO (qualquer emissão), então pode ser maior que o negociado da janela. 250009 = rebaixa de custo · 250008 = conta corrente (é onde o saldo encalha) · 200013 = premiações e campanhas.')}</span></h3>
    ${cts.map(c=>`<div style="margin:7px 0;font-size:.85em">
      <div style="display:flex;justify-content:space-between"><span>${esc(c.conta)} <small class="muted">· ${int(c.n)}</small></span><b>${moneyK(c.negociado)}</b></div>
      <span style="display:block;height:8px;background:var(--surface3);border-radius:4px;margin-top:3px"><span style="display:block;height:8px;width:${Math.round(100*c.negociado/ctMax)}%;background:${C.accent};border-radius:4px"></span></span>
      ${c.saldo>0?`<small style="color:${C.orange}">saldo em aberto: ${money(c.saldo)}</small>`:''}</div>`).join('')||'<div class="muted">—</div>'}`;

  // painel: grandes compradores sem verba (o argumento de negociação)
  $('#vb-grandes').innerHTML=`<h3><span>⚠️ Compram muito e não dão verba${tipT('Fornecedores com compra 12m relevante e NENHUMA verba registrada. Se pagassem o % dos pares, é dinheiro deixado na mesa — leve esta lista pra negociação.')}</span>
      <small class="muted">· nenhuma verba em 12m nem saldo anterior</small></h3>`+
    (grandes.length?`<div class="tbl-wrap" style="max-height:180px;overflow:auto"><table style="font-size:.85em">
      <thead><tr><th>Fornecedor</th><th>Comprador</th><th class="num">Compra 12m</th><th class="num">Se pagasse 2% · 4%</th></tr></thead>
      <tbody>${grandes.map(g=>`<tr><td><span class="prod">${esc(g.fornecedor)}</span> <small class="muted">· ${g.codfornec}</small></td>
        <td>${esc(g.comprador||'—')}</td>
        <td class="num"><b>${money(g.compra_12m)}</b></td>
        <td class="num" style="color:${C.green}">${moneyK(g.compra_12m*0.02)} · ${moneyK(g.compra_12m*0.04)}</td></tr>`).join('')}</tbody></table></div>`
      :'<div class="muted" style="padding:6px 0">Nenhum no recorte atual.</div>');

  wireSortTbl(el,'verbas',render);
  el.querySelectorAll('tr.vb-row').forEach(tr=>tr.onclick=async()=>{
    const cod=parseInt(tr.dataset.cod,10);
    if(S.vbOpen.has(cod)){ S.vbOpen.delete(cod); render(); return; }
    S.vbOpen.add(cod);
    render();
    if(!S.vbDet[cod]){
      try{ S.vbDet[cod]=await getJSON('/estoque/api/verbas/detalhe?fornec='+cod); }
      catch(e){ S.vbDet[cod]={ok:false,error:e.message}; }
      if(S.view==='verbas') render();
    }
  });
}

/* linha expandida da auditoria de UM fornecedor (drill das Verbas) */
function vbDetRow(cod){
  const d=S.vbDet[cod];
  const wrap=inner=>`<tr class="vb-det"><td colspan="11" style="background:var(--surface2);padding:14px 18px">${inner}</td></tr>`;
  if(!d) return wrap(`<div class="loader" style="padding:8px"><div class="spinner"></div>Carregando verbas do fornecedor…</div>`);
  if(d.ok===false) return wrap(`<div class="empty">Falha ao carregar o detalhe: ${esc(d.error||'?')}</div>`);
  const st=d.stats||{}, vs=(d.verbas||[]);
  const chip=(l,v)=>`<span style="margin-right:16px"><span class="muted">${l}</span> <b>${v}</b></span>`;
  const stBadge=v=>{
    if(v.saldo<=0) return `<span class="badge" style="background:${C.green}22;color:${C.green}">✓ aplicada</span>`;
    const parado=(v.idade_saldo||0)>120;
    return `<span class="badge" style="background:${(parado?C.red:C.orange)}22;color:${parado?C.red:C.orange}">saldo ${money(v.saldo)} · ${int(v.idade_saldo)}d${parado?' · PARADO':''}</span>`;};
  return wrap(
    `<div style="margin-bottom:10px">
      ${chip('Verbas:',int(st.n_verbas))}${chip('Negociado:',money(st.negociado))}
      ${chip('Aplicado:',money(st.aplicado))}${chip('Saldo em aberto:',st.saldo>0?`<span style="color:${C.orange}">${money(st.saldo)}</span>`:'R$ 0')}
      ${chip('Em aberto:',int(st.n_abertas))}
      <div class="muted" style="margin-top:6px;font-size:.82em">⚠️ Janela deste drill: <b>todas as emissões desde 2024</b> — a linha da tabela soma <b>12 meses</b>. Números diferentes aqui e ali é isso, não divergência.</div></div>
    <div class="tbl-wrap" style="max-height:300px;overflow:auto"><table style="font-size:.85em">
    <thead><tr><th>Verba</th><th>Emissão</th><th>Venc.</th><th>Conta</th><th>Campanha (texto da 1801)</th><th>Pgto</th>
      <th class="num">Valor</th><th class="num">Aplicado</th><th class="num">Aplicações</th><th>Status</th></tr></thead>
    <tbody>${vs.map(v=>`<tr>
      <td class="num">${v.numverba}</td><td>${dt(v.emissao)}</td><td>${dt(v.venc)}</td>
      <td><small>${esc(v.conta)}</small></td>
      <td><span class="prod" title="${esc(v.campanha||'')}">${esc((v.campanha||'—').slice(0,38))}</span></td>
      <td>${v.formapgto==='M'?'Mercad.':(v.formapgto==='D'?'Dinheiro':esc(v.formapgto||'—'))}</td>
      <td class="num"><b>${money(v.valor)}</b></td><td class="num">${money(v.aplicado)}</td>
      <td class="num">${v.n_aplic?`${int(v.n_aplic)}× <small class="muted">últ. ${dt(v.ult_aplic)}</small>`:'<span class="muted">—</span>'}</td>
      <td>${stBadge(v)}</td></tr>`).join('')||'<tr><td colspan="10" class="muted">Sem verbas (2024+).</td></tr>'}</tbody></table></div>`);
}

function renderComprasVendas(P){
  const dim=S.cvDim, el=$('#v-comprasvendas');
  const seg=`<div class="seg" id="cv-seg">
    ${['comprador','fornecedor','produto'].map(d=>`<span class="seg-opt ${d===dim?'on':''}" data-d="${d}">${({comprador:'Por comprador',fornecedor:'Por fornecedor',produto:'Por produto'})[d]}</span>`).join('')}</div>`;
  const expv=dim==='comprador'?'compradores':(dim==='fornecedor'?'fornecedores':'comprasvendas');
  let html=`<h2 class="section"><span>Compras × Vendas — ${({comprador:'por comprador',fornecedor:'por fornecedor',produto:'por produto'})[dim]}${tipT('Cruza o capital em estoque (compras) com a venda realizada, por comprador, fornecedor ou produto.')}</span>${exportBtns(expv)}</h2>
    <div class="count-line" style="display:flex;justify-content:space-between;align-items:center">${seg}<span>Estoque = capital em compras · Venda/Lucro/Margem = realizado no período (${({mes:'mês',['90d']:'90d',['6m']:'6m',['12m']:'12m'})[S.vperiodo]})</span></div>`;
  if(dim==='produto'){
    const cols=[colCod,colProd,colForn,{key:'curva_abc',label:'ABC',badge:true},{key:'comprador',label:'Comprador',fmt:v=>esc((v||'').split(' ')[0]||'—')},
      {key:'valor',label:'Estoque R$',num:true,fmt:money},{key:'venda',label:'Venda R$',num:true,fmt:money},
      {key:'lucro',label:'Lucro R$',num:true,fmt:money},{key:'margem',label:'Margem',num:true,fmt:v=>v==null?'—':dec(v,1)+'%'},
      colGiroSpark,{key:'cobertura',label:'Cob.',num:true,fmt:cob}];
    html+=renderTable(P,cols,'comprasvendas');
    el.innerHTML=html;
  } else {
    const base=dim==='fornecedor'?filtered(true):P;   // Opção A: em "por fornecedor" a Curva filtra pela ABC do fornecedor
    const g={};
    base.forEach(p=>{const key=dim==='fornecedor'?p.codfornec:p.codcomprador; if(key==null)return;
      const nome=dim==='fornecedor'?(p.fornecedor||'Forn '+key):(p.comprador||'Sem comprador');
      const o=g[key]=g[key]||{key,nome,n:0,estoque:0,venda:0,lucro:0,giro:0,rupt:0,parado:0};
      o.n++; o.estoque+=(p.valor||0); o.venda+=(p.venda||0); o.lucro+=(p.lucro||0); o.giro+=(p.giro_mes||0);
      // ruptura = critério oficial (estoque<=0 & giro>0); cobertura baixa é atenção, não ruptura
      if((p.qtdisp<=0)&&(p.giro_dia>0))o.rupt++; if(p.status_parado)o.parado+=(p.valor||0);});
    const rows0=Object.values(g).map(o=>({...o,margem:o.venda?o.lucro/o.venda*100:null,turn:o.estoque?o.venda/o.estoque:null,pct_rupt:o.n?o.rupt/o.n*100:0}));
    if(dim==='fornecedor'){const _tv=rows0.reduce((s,o)=>s+(o.venda||0),0)||1;let _ac=0;   // curva ABC do fornecedor por venda
      [...rows0].sort((a,b)=>(b.venda||0)-(a.venda||0)).forEach(o=>{_ac+=(o.venda||0);const _p=_ac/_tv*100;o.curva_abc=_p<=80?'A':(_p<=95?'B':'C');});}
    const ck=[{k:'nome',label:dim==='fornecedor'?'Fornecedor':'Comprador'},...(dim==='fornecedor'?[{k:'curva_abc',label:'ABC',badge:1}]:[]),{k:'n',label:'Itens',num:1},
      {k:'estoque',label:'Estoque R$',num:1},{k:'venda',label:'Venda R$',num:1},{k:'lucro',label:'Lucro R$',num:1},
      {k:'margem',label:'Margem',num:1},{k:'turn',label:'Venda/Estoque',num:1},{k:'rupt',label:'Ruptura',num:1},{k:'pct_rupt',label:'% Rupt.',num:1},{k:'parado',label:'Parado R$',num:1}];
    const skk='cv_'+dim, sk=S.sort[skk]||{key:'venda',dir:-1};
    const rows0f=(dim==='fornecedor'&&S.cli.curva.length)?rows0.filter(o=>S.cli.curva.includes(o.curva_abc)):rows0;
    const rows=[...rows0f].sort((a,b)=>{let x=a[sk.key],y=b[sk.key];if(x==null)x=-Infinity;if(y==null)y=-Infinity;
      if(typeof x==='string'||typeof y==='string')return sk.dir*String(x).localeCompare(String(y));return sk.dir*(x-y);});
    const totE=rows.reduce((s,r)=>s+r.estoque,0),totV=rows.reduce((s,r)=>s+r.venda,0),totL=rows.reduce((s,r)=>s+r.lucro,0);
    html+=`<div class="kpi-grid" style="grid-template-columns:repeat(4,1fr)">
      ${kpi('Estoque (compras)',money(totE),'',C.accent)}${kpi('Venda',money(totV),'',C.green)}
      ${kpi('Lucro',money(totL),'',C.accent2)}${kpi('Margem',totV?dec(totL/totV*100,1)+'%':'—','',C.purple)}</div>`;
    html+=`<div class="tbl-wrap"><table><thead><tr>${ck.map(c=>`<th class="${c.num?'num':''}" data-k="${c.k}">${c.label}${tip(S.view,c.label)}${sk.key===c.k?(sk.dir<0?' ↓':' ↑'):''}</th>`).join('')}</tr></thead><tbody>`+
      rows.map(r=>`<tr><td><span class="prod">${esc(r.nome)}</span></td>${dim==='fornecedor'?`<td>${badge(r.curva_abc)}</td>`:''}<td class="num">${int(r.n)}</td><td class="num">${money(r.estoque)}</td><td class="num">${money(r.venda)}</td><td class="num">${money(r.lucro)}</td><td class="num">${r.margem==null?'—':dec(r.margem,1)+'%'}</td><td class="num">${r.turn==null?'—':dec(r.turn,2)+'×'}</td><td class="num">${int(r.rupt)}</td><td class="num">${dec(r.pct_rupt||0,1)}%</td><td class="num">${money(r.parado)}</td></tr>`).join('')+
      `</tbody></table></div><div class="count-line">${rows.length} ${dim==='fornecedor'?'fornecedores':'compradores'} · "Venda/Estoque" = quantas vezes o capital girou no período.</div>`;
    el.innerHTML=html;
    el.querySelectorAll('thead th[data-k]').forEach(th=>th.onclick=()=>{const k=th.dataset.k,cur=S.sort[skk]||{};S.sort[skk]={key:k,dir:cur.key===k?-cur.dir:-1};render();});
  }
  el.querySelectorAll('#cv-seg .seg-opt').forEach(o=>o.onclick=()=>{S.cvDim=o.dataset.d;render();});
  el.querySelectorAll('tbody tr[data-cod]').forEach(tr=>tr.onclick=()=>openProduto(tr.dataset.cod));
}

/* ───────── Orçamento ───────── */
const PRAZO_BADGE={atrasado:['Atrasado','#ef4444'],chega_7:['Chega ≤7d','#f97316'],no_prazo:['No prazo','#22c55e'],recebido:['Recebido','#22c55e'],sem_prev:['Sem previsão','#64748b']};
const prazoBadge=v=>{const s=PRAZO_BADGE[v];return s?`<span class="badge" style="background:${s[1]}22;color:${s[1]}">${s[0]}</span>`:'—';};
// peso/cubagem do pedido: total + aviso quando há item SEM cadastro no Winthor (senão o número engana).
function pedMedida(v,un,dig,faltam){
  const f=+faltam||0;
  const av=f>0?` <small class="muted" title="${f} item(ns) sem cadastro de ${un==='kg'?'peso':'volume'} no Winthor — total subestimado">⚠</small>`:'';
  if(v==null||v<=0) return f>0?`<span class="muted" title="sem cadastro de ${un==='kg'?'peso':'volume'} nos itens">—</span>`:'—';
  return dec(v,dig)+' '+un+av;
}

/* ── Mês fechado no Orçamento (pergunta do diretor 08/2026) ──
   "quando vira o mês, o orçamento do comprador zera... se ele estourou o mês passado, não
   deveria arrastar o valor para diminuir do mês atual?"

   O número passa a APARECER sempre — era isso que faltava. Descontá-lo da meta é opção
   (`S.orcArrastar`), desligada por default: a meta é 65% da venda dos ÚLTIMOS 30 DIAS, uma
   régua de fluxo (repor o que girou), não um budget anual. Quem estourou porque a venda subiu
   seria punido duas vezes; quem estourou por antecipação tem a mercadoria no estoque e aí o
   desconto é o controle de capital certo — quem sabe qual dos dois é o caso é o diretor, não
   uma regra fixa. Sobra nunca vira crédito (ver core.orcamento_winthor). */
function orcMesAnterior(r){
  if(!r.mes_ant) return '';
  const on=!!r.arrastar;
  const toggle=`<label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:12px">
      <input type="checkbox" id="orc-arrasta" ${on?'checked':''}>
      <span>descontar o estouro da meta deste mês</span></label>`;
  if(r.meta_ant==null)
    return `<div class="panel"><div class="count-line">Mês anterior (${esc(r.mes_ant)}):
      comprado <b>${money(r.comprado_ant)}</b> · <span class="muted">meta do fechamento
      indisponível, então o estouro não pôde ser apurado</span></div></div>`;
  const est=r.saldo_ant<0;
  return `<div class="panel" style="border-color:${est?C.orange:'var(--border)'}">
    <div class="count-line" style="display:flex;gap:14px;align-items:center;flex-wrap:wrap">
      <span><b>Mês anterior (${esc(r.mes_ant)})</b> · meta ${money(r.meta_ant)} ·
        comprado ${money(r.comprado_ant)} ·
        <b style="color:${est?C.red:C.green}">${est?'estourou '+money(-r.saldo_ant)
                                                    :'sobrou '+money(r.saldo_ant)}</b></span>
      ${est?toggle:'<span class="muted">nada a arrastar</span>'}
    </div>
    <div class="count-line muted">A meta é <b>65% da venda dos últimos 30 dias</b> — régua de
      fluxo, não orçamento anual. Estouro por <b>venda maior</b> não deveria ser descontado (puniria
      duas vezes); estouro por <b>antecipação de compra</b> deveria, porque a mercadoria está no
      estoque. Sobra nunca vira crédito.</div></div>`;
}

async function renderOrcamento(useCache){
  const el=$('#v-orcamento');
  const comp=S.compradorNome||'TODOS';
  let o=useCache?S.orcamento:null;
  if(!o){ el.innerHTML=`<div class="loader"><div class="spinner"></div></div>`;
    try{ o=await getJSON('/estoque/api/orcamento?comprador='+encodeURIComponent(comp)
                         +(S.orcArrastar?'&arrastar=1':'')); }
    catch(e){ el.innerHTML=`<div class="empty">Orçamento indisponível: ${e.message}</div>`; return; }
    S.orcamento=o; }
  const r=o.resumo;
  const prog=r.pct_consumido!=null?Math.min(100,r.pct_consumido*100):0;
  const cor=prog>=100?C.red:(prog>=85?C.orange:C.green);
  const abertos=o.abertos||[], manuais=o.manuais||[];
  // ordenação clicável (mantém a ordem do servidor até o 1º clique)
  const skC=S.sort['orc_comp'], pcS=skC?_sortArr(o.por_comprador||[],skC):(o.por_comprador||[]);
  const colsC=[{k:'comprador',label:'Comprador'},{k:'meta',label:'Meta',num:1},{k:'comprado',label:'Comprado',num:1},{k:'aberto',label:'Aberto',num:1},{k:'saldo',label:'Saldo',num:1},{k:'pct_consumido',label:'Consumido',num:1}];
  const skA=S.sort['orc_abertos'], abertosS=skA?_sortArr(abertos,skA):abertos;
  const colsA=[{k:'numped',label:'Nº',num:1},{k:'data_pedido',label:'Data'},{k:'fornecedor',label:'Fornecedor'},{k:'comprador',label:'Comprador'},{k:'valor',label:'Valor',num:1},{k:'valor_aberto',label:'A entregar',num:1},{k:'peso_kg',label:'Peso',num:1},{k:'cubagem_m3',label:'Cubagem',num:1},{k:'dias_para_chegar',label:'Previsão entrega'},{k:'status_prazo',label:'Status'}];
  const skM=S.sort['orc_manuais'], manuaisS=skM?_sortArr(manuais,skM):manuais;
  const colsM=[{k:'data_pedido',label:'Data'},{k:'fornecedor',label:'Fornecedor'},{k:'n_pedido',label:'Pedido'},{k:'valor',label:'Valor',num:1}];
  el.innerHTML=`<h2 class="section"><span>Orçamento de compras — ${esc(comp)} · ${r.mes}${tipT('Meta de compras do mês vs. realizado (pedidos reais do Winthor). Verde = dentro; vermelho = estourou.')}</span>
      <span><button class="btn sm primary" id="btn-pedido">+ Pedido</button></span></h2>
    <div class="kpi-grid">
      ${kpi('Meta do mês',money(r.meta),
            r.arrastar&&r.arrasto_aplicado<0
              ? `${money(r.meta_base)} − ${money(-r.arrasto_aplicado)} do mês passado`
              : (r.meta_auto?'65% da venda líq. 30d':'meta manual'),C.accent)}
      ${kpi('Comprado (Winthor)',money(r.comprado),r.n_pedidos+' pedidos',C.accent2)}
      ${kpi('Saldo',money(r.saldo),'comprometido aberto '+moneyK(r.aberto),r.saldo<0?C.red:C.green)}
      ${kpi('Consumido',r.pct_consumido!=null?pct(r.pct_consumido):'—','',cor)}
    </div>
    ${orcMesAnterior(r)}
    <div class="panel"><div class="bar big"><i style="width:${prog}%;background:${cor}"></i></div>
      <div class="count-line">${prog>=100?'⚠️ Meta estourada':(prog>=85?'Atenção: perto da meta':'Dentro do planejado')} · realizado lido direto do Winthor (pedido real).${
        // sem este aviso o valor cai e ninguém sabe por quê — vira a próxima desconfiança
        r.transf_n?` <b style="color:${C.orange}">${int(r.transf_n)} pedido${r.transf_n>1?'s':''} de transferência entre filiais (${money(r.transf_valor)}) não entra${r.transf_n>1?'m':''} no orçamento</b> — fornecedor é a própria empresa, não é compra.`:''}</div></div>
    ${pcS.length?`<div class="panel" id="orc-comp"><h3><span>Orçamento por comprador${tipT('Meta e realizado de cada comprador no mês.')}</span> <small class="muted">· meta = 65% da venda líq. 30d por comprador</small></h3>
      <div class="tbl-wrap"><table><thead><tr>${sortTh(colsC,skC||{})}</tr></thead>
      <tbody>${pcS.map(c=>`<tr><td><span class="prod">${esc(c.comprador)}</span></td><td class="num">${money(c.meta)}</td><td class="num">${money(c.comprado)}</td><td class="num">${money(c.aberto)}</td><td class="num" style="color:${c.saldo<0?C.red:C.green}">${money(c.saldo)}</td><td class="num">${c.pct_consumido!=null?pct(c.pct_consumido):'—'}</td></tr>`).join('')}</tbody></table></div></div>`:''}
    ${(r.n_atrasados||r.n_chega7)?`<div class="alerts">
      ${r.n_atrasados?alertCard(r.n_atrasados,'Entregas atrasadas',sum2(abertos.filter(p=>p.status_prazo==='atrasado'),'valor_aberto'),C.red,'orcamento',{}):''}
      ${r.n_chega7?alertCard(r.n_chega7,'Chegam em ≤7 dias',sum2(abertos.filter(p=>p.status_prazo==='chega_7'),'valor_aberto'),C.orange,'orcamento',{}):''}
    </div>`:''}
    <div class="panel" id="orc-abertos"><h3><span>Acompanhamento de pedidos em aberto${tipT('Pedidos de compra ainda não recebidos e a previsão de entrega de cada um.')}</span> <small class="muted">· ${abertos.length} em aberto · ${moneyK(r.valor_aberto)} a entregar</small></h3>
      ${abertos.length?`<div class="tbl-wrap"><table><thead><tr>${sortTh(colsA,skA||{})}</tr></thead>
      <tbody>${abertosS.map(pe=>`<tr data-numped="${pe.numped}" style="cursor:pointer" title="ver itens comprados"><td class="num">${pe.numped}</td><td>${dt(pe.data_pedido)}</td><td><span class="prod">${esc(pe.fornecedor||'')}</span></td><td>${esc((pe.comprador||'').split(' ')[0]||'—')}</td><td class="num">${money(pe.valor)}</td><td class="num">${money(pe.valor_aberto)}</td><td class="num">${pedMedida(pe.peso_kg,'kg',1,pe.sem_peso_itens)}</td><td class="num">${pedMedida(pe.cubagem_m3,'m³',2,pe.sem_cubagem_itens)}</td><td>${dt(pe.dt_previsao)}${pe.dias_para_chegar!=null?` <small class="muted">(${pe.dias_para_chegar}d)</small>`:''}</td><td>${prazoBadge(pe.status_prazo)}</td></tr>`).join('')}</tbody></table></div>`:'<div class="empty">Nenhum pedido em aberto.</div>'}
    </div>
    ${manuais.length?`<div class="panel" id="orc-manuais" style="border-color:var(--accent2)"><h3><span>Pedidos da nossa plataforma${tipT('Pedidos lançados aqui, pendentes de envio ao Winthor — não somam no realizado até voltarem da base oficial.')}</span> <small class="muted">· pendentes de envio ao Winthor</small></h3>
      <div class="tbl-wrap"><table><thead><tr>${sortTh(colsM,skM||{})}<th></th></tr></thead>
      <tbody>${manuaisS.map(pe=>`<tr><td>${dt(pe.data_pedido)}</td><td><span class="prod">${esc(pe.fornecedor||'')}</span></td><td>${esc(pe.n_pedido||'')}</td><td class="num" title="${+pe.valor_nf>+pe.valor?`mercadoria ${money(+pe.valor)} + impostos ${money(+pe.valor_nf-+pe.valor)}`:'sem imposto previsto'}">${money(+pe.valor_nf||+pe.valor)}</td><td><a class="btn sm" href="/estoque/api/pedidos/${pe.id}.xlsx" title="Planilha de importação do Winthor (cód · preço · qtd)">⬇ Excel</a> <a class="btn sm" href="/estoque/api/pedidos/${pe.id}.pdf">⬇ PDF</a> <button class="btn sm" data-delped="${pe.id}">✕</button></td></tr>`).join('')}</tbody></table></div>
      <div class="count-line">Não somam no realizado — entram quando voltarem da base oficial (Winthor). <b>⬇ Excel</b> = planilha de importação de pedido do Winthor (v26+): código · preço unitário · quantidade (un).</div></div>`:''}`;
  $('#btn-pedido').onclick=()=>modalPedido(null);
  // refaz o fetch: a meta ajustada é calculada no servidor, junto com a quebra por comprador
  const _arr=$('#orc-arrasta');
  if(_arr) _arr.onchange=e=>{ S.orcArrastar=e.target.checked; savePrefs(); renderOrcamento(); };
  wireSortTbl($('#orc-comp'),'orc_comp',()=>renderOrcamento(true));
  wireSortTbl($('#orc-abertos'),'orc_abertos',()=>renderOrcamento(true));
  wireSortTbl($('#orc-manuais'),'orc_manuais',()=>renderOrcamento(true));
  $('#orc-abertos').querySelectorAll('tr[data-numped]').forEach(tr=>tr.onclick=()=>modalPedidoItens(tr.dataset.numped));
  el.querySelectorAll('[data-delped]').forEach(b=>b.onclick=async()=>{ await postJSON('/estoque/api/pedidos/'+b.dataset.delped,{}, 'DELETE'); toast('Pedido removido'); renderOrcamento(); });
}
function sum2(arr,key){ key=key||'valor'; return arr.reduce((s,p)=>s+(p[key]||0),0); }

const RESUMO_COR={'URGENTE':'red','ALTO':'orange','ATENCAO':'yellow','BAIXO':'accent','OK':'green','RISCO RUPTURA':'red','CRITICO':'purple'};
const resumoBadge=s=>{const cor=C[RESUMO_COR[s]||'dim'];return `<span class="badge" style="background:${cor}22;color:${cor}">${s}</span>`;};
function resumoTabela(titulo,faixas,total,colQt,lblQt,tipTxt){
  return `<div class="panel grow"><h3><span>${titulo}${tipT(tipTxt||'')}</span></h3>
    <div class="tbl-wrap"><table><thead><tr><th>Faixa</th><th class="num">${lblQt}</th><th class="num">Valor estoque${tip('gerencial','Valor estoque')}</th><th class="num">% ${lblQt.toLowerCase()}</th><th>Status${tip('gerencial','Status')}</th></tr></thead>
    <tbody>${faixas.map(f=>`<tr><td>${f.faixa}</td><td class="num">${int(f[colQt])}</td><td class="num">${money(f.valor)}</td><td class="num">${pct(f.perc)}</td><td>${resumoBadge(f.status)}</td></tr>`).join('')}
    <tr style="border-top:2px solid var(--border);font-weight:700"><td>TOTAL</td><td class="num">${int(total[colQt])}</td><td class="num">${money(total.valor)}</td><td class="num">100%</td><td></td></tr></tbody></table></div></div>`;
}
// variante da resumoTabela p/ um SUBGRUPO de faixas: calcula o próprio subtotal
// (itens/valor/%) — usada p/ separar Cobertura de Estoque × Estoque Parado.
function resumoTabelaGrupo(titulo,faixas,colQt,lblQt,tipTxt){
  const tQt=faixas.reduce((s,f)=>s+(f[colQt]||0),0),tVal=faixas.reduce((s,f)=>s+(f.valor||0),0),tPerc=faixas.reduce((s,f)=>s+(f.perc||0),0);
  return `<div class="panel grow"><h3><span>${titulo}${tipT(tipTxt||'')}</span></h3>
    <div class="tbl-wrap"><table><thead><tr><th>Faixa</th><th class="num">${lblQt}</th><th class="num">Valor estoque${tip('gerencial','Valor estoque')}</th><th class="num">% ${lblQt.toLowerCase()}</th><th>Status${tip('gerencial','Status')}</th></tr></thead>
    <tbody>${faixas.map(f=>`<tr><td>${f.faixa}</td><td class="num">${int(f[colQt])}</td><td class="num">${money(f.valor)}</td><td class="num">${pct(f.perc)}</td><td>${resumoBadge(f.status)}</td></tr>`).join('')}
    <tr style="border-top:2px solid var(--border);font-weight:700"><td>TOTAL</td><td class="num">${int(tQt)}</td><td class="num">${money(tVal)}</td><td class="num">${pct(tPerc)}</td><td></td></tr></tbody></table></div></div>`;
}
function resumoCard(titulo,rows,cor,tipTxt,nota){
  return `<div class="panel grow"><h3><span>${titulo}${tipT(tipTxt||'')}</span></h3>
    <table class="mini">${rows.map(([l,v])=>`<tr><td class="muted">${l}</td><td class="num"><b>${v}</b></td></tr>`).join('')}</table>
    ${nota?`<div class="count-line" style="margin-top:6px;color:${C.orange}">${nota}</div>`:''}
    ${cor?`<div class="bar" style="margin-top:8px"><i style="width:0;background:${cor}"></i></div>`:''}</div>`;
}
// Buscar, montar e desenhar viraram três funções separadas (antes era um `injectResumos` que fazia
// as três e se injetava sozinho no DOM). A aba Painel gerencial precisa do DADO antes de pintar
// qualquer coisa — ver renderGerencial.
async function fetchResumos(){
  const comp=S.compradorNome||'TODOS';
  // `comprador` (nome) é p/ o orçamento; os demais filtros vão em filtrosQS() e recortam os produtos
  return getJSON('/estoque/api/resumos?'+filtrosQS()+'&comprador='+encodeURIComponent(comp));
}
// Régua do estoque ideal: lida pelo HTML e pelo gráfico. Derivar nos dois lugares faria os rótulos
// e a rosquinha discordarem se o limiar mudasse no servidor.
function _resumosIdeal(o){
  const ei=o.estoque_ideal||{};
  return {iId:ei.ideal||{}, iRis:ei.em_risco||{}, iSem:ei.sem_giro||{},
          lim:ei.limiar!=null?ei.limiar:45, metaPct:ei.meta_pct||0.90, alerta:!!ei.alerta};
}
function resumosHTML(o){
  const orc=o.orcamento||{}, rup=o.ruptura||{};
  const dentro=(orc.saldo||0)>=0;
  const cardOrc=resumoCard('Orçamento de compras — comprado × meta'+tipT('Meta do mês = 65% da venda líquida dos últimos 30 dias por comprador. “Comprado” = pedidos reais lançados no Winthor no mês.'),[
    ['Meta de compras (65% venda líq. 30d)',money(orc.meta)],
    ['Comprado no mês (Winthor)',money(orc.comprado)],
    ['% da meta',orc.pct_consumido!=null?pct(orc.pct_consumido):'—'],
    ['Saldo da meta',money(orc.saldo)],
    ['Status',dentro?'DENTRO DA META':'FORA DA META'],
    ['Mês competência',orc.mes||'—'],
  ],dentro?C.green:C.red,'',
    // orçamento é o único bloco que NÃO honra curva/XYZ/etc: nem a meta (65% da venda líq. do
    // comprador) nem o comprado (pedido do Winthor) têm quebra por curva. Avisar > exibir % falso.
    (o.orcamento_ignora||[]).length
      ? `⚠ Não filtra por <b>${(o.orcamento_ignora||[]).join('</b>, <b>')}</b> — meta e pedido do Winthor não têm quebra por curva. Valores do comprador inteiro.`
      : '');
  const cardRup=resumoCard('Ruptura de produtos'+tipT('Itens que deveriam ter estoque e não têm: estoque ≤ 0 e giro > 0. “Venda perdida” = o que se deixou de vender no período parado.'),[
    ['Itens em ruptura',int(rup.itens)],
    ['Total de produtos',int(rup.total)],
    ['% ruptura',rup.perc!=null?pct(rup.perc):'—'],
    ['Venda perdida (ruptura)',money(rup.venda_perdida)],
    ['Critério',rup.criterio||'ESTOQUE ≤ 0 E GIRO > 0'],
  ],C.red);
  // Estoque ideal — cobertura mínima + "sem giro" à parte + gatilho <90%
  // Fronteira INCLUSIVA (ajuste 07/2026): cobertura == limiar já é ideal, porque o limiar é o
  // próprio alvo de compra — quem repôs no alvo acertou. Rótulos derivam de `ei.limiar` p/ não
  // voltarem a mentir se o limiar mudar no servidor.
  const {iId,iRis,iSem,lim,metaPct,alerta}=_resumosIdeal(o);
  const corIdeal=alerta?C.red:C.green;
  const idealPanel=`<div class="panel" id="gg-ideal-panel"${alerta?` style="border-color:${C.red}"`:''}>
      <h3><span>Estoque ideal — cobertura mínima${tipT(`% dos SKUs que giram por faixa de cobertura. Ideal = ${lim} dias ou mais; risco = menos de ${lim}. Meta: ≥${dec(metaPct*100,0)}% na faixa ideal. Os dois valores são editáveis em ⚙ Parâmetros (“Estoque ideal”) e só MEDEM — não alteram a sugestão de compra, que usa a Cobertura alvo. “Sem giro” fica à parte e não entra no %.`)}</span>${alerta
        ?`<span class="badge" style="background:${C.red}22;color:${C.red}">⚠ abaixo da meta (≥${dec(metaPct*100,0)}%)</span>`
        :`<span class="badge" style="background:${C.green}22;color:${C.green}">✓ dentro da meta</span>`}</h3>
      <div class="row" style="align-items:center">
        <div style="flex:0 0 190px;position:relative">
          <div class="chart-box sm" style="height:180px"><canvas id="gg-ideal"></canvas></div>
          <div style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;pointer-events:none">
            <div style="font-size:1.5rem;font-weight:700;color:${corIdeal};line-height:1">${iId.pct!=null?pct(iId.pct):'—'}</div>
            <div style="font-size:.58rem;color:var(--text-dim);text-transform:uppercase;letter-spacing:.6px">ideal ≥${int(lim)}d</div>
          </div>
        </div>
        <div class="grow">
          <div class="kpi-grid" style="grid-template-columns:repeat(3,1fr);margin-bottom:8px">
            ${kpi(`Cobertura ideal (≥${int(lim)}d)`,iId.pct!=null?pct(iId.pct):'—',int(iId.n)+' SKUs · '+moneyK(iId.valor),corIdeal)}
            ${kpi(`Em risco (<${int(lim)}d)`,iRis.pct!=null?pct(iRis.pct):'—',int(iRis.n)+' SKUs · '+moneyK(iRis.valor),C.red)}
            ${kpi('Sem giro',iSem.pct!=null?pct(iSem.pct):'—',int(iSem.n)+' SKUs · '+moneyK(iSem.valor),C.dim)}
          </div>
          <div class="count-line">% de SKUs <b>que giram</b> por faixa de cobertura (ARREDONDA.CIMA(estoque ÷ giro diário)). Meta: <b>≥${dec(metaPct*100,0)}%</b> com cobertura <b>ideal (≥${int(lim)} dias)</b>. ${alerta?`<b style="color:${C.red}">⚠ Só ${iId.pct!=null?pct(iId.pct):'—'} atingem a cobertura ideal — abaixo da meta.</b>`:`<b style="color:${C.green}">✓ Meta atingida.</b>`} "Sem giro" (${int(iSem.n)} SKUs) fica à parte e não entra no %.</div>
        </div>
      </div></div>`;
  return `<h2 class="section"><span>Painel gerencial — resumos${tipT('Visão executiva do estoque: orçamento de compras, ruptura, validade, cobertura e lucro por comprador.')}</span></h2>
    <div class="gg-grid">${cardOrc}${cardRup}</div>
    ${idealPanel}
    <div class="gg-grid">
      ${resumoTabela('Itens a vencer por faixa de validade'+tipT('Estoque com validade próxima, agrupado por dias até vencer. Valor = quantidade × custo.'),o.validade.faixas,o.validade.total,'itens','Itens')}
      <div class="gg-col">
        ${resumoTabelaGrupo('Cobertura de estoque'+tipT('Nº de produtos por faixa de dias de cobertura (0 a 90 dias). Cobertura = estoque ÷ giro diário.'),(o.cobertura.faixas||[]).filter(f=>{const n=parseInt(f.faixa,10);return !isNaN(n)&&n<91;}),'produtos','Produtos')}
        ${resumoTabelaGrupo('Estoque parado'+tipT('Produtos com cobertura muito alta ou sem giro (91 dias ou mais) — capital parado.'),(o.cobertura.faixas||[]).filter(f=>{const n=parseInt(f.faixa,10);return isNaN(n)||n>=91;}),'produtos','Produtos')}
      </div>
    </div>
    <div class="count-line">Comprado = pedido real do Winthor (pode divergir do manual da planilha). Cobertura/ruptura no escopo de produtos de revenda; números acompanham o estoque ao vivo.</div>`;
}
// Só depois do innerHTML: o Chart.js precisa do <canvas> já no DOM.
function resumosCharts(o){
  const {iId,iRis,lim}=_resumosIdeal(o);
  chart('gg-ideal',{type:'doughnut',data:{labels:[`Ideal (≥${int(lim)}d)`,`Em risco (<${int(lim)}d)`],datasets:[{data:[iId.n||0,iRis.n||0],backgroundColor:[C.green,C.red],borderColor:getComputedStyle(document.documentElement).getPropertyValue('--surface').trim()||'#111827',borderWidth:2,hoverOffset:4}]},
    options:{cutout:'66%',plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>c.label+': '+int(c.raw)+' SKUs'}}}}});
}

// paleta p/ o donut de lucro por comprador
const PAL_COMP=[C.accent,C.green,C.purple,C.orange,C.accent2,C.yellow,C.red,C.dim];
// Aba "Painel gerencial": os 5 pilares (orçamento, ruptura, validade, cobertura + participação de lucro por comprador).
// ⚠️ A aba abre INTEIRA ou não abre. As duas metades têm origens diferentes: a rosquinha de lucro
// sai do snapshot (já em memória, pinta na hora) e os 5 pilares vêm de /api/resumos (rede). A 1ª
// versão pintava a rosquinha e injetava os resumos quando chegassem — a tela crescia sozinha por
// segundos e, pior, o gerente lia um painel gerencial que AINDA IA MUDAR. Aqui a espera é o
// recurso: um spinner só, e o conteúdo aparece de uma vez.
let _ggSeq=0;
async function renderGerencial(P){
  const el=$('#v-gerencial');
  // Trocar de filtro/aba durante a busca dispara outra render. Sem este selo, a resposta mais
  // LENTA venceria a mais nova e a tela ficaria com o dado do filtro anterior.
  const meu=++_ggSeq;
  el.innerHTML=`<div class="loader"><div class="spinner"></div></div>`;
  let o;
  try{ o=await fetchResumos(); }
  catch(e){ if(meu===_ggSeq) el.innerHTML=`<div class="empty">Resumos gerenciais indisponíveis: ${e.message}</div>`; return; }
  if(meu!==_ggSeq) return;
  el.innerHTML=resumosHTML(o)+
    `<h2 class="section"><span>Participação de lucro por comprador${tipT('Lucro (venda líquida − custo) de cada comprador no período; % = fatia do lucro total.')}</span></h2>
    <div class="row"><div class="panel grow">
      <div class="row" style="align-items:center">
        <div style="width:230px"><div class="chart-box sm" style="height:210px"><canvas id="ch-lucrocomp"></canvas></div></div>
        <div class="grow"><table class="mini" id="gg-lucrotab"></table></div>
      </div>
      <div class="count-line" style="margin-top:6px">Lucro (venda líquida − custo) por comprador no período de venda selecionado; respeita os filtros do topo. A rosquinha mostra só participações positivas.</div>
    </div></div>`;
  resumosCharts(o);
  // lucro por comprador — agrega os produtos filtrados
  const by={};
  P.forEach(p=>{ const nome=p.comprador||'Sem comprador'; by[nome]=(by[nome]||0)+(p.lucro||0); });
  const arr=Object.entries(by).map(([nome,lucro])=>({nome,lucro})).sort((a,b)=>b.lucro-a.lucro);
  const total=arr.reduce((s,x)=>s+x.lucro,0)||1;
  const pos=arr.filter(x=>x.lucro>0);
  const corDe=nome=>{ const i=pos.findIndex(p=>p.nome===nome); return i>=0?PAL_COMP[i%PAL_COMP.length]:C.dim; };
  chart('ch-lucrocomp',{type:'doughnut',data:{labels:pos.map(x=>x.nome),datasets:[{data:pos.map(x=>x.lucro),backgroundColor:pos.map(x=>corDe(x.nome)),borderWidth:0}]},
    options:{plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>pos[c.dataIndex].nome+': '+money(c.raw)+' ('+dec(c.raw/total*100,1)+'%)'}}},cutout:'62%'}});
  $('#gg-lucrotab').innerHTML=arr.map(x=>`<tr><td><span class="dot" style="background:${corDe(x.nome)}"></span> ${esc(x.nome)}</td><td class="num">${money(x.lucro)}</td><td class="num">${dec(x.lucro/total*100,1)}%</td></tr>`).join('')||'<tr><td class="muted">Sem lucro no filtro</td></tr>';
}

const OCUP_BADGE={baixa:['Baixa ocupação','#f97316'],media:['Ocupação média','#eab308'],ok:['OK','#22c55e'],sem_cubagem:['Sem cubagem','#64748b']};
const ocupBadge=v=>{const s=OCUP_BADGE[v];return s?`<span class="badge" style="background:${s[1]}22;color:${s[1]}">${s[0]}</span>`:'—';};

async function renderLogistica(useCache){
  const el=$('#v-logistica');
  let o=useCache?S.logistica:null;
  if(!o){ el.innerHTML=`<div class="loader"><div class="spinner"></div></div>`;
    try{ o=await getJSON('/estoque/api/logistica?'+serverQS()); }
    catch(e){ el.innerHTML=`<div class="empty">Logística indisponível: ${e.message}</div>`; return; }
    S.logistica=o; }
  const r=o.resumo, ps0=o.pedidos||[];
  const skL=S.sort['logistica'], ps=skL?_sortArr(ps0,skL):ps0;
  const colsL=[{k:'numped',label:'Nº',num:1},{k:'fornecedor',label:'Fornecedor'},{k:'comprador',label:'Comprador'},{k:'skus',label:'SKUs',num:1},{k:'caixas',label:'Caixas',num:1},{k:'cubagem_m3',label:'Cubagem m³',num:1},{k:'ocupacao',label:'Ocupação',num:1},{k:'valor_aberto',label:'Valor',num:1},{k:'dt_previsao',label:'Previsão'},{k:'status',label:'Status'}];
  el.innerHTML=`<h2 class="section"><span>Logística de pedidos — cubagem &amp; ocupação</span></h2>
    <div class="kpi-grid">
      ${kpi('Pedidos em aberto',int(r.n_pedidos),moneyK(r.valor_total)+' a entregar',C.accent)}
      ${kpi('Cubagem total',dec(r.cubagem_total,1)+' m³','cap. '+int(r.capacidade_m3)+' m³/veículo',C.accent2)}
      ${kpi('Baixa ocupação',int(r.n_baixa),'avaliar consolidação',C.orange)}
    </div>
    <div class="count-line">Ocupação estimada = cubagem (Σ qtd em aberto × volume unitário) ÷ capacidade do veículo (${int(r.capacidade_m3)} m³). Baixa ocupação = candidato a consolidação de carga.</div>
    <div class="panel">
      ${ps.length?`<div class="tbl-wrap"><table><thead><tr>${sortTh(colsL,skL||{})}</tr></thead>
      <tbody>${ps.map(p=>`<tr><td class="num">${p.numped}</td><td><span class="prod">${esc(p.fornecedor||'')}</span></td><td>${esc((p.comprador||'').split(' ')[0]||'—')}</td><td class="num">${int(p.skus)}</td><td class="num">${int(p.caixas)}</td><td class="num">${dec(p.cubagem_m3,2)}</td><td class="num">${p.ocupacao!=null?pct(p.ocupacao):'—'}</td><td class="num">${money(p.valor_aberto)}</td><td>${dt(p.dt_previsao)}</td><td>${ocupBadge(p.status)}</td></tr>`).join('')}</tbody></table></div>`:'<div class="empty">Nenhum pedido em aberto.</div>'}
    </div>`;
  wireSortTbl($('#v-logistica'),'logistica',()=>renderLogistica(true));
}

/* ───────── planos de ação (inline) ───────── */
function planoCell(tipo,chave,cod,desc,dtval){
  const pl=S.planos[chave];
  if(pl&&(pl.acao||pl.responsavel)) return `<span class="rowact plano-set" data-tipo="${tipo}" data-chave="${esc(chave)}" data-cod="${cod}" data-desc="${esc(desc)}" data-dtval="${dtval||''}">${badge((pl.status||'').toLowerCase().replace(/ /g,'_'),pl.acao||pl.status)}</span>`;
  return `<button class="btn sm rowact plano-set" data-tipo="${tipo}" data-chave="${esc(chave)}" data-cod="${cod}" data-desc="${esc(desc)}" data-dtval="${dtval||''}">+ plano</button>`;
}
function wirePlanoCells(){ document.querySelectorAll('.plano-set').forEach(b=>b.onclick=e=>{e.stopPropagation();modalPlano({cod:+b.dataset.cod,desc:b.dataset.desc,tipo:b.dataset.tipo,chave:b.dataset.chave,dtval:b.dataset.dtval||null});}); }

/* ───────── modais ───────── */
function openModal(html,wide){ $('#modal').innerHTML=html; $('#modal').classList.toggle('wide',!!wide); $('#modal-bg').classList.add('on'); }
function closeModal(){ $('#modal-bg').classList.remove('on'); }
function modalMeta(r){ openModal(`<h3>Meta de compras — ${r.mes}</h3>
  <label>Valor da meta (R$)</label><input type="number" id="m-meta" value="${r.meta||''}" step="1000">
  <div class="m-acts"><button class="btn" id="m-cancel">Cancelar</button><button class="btn primary" id="m-ok">Salvar</button></div>`);
  $('#m-cancel').onclick=closeModal;
  $('#m-ok').onclick=async()=>{ await postJSON('/estoque/api/orcamento/meta',{mes:r.mes,comprador:r.comprador,meta_valor:+$('#m-meta').value||0}); closeModal(); toast('Meta salva'); renderOrcamento(); };
}
// monta um item (snapshot) a partir de um produto
function _prodItem(p,qtd){ return {codprod:p.codprod,descricao:p.descricao,qtdisp:p.qtdisp,cobertura:p.cobertura,
  // qtd do pedido é sempre em UNIDADES INTEIRAS: a sugestão crua pode ser fracionária (ex.: 0,3 un
  // em item sem fator de caixa) — a tela ceila p/ "1 un", mas se guardar 0,3 o item_master faz
  // round→0 e o Excel/PDF do Winthor PULA a linha (qtd<=0). Ceilar aqui alinha pedido↔tela↔export.
  giro_mes:p.giro_mes,qtunitcx:p.qtunitcx,custo_unit:p.custo_unit,
  // alíquotas efetivas do par (fornecedor, produto) — viajam com o item até o banco/PDF
  perc_ipi:p.perc_ipi,perc_st:p.perc_st,trib_fonte:p.trib_fonte,trib_firme:p.trib_firme,
  qtd:(qtd!=null?qtd:Math.ceil(p.sugestao_compra||0))}; }

// Construtor de pedido com itens. opts: produto único (do 360°) | {fornecedor,codfornec,itens} (sugestão) | null (manual)
// drill: itens comprados de um pedido REAL do Winthor (PCITEM)
async function modalPedidoItens(numped){
  openModal(`<h3>Itens comprados — pedido ${esc(numped)}</h3><div id="pi-body"><div class="loader"><div class="spinner"></div></div></div><div class="m-acts"><button class="btn" id="m-cancel">Fechar</button></div>`, true);
  $('#m-cancel').onclick=closeModal;
  try{
    const o=await getJSON('/estoque/api/pedido_itens/'+numped); const it=o.itens||[];
    $('#pi-body').innerHTML=it.length
      ? `<div class="tbl-wrap" style="max-height:360px"><table><thead><tr><th>Cód</th><th>Produto</th><th class="num">Pedida</th><th class="num">Entregue</th><th class="num">A entregar</th></tr></thead>
         <tbody>${it.map(x=>`<tr data-cod="${x.codprod}" style="cursor:pointer"><td class="num">${x.codprod}</td><td><span class="prod">${esc(x.descricao)}</span></td><td class="num">${int(x.qtped)}</td><td class="num">${int(x.qtentregue)}</td><td class="num">${x.aberto>0?int(x.aberto):'—'}</td></tr>`).join('')}</tbody></table></div>
         <div class="count-line">${it.length} itens no pedido. Clique num item p/ abrir o produto.</div>`
      : '<div class="empty">Sem itens neste pedido.</div>';
    $('#pi-body').querySelectorAll('tr[data-cod]').forEach(tr=>tr.onclick=()=>{ closeModal(); openProduto(tr.dataset.cod); });
  }catch(e){ $('#pi-body').innerHTML=`<div class="empty">Erro ao carregar itens: ${e.message}</div>`; }
}
function modalPedido(opts){
  opts=opts||{};
  let itens=[], fornIni='';
  if(opts.itens){ itens=opts.itens.map(x=>({...x})); fornIni=opts.fornecedor||''; }
  else if(opts.codprod){ itens=[_prodItem(opts)]; fornIni=opts.fornecedor||''; }
  const comp=S.compradorNome||'TODOS', hoje=new Date().toISOString().slice(0,10);
  const fdl=(S.fornecedores||[]).map(o=>`<option value="${esc(o.fornecedor)}">`).join('');
  const pdl=(S.produtosAll||[]).map(p=>`<option value="${p.codprod} — ${esc(p.descricao||'')}">`).join('');
  openModal(`<h3>${opts.itens?('Gerar pedido — '+esc(fornIni)):'Novo pedido de compra'}</h3>
    <div class="row">
      <div class="fb-group"><label>Data</label><input type="date" id="pd-data" value="${hoje}" style="width:150px"></div>
      <div class="fb-group grow" style="flex:1 1 240px"><label>Fornecedor</label><input type="text" id="pd-forn" list="pd-forn-dl" autocomplete="off" placeholder="digite e selecione…" value="${esc(fornIni)}"><datalist id="pd-forn-dl">${fdl}</datalist></div>
      <div class="fb-group"><label>Nº pedido</label><input type="text" id="pd-num" style="width:130px"></div>
      <div class="fb-group"><label>Prazo (dias)</label><input type="number" id="pd-prazo" style="width:100px"></div>
      <div class="fb-group"><label>Valor (R$)</label><input type="number" id="pd-valor" step="0.01" style="width:130px"></div>
    </div>
    <div class="d-sec">Itens do pedido</div>
    <div class="row">
      <div class="fb-group grow" style="flex:1 1 320px"><label>Adicionar produto</label><input type="text" id="pd-prodadd" list="pd-prod-dl" autocomplete="off" placeholder="código ou descrição…"><datalist id="pd-prod-dl">${pdl}</datalist></div>
      <div class="fb-group"><label>Qtd (un)</label><input type="number" id="pd-prodqt" min="1" style="width:100px"></div>
      <div class="fb-group"><label>&nbsp;</label><button class="btn" id="pd-additem">＋ Adicionar</button></div>
    </div>
    <div id="pd-itens" style="margin-top:8px"></div>
    <div class="m-acts"><button class="btn" id="m-cancel">Cancelar</button><button class="btn primary" id="m-ok">Lançar</button></div>`, true);
  const total=()=>itens.reduce((s,x)=>s+((+x.qtd||0)*(+x.custo_unit||0)),0);
  // mercadoria + IPI + ST — a régua da NF (a que o Orçamento mede). O campo "Valor" do pedido
  // continua guardando a MERCADORIA; o valor_nf vai separado no payload.
  const linhaNF=x=>(+x.qtd||0)*(+x.custo_unit||0)*(1+((+x.perc_ipi||0)+(+x.perc_st||0))/100);
  const totalNF=()=>itens.reduce((s,x)=>s+linhaNF(x),0);
  const incertoNF=()=>itens.reduce((s,x)=>s+(x.trib_firme===false?linhaNF(x):0),0);
  const rodape=()=>{const t=total(),nf=totalNF(),inc=incertoNF();
    return nf>t+0.005
      ? `Mercadoria: <b>${money(t)}</b> · impostos ${money(nf-t)} · <b>Total da NF previsto ${money(nf)}</b> · ${itens.length} itens${notaIncerteza(nf,inc)}`
      : `Total: <b>${money(t)}</b> · ${itens.length} itens`;};
  // atualiza só o rodapé + o campo Valor (sem reconstruir a tabela → não rouba o foco do input)
  function refreshTotals(){
    const cl=$('#pd-itens .count-line'); if(cl) cl.innerHTML=rodape();
    const v=$('#pd-valor'); if(itens.length) v.value=total().toFixed(2);
  }
  // ───── ordenação das colunas (pedido do diretor 08/2026, "principalmente em ordem alfabética")
  // Ordena o ARRAY, não só a exibição: os inputs se ligam por índice (data-qi/data-cxi), então
  // mexer só na tela faria o que o comprador digitasse cair no item errado.
  // Só no clique do cabeçalho — redesenhar durante a digitação destrói o input e rouba o foco
  // (é a mesma razão pela qual o oninput abaixo NÃO chama draw()).
  // A ordem inicial é preservada de propósito: vem da Abastecimento, por cobertura projetada, ou
  // seja, o mais urgente primeiro. Quem quiser conferir contra uma lista clica em Produto.
  let ord = {col: null, dir: 1};
  const _ordVal = (x, col) => ({
    codprod: +x.codprod || 0,
    descricao: (x.descricao || '').toLowerCase(),
    caixas: x.qtunitcx > 1 ? Math.ceil((+x.qtd || 0) / x.qtunitcx) : -1,
    qtd: +x.qtd || 0,
    custo_unit: +x.custo_unit || 0,
    perc_ipi: +x.perc_ipi || 0,
    nf: linhaNF(x),
  }[col]);
  function ordenar(col){
    ord = (ord.col === col) ? {col, dir: -ord.dir} : {col, dir: 1};
    itens.sort((a, b) => {
      const x = _ordVal(a, col), y = _ordVal(b, col);
      return (typeof x === 'string' ? x.localeCompare(y, 'pt-BR') : x - y) * ord.dir;
    });
    draw();
  }
  const _seta = col => ord.col === col ? (ord.dir < 0 ? ' ↓' : ' ↑') : '';
  const _th = (col, rotulo, cls, tip) =>
    `<th class="${cls || ''}" data-ord="${col}" style="cursor:pointer" title="Ordenar por ${esc(rotulo)}">${rotulo}${tip || ''}${_seta(col)}</th>`;

  function draw(){
    $('#pd-itens').innerHTML = itens.length
      // larguras MEDIDAS no navegador, não estimadas: com 104px a etiqueta "cx" ao lado do campo
      // era cortada em "..", e com 44px o botão de remover também. O produto é a única coluna
      // elástica, então ela absorve a diferença.
      ? `<div class="tbl-wrap" style="max-height:46vh"><table class="pd-tab"><colgroup>
          <col style="width:74px"><col><col style="width:124px"><col style="width:96px">
          <col style="width:104px"><col style="width:92px"><col style="width:124px"><col style="width:56px">
        </colgroup><thead><tr>${
          _th('codprod', 'Cód')}${
          _th('descricao', 'Produto')}${
          _th('caixas', 'Caixas', 'num', tipT('Edite direto em CAIXAS — a quantidade em unidades ao lado recalcula sozinha (caixas × fator un/cx do item). Item sem fator de caixa cadastrado mostra "—": nele só dá para digitar unidade. O pedido é sempre enviado ao Winthor em UNIDADES.'))}${
          _th('qtd', 'Qtd (un)', 'num')}${
          _th('custo_unit', 'Custo', 'num')}${
          _th('perc_ipi', 'IPI %', 'num', tipT('Alíquota que o Winthor deve aplicar na entrada. Vem da tributação de entrada do ERP; quando o item não tem regra fiscal para a UF do fornecedor, é ESTIMATIVA (marcada com ≈) — confira e corrija aqui, o total da NF recalcula.'))}${
          _th('nf', 'Valor NF', 'num')}<th></th></tr></thead><tbody>`+
        // Caixa vem ANTES da unidade de propósito (pedido do diretor 07/2026): é nela que o
        // comprador raciocina e é ela que o fornecedor fatura. A unidade continua visível e
        // editável porque é ela que vai no payload/PDF/planilha — ver o handler de [data-cxi].
        itens.map((x,i)=>`<tr><td class="num">${x.codprod}</td><td><span class="prod">${esc(x.descricao||'')}</span></td>
          <td class="num">${x.qtunitcx>1
            ?`<input type="number" data-cxi="${i}" value="${Math.ceil((+x.qtd||0)/x.qtunitcx)}" min="0" step="1" style="width:66px;text-align:right" title="${int(x.qtunitcx)} un por caixa"> <span class="muted">cx</span>`
            :'<span class="muted" title="item sem fator de caixa cadastrado — digite em unidades">—</span>'}</td>
          <td class="num"><input type="number" data-qi="${i}" value="${+x.qtd||0}" min="0" style="width:74px;text-align:right"></td>
          <td class="num"><input type="number" data-ci="${i}" value="${+x.custo_unit||0}" min="0" step="0.01" style="width:84px;text-align:right"></td>
          <td class="num"><input type="number" data-ipi="${i}" value="${+x.perc_ipi||0}" min="0" max="100" step="0.01" style="width:70px;text-align:right"${x.trib_firme===false?' title="estimativa — o item não tem regra fiscal para esta origem"':''}>${x.trib_firme===false?' ≈':''}</td>
          <td class="num">${money(linhaNF(x))}</td>
          <td><button class="btn sm" data-ri="${i}">✕</button></td></tr>`).join('')+
        `</tbody></table></div><div class="count-line" style="text-align:right">${rodape()}</div>`
      : `<div class="count-line">Nenhum item — adicione produtos acima${opts.itens?'':' (ou lance só com o valor)'}.</div>`;
    const v=$('#pd-valor'); if(itens.length){ v.value=total().toFixed(2); v.disabled=true; } else { v.disabled=false; }
    $('#pd-itens').querySelectorAll('th[data-ord]').forEach(th=>th.onclick=()=>ordenar(th.dataset.ord));
    $('#pd-itens').querySelectorAll('[data-qi]').forEach(inp=>inp.oninput=()=>{
      // NÃO chamar draw() aqui: reconstruir a tabela a cada tecla destruía o input e roubava o
      // foco (não dava pra digitar). Atualiza modelo + as células derivadas (Cx/Valor) na mão.
      const i=+inp.dataset.qi, x=itens[i]; x.qtd=+inp.value||0; const tr=inp.closest('tr');
      const cxin=tr.querySelector('[data-cxi]');   // null em item sem fator de caixa
      if(cxin) cxin.value=Math.ceil((+x.qtd||0)/x.qtunitcx);
      tr.children[6].textContent = money(linhaNF(x));
      refreshTotals();
    });
    // Edição em CAIXAS: escreve de volta em UNIDADES (qtd continua a única fonte de verdade —
    // é ela que vai no payload, no item_master, no PDF e na planilha do Winthor). Nada em caixa
    // sai daqui: o Winthor faz preço × quantidade literal, então converter a qtd sem converter o
    // preço colocaria o pedido no ERP com valor ~50× menor (ver armadilha do README).
    $('#pd-itens').querySelectorAll('[data-cxi]').forEach(inp=>inp.oninput=()=>{
      const i=+inp.dataset.cxi, x=itens[i], tr=inp.closest('tr');
      const cx=Math.max(0,Math.floor(+inp.value||0));
      x.qtd=cx*(x.qtunitcx||1);
      tr.querySelector('[data-qi]').value=x.qtd;   // espelha sem redesenhar (não rouba o foco)
      tr.children[6].textContent = money(linhaNF(x));
      refreshTotals();
    });
    $('#pd-itens').querySelectorAll('[data-ci]').forEach(inp=>inp.onchange=()=>{ itens[+inp.dataset.ci].custo_unit=+inp.value||0; draw(); });
    // IPI corrigido na mão: vira snapshot do pedido (grava no banco e sai no PDF). É a saída
    // para os ~5% de itens sem regra fiscal, onde a previsão é estimativa.
    $('#pd-itens').querySelectorAll('[data-ipi]').forEach(inp=>inp.onchange=()=>{
      const i=+inp.dataset.ipi, x=itens[i]; x.perc_ipi=+inp.value||0; x.trib_fonte='manual'; x.trib_firme=true;
      inp.closest('tr').children[6].textContent = money(linhaNF(x)); refreshTotals();
    });
    $('#pd-itens').querySelectorAll('[data-ri]').forEach(b=>b.onclick=()=>{ itens.splice(+b.dataset.ri,1); draw(); });
  }
  draw();
  $('#pd-additem').onclick=()=>{
    const raw=($('#pd-prodadd').value||'').trim(); const cod=parseInt(raw,10);
    const p=(S.produtosAll||[]).find(x=>x.codprod===cod)||(S.produtosAll||[]).find(x=>(x.descricao||'').toLowerCase()===raw.toLowerCase());
    if(!p){ toast('Produto não encontrado',true); return; }
    const qt=+$('#pd-prodqt').value||Math.ceil(p.sugestao_compra||0);
    const ex=itens.find(x=>x.codprod===p.codprod); if(ex){ ex.qtd=(+ex.qtd||0)+qt; } else { itens.push(_prodItem(p,qt)); }
    if(!$('#pd-forn').value && p.fornecedor) $('#pd-forn').value=p.fornecedor;
    $('#pd-prodadd').value=''; $('#pd-prodqt').value=''; draw();
  };
  $('#m-cancel').onclick=closeModal;
  $('#m-ok').onclick=async()=>{
    const nome=($('#pd-forn').value||'').trim();
    const match=(S.fornecedores||[]).find(x=>(x.fornecedor||'').toLowerCase()===nome.toLowerCase());
    const itensPayload=itens.map(x=>({codprod:x.codprod,descricao:x.descricao,qtdisp:x.qtdisp,cobertura:x.cobertura,
      giro_mes:x.giro_mes,qtunitcx:x.qtunitcx,qtd:+x.qtd||0,custo_unit:x.custo_unit,valor:(+x.qtd||0)*(+x.custo_unit||0),
      // snapshot da tributação praticada: o PDF reimpresso meses depois tem de sair igual
      perc_ipi:x.perc_ipi==null?null:+x.perc_ipi, perc_st:x.perc_st==null?null:+x.perc_st}));
    const valor=itens.length?total():(+$('#pd-valor').value||0);
    const valor_nf=itens.length?totalNF():valor;
    await postJSON('/estoque/api/pedidos',{data_pedido:$('#pd-data').value,comprador:comp,codfornec:match?match.codfornec:(opts.codfornec||null),
      fornecedor:match?match.fornecedor:nome,n_pedido:$('#pd-num').value,valor,valor_nf,prazo_dias:+$('#pd-prazo').value||null,itens:itensPayload});
    closeModal(); toast('Pedido lançado ✓'); if(S.view==='orcamento')renderOrcamento(); };
}
function modalPedidoFornecedor(gr){ // "Gerar pedido" da Reposição → construtor com itens pré-preenchidos editáveis
  // _prodItem(p) (sem qtd) usa a sugestão CEILADA em unidades — nunca fracionária (ver _prodItem)
  modalPedido({fornecedor:gr.forn, codfornec:gr.cod, itens:gr.itens.map(p=>_prodItem(p))});
}
function modalPlano(it){
  const chave=it.chave||(it.tipo==='validade'?(it.cod+'|'+(it.lote?it.lote.dtval:it.dtval)):String(it.cod));
  const dtval=it.dtval||(it.lote?it.lote.dtval:null);
  const pl=S.planos[chave]||{};
  openModal(`<h3>Plano de ação</h3><div class="count-line">${esc(it.desc||'')}</div>
    <label>Responsável</label><input type="text" id="pl-resp" value="${esc(pl.responsavel||'')}">
    <label>Ação</label><input type="text" id="pl-acao" value="${esc(pl.acao||'')}" placeholder="ex.: ENCARTE, DEVOLUÇÃO, BONIFICAÇÃO">
    <label>Prazo</label><input type="date" id="pl-prazo" value="${pl.prazo?String(pl.prazo).slice(0,10):''}">
    <label>Status</label><select id="pl-status"><option ${pl.status==='PENDENTE'?'selected':''}>PENDENTE</option><option ${pl.status==='EM ANDAMENTO'?'selected':''}>EM ANDAMENTO</option><option ${pl.status==='CONCLUIDO'?'selected':''}>CONCLUIDO</option></select>
    <div class="m-acts">${pl.acao?`<button class="btn" id="m-del">Excluir</button>`:''}<button class="btn" id="m-cancel">Cancelar</button><button class="btn primary" id="m-ok">Salvar</button></div>`);
  $('#m-cancel').onclick=closeModal;
  if($('#m-del'))$('#m-del').onclick=async()=>{ await postJSON('/estoque/api/planos/'+encodeURIComponent(chave),{}, 'DELETE'); delete S.planos[chave]; closeModal(); toast('Plano removido'); render(); };
  $('#m-ok').onclick=async()=>{ const d={chave,tipo:it.tipo,codprod:it.cod,dtvalidade:dtval,descricao:it.desc,responsavel:$('#pl-resp').value,acao:$('#pl-acao').value,prazo:$('#pl-prazo').value||null,status:$('#pl-status').value};
    await postJSON('/estoque/api/planos',d); S.planos[chave]=d; closeModal(); toast('Plano salvo ✓'); render(); };
}

/* ───────── plano de reposição (360°) ───────── */
// Bloco de VENDA (12 meses) do 360°. Substituiu o gráfico do plano de reposição — a venda é a
// informação mais importante aqui (decisão do diretor). A linha "Próximo pedido" do plano
// CONTINUA, porque é acionável; só o gráfico mudou.
const MES_ABREV=['jan','fev','mar','abr','mai','jun','jul','ago','set','out','nov','dez'];
const mesLbl12=am=>{const s=String(am);return MES_ABREV[+s.slice(4)-1]+'/'+s.slice(2,4);};
const _temSerie=a=>Array.isArray(a)&&a.some(v=>(v||0)>0);
function planoDrawer(plano,p){
  const prox=(plano&&!plano.sem_giro&&(plano.liberacoes||[])[0])||null;
  const resumo=prox
    ? `Próximo pedido: <b>${int(prox.qt)} un</b> (${money(prox.valor)}) — ${prox.semana===0?'<b>sair agora</b>':'liberar em '+dt(prox.data)+' (sem. +'+prox.semana+')'}`
    : 'Sem necessidade de pedido no horizonte.';
  const tem=_temSerie(p&&p.serie_mensal_rs)||_temSerie(p&&p.serie_mensal);
  return `<div class="d-sec">Venda (12 meses)</div>
    <div class="count-line">${resumo}</div>
    ${tem?`${_temSerie(p&&p.serie_mensal_clientes)?`<div class="seg" id="d12-modo" style="margin:2px 0 6px">
          <span class="seg-opt ${S.d12!=='clientes'?'on':''}" data-d12="unidades" title="Volume vendido no mês">Unidades</span>
          <span class="seg-opt ${S.d12==='clientes'?'on':''}" data-d12="clientes" title="Positivação: clientes distintos que compraram ESTE item no mês. Venda caindo com clientes caindo = perda de base; venda caindo com clientes estável = os mesmos comprando menos.">Clientes</span>
        </div>`:''}
        <div class="chart-box sm" style="height:175px"><canvas id="d-venda12"></canvas></div>
        <div class="count-line" style="margin-top:4px">Inclui o <b>mês corrente</b> (barra mais clara) — ainda em andamento, então tende a ficar abaixo dos fechados.${_temSerie(p&&p.serie_mensal_clientes)?' <b>Clientes</b> = positivação do item (quantos clientes distintos o compraram no mês); passe o mouse para ver os três números.':''}</div>`
        :'<div class="muted" style="font-size:.8rem">Sem venda registrada nos últimos 12 meses.</div>'}`;
}
function buildVendaChart(p){
  const meses=(p&&p.serie_mensal_meses)||[];
  const rs=p&&p.serie_mensal_rs, un=p&&p.serie_mensal, cli=p&&p.serie_mensal_clientes;
  if(!meses.length||!(_temSerie(rs)||_temSerie(un))) return;
  // o último mês é o CORRENTE (parcial) → barra mais clara, p/ não ser lido como queda
  const _d=new Date(), hojeAM=_d.getFullYear()*100+(_d.getMonth()+1);
  const iParc=meses.indexOf(hojeAM);
  const corBar=meses.map((m,i)=>i===iParc?'rgba(56,189,248,.42)':C.accent);
  // UMA linha secundária por vez (Unidades ou Clientes), nunca as duas.
  // Desenhamos as três juntas para comparar: dois eixos à direita empilhados comem a largura, e
  // unidades × clientes ficam sobrepostas quase o tempo todo (são correlacionadas). Pior: a linha
  // de UNIDADES já duplica as barras — unidades ≈ venda ÷ preço, e o preço é estável. Por isso o
  // alternador, com os TRÊS números sempre no tooltip: ninguém perde valor, só escolhe a
  // tendência que quer ver desenhada.
  const temCli=_temSerie(cli);
  const modo=(S.d12==='clientes'&&temCli)?'clientes':'unidades';
  const sec=modo==='clientes'?cli:un;
  const secLbl=modo==='clientes'?'Clientes':'Unidades';
  const secCor=modo==='clientes'?C.purple:C.green;
  const ds=[];
  if(_temSerie(rs)) ds.push({type:'bar',label:'Venda R$',yAxisID:'y',data:rs,backgroundColor:corBar,borderRadius:4,order:2});
  if(_temSerie(sec)) ds.push({type:'line',label:secLbl,yAxisID:_temSerie(rs)?'y1':'y',data:sec,
    borderColor:secCor,backgroundColor:'transparent',tension:.25,pointRadius:2,borderWidth:2,order:1});
  const scales={y:{position:'left',ticks:{callback:v=>_temSerie(rs)?moneyK(v):int(v)}}};
  if(_temSerie(rs)&&_temSerie(sec)) scales.y1={position:'right',grid:{drawOnChartArea:false},ticks:{callback:v=>int(v)}};
  chart('d-venda12',{data:{labels:meses.map(mesLbl12),datasets:ds},
    options:{maintainAspectRatio:false,
      plugins:{legend:{display:true,labels:{boxWidth:10,font:{size:9}}},
        tooltip:{callbacks:{
          label:c=>c.dataset.type==='bar'?('Venda: '+money(c.raw)):(secLbl+': '+int(c.raw)),
          // os três números em TODO mês, independentemente da linha escolhida
          afterBody:c=>{ if(!c||!c.length) return '';
            const i=c[0].dataIndex, l=[];
            if(_temSerie(un)&&modo!=='unidades') l.push('Unidades: '+int(un[i]));
            if(temCli&&modo!=='clientes') l.push('Clientes: '+int(cli[i]));
            if(i===iParc) l.push('mês em andamento (parcial)');
            return l.join('\n'); }}}},
      scales}});
  const seg=$('#d12-modo');
  if(seg) seg.querySelectorAll('[data-d12]').forEach(b=>b.onclick=()=>{
    S.d12=b.dataset.d12; buildVendaChart(p);
    seg.querySelectorAll('[data-d12]').forEach(x=>x.classList.toggle('on',x.dataset.d12===S.d12));
  });
}

/* ───────── ficha 360° exportável ─────────
   Botões de export dos drawers (pedido do diretor 07/2026: "exportar os dois e colocar em
   horizontal"). Leva o MESMO serverQS() do fetch do drawer — unidade e período mudam os
   números, e ficha que ignora o filtro sai divergindo da tela que a originou. */
const fichaBtns=(tipo,cod)=>`<div style="display:flex;gap:8px;margin:6px 0 12px">
  <a class="btn sm" href="/estoque/api/export/ficha/${tipo}/${cod}.xlsx?${serverQS()}" title="Ficha em uma linha horizontal (Excel)">⬇ Excel</a>
  <button class="btn sm" data-ficha-pdf="${tipo}:${cod}" title="Ficha em paisagem, com o gráfico da tela (PDF)">⬇ PDF</button></div>`;

// O PDF vira POST porque leva o GRÁFICO junto (pedido do diretor 07/2026). Capturar o canvas do
// Chart.js põe no papel exatamente a curva da tela — sem redesenhar no reportlab, que seria uma
// segunda implementação do mesmo gráfico para manter em sincronia.
// ⚠️ `toDataURL` num canvas do Chart.js sai com fundo TRANSPARENTE, que no PDF branco vira texto
// claro sobre nada. Por isso a captura é rebatida sobre branco antes de subir.
function capturaGrafico(id){
  const cv=document.getElementById(id);
  if(!cv||!cv.width||!cv.height) return null;
  try{
    const off=document.createElement('canvas');
    off.width=cv.width; off.height=cv.height;
    const ctx=off.getContext('2d');
    ctx.fillStyle='#ffffff'; ctx.fillRect(0,0,off.width,off.height);
    ctx.drawImage(cv,0,0);
    return off.toDataURL('image/png');
  }catch(e){ return null; }   // canvas "sujo" ou sem contexto → PDF sai sem gráfico
}

async function baixarFichaPdf(tipo,cod,btn){
  const rotulo=btn.textContent;
  btn.disabled=true; btn.textContent='gerando…';
  try{
    const grafico=capturaGrafico(tipo==='produto'?'d-venda12':'d-forn12');
    const r=await fetch(`/estoque/api/export/ficha/${tipo}/${cod}.pdf?${serverQS()}`,
      {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({grafico})});
    if(!r.ok) throw new Error('HTTP '+r.status);
    const blob=await r.blob(), url=URL.createObjectURL(blob);
    const a=document.createElement('a'); a.href=url; a.download=`ficha_${tipo}_${cod}.pdf`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(()=>URL.revokeObjectURL(url),4000);
  }catch(e){ toast('Falha ao gerar o PDF: '+e.message); }
  finally{ btn.disabled=false; btn.textContent=rotulo; }
}

/* Giro do KPI do drawer em CAIXAS (pedido do diretor 08/2026: "consegue trocar esse giro de
   unidade para cx?"). Usa o `giro_cx` que o core já calcula (giro_mes ÷ qtunitcx).

   Mostra os DOIS, com a caixa em destaque e a unidade embaixo — não substitui. É a mesma razão
   pela qual o campo "Caixas" do modal de pedido é só uma view: a unidade é a fonte de verdade
   em todo o módulo, e ter uma tela falando só em caixa convida a comparar grandezas diferentes
   com as outras telas (que seguem em unidade).
   Item sem fator de caixa não tem giro_cx — aí a unidade é o único número possível. */
function giroKpi(p){
  const cx=p.giro_cx;
  if(cx==null||!(p.caixa>1)) return int(p.giro_mes);
  // 1 casa abaixo de 10 cx: arredondar para inteiro faria um item que gira 0,4 caixa por mês
  // aparecer como "0 cx" — ou seja, parado, quando ele vende. O core já manda com 2 casas.
  return `${dec(cx, cx<10?1:0)} cx<div class="l" style="margin-top:2px">${int(p.giro_mes)} un</div>`;
}

/* Top 3 vendedores do item — UM campo dentro de "Venda no período", não seção própria
   (decisão do diretor 07/2026: "é só o campo com os três vendedores, não precisa abrir uma nova
   aba do drawer pra tão pouca coisa"). Quantidade primeiro, porque é ela que escoa estoque; o
   faturamento ao lado revela quando os dois discordam (muita unidade barata × pouca cara). */
function topVendedoresRow(tops){
  if(!tops||!tops.length) return '';
  const linha=(v,i)=>`<div${i?' style="margin-top:3px"':''}>${i+1}. ${esc(v.nome)}
     <small class="muted">${int(v.qtd)} un · ${moneyK(v.valor)}</small></div>`;
  return `<div class="lote-row"><span>Top vendedores<br><small class="muted">no período</small></span>
    <span class="lr-r" style="text-align:right">${tops.map(linha).join('')}</span></div>`;
}

/* ───────── produto 360 ───────── */
async function openProduto(cod){
  const ov=$('#overlay'),dr=$('#drawer'); ov.classList.add('on'); dr.classList.add('on'); dr.innerHTML='<div class="loader"><div class="spinner"></div></div>';
  try{
    const j=await getJSON('/estoque/api/produto/'+cod+'?'+serverQS());
    if(!j.produto){ dr.innerHTML='<span class="d-close">×</span><div class="empty">Produto sem posição.</div>'; wireDrawer(); return; }
    const p=j.produto,lotes=j.lotes||[];
    const endVal=endsByValidade(j.enderecos);   // posições por data de validade
    const cobPct=p.cobertura!=null?Math.min(100,p.cobertura/(S.params.cob*2)*100):0;
    dr.innerHTML=`<span class="d-close">×</span>
      <h2>${esc(p.descricao)}</h2>
      <div class="d-cod">cód ${p.codprod} · ${esc(p.fornecedor||'')} · ${badge(p.curva_abc)} ${badge(p.xyz)} · ${esc((p.comprador||'').split(' ')[0]||'')}</div>
      ${fichaBtns('produto',p.codprod)}
      <div class="d-kpis">
        <div class="d-kpi"><div class="l">Disponível</div><div class="v">${int(p.qtdisp)}</div></div>
        <div class="d-kpi"><div class="l">Valor</div><div class="v">${money(p.valor)}</div></div>
        <div class="d-kpi"><div class="l">Giro/mês ${spark((p.serie_mensal&&p.serie_mensal.length?p.serie_mensal:p.serie_giro))}</div><div class="v">${giroKpi(p)}</div></div>
        <div class="d-kpi"><div class="l">Cobertura</div><div class="v">${cob(p.cobertura)}</div></div>
      </div>
      <div class="bar"><i style="width:${cobPct}%"></i></div>
      ${p.giro_fonte==='forecast'?`<div class="count-line">Giro por <b>forecast (RCA, ${S.params.fcmeses}m)</b>: ${int(p.giro_forecast)}/mês · média 3m (oficial): ${int(p.giro_media3)}/mês</div>`:''}
      ${p.giro_fonte==='sazonal'?`<div class="count-line">Giro por <b>forecast sazonal (RCA, 24m)</b>: ${int(p.giro_mes)}/mês${p.fatores_sazonais?` · fator do mês ${dec(p.fatores_sazonais[new Date().getMonth()+1]||1,2)}×`:''} · média 3m (oficial): ${int(p.giro_media3)}/mês</div>`:''}
      ${p.giro_fonte==='novo_item'?`<div class="count-line">Giro por <b>item novo</b>: ${int(p.giro_mes)}/mês — média da venda real (RCA) desde o lançamento. Os 3 meses fechados do giro oficial ainda estão zerados (produto com menos de 3 meses de casa).</div>`:''}
      ${p.giro_fonte==='mes_corrente'?`<div class="count-line">Giro pela <b>venda do mês corrente</b>: ${int(p.giro_mes)}/mês — o produto só começou a vender agora, então os 3 meses fechados do giro oficial estão zerados. Usa a venda crua do mês (sobe conforme o mês avança) p/ o item não sumir do abastecimento.</div>`:''}
      <div class="d-sec">Venda no período</div>
      <div class="lote-row"><span>Venda</span><span>${money(p.venda)}</span></div>
      <div class="lote-row"><span>Lucro</span><span>${money(p.lucro)} ${p.margem!=null?`<small class="muted">(${dec(p.margem,1)}%)</small>`:''}</span></div>
      <div class="lote-row"><span>Qtd vendida</span><span>${int(p.qtd_vendida)}<br><small class="muted">líquida de devolução</small></span></div>
      ${p.preco_medio!=null?`<div class="lote-row"><span>Preço médio<br><small class="muted">no período</small></span><span class="lr-r">${money(p.preco_medio)}/un${p.caixa>1?`<br><small class="muted">${money(p.preco_medio*p.caixa)}/cx</small>`:''}</span></div>`:''}
      ${topVendedoresRow(j.top_vendedores)}
      <div class="d-sec">Situação</div>
      <div class="lote-row"><span>Abastecimento</span><span>${badge(p.status_abast)}</span></div>
      <div class="lote-row"><span>Ruptura</span><span>${p.status_ruptura?badge('0-15',p.status_ruptura+'d'):'—'}</span></div>
      <div class="lote-row"><span>Parado</span><span>${p.status_parado?badge(p.status_parado):badge('ok','ok')}</span></div>
      <div class="lote-row"><span>Última entrada</span><span>${dt(p.dtultent)} ${p.dias_sem_entrada!=null?'('+p.dias_sem_entrada+'d)':''}</span></div>
      <div class="lote-row"><span>Última saída</span><span>${dt(p.dtultsaida)} ${p.dias_sem_venda!=null?'('+p.dias_sem_venda+'d)':''}</span></div>
      <div class="d-sec">Abastecimento (lead ${int(p.lead_efetivo)}d)</div>
      <div class="lote-row"><span>Embalagem</span><span>${embCell(p)}</span></div>
      <div class="lote-row"><span>Já pedido (aberto)</span><span>${p.qtd_ja_pedida>0?int(p.qtd_ja_pedida)+' un':'—'}</span></div>
      <div class="lote-row"><span>Estoque projetado</span><span>${int(p.estoque_projetado)} <small class="muted">(cob. ${cob(p.cobertura_proj)})</small></span></div>
      <div class="lote-row"><span>Estoque alvo</span><span>${int(p.est_alvo)}</span></div>
      <div class="lote-row"><span><b>Sugestão de compra</b></span><span><b>${sugCxN(p)}</b> ${money(valReporNF(p))} <small class="muted">c/ imp.${p.trib_firme===false?' ≈':''} · merc. ${money(valReporMerc(p))}</small></span></div>
      <div class="lote-row"><span>Status</span><span>${statExec(p.status_exec)}</span></div>
      ${p.qt_transicao>0?`<div class="lote-row"><span>Recebido (pré-entrada)</span><span><b>${int(p.qt_transicao)}</b> <small class="muted">aguardando liberação</small></span></div>`:''}
      ${planoDrawer(p.plano,p)}
      ${enderecosDrawer(j.enderecos)}
      <div class="d-sec">Lotes / validade</div>
      ${lotes.length?lotes.map(l=>{
        const d=l.dtval?String(l.dtval).slice(0,10):null, pos=(d&&endVal[d])||[];
        const sub=pos.length?`<div class="count-line" style="margin:1px 2px 9px">${pos.length} pos: ${pos.slice(0,6).join(' · ')}${pos.length>6?` <span class="muted">(+${pos.length-6})</span>`:''}</div>`:'';
        return `<div class="lote-row"><span>${dt(l.dtval)} · lote ${esc(l.numlote)}</span><span class="lr-r">${int(l.qt)} un · ${l.dias_para_vencer}d ${badge(l.classificacao)}</span></div>${sub}`;
      }).join(''):'<div class="muted" style="font-size:.8rem">Sem lotes endereçados.</div>'}`;
    wireDrawer();
    buildVendaChart(p);
  }catch(e){ dr.innerHTML='<span class="d-close">×</span><div class="empty">Erro: '+e.message+'</div>'; wireDrawer(); }
}
// ───────── drawer 360° do FORNECEDOR ─────────
// Pedido do diretor 07/2026: "colocar a tela adicional de venda mês a mês do fornecedor, igual
// tem a do produto". O gráfico sobrepõe o MESMO mês do ano anterior — sem isso a coluna
// "Cresc. AA" diz que caiu 20% e não diz QUANDO nem se é tendência ou mês pontual.
async function openFornecedor(cod){
  const ov=$('#overlay'),dr=$('#drawer'); ov.classList.add('on'); dr.classList.add('on');
  dr.innerHTML='<div class="loader"><div class="spinner"></div></div>';
  try{
    const j=await getJSON('/estoque/api/fornecedor/'+cod+'?'+serverQS());
    const f=j.fornecedor||{}, tops=j.top_produtos||[], peds=j.pedidos_abertos||[];
    const yoy=(f.venda_ant_yoy>0)?((f.venda_yoy-f.venda_ant_yoy)/f.venda_ant_yoy*100):null;
    const kv=(lbl,val,sub)=>`<div class="lote-row"><span>${lbl}</span><span class="lr-r">${val}${sub?`<br><small class="muted">${sub}</small>`:''}</span></div>`;
    // lead time só sai como número quando a amostra sustenta (mesma régua da aba Lead time)
    const leadTxt=f.lead_real!=null&&f.lead_confiavel?`${int(f.lead_real)}d`
      :(f.lead_todos!=null?`~${int(f.lead_todos)}d <small class="muted">(amostra fraca)</small>`:'—');
    dr.innerHTML=`<span class="d-close">×</span>
      <h2>${esc(f.fornecedor)}</h2>
      <div class="count-line">Cód ${f.codfornec}${f.estado?` · ${esc(f.estado)}`:''}${f.comprador?` · comprador ${esc(f.comprador)}`:''} · ${int(f.n_produtos)} itens em estoque</div>
      ${fichaBtns('fornecedor',f.codfornec)}
      <div class="d-sec">Resultado (${({mes:'mês atual','90d':'últimos 90 dias','6m':'6 meses','12m':'12 meses'})[S.vperiodo]||'período'})</div>
      ${kv('Venda líquida',`<b>${money(f.venda)}</b>`, yoy!=null?`${yoy>=0?'▲':'▼'} ${dec(Math.abs(yoy),1)}% vs ano anterior`:'')}
      ${kv('Lucro bruto',money(f.lucro), f.margem!=null?`margem ${dec(f.margem,1)}%`:'')}
      ${f.verba?kv('Verba negociada',money(f.verba), f.verba_campanha?`inclui ${money(f.verba_campanha)} de campanha`:''):''}
      <div class="d-sec">Estoque e reposição</div>
      ${kv('Valor em estoque',money(f.estoque))}
      ${kv('A comprar',`<b>${money(f.sugestao_nf)}</b>`,'sugestão c/ impostos')}
      ${f.n_ruptura>0?kv('Em ruptura',`<b style="color:${C.red}">${int(f.n_ruptura)}</b> itens`,'com giro e sem estoque'):''}
      ${f.valor_parado>0?kv('Estoque parado',money(f.valor_parado)):''}
      <div class="d-sec">Ritmo de compra</div>
      ${kv('Ciclo de compra',f.ciclo_dias!=null?`${dec(f.ciclo_dias,0)}d`:'—','de quanto em quanto tempo compramos (12m)')}
      ${kv('Lead time',leadTxt,f.lead_n?`${int(f.lead_n)} entradas medidas`:'sem entrada medida')}
      ${f.ciclo_dias!=null&&f.lead_real!=null&&f.lead_confiavel&&f.ciclo_dias<f.lead_real?`<div class="count-line" style="color:${C.orange}">⚠ Ciclo menor que o lead: pedido novo antes do anterior chegar.</div>`:''}
      ${kv('Última compra',f.ultima_compra?dt(f.ultima_compra):'—',f.n_pedidos?`${int(f.n_pedidos)} pedidos no período`:'')}
      ${peds.length?`<div class="d-sec">Pedidos em aberto (${peds.length})</div>`+peds.map(pd=>
        `<div class="lote-row"><span>#${pd.numped} · ${dt(pd.dtemissao)}</span><span class="lr-r">${money(pd.valor)}${pd.dtprevent?`<br><small class="muted">prev. ${dt(pd.dtprevent)}</small>`:''}</span></div>`).join(''):''}
      <div class="d-sec">Venda (12 meses)</div>
      <div class="count-line">Linha clara = mesmo mês do ano anterior. Inclui o <b>mês corrente</b>, ainda em andamento.</div>
      <div class="chart-box sm" style="height:185px"><canvas id="d-forn12"></canvas></div>
      ${tops.length?`<div class="d-sec">Top produtos (venda no período)</div>`+tops.map(tp=>
        `<div class="lote-row" data-cod="${tp.codprod}" style="cursor:pointer"><span class="prod">${esc(tp.descricao)}</span><span class="lr-r">${money(tp.venda)}<br><small class="muted">estoque ${money(tp.valor)}${tp.cobertura!=null?` · ${cob(tp.cobertura)}`:''}</small></span></div>`).join(''):''}`;
    wireDrawer();
    dr.querySelectorAll('.lote-row[data-cod]').forEach(r=>r.onclick=()=>openProduto(r.dataset.cod));
    buildFornChart(j);
  }catch(e){ dr.innerHTML='<span class="d-close">×</span><div class="empty">Erro: '+e.message+'</div>'; wireDrawer(); }
}
function buildFornChart(j){
  const meses=j.meses||[], atual=j.serie||[], ant=j.serie_ant||[];
  if(!meses.length||!atual.some(v=>v>0)){ const b=$('#d-forn12'); if(b&&b.parentElement) b.parentElement.innerHTML='<div class="muted" style="font-size:.8rem">Sem venda registrada nos últimos 12 meses.</div>'; return; }
  const lbl=meses.map(m=>{const s=String(m); return ('0'+((+s.slice(4,6)))).slice(-2)+'/'+s.slice(2,4);});
  const ult=atual.length-1;
  chart('d-forn12',{data:{labels:lbl,datasets:[
    {type:'bar',label:'Venda líquida',data:atual,backgroundColor:atual.map((_,i)=>i===ult?'rgba(56,189,248,.45)':C.accent),borderRadius:4,order:2},
    {type:'line',label:'Ano anterior',data:ant,borderColor:C.dim,borderWidth:1.5,borderDash:[5,4],pointRadius:0,tension:.3,fill:false,order:1}]},
    options:{plugins:{legend:{display:true,labels:{boxWidth:10,font:{size:10}}},
      tooltip:{callbacks:{label:c=>c.dataset.label+': '+money(c.raw)}}},
      scales:{y:{ticks:{callback:v=>moneyK(v)}}}}});
}
function wireDrawer(){
  $('#drawer .d-close').onclick=closeDrawer;
  // botão de PDF dos dois drawers (produto e fornecedor) — ligado aqui porque os dois passam
  // por wireDrawer() e o gráfico só existe depois que o drawer terminou de renderizar
  $('#drawer').querySelectorAll('[data-ficha-pdf]').forEach(b=>b.onclick=()=>{
    const [tipo,cod]=b.dataset.fichaPdf.split(':'); baixarFichaPdf(tipo,cod,b);
  });
}
function closeDrawer(){ $('#overlay').classList.remove('on'); $('#drawer').classList.remove('on'); }

/* ───────── ocupação / WMS ───────── */
// TIPOENDER: AP = face de apanha (chão, 1 SKU/posição) · AE = pulmão (paletes, níveis altos)
const TIPO_WMS={AP:'Picking',AE:'Pulmão'};
const tipoWms=t=>TIPO_WMS[t]||t||'—';
// seção "Endereços WMS" no drawer do produto — agrupada por Picking/Pulmão
function enderecosDrawer(ends){
  if(!ends||!ends.length) return '<div class="d-sec">Endereços WMS</div><div class="muted" style="font-size:.8rem">Sem posição endereçada.</div>';
  const tot=ends.reduce((s,e)=>s+(+e.q||0),0);
  const fmt=e=>`R${int(e.rua)} · P${int(e.predio)} · N${int(e.nivel)} · A${int(e.apto)}`;
  const g={}; ends.forEach(e=>{const t=tipoWms(e.tipo);(g[t]=g[t]||[]).push(e);});
  const keys=[...new Set(['Picking','Pulmão',...Object.keys(g)])].filter(k=>g[k]);
  let html=`<div class="d-sec">Endereços WMS · ${int(ends.length)} posições · ${int(tot)} un</div>`;
  keys.forEach(t=>{
    const arr=g[t].sort((a,b)=>(b.q||0)-(a.q||0)), gt=arr.reduce((s,e)=>s+(+e.q||0),0);
    html+=`<div style="font-size:.7rem;font-weight:700;letter-spacing:.4px;color:var(--accent);text-transform:uppercase;margin:9px 2px 4px">${esc(t)} · ${arr.length} pos · ${int(gt)} un</div>`
      + arr.map(e=>`<div class="lote-row"><span class="mono">${fmt(e)}</span><span class="lr-r">${int(e.q)} un</span></div>`).join('');
  });
  return html;
}
// agrupa as posições WMS por data de validade (p/ listar embaixo de cada lote no drawer)
function endsByValidade(ends){
  const m={};
  (ends||[]).forEach(e=>{ if(e.dtval==null) return; const d=String(e.dtval).slice(0,10);
    (m[d]=m[d]||[]).push(`R${int(e.rua)}·P${int(e.predio)}·N${int(e.nivel)}·A${int(e.apto)}`); });
  return m;
}
// barras de ocupação por RUA — 2 COLUNAS (metade da altura → sem buraco na direita)
// e cada rua é CLICÁVEL: seleciona a rua p/ a lista de conferência.
function ruasHtml(ruas){
  if(!ruas.length) return '<div class="empty">Sem ruas.</div>';
  return `<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:5px 24px;margin-top:8px">`+ruas.map(r=>{
    const c=r.pct>=0.85?C.red:(r.pct>=0.5?C.yellow:C.green), on=S.ocRua===r.rua;
    return `<div class="oc-rua-row" data-rua="${r.rua}" title="Clique p/ montar a conferência da rua" style="display:flex;align-items:center;gap:8px;font-size:.76rem;cursor:pointer;padding:3px 5px;border-radius:6px;${on?`background:rgba(56,189,248,.14);outline:1px solid ${C.accent}`:''}">
      <span class="mono" style="width:38px;color:${on?C.accent:'var(--text-dim)'}">R${int(r.rua)}</span>
      <span class="oc-bar"><i style="width:${Math.round(r.pct*100)}%;background:${c}"></i></span>
      <span class="num" style="width:42px;text-align:right">${pct(r.pct)}</span>
      <span class="muted" style="width:60px;text-align:right;font-size:.68rem">${int(r.ocupadas)}/${int(r.posicoes)}</span>
    </div>`;
  }).join('')+`</div>`;
}
// painel de conferência da rua selecionada (ordem de caminhada + reservadas vazias + export)
function confPanel(conf){
  if(!conf) return '';
  if(conf.erro) return `<div class="panel"><div class="empty">Conferência indisponível: ${esc(conf.erro)}</div></div>`;
  const todas=conf.itens||[], its=S.confTipo?todas.filter(x=>x.tipo===S.confTipo):todas;
  const qs=serverQS()+'&rua='+conf.rua+(S.confTipo?'&tipo='+encodeURIComponent(S.confTipo):'');
  const nvz=its.filter(x=>(x.situacao||'').startsWith('VAZIA')).length;
  return `<div class="panel" id="oc-conf" style="border-color:${C.accent}">
    <h3>Conferência — Rua ${int(conf.rua)} <small class="muted">· ${int(its.length)} posições · ${int(new Set(its.filter(x=>x.codprod).map(x=>x.codprod)).size)} itens · ${int(nvz)} devem estar vazias${S.confTipo?` · só ${esc(S.confTipo)}`:''}</small></h3>
    <div style="display:flex;gap:8px;margin:8px 0 4px;flex-wrap:wrap;align-items:center">
      <span class="seg" id="conf-tipo">${['','Picking','Pulmão'].map(t=>`<span class="seg-opt ${(S.confTipo||'')===t?'on':''}" data-t="${esc(t)}">${t||'Todos'}</span>`).join('')}</span>
      <a class="btn sm" href="/estoque/api/export/conferencia.xlsx?${qs}">⬇ Excel</a>
      <a class="btn sm" href="/estoque/api/export/conferencia.pdf?${qs}">⬇ PDF</a>
      <button class="btn sm" id="oc-conf-x">✕ fechar</button>
    </div>
    <div class="count-line">Ordem de caminhada (prédio → nível → apto). Quantidade do sistema p/ comparar com a prateleira. As <b>reservadas vazias</b> entram na lista pra confirmar que estão vazias.</div>
    <div class="tbl-wrap" style="max-height:560px;overflow:auto"><table><thead><tr><th>Endereço</th><th>Tipo</th><th class="num">Cód</th><th>Produto</th><th class="num">Qtd (sist.)</th><th>Validade</th><th>Situação</th></tr></thead>
    <tbody>${its.map(x=>{const vz=(x.situacao||'').startsWith('VAZIA');
      return `<tr ${x.codprod?`data-cod="${x.codprod}" style="cursor:pointer"`:''}><td class="mono">${esc(x.endereco)}</td><td>${esc(x.tipo)}</td><td class="num">${x.codprod||'—'}</td>
        <td><span class="prod" title="${esc(x.descricao||'')}">${esc(x.descricao||'—')}</span></td>
        <td class="num">${vz?'0':int(x.qt)}</td>
        <td>${x.dtval?dt(x.dtval):'—'}</td>
        <td>${vz?`<span class="badge" style="background:${C.orange}22;color:${C.orange}">vazia (reservada)</span>`:''}</td></tr>`;}).join('')}</tbody></table></div></div>`;
}
// ocupação por tipo de endereço (picking × pulmão)
function tiposHtml(tipos){
  if(!tipos.length) return '<div class="empty">—</div>';
  return `<div style="display:flex;flex-direction:column;gap:14px;margin-top:8px">`+tipos.map(t=>{
    const c=t.pct>=0.85?C.red:(t.pct>=0.5?C.yellow:C.green);
    return `<div>
      <div style="display:flex;justify-content:space-between;align-items:baseline;font-size:.88rem"><b>${esc(t.label)}</b><span class="num">${pct(t.pct)}</span></div>
      <span class="oc-bar" style="display:block;width:100%;margin-top:5px"><i style="width:${Math.round(t.pct*100)}%;background:${c}"></i></span>
      <div class="muted" style="font-size:.72rem;margin-top:3px">${int(t.ocupadas)} / ${int(t.posicoes)} posições</div>
    </div>`;
  }).join('')+`</div>`;
}
async function renderOcupacao(P){
  const el=$('#v-ocupacao'), qs=serverQS();
  let j=S._ocJ;
  if(!j || S._ocKey!==qs){   // cacheia o resumo p/ o toggle do card não re-buscar (evita flash)
    el.innerHTML=head('Ocupação do depósito (WMS)','ocupacao')+`<div class="loader"><div class="spinner"></div>Calculando ocupação…</div>`;
    try{ j=await getJSON('/estoque/api/ocupacao?'+qs); }
    catch(e){ el.innerHTML=head('Ocupação do depósito (WMS)','ocupacao')+`<div class="empty">Ocupação indisponível: ${esc(e.message)}</div>`; return; }
    S._ocJ=j; S._ocKey=qs;
  }
  let conf=null;   // conferência da rua selecionada (clique no heatmap)
  if(S.ocRua!=null){ try{ conf=await getJSON('/estoque/api/rua/'+S.ocRua); }catch(e){ conf={erro:e.message}; } }
  const ocup=j.com_estoque||1;   // % por item = sobre as posições COM ESTOQUE (pos_end é QT>0)
  const mortos=P.filter(p=>p.espaco_morto);
  let rows=P.filter(p=>(p.pos_end||0)>0);
  if(S.ocMorto) rows=rows.filter(p=>p.espaco_morto);   // filtro na tela via card
  rows=rows.sort((a,b)=>(b.pos_end||0)-(a.pos_end||0));
  const cols=[colCod,colProd,colForn,
    {key:'curva_abc',label:'ABC',badge:true},{key:'xyz',label:'XYZ',badge:true},
    {key:'pos_end',label:'Posições',num:true,fmt:int},
    {key:'pos_end',label:'% ocup.',num:true,fmt:v=>dec(v/ocup*100,1)+'%'},
    {key:'m3_end',label:'m³ end.',num:true,fmt:v=>v?dec(v,2):'—'},
    colGiroSpark,{key:'cobertura',label:'Cob.',num:true,fmt:cob},
    {key:'espaco_morto',label:'',html:p=>p.espaco_morto?`<span class="badge" style="background:${C.orange}22;color:${C.orange}">espaço morto</span>`:''}];
  // card Espaço morto = clicável, filtra a tabela "Ocupação por item" nos itens espaço morto
  const mortoCard=`<div class="card kpi" id="oc-card-morto" style="cursor:pointer${S.ocMorto?';border-color:'+C.orange:''}" title="Clique p/ filtrar a tabela nos itens espaço morto">
      <div class="k-label"><span class="dot" style="background:${C.orange}"></span>Espaço morto${S.ocMorto?` · <span style="color:${C.orange}">✕ limpar</span>`:''}</div>
      <div class="k-value">${int(mortos.length)}</div>
      <div class="k-sub">${S.ocMorto?'filtrando na tabela ↓':'ocupam muito · giram pouco · clique'}</div></div>`;
  el.innerHTML=head('Ocupação do depósito (WMS)','ocupacao')
    +`<div class="kpi-grid">
        ${kpi('Ocupação do depósito',pct(j.pct_ocupado),int(j.ocupadas)+' / '+int(j.posicoes)+' · com estoque '+pct(j.pct_com_estoque),C.accent)}
        ${kpi('Posições livres',int(j.livres),pct(j.pct_livre)+' livre',C.green)}
        ${kpi('Bloqueados',int(j.bloqueados),'fora da conta · à parte',C.red)}
        ${kpi('Produtos endereçados',int(j.produtos),'no depósito',C.accent2)}
        ${kpi('Média posições/produto',dec(j.media_pos,1),'espalhamento',C.purple)}
        ${mortoCard}
      </div>
      <div class="row">
        <div class="panel grow" style="flex:2 1 420px"><h3><span>Ocupação por RUA${tipT('% de posições ocupadas em cada rua. Clique numa rua para montar a conferência.')}</span> <small class="muted">· ${(j.ruas||[]).length} ruas · clique numa rua p/ conferir</small></h3>${ruasHtml(j.ruas||[])}
          <div class="count-line">Régua do <b>WMS</b> (exclui bloqueados · inclui RUA 99) — bate com a consulta 1772 do Winthor. Verde = tem espaço · amarelo = enchendo · vermelho = rua lotada.</div></div>
        <div class="grow" style="flex:1 1 240px;display:flex;flex-direction:column;gap:16px;min-width:0">
          <div class="panel" style="margin:0"><h3><span>Por tipo de endereço${tipT('Picking (face de apanha, no chão) × Pulmão (paletes de armazenagem).')}</span></h3>${tiposHtml(j.tipos||[])}
            <div class="count-line">Picking = face de apanha (chão) · Pulmão = paletes de armazenagem.</div></div>
          <div class="panel" id="oc-card-vazias" style="margin:0;cursor:pointer" title="Ver a lista das vagas reservadas"><h3><span>Reservadas vazias 🔒${tipT('Posições que o WMS marca como ocupadas mas estão sem mercadoria.')}</span></h3>
            <div style="font-size:2.2rem;font-weight:800;line-height:1;color:${C.orange};font-family:'JetBrains Mono',monospace">${int(j.vazias_total||0)}</div>
            <div class="count-line" style="margin-top:7px">posições que o WMS diz ocupadas mas <b>sem mercadoria</b> · ${int(j.vazias_com_prod||0)} com produto alocado. <b>Clique p/ ver a lista ↓</b></div></div>
        </div>
      </div>
      ${confPanel(conf)}
      <div class="panel"><h3><span>Ocupação por item${tipT('Posições e volume (m³) ocupados por cada produto endereçado.')}</span> <small class="muted">· ${int(rows.length)} produtos${S.ocMorto?` · <span style="color:${C.orange}">filtrando espaço morto</span>`:' endereçados'} · clique p/ ver as posições</small></h3>
        <div class="count-line">Posições = slots <b>com estoque</b> do item · <b>% ocup.</b> = sobre as ${int(j.com_estoque)} posições com estoque (não o total) · m³ = volume endereçado.</div>
        ${renderTable(rows,cols,'ocupacao')}</div>
      ${vaziasPanel(j)}`;
  const cm=$('#oc-card-morto'); if(cm) cm.onclick=()=>{ S.ocMorto=!S.ocMorto; render(); };
  const cv=$('#oc-card-vazias'); if(cv) cv.onclick=()=>{ const t=$('#oc-vazias'); if(t) t.scrollIntoView({behavior:'smooth',block:'start'}); };
  // rua clicável → conferência
  el.querySelectorAll('.oc-rua-row').forEach(x=>x.onclick=()=>{ const r=+x.dataset.rua, on=(S.ocRua===r); S.ocRua=on?null:r; S._ocScroll=!on; render(); });
  const cx=$('#oc-conf-x'); if(cx) cx.onclick=()=>{ S.ocRua=null; render(); };
  // filtros picking/pulmão (listas em que a linha É uma posição)
  const ct=$('#conf-tipo'); if(ct) ct.querySelectorAll('.seg-opt').forEach(o=>o.onclick=()=>{ S.confTipo=o.dataset.t||''; render(); });
  const vt=$('#vaz-tipo'); if(vt) vt.querySelectorAll('.seg-opt').forEach(o=>o.onclick=()=>{ S.vazTipo=o.dataset.t||''; render(); });
  if(conf && !conf.erro && S._ocScroll){ S._ocScroll=false; const t=$('#oc-conf'); if(t) t.scrollIntoView({behavior:'smooth',block:'start'}); }
}
// tabela full-width das posições ocupadas-mas-vazias (o "reservado") + produto que alocou a vaga
function vaziasPanel(j){
  const todas=j.vazias||[]; if(!todas.length) return '';
  const list=S.vazTipo?todas.filter(v=>v.tipo===S.vazTipo):todas;
  const dm={}; (S.produtosAll||[]).forEach(p=>{dm[p.codprod]=p.descricao;});
  const vqs=serverQS()+(S.vazTipo?'&tipo='+encodeURIComponent(S.vazTipo):'');
  return `<div class="panel" id="oc-vazias"><h3><span>Posições ocupadas sem estoque — reservadas${tipT('Vagas reservadas sem mercadoria e o produto que reservou cada uma.')}</span> <small class="muted">· ${int(list.length)} vagas${S.vazTipo?` · só ${esc(S.vazTipo)}`:''} · o que reservou cada uma</small></h3>
    <div style="display:flex;gap:8px;margin:6px 0 2px;align-items:center;flex-wrap:wrap"><span class="seg" id="vaz-tipo">${['','Picking','Pulmão'].map(t=>`<span class="seg-opt ${(S.vazTipo||'')===t?'on':''}" data-t="${esc(t)}">${t||'Todos'}</span>`).join('')}</span>
      <a class="btn sm" href="/estoque/api/export/vazias.xlsx?${vqs}">⬇ Excel</a>
      <a class="btn sm" href="/estoque/api/export/vazias.pdf?${vqs}">⬇ PDF</a></div>
    <div class="count-line">O WMS marca a posição como ocupada mas não há mercadoria. <b>Endereço fixo</b> → normal (a vaga é do produto, vai repor); senão, dá pra liberar. Clique p/ abrir o produto.</div>
    <div class="tbl-wrap" style="max-height:520px;overflow:auto"><table><thead><tr><th>Endereço</th><th>Tipo</th><th class="num">Cód</th><th>Produto que reservou a vaga</th></tr></thead>
    <tbody>${list.map(v=>{const nm=v.descricao||dm[v.codprod]||(v.codprod?('Produto '+v.codprod):'— sem produto');
      return `<tr ${v.codprod?`data-cod="${v.codprod}" style="cursor:pointer"`:''}><td class="mono">${esc(v.end)}</td><td>${esc(v.tipo)}</td><td class="num">${v.codprod||'—'}</td><td><span class="prod" title="${esc(nm)}">${esc(nm)}</span></td></tr>`;}).join('')}</tbody></table></div></div>`;
}

/* ───────── dispatch ───────── */
// tooltip: descrição completa ao passar o mouse — só quando o texto da coluna Produto
// está cortado (ellipsis). A descrição inteira já vem no DOM; aqui só expomos via `title`.
// Um observer no #content cobre TODAS as abas (sync e async) num ponto só, sem tocar templates.
function markProdTitles(root){
  (root || document).querySelectorAll('.prod').forEach(el=>{
    if(el.dataset.tt) return;                       // já processado nesta renderização
    el.dataset.tt = '1';
    if(el.scrollWidth > el.clientWidth + 1) el.title = el.textContent;  // só se truncado
  });
}
function startProdTitles(){
  const box = $('#content'); if(!box) return;
  new MutationObserver(muts=>{
    for(const m of muts){ if(m.addedNodes.length){ markProdTitles(); break; } }
  }).observe(box, {childList:true, subtree:true});
}
// tooltip de ajuda: uma única caixinha fixa no body (não é cortada pelo overflow das tabelas),
// acionada por delegação no ícone .ttip (gerado dentro do HTML dos cabeçalhos/títulos).
function setupTips(){
  if(document.getElementById('tt-pop')) return;
  const pop=document.createElement('div'); pop.id='tt-pop'; document.body.appendChild(pop);
  let cur=null;
  function place(el){
    const r=el.getBoundingClientRect(), m=8, vw=innerWidth, vh=innerHeight;
    pop.style.left='0px'; pop.style.top='0px';                 // reset p/ medir a largura real
    const pw=pop.offsetWidth, ph=pop.offsetHeight;
    let left=Math.max(m, Math.min(r.left+r.width/2-pw/2, vw-pw-m));
    let top=r.bottom+6; if(top+ph>vh-m) top=r.top-ph-6;        // sem espaço embaixo → mostra acima
    pop.style.left=left+'px'; pop.style.top=Math.max(m,top)+'px';
  }
  function show(el){ const txt=el.getAttribute('data-tip'); if(!txt) return; cur=el; pop.textContent=txt; pop.classList.add('on'); place(el); }
  function hide(){ cur=null; pop.classList.remove('on'); }
  const near=e=>e.target && e.target.closest ? e.target.closest('.ttip') : null;
  document.addEventListener('mouseover',e=>{ const t=near(e); if(t) show(t); });
  document.addEventListener('mouseout', e=>{ const t=near(e); if(t&&t===cur) hide(); });
  document.addEventListener('focusin', e=>{ const t=near(e); if(t) show(t); });
  document.addEventListener('focusout',e=>{ const t=near(e); if(t&&t===cur) hide(); });
  // clicar no ⓘ não deve disparar a ordenação da coluna (th.onclick, fase de bolha)
  document.addEventListener('click',e=>{ if(near(e)){ e.stopPropagation(); e.preventDefault(); } }, true);
  window.addEventListener('scroll',()=>{ if(cur) hide(); }, true);
  window.addEventListener('resize',()=>{ if(cur) hide(); });
}
// mostra só os tabs do grupo ativo (chamado cedo no boot p/ não piscar todos os tabs no load)
function applyNav(){
  if(!$('#v-'+S.view)) S.view='cockpit';
  const g=GROUP_OF(S.view);
  document.querySelectorAll('.navgroup').forEach(x=>x.classList.toggle('active',x.dataset.group===g));
  document.querySelectorAll('.tab').forEach(t=>{ t.style.display=(t.dataset.group===g)?'':'none'; t.classList.toggle('active',t.dataset.view===S.view); });
}
function render(){
  if(!$('#v-'+S.view)) S.view='cockpit';   // view inválida/removida (ex.: 'fila' salva) → cockpit
  document.querySelectorAll('.view').forEach(v=>v.classList.remove('active'));
  $('#v-'+S.view).classList.add('active');
  applyNav();
  if(S.view==='orcamento'){ renderOrcamento(); savePrefs(); return; }
  if(S.view==='logistica'){ renderLogistica(); savePrefs(); return; }
  if(S.view==='plano'){ renderPlano(); savePrefs(); return; }
  if(S.view==='desempenho'){ renderDesempenho(); savePrefs(); return; }
  if(S.view==='leadtime'){ renderLeadtime(); savePrefs(); return; }   // base própria (12m de pedidos), não usa filtered()
  if(S.view==='verbas'){ renderVerbas(); savePrefs(); return; }       // base própria (PCVERBA), não usa filtered()
  if(S.view==='vencidos'){ renderVencidos(); savePrefs(); return; }
  if(S.view==='meta_ruptura'){ renderMetaRuptura(); savePrefs(); return; }   // base própria (90d), não usa filtered()
  const P=filtered();
  ({cockpit:renderCockpit,gerencial:renderGerencial,ruptura:renderRuptura,ruptura_comprador:renderRupturaComprador,estoque_zero:renderEstoqueZero,reposicao:renderReposicao,validade:()=>renderValidade(),parado:renderParado,comprasvendas:renderComprasVendas,abcxyz:renderABCXYZ,fornecedores:renderFornecedores,produtos:renderProdutos,qualidade:renderQualidade,ocupacao:renderOcupacao}[S.view]||renderCockpit)(P);
  savePrefs();
}
function goView(view,filt){ S.view=view; filt=filt||{}; S.cli.abast=filt.abast?(Array.isArray(filt.abast)?filt.abast:[filt.abast]):[]; S.cli.parado=filt.parado||''; S.cli.ruptura=filt.ruptura||''; S.cli.cobFaixa=filt.cobFaixa?(Array.isArray(filt.cobFaixa)?filt.cobFaixa:[filt.cobFaixa]):[]; S.cli.cobSub=''; if(filt.curva!=null){S.cli.curva=Array.isArray(filt.curva)?filt.curva:[filt.curva];syncCurvaUI();} render(); }

/* ───────── boot ───────── */
async function init(){
  const pr=loadPrefs();
  if(pr.vperiodo) S.vperiodo=pr.vperiodo; if(pr.params) S.params={...S.params,...pr.params};   // base fixa em gerencial (endereçado só p/ validade, que é isolada)
  if(pr.unidade) S.unidade=pr.unidade;
  if(pr.view) S.view=pr.view;
  if(pr.repAll) S.repAll=true;                 // quem prefere a Abastecimento aberta não reclica todo dia
  if(pr.repOrd) S.repOrd=pr.repOrd;
  if(pr.orcArrastar) S.orcArrastar=true;       // arraste do estouro do mês anterior (opt-in)
  applyNav();   // organiza os tabs já na 1ª pintura (antes do fetch) — evita o flash de todos os tabs
  document.body.classList.add('booted');   // revela os tabs (CSS esconde até aqui)
  try{
    const f=await getJSON('/estoque/api/filtros');
    S.filiaisAll=f.filiais; S.nomesFilial=f.nomes_filial||{};
    // seletor de Unidade de negócio (escopa estoque + venda)
    const unids=f.unidades||[{id:'atacado',nome:'Atacado'}];
    if(!unids.some(u=>u.id===S.unidade)) S.unidade=f.unidade_padrao||'atacado';
    $('#f-unidade').innerHTML=unids.map(u=>`<option value="${u.id}" ${u.id===S.unidade?'selected':''}>${u.cod?esc(u.cod)+' - ':''}${esc(u.nome)}</option>`).join('');
    S.fornecedores=f.fornecedores||[];
    $('#f-fornec-dl').innerHTML=f.fornecedores.map(o=>`<option value="${o.codfornec} · ${esc(o.fornecedor)}">`).join('');
    $('#f-depto').innerHTML+=f.deptos.map(d=>`<option value="${d}">${d}</option>`).join('');
    $('#f-comprador').innerHTML='<option value="">Empresa toda</option>'+f.compradores.filter(c=>c.codcomprador>0).map(c=>`<option value="${c.codcomprador}">${esc(c.comprador)}</option>`).join('');
    // Comprador inicial: preferência local (última escolha do usuário) tem prioridade; sem ela,
    // cai no comprador vinculado ao usuário no Admin. É default, não trava — trocar é livre e a
    // troca vira preferência, então o vínculo não volta a se impor na próxima visita.
    const compIni = pr.comprador || (f.comprador_padrao != null ? String(f.comprador_padrao) : '');
    if(compIni && $('#f-comprador').querySelector(`option[value="${compIni}"]`)){
      S.cli.comprador=compIni; $('#f-comprador').value=compIni;
      S.compradorNome=$('#f-comprador').selectedOptions[0]?.textContent||'';
    }
  }catch(e){ toast('Falha nos filtros: '+e.message,true); }
  // base toggle visual
  // params inputs
  $('#p-lead').value=S.params.lead; $('#p-seg').value=S.params.seg; $('#p-cob').value=S.params.cob; $('#p-hor').value=S.params.hor;
  $('#p-parado').value=S.params.parado; $('#p-fcmeses').value=S.params.fcmeses;
  $('#p-meta-a').value=S.params.metaA; $('#p-meta-b').value=S.params.metaB; $('#p-meta-c').value=S.params.metaC;
  $('#p-ideal-dias').value=S.params.idealDias; $('#p-ideal-meta').value=S.params.idealMeta;
  const giroModo=()=>S.params.sazonal?2:(S.params.forecast?1:0);  // 0=media3 1=forecast 2=sazonal
  $('#p-forecast').querySelectorAll('.seg-opt').forEach(o=>o.classList.toggle('on',+o.dataset.v===giroModo()));
  $('#p-forecast').querySelectorAll('.seg-opt').forEach(o=>o.onclick=()=>{const v=+o.dataset.v;S.params.forecast=v>=1?1:0;S.params.sazonal=v===2?1:0;$('#p-forecast').querySelectorAll('.seg-opt').forEach(x=>x.classList.toggle('on',x===o));});
  $('#p-arredcx').querySelectorAll('.seg-opt').forEach(o=>o.classList.toggle('on',+o.dataset.v===(S.params.arredondacx?1:0)));
  $('#p-arredcx').querySelectorAll('.seg-opt').forEach(o=>o.onclick=()=>{S.params.arredondacx=+o.dataset.v;$('#p-arredcx').querySelectorAll('.seg-opt').forEach(x=>x.classList.toggle('on',x===o));});

  // comprador → client filter + define visão inicial
  $('#f-comprador').onchange=e=>{ S.cli.comprador=e.target.value; S.compradorNome=e.target.value?(e.target.selectedOptions[0]?.textContent||''):''; render(); };
  $('#f-unidade').onchange=e=>{S.unidade=e.target.value; S.cli.comprador=''; $('#f-comprador').value=''; S.compradorNome=''; loadData();};
  $('#f-vperiodo').value=S.vperiodo; $('#f-vperiodo').onchange=e=>{S.vperiodo=e.target.value;loadData();};
  { const fc=$('#f-curva'); if(fc) fc.addEventListener('change',()=>{ S.cli.curva=[...fc.querySelectorAll('input[type=checkbox]:checked')].map(c=>c.value); syncCurvaUI(); render(); }); }
  { const fx=$('#f-xyz'); if(fx) fx.addEventListener('change',()=>{ S.cli.xyz=[...fx.querySelectorAll('input[type=checkbox]:checked')].map(c=>c.value); syncXyzUI(); render(); }); }
  $('#f-fornec').onchange=e=>{
    const raw=(e.target.value||'').trim(), low=raw.toLowerCase(), L=S.fornecedores||[];
    const cod=(raw.match(/^\s*(\d+)/)||[])[1];                     // código à esquerda ("708 · NOME") ou digitado puro
    let m = cod ? L.find(x=>String(x.codfornec)===cod) : null;
    if(!m) m=L.find(x=>`${x.codfornec} · ${x.fornecedor||''}`.toLowerCase()===low)  // valor exato do datalist
             ||L.find(x=>(x.fornecedor||'').toLowerCase()===low);                   // razão social exata
    if(!m){ const hits=L.filter(x=>(x.fornecedor||'').toLowerCase().includes(low)); if(hits.length===1) m=hits[0]; } // parcial só se única
    S.cli.fornec=m?String(m.codfornec):'';
    e.target.value=m?`${m.codfornec} · ${m.fornecedor}`:'';        // normaliza p/ "código · razão"; sem correspondência → volta p/ Todos
    render();
  };
  $('#f-depto').onchange=e=>{S.cli.depto=e.target.value;render();};
  document.addEventListener('click',e=>{ document.querySelectorAll('details.ms[open]').forEach(d=>{ if(!d.contains(e.target)) d.open=false; }); });
  let bt; $('#f-busca').oninput=e=>{clearTimeout(bt);bt=setTimeout(()=>{S.cli.busca=e.target.value;render();},250);};
  $('#btn-params').onclick=()=>{const p=$('#params-panel');p.style.display=p.style.display==='none'?'block':'none';};
  $('#btn-limpar').onclick=()=>{
    S.cli={comprador:'',curva:[],xyz:[],fornec:'',depto:'',busca:'',abast:[],margem:[],parado:'',ruptura:'',valDias:'',cobFaixa:[],parFaixa:[]};
    S.compradorNome='';
    ['#f-comprador','#f-fornec','#f-depto'].forEach(s=>{const e=$(s);if(e)e.value='';});
    $('#f-busca').value=''; syncCurvaUI(); syncXyzUI();
    render();
  };
  // meta aceita 0 (tolerância zero), então não dá p/ usar `||default` — vazio/inválido cai no default
  const _meta=(id,d)=>{const v=($(id).value||'').trim(); return v===''||!isFinite(+v)?d:Math.max(0,+v);};
  $('#p-apply').onclick=()=>{S.params={lead:+$('#p-lead').value,seg:+$('#p-seg').value,cob:+$('#p-cob').value,hor:+$('#p-hor').value,parado:+$('#p-parado').value||60,forecast:S.params.forecast?1:0,sazonal:S.params.sazonal?1:0,fcmeses:+$('#p-fcmeses').value||6,arredondacx:S.params.arredondacx?1:0,metaA:_meta('#p-meta-a',2),metaB:_meta('#p-meta-b',5),metaC:_meta('#p-meta-c',10),
    // limiar 0 tornaria TODO item "ideal" → piso de 1 dia aqui (o servidor tem o seu próprio
    // clamp p/ querystring montada na mão, onde 0/lixo cai no default 45). Meta 0 é válida.
    idealDias:Math.max(1,_meta('#p-ideal-dias',45)),idealMeta:Math.min(100,_meta('#p-ideal-meta',90))};loadData();};
  document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{S.view=t.dataset.view;S.cli.parado='';S.cli.ruptura='';S.cli.cobFaixa=[];S.cli.cobSub='';render();});
  document.querySelectorAll('.navgroup').forEach(x=>x.onclick=()=>{ const g=x.dataset.group; if(GROUP_OF(S.view)!==g){ S.view=NAV[g][0]; S.cli.parado='';S.cli.ruptura='';S.cli.cobFaixa=[];S.cli.cobSub=''; render(); }});
  $('#overlay').onclick=closeDrawer; $('#modal-bg').onclick=e=>{if(e.target===$('#modal-bg'))closeModal();};
  document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeDrawer();closeModal();}});
  setStickTop(); window.addEventListener('resize', setStickTop); window.addEventListener('load', setStickTop);
  startProdTitles();   // tooltip da descrição completa na coluna Produto (todas as abas)
  setupTips();         // tooltip de ajuda (ⓘ) nos títulos e cabeçalhos calculados
  loadData();
}
// altura real da topbar+filterbar (ambas sticky) → offset do cabeçalho congelado das tabelas
function setStickTop(){
  const tb=$('.topbar'), fb=$('.filterbar');
  const h=(tb?tb.offsetHeight:0)+(fb?fb.offsetHeight:0);
  if(h) document.documentElement.style.setProperty('--stick-top', h+'px');
}
init();
