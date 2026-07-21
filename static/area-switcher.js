'use strict';
/* Seletor de área (Comercial ⇄ Compras).

   Componente ÚNICO, carregado por todas as páginas das duas áreas. A escolha de injetar via
   JS (em vez de colar o markup em cada .html) é deliberada: o comercial é multi-página, então
   markup duplicado em 12 arquivos divergiria com o tempo — e é justamente a barra desalinhada
   entre as áreas que faz o produto parecer dois apps colados.

   Só aparece para quem tem 2 áreas efetivas (areas ∩ MODULOS). Com uma área só, o sistema se
   comporta exatamente como antes da fusão: sem seletor e sem portal. */
(function () {
  const AREAS = {
    comercial: { rotulo: 'Comercial', href: '/', dica: 'Vendas, carteira e metas' },
    compras: { rotulo: 'Compras', href: '/estoque/', dica: 'Estoque, reposição e pedidos' },
  };

  const areaAtual = () => (location.pathname.startsWith('/estoque') ? 'compras' : 'comercial');

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

  /* Bloco de conta — só no Compras, que não tinha nem Admin nem Sair no cabeçalho. */
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

  function montar(efetivas) {
    const brand = document.querySelector('.top-bar .brand, .topbar .brand');
    if (!brand || document.querySelector('.area-sw')) return;

    const atual = areaAtual();
    const wrap = document.createElement('div');
    wrap.className = 'area-sw';

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'area-sw__btn';
    btn.setAttribute('aria-haspopup', 'true');
    btn.setAttribute('aria-expanded', 'false');
    btn.innerHTML = `${(AREAS[atual] || {}).rotulo || 'Área'}
      <svg viewBox="0 0 10 6" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M1 1l4 4 4-4"/></svg>`;

    const menu = document.createElement('div');
    menu.className = 'area-sw__menu';
    menu.hidden = true;
    menu.innerHTML =
      efetivas.map(a => `<a class="area-sw__item${a === atual ? ' on' : ''}" href="${AREAS[a].href}">
           ${AREAS[a].rotulo}<small>${AREAS[a].dica}</small></a>`).join('') +
      `<div class="area-sw__sep"></div>
       <a class="area-sw__item" href="/portal">Portal de entrada<small>Escolher e fixar a área padrão</small></a>`;

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
    // Depois do bloco de marca: fica no mesmo lugar nas duas áreas.
    brand.insertAdjacentElement('afterend', wrap);
  }

  /* O Compras nasceu como app standalone de senha única: o cabeçalho dele não tem Admin nem
     Sair. Com conta nominal isso vira problema real — um usuário exclusivo de Compras não
     teria como sair do sistema. Injetamos aqui em vez de colar no HTML para o bloco nascer
     igual ao do Comercial e continuar assim. */
  function montarConta(me) {
    if (areaAtual() !== 'compras') return;              // o Comercial já tem os seus
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

  function iniciar() {
    fetch('/api/me', { credentials: 'same-origin' })
      .then(r => (r.ok ? r.json() : null))
      .then(me => {
        if (!me || !me.ok) return;
        esconderModulosInativos(me.modulos || []);
        injetarCSS();
        montarConta(me);
        const efetivas = (me.areas || []).filter(a => AREAS[a]);
        if (efetivas.length < 2) return;   // 1 área → nada a trocar
        montar(efetivas);
      })
      .catch(() => { /* seletor é acessório: falha silenciosa não pode derrubar a página */ });
  }

  /* Se o módulo Comercial não foi contratado, os links do menu dele não devem sequer aparecer.
     O servidor já nega as rotas (404), mas link que leva a erro é defeito de produto. */
  function esconderModulosInativos(modulos) {
    if (!modulos.length || modulos.includes('comercial')) return;
    document.querySelectorAll('.top-bar .nav a').forEach(a => {
      const href = a.getAttribute('href') || '';
      if (href === '/logout' || href.startsWith('/estoque') || href === '/portal') return;
      a.style.display = 'none';
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', iniciar);
  } else {
    iniciar();
  }
})();
