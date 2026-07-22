'use strict';
/* Chart.js lendo a cor do tema.
 *
 * Antes cada página cravava  Chart.defaults.color = '#94a3b8'  e  borderColor = '#1e293b'  —
 * os valores de --text-dim e --border. Canvas não lê CSS, então esses defaults ignoravam o
 * tema: no claro, eixo e grade continuariam escuros.
 *
 * ⚠️ ORDEM IMPORTA: este script tem de rodar DEPOIS do tema.css e do script anti-piscada (que
 * define data-tema). Se rodar antes, getComputedStyle('--border') volta vazio, cai no fallback
 * escuro e a grade sai escura mesmo no tema claro — foi o bug que deixou os gráficos feios.
 */
(function () {
  function corDoTema(nome, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(nome).trim();
    return v || fallback;
  }

  window.aplicarTemaChart = function () {
    if (typeof Chart === 'undefined') return;
    const txt = corDoTema('--text-dim', '#94a3b8');
    const grade = corDoTema('--border', '#1e293b');

    // 1) Defaults — valem para os gráficos criados daqui pra frente.
    Chart.defaults.color = txt;
    Chart.defaults.borderColor = grade;
    Chart.defaults.font.family = 'DM Sans, sans-serif';

    // 2) Gráficos JÁ renderizados — para a troca de tema ao vivo repintar a grade/eixo sem
    //    precisar recarregar a página. Cada chart pode ter cor própria nas opções, então
    //    sobrescrevemos scale.grid/ticks e mandamos atualizar sem animação.
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
})();
