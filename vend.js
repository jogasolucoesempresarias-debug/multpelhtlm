const BRL = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 });
const BRL2 = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' });
const NUM = new Intl.NumberFormat('pt-BR');
const PCT = (v) => (v == null ? '—' : (v * 100).toFixed(1).replace('.', ',') + '%');

Chart.defaults.font.family = "'DM Sans', sans-serif";
Chart.defaults.color = '#94a3b8';
Chart.defaults.borderColor = '#1e293b';

let _todos = [];
let _ordem = { campo: 'rank', dir: 'asc' };
let _filtros = { tipovend: 'R', supervisor: '', uf: '', busca: '', internos: false };
let _charts = {};

// fetchJSON vem do /static/fetch-resiliente.js (retry + backoff + timeout)
// Wrapper local pra preservar UX de 403 (innerHTML custom).
// IMPORTANTE: usar arrow function (não `async function fetchJSON`) — declarações de função
// são HOISTED e sobrescrevem window.fetchJSON antes do const capturar, causando recursão infinita.
const _fetchJSON_shared = window.fetchJSON;
const fetchJSON = async (url, opts) => {
  try {
    return await _fetchJSON_shared(url, opts);
  } catch (e) {
    if (e && e.message === 'forbidden') {
      document.body.innerHTML = '<div style="padding:40px;color:#f87171;font-family:sans-serif;">Você não tem permissão pra ver vendedores.</div>';
    }
    throw e;
  }
};

async function loadMe() {
  try {
    const j = await fetchJSON('/api/me');
    document.getElementById('userInfo').textContent = `${j.nome} · ${j.role}`;
    if (j.role === 'admin') document.getElementById('linkAdmin').classList.remove('hidden');
  } catch (e) {}
}

function drawChart(id, cfg) {
  if (_charts[id]) _charts[id].destroy();
  _charts[id] = new Chart(document.getElementById(id), cfg);
}

async function loadVendedores() {
  const params = new URLSearchParams({
    tipovend: _filtros.tipovend,
    incluir_internos: _filtros.internos ? 'true' : 'false',
  });
  if (_filtros.supervisor) params.set('supervisor', _filtros.supervisor);
  if (_filtros.uf) params.set('uf', _filtros.uf);
  if (_filtros.busca) params.set('busca', _filtros.busca);

  const r = await fetchJSON('/api/vendedores?' + params.toString());
  _todos = r.vendedores || [];
  popularFiltrosDinamicos();
  renderTudo();
}

let _supMap = {};  // {codsupervisor_str: {nome, tipo}}

async function loadSupervisores() {
  try {
    const j = await fetchJSON('/api/_internal/supervisores-map');
    _supMap = j.supervisores || {};
  } catch (e) { console.warn('supervisores-map:', e); }
}

function extrairCodigo(valor) {
  if (!valor) return '';
  const m = String(valor).match(/\((\d+)\)\s*$/);
  if (m) return m[1];
  if (/^\d+$/.test(valor.trim())) return valor.trim();
  return '';
}

function popularFiltrosDinamicos() {
  // Supervisor: pega únicos da lista carregada + enriquece com nome real (datalist)
  const dl_sup = document.getElementById('lista_supervisores');
  const sups = [...new Set(_todos.map(v => v.codsupervisor).filter(Boolean))]
    .sort((a, b) => {
      const na = (_supMap[String(a)] && _supMap[String(a)].nome) || ('Sup ' + a);
      const nb = (_supMap[String(b)] && _supMap[String(b)].nome) || ('Sup ' + b);
      return na.localeCompare(nb);
    });
  dl_sup.innerHTML = sups.map(s => {
    const nome = (_supMap[String(s)] && _supMap[String(s)].nome) || ('Sup ' + s);
    return `<option value="${nome} (${s})">`;
  }).join('');

  // UF: select clássico (poucas opções)
  const sel_uf = document.getElementById('filt_uf');
  if (sel_uf.options.length <= 1) {
    const ufs = [...new Set(_todos.map(v => v.estado).filter(Boolean))].sort();
    sel_uf.innerHTML = '<option value="">Todas</option>' + ufs.map(u => `<option value="${u}">${u}</option>`).join('');
  }
}

function renderTudo() {
  // Ordena cópia
  const ord = [..._todos].sort((a,b) => {
    const va = a[_ordem.campo]; const vb = b[_ordem.campo];
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    if (typeof va === 'string') return _ordem.dir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
    return _ordem.dir === 'asc' ? va - vb : vb - va;
  });
  renderTabela(ord);
  renderTop10(ord);
  renderHistPos(ord);
  document.getElementById('tabela_total').textContent = NUM.format(_todos.length);
}

function renderTabela(rows) {
  const maxLucro = Math.max(1, ...rows.map(v => v.lucro || 0));
  const tbody = document.getElementById('tbody');
  tbody.innerHTML = rows.map(v => {
    const ratio = (v.lucro || 0) / maxLucro;
    const cor = ratio > 0.7 ? 'rgba(52,211,153,0.18)' : ratio > 0.3 ? 'rgba(251,191,36,0.15)' : 'rgba(248,113,113,0.13)';
    const yoy = v.yoy_receita;
    const yoyStr = yoy == null ? '—' : (yoy >= 0 ? '<span style="color:var(--green);">↗ ' : '<span style="color:var(--red);">↘ ') + PCT(yoy) + '</span>';
    return `
      <tr style="background-image:linear-gradient(90deg, ${cor} 0%, ${cor} ${Math.round(ratio*100)}%, transparent ${Math.round(ratio*100)}%, transparent 100%);">
        <td><strong>${v.rank}</strong></td>
        <td><a href="/vendedor/${v.codusur}">${v.nome}</a></td>
        <td><span class="badge">${v.tipo || '—'}</span></td>
        <td><span style="font-size:0.74rem;color:var(--text-dim);">${(_supMap[String(v.codsupervisor)] && _supMap[String(v.codsupervisor)].nome) || (v.codsupervisor ? 'Sup ' + v.codsupervisor : '—')}</span></td>
        <td>${v.estado || '—'}</td>
        <td class="num">${BRL.format(v.venda_liq || 0)}</td>
        <td class="num"><strong>${BRL.format(v.lucro || 0)}</strong></td>
        <td class="num">${BRL2.format(v.ticket_medio || 0)}</td>
        <td class="num">${PCT(v.taxa_positivacao)}</td>
        <td class="num">${NUM.format(v.clientes_unicos || 0)}</td>
        <td class="num">${yoyStr}</td>
      </tr>
    `;
  }).join('');
}

function renderTop10(rows) {
  const top = [...rows].sort((a,b) => (b.lucro||0) - (a.lucro||0)).slice(0, 10);
  drawChart('chartTop10', {
    type: 'bar',
    data: {
      labels: top.map(v => v.nome.slice(0, 22) + (v.nome.length > 22 ? '…' : '')),
      datasets: [{ label: 'Lucro 12m', data: top.map(v => v.lucro), backgroundColor: '#34d399' }]
    },
    options: {
      indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => BRL.format(c.parsed.x) } } },
      scales: { x: { ticks: { callback: v => BRL.format(v) } } }
    }
  });
}

function renderHistPos(rows) {
  // Bins de taxa de positivação: 0-2%, 2-5%, 5-10%, 10-20%, 20%+
  const bins = [{label: '0-2%', min: 0, max: 0.02, count: 0}, {label: '2-5%', min: 0.02, max: 0.05, count: 0},
                {label: '5-10%', min: 0.05, max: 0.1, count: 0}, {label: '10-20%', min: 0.1, max: 0.2, count: 0},
                {label: '20%+', min: 0.2, max: 999, count: 0}];
  for (const v of rows) {
    const t = v.taxa_positivacao || 0;
    for (const b of bins) {
      if (t >= b.min && t < b.max) { b.count++; break; }
    }
  }
  drawChart('chartHistPos', {
    type: 'bar',
    data: {
      labels: bins.map(b => b.label),
      datasets: [{ label: 'Qtd vendedores', data: bins.map(b => b.count), backgroundColor: '#38bdf8' }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { y: { ticks: { precision: 0 } } }
    }
  });
}

function aplicarFiltros() {
  _filtros.tipovend = document.getElementById('filt_tipovend').value;
  _filtros.supervisor = extrairCodigo(document.getElementById('filt_supervisor').value);
  _filtros.uf = document.getElementById('filt_uf').value;
  _filtros.internos = document.getElementById('filt_internos').checked;
  loadVendedores();
}

let _debounceId;
function debouncedFilter() {
  clearTimeout(_debounceId);
  _debounceId = setTimeout(() => {
    _filtros.busca = document.getElementById('filt_busca').value;
    loadVendedores();
  }, 300);
}

function limparFiltros() {
  document.getElementById('filt_tipovend').value = 'R';
  document.getElementById('filt_supervisor').value = '';
  document.getElementById('filt_uf').value = '';
  document.getElementById('filt_busca').value = '';
  document.getElementById('filt_internos').checked = false;
  _filtros = { tipovend: 'R', supervisor: '', uf: '', busca: '', internos: false };
  loadVendedores();
}

function ordenarPor(campo) {
  if (_ordem.campo === campo) {
    _ordem.dir = _ordem.dir === 'asc' ? 'desc' : 'asc';
  } else {
    _ordem.campo = campo;
    _ordem.dir = (campo === 'rank' || campo === 'nome') ? 'asc' : 'desc';
  }
  renderTudo();
}

async function carregarTudo() {
  try {
    await loadMe();
    await loadSupervisores();   // popula _supMap antes do dropdown
    await loadVendedores();
    document.getElementById('loading').classList.add('hidden');
    document.getElementById('app').classList.remove('hidden');
  } catch (e) {
    console.error('Falha em carregarTudo:', e);
    const msg = (e && e.message) ? e.message : String(e);
    document.getElementById('loading').innerHTML = `
      <div style="color:var(--red); max-width:480px; text-align:center; padding:20px;">
        <div style="font-size:1.05rem; margin-bottom:8px;">Erro ao carregar vendedores</div>
        <div style="color:var(--text-dim); font-size:0.85rem; font-family:'JetBrains Mono',monospace; word-break:break-word;">${msg}</div>
        <button onclick="location.reload()" style="margin-top:14px; padding:8px 16px; background:var(--accent); color:var(--bg); border:none; border-radius:6px; cursor:pointer; font-weight:600;">Tentar novamente</button>
      </div>`;
  }
}

carregarTudo();