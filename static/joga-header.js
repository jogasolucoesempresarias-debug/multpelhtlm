'use strict';
/* Cabeçalho único do JOGA Analytics.

   Antes cada página trazia o próprio cabeçalho escrito à mão. Com 12 arquivos, eles
   divergiram: o Admin usava outra classe e não tinha `.brand` (o que deixou a página sem o
   seletor de área e sem caminho de volta), e 3 páginas simplesmente não tinham link de Admin.
   Aqui existe UMA definição do menu, então essa classe de bug deixa de ser possível.

   Dois modos, porque as áreas são legitimamente diferentes:
     • DONO    (Comercial e Administração) — renderiza a barra inteira.
     • ENXERTO (Compras) — a barra do módulo tem controles funcionais (Unidade, Comprador),
       então não é substituída: recebe só o seletor de área e o bloco de conta.

   O esqueleto é montado de forma SÍNCRONA, antes de qualquer script de página rodar. Os
   scripts das páginas escrevem em #userInfo dentro de try/catch com handler vazio — se o
   elemento não existisse no momento certo, o TypeError seria engolido e a inicialização da
   página morreria em silêncio. Só o que depende do usuário é preenchido depois do /api/me. */
(function () {
  const AREAS = {
    comercial: { rotulo: 'Comercial', href: '/', dica: 'Vendas, carteira e metas' },
    compras: { rotulo: 'Gestão de Estoque', href: '/estoque/', dica: 'Reposição, ruptura, validade e pedidos' },
  };
  const ROTULO_ADMIN = 'Administração';

  // Menu do Comercial — fonte única. `perfis` limita quem enxerga o item.
  const MENU = [
    { href: '/', rotulo: 'Dashboard' },
    { href: '/carteira', rotulo: 'Carteira' },
    { href: '/vendedores', rotulo: 'Vendedores', exceto: ['vendedor'] },
    { href: '/categorias', rotulo: 'Categorias' },
    { href: '/mix', rotulo: 'Mix' },
    { href: '/radar', rotulo: 'Radar' },
    { href: '/tendencias', rotulo: 'Tendências' },
    { href: '/metas', rotulo: 'Metas' },
    { href: '/gerencial', rotulo: 'Gerencial' },
  ];

  const emCompras = () => location.pathname.startsWith('/estoque');
  const emAdmin = () => location.pathname === '/admin' || location.pathname.startsWith('/admin/');

  function localAtual() {
    if (emCompras()) return 'compras';
    if (emAdmin()) return 'admin';
    return 'comercial';
  }

  const CSS = `
  /* O link de Admin nasce com o atributo [hidden] e só é revelado para admin. Sem esta regra
     ele apareceria para TODO MUNDO: [hidden] do navegador é 'display:none', mas a página
     define '.btn-sm { display:inline-block }' — estilo de autor vence o do agente, com a mesma
     especificidade. Falha silenciosa clássica: nenhum erro, só UI de administração exposta. */
  [data-joga-header] [hidden] { display: none !important; }

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

  /* Bloco de conta injetado no Compras, cuja barra própria não tem Admin nem Sair. */
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

  .tema-btn {
    cursor: pointer; line-height: 1; font-size: .95rem;
    padding: 6px 10px; border-radius: 7px;
    background: var(--surface2, #1a2235); border: 1px solid var(--border, #1e293b);
  }
  .tema-btn:hover { border-color: var(--accent, #38bdf8); }
  `;

  function injetarCSS() {
    if (document.getElementById('joga-header-css')) return;
    const st = document.createElement('style');
    st.id = 'joga-header-css';
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  // ── Esqueleto (síncrono) ────────────────────────────────────────────────────────────────
  // Renderiza a barra completa nas páginas "donas". Os itens que dependem do usuário nascem
  // ocultos e são revelados depois do /api/me — nunca o contrário, para um erro de rede não
  // acabar exibindo link de administração para quem não é admin.
  function montarEsqueleto() {
    const barra = document.querySelector('.top-bar[data-joga-header], .topbar[data-joga-header]');
    if (!barra) return null;

    const titulo = barra.getAttribute('data-titulo') || 'JOGA Analytics';
    const aqui = location.pathname;
    const itens = emAdmin() ? [] : MENU;   // Administração é neutra: não carrega o menu do Comercial

    barra.innerHTML = `
      <div class="brand">
        <div class="mark" style="background:none;overflow:hidden;padding:0"><img src="/static/joga-mark.svg" alt="JOGA" style="width:100%;height:100%;display:block"></div>
        <div>
          <h1>${titulo}</h1>
          <div class="meta" id="userInfo"></div>
        </div>
      </div>
      <div class="nav">
        ${itens.map(i => `<a href="${i.href}" class="btn-sm${i.href === aqui ? ' active' : ''}"
             data-menu="${i.href}" ${i.exceto ? `data-exceto="${i.exceto.join(',')}"` : ''}
             >${i.rotulo}</a>`).join('')}
        <a href="/admin" class="btn-sm${emAdmin() ? ' active' : ''}" data-so-admin hidden>Admin</a>
        <a href="/logout" class="btn-sm">Sair</a>
      </div>`;
    return barra;
  }

  // ── Seletor de área ─────────────────────────────────────────────────────────────────────
  function inserirNaBarra(el) {
    const barra = document.querySelector('.top-bar, .topbar');
    if (!barra) return false;
    const marca = barra.querySelector('.brand');
    if (marca) marca.insertAdjacentElement('afterend', el);
    else if (barra.firstElementChild) barra.firstElementChild.insertAdjacentElement('afterend', el);
    else barra.appendChild(el);
    return true;
  }

  function montarSeletor(efetivas, ehAdmin) {
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
           ${ROTULO_ADMIN}<small>Usuários, acessos e relatórios</small></a>` : '';

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

  // ── Bloco de conta (só no Compras) ──────────────────────────────────────────────────────
  function montarConta(me) {
    if (!emCompras()) return;               // as demais páginas já têm o delas
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

  // ── Troca de tema ───────────────────────────────────────────────────────────────────────
  // O anti-piscada no <head> já aplicou o tema salvo antes da 1ª pintura. Aqui só montamos o
  // botão e reconciliamos com o banco (a escolha pode ter sido feita em outra máquina).
  function temaAtual() {
    return document.documentElement.dataset.tema === 'claro' ? 'claro' : 'escuro';
  }

  // A marca e o loader têm o hexágono claro (some no branco). No tema claro, trocamos pela
  // versão de hexágono escuro (kit de marca da JOGA). O laranja é o mesmo nos dois.
  function trocarArteDaMarca(claro) {
    document.querySelectorAll('img[src*="joga-mark"]').forEach(img => {
      img.src = claro ? '/static/joga-mark-claro.svg' : '/static/joga-mark.svg';
    });
    document.querySelectorAll('img[src*="joga-loader"]').forEach(img => {
      img.src = claro ? '/static/joga-loader-claro.svg' : '/static/joga-loader.svg';
    });
  }

  function aplicarTema(t) {
    if (t === 'claro') document.documentElement.dataset.tema = 'claro';
    else document.documentElement.removeAttribute('data-tema');   // ausência = escuro (default)
    try { localStorage.setItem('joga:tema', t); } catch (e) { /* modo privado: sem persistência local */ }
    if (window.aplicarTemaChart) window.aplicarTemaChart();       // gráficos futuros já saem no tom certo
    trocarArteDaMarca(t === 'claro');
    const b = document.querySelector('.tema-btn');
    if (b) b.textContent = t === 'claro' ? '🌙' : '☀️';           // mostra o que o clique VAI fazer
  }

  function montarToggleTema() {
    if (document.querySelector('.tema-btn')) return;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'tema-btn';
    btn.title = 'Alternar tema claro/escuro';
    btn.textContent = temaAtual() === 'claro' ? '🌙' : '☀️';
    btn.addEventListener('click', () => {
      const novo = temaAtual() === 'claro' ? 'escuro' : 'claro';
      aplicarTema(novo);
      // Persiste no banco para seguir a pessoa; falha de rede não desfaz a troca local.
      fetch('/api/me/tema', {
        method: 'PUT', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tema: novo }),
      }).catch(() => {});
    });
    // Encaixa junto dos controles de conta: antes do "Sair" no menu, ou no bloco de conta do Compras.
    const sairNav = document.querySelector('.nav a[href="/logout"]');
    const conta = document.querySelector('.area-conta');
    if (sairNav) sairNav.insertAdjacentElement('beforebegin', btn);
    else if (conta) conta.insertBefore(btn, conta.firstChild);
    else (document.querySelector('.top-bar, .topbar') || document.body).appendChild(btn);
  }

  // ── Ajustes dependentes do usuário ──────────────────────────────────────────────────────
  function aplicarPerfil(me) {
    const el = document.getElementById('userInfo');
    if (el && !el.textContent.trim()) {
      el.textContent = `${me.nome || ''} · ${me.role || ''}` +
        (me.codusur ? ' · RCA ' + me.codusur : '');
    }

    // Admin: revelado só para admin (nasce hidden — falha de rede não expõe).
    const linkAdmin = document.querySelector('[data-so-admin]');
    if (linkAdmin && me.role === 'admin') linkAdmin.hidden = false;

    // Itens vetados para certos perfis (ex.: vendedor não vê "Vendedores").
    document.querySelectorAll('[data-exceto]').forEach(a => {
      if (a.getAttribute('data-exceto').split(',').includes(me.role)) a.style.display = 'none';
    });

    // Comercial indisponível (módulo não contratado ou usuário sem a área): os links somem.
    const temModulo = !(me.modulos || []).length || (me.modulos || []).includes('comercial');
    const temArea = (me.areas || []).includes('comercial');
    if (!temModulo || !temArea) {
      document.querySelectorAll('[data-menu]').forEach(a => { a.style.display = 'none'; });
    }
  }

  function iniciar() {
    // Esqueleto AGORA, de forma síncrona. A tag <script> deste arquivo fica logo depois do
    // placeholder, então quando isto roda o elemento já existe — e qualquer script inline
    // mais abaixo na página encontra #userInfo pronto. Esperar DOMContentLoaded não serviria:
    // script inline no fim do <body> executa ANTES de scripts deferidos.
    injetarCSS();
    montarEsqueleto();
    // O anti-piscada já definiu data-tema; aplica a arte da marca no tom certo desde o load
    // (senão a página que abre no claro mostraria a marca creme por um instante).
    trocarArteDaMarca(temaAtual() === 'claro');

    fetch('/api/me', { credentials: 'same-origin' })
      .then(r => (r.ok ? r.json() : null))
      .then(me => {
        if (!me || !me.ok) return;
        aplicarPerfil(me);
        montarConta(me);
        montarToggleTema();
        // Reconcilia com o banco: se a pessoa trocou o tema em outra máquina, o banco vence.
        // (Pode haver 1 flip visível aqui — só quando os dois discordam; depois fica alinhado.)
        if (me.tema && me.tema !== temaAtual()) aplicarTema(me.tema);
        const efetivas = (me.areas || []).filter(a => AREAS[a]);
        // Com 1 área não há troca a fazer — exceto no Admin, onde o seletor é a única saída.
        if (efetivas.length < 2 && !emAdmin()) return;
        montarSeletor(efetivas, me.role === 'admin');
      })
      .catch(() => { /* cabeçalho é acessório: falha não pode derrubar a página */ });
  }

  iniciar();
})();
