/* Drill 360° de cliente — auto-injeta markup do painel lateral.
   Importe com: <script defer src="/static/drill-cliente.js"></script>
   Expõe globalmente: abrirDrill(codcli), fecharDrill().
   Depende de Chart.js já carregado, e dos globals: BRL, BRL2, NUM, MESES, NOME_SEG (da página).
   _modo (string 'fixa'/'personalizada') também é lido se existir — fallback 'personalizada'. */

(function() {
  let _drillChart = null;

  function _injectMarkup() {
    if (document.getElementById('drillPanel')) return;
    const html = `
      <div id="backdrop" class="backdrop" onclick="fecharDrill()"></div>
      <aside id="drillPanel" class="side-panel">
        <div class="side-head">
          <div>
            <h2 id="drill_nome">—</h2>
            <div class="meta-cli" id="drill_meta">—</div>
          </div>
          <button onclick="fecharDrill()" title="Fechar (ESC)">×</button>
        </div>
        <div class="side-body">
          <section>
            <h3>Indicadores</h3>
            <div class="kv-grid" id="drill_kv"></div>
          </section>
          <section>
            <h3>Histórico mensal (12m)</h3>
            <div class="drill-chart-wrap"><canvas id="drillChart"></canvas></div>
          </section>
          <section>
            <h3>Top departamentos (12m)</h3>
            <div id="drill_categorias" style="font-size:0.82rem;"></div>
          </section>
        </div>
      </aside>
    `;
    document.body.insertAdjacentHTML('beforeend', html);
    document.addEventListener('keydown', e => { if (e.key === 'Escape') fecharDrill(); });
  }

  async function _fetchDrill(codcli) {
    const r = await fetch('/api/carteira/cliente/' + codcli, { credentials: 'same-origin' });
    if (r.status === 401) { window.location.href = '/login'; throw new Error('unauth'); }
    if (r.status === 403) throw new Error('forbidden');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }

  window.abrirDrill = async function(codcli) {
    _injectMarkup();
    document.getElementById('drill_nome').textContent = 'Carregando...';
    document.getElementById('drill_meta').textContent = '';
    document.getElementById('drill_kv').innerHTML = '';
    document.getElementById('drill_categorias').innerHTML = '';
    document.getElementById('drillPanel').classList.add('open');
    document.getElementById('backdrop').classList.add('show');

    try {
      const d = await _fetchDrill(codcli);
      const c = d.cliente;

      document.getElementById('drill_nome').textContent = c.cliente || ('Cliente #' + c.codcli);
      document.getElementById('drill_meta').textContent =
        `#${c.codcli} · ${c.cidade || '—'}/${c.uf || '—'} · RCA ${c.codusur || '—'} (${c.vendedor || '—'}) · ${c.telefone || '—'}`;

      document.getElementById('drill_kv').innerHTML = `
        <div class="k">Última compra</div><div class="v">${c.ultima_compra || '—'}</div>
        <div class="k">Dias sem comprar</div><div class="v">${c.recencia_dias != null ? c.recencia_dias : '—'}</div>
        <div class="k">Frequência 12m</div><div class="v">${NUM.format(c.frequencia_12m || 0)} compras</div>
        <div class="k">Ciclo pessoal</div><div class="v">${c.ciclo_pessoal ? c.ciclo_pessoal + ' dias' : '—'}</div>
        <div class="k">Venda 12m</div><div class="v">${BRL2.format(c.venda_12m || 0)}</div>
        <div class="k">Média Venda 12m</div><div class="v">${BRL2.format((c.venda_12m || 0) / 12)}</div>
        <div class="k">Lucro 12m</div><div class="v">${BRL2.format(c.lucro_12m || 0)}</div>
        <div class="k">Lucro perdido proj.</div><div class="v" style="color:var(--red);">${BRL2.format(c.lucro_perdido_proj || 0)}</div>
        <div class="k">RFM</div><div class="v">R=${c.r} F=${c.f} M=${c.m}</div>
        <div class="k">Segmento</div><div class="v"><span class="badge b-${c.segmento}">${(window.NOME_SEG && NOME_SEG[c.segmento]) || c.segmento}</span></div>
      `;

      // Histórico
      const hist = (d.historico || []).slice().sort((a, b) => (a.AnoMes || 0) - (b.AnoMes || 0));
      const labels = hist.map(r => {
        const am = String(r.AnoMes || '');
        return MESES[parseInt(am.slice(4, 6), 10)] + '/' + am.slice(2, 4);
      });
      if (_drillChart) _drillChart.destroy();
      _drillChart = new Chart(document.getElementById('drillChart'), {
        type: 'bar',
        data: {
          labels,
          datasets: [
            { label: 'Venda', data: hist.map(r => r.VendaLiquida), backgroundColor: 'rgba(56,189,248,0.6)' },
            { label: 'Lucro', data: hist.map(r => r.LucroTotal),   backgroundColor: 'rgba(52,211,153,0.6)' },
          ]
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: {
            legend: { position: 'bottom', labels: { boxWidth: 12 } },
            tooltip: { callbacks: { label: cc => cc.dataset.label + ': ' + BRL.format(cc.parsed.y) } }
          },
          scales: { y: { min: 0, ticks: { callback: v => BRL.format(v) } } }
        }
      });

      // Top departamentos (CODEPTO + nome textual). 'd.deptos' é o campo novo; mantém
      // fallback pra 'd.categorias' enquanto qualquer cache antigo expira.
      const lista_deptos = d.deptos || d.categorias || [];
      document.getElementById('drill_categorias').innerHTML = lista_deptos.slice(0, 5).map(cat => {
        const nome = cat.nome || ('Cat ' + (cat.CODCATEGORIA || cat.CODEPTO || '—'));
        const sem_nome = cat.tem_nome === false;
        return `
        <div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border);">
          <span ${sem_nome ? 'title="Departamento sem nome cadastrado no ERP"' : ''}>${nome}${sem_nome ? ' <span style="color:var(--text-dim);font-size:0.7rem;">(i)</span>' : ''}</span>
          <span style="font-family:'JetBrains Mono',monospace;">${BRL.format(cat.VendaLiquida || 0)} · lucro ${BRL.format(cat.LucroTotal || 0)}</span>
        </div>
        `;
      }).join('') || '<div style="color:var(--text-dim);">Sem dados de departamento.</div>';
    } catch (e) {
      document.getElementById('drill_nome').textContent = 'Erro ao carregar drill';
      console.error(e);
    }
  };

  window.fecharDrill = function() {
    const p = document.getElementById('drillPanel');
    const b = document.getElementById('backdrop');
    if (p) p.classList.remove('open');
    if (b) b.classList.remove('show');
  };
})();
