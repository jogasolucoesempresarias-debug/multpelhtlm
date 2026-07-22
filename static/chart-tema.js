'use strict';
/* Chart.js lendo a cor do tema.
 *
 * Antes cada página cravava  Chart.defaults.color = '#94a3b8'  e  borderColor = '#1e293b'  —
 * os valores de --text-dim e --border. Canvas não lê CSS, então esses defaults ignoravam o
 * tema: no claro, eixo e grade continuariam escuros.
 *
 * Este helper lê as variáveis já resolvidas do CSS (getComputedStyle) e alimenta o Chart.js.
 * Uma definição para as 9 páginas que usam gráfico. Chamar DEPOIS de o tema.css ter carregado
 * (é o caso: as páginas incluem o tema no <head>, este script roda no corpo/onload).
 */
(function () {
  function corDoTema(nome, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(nome).trim();
    return v || fallback;
  }

  window.aplicarTemaChart = function () {
    if (typeof Chart === 'undefined') return;
    Chart.defaults.color = corDoTema('--text-dim', '#94a3b8');
    Chart.defaults.borderColor = corDoTema('--border', '#1e293b');
    Chart.defaults.font.family = 'DM Sans, sans-serif';
  };

  window.aplicarTemaChart();
})();
