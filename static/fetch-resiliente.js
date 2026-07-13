/**
 * fetchJSON resiliente — retry com backoff + timeout + abort.
 * Exposto SOMENTE via window.fetchJSON (IIFE evita poluir global scope).
 * Usado por todas as 7 telas.
 *
 * IMPORTANTE: O wrap em IIFE é obrigatório. Sem ele, `async function fetchJSON` aqui
 * vira global, e qualquer página que declare `const fetchJSON = ...` quebra com
 * "SyntaxError: Identifier 'fetchJSON' has already been declared".
 *
 * Comportamento:
 *  - 401 → redirect /login
 *  - 403 com {redirect} → redirect destino; senão throw 'forbidden'
 *  - 5xx, 408, 429 ou timeout → retry com backoff 2s/4s/8s (3 tentativas)
 *  - Outras 4xx → throw imediato (não adianta retry)
 */
(function () {
  async function _fetchJSONResiliente(url, opts) {
    opts = opts || {};
    const tentativas = opts.tentativas || 3;
    const timeoutMs = opts.timeoutMs || 90000;
    let ultErro = null;

    for (let t = 0; t < tentativas; t++) {
      const ctrl = new AbortController();
      const tid = setTimeout(() => ctrl.abort(), timeoutMs);
      try {
        const r = await fetch(url, { credentials: 'same-origin', signal: ctrl.signal });
        clearTimeout(tid);

        if (r.status === 401) {
          window.location.href = '/login';
          throw new Error('unauth');
        }
        if (r.status === 403) {
          const j = await r.json().catch(() => ({}));
          if (j.redirect) { window.location.href = j.redirect; throw new Error('forbidden'); }
          throw new Error('forbidden');
        }
        if (r.status >= 500 || r.status === 408 || r.status === 429) {
          ultErro = new Error('HTTP ' + r.status);
          if (t < tentativas - 1) {
            await new Promise(res => setTimeout(res, 2000 * Math.pow(2, t)));
            continue;
          }
          throw ultErro;
        }
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      } catch (e) {
        clearTimeout(tid);
        if (e.message === 'unauth' || e.message === 'forbidden') throw e;
        ultErro = e;
        if (t < tentativas - 1) {
          await new Promise(res => setTimeout(res, 2000 * Math.pow(2, t)));
        }
      }
    }
    throw ultErro || new Error('Falha após múltiplas tentativas');
  }

  window.fetchJSON = _fetchJSONResiliente;
})();

/**
 * Injeta "BI atualizado em dd/mm/aaaa hh:mm" no cabeçalho de QUALQUER página que
 * inclua este script. Ancora logo abaixo do #userInfo (existe em todas as telas),
 * então serve o site inteiro sem editar cada HTML. Degrada em silêncio (login, erro).
 */
(function () {
  document.addEventListener('DOMContentLoaded', function () {
    var anchor = document.getElementById('userInfo');
    if (!anchor || document.getElementById('biRefresh')) return;
    var el = document.createElement(anchor.tagName === 'SPAN' ? 'span' : 'div');
    el.id = 'biRefresh';
    el.className = anchor.className || 'meta';
    el.style.opacity = '0.85';
    if (anchor.tagName === 'SPAN') el.style.marginLeft = '10px';
    el.textContent = 'BI: —';
    anchor.parentNode.insertBefore(el, anchor.nextSibling);

    window.fetchJSON('/api/pbi/refresh').then(function (j) {
      if (j && j.refresh && j.refresh.end_fmt) {
        el.textContent = 'BI atualizado em ' + j.refresh.end_fmt +
                         (j.refresh.in_progress ? ' · 🔄 atualizando' : '');
      } else {
        el.textContent = 'BI: atualização indisponível';
      }
    }).catch(function () { el.textContent = 'BI: —'; });
  });
})();
