"""Foto diária do estoque — a matéria-prima da aba Evolução do Estoque.

**Por que existe:** o `PCEST` é POSIÇÃO, não evento. O `QTESTGER` de ontem foi sobrescrito pelo
de hoje e não está guardado em lugar nenhum — nem no BI, nem no Winthor. É por isso que a aba
Vencidos consegue mostrar mês a mês desde sempre (perda por validade é evento datado, fica no
livro) e a evolução do estoque **não pode ser gerada para trás**. A única saída é fotografar
daqui para a frente; cada dia sem foto é um ponto que não volta.

**O que se grava:** os INGREDIENTES CRUS por item, não os números prontos. Guardar o resultado
calculado congelaria a série na régua do dia — e a aba existe justamente para provar gestão,
onde mudança de definição parece resultado. Com o cru, corrigir o `eh_parado` ou mexer no
`novo_dias`/`ideal_dias` recalcula o passado inteiro, sem degrau no gráfico.

**Como roda:** contexto de request simulado, o mesmo padrão do `emails.py` — assim o robô reusa
`_build_produtos()` inteiro (caches, providers, Power BI **ou** Postgres) em vez de reimplementar
a montagem e divergir dela com o tempo.
"""

import os
from datetime import date

from . import core, pbi, store

# Parâmetros da foto: SEMPRE os defaults do servidor, nunca os do navegador de quem estiver
# logado. Os ⚙ Parâmetros vivem no `localStorage` (por pessoa), e uma série gravada com a régua
# de um usuário não bateria com a tela de nenhum outro. Decisão do João (08/2026): "aqui vamos
# usar um padrão e manter padrão".
PARAMS_FOTO = {}          # vazio ⇒ core.merge_params devolve DEFAULTS puro

# Janela de venda usada para apurar a CURVA ABC gravada na foto.
# ⚠️ NÃO pode ser o default ("mes"), que é o ACUMULADO do mês (`hoje.replace(day=1)`): no dia 1º
# a ABC sairia de um único dia de venda e no dia 30 de trinta. Numa série histórica isso vira
# dente de serra em toda virada de mês — itens pulando de curva sem nada ter mudado na operação,
# que é exatamente o "mudança de definição parece resultado" que esta aba existe para evitar.
# 90 dias é janela MÓVEL: estável, comparável entre dias, e já é opção da barra de filtros.
PERIODO_CURVA = "90d"

# colunas gravadas por item, na ordem do INSERT
_COLS = ("data", "unidade", "codprod", "codfornec", "codcomprador", "qtdisp", "custo_unit",
         "valor", "giro_mes", "giro_dia", "cobertura_dias", "dtultsaida", "dtultent",
         "qtd_ja_pedida", "qt_transicao", "curva_abc", "xyz")


def _hoje_ancorado():
    """True quando o "hoje" da instância é FIXO (`ANALYTICS_HOJE`) — é a demo.

    Fotografar ali não faz sentido: a base não envelhece, então o robô reescreveria a mesma data
    todo dia. A demo recebe histórico sintético pelo seeder (`_seed_demo`), que é o que faz a
    aba aparecer cheia na apresentação em vez de vazia."""
    return bool(os.getenv("ANALYTICS_HOJE"))


def pode_fotografar(hoje=None):
    """(pode, motivo) — as guardas, isoladas para o job poder LOGAR por que pulou.

    A foto tem de sair depois do refresh do BI do dia: tirada antes, ela grava a posição de
    ontem carimbada com a data de hoje, e o gráfico ganha um degrau que ninguém consegue
    explicar depois."""
    hoje = hoje or date.today()
    if _hoje_ancorado():
        return False, "instância com ANALYTICS_HOJE fixo (demo) — histórico vem do seeder"
    if not store.ensure():
        return False, "Postgres indisponível"
    if pbi.CONFIG["data_source"] != "powerbi":
        return True, "modo banco (sem refresh de BI a esperar)"
    ref = pbi.get_dataset_refresh()
    if not ref:
        return False, "refresh do BI desconhecido — não fotografo posição de data incerta"
    if ref.get("in_progress"):
        return False, "BI atualizando agora"
    fim = (ref.get("end") or "")[:10]
    if fim != hoje.isoformat():
        return False, f"último refresh do BI é de {fim or '?'}, não de hoje"
    return True, f"BI atualizado {ref.get('end_fmt')}"


def _linha(p, dia, unidade):
    return (dia, unidade, p["codprod"], p.get("codfornec"), p.get("codcomprador"),
            p.get("qtdisp"), p.get("custo_unit"), p.get("valor"),
            p.get("giro_mes"), p.get("giro_dia"), p.get("cobertura_dias"),
            p.get("dtultsaida"), p.get("dtultent"),
            p.get("qtd_ja_pedida"), p.get("qt_transicao"),
            p.get("curva_abc"), p.get("xyz"))


def gravar(dia, unidade, produtos, bi_refresh=None, params=None):
    """Grava (ou regrava) a foto de UMA unidade num dia. Retorna o nº de itens.

    Upsert de propósito: o scheduler é in-process e o Swarm pode subir mais de uma réplica —
    duas fotos do mesmo dia têm de convergir para uma, não duplicar nem estourar a chave."""
    from psycopg2.extras import execute_values
    linhas = [_linha(p, dia, unidade) for p in produtos]
    conn = store.get_db()
    try:
        with conn, conn.cursor() as cur:
            # a regravação limpa o que sobrou: item que saiu do snapshot entre duas execuções do
            # mesmo dia não pode ficar preso na foto com o saldo velho
            cur.execute("DELETE FROM estoque_foto_item WHERE data=%s AND unidade=%s", (dia, unidade))
            if linhas:
                execute_values(
                    cur,
                    f"INSERT INTO estoque_foto_item ({','.join(_COLS)}) VALUES %s",
                    linhas, page_size=1000)
            cur.execute(
                """INSERT INTO estoque_foto_log (data, unidade, n_itens, bi_refresh, params)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (data, unidade) DO UPDATE
                     SET n_itens=EXCLUDED.n_itens, bi_refresh=EXCLUDED.bi_refresh,
                         params=EXCLUDED.params, criado_em=now()""",
                (dia, unidade, len(linhas), bi_refresh, _json(params)))
    finally:
        conn.close()
    # rollup nasce junto da foto: nunca pode existir dia fotografado sem agregado pronto
    gravar_rollup(dia, unidade, [
        (dia, core._n(p.get("qtdisp")), core._n(p.get("valor")), core._n(p.get("giro_dia")),
         p.get("cobertura_dias"), _parse(p.get("dtultsaida")), _parse(p.get("dtultent")),
         p.get("curva_abc"), core._n(p.get("qtd_ja_pedida")), core._n(p.get("qt_transicao")))
        for p in produtos])
    return len(linhas)


def _parse(v):
    """Data (ISO str ou date) → date. `agregar` faz aritmética de datas, então tem de ser date."""
    return v if isinstance(v, date) else core._parse_dt(v)


def _json(v):
    import json
    return json.dumps(v or {}, default=str)


def ja_fotografado(dia, unidades=None):
    """Unidades que JÁ têm foto no dia. O job roda de hora em hora pela manhã (esperando o
    refresh do BI), e sem isto ele refaria as 5 unidades a cada passagem — cada uma custa um
    `_build_produtos` inteiro."""
    if not store.ensure():
        return set()
    conn = store.get_db()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT unidade FROM estoque_foto_log WHERE data=%s", (dia,))
            feitas = {u for (u,) in cur.fetchall()}
    finally:
        conn.close()
    return feitas & set(unidades) if unidades else feitas


def fotografar(app, hoje=None, unidades=None, refazer=False):
    """Tira a foto de TODAS as unidades. Retorna {unidade: n_itens} e a lista de erros.

    Uma unidade que falhar não derruba as outras — mesma política do `gerar_anexos`: é melhor
    ter 4 unidades fotografadas e um erro no log do que perder o dia inteiro."""
    from . import routes as R
    hoje = hoje or date.today()
    ok, motivo = pode_fotografar(hoje)
    if not ok:
        return {}, [f"pulado: {motivo}"]
    ref = pbi.get_dataset_refresh() or {}
    alvo = list(unidades or R.UNIDADES)
    if not refazer:
        prontas = ja_fotografado(hoje, alvo)
        alvo = [u for u in alvo if u not in prontas]
        if not alvo:
            return {}, []
    feitas, erros = {}, []
    for uid in alvo:
        try:
            # `venda_periodo` entra SÓ por causa da CURVA (ver PERIODO_CURVA). Os outros campos que
            # variam com o período (venda, lucro, margem) não são gravados, então o efeito é só
            # tornar a ABC comparável entre dias.
            with app.test_request_context(
                    f"/estoque/api/snapshot?unidade={uid}&venda_periodo={PERIODO_CURVA}"):
                produtos, params, _ = R._build_produtos()
            feitas[uid] = gravar(hoje, uid, produtos, ref.get("end_fmt"), params)
        except Exception as e:                                    # noqa: BLE001
            erros.append(f"{uid}: {e}")
            continue
        # ── estado do dia que não sai do grão do item (ocupação do WMS) ──
        # ⚠️ DEPOIS do `gravar` e num try PRÓPRIO: a ocupação vem de outra fonte (PCENDERECO, o
        # WMS) e uma falha dela não pode custar a foto do estoque, que é o dado principal. Sem
        # esta separação, o depósito com WMS fora do ar derrubaria a série inteira do dia.
        # ⚠️ Cada métrica num try PRÓPRIO e gravada sozinha (o UPSERT faz merge de chave). Uma
        # fonte fora do ar — WMS, cadastro, pedidos — não pode custar as outras nem a foto do
        # estoque, que é o dado principal. Degradar é perder UMA métrica de UM dia; um try único
        # perderia o dia inteiro de estado.
        for chave, fn in _ESTADO_DO_DIA.items():
            try:
                gravar_estado(hoje, uid, {chave: fn(app, uid)})
            except Exception as e:                                # noqa: BLE001
                erros.append(f"{uid} (estado/{chave}): {e}")
    return feitas, erros


def _ocupacao_do_dia(app, uid):
    """Só os ESCALARES da ocupação — não a lista de ruas nem as vagas vazias.

    ⚠️ O pedido do diretor foi explícito ("só o percentual de ocupação"), e guardar o resto seria
    caro sem responder mais: rua e vaga vazia são um retrato de ENDEREÇO, e o que a série precisa
    é "o depósito está enchendo?". `pct` é derivado e vai gravado junto de propósito — ele é o
    número que a tela mostra, e ter o par (ocupadas, posições) ao lado permite refazer a conta se
    a régua do denominador mudar um dia."""
    from . import routes as R
    with app.test_request_context(f"/estoque/api/ocupacao?unidade={uid}"):
        filiais = R._filiais_estoque()
        if R._pg():
            oc = core.ocupacao_resumo(R.PS.ocupacao_kpis(filiais), R.PS.ocupacao_por_rua(filiais),
                                      R.PS.ocupacao_por_tipo(filiais))
        else:
            oc = core.ocupacao_resumo(pbi.run_dax(R.Q.q_ocupacao_kpis(filiais)),
                                      pbi.run_dax(R.Q.q_ocupacao_por_rua(filiais)),
                                      pbi.run_dax(R.Q.q_ocupacao_por_tipo(filiais)))
    pos, occ = int(oc.get("posicoes") or 0), int(oc.get("ocupadas") or 0)
    return {"posicoes": pos, "ocupadas": occ, "livres": int(oc.get("livres") or 0),
            "bloqueados": int(oc.get("bloqueados") or 0),
            "com_estoque": int(oc.get("com_estoque") or 0),
            # ⚠️ **`pct_ocupado` VERBATIM do `ocupacao_resumo`** — a MESMA chave que a aba Ocupação
            # lê, e não uma reconta. É FRAÇÃO (0-1) arredondada a 4 casas, e a tela a renderiza com
            # o helper `pct()` (Intl, arredondamento half-expand).
            #
            # Recalcular aqui como `occ/pos*100` arredondado a 1 casa parecia inofensivo e **dava
            # outro número**: 4.446/5.290 = 0,8404536… → a aba mostra **84,1%** (0,8405 pelo Intl) e a
            # reconta dava **84,0%**. Mesmo dia, mesmo dado, duas telas discordando em 0,1 p.p. —
            # exatamente o defeito do card "Em risco" (789 SKUs no card × 791 na lista), que nasceu
            # de refazer a conta a partir de valores já arredondados.
            #
            # O par (ocupadas, posicoes) segue gravado ao lado para auditoria e para permitir
            # refazer a conta se a régua do denominador mudar um dia.
            "pct_ocupado": oc.get("pct_ocupado"),
            # picking x pulmão: encher o pulmão é normal, encher o picking trava a separação
            "tipos": {t.get("tipo"): {"posicoes": t.get("posicoes"), "ocupadas": t.get("ocupadas")}
                      for t in (oc.get("tipos") or []) if t.get("tipo")}}


def _qualidade_do_dia(app, uid):
    """Cadastros quebrados na BASE INTEIRA (não no snapshot) — o mesmo universo da aba Qualidade.

    ⚠️ **Estado puro, e o exemplo mais claro do critério.** Quando o TI corrige um cadastro, o
    valor anterior é SOBRESCRITO: não existe forma de saber quantos estavam errados em julho.
    É a métrica que transforma "mandei a lista pro TI" em "caiu de 72 para 40" — e ela só existe
    se alguém fotografar. Custo: zero query nova (cadastro e embalagem já estão em cache)."""
    from . import routes as R
    with app.test_request_context(f"/estoque/api/qualidade-cadastro?unidade={uid}"):
        res = core.qualidade_cadastro(R._cadastro_produtos(), R._embalagem_map(),
                                      R._cadastro_fornecedores(), R._compradores_map())
    r = res.get("resumo") or {}
    return {"total": r.get("total"), "base": r.get("base"),
            # `contagem` é {categoria: n} — viaja inteira para uma categoria nova aparecer
            # sozinha no histórico, sem precisar mexer aqui de novo
            "contagem": r.get("contagem") or {}}


def _validade_do_dia(app, uid):
    """Valor em RISCO de vencer, por faixa — a foto do FEFO.

    ⚠️ Risco é ESTADO, perda é EVENTO: a aba Vencidos mostra mês a mês desde sempre porque baixa
    por validade fica no livro (PCLANC), mas quanto estava a vencer em 30 dias numa data passada
    não existe em lugar nenhum — a quantidade de cada lote é sobrescrita. É o par que melhor
    ilustra por que umas métricas se fotografam e outras não."""
    from . import routes as R
    from datetime import timedelta
    with app.test_request_context(f"/estoque/api/resumos?unidade={uid}"):
        hoje = R._hoje()
        filiais = R._filiais_estoque()
        _jan = (hoje, hoje + timedelta(days=3650))
        lotes = (R.PS.validade(*_jan, filiais) if R._pg()
                 else pbi.run_dax(R.Q.q_validade(*_jan, filiais)))
        produtos, _params, _ = R._build_produtos()
        idx = {p["codprod"]: p for p in produtos}
        res = core.resumo_validade(lotes, idx, hoje=hoje)
    tot = res.get("total") or {}
    return {"itens": tot.get("itens"), "valor": tot.get("valor"),
            "faixas": {f.get("faixa"): {"itens": f.get("itens"), "valor": f.get("valor")}
                       for f in (res.get("faixas") or []) if f.get("faixa")}}


def _pedidos_do_dia(app, uid):
    """Posição dos pedidos EM ABERTO: valor, atrasados, chegando em 7 dias.

    ⚠️ `PCITEM[QTENTREGUE]` é campo CUMULATIVO corrente — o Winthor o atualiza a cada recebimento
    e não guarda o histórico. Não há como saber o que estava em aberto em 15/08. Fotografado, isto
    vira performance de fornecedor MEDIDA ao longo do tempo, em vez de impressão."""
    from . import routes as R
    with app.test_request_context(f"/estoque/api/orcamento?unidade={uid}"):
        hoje = R._hoje()
        filiais = R._filiais_estoque()
        cab = R._pedidos_data(filiais, hoje)["cab"]
        venda_comp = R._venda_comprador_30d(filiais, R._filiais_venda(), hoje)
        orc = core.orcamento_winthor(cab, venda_comp, R._compradores_map(),
                                     R._cadastro_fornecedores(), R._mes_atual(), "TODOS",
                                     pct=0.65, hoje=hoje, meta_override=None,
                                     cnpj_empresa=R.MULTPEL_EMPRESA["cnpj"])
    r = orc.get("resumo") or {}
    return {"n_abertos": r.get("n_abertos"), "n_atrasados": r.get("n_atrasados"),
            "n_chega7": r.get("n_chega7"), "valor_aberto": r.get("valor_aberto"),
            # o orçamento do mês vai junto: a meta é 65% da venda de 30d MEDIDA NAQUELE DIA, e
            # reconstruí-la depois com a venda de hoje produziria uma meta que nunca existiu
            "meta": r.get("meta"), "comprado": r.get("comprado"), "saldo": r.get("saldo")}


def _avaria_do_dia(app, uid):
    """Mercadoria BLOQUEADA que não é pré-entrada — avaria de verdade, em quantidade e R$.

    ⚠️ A foto do item guarda o `qtdisp` JÁ LÍQUIDO de avaria, e o `qtbloq` não está entre as
    colunas — então "a avaria está crescendo?" hoje não tem resposta. Aqui ela vira escalar sem
    tocar no grão do item (que é ~6k linhas/dia e custaria coluna nova no SELECT posicional).

    A separação avaria × pré-entrada é a mesma heurística do `core.qt_em_transicao`, com o cap
    por `QTULTENT`: 200 un bloqueadas com entrada de 12 = 12 chegando, 188 avaria."""
    from . import routes as R
    with app.test_request_context(
            f"/estoque/api/snapshot?unidade={uid}&venda_periodo={PERIODO_CURVA}"):
        produtos, _params, _ = R._build_produtos()
    n = qt = valor = 0.0
    for p in produtos:
        bloq = core._n(p.get("qtbloq"))
        transito = core._n(p.get("qt_transicao"))
        av = max(0.0, bloq - transito)
        if av > 0:
            n += 1
            qt += av
            valor += av * core._n(p.get("custo_unit"))
    return {"n_itens": int(n), "qt": core._round(qt, 2), "valor": core._round(valor)}


# Registro das métricas de ESTADO do dia. ⚠️ Métrica nova entra AQUI e mais nada: a tabela é
# `payload JSONB` com merge no upsert, então não há migration, e o passado simplesmente não tem
# a chave — que é a verdade (ninguém mediu antes).
#
# ⚠️ O critério para entrar: **estado sobrescrito, não evento datado**. Lead time, verbas,
# vencidos, compras×vendas e desempenho comercial NÃO entram — todos derivam de fatos com data,
# que o livro já guarda e o app recalcula a qualquer momento. Fotografá-los só duplicaria o dado
# com risco de divergir da fonte.
_ESTADO_DO_DIA = {
    "ocupacao":  _ocupacao_do_dia,
    "qualidade": _qualidade_do_dia,
    "validade":  _validade_do_dia,
    "pedidos":   _pedidos_do_dia,
    "avaria":    _avaria_do_dia,
}

def gravar_estado(dia, unidade, payload):
    """UPSERT do estado do dia. MERGE por chave (`payload || novo`), não substituição: métrica de
    estado nova entra sem apagar as que já foram medidas naquele dia."""
    if not store.ensure():
        return
    conn = store.get_db()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO estoque_foto_estado (data, unidade, payload)
                           VALUES (%s,%s,%s::jsonb)
                           ON CONFLICT (data, unidade) DO UPDATE
                              SET payload = estoque_foto_estado.payload || EXCLUDED.payload""",
                        (dia, unidade, _json(payload)))
    finally:
        conn.close()


# ───────────────────────── leitura (alimenta a aba) ─────────────────────────
def dias_com_foto(unidade, ini=None, fim=None):
    """Datas em que a foto REALMENTE saiu. A tela usa isto para desenhar buraco onde o robô não
    rodou, em vez de ligar os pontos e inventar uma reta que ninguém mediu."""
    conn = store.get_db()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""SELECT data, n_itens FROM estoque_foto_log
                           WHERE unidade=%s AND (%s::date IS NULL OR data>=%s)
                             AND (%s::date IS NULL OR data<=%s)
                           ORDER BY data""", (unidade, ini, ini, fim, fim))
            return [{"data": d.isoformat(), "n_itens": n} for d, n in cur.fetchall()]
    finally:
        conn.close()


# Únicos parâmetros que mudam o resultado de `agregar`. A querystring da tela carrega dezenas
# (lead time, forecast, cortes ABC…) que não tocam nenhuma das 4 séries — exigir igualdade de
# todos jogaria o cache fora em 100% dos acessos.
_PARAMS_DA_SERIE = ("novo_dias", "ideal_dias",
                    # a watchlist Em desaceleração é recalculada por estes 4 — fora daqui,
                    # mexer nos campos de ⚙ Parâmetros serviria a série cacheada de antes
                    "desacel_de", "desacel_ate", "desacel_cob", "desacel_valor_min")

# ⚠️ A ordem deste SELECT É a assinatura posicional que o `agregar` desempacota. Coluna nova
# tem de entrar nos DOIS lugares (e no builder do `gravar_rollup`), senão o valor entra no campo
# errado sem erro nenhum. Gate: `test_a_ordem_do_select_casa_com_o_agregar`.
_SQL_CRU = """
    SELECT data, qtdisp, valor, giro_dia, cobertura_dias, dtultsaida, dtultent, curva_abc,
           qtd_ja_pedida, qt_transicao
      FROM estoque_foto_item
     WHERE unidade=%s
       AND (%s::date IS NULL OR data>=%s) AND (%s::date IS NULL OR data<=%s)
       AND (%s::int  IS NULL OR codcomprador=%s)
       AND (%s::int  IS NULL OR codfornec=%s)
       AND (%s::text IS NULL OR curva_abc=%s)
       AND (%s::text IS NULL OR xyz=%s)
"""


def _linhas_cruas(unidade, ini=None, fim=None, comprador=None, fornecedor=None, dia=None,
                  curva=None, xyz=None):
    sql = _SQL_CRU + (" AND data=%s" if dia else "")
    args = [unidade, ini, ini, fim, fim, comprador, comprador, fornecedor, fornecedor,
            curva, curva, xyz, xyz]
    if dia:
        args.append(dia)
    conn = store.get_db()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall()
    finally:
        conn.close()


def _regua_padrao(params):
    p = core.merge_params(params or {})
    return all(p[k] == core.DEFAULTS[k] for k in _PARAMS_DA_SERIE)


# Chaves que o `agregar` de HOJE produz. Rollup gravado por uma versão anterior não as tem, e
# servi-lo faria a aba mostrar "—" numa métrica que o cru sabe calcular.
_ROLLUP_CHAVES = ("valor_estoque", "valor_parado", "n_ruptura", "pct_ideal", "faixas",
                  "pct_ruptura", "ruptura_curva", "valor_desacel", "n_desacel",
                  "n_rup_sem_prov")

# ⚠️ VERSÃO DA SEMÂNTICA. A checagem por chaves só pega métrica NOVA; ela não vê quando uma
# métrica existente muda de significado — e foi exatamente o que aconteceu ao alinhar o
# `valor_parado` com a régua do Cockpit (60+ dias no lugar de 15+): mesmas chaves, número
# diferente. Sem este selo, os rollups já gravados seguiriam servindo o valor antigo em silêncio,
# que é o pior modo de falha possível numa aba feita para provar gestão.
# **Suba este número sempre que mudar o resultado do `agregar`.**
#   2 → 08/2026: capital parado passou a usar `core.status_parado_de` (piso 60 dias).
#   3 → 08/2026: entrou a watchlist "Em desaceleração" (`valor_desacel`/`n_desacel`). Aqui as
#       chaves são NOVAS, então a checagem por chaves já pegaria; o selo sobe assim mesmo porque
#       depender de qual dos dois mecanismos salvou é como o parado passou despercebido.
#   4 → 08/2026: `n_rup_sem_prov` (régua da Meta de ruptura) + o SELECT do cru ganhou
#       `qtd_ja_pedida`/`qt_transicao`. Rollup gravado antes não tem a chave.
_ROLLUP_VERSAO = 4


def _rollup_atual(payload):
    """O payload foi gravado pela versão CORRENTE do `agregar`?

    ⚠️ Sem isto, toda mudança no agregado exigia lembrar de rodar o `rebuild_rollup` no deploy —
    e esquecer não dava erro: a aba servia o agregado velho em silêncio. O rollup é cache da
    mesma função; cache de uma versão anterior é cache que mente."""
    return (isinstance(payload, dict)
            and payload.get("_v") == _ROLLUP_VERSAO
            and all(k in payload for k in _ROLLUP_CHAVES))


def _serie_rollup(unidade, ini, fim):
    """Lê o rollup pronto. Devolve None se QUALQUER dia da janela ainda não foi rolado **ou foi
    rolado por uma versão anterior do `agregar`** — aí a leitura cai no cru, que é sempre
    correto. Cache que mente é pior que cache que falta."""
    conn = store.get_db()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""SELECT l.data, r.payload
                             FROM estoque_foto_log l
                             LEFT JOIN estoque_foto_dia r ON r.data=l.data AND r.unidade=l.unidade
                            WHERE l.unidade=%s AND (%s::date IS NULL OR l.data>=%s)
                              AND (%s::date IS NULL OR l.data<=%s)
                            ORDER BY l.data""", (unidade, ini, ini, fim, fim))
            rows = cur.fetchall()
    finally:
        conn.close()
    if not rows or any(not _rollup_atual(pl) for _d, pl in rows):
        return None
    return [pl for _d, pl in rows]


def serie(unidade, ini=None, fim=None, comprador=None, fornecedor=None, params=None,
          curva=None, xyz=None):
    """As 4 séries por dia, DERIVADAS da foto crua — nunca de um agregado gravado como verdade.

    Caminho rápido: sem filtro e com a régua padrão, lê o rollup (que é cache da MESMA função
    `agregar`, gravado junto da foto). Medido antes do rollup: 3,5s para 45 dias / 129 mil linhas,
    ~28s projetados para 12 meses — e piorando a cada dia de história.

    Caminho completo: QUALQUER recorte (comprador, fornecedor, curva, XYZ) ou ⚙ Parâmetros fora
    do padrão recalcula do cru. É aqui que o "reescrever o passado" acontece — e é por isso que o
    rollup nunca pode ser a única fonte.

    ⚠️ Filtro novo tem de entrar NA CONDIÇÃO do rollup também. Esquecer isso serviria o agregado
    da empresa inteira para quem pediu a curva A — sem erro nenhum, só o número errado.

    Contrapeso: `n_ruptura` viaja junto com o valor de estoque de propósito. Estoque caindo,
    sozinho, não é boa notícia — pode ser desabastecimento.
    """
    sem_recorte = not any((comprador, fornecedor, curva, xyz))
    if sem_recorte and _regua_padrao(params):
        pronto = _serie_rollup(unidade, ini, fim)
        if pronto is not None:
            return _com_estado(pronto, unidade, ini, fim, sem_recorte)
    dias = agregar(_linhas_cruas(unidade, ini, fim, comprador, fornecedor,
                                 curva=curva, xyz=xyz), params)
    return _com_estado(dias, unidade, ini, fim, sem_recorte)


def _com_estado(dias, unidade, ini, fim, sem_recorte):
    """Costura o estado do dia (ocupação do WMS) nas séries, num lugar SÓ.

    ⚠️ Merge feito aqui e não dentro do `agregar` porque o `agregar` é PURO (recebe linhas, não
    toca banco) — e é essa pureza que permite recalcular o passado com parâmetro novo. Fazê-lo
    ler outra tabela transformaria a função em I/O e mataria os testes de recálculo.

    ⚠️ **Com recorte ativo a ocupação NÃO viaja.** Ela é do DEPÓSITO (grão = posição do WMS) e
    não se decompõe por comprador, fornecedor, curva ou XYZ. Servi-la ao lado de um gráfico
    filtrado pela curva A mostraria o número da empresa inteira como se respondesse ao filtro —
    a falha clássica do módulo (foi assim que a tabela de Verbas ficou ao lado do gráfico da
    empresa toda). Ausente, o front mostra "—" e diz por quê."""
    if not dias or not sem_recorte:
        return dias
    est = _estado_por_dia(unidade, ini, fim)
    for d in dias:
        do_dia = est.get(d["data"]) or {}
        # ⚠️ Merge por CHAVE, sem lista fixa: métrica de estado nova aparece na série sozinha,
        # sem passar por aqui. `setdefault` para nunca sobrescrever uma chave que o `agregar`
        # calculou — o estado ACRESCENTA à série, nunca disputa com ela.
        for chave, val in do_dia.items():
            if chave != "ocupacao":
                d.setdefault(chave, val)
        oc = do_dia.get("ocupacao")
        if oc:
            # FRAÇÃO (0-1), igual à aba Ocupação. O front usa o mesmo helper `pct()` das duas
            # telas — é o que garante que elas não divirjam na 1ª casa decimal.
            d["ocupacao_pct"] = oc.get("pct_ocupado")
            d["ocupacao"] = oc
    return dias


def _estado_por_dia(unidade, ini=None, fim=None):
    """{data_iso: payload} da `estoque_foto_estado`. Degrada para {} — a ocupação é um
    acréscimo à aba, e uma falha na leitura dela não pode derrubar a série do estoque."""
    if not store.ensure():
        return {}
    try:
        conn = store.get_db()
        try:
            with conn, conn.cursor() as cur:
                cur.execute("""SELECT data, payload FROM estoque_foto_estado
                               WHERE unidade=%s AND (%s::date IS NULL OR data>=%s)
                                 AND (%s::date IS NULL OR data<=%s)""",
                            (unidade, ini, ini, fim, fim))
                return {d.isoformat(): (pl or {}) for d, pl in cur.fetchall()}
        finally:
            conn.close()
    except Exception as e:                                        # noqa: BLE001
        print(f"[historico] estado do dia indisponivel ({e}).")
        return {}


def gravar_rollup(dia, unidade, linhas_cruas):
    """Grava o agregado do dia na régua PADRÃO. Chamado pelo `gravar` — o rollup nasce junto
    da foto para nunca existir dia com foto e sem agregado."""
    agg = agregar(linhas_cruas)
    if agg:
        agg[0]["_v"] = _ROLLUP_VERSAO
    conn = store.get_db()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO estoque_foto_dia (data, unidade, payload)
                           VALUES (%s,%s,%s::jsonb)
                           ON CONFLICT (data, unidade) DO UPDATE SET payload=EXCLUDED.payload""",
                        (dia, unidade, _json(agg[0] if agg else {})))
    finally:
        conn.close()


def rebuild_rollup(unidade=None):
    """Reconstrói o rollup a partir da foto crua.

    Depois de mudar uma régua ou acrescentar uma métrica, a CORREÇÃO já se resolve sozinha (o
    `_rollup_atual` derruba o payload velho e a leitura cai no cru). O que este rebuild devolve
    é a PERFORMANCE: sem ele toda leitura recalcula a janela inteira, que era 3,5s para 45 dias.
    Rodar no deploy continua sendo o certo — só deixou de ser o que separa número certo de
    número errado."""
    conn = store.get_db()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""SELECT DISTINCT data, unidade FROM estoque_foto_item
                           WHERE (%s::text IS NULL OR unidade=%s) ORDER BY unidade, data""",
                        (unidade, unidade))
            chaves = cur.fetchall()
    finally:
        conn.close()
    for dia, uni in chaves:
        gravar_rollup(dia, uni, _linhas_cruas(uni, dia=dia))
    return len(chaves)


CURVAS = ("A", "B", "C")


def curva_de(c):
    """Curva do item NA FOTO, normalizada. **Item sem curva entra na C** — mesma leitura do
    placar da Meta de ruptura ("a cauda longa; item sem curva entra aqui"). Divergir disso faria
    duas telas do mesmo módulo somarem universos diferentes."""
    c = (str(c or "").strip().upper() or "C")[:1]
    return c if c in CURVAS else "C"


def agregar(linhas, params=None):
    """As 4 séries por dia a partir das linhas CRUS da foto — função PURA, sem I/O.

    Separada do `serie()` de propósito: é ela que materializa a decisão de guardar ingrediente
    em vez de resultado. Todo o "recalcular o passado" mora aqui — troque `params` e o gráfico
    inteiro se redesenha sem uma linha do banco mudar.

    **Ruptura por curva** (08/2026, pedido do diretor: "trazer a ruptura por curva ABC na foto
    do dia, e incluir o % de ruptura além da quantidade de itens").

    ⚠️ É a ruptura REAL — item zerado com giro, tenha ou não pedido em aberto. Decisão dele:
    "pode usar a ruptura real, esquece a ruptura da meta; o objetivo é medir a evolução da real,
    o que tem ou não tem de fato no estoque". Portanto **este número NÃO é o do placar da Meta
    de ruptura**, que conta só o que está sem providência (`core._sem_providencia`) e é sempre
    menor. As duas réguas convivem de propósito e a tela declara qual está mostrando — ler 11%
    daqui contra a meta de 2% de lá seria concluir catástrofe onde não há.

    O denominador é o TOTAL de SKUs da curva na foto, que é o mesmo do KPI ("314 de 2.878").

    `linhas`: (data, qtdisp, valor, giro_dia, cobertura_dias, dtultsaida, dtultent, curva_abc,
    qtd_ja_pedida, qt_transicao).
    """
    p = core.merge_params(params or {})
    limiar, _meta = core.regua_estoque_ideal(p)
    novo_dias = int(p["novo_dias"])
    por_dia = {}
    for dia, qtdisp, valor, giro_dia, cob, dtsaida, dtent, curva, ja_ped, transito in linhas:
        d = por_dia.setdefault(dia, {"data": dia.isoformat(), "n_skus": 0, "valor_estoque": 0.0,
                                     "valor_parado": 0.0, "n_ruptura": 0,
                                     "valor_desacel": 0.0, "n_desacel": 0,
                                     "n_rup_sem_prov": 0,
                                     "faixas": {n: 0.0 for n, _ in core._FAIXAS_COB_LIM},
                                     "ideal_n": 0, "risco_n": 0, "semgiro_n": 0,
                                     "_rup_cv": {c: 0 for c in CURVAS},
                                     "_skus_cv": {c: 0 for c in CURVAS}})
        qtdisp = core._n(qtdisp); valor = core._n(valor); giro_dia = core._n(giro_dia)
        cv = curva_de(curva)
        d["n_skus"] += 1
        d["_skus_cv"][cv] += 1
        d["valor_estoque"] += valor
        if qtdisp <= 0 and giro_dia > 0:
            d["n_ruptura"] += 1
            d["_rup_cv"][cv] += 1
            # ⚠️ A régua da META de ruptura, que é OUTRA: conta só o item **sem providência** —
            # nem pedido em aberto nem mercadoria em pré-entrada. Sempre MENOR que a ruptura real
            # (`n_ruptura`), e as duas convivem de propósito: a real mede "o que falta de fato",
            # esta mede "o que ninguém providenciou". Ler uma pela outra é concluir catástrofe
            # onde não há — a tela declara qual está mostrando.
            #
            # Saiu **de graça e com histórico retroativo**: `qtd_ja_pedida` e `qt_transicao` já
            # eram gravadas no grão do item desde o 1º dia, só não eram lidas. Espelha
            # `core._sem_providencia`.
            if core._n(ja_ped) <= 0 and core._n(transito) <= 0:
                d["n_rup_sem_prov"] += 1
        # "parado" reconstruído pela MESMA régua do Cockpit, a partir das datas gravadas.
        # ⚠️ Mudou em 08/2026: usava `parado_faixa_de`, cujo piso é 15 dias — a série dizia
        # R$ 433.647 onde o Cockpit dizia R$ 181.182, no mesmo dia e na mesma base. Não eram
        # conceitos diferentes: somando as faixas de 61 dias para cima a régua antiga dava
        # R$ 181.155, ou seja, a mesma conta com piso diferente. Item sem venda há 20 dias é
        # ROTAÇÃO num distribuidor, não dead stock — a faixa 15-30 sozinha eram R$ 151.699, e
        # chamá-la de capital parado fazia o KPI gritar lobo.
        # A aba Estoque parado continua em 15+ de propósito: lá o papel é mostrar o gradiente.
        # Trocar aqui não perde história — a foto guarda o INGREDIENTE, então o passado inteiro
        # se recalcula (é a decisão de gravar o cru pagando pela segunda vez).
        dsv = (dia - dtsaida).days if dtsaida else None
        dse = (dia - dtent).days if dtent else None
        # ⚠️ `cobd` é calculado AQUI (antes do parado/desaceleração) e reusado nas faixas mais
        # abaixo: a watchlist precisa da MESMA cobertura que a tela usa. Recalcular a partir dos
        # valores já arredondados do dict daria um `ceil` diferente — é o defeito que o card
        # "Em risco" do Painel gerencial teve (789 SKUs no card × 791 na lista).
        cobd = int(cob) if cob is not None else core.cobertura_dias_oficial(qtdisp, giro_dia)
        st_par = core.status_parado_de(dsv, qtdisp, dse, novo_dias)
        if core.eh_parado({"status_parado": st_par}):
            d["valor_parado"] += valor
        # Watchlist "Em desaceleração" — o aviso ANTES do dead stock, pela MESMA função da tela.
        # ⚠️ Ela nasce com todo o histórico que a foto já tem (a régua sai de `dias sem venda` +
        # `cobertura_dias` + `valor`, os três gravados desde o 1º dia): é o segundo pagamento da
        # decisão de guardar o INGREDIENTE. Se a foto guardasse "parado = R$ X", esta métrica só
        # poderia começar hoje — e uma série que começa do zero não responde "está melhorando?".
        elif core.em_desaceleracao({"status_parado": st_par, "dias_sem_venda": dsv,
                                    "qtdisp": qtdisp, "cobertura_dias": cobd,
                                    "valor": valor}, p):
            d["valor_desacel"] += valor
            d["n_desacel"] += 1
        # ⚠️ DUAS convenções, e as duas espelham a tela de propósito:
        # · as FAIXAS seguem o Painel gerencial (`core.resumo_cobertura`), onde giro<=0 vira
        #   cobertura 9999 e cai no 121+. Assim Σ faixas == valor de estoque, que é o que o
        #   gráfico de área empilhada precisa — e o total bate com o KPI do dia.
        # · o trio ideal/risco/sem-giro segue o `core.resumo_estoque_ideal`, que reporta o
        #   sem-giro À PARTE para não distorcer o %.
        # Unificar as duas faria a série discordar de uma das telas que ela promete reproduzir.
        d["faixas"][core.cobertura_faixa_de(cobd)] += valor
        if giro_dia <= 0:
            d["semgiro_n"] += 1
        elif cobd < limiar:
            d["risco_n"] += 1
        else:
            d["ideal_n"] += 1
    saida = []
    for d in sorted(por_dia.values(), key=lambda x: x["data"]):
        d["valor_estoque"] = core._round(d["valor_estoque"])
        d["valor_parado"] = core._round(d["valor_parado"])
        d["valor_desacel"] = core._round(d["valor_desacel"])
        d["faixas"] = {k: core._round(v) for k, v in d["faixas"].items()}
        com_giro = d["ideal_n"] + d["risco_n"]
        d["pct_ideal"] = core._round(d["ideal_n"] / com_giro, 4) if com_giro else None
        # % de ruptura sobre o TOTAL de SKUs — a mesma conta que o KPI já exibia como "314 de
        # 2.878". Por curva, é ela que torna A e C comparáveis: a C tem ~6x mais itens, então a
        # contagem crua sempre a faria parecer a pior.
        d["pct_ruptura"] = (core._round(d["n_ruptura"] / d["n_skus"] * 100, 1)
                            if d["n_skus"] else None)
        rup_cv, skus_cv = d.pop("_rup_cv"), d.pop("_skus_cv")
        d["ruptura_curva"] = {c: {"n": rup_cv[c], "skus": skus_cv[c],
                                  "pct": (core._round(rup_cv[c] / skus_cv[c] * 100, 1)
                                          if skus_cv[c] else None)}
                              for c in CURVAS}
        d["pct_parado"] = (core._round(d["valor_parado"] / d["valor_estoque"] * 100, 1)
                           if d["valor_estoque"] else None)
        saida.append(d)
    return saida
