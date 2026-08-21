/* ═══════════════════════ Agente de IA — chat do módulo Compras ═══════════════════════
   Arquivo PRÓPRIO, carregado depois do estoque.js: reusa os globais dele (`$`, `esc`, `getJSON`,
   `filtrosQS`) sem acrescentar mais 200 linhas a um arquivo que já tem 3,5 mil.

   O contexto é CONSOLIDADO e vem do SERVIDOR (ver `estoque/ia.py`). O front não monta payload:
   as abas carregam sob demanda — `S.evo` só existe se a Evolução foi aberta, o
   `/api/fornecedores_extra` idem —, então montar aqui daria um contexto que depende de quais
   abas a pessoa visitou. Imprevisível de depurar e impossível de reproduzir num suporte.

   ⚠️ O agente responde no RECORTE DA TELA (decisão do Gabriel: "senão fica dupla
   interpretação"). Por isso o `filtrosQS()` viaja na URL de toda pergunta, e quando o filtro
   muda com o chat aberto a tela AVISA e refaz as sugestões — o número mudou debaixo da conversa
   e as respostas na rolagem passam a descrever outro universo. */

let IA = { modulo: false, on: false, upsell: null, modelo: null, hist: [], qs: null, sugCarregada: false };

/* Markdown mínimo. ESCAPA PRIMEIRO e só então formata — a resposta é texto de um modelo e nunca
   pode virar HTML por acidente. Cobre exatamente o que o prompt autoriza (negrito, itálico,
   `código`, lista, parágrafo). Evita vendorizar ~50KB de marked.js num repo sem cache-busting;
   o preço é que tabela/título viriam como texto cru — e o prompt proíbe os dois. */
function iaMd(t) {
  let h = esc(t || '');
  h = h.replace(/`([^`\n]+)`/g, '<code>$1</code>');
  h = h.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  h = h.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
  const out = [];
  let ul = false;
  for (const l of h.split('\n')) {
    const m = l.match(/^\s*[-•]\s+(.*)$/);
    if (m) {
      if (!ul) { out.push('<ul>'); ul = true; }
      out.push('<li>' + m[1] + '</li>');
      continue;
    }
    if (ul) { out.push('</ul>'); ul = false; }
    if (l.trim()) out.push('<p style="margin:0 0 6px">' + l + '</p>');
  }
  if (ul) out.push('</ul>');
  return out.join('');
}

function iaMsg(txt, tipo) {
  const b = $('#ia-body');
  const d = document.createElement('div');
  d.className = 'ia-msg ' + tipo;
  if (tipo === 'bot') d.innerHTML = iaMd(txt); else d.textContent = txt;
  b.appendChild(d);
  b.scrollTop = b.scrollHeight;
  return d;
}

async function iaStatus() {
  // `modulo` decide se o widget EXISTE; `disponivel` decide se ele conversa. São coisas
  // diferentes: a instância que não contratou o Agente não pode nem ver o botão, enquanto a que
  // o tem ligado sem chave vê o cadeado e a oferta (é venda adicional, e recurso escondido não
  // vende). Falha de rede cai no mesmo lugar do módulo desligado — melhor não desenhar do que
  // desenhar um botão que não responde.
  IA.modulo = false;
  try {
    const j = await getJSON('/estoque/api/ia/status');
    IA.modulo = !!j.modulo;
    IA.on = !!j.disponivel; IA.upsell = j.upsell; IA.modelo = j.modelo;
  } catch (e) { IA.modulo = false; IA.on = false; IA.upsell = null; }
  const fab = $('#ia-fab');
  if (!fab) return;
  if (!IA.modulo) { fab.hidden = true; return; }   // Agente não habilitado: nem aparece
  fab.hidden = false;                       // dos dois estados restantes, o travado é a oferta
  fab.classList.toggle('travado', !IA.on);
  fab.textContent = IA.on ? '\u{1F4AC}' : '\u{1F512}';
  fab.title = IA.on ? 'Analista de Compras (IA)'
                    : 'Agente de IA especialista — não incluído no seu plano';
}

/* A OFERTA. Sem credencial o chat abre mostrando o que ele FARIA — é venda adicional, e recurso
   escondido não vende (decisão do Gabriel). Nenhuma pergunta sai daqui, e o backend ainda recusa
   com 402: não existe caminho em que a instância sem contrato consuma API. */
function iaOferta() {
  const u = IA.upsell || {};
  $('#ia-titulo').textContent = u.titulo || 'Agente de IA especialista';
  $('#ia-in').hidden = true;
  $('#ia-pe').hidden = true;
  $('#ia-body').innerHTML =
    '<div class="ia-oferta">' +
      '<div class="ic">\u{1F916}</div>' +
      '<h5>' + esc(u.titulo || 'Agente de IA especialista') + '</h5>' +
      '<p>' + iaMd(u.texto || '') + '</p>' +
      '<div class="cta">' + esc(u.cta || 'Fale com o administrador da sua conta.') + '</div>' +
    '</div>';
}

function iaAbrirChat() {
  $('#ia-titulo').textContent = 'Analista de Compras';
  $('#ia-in').hidden = false;
  $('#ia-pe').hidden = false;
  $('#ia-pe').textContent =
    'Lê os números do painel no recorte da sua tela · ' + (IA.modelo || 'IA');
  if (!IA.hist.length && !$('#ia-body').children.length) {
    iaMsg('Sou o analista deste painel. Leio os números **no recorte que você está '
        + 'vendo agora**. Pergunte à vontade:', 'bot');
  }
  iaSugestoes();
}

function iaToggle() {
  const box = $('#ia-box'), fab = $('#ia-fab');
  const aberto = box.classList.toggle('aberto');
  fab.classList.toggle('aberto', aberto);
  if (!aberto) return;
  if (!IA.on) { iaOferta(); return; }
  iaAbrirChat();
  setTimeout(() => { const i = $('#ia-input'); if (i) i.focus(); }, 80);
}

/* Sugestões vêm do SERVIDOR porque dependem do ESTADO (ruptura > 0, orçamento estourado, série
   histórica existindo…) e é o servidor que tem o contexto. Sugestão fixa envelhece e vira
   decoração — a da DRE reage ao número, e é isso que ensina a pessoa a usar a ferramenta. */
async function iaSugestoes() {
  const antigo = document.getElementById('ia-sug-box');
  if (antigo) antigo.remove();
  const wrap = document.createElement('div');
  wrap.className = 'ia-sug';
  wrap.id = 'ia-sug-box';
  $('#ia-body').appendChild(wrap);
  // as sugestões reagem ao ESTADO (ver `ia.sugestoes`): com item vencendo em 7 dias a de
  // validade sobe para o topo. Por isso são recarregadas, não fixas.
  try {
    const j = await getJSON('/estoque/api/ia/contexto?' + filtrosQS());
    IA.qs = filtrosQS();
    IA.sugCarregada = true;
    wrap.innerHTML = (j.sugestoes || [])
      .map(s => '<button type="button" onclick="iaUsarSug(this)">' + esc(s) + '</button>')
      .join('');
    $('#ia-body').scrollTop = $('#ia-body').scrollHeight;
  } catch (e) { wrap.remove(); }
}

function iaUsarSug(btn) { $('#ia-input').value = btn.textContent; iaEnviar(); }

/* Chamado por TODO `render()`, antes dos early-returns por aba, então pega qualquer mudança de
   filtro em qualquer tela. Só age com o chat aberto: fora dele, abrir já recarrega as sugestões. */
function chatCheckFiltros() {
  if (!IA.on || !IA.sugCarregada) return;
  const box = $('#ia-box');
  if (!box || !box.classList.contains('aberto')) return;
  const agora = filtrosQS();
  if (agora === IA.qs) return;
  IA.qs = agora;
  $('#ia-body').querySelectorAll('.ia-msg.sis').forEach(el => el.remove());   // não acumula
  iaMsg('⟳ O filtro da tela mudou. As respostas acima são do recorte anterior — '
      + 'refaça a pergunta para o recorte atual.', 'sis');
  iaSugestoes();
}

async function iaEnviar() {
  const inp = $('#ia-input'), btn = $('#ia-send');
  const pergunta = (inp.value || '').trim();
  if (!pergunta || btn.disabled) return;
  const sug = document.getElementById('ia-sug-box');
  if (sug) sug.remove();
  iaMsg(pergunta, 'user');
  inp.value = '';
  btn.disabled = true;

  const typing = iaMsg('', 'bot');
  typing.innerHTML = '<span class="ia-typing"><span></span><span></span><span></span></span>';
  let resposta = '', alvo = null;

  try {
    const r = await fetch('/estoque/api/ia/chat?' + filtrosQS(), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ pergunta: pergunta, historico: IA.hist.slice(-6) })
    });
    // 402 = recurso não contratado. Defesa em profundidade: o front já sabia pelo status, mas se
    // a chave cair no meio do uso a conversa degrada para a oferta em vez de dar erro seco.
    if (r.status === 402) {
      typing.remove();
      IA.on = false;
      const j = await r.json().catch(() => ({}));
      IA.upsell = j.upsell || IA.upsell;
      iaOferta();
      return;
    }
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error(j.error || ('erro ' + r.status));
    }
    typing.remove();
    alvo = iaMsg('', 'bot');

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
        let obj;
        try { obj = JSON.parse(raw); } catch (e) { continue; }
        if (obj.erro) { alvo.innerHTML = iaMd('**Erro:** ' + obj.erro); return; }
        if (obj.token) {
          resposta += obj.token;
          alvo.innerHTML = iaMd(resposta);
          $('#ia-body').scrollTop = $('#ia-body').scrollHeight;
        }
      }
    }
    if (resposta) {
      IA.hist.push({ role: 'user', content: pergunta });
      IA.hist.push({ role: 'assistant', content: resposta });
    }
    // ⚠️ As sugestões VOLTAM depois de cada resposta. Antes elas apareciam só na abertura e
    // sumiam na primeira pergunta — exatamente quando passam a ser mais úteis: a pessoa acabou
    // de ver que o agente responde e é aí que ela quer saber o que MAIS dá para perguntar.
    // Sem isso o chat vira uma caixa de texto em branco depois do primeiro uso.
    iaSugestoes();
  } catch (e) {
    if (typing.isConnected) typing.remove();
    if (!alvo) alvo = iaMsg('', 'bot');
    alvo.innerHTML = iaMd('**Não consegui responder agora.** ' + (e.message || ''));
  } finally {
    btn.disabled = false;
    inp.focus();
  }
}

// o status é barato (uma env var no servidor) e decide se o botão nasce chat ou oferta
document.addEventListener('DOMContentLoaded', iaStatus);
