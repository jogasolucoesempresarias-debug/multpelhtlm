'use strict';
/* Seletor de área do JOGA Analytics.

   O sistema tem três lugares, e essa distinção é o que faz a navegação parar de confundir:
     • Comercial      — vendas, carteira, metas
     • Compras        — estoque, reposição, pedidos
     • Administração  — não pertence a nenhuma das duas; administra usuários das DUAS

   Componente ÚNICO, carregado por todas as páginas. Injetar via JS (em vez de colar markup em
   cada .html) é deliberado: o Comercial é multi-página, e markup repetido em 12 arquivos
   diverge com o tempo — foi exatamente essa divergência que já deixou o Admin sem seletor.

   O seletor só aparece para quem tem 2 áreas efetivas (areas ∩ MODULOS). Com uma área só o
   sistema se comporta como antes da fusão: sem seletor e sem portal. */
(function () {
  const AREAS = {
    comercial: { rotulo: 'Comercial', href: '/', dica: 'Vendas, carteira e metas' },
    compras: { rotulo: 'Compras', href: '/estoque/', dica: 'Estoque, reposição e pedidos' },
  };

  const ROTULO_ADMIN = 'Administração';

  const emCompras = () => location.pathname.startsWith('/estoque');
  const emAdmin = () => location.pathname === '/admin' || location.pathname.startsWith('/admin/');

  /* Onde a pessoa ESTÁ. O Admin é um lugar próprio: dizer "Comercial" ali dava a impressão de
     que ela tinha sido movida de área ao abrir o Admin vindo do Compras. */
  function localAtual() {
    if (emCompras()) return 'compras';
    if (emAdmin()) return 'admin';
    return 'comercial';
  }

  const CSS = `
  .area-sw { position: relative; margin-left: 14px; }
  .area-sw__btn {
    display: flex; align-items: center; gap: 7px; cursor: pointer;
    padding: 7px 12px; border-radius: 8px; font-size: .82rem; font-weight: 600;
    background: var(--surface2, #1a2235); border: 1px solid var(--border, #1e293b);
    color: var(--text, #e2e8f0); font-family: inherit; line-height: 1;
  }
  .area-sw__btn:hover { border-color: var(--accent, #38bdf8); color: var(--accent, #38bdf8); }
  .area-sw__btn svg { width: 10px; height: 10px; opacity: .7; }
  .area-sw__menu {
    position: absolute; top: calc(100% + 6px); left: 0; z-index: 900; min-width: 216px;
    background: var(--surface, #111827); border: 1px solid var(--border, #1e293b);
    border-radius: 10px; padding: 6px; box-shadow: 0 10px 30px rgba(0,0,0,.45);
  }
  .area-sw__menu[hidden] { display: none; }
  .area-sw__item {
    display: block; padding: 9px 11px; border-radius: 7px; text-decoration: none;
    color: var(--text, #e2e8f0); font-size: .82rem;
  }
  .area-sw__item:hover { background: var(--surface2, #1a2235); }
  .area-sw__item small { display: block; color: var(--text-dim, #94a3b8); font-size: .7rem; margin-top: 2px; }
  .area-sw__item.on { color: var(--accent, #38bdf8); }
  .area-sw__sep { height: 1px; background: var(--border, #1e293b); margin: 5px 4px; }

  /* Bloco de conta — injetado só no Compras, que nasceu standalone (senha única) e não tinha
     nem Admin nem Sair no cabeçalho. As demais páginas já trazem o delas no HTML. */
  .area-conta { display: flex; align-items: center; gap: 8px; margin-left: auto; }
  .area-conta__nome {
    font-size: .72rem; color: var(--text-dim, #94a3b8);
    font-family: 'JetBrains Mono', monospace; white-space: nowrap;
  }
  .area-conta a {
    padding: 6px 11px; border-radius: 7px; font-size: .78rem; text-decoration: none;
    background: var(--surface2, #1a2235); border: 1px solid var(--border, #1e293b);
    color: var(--text, #e2e8f0); white-space: nowrap;
  }
  .area-conta a:hover { border-color: var(--accent, #38bdf8); color: var(--accent, #38bdf8); }
  `;

  function injetarCSS() {
    if (document.getElementById('area-sw-css')) return;
    const st = document.createElement('style');
    st.id = 'area-sw-css';
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  /* Os cabeçalhos das páginas NÃO são iguais: o Comercial usa `.top-bar > .brand`, o Compras
     usa `.topbar > .brand` e o Admin usa `.topbar` com links soltos (sem .brand). Cascata de
     âncoras cobre as três sem exigir que os 12 HTMLs sejam uniformizados. */
  function inserirNaBarra(el) {
    const barra = document.querySelector('.top-bar, .topbar');
    if (!barra) return false;
    const marca = barra.querySelector('.brand');
    if (marca) {
      marca.insertAdjacentElement('afterend', el);
    } else if (barra.firstElementChild) {
      barra.firstElementChild.insertAdjacentElement('afterend', el);
    } else {
      barra.appendChild(el);
    }
    return true;
  }

  function montar(efetivas, ehAdmin) {
    if (document.querySelector('.area-sw')) return;

    const atual = localAtual();
    const wrap = document.createElement('div');
    wrap.className = 'area-sw';

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'area-sw__btn';
    btn.setAttribute('aria-haspopup', 'true');
    btn.setAttribute('aria-expanded', 'false');
    const rotulo = atual === 'admin' ? ROTULO_ADMIN : (AREAS[atual] || {}).rotulo || 'Área';
    btn.innerHTML = `${rotulo}
      <svg viewBox="0 0 10 6" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M1 1l4 4 4-4"/></svg>`;

    const itemAdmin = ehAdmin
      ? `<a class="area-sw__item${atual === 'admin' ? ' on' : ''}" href="/admin">
           ${ROTULO_ADMIN}<small>Usuários, acessos e relatórios</small></a>`
      : '';

    const menu = document.createElement('div');
    menu.className = 'area-sw__menu';
    menu.hidden = true;
    menu.innerHTML =
      efetivas.map(a => `<a class="area-sw__item${a === atual ? ' on' : ''}" href="${AREAS[a].href}">
           ${AREAS[a].rotulo}<small>${AREAS[a].dica}</small></a>`).join('') +
      `<div class="area-sw__sep"></div>${itemAdmin}` +
      `<a class="area-sw__item" href="/portal">Portal de entrada<small>Escolher e fixar a área padrão</small></a>`;

    const fechar = () => { menu.hidden = true; btn.setAttribute('aria-expanded', 'false'); };
    btn.addEventListener('click', e => {
      e.stopPropagation();
      menu.hidden = !menu.hidden;
      btn.setAttribute('aria-expanded', String(!menu.hidden));
    });
    document.addEventListener('click', fechar);
    document.addEventListener('keydown', e => { if (e.key === 'Escape') fechar(); });
    menu.addEventListener('click', e => e.stopPropagation());

    wrap.appendChild(btn);
    wrap.appendChild(menu);
    inserirNaBarra(wrap);
  }

  /* Só no Compras. A condição é a LOCALIZAÇÃO FÍSICA, não a área "atual" — quando eram a mesma
     coisa, abrir o Admin vindo do Compras duplicava o bloco de conta numa página que já tem o
     seu (nome e Sair apareciam duas vezes). */
  function montarConta(me) {
    if (!emCompras()) return;
    const bar = document.querySelector('.topbar');
    if (!bar || document.querySelector('.area-conta')) return;

    const box = document.createElement('div');
    box.className = 'area-conta';
    const admin = me.role === 'admin'
      ? '<a href="/admin" title="Administração de usuários">Admin</a>' : '';
    box.innerHTML =
      `<span class="area-conta__nome">${me.nome || ''}${me.role ? ' · ' + me.role : ''}</span>
       ${admin}<a href="/logout">Sair</a>`;
    bar.appendChild(box);
  }

  /* Esconde links do Comercial quando ele não está disponível — porque a empresa não contratou
     o módulo, ou porque este usuário não tem a área. O servidor já nega (404/403), mas link que
     leva a erro é defeito de produto. Vale sobretudo no Admin, que é neutro e um admin só de
     Compras consegue abrir. */
  function esconderComercialIndisponivel(me) {
    const temModulo = !(me.modulos || []).length || (me.modulos || []).includes('comercial');
    const temArea = (me.areas || []).includes('comercial');
    if (temModulo && temArea) return;
    const NEUTROS = ['/logout', '/portal', '/admin'];
    document.querySelectorAll('.top-bar .nav a, .topbar > a').forEach(a => {
      const href = a.getAttribute('href') || '';
      if (NEUTROS.includes(href) || href.startsWith('/estoque')) return;
      a.style.display = 'none';
    });
  }

  function iniciar() {
    fetch('/api/me', { credentials: 'same-origin' })
      .then(r => (r.ok ? r.json() : null))
      .then(me => {
        if (!me || !me.ok) return;
        esconderComercialIndisponivel(me);
        injetarCSS();
        montarConta(me);
        const efetivas = (me.areas || []).filter(a => AREAS[a]);
        // Com 1 área só não há troca a fazer — exceto no Admin, onde o seletor é o caminho de
        // volta: sem ele, um admin de área única entra no Admin e fica sem saída.
        if (efetivas.length < 2 && !emAdmin()) return;
        montar(efetivas, me.role === 'admin');
      })
      .catch(() => { /* seletor é acessório: falha silenciosa não pode derrubar a página */ });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', iniciar);
  } else {
    iniciar();
  }
})();
