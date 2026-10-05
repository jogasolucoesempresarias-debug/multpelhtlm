/* ═══════════ Plano de ação por cliente (CRM leve) — componente ÚNICO para todas as listas ═══════════
   Pedido do João (29/09/2026): em toda lista de cliente, um botão de plano com status, data e uma
   breve descrição; o gestor vê quem recebeu tratativa, o vendedor tem o histórico. O acompanhamento
   ZERA quando o cliente compra (o servidor separa atual × anteriores pela data da última compra).

   Uso numa lista:   PlanoCliente.selo(codcli, resumo, nomeDoCliente)   → HTML do botão da linha
   Selos em lote:    await PlanoCliente.resumos([codcli, ...])          → {codcli: resumo}
   Ao salvar, dispara `plano:salvo` (detail = {codcli, resumo}) para a tela atualizar cards/filtros.

   Registrar tem de ser mais rápido que ligar: um toque no status grava (data de hoje, descrição
   opcional); só o "Retorno agendado" pede a data. */
(function () {
  'use strict';
  if (window.PlanoCliente) return;

  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const dataBR = (iso) => iso ? String(iso).slice(0, 10).split('-').reverse().join('/') : '—';
  const ICONE = { ligacao_feita: '📞', sem_contato: '📵', retorno_agendado: '⏰', pedido_prometido: '🤝',
                  nao_compra_mais: '⛔', transferir: '↪' };
  let _atual = null;           // {codcli, nome, el}

  function css() {
    if (document.getElementById('pc-css')) return;
    const st = document.createElement('style');
    st.id = 'pc-css';
    st.textContent = `
      .pc-selo{background:var(--surface2,#1a2235);border:1px solid var(--border,#1e293b);color:var(--text-dim,#94a3b8);
        border-radius:12px;padding:3px 9px;font-size:.72rem;cursor:pointer;white-space:nowrap;font-family:inherit}
      .pc-selo:hover{border-color:var(--accent,#38bdf8);color:var(--accent,#38bdf8)}
      .pc-selo.tem{color:var(--text,#e2e8f0)}
      .pc-selo.destaque{background:rgba(251,146,60,.15);border-color:var(--orange,#fb923c);color:var(--orange,#fb923c);font-weight:600}
      .pc-bd{position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:10000;display:none}
      .pc-bd.on{display:block}
      .pc-modal{position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);width:min(520px,calc(100vw - 24px));
        max-height:calc(100vh - 40px);overflow:auto;background:var(--surface,#111827);border:1px solid var(--border,#1e293b);
        border-radius:14px;z-index:10001;display:none;padding:16px 18px;color:var(--text,#e2e8f0)}
      .pc-modal.on{display:block}
      .pc-modal h4{margin:0 0 2px;font-size:1rem;color:var(--accent,#38bdf8)}
      .pc-modal .pc-sub{font-size:.75rem;color:var(--text-dim,#94a3b8);margin-bottom:12px}
      .pc-st{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin:8px 0}
      .pc-st button{background:var(--surface2,#1a2235);border:1px solid var(--border,#1e293b);color:var(--text,#e2e8f0);
        border-radius:9px;padding:9px 6px;font-size:.78rem;cursor:pointer;font-family:inherit}
      .pc-st button:hover{border-color:var(--accent,#38bdf8)}
      .pc-modal textarea{width:100%;box-sizing:border-box;min-height:54px;background:var(--surface2,#1a2235);color:var(--text,#e2e8f0);
        border:1px solid var(--border,#1e293b);border-radius:9px;padding:8px;font-family:inherit;font-size:.8rem;resize:vertical}
      .pc-ret{display:none;gap:8px;align-items:center;margin:6px 0;font-size:.8rem}
      .pc-ret.on{display:flex}
      .pc-ret input{background:var(--surface2,#1a2235);color:var(--text,#e2e8f0);border:1px solid var(--border,#1e293b);
        border-radius:8px;padding:6px 8px;color-scheme:dark light}
      .pc-ret button,.pc-fechar{background:var(--accent,#38bdf8);color:var(--sobre-accent,#04111f);border:none;border-radius:8px;
        padding:7px 12px;font-weight:600;cursor:pointer;font-family:inherit}
      .pc-fechar{float:right;background:transparent;color:var(--text-dim,#94a3b8);font-size:1rem;padding:2px 6px}
      .pc-h{font-size:.7rem;text-transform:uppercase;letter-spacing:.05em;color:var(--text-dim,#94a3b8);margin:14px 0 6px}
      .pc-item{border-left:2px solid var(--accent,#38bdf8);padding:4px 0 6px 10px;margin-bottom:6px;font-size:.8rem;position:relative}
      .pc-del{position:absolute;top:2px;right:0;background:transparent;border:none;color:var(--text-dim,#94a3b8);
        cursor:pointer;font-size:.85rem;padding:2px 6px;border-radius:6px;font-family:inherit}
      .pc-del:hover{color:var(--red,#f87171);background:rgba(248,113,113,.12)}
      .pc-item .m{font-size:.7rem;color:var(--text-dim,#94a3b8)}
      .pc-msg{font-size:.75rem;min-height:1em;margin-top:4px}
      .pc-modal details summary{cursor:pointer;font-size:.75rem;color:var(--text-dim,#94a3b8);margin-top:10px}`;
    document.head.appendChild(st);
  }

  function markup() {
    if (document.getElementById('pc-modal')) return;
    const bd = document.createElement('div');
    bd.className = 'pc-bd'; bd.id = 'pc-bd';
    bd.addEventListener('click', fechar);
    const m = document.createElement('div');
    m.className = 'pc-modal'; m.id = 'pc-modal';
    m.setAttribute('role', 'dialog');
    m.addEventListener('click', (e) => e.stopPropagation());
    document.body.appendChild(bd);
    document.body.appendChild(m);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') fechar(); });
  }

  function rotuloSelo(r) {
    if (!r || !r.n) return '+ Plano';
    if (r.retorno_situacao === 'hoje') return '⏰ Retorno hoje';
    if (r.retorno_situacao === 'atrasado') return '⏰ Retorno ' + dataBR(r.retorno) + ' (atrasado)';
    const u = r.ultimo || {};
    return (ICONE[u.status] || '•') + ' ' + esc(u.rotulo) + ' · ' + dataBR(u.status === 'retorno_agendado' ? r.retorno : u.data_acao)
      + (r.n > 1 ? ' (' + r.n + ')' : '');
  }

  function selo(codcli, resumo, nome) {
    css();                     // o selo aparece ANTES de qualquer clique — sem isto saía com o estilo cru do navegador
    const r = resumo || {};
    const cls = 'pc-selo' + (r.n ? ' tem' : '') + (r.destaque ? ' destaque' : '');
    const tip = r.n ? 'Plano de ação — ' + r.n + ' registro(s) desde a última compra' : 'Registrar a ação com este cliente';
    return `<button type="button" class="${cls}" data-pc="${codcli}" data-nome="${esc(nome || '')}" title="${esc(tip)}"`
      + ` onclick="event.stopPropagation();PlanoCliente.abrir(${Number(codcli)}, this)">${rotuloSelo(r)}</button>`;
  }

  async function resumos(codclis) {
    const ids = (codclis || []).filter((c) => c != null);
    if (!ids.length) return {};
    const r = await fetch('/api/plano/resumo', { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ codclis: ids }) });
    const j = await r.json().catch(() => ({}));
    return (j && j.resumos) || {};
  }

  function itemHTML(x) {
    const quando = x.status === 'retorno_agendado' ? 'para ' + dataBR(x.data_acao) : dataBR(x.data_acao);
    // ✕ só aparece para quem pode excluir (autor até 24 h, admin sempre) — o servidor confere de novo
    const del = x.pode_excluir
      ? `<button type="button" class="pc-del" title="Excluir este registro (preenchido errado)" onclick="PlanoCliente._excluir(${Number(x.id)})">✕</button>`
      : '';
    return `<div class="pc-item">${del}<b>${ICONE[x.status] || '•'} ${esc(x.rotulo)}</b> · ${quando}`
      + (x.descricao ? `<div>${esc(x.descricao)}</div>` : '')
      + `<div class="m">${esc(x.autor_nome)} · registrado em ${dataBR(x.criado_em)} ${String(x.criado_em || '').slice(11, 16)}</div></div>`;
  }

  function render(j) {
    const m = document.getElementById('pc-modal');
    const botoes = (j.status_opcoes || []).map((s) =>
      `<button type="button" onclick="PlanoCliente._clicar('${s.codigo}')">${ICONE[s.codigo] || ''} ${esc(s.rotulo)}</button>`).join('');
    const atual = (j.atual || []).length ? j.atual.map(itemHTML).join('')
      : '<div class="pc-sub">Nenhuma ação registrada desde a última compra.</div>';
    const ant = (j.anteriores || []).length
      ? `<details><summary>Acompanhamentos anteriores (${j.anteriores.length}) — antes da última compra</summary>${j.anteriores.map(itemHTML).join('')}</details>`
      : '';
    m.innerHTML = `<button type="button" class="pc-fechar" onclick="PlanoCliente.fechar()" title="Fechar">✕</button>
      <h4>Plano de ação</h4>
      <div class="pc-sub">${esc(j.cliente || _atual.nome || '')} · cod ${j.codcli} · última compra ${dataBR(j.ultima_compra)}</div>
      <textarea id="pc-desc" maxlength="500" placeholder="Descrição (opcional) — o que foi combinado"></textarea>
      <div class="pc-st">${botoes}</div>
      <div class="pc-ret" id="pc-ret"><span>Retornar em</span><input type="date" id="pc-data" min="${j.hoje}" value="${j.hoje}">
        <button type="button" onclick="PlanoCliente._agendar()">Agendar</button></div>
      <div class="pc-msg" id="pc-msg"></div>
      <div class="pc-h">Acompanhamento atual</div>${atual}${ant}`;
  }

  async function carregar(codcli) {
    const r = await fetch('/api/plano/' + codcli, { credentials: 'same-origin' });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || !j.ok) throw new Error(j.error || ('erro ' + r.status));
    return j;
  }

  async function abrir(codcli, el) {
    css(); markup();
    _atual = { codcli, el, nome: el && el.dataset ? el.dataset.nome : '' };
    const m = document.getElementById('pc-modal');
    m.innerHTML = '<div class="pc-sub">Carregando…</div>';
    m.classList.add('on');
    document.getElementById('pc-bd').classList.add('on');
    try { render(await carregar(codcli)); }
    catch (e) { m.innerHTML = `<button type="button" class="pc-fechar" onclick="PlanoCliente.fechar()">✕</button><div class="pc-msg" style="color:var(--red,#f87171)">Não consegui abrir o plano: ${esc(e.message)}</div>`; }
  }

  function fechar() {
    const m = document.getElementById('pc-modal');
    if (m) m.classList.remove('on');
    const bd = document.getElementById('pc-bd');
    if (bd) bd.classList.remove('on');
  }

  async function salvar(corpo) {
    const msg = document.getElementById('pc-msg');
    msg.textContent = 'Salvando…'; msg.style.color = '';
    try {
      const r = await fetch('/api/plano/' + _atual.codcli, { method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(corpo) });
      const j = await r.json().catch(() => ({}));
      if (!r.ok || !j.ok) throw new Error(j.error || ('erro ' + r.status));
      render(j);
      document.getElementById('pc-msg').textContent = '✓ Registrado';
      if (_atual.el && _atual.el.isConnected) _atual.el.outerHTML = selo(_atual.codcli, j.resumo, _atual.nome);
      document.dispatchEvent(new CustomEvent('plano:salvo', { detail: { codcli: _atual.codcli, resumo: j.resumo } }));
    } catch (e) {
      msg.textContent = 'Não salvou: ' + e.message; msg.style.color = 'var(--red,#f87171)';
    }
  }

  async function _excluir(id) {
    if (!confirm('Excluir este registro do plano? Use para corrigir um registro preenchido errado.')) return;
    const msg = document.getElementById('pc-msg');
    try {
      const r = await fetch('/api/plano/' + _atual.codcli + '/' + id, { method: 'DELETE', credentials: 'same-origin' });
      const j = await r.json().catch(() => ({}));
      if (!r.ok || !j.ok) throw new Error(j.error || ('erro ' + r.status));
      render(j);
      document.getElementById('pc-msg').textContent = '✓ Excluído';
      if (_atual.el && _atual.el.isConnected) _atual.el.outerHTML = selo(_atual.codcli, j.resumo, _atual.nome);
      document.dispatchEvent(new CustomEvent('plano:salvo', { detail: { codcli: _atual.codcli, resumo: j.resumo } }));
    } catch (e) {
      msg.textContent = 'Não excluiu: ' + e.message; msg.style.color = 'var(--red,#f87171)';
    }
  }

  function _clicar(status) {
    if (status === 'retorno_agendado') {       // só o retorno pede a data
      document.getElementById('pc-ret').classList.add('on');
      document.getElementById('pc-data').focus();
      return;
    }
    salvar({ status, descricao: document.getElementById('pc-desc').value });
  }

  function _agendar() {
    salvar({ status: 'retorno_agendado', data_acao: document.getElementById('pc-data').value,
             descricao: document.getElementById('pc-desc').value });
  }

  window.PlanoCliente = { selo, resumos, abrir, fechar, _clicar, _agendar, _excluir };
  css();                       // e já no carregamento do script (a lista pode renderizar por outro caminho)
})();
