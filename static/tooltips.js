'use strict';
/* Tooltips de ajuda (hover) — componente compartilhado do Multpel Comercial.
   Registro central de textos (auditados contra cobertura.py/rfm.py/metas.py/cohort.py/server.py) +
   decorador que coloca o ícone ⓘ nos títulos e cabeçalhos calculados de cada página, e uma única
   caixinha (#tt-pop) fixa no body (não é cortada pelo overflow das tabelas).
   Puramente aditivo: rótulo sem texto no registro → nada aparece. */
(function () {

  // ── Registro (fonte única = catálogo aprovado). COMMON vale p/ todas as páginas;
  //    a chave da página sobrescreve a COMMON. Chave = texto exato do título OU do cabeçalho. ──
  var COMMON = {
    'Lucro 12m': 'Lucro dos últimos 12 meses.',
    'Venda 12m': 'Venda líquida dos últimos 12 meses (venda − devoluções).',
    'Positivação': '% da carteira que comprou nos últimos 12 meses (clientes que positivaram ÷ carteira).',
    'YoY': 'Variação da receita vs. o mesmo período do ano anterior.'
  };

  var TIPS = {
    index: {
      'Série temporal — 12 meses': 'Venda e lucro mês a mês nos últimos 12 meses.',
      'YoY — Métricas-chave (12m)': 'Últimos 12 meses vs. os 12 meses anteriores (venda, lucro, clientes, mix). Move devagar de propósito: é tendência de longo prazo. O % embaixo dos cards é outra conta — lá o mês atual é comparado com o mesmo período do ano passado.',
      'Top 10 departamentos por lucro (12m)': 'Departamentos que mais deram lucro nos últimos 12 meses. Clique para ver os clientes.',
      'Top 10 vendedores por lucro (12m)': 'Vendedores que mais deram lucro nos últimos 12 meses.',
      'Top 10 clientes por lucro (12m)': 'Clientes que mais deram lucro nos últimos 12 meses.'
    },
    radar: {
      'Radar de Produtos': 'Produtos e clientes que reduziram ou pararam de comprar — onde a receita está escorrendo.',
      'Receita em risco': 'Queda de receita do item vs. o período anterior — quanto se está deixando de faturar.',
      '% queda': 'Queda percentual da receita vs. o período anterior.',
      'Clientes perdidos': 'Clientes que compravam o item na janela anterior e NÃO voltaram a comprar na janela recente. Não é o saldo entre as duas janelas: 20 clientes que param e 20 que entram dariam zero e esconderiam a perda. Ao abrir o produto, a lista pode mostrar MAIS clientes — lá o recorte é de 12 meses, aqui é só a janela escolhida.',
      'Situação': 'Situação do cliente no item: esfriando (volume caiu >50%), parou (sem comprar há ≥ a janela) ou perdido (há ≥2× a janela, ou nunca).',
      'Dias parado': 'Dias desde a última compra desse item pelo cliente.',
      'Comprava→Agora': 'Quanto o cliente comprava do item antes vs. agora — mostra a queda de volume.'
    },
    metas: {
      'Meta': 'Meta do mês para a métrica (venda, rentabilidade, clientes ou mix).',
      'Realizado': 'Realizado no mês até agora.',
      'Realiz. venda': 'Venda realizada no mês até agora.',
      'Venda (R$)': 'Venda realizada no mês (R$).',
      'Rentab. (R$)': 'Rentabilidade (lucro) realizada no mês (R$).',
      'Falta': 'Quanto falta para bater a meta (0 se já bateu).',
      'Falta/dia': 'Quanto precisa vender por dia útil restante para fechar a meta.',
      'Projeção': 'Projeção de fechamento do mês pelo ritmo atual: realizado × dias úteis do mês ÷ dias úteis decorridos.',
      '%': '% da meta já realizada (realizado ÷ meta).',
      '% Proj.': '% da meta projetada para o fim do mês (projeção ÷ meta).',
      '% Margem': 'Margem = rentabilidade ÷ venda realizada (a mesma da coluna Realizado da aba Venda, com bonificação). Mesma régua do BI.',
      'Mix': 'Mix de produtos: nº de itens/produtos distintos vendidos. Não soma entre vendedores (é contagem distinta).',
      'Clientes': 'Nº de clientes distintos atendidos. Não soma entre vendedores (é contagem distinta).'
    },
    gerencial: {
      'Distribuição por faixa de recência': 'Carteira dividida por dias desde a última compra (0-15, 16-30… 91+).',
      'Ranking (pior → melhor)': 'Times e vendedores ordenados pela cobertura de clientes, do pior para o melhor.',
      'Faixa (dias s/ comprar)': 'Dias desde a última compra do cliente.',
      '% clientes': 'Participação da faixa no total de clientes da carteira.',
      '% valor': 'Participação da faixa no valor total da carteira (venda 12m).',
      'Valor vendido (12m)': 'Venda líquida dos clientes da faixa nos últimos 12 meses.',
      'Positivados / Carteira': 'Clientes que compraram na janela de cobertura (≤ dias configurados) ÷ total de clientes da carteira.',
      'Cobertura clientes': 'Clientes em dia (última compra dentro da janela) ÷ total de clientes.',
      'Cobertura valor': 'Valor dos clientes em dia ÷ valor total da carteira.',
      'Dentro do ciclo': 'Clientes dentro do próprio ciclo de compra — régua justa que não pune quem compra espaçado.',
      'Receita em risco': 'Soma da receita projetada perdida dos clientes atrasados (venda mensal × meses de atraso além do ciclo).',
      'Base morta': 'Clientes sem comprar há 91 dias ou mais.',
      '⚑': 'Marca times/vendedores com cobertura abaixo do limiar (alerta).'
    },
    carteira: {
      'Segmentação RFM (8 segmentos)': 'Clientes classificados por Recência, Frequência e Monetário em 8 segmentos (champions, fiéis, em risco, perdidos…).',
      'Receita líquida × Clientes positivados — últimos 12 meses': 'Evolução da receita e do nº de clientes que compraram, mês a mês.',
      'Detalhe do mês': 'Clientes e valores do mês selecionado no gráfico.',
      'Venda': 'Venda líquida do cliente no mês selecionado.',
      'Lucro': 'Lucro do cliente no mês selecionado.',
      'R (dias)': 'Recência: dias desde a última compra do cliente.',
      'F (12m)': 'Frequência: nº de compras do cliente nos últimos 12 meses.',
      'Média Venda': 'Venda média mensal do cliente (venda 12m ÷ 12).',
      '⚠ Receita Perdida proj.': 'Receita projetada perdida: venda mensal × meses de atraso além do ciclo (0 se ainda dentro do ciclo).',
      'Segmento': 'Segmento RFM do cliente (champion, fiel, em risco, perdido…).',
      'Time': 'Equipe/supervisor do vendedor do cliente.',
      'Últ. compra': 'Data da última compra do cliente.',
      'Ciclo': 'Ciclo pessoal de compra: mediana dos intervalos entre as compras do cliente (mínimo 7 dias).',
      'Previsão': 'Previsão do próximo pedido = última compra + ciclo pessoal.',
      'Atraso': 'Dias de atraso em relação ao ciclo (negativo = ainda dentro do ciclo).',
      'Receita em risco': 'Receita projetada perdida por atraso: venda mensal × meses de atraso além do ciclo.'
    },
    vendedores: {
      'Top 10 por Lucro 12m': 'Os 10 vendedores com maior lucro nos últimos 12 meses.',
      'Distribuição: Taxa de Positivação': 'Quantos vendedores caem em cada faixa de positivação (0-2%, 2-5%, 5-10%, 10-20%, 20%+).',
      'Ticket': 'Ticket médio de venda do vendedor (valor médio por cliente atendido).',
      'Time (Supervisor)': 'Supervisor/equipe do vendedor.',
      'Tipo': 'Tipo do RCA (ex.: vendedor externo, interno).',
      'Clientes': 'Nº de clientes distintos que compraram no período.'
    },
    vendedor: {
      '⚠ Alertas acionáveis': 'Clientes que precisam de contato agora (vencidos, em risco, com receita a perder).',
      'Série 12m (Venda + Lucro)': 'Venda e lucro do vendedor mês a mês nos últimos 12 meses.',
      'Sua carteira (RFM)': 'Segmentação RFM dos clientes do vendedor.',
      'Sua carteira (ordenada por receita perdida)': 'Clientes ordenados pela receita projetada perdida (quem está atrasado e vale mais aparece primeiro).',
      'Segmento': 'Segmento RFM do cliente (champion, fiel, em risco, perdido…).',
      'R (dias)': 'Recência: dias desde a última compra do cliente.',
      'F (12m)': 'Frequência: nº de compras do cliente nos últimos 12 meses.',
      '⚠ Receita Perdida': 'Receita projetada perdida: venda mensal × meses de atraso além do ciclo (0 se ainda dentro do ciclo).'
    },
    categorias: {
      'Treemap — Departamentos (tamanho = venda, cor = margem)': 'Cada bloco é um departamento: o tamanho é a venda 12m e a cor é a margem.',
      'Top 10 Fornecedores (12m)': 'Os 10 fornecedores com maior venda nos últimos 12 meses.',
      'Clientes únicos': 'Nº de clientes distintos que compraram do departamento (12m).',
      'Produtos únicos': 'Nº de produtos distintos vendidos no departamento (12m).',
      'Margem %': 'Margem = lucro ÷ venda do departamento.',
      'Share': 'Participação do departamento na venda total (12m).'
    },
    mix: {
      'Mix Abandonado': 'Pares cliente × departamento que o cliente comprava e parou — oportunidade de recompra.',
      'Dias parado': 'Dias desde a última compra desse departamento por esse cliente.',
      'Última compra': 'Data da última compra do departamento pelo cliente.',
      'Venda Cat 12m': 'Venda líquida do departamento por esse cliente nos últimos 12 meses.',
      'Lucro Cat 12m': 'Lucro do departamento por esse cliente nos últimos 12 meses.',
      'Time': 'Equipe/supervisor do vendedor do cliente.'
    },
    tendencias: {
      'Tendências — Cohort Retention': 'Retenção por safra: acompanha se os clientes que começaram a comprar em cada mês continuam comprando.',
      'Cohorts retidos no tempo': 'Cada linha é a safra do mês da 1ª compra; as colunas M+0, M+1, M+2… mostram o % da safra que voltou a comprar naquele mês.'
    },
    admin: {
      'Cobertura de carteira (Gerencial)': 'Placar de cobertura da carteira (mesma métrica da tela Gerencial).',
      'Usuários cadastrados': 'Usuários com acesso ao sistema e suas permissões.',
      'Novo usuário': 'Cadastro de um novo acesso ao sistema.',
      'Cron envio': 'Horário/frequência do envio automático de relatórios para o usuário.',
      'Função': 'Papel do usuário no sistema (admin, diretor, supervisor, vendedor…).',
      'Vendedor/Time': 'RCA ou equipe vinculada ao usuário — define o escopo que ele enxerga.'
    }
  };

  // ── helpers de normalização/lookup ──
  function norm(s) {
    return (s || '').replace(/[↑↓▲▼▾▴⌄►◄◂▸]/g, '').replace(/\s+/g, ' ').trim();
  }
  // texto "principal" do elemento: nós de texto do começo, parando no 1º filho-elemento
  // (ignora <small>/badges/ícone ⓘ já anexado); fallback = textContent inteiro.
  function labelText(el) {
    var t = '';
    for (var i = 0; i < el.childNodes.length; i++) {
      var n = el.childNodes[i];
      if (n.nodeType === 1) break;
      if (n.nodeType === 3) t += n.textContent;
    }
    t = norm(t);
    return t || norm(el.textContent);
  }
  function lookup(el, reg) {
    var key = el.getAttribute && el.getAttribute('data-tip-key');
    if (key) return reg[key] || COMMON[key] || null;
    var k1 = labelText(el);
    if (reg[k1] || COMMON[k1]) return reg[k1] || COMMON[k1];
    var k2 = norm(el.textContent);
    return reg[k2] || COMMON[k2] || null;
  }

  function makeIcon(tip) {
    var ic = document.createElement('span');
    ic.className = 'ttip';
    ic.setAttribute('data-tip', tip);
    ic.setAttribute('tabindex', '0');
    ic.setAttribute('aria-label', 'ajuda');
    ic.setAttribute('role', 'img');
    return ic;
  }

  var _pageReg = null;
  function decorate(root) {
    if (_pageReg === null) _pageReg = TIPS[(document.body && document.body.dataset.page) || ''] || {};
    var els = (root || document).querySelectorAll('thead th, h1, h2, h3');
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      if (el.querySelector && el.querySelector(':scope > .ttip')) continue; // já tem ⓘ
      var tip = lookup(el, _pageReg);
      if (!tip) continue;
      el.appendChild(makeIcon(tip));
    }
  }

  // ── caixinha única (position:fixed → nunca cortada por overflow das tabelas) ──
  function setupPop() {
    if (document.getElementById('tt-pop')) return;
    var pop = document.createElement('div');
    pop.id = 'tt-pop';
    document.body.appendChild(pop);
    var cur = null;
    function place(el) {
      var r = el.getBoundingClientRect(), m = 8, vw = window.innerWidth, vh = window.innerHeight;
      pop.style.left = '0px'; pop.style.top = '0px';           // reset p/ medir largura real
      var pw = pop.offsetWidth, ph = pop.offsetHeight;
      var left = Math.max(m, Math.min(r.left + r.width / 2 - pw / 2, vw - pw - m));
      var top = r.bottom + 6;
      if (top + ph > vh - m) top = r.top - ph - 6;             // sem espaço embaixo → acima
      pop.style.left = left + 'px'; pop.style.top = Math.max(m, top) + 'px';
    }
    function show(el) {
      var txt = el.getAttribute('data-tip'); if (!txt) return;
      cur = el; pop.textContent = txt; pop.classList.add('on'); place(el);
    }
    function hide() { cur = null; pop.classList.remove('on'); }
    function near(e) { return (e.target && e.target.closest) ? e.target.closest('.ttip') : null; }
    document.addEventListener('mouseover', function (e) { var t = near(e); if (t) show(t); });
    document.addEventListener('mouseout', function (e) { var t = near(e); if (t && t === cur) hide(); });
    document.addEventListener('focusin', function (e) { var t = near(e); if (t) show(t); });
    document.addEventListener('focusout', function (e) { var t = near(e); if (t && t === cur) hide(); });
    // clicar no ⓘ não deve disparar a ordenação da coluna (onclick do th, fase de bolha)
    document.addEventListener('click', function (e) { if (near(e)) { e.stopPropagation(); e.preventDefault(); } }, true);
    window.addEventListener('scroll', function () { if (cur) hide(); }, true);
    window.addEventListener('resize', function () { if (cur) hide(); });
  }

  var _raf = false;
  function init() {
    if (!document.body) return;
    setupPop();
    decorate(document);
    // tabelas/títulos gerados em JS (index tops, carteira detalhe, vendedores, tendencias):
    // re-decora quando o DOM muda. Se o JS reescrever o texto de um título e remover o ⓘ,
    // a próxima mutação o recoloca (o guard evita duplicar/loopar).
    var obs = new MutationObserver(function () {
      if (_raf) return;
      _raf = true;
      requestAnimationFrame(function () { _raf = false; decorate(document); });
    });
    obs.observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
