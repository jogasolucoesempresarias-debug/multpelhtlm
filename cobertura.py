"""Motor de Cobertura de Carteira. Funções puras — sem Flask/DAX/DB.
Testes em tests/test_cobertura.py validam matematicamente cada função.

Conceito (pedido da diretoria):
- "Cobertura" = fatia da carteira que está EM DIA (última compra ≤ coberto_dias).
  É a mesma métrica em dois zooms: o índice % (placar) e a distribuição por faixa de recência.
  Índice = clientes/valor na janela coberta ÷ total da carteira.

Sobre isso somamos 2 camadas que já vêm prontas em cada cliente (rfm.calcular_clientes):
- cobertura_ciclo: fração dentro do PRÓPRIO ciclo do cliente (ranking justo — não pune ciclo longo).
- receita_em_risco: Σ receita_perdida_proj (o "onde atuar" com cifrão).

Entrada: lista de clientes já enriquecidos por rfm.py + lookups de vendedor/time
(cada dict tem recencia_dias, venda_12m, lucro_12m, ciclo_pessoal, status_personalizada,
receita_perdida_proj, codusur, vendedor, codsupervisor, time).
"""
from datetime import datetime

# Faixas FIXAS pedidas pela diretoria (não mudam com o toggle de "coberto").
# (chave, limite_inferior, limite_superior) — inclusivos. 91+ tem topo aberto.
FAIXAS = [
    ('0-15',  0,  15),
    ('16-30', 16, 30),
    ('31-45', 31, 45),
    ('46-60', 46, 60),
    ('61-90', 61, 90),
    ('91+',   91, 10 ** 9),
]

# ── Régua ÚNICA de cobertura (João, 28–29/09/2026 — PLANO_MELHORIAS §11) ──────────────────
# Uma métrica em Gerencial, Vendedores e Performance: base = clientes CADASTRADOS no vendedor com
# última compra (de QUALQUER vendedor) nos últimos BASE_DIAS; positivado = última compra nos
# últimos COBERTO_DIAS_PADRAO. O Gerencial mede em HOJE; Performance/Vendedores no último dia do
# mês fechado (`cobertura_por_dono`). Antes: carteira de 24 meses e janela de 30 d — a empresa
# saía em 35% porque quem comprou 12–24 meses atrás contava como base.
BASE_DIAS = 365
COBERTO_DIAS_PADRAO = 60          # "em dia" default (era 30 até 09/2026)
LIMIAR_PADRAO = 85.0              # % — o piso das faixas de nota (< 85% = nota 0); era 60
MIN_AMOSTRA = 5                   # abaixo disso o % é ruído → flag amostra_pequena
_STATUS_NO_CICLO = ('ok', 'normal')  # dentro do ciclo pessoal do cliente


def na_base(recencia_dias, base_dias=BASE_DIAS):
    """Cliente é BASE ativa na data: comprou (de qualquer vendedor) nos últimos `base_dias`."""
    return recencia_dias is not None and 0 <= recencia_dias <= base_dias


def positivado(recencia_dias, janela=COBERTO_DIAS_PADRAO):
    """Cliente POSITIVADO na janela: comprou (de qualquer vendedor) nos últimos `janela` dias."""
    return recencia_dias is not None and 0 <= recencia_dias <= janela


def cobertura_por_dono(ultima_compra, dono_de, data_ref, janela=COBERTO_DIAS_PADRAO, base_dias=BASE_DIAS):
    """{codusur: {base, positivados, pct}} na data `data_ref` — a MESMA régua do Gerencial, para o
    mês fechado (Performance e Vendedores). `ultima_compra` = {codcli: date} (última compra de
    qualquer vendedor até a data); `dono_de` = {codcli: codusur} (cadastro). Base < MIN_AMOSTRA →
    pct None (caixa/balcão não é parâmetro). Compra DEPOIS de `data_ref` não vale: não se sabe a
    anterior, então o cliente não é base naquela data (nunca vira positivado por engano)."""
    out = {}
    for codcli, ult in (ultima_compra or {}).items():
        u = dono_de.get(codcli)
        if u is None or ult is None:
            continue
        dias = (data_ref - ult).days
        if not na_base(dias, base_dias):
            continue
        g = out.setdefault(u, {'base': 0, 'positivados': 0})
        g['base'] += 1
        g['positivados'] += 1 if positivado(dias, janela) else 0
    for g in out.values():
        g['pct'] = (g['positivados'] / g['base']) if g['base'] >= MIN_AMOSTRA else None
    return out


def lista_limpeza(clientes, janela=COBERTO_DIAS_PADRAO, base_dias=BASE_DIAS):
    """Clientes da BASE que NÃO compraram na janela — a lista do mês de limpeza (regra 1 do João,
    29/09/2026): o vendedor vende ou transfere. Maior venda 12m primeiro (onde vale mais a ação)."""
    out = [c for c in clientes if na_base(c.get('recencia_dias'), base_dias)
           and not positivado(c.get('recencia_dias'), janela)]
    return sorted(out, key=lambda c: -(c.get('venda_12m') or 0))


def linhas_foto(clientes, anomes):
    """[(anomes, codcli, codusur, codsupervisor)] — a foto do CADASTRO do mês (toda a carteira,
    não só a base). O BI só guarda o dono ATUAL; é a foto que permite dizer, depois, quem saiu da
    carteira de quem (regra 3 do João: transferência só para vendedor ativo, nunca fictício)."""
    return [(anomes, c['codcli'], c.get('codusur'), c.get('codsupervisor'))
            for c in clientes if c.get('codcli') is not None]


def faixa_de(recencia_dias):
    """Mapeia dias-sem-comprar na chave da faixa. None/negativo → '91+' (tratado como sumido)."""
    if recencia_dias is None or recencia_dias < 0:
        return '91+'
    for chave, lo, hi in FAIXAS:
        if lo <= recencia_dias <= hi:
            return chave
    return '91+'


def _pct(parte, todo):
    """Divisão segura → 0.0 se denominador vazio."""
    return (parte / todo) if todo else 0.0


def agregar_grupo(clientes, coberto_dias=COBERTO_DIAS_PADRAO):
    """Agrega uma lista de clientes num placar de cobertura + distribuição por faixa.

    Retorna dict com:
      total_clientes, valor_total, media_mensal,
      clientes_cobertos, valor_coberto, cobertura_clientes, cobertura_valor,
      cobertura_ciclo, receita_em_risco, base_morta,
      buckets: [{faixa, clientes, valor, pct_clientes, pct_valor}]  (6 faixas fixas),
      rollup_0_30: {clientes, valor, pct_clientes, pct_valor}       (linha 0-30 do diretor).
    """
    total = len(clientes)
    valor_total = sum((c.get('venda_12m') or 0.0) for c in clientes)

    # Distribuição por faixa (contagem + valor)
    buckets_idx = {chave: {'clientes': 0, 'valor': 0.0} for chave, _, _ in FAIXAS}
    clientes_cobertos = 0
    valor_coberto = 0.0
    no_ciclo = 0
    receita_em_risco = 0.0
    for c in clientes:
        dias = c.get('recencia_dias')
        b = buckets_idx[faixa_de(dias)]
        v = c.get('venda_12m') or 0.0
        b['clientes'] += 1
        b['valor'] += v
        # Cobertura (janela dinâmica do toggle)
        if dias is not None and dias >= 0 and dias <= coberto_dias:
            clientes_cobertos += 1
            valor_coberto += v
        # Cobertura dentro do ciclo (justa)
        if c.get('status_personalizada') in _STATUS_NO_CICLO:
            no_ciclo += 1
        receita_em_risco += (c.get('receita_perdida_proj') or 0.0)

    buckets = [{
        'faixa':        chave,
        'clientes':     buckets_idx[chave]['clientes'],
        'valor':        round(buckets_idx[chave]['valor'], 2),
        'pct_clientes': _pct(buckets_idx[chave]['clientes'], total),
        'pct_valor':    _pct(buckets_idx[chave]['valor'], valor_total),
    } for chave, _, _ in FAIXAS]

    # Rollup 0-30 = 0-15 + 16-30 (linha explícita do diretor, independente do toggle)
    c_0_30 = buckets_idx['0-15']['clientes'] + buckets_idx['16-30']['clientes']
    v_0_30 = buckets_idx['0-15']['valor'] + buckets_idx['16-30']['valor']
    rollup_0_30 = {
        'clientes':     c_0_30,
        'valor':        round(v_0_30, 2),
        'pct_clientes': _pct(c_0_30, total),
        'pct_valor':    _pct(v_0_30, valor_total),
    }

    return {
        'total_clientes':     total,
        'valor_total':        round(valor_total, 2),
        'media_mensal':       round(valor_total / 12, 2),
        'clientes_cobertos':  clientes_cobertos,
        'valor_coberto':      round(valor_coberto, 2),
        'cobertura_clientes': _pct(clientes_cobertos, total),
        'cobertura_valor':    _pct(valor_coberto, valor_total),
        'cobertura_ciclo':    _pct(no_ciclo, total),
        'receita_em_risco':   round(receita_em_risco, 2),
        'base_morta':         buckets_idx['91+']['clientes'],
        'buckets':            buckets,
        'rollup_0_30':        rollup_0_30,
    }


def _agrupar_por(clientes, id_key, nome_key, sem_label, coberto_dias, extra_keys=()):
    """Agrupa por id_key (codsupervisor/codusur), monta placar por grupo, ordena pior→melhor.
    Clientes sem id (sem cadastro) caem num grupo id=None com rótulo `sem_label` — assim os
    totais dos grupos reconciliam com o total da empresa.
    `extra_keys`: campos copiados do 1º cliente pra cada grupo (ex.: codsupervisor no vendedor,
    pra o frontend filtrar RCAs por time no drill)."""
    grupos = {}
    for c in clientes:
        gid = c.get(id_key)
        bucket = grupos.setdefault(gid, {'clientes': [], 'nome': None})
        bucket['clientes'].append(c)
        if bucket['nome'] is None and c.get(nome_key):
            bucket['nome'] = c.get(nome_key)

    out = []
    for gid, bucket in grupos.items():
        agg = agregar_grupo(bucket['clientes'], coberto_dias)
        agg['id'] = gid
        agg['nome'] = bucket['nome'] or (sem_label if gid is None else f'#{gid}')
        agg['amostra_pequena'] = agg['total_clientes'] < MIN_AMOSTRA
        primeiro = bucket['clientes'][0]
        for k in extra_keys:
            agg[k] = primeiro.get(k)
        out.append(agg)

    # Pior→melhor por cobertura_clientes; empate → carteira maior primeiro (mais impacto).
    out.sort(key=lambda g: (g['cobertura_clientes'], -g['total_clientes']))
    return out


def agregar_niveis(clientes, coberto_dias=COBERTO_DIAS_PADRAO, base_dias=BASE_DIAS):
    """Placar completo em 3 níveis. Empresa reconcilia com a soma dos times e dos vendedores.

    ⚠️ Só a BASE ATIVA entra (última compra ≤ `base_dias`): o filtro fica AQUI, e não em cada
    chamador, porque tela, CSV, PDF e e-mail de alerta passam por esta função — um filtro em cada
    um deixaria o e-mail com outra régua da tela no primeiro esquecimento.

    Retorna {empresa, times[], vendedores[], coberto_dias, gerado_em}.
    - times: agrupado por codsupervisor (nome = campo `time`), pior→melhor.
    - vendedores: agrupado por codusur (nome = campo `vendedor`); cada um carrega codsupervisor/time
      pro drill Time→RCA no frontend. Pior→melhor.
    """
    clientes = [c for c in clientes if na_base(c.get('recencia_dias'), base_dias)]
    empresa = agregar_grupo(clientes, coberto_dias)
    times = _agrupar_por(clientes, 'codsupervisor', 'time', '(Sem time)', coberto_dias)
    vendedores = _agrupar_por(clientes, 'codusur', 'vendedor', '(Sem RCA)', coberto_dias,
                              extra_keys=('codsupervisor', 'time'))
    return {
        'empresa':     empresa,
        'times':       times,
        'vendedores':  vendedores,
        'coberto_dias': coberto_dias,
        'gerado_em':   datetime.now().isoformat(timespec='seconds'),
    }


def times_rcas_abaixo(niveis, limiar_pct, ignorar=()):
    """Filtra times e vendedores com cobertura_clientes < limiar (0..1) — base do alerta.
    limiar_pct em % (ex.: 60). Retorna {'times': [...], 'vendedores': [...]} pior→melhor
    (as listas já vêm ordenadas de agregar_niveis).
    Só PESSOAS entram: amostra pequena (< MIN_AMOSTRA clientes) e os ids em `ignorar`
    (códigos fictícios, canais) ficam de fora — senão o alerta acusa quem não é gente.
    Marca `alerta` (bool) em cada grupo para a tela usar a MESMA regra do e-mail."""
    lim = (limiar_pct or 0) / 100.0
    ignorar = set(ignorar or ())
    for g in list(niveis['times']) + list(niveis['vendedores']):
        g['alerta'] = (g['cobertura_clientes'] < lim and not g.get('amostra_pequena')
                       and g.get('id') not in ignorar)
    return {
        'times':      [t for t in niveis['times'] if t['alerta']],
        'vendedores': [v for v in niveis['vendedores'] if v['alerta']],
    }
