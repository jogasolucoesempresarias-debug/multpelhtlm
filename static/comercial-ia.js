/* ═══════════════════ Agente de IA — chat do módulo COMERCIAL (gestão) ═══════════════════
   Carregado SÓ pelo joga-header.js, e só quando o /api/me diz que a instância tem `ia` no
   MODULOS e a pessoa tem a área Comercial. Na Multpel (estado off) este arquivo nem é baixado:
   nenhum botão, nenhuma requisição a /api/ia/*.

   O contexto é montado no SERVIDOR (ia_comercial.py + server._iacom_panorama), a partir das
   MESMAS rotas das telas, no escopo da pessoa. O front não monta payload: manda a pergunta, a
   TELA e os filtros visíveis — que só escolhem sugestões e são declarados ao modelo.

   ⚠️ O panorama é lento com cache frio (~45 s no BI real: a Recuperação sozinha leva ~43 s).
   Por isso ele é aquecido ao ABRIR o chat, com "carregando" na tela, e não na 1ª pergunta. */
(function () {
  'use strict';
  if (window.__comercialIA) return;
  window.__comercialIA = true;

  const IA = { modulo: false, on: false, upsell: null, modelo: null, hist: [], pronto: false, carregando: false };
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  /* Markdown mínimo: ESCAPA primeiro, só então formata (a resposta é texto de um modelo). */
  function md(t) {
    let h = esc(t || '');
    h = h.replace(/`([^`\n]+)`/g, '<code>$1</code>');
    h = h.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    h = h.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    const out = [];
    let ul = false;
    for (const l of h.split('\n')) {
      const m = l.match(/^\s*[-•]\s+(.*)$/);
      if (m) { if (!ul) { out.push('<ul>'); ul = true; } out.push('<li>' + m[1] + '</li>'); continue; }
      if (ul) { out.push('</ul>'); ul = false; }
      if (l.trim()) out.push('<p style="margin:0 0 6px">' + l + '</p>');
    }
    if (ul) out.push('</ul>');
    return out.join('');
  }

  function tela() { return location.pathname || '/'; }

  /* Filtros VISÍVEIS: querystring + selects com valor. Só são DECLARADOS ao modelo ("o panorama é
     do seu escopo inteiro, a tela está filtrada em X") — não recortam o contexto nesta fase. */
  function filtros() {
    const f = {};
    new URLSearchParams(location.search).forEach((v, k) => { if (v) f[k] = v; });
    document.querySelectorAll('select[id]').forEach((s) => {
      if (Object.keys(f).length >= 8 || s.closest('#cia-box')) return;
      if (!s.value || s.offsetParent === null) return;
      const op = s.options[s.selectedIndex];
      if (s.selectedIndex === 0 && !s.value) return;
      f[s.id] = op ? op.text.trim().slice(0, 60) : s.value;
    });
    return f;
  }

  function montar() {
    const fab = document.createElement('button');
    fab.id = 'cia-fab'; fab.className = 'cia-fab'; fab.type = 'button'; fab.hidden = true;
    fab.addEventListener('click', toggle);
    const box = document.createElement('div');
    box.id = 'cia-box'; box.className = 'cia-box';
    box.innerHTML =
      '<div class="cia-head"><h4 id="cia-titulo">Analista Comercial</h4>' +
      '<button type="button" id="cia-fechar" title="Fechar">✕</button></div>' +
      '<div class="cia-body" id="cia-body"></div>' +
      '<div class="cia-in" id="cia-in"><input id="cia-input" maxlength="500" autocomplete="off" ' +
      'placeholder="Pergunte sobre carteira, recuperação, metas, performance…">' +
      '<button type="button" id="cia-send">Enviar</button></div>' +
      '<div class="cia-pe" id="cia-pe"></div>';
    document.body.appendChild(fab);
    document.body.appendChild(box);
    $('#cia-fechar').addEventListener('click', toggle);
    $('#cia-send').addEventListener('click', enviar);
    $('#cia-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') enviar(); });
  }

  function msg(txt, tipo) {
    const b = $('#cia-body');
    const d = document.createElement('div');
    d.className = 'cia-msg ' + tipo;
    if (tipo === 'bot') d.innerHTML = md(txt); else d.textContent = txt;
    b.appendChild(d);
    b.scrollTop = b.scrollHeight;
    return d;
  }

  async function status() {
    try {
      const r = await fetch('/api/ia/status', { credentials: 'same-origin' });
      const j = r.ok ? await r.json() : {};
      IA.modulo = !!j.modulo; IA.on = !!j.disponivel; IA.upsell = j.upsell; IA.modelo = j.modelo;
    } catch (e) { IA.modulo = false; }
    const fab = $('#cia-fab');
    if (!IA.modulo) { fab.hidden = true; return; }
    fab.hidden = false;
    fab.classList.toggle('travado', !IA.on);
    fab.textContent = IA.on ? '\u{1F4AC}' : '\u{1F512}';
    fab.title = IA.on ? 'Analista Comercial (IA)' : 'Agente de IA especialista — não incluído no seu plano';
  }

  function oferta() {
    const u = IA.upsell || {};
    $('#cia-titulo').textContent = u.titulo || 'Agente de IA especialista';
    $('#cia-in').hidden = true; $('#cia-pe').hidden = true;
    $('#cia-body').innerHTML = '<div class="cia-oferta"><div class="ic">\u{1F916}</div><h5>' +
      esc(u.titulo || 'Agente de IA especialista') + '</h5><p>' + md(u.texto || '') + '</p><div class="cta">' +
      esc(u.cta || 'Fale com o administrador da sua conta.') + '</div></div>';
  }

  /* Aquece o panorama. Com cache frio leva até ~1 min — a pessoa vê o que está acontecendo. */
  async function carregar() {
    if (IA.carregando) return;
    IA.carregando = true;
    const aviso = msg('', 'sis');
    aviso.innerHTML = '<span class="cia-typing"><span></span><span></span><span></span></span> ' +
      'Lendo os números do painel no seu escopo… na primeira abertura do dia pode levar até 1 minuto.';
    try {
      const r = await fetch('/api/ia/contexto?tela=' + encodeURIComponent(tela()), { credentials: 'same-origin' });
      const j = await r.json().catch(() => ({}));
      if (r.status === 402) { IA.on = false; IA.upsell = j.upsell || IA.upsell; oferta(); return; }
      if (!r.ok || !j.ok) throw new Error(j.error || ('erro ' + r.status));
      aviso.remove();
      IA.pronto = true;
      const tem = (j.indice || []).join('; ');
      msg('Sou o analista do Comercial. Leio os números **no seu escopo de acesso**' +
          (tem ? ' — ' + tem : '') + ', e consulto qualquer **vendedor, time ou cliente** pelo código ou nome.', 'bot');
      if ((j.indisponivel || []).length) {
        msg('Fora do ar agora: ' + j.indisponivel.join(', ') + '. Respondo com o resto.', 'sis');
      }
      sugestoes(j.sugestoes);
    } catch (e) {
      aviso.textContent = 'Não consegui ler os números agora (' + (e.message || 'erro') + '). Tente de novo em instantes.';
    } finally {
      IA.carregando = false;
    }
  }

  function sugestoes(lista) {
    const antigo = document.getElementById('cia-sug-box');
    if (antigo) antigo.remove();
    if (!lista || !lista.length) return;
    const wrap = document.createElement('div');
    wrap.className = 'cia-sug'; wrap.id = 'cia-sug-box';
    lista.forEach((s) => {
      const b = document.createElement('button');
      b.type = 'button'; b.textContent = s;
      b.addEventListener('click', () => {
        const inp = $('#cia-input');
        inp.value = s;
        // "Explique este número: " é um convite a completar, não uma pergunta pronta
        if (/:\s*$/.test(s)) { inp.focus(); inp.setSelectionRange(s.length, s.length); return; }
        enviar();
      });
      wrap.appendChild(b);
    });
    $('#cia-body').appendChild(wrap);
    $('#cia-body').scrollTop = $('#cia-body').scrollHeight;
  }

  /* As sugestões VOLTAM depois de cada resposta (lição do Compras): é logo depois da 1ª resposta
     que a pessoa quer saber o que MAIS dá para perguntar. O panorama já está em cache: é barato. */
  async function recarregarSugestoes() {
    try {
      const r = await fetch('/api/ia/contexto?tela=' + encodeURIComponent(tela()), { credentials: 'same-origin' });
      const j = await r.json();
      if (j.ok) sugestoes(j.sugestoes);
    } catch (e) { /* sugestão é acessório */ }
  }

  function toggle() {
    const box = $('#cia-box'), fab = $('#cia-fab');
    const aberto = box.classList.toggle('aberto');
    fab.classList.toggle('aberto', aberto);
    if (!aberto) return;
    if (!IA.on) { oferta(); return; }
    $('#cia-titulo').textContent = 'Analista Comercial';
    $('#cia-in').hidden = false; $('#cia-pe').hidden = false;
    $('#cia-pe').textContent = 'Lê os números do seu escopo · ' + (IA.modelo || 'IA');
    if (!IA.pronto) carregar();
    setTimeout(() => { const i = $('#cia-input'); if (i) i.focus(); }, 80);
  }

  async function enviar() {
    const inp = $('#cia-input'), btn = $('#cia-send');
    const pergunta = (inp.value || '').trim();
    if (!pergunta || btn.disabled) return;
    const sug = document.getElementById('cia-sug-box');
    if (sug) sug.remove();
    msg(pergunta, 'user');
    inp.value = '';
    btn.disabled = true;
    const typing = msg('', 'bot');
    typing.innerHTML = '<span class="cia-typing"><span></span><span></span><span></span></span>' +
      (IA.pronto ? '' : ' <small>lendo o painel…</small>');
    let resposta = '', alvo = null;
    try {
      const r = await fetch('/api/ia/chat', {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pergunta: pergunta, historico: IA.hist.slice(-6), tela: tela(), filtros: filtros() })
      });
      if (r.status === 402) {
        typing.remove(); IA.on = false;
        const j = await r.json().catch(() => ({}));
        IA.upsell = j.upsell || IA.upsell; oferta(); return;
      }
      if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.error || ('erro ' + r.status)); }
      IA.pronto = true;
      const reader = r.body.getReader(), dec = new TextDecoder();
      let buf = '';
      for (;;) {
        const passo = await reader.read();
        if (passo.done) break;
        buf += dec.decode(passo.value, { stream: true });
        const partes = buf.split('\n\n');
        buf = partes.pop() || '';
        for (const linha of partes) {
          if (linha.indexOf('data: ') !== 0) continue;
          const raw = linha.slice(6);
          if (raw === '[DONE]') continue;
          let obj; try { obj = JSON.parse(raw); } catch (e) { continue; }
          if (obj.status && !alvo) {
            typing.innerHTML = '<span class="cia-typing"><span></span><span></span><span></span></span> <small>' +
              esc(obj.status) + '</small>';
            continue;
          }
          if (obj.erro) { (alvo || typing).innerHTML = md('**Erro:** ' + obj.erro); return; }
          if (obj.token) {
            if (!alvo) { typing.remove(); alvo = msg('', 'bot'); }
            resposta += obj.token;
            alvo.innerHTML = md(resposta);
            $('#cia-body').scrollTop = $('#cia-body').scrollHeight;
          }
        }
      }
      if (!alvo) typing.remove();
      if (resposta) {
        IA.hist.push({ role: 'user', content: pergunta });
        IA.hist.push({ role: 'assistant', content: resposta });
      }
      recarregarSugestoes();
    } catch (e) {
      if (typing.isConnected) typing.remove();
      if (!alvo) alvo = msg('', 'bot');
      alvo.innerHTML = md('**Não consegui responder agora.** ' + (e.message || ''));
    } finally {
      btn.disabled = false;
      inp.focus();
    }
  }

  function css() {
    if (document.getElementById('cia-css')) return;
    const l = document.createElement('link');
    l.id = 'cia-css'; l.rel = 'stylesheet'; l.href = '/static/comercial-ia.css';
    document.head.appendChild(l);
  }

  function iniciar() { css(); montar(); status(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', iniciar);
  else iniciar();
})();
