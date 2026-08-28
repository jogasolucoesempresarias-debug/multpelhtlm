"""
Persistência (Postgres) do painel de estoque — estado editável que o Power BI não guarda:
orçamento de compras, pedidos lançados e planos de ação (validade/cobertura).

Reutiliza o Postgres do projeto principal (mesmas vars DB_* do .env). Tabelas com
prefixo `estoque_` para não colidir com o app Multpel.
"""

import os
from datetime import datetime
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# .env da RAIZ do app — o módulo virou subpacote na fusão (ver nota igual em pbi.py).
load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")


def get_db():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "multpel_db"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", ""),
    )


DDL = """
CREATE TABLE IF NOT EXISTS estoque_orcamento (
    mes         TEXT NOT NULL,
    comprador   TEXT NOT NULL DEFAULT 'TODOS',
    meta_valor  NUMERIC NOT NULL DEFAULT 0,
    PRIMARY KEY (mes, comprador)
);
CREATE TABLE IF NOT EXISTS estoque_pedidos (
    id            SERIAL PRIMARY KEY,
    data_pedido   DATE,
    mes           TEXT,
    comprador     TEXT,
    codfornec     INTEGER,
    fornecedor    TEXT,
    n_pedido      TEXT,
    valor         NUMERIC NOT NULL DEFAULT 0,
    prazo_dias    INTEGER,
    dt_vencimento DATE,
    status        TEXT DEFAULT 'ABERTO',
    forma_pgto    TEXT,
    obs           TEXT,
    criado_em     TIMESTAMP DEFAULT now()
);
CREATE TABLE IF NOT EXISTS estoque_pedido_itens (
    id          SERIAL PRIMARY KEY,
    pedido_id   INTEGER NOT NULL REFERENCES estoque_pedidos(id) ON DELETE CASCADE,
    codprod     INTEGER,
    descricao   TEXT,
    qtdisp      NUMERIC,
    cobertura   NUMERIC,
    giro_mes    NUMERIC,
    qtunitcx    NUMERIC,
    qtd         NUMERIC,
    custo_unit  NUMERIC,
    valor       NUMERIC
);
CREATE TABLE IF NOT EXISTS estoque_planos_acao (
    chave         TEXT PRIMARY KEY,
    tipo          TEXT NOT NULL,
    codprod       INTEGER,
    dtvalidade    DATE,
    descricao     TEXT,
    responsavel   TEXT,
    acao          TEXT,
    prazo         DATE,
    status        TEXT DEFAULT 'PENDENTE',
    obs           TEXT,
    criado_em     TIMESTAMP DEFAULT now(),
    atualizado_em TIMESTAMP DEFAULT now()
);
-- migração: pedido manual vira "ordem gerada na nossa plataforma", pendente de envio ao
-- Winthor. Quando sincronizado, sai do orçamento (o realizado passa a vir do Winthor real).
ALTER TABLE estoque_pedidos ADD COLUMN IF NOT EXISTS origem TEXT DEFAULT 'NOSSO_SISTEMA';
ALTER TABLE estoque_pedidos ADD COLUMN IF NOT EXISTS sincronizado_winthor BOOLEAN DEFAULT false;
ALTER TABLE estoque_pedidos ADD COLUMN IF NOT EXISTS numped_winthor TEXT;
-- migração: pedido passa a guardar as DUAS réguas. `valor` continua sendo a MERCADORIA (é ele
-- que vira preço na planilha de importação — o Winthor calcula o imposto sozinho no cadastro);
-- `valor_nf` é mercadoria+IPI+ST, a régua do Orçamento (PCPEDIDO[VLTOTAL]). Alíquotas ficam
-- gravadas por item porque o pedido é um SNAPSHOT: reimprimir meses depois não pode mudar o
-- documento se a tributação do fornecedor tiver mudado nesse meio-tempo.
ALTER TABLE estoque_pedidos ADD COLUMN IF NOT EXISTS valor_nf NUMERIC;
-- SEM default: NULL = item gravado antes desta migração (o PDF cai no % do cadastro, como
-- sempre fez) e 0 = alíquota zero REALMENTE praticada pelo fornecedor. Um DEFAULT 0 apagaria
-- essa diferença e faria pedido antigo imprimir "sem IPI".
ALTER TABLE estoque_pedido_itens ADD COLUMN IF NOT EXISTS perc_ipi NUMERIC;
ALTER TABLE estoque_pedido_itens ADD COLUMN IF NOT EXISTS perc_st NUMERIC;
-- ───────────────────────── foto diária do estoque (aba Evolução) ─────────────────────────
-- ⚠️ Guarda os INGREDIENTES CRUS, não os números prontos. O PCEST é POSIÇÃO: o saldo de ontem
-- é sobrescrito e não existe em lugar nenhum (é por isso que o histórico não pode ser gerado
-- para trás — diferente da perda por validade, que é EVENTO datado e por isso a aba Vencidos
-- consegue mostrar mês a mês desde sempre).
--
-- Gravar o resultado calculado congelaria a série na régua do dia. Com o cru, toda régua é
-- recalculável para trás: corrigir o `eh_parado`, mexer no `novo_dias` ou no `ideal_dias` NÃO
-- cria degrau no gráfico. Isso importa porque a aba existe para PROVAR gestão — e num gráfico
-- assim, mudança de definição parece resultado.
--
-- Grão: data × unidade × produto. Medido na base real da Multpel (08/2026): 4.519 produtos de
-- revenda, ~6.081 linhas de posição por dia somando as unidades ⇒ ~2,2M linhas/ano (~265 MB).
-- `unidade` inclui "todas", que se sobrepõe às demais de propósito: faixa de cobertura NÃO é
-- decomponível por filial (o giro soma junto com o saldo), então cada unidade precisa da sua
-- própria foto para bater exatamente com o que a tela mostrou.
CREATE TABLE IF NOT EXISTS estoque_foto_item (
    data           DATE    NOT NULL,
    unidade        TEXT    NOT NULL,
    codprod        INTEGER NOT NULL,
    -- gravados no dia (e não derivados do cadastro na hora da leitura): remanejar um comprador
    -- amanhã não pode reescrever o passado dele
    codfornec      INTEGER,
    codcomprador   INTEGER,
    qtdisp         NUMERIC,
    custo_unit     NUMERIC,
    valor          NUMERIC,   -- max(0,qtdisp)×custo, igual ao que a tela soma
    giro_mes       NUMERIC,
    giro_dia       NUMERIC,
    cobertura_dias INTEGER,
    dtultsaida     DATE,      -- com dtultent, é o que permite recalcular "parado" depois
    dtultent       DATE,
    qtd_ja_pedida  NUMERIC,
    qt_transicao   NUMERIC,
    PRIMARY KEY (data, unidade, codprod)
);
-- a tela lê por período; o índice cobre o filtro que ela sempre aplica
CREATE INDEX IF NOT EXISTS ix_estoque_foto_item_data ON estoque_foto_item (data, unidade);
CREATE INDEX IF NOT EXISTS ix_estoque_foto_item_forn ON estoque_foto_item (unidade, codfornec, data);
-- Diário de bordo da fotografia. Existe para a tela distinguir "dia sem movimento" de "dia em
-- que o robô não rodou": sem isto o gráfico ligaria os pontos por cima do buraco e inventaria
-- uma linha reta onde não houve medição. Guarda também o refresh do BI que originou a foto.
CREATE TABLE IF NOT EXISTS estoque_foto_log (
    data        DATE NOT NULL,
    unidade     TEXT NOT NULL,
    n_itens     INTEGER,
    bi_refresh  TEXT,
    params      JSONB,
    criado_em   TIMESTAMP DEFAULT now(),
    PRIMARY KEY (data, unidade)
);
-- Rollup diário: o resultado de `historico.agregar` para os parâmetros PADRÃO, gravado junto da
-- foto. NÃO é uma segunda regra — é cache da MESMA função Python, e pode ser reconstruído a
-- qualquer momento (`historico.rebuild_rollup`). Existe por medição: agregar a foto crua custava
-- 3,5s para 45 dias (129 mil linhas) e ~28s projetados para 12 meses, piorando todo dia.
-- Leitura sem filtro e com régua padrão usa isto; filtro por comprador/fornecedor ou parâmetro
-- customizado cai no cru, que é onde a recomputação do passado continua acontecendo.
-- Curva ABC e XYZ do item NO DIA da foto (08/2026, dúvida do diretor: "filtra por curva?").
-- Gravadas junto porque histórico não se reconstrói: a coluna custa nada hoje e custaria meses
-- de série depois. Mesma razão pela qual o fornecedor entrou no grão.
-- ⚠️ A curva ABC é o Pareto da venda do PERÍODO. O robô fotografa numa janela MÓVEL de 90 dias
-- (`historico.PERIODO_CURVA`), NÃO no default "mes" — o default é o acumulado do mês, então no
-- dia 1º a curva sairia de um dia de venda e no dia 30 de trinta, e a série ganharia um dente de
-- serra em toda virada de mês. A tela declara a janela: quem comparar com a ABC de 12 meses da
-- aba Produtos tem de saber que são réguas diferentes, não uma delas errada.
ALTER TABLE estoque_foto_item ADD COLUMN IF NOT EXISTS curva_abc CHAR(1);
ALTER TABLE estoque_foto_item ADD COLUMN IF NOT EXISTS xyz CHAR(1);
CREATE INDEX IF NOT EXISTS ix_estoque_foto_item_curva ON estoque_foto_item (unidade, curva_abc, data);
CREATE TABLE IF NOT EXISTS estoque_foto_dia (
    data     DATE NOT NULL,
    unidade  TEXT NOT NULL,
    payload  JSONB NOT NULL,
    PRIMARY KEY (data, unidade)
);
-- ───────────────── estado do dia que NÃO sai do grão do item ─────────────────
-- Métricas escalares por dia×unidade que não são decomponíveis por produto: ocupação do WMS
-- (grão = POSIÇÃO), qualidade de cadastro (grão = base inteira), posição de pedidos em aberto.
--
-- ⚠️ **Por que não no `estoque_foto_dia`**, que é a tabela ao lado e tem exatamente esta forma:
-- aquela é o ROLLUP, ou seja, CACHE de `historico.agregar` — ela pode ser jogada fora e
-- reconstruída do cru a qualquer momento (`rebuild_rollup`), e o `_rollup_atual` a descarta
-- sozinho quando a versão muda. Isto aqui é dado PRIMÁRIO e IRRECUPERÁVEL: a ocupação de ontem
-- não existe em lugar nenhum do Winthor. Misturar os dois faria um `rebuild_rollup` de rotina
-- APAGAR histórico que não se refaz — e sem erro nenhum, que é o pior modo de falha desta aba.
--
-- ⚠️ `payload` é JSONB de propósito, e não colunas: a lista de métricas de estado vai crescer
-- (qualidade de cadastro, avaria, pedidos em aberto já estão mapeados). Com colunas, cada
-- métrica nova seria uma migration; com payload, é só passar a gravar a chave — e o passado
-- simplesmente não a tem, que é a verdade (ninguém mediu antes).
CREATE TABLE IF NOT EXISTS estoque_foto_estado (
    data     DATE NOT NULL,
    unidade  TEXT NOT NULL,
    payload  JSONB NOT NULL,
    PRIMARY KEY (data, unidade)
);
-- ───────────────────── pesquisa de preço (captura em campo) ─────────────────────
-- Pedido do diretor 08/2026: preencher o preço pesquisado direto no item, durante a visita.
-- É a 1ª vez que a ferramenta vira FONTE de um dado que o Winthor não tem.
--
-- ⚠️ SEM chave única de propósito: duas cotações do mesmo item no mesmo dia, de origens
-- diferentes, são dois FATOS. Isto é histórico, não estado — o `plano_upsert` usa UPSERT porque
-- lá o grão é o item; aqui o grão é a medição.
--
-- As três colunas que decidem se o dado presta (e que não se recalculam depois):
--   `unidade`     — o preço visto é da UNIDADE ou da EMBALAGEM. O módulo já teve pedido saindo
--                   ~50x errado por converter quantidade sem converter preço (core.item_master).
--   `com_imposto` — nasceu para reconciliar mercadoria x NF quando a comparação era contra o
--                   `CUSTOFIN`. A premissa era errada: a referência é o nosso PREÇO DE VENDA e
--                   os dois lados são cheios, então a coluna deixou de entrar na conta em
--                   08/2026 (ver `core.normaliza_pesquisa`). Segue gravada — histórico não se
--                   regenera, e as linhas antigas precisam dizer sob que régua entraram.
--   `qtunitcx`    — o fator NO DIA da pesquisa. O cadastro muda; o passado não pode se
--                   reinterpretar (mesma razão do `codcomprador` na foto de estoque).
CREATE TABLE IF NOT EXISTS estoque_pesquisa_preco (
    id            SERIAL PRIMARY KEY,
    data_pesquisa DATE    NOT NULL,
    codprod       INTEGER NOT NULL,
    tipo          TEXT    NOT NULL DEFAULT 'fornecedor',   -- fornecedor | concorrente
    origem        TEXT,                                     -- nome do fornecedor ou da loja
    preco         NUMERIC NOT NULL,
    unidade       TEXT    NOT NULL DEFAULT 'un',            -- un | cx
    com_imposto   BOOLEAN NOT NULL DEFAULT false,
    qtunitcx      NUMERIC,
    obs           TEXT,
    usuario_id    INTEGER,
    criado_em     TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_pesq_preco_prod
    ON estoque_pesquisa_preco (codprod, data_pesquisa DESC);
"""

_disponivel = None  # cache do teste de conexão (True/False)


def init():
    """Cria as tabelas (idempotente). Marca se o Postgres está acessível."""
    global _disponivel
    try:
        conn = get_db()
        with conn, conn.cursor() as cur:
            cur.execute(DDL)
        conn.close()
        _disponivel = True
    except Exception as e:
        _disponivel = False
        print(f"[store] Postgres indisponível ({e}). Orçamento/planos ficam desabilitados.")
    return _disponivel


def disponivel():
    return bool(_disponivel)


def ensure():
    """Tenta (re)conectar se ainda não está disponível — robusto a Postgres que sobe depois."""
    return True if _disponivel else init()


def _rows(cur):
    return [dict(r) for r in cur.fetchall()]


def _iso_dates(row):
    """Serializa colunas DATE (data_pedido/dt_vencimento) em ISO 'YYYY-MM-DD'. Sem isto o
    jsonify do Flask emite o objeto date como RFC 1123 ('Tue, 21 Jul 2026 00:00:00 GMT') e o
    dt() do front (que espera ISO) mostra a string crua com horário/GMT."""
    if row:
        for k in ("data_pedido", "dt_vencimento", "data_pesquisa", "criado_em"):
            v = row.get(k)
            if hasattr(v, "isoformat"):
                row[k] = v.isoformat()
    return row


# ───────────────────────── orçamento + pedidos ─────────────────────────
def orcamento_resumo(mes, comprador="TODOS"):
    """Meta do mês + total comprado (soma de pedidos) + saldo + % consumido."""
    conn = get_db()
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT meta_valor FROM estoque_orcamento WHERE mes=%s AND comprador=%s",
                    (mes, comprador))
        row = cur.fetchone()
        meta = float(row["meta_valor"]) if row else 0.0
        if comprador and comprador != "TODOS":
            cur.execute("SELECT COALESCE(SUM(valor),0) s, COUNT(*) n FROM estoque_pedidos WHERE mes=%s AND comprador=%s",
                        (mes, comprador))
        else:
            cur.execute("SELECT COALESCE(SUM(valor),0) s, COUNT(*) n FROM estoque_pedidos WHERE mes=%s", (mes,))
        agg = cur.fetchone()
    conn.close()
    comprado = float(agg["s"]); n = int(agg["n"])
    saldo = meta - comprado
    pct = (comprado / meta) if meta > 0 else None
    return {"mes": mes, "comprador": comprador, "meta": meta, "comprado": comprado,
            "saldo": saldo, "pct": pct, "n_pedidos": n}


def meta_get(mes, comprador="TODOS"):
    """Meta manual lançada (override do 65% automático), ou None se não houver."""
    try:
        conn = get_db()
        with conn, conn.cursor() as cur:
            cur.execute("SELECT meta_valor FROM estoque_orcamento WHERE mes=%s AND comprador=%s",
                        (mes, comprador or "TODOS"))
            row = cur.fetchone()
        conn.close()
        return float(row[0]) if row and row[0] else None
    except Exception:
        return None


def pedidos_pendentes(mes, comprador=None):
    """Pedidos manuais (gerados na nossa plataforma) ainda NÃO sincronizados com o Winthor.
    Ficam à parte do realizado p/ não duplicar quando voltarem da base oficial."""
    try:
        conn = get_db()
        with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            base = ("SELECT * FROM estoque_pedidos WHERE mes=%s "
                    "AND COALESCE(sincronizado_winthor,false)=false")
            if comprador and comprador != "TODOS":
                cur.execute(base + " AND comprador=%s ORDER BY id DESC", (mes, comprador))
            else:
                cur.execute(base + " ORDER BY id DESC", (mes,))
            out = [_iso_dates(r) for r in _rows(cur)]
        conn.close()
        return out
    except Exception:
        return []


def orcamento_set(mes, comprador, meta_valor):
    conn = get_db()
    with conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO estoque_orcamento (mes, comprador, meta_valor)
                       VALUES (%s,%s,%s)
                       ON CONFLICT (mes, comprador) DO UPDATE SET meta_valor=EXCLUDED.meta_valor""",
                    (mes, comprador or "TODOS", meta_valor))
    conn.close()


def pedidos_list(mes, comprador=None):
    conn = get_db()
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        if comprador and comprador != "TODOS":
            cur.execute("SELECT * FROM estoque_pedidos WHERE mes=%s AND comprador=%s ORDER BY data_pedido DESC, id DESC",
                        (mes, comprador))
        else:
            cur.execute("SELECT * FROM estoque_pedidos WHERE mes=%s ORDER BY data_pedido DESC, id DESC", (mes,))
        out = [_iso_dates(r) for r in _rows(cur)]
    conn.close()
    return out


def pedido_get(pid):
    conn = get_db()
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM estoque_pedidos WHERE id=%s", (pid,))
        row = cur.fetchone()
    conn.close()
    return _iso_dates(dict(row)) if row else None


def pedido_itens(pid):
    """Itens (snapshot) de um pedido, em ordem de inclusão."""
    conn = get_db()
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM estoque_pedido_itens WHERE pedido_id=%s ORDER BY id", (pid,))
        out = _rows(cur)
    conn.close()
    return out


def pedido_add(d):
    conn = get_db()
    with conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO estoque_pedidos
            (data_pedido, mes, comprador, codfornec, fornecedor, n_pedido, valor, valor_nf, prazo_dias, dt_vencimento, status, forma_pgto, obs)
            VALUES (%(data_pedido)s,%(mes)s,%(comprador)s,%(codfornec)s,%(fornecedor)s,%(n_pedido)s,%(valor)s,%(valor_nf)s,%(prazo_dias)s,%(dt_vencimento)s,%(status)s,%(forma_pgto)s,%(obs)s)
            RETURNING id""", _ped_defaults(d))
        new_id = cur.fetchone()[0]
        for it in (d.get("itens") or []):
            cur.execute("""INSERT INTO estoque_pedido_itens
                (pedido_id, codprod, descricao, qtdisp, cobertura, giro_mes, qtunitcx, qtd, custo_unit, valor, perc_ipi, perc_st)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (new_id, it.get("codprod"), it.get("descricao"), it.get("qtdisp"), it.get("cobertura"),
                 it.get("giro_mes"), it.get("qtunitcx"), it.get("qtd"), it.get("custo_unit"), it.get("valor"),
                 it.get("perc_ipi"), it.get("perc_st")))
    conn.close()
    return new_id


def pedido_update(pid, d):
    campos = [k for k in ("data_pedido", "comprador", "codfornec", "fornecedor", "n_pedido",
                          "valor", "prazo_dias", "dt_vencimento", "status", "forma_pgto", "obs") if k in d]
    if not campos:
        return
    sets = ", ".join(f"{c}=%s" for c in campos)
    conn = get_db()
    with conn, conn.cursor() as cur:
        cur.execute(f"UPDATE estoque_pedidos SET {sets} WHERE id=%s", [d[c] for c in campos] + [pid])
    conn.close()


def pedido_delete(pid):
    conn = get_db()
    with conn, conn.cursor() as cur:
        cur.execute("DELETE FROM estoque_pedidos WHERE id=%s", (pid,))
    conn.close()


def _ped_defaults(d):
    return {
        "data_pedido": d.get("data_pedido"), "mes": d.get("mes"), "comprador": d.get("comprador"),
        "codfornec": d.get("codfornec"), "fornecedor": d.get("fornecedor"), "n_pedido": d.get("n_pedido"),
        "valor": d.get("valor") or 0, "valor_nf": d.get("valor_nf") or d.get("valor") or 0,
        "prazo_dias": d.get("prazo_dias"),
        "dt_vencimento": d.get("dt_vencimento"), "status": d.get("status") or "ABERTO",
        "forma_pgto": d.get("forma_pgto"), "obs": d.get("obs"),
    }


# ───────────────────────── planos de ação ─────────────────────────
def planos_map(tipo=None):
    """Retorna {chave: plano} para merge no payload das views."""
    conn = get_db()
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        if tipo:
            cur.execute("SELECT * FROM estoque_planos_acao WHERE tipo=%s", (tipo,))
        else:
            cur.execute("SELECT * FROM estoque_planos_acao")
        out = {r["chave"]: dict(r) for r in cur.fetchall()}
    conn.close()
    return out


def plano_upsert(d):
    conn = get_db()
    with conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO estoque_planos_acao
            (chave, tipo, codprod, dtvalidade, descricao, responsavel, acao, prazo, status, obs, atualizado_em)
            VALUES (%(chave)s,%(tipo)s,%(codprod)s,%(dtvalidade)s,%(descricao)s,%(responsavel)s,%(acao)s,%(prazo)s,%(status)s,%(obs)s, now())
            ON CONFLICT (chave) DO UPDATE SET
              responsavel=EXCLUDED.responsavel, acao=EXCLUDED.acao, prazo=EXCLUDED.prazo,
              status=EXCLUDED.status, obs=EXCLUDED.obs, atualizado_em=now()""",
            {"chave": d["chave"], "tipo": d.get("tipo", "geral"), "codprod": d.get("codprod"),
             "dtvalidade": d.get("dtvalidade"), "descricao": d.get("descricao"),
             "responsavel": d.get("responsavel"), "acao": d.get("acao"), "prazo": d.get("prazo"),
             "status": d.get("status") or "PENDENTE", "obs": d.get("obs")})
    conn.close()


def plano_delete(chave):
    conn = get_db()
    with conn, conn.cursor() as cur:
        cur.execute("DELETE FROM estoque_planos_acao WHERE chave=%s", (chave,))
    conn.close()


# ───────────────────────── pesquisa de preço ─────────────────────────
def pesquisa_add(d):
    """Grava uma medição de preço. Retorna o id.

    Sem UPSERT: cada linha é um fato datado (ver DDL). Regravar por cima apagaria a cotação
    anterior do mesmo item, que é justamente o histórico que dá valor ao dado."""
    conn = get_db()
    with conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO estoque_pesquisa_preco
            (data_pesquisa, codprod, tipo, origem, preco, unidade, com_imposto, qtunitcx, obs, usuario_id)
            VALUES (%(data_pesquisa)s,%(codprod)s,%(tipo)s,%(origem)s,%(preco)s,%(unidade)s,
                    %(com_imposto)s,%(qtunitcx)s,%(obs)s,%(usuario_id)s)
            RETURNING id""", {
                "data_pesquisa": d.get("data_pesquisa") or datetime.now().date(),
                "codprod": int(d["codprod"]),
                # a tela de campo não pergunta mais (é sempre concorrente); o default cobre
                # tanto ela quanto qualquer item que ainda esteja na fila offline do navegador
                "tipo": (d.get("tipo") or "concorrente"),
                "origem": (d.get("origem") or None),
                "preco": d["preco"],
                "unidade": (d.get("unidade") or "un"),
                "com_imposto": bool(d.get("com_imposto")),
                "qtunitcx": d.get("qtunitcx"),
                "obs": (d.get("obs") or None),
                "usuario_id": d.get("usuario_id"),
            })
        return cur.fetchone()[0]


def pesquisa_ultima(codprods=None):
    """{codprod: última medição} — DISTINCT ON pega a mais recente por produto.

    `codprods=None` traz tudo (usado pelo modal de pedido, que já tem a lista em mãos e
    filtra no cliente). Lista vazia devolve {} sem ir ao banco."""
    if codprods is not None and not codprods:
        return {}
    sql = """SELECT DISTINCT ON (codprod) * FROM estoque_pesquisa_preco
             {onde} ORDER BY codprod, data_pesquisa DESC, id DESC"""
    onde, args = "", ()
    if codprods is not None:
        onde, args = "WHERE codprod = ANY(%s)", ([int(c) for c in codprods],)
    conn = get_db()
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql.format(onde=onde), args)
        out = {r["codprod"]: _iso_dates(dict(r)) for r in cur.fetchall()}
    conn.close()
    return out


def pesquisa_do_produto(codprod, limite=20):
    """Histórico de medições de UM produto, mais recente primeiro (drawer 360°)."""
    conn = get_db()
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""SELECT * FROM estoque_pesquisa_preco WHERE codprod=%s
                       ORDER BY data_pesquisa DESC, id DESC LIMIT %s""", (int(codprod), int(limite)))
        out = [_iso_dates(dict(r)) for r in cur.fetchall()]
    conn.close()
    return out


def pesquisa_lista(dias=90, limite=1000):
    """Medições recentes com o NOME de quem preencheu (join em multpel_users).

    ⚠️ `usuario_id` sozinho é número na tela — inútil. O join é LEFT: medição de usuário já
    removido continua aparecendo (o fato aconteceu), só sem nome.
    O recorte por FORNECEDOR fica no routes: fornecedor não está nesta tabela, vem do cadastro
    do produto — e duplicá-lo aqui seria o mesmo erro do `codcomprador` que a foto de estoque
    grava para não reinterpretar o passado."""
    conn = get_db()
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT p.*, u.nome AS usuario
              FROM estoque_pesquisa_preco p
              LEFT JOIN multpel_users u ON u.id = p.usuario_id
             WHERE p.data_pesquisa >= (CURRENT_DATE - %s::int)
             ORDER BY p.data_pesquisa DESC, p.id DESC
             LIMIT %s""", (int(dias), int(limite)))
        out = [_iso_dates(dict(r)) for r in cur.fetchall()]
    conn.close()
    return out


# ═══════════════ régua OFICIAL da empresa (⚙ Parâmetros) ═══════════════
# Decisão de 08/2026 (Gabriel + diretor): os ⚙ Parâmetros deixam de viver no `localStorage` de
# cada navegador e passam a ter um PADRÃO DA EMPRESA, editável só por quem tem a flag
# `pode_parametrizar`. Todo mundo ABRE nele; quem quiser testar outro cenário simula na sessão,
# e a simulação morre com a aba.
#
# ⚠️ Por que aqui e não reusando `server._config_get`: em produção o container roda
# `python server.py`, então `server` é `__main__` — importá-lo daqui traria uma SEGUNDA cópia do
# módulo, com outro pool de conexão e outro estado. É a mesma armadilha que fez o `ia.py` ler a
# env direto em vez de `from server import MODULOS`, e ela já mordeu duas vezes no `routes.py`.
#
# A tabela `multpel_config` é a MESMA do Comercial (que guarda `cobertura_limiar_pct` e os
# limiares de login) — daí o PREFIXO: sem ele, `cobertura_total` do Compras colidiria com o
# `cobertura_coberto_dias` do Painel gerencial na primeira letra errada.
PARAM_PREFIXO = "estoque_param."

_PARAMS_CACHE = {"em": None, "val": None}
_PARAMS_TTL = 30            # segundos


def params_oficiais(force=False):
    """A régua oficial da empresa: {nome_do_parametro: valor_texto}.

    Devolve `{}` quando nada foi definido — e `{}` é o caso NORMAL, não um erro: significa
    "ninguém mexeu, vale o DEFAULTS do código". Foi assim que a Multpel entrou, porque a conta
    admin nunca teve o ⚙ tocado (os 13 campos batiam com o default, conferido em 28/08/2026).

    Cache curto (30s) porque `_build_produtos` roda a cada requisição e isto é um SELECT de
    poucas linhas — sem ele, cada troca de aba pagaria um round-trip. `force` fura o cache
    logo depois de salvar, para o autor da mudança não ver a régua antiga.
    Falha em silêncio: config indisponível NUNCA pode derrubar o painel; cai no DEFAULTS.
    """
    import time
    agora = time.time()
    if not force and _PARAMS_CACHE["em"] and (agora - _PARAMS_CACHE["em"]) < _PARAMS_TTL:
        return dict(_PARAMS_CACHE["val"])
    out = {}
    try:
        conn = get_db()
        try:
            with conn, conn.cursor() as cur:
                cur.execute("SELECT chave, valor FROM multpel_config WHERE chave LIKE %s",
                            (PARAM_PREFIXO + "%",))
                out = {c[len(PARAM_PREFIXO):]: v for c, v in cur.fetchall() if v is not None}
        finally:
            conn.close()
    except Exception:                                    # noqa: BLE001
        return dict(_PARAMS_CACHE["val"] or {})
    _PARAMS_CACHE.update(em=agora, val=dict(out))
    return dict(out)


def params_oficiais_set(valores, usuario_id=None):
    """Grava a régua oficial. Retorna o dict gravado.

    ⚠️ Grava também uma linha em `multpel_log` — a régua oficial precisa de HISTÓRICO. Foi a
    falta dele que deixou a cobertura alvo andar 45 -> 40 -> 30 em cinco semanas, no navegador
    do diretor, sem registro em lugar nenhum (medido em 27/08/2026). `atualizado_em` diz QUANDO;
    o log diz QUEM e o QUÊ.
    """
    import json
    valores = {str(k): str(v) for k, v in (valores or {}).items() if v is not None}
    conn = get_db()
    try:
        with conn, conn.cursor() as cur:
            for k, v in valores.items():
                cur.execute(
                    "INSERT INTO multpel_config (chave, valor, atualizado_em) VALUES (%s,%s,NOW()) "
                    "ON CONFLICT (chave) DO UPDATE SET valor=EXCLUDED.valor, atualizado_em=NOW()",
                    (PARAM_PREFIXO + k, v))
            cur.execute(
                "INSERT INTO multpel_log (usuario_id, rota, parametros) VALUES (%s,%s,%s)",
                (usuario_id, "estoque:params_oficiais",
                 json.dumps(valores, ensure_ascii=False)[:12000]))
    finally:
        conn.close()
    _PARAMS_CACHE.update(em=None, val=None)      # próxima leitura busca do banco
    return valores
