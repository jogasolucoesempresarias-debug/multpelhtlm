"""
Provider SQL — modo DATA_SOURCE=postgres (produtização).

Lê os dados analíticos (formato Winthor) de um Postgres — a demo aponta pro `joga_demo`;
um cliente "só banco" apontaria pro Winthor dele. Devolve as MESMAS formas de dado que o
caminho DAX, pra reusar rfm.py/cohort.py/estoque.core sem alteração.

- Conexão SEPARADA do banco de auth do app (ANALYTICS_DB_*, com fallback pro DB_* padrão).
- `periodo_sql` espelha filtro_periodo (server.py); `escopo_where` espelha aplicar_rbac_dax.
- Medidas em coluna crua (as mesmas fórmulas validadas em medidas_dax.py, agora em SQL).
"""
import os
import calendar
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

try:
    import psycopg2 as _pg
except ImportError:
    import psycopg as _pg

# defensivo: no app o server.py já carregou; standalone (gate/testes) precisa daqui.
load_dotenv(Path(__file__).resolve().parent / ".env")


# ───────────────────────── conexão analítica ─────────────────────────
def analytics_conn():
    return _pg.connect(
        host=os.getenv("ANALYTICS_DB_HOST", os.getenv("DB_HOST", "localhost")),
        port=os.getenv("ANALYTICS_DB_PORT", os.getenv("DB_PORT", "5432")),
        dbname=os.getenv("ANALYTICS_DB_NAME", "joga_demo"),
        user=os.getenv("ANALYTICS_DB_USER", os.getenv("DB_USER", "postgres")),
        password=os.getenv("ANALYTICS_DB_PASSWORD", os.getenv("DB_PASSWORD", "")),
    )


# ───────────────────────── medidas em SQL (coluna crua) ─────────────────────────
VB = "coalesce(sum(vlvenda-icmsretido-vlfecp) FILTER (WHERE codoper='S'), 0)"          # VENDA BRUTA
CT = "coalesce(sum(vlcustofin+vlcustofinbonif) FILTER (WHERE codoper IN ('S','SB')), 0)"  # CUSTO TOTAL
# LUCRO no grão agrupado (por depto/produto/cliente): venda − custo, só 'S'. Mesma simplificação de
# categorias_dados/ranking_vendedores_dados (no grão agregado ignora-se a devolução).
LUCRO = "coalesce(sum(vlvenda-icmsretido-vlfecp-vlcustofin) FILTER (WHERE codoper='S'), 0)"
_AM_FAT = "extract(year FROM dtsaida)::int*100 + extract(month FROM dtsaida)::int"   # AnoMes YYYYMM
_AM_DEV = "extract(year FROM dtent)::int*100 + extract(month FROM dtent)::int"
_DEV_EXCL = "NOT (codativ=37 AND coddevol<>9)"   # exclui transferência interna de filial
DEV = f"coalesce(sum(vldevolucao) FILTER (WHERE {_DEV_EXCL}), 0)"
CDEV = f"coalesce(sum(vlcustofin) FILTER (WHERE {_DEV_EXCL}), 0)"


# ───────────────────────── "hoje" ancorado no dado (não em date.today()) ─────────────────────────
_HOJE_CACHE = None


def hoje_analitico():
    """'Hoje' do modo BD = max(dtsaida) do joga_demo (override por env ANALYTICS_HOJE=YYYY-MM-DD).
    Ancorar o "hoje" no dado (em vez de date.today()) mantém a demo viva com o passar do tempo sem
    regenerar o fato: janelas relativas (12m, recente/anterior, mês corrente) seguem o último dia com
    venda. Só o modo postgres usa isto; o caminho DAX/Power BI usa TODAY() (correto no cliente real)."""
    global _HOJE_CACHE
    if _HOJE_CACHE is not None:
        return _HOJE_CACHE
    env = os.getenv("ANALYTICS_HOJE")
    if env:
        _HOJE_CACHE = date.fromisoformat(env)
        return _HOJE_CACHE
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute("SELECT max(dtsaida) FROM faturamento_vendas")
        r = cur.fetchone()[0]
    _HOJE_CACHE = r or date.today()
    return _HOJE_CACHE


# ───────────────────────── período (espelha filtro_periodo) ─────────────────────────
def _mes(y, m):
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def periodo_sql(tipo):
    """Token de período → (d0, d1) inclusivo. Espelha server.py:filtro_periodo.
    'hoje' ancorado no dado (hoje_analitico) — ver docstring de hoje_analitico."""
    hoje = hoje_analitico()
    if isinstance(tipo, str) and tipo.startswith("range:"):
        _, i, f = tipo.split(":")
        return date.fromisoformat(i), date.fromisoformat(f)
    if tipo == "mes_atual":
        return date(hoje.year, hoje.month, 1), hoje
    if tipo == "mes_anterior":
        y, m = (hoje.year, hoje.month - 1) if hoje.month > 1 else (hoje.year - 1, 12)
        return _mes(y, m)
    if tipo == "ytd":
        return date(hoje.year, 1, 1), hoje
    if tipo == "ano_anterior":
        return date(hoje.year - 1, 1, 1), date(hoje.year - 1, 12, 31)
    if tipo == "12m":
        return hoje - timedelta(days=365), hoje
    if tipo == "12m_anterior":
        return hoje - timedelta(days=730), hoje - timedelta(days=365)
    if tipo == "24m":
        return hoje - timedelta(days=730), hoje
    y, m = tipo.split("-")           # 'YYYY-MM'
    return _mes(int(y), int(m))


# ───────────────────────── RBAC (espelha aplicar_rbac_dax) ─────────────────────────
def escopo_where(rbac, tab="faturamento_vendas"):
    """Fragmento ' AND ...' de RBAC por VENDA. rbac = {role, codusur, supervisores}."""
    if not rbac or rbac.get("role") in ("admin", "viewer"):
        return ""
    cu = rbac.get("codusur")
    if cu is not None:
        return f" AND {tab}.codusur = {int(cu)}"
    sups = rbac.get("supervisores") or []
    if sups:
        return f" AND {tab}.codsupervisor IN ({','.join(str(int(s)) for s in sups)})"
    return " AND 1=0"   # supervisor sem áreas → nada


# ───────────────────────── resultado financeiro (alinhamento RCA) ─────────────────────────
def resultado(cur, d0, d1, rbac):
    """{vb, liquida, lucro, margem} — venda por DTSAIDA, devoluções por DTENT (RCA F.3/F.4)."""
    wv = escopo_where(rbac, "faturamento_vendas")
    cur.execute(f"SELECT {VB}, {CT} FROM faturamento_vendas "
                f"WHERE dtsaida BETWEEN %s AND %s{wv}", (d0, d1))
    vb, ct = (float(x) for x in cur.fetchone())
    wd = escopo_where(rbac, "faturamento_devolucao")
    cur.execute(f"SELECT {DEV}, {CDEV} FROM faturamento_devolucao "
                f"WHERE dtent BETWEEN %s AND %s{wd}", (d0, d1))
    dev, cdev = (float(x) for x in cur.fetchone())
    wa = escopo_where(rbac, "faturamento_devolucao_avulsa")
    cur.execute(f"SELECT coalesce(sum(vldevolucao),0), coalesce(sum(vlcusto),0) "
                f"FROM faturamento_devolucao_avulsa WHERE dtent BETWEEN %s AND %s{wa}", (d0, d1))
    dav, cdav = (float(x) for x in cur.fetchone())
    liquida = vb - dev - dav
    lucro = liquida - (ct - cdev - cdav)
    return {"vb": vb, "liquida": liquida, "lucro": lucro,
            "margem": (lucro / liquida) if liquida else 0}


def _scalar(cur, sql, args):
    cur.execute(sql, args)
    v = cur.fetchone()[0]
    return float(v) if v is not None else 0.0


# ───────────────────────── YoY (4 percentuais) — espelha _yoy_parse do server ─────────────────────────
_MESES_ABREV = ("jan", "fev", "mar", "abr", "mai", "jun",
                "jul", "ago", "set", "out", "nov", "dez")


def _yoy_metricas(cur, d0, d1, rbac):
    """(receita_liquida, lucro, clientes_pos, mix) numa janela — mesmas 4 métricas do _yoy_query."""
    r = resultado(cur, d0, d1, rbac)
    w = escopo_where(rbac, "faturamento_vendas")
    cli = _scalar(cur, "SELECT count(DISTINCT codcli) FILTER (WHERE codoper='S') "
                  f"FROM faturamento_vendas WHERE dtsaida BETWEEN %s AND %s{w}", (d0, d1))
    mix = _scalar(cur, "SELECT coalesce(sum(pd),0) FROM (SELECT count(DISTINCT codprod) pd "
                  f"FROM faturamento_vendas WHERE dtsaida BETWEEN %s AND %s AND codoper='S'{w} "
                  "GROUP BY dtsaida) t", (d0, d1))
    return r["liquida"], r["lucro"], cli, mix


def _yoy4(cur, atual, anterior, rbac):
    """{receita_liquida, lucro_bruto, positivacao_cliente, positivacao_mix} — variação atual vs anterior.
    Formato IDÊNTICO ao _yoy_parse do server (é o que o front lê nos badges e no gráfico YoY)."""
    a = _yoy_metricas(cur, *atual, rbac)
    b = _yoy_metricas(cur, *anterior, rbac)

    def _v(x, y):
        return ((x - y) / y) if y else None

    return {"receita_liquida": _v(a[0], b[0]), "lucro_bruto": _v(a[1], b[1]),
            "positivacao_cliente": _v(a[2], b[2]), "positivacao_mix": _v(a[3], b[3])}


def _janelas_yoy_mes(d0, d1):
    """Janelas MTD: (1º→corte do mês corrente) vs (mesmo intervalo do ano anterior). d0/d1 já são
    (1º do mês, corte=hoje_analitico). Dia do ano anterior clampado ao fim do mês (29/02→28/02)."""
    dia_ant = min(d1.day, calendar.monthrange(d1.year - 1, d1.month)[1])
    return (d0, d1), (date(d0.year - 1, d0.month, 1), date(d1.year - 1, d1.month, dia_ant))


def _yoy_mes_info(atual, anterior):
    """Rótulo/tooltip do YoY mensal (espelha _yoy_mes_meta do server)."""
    (ia, fa), (ip, fp) = atual, anterior

    def _du(ini, fim):
        return sum(1 for n in range((fim - ini).days + 1) if (ini + timedelta(n)).weekday() < 5)

    return {"rotulo": f"vs {_MESES_ABREV[ip.month - 1]}/{ip.year % 100:02d}",
            "periodo": f"{ia.day:02d}–{fa.day:02d}/{_MESES_ABREV[ia.month - 1]}/{ia.year % 100:02d} "
                       f"vs {ip.day:02d}–{fp.day:02d}/{_MESES_ABREV[ip.month - 1]}/{ip.year % 100:02d}",
            "dias_uteis": _du(ia, fa), "dias_uteis_anterior": _du(ip, fp)}


# ───────────────────────── tela: Dashboard KPIs ─────────────────────────
def dashboard_kpis(rbac):
    """Mesma estrutura de /api/dashboard/kpis, lida do Postgres analítico."""
    with analytics_conn() as c:
        cur = c.cursor()
        d0, d1 = periodo_sql("mes_atual")
        wv = escopo_where(rbac, "faturamento_vendas")
        r = resultado(cur, d0, d1, rbac)

        n_ped = _scalar(cur, "SELECT count(DISTINCT numtransvenda) FILTER (WHERE codoper='S') "
                        f"FROM faturamento_vendas WHERE dtsaida BETWEEN %s AND %s{wv}", (d0, d1))
        ticket = r["liquida"] / n_ped if n_ped else 0

        total_mix = _scalar(cur, "SELECT coalesce(sum(pd),0) FROM (SELECT count(DISTINCT codprod) pd "
                            f"FROM faturamento_vendas WHERE dtsaida BETWEEN %s AND %s AND codoper='S'{wv} "
                            "GROUP BY dtsaida) t", (d0, d1))
        cli = _scalar(cur, "SELECT count(DISTINCT codcli) FILTER (WHERE codoper='S') "
                      f"FROM faturamento_vendas WHERE dtsaida BETWEEN %s AND %s{wv}", (d0, d1))
        peso = _scalar(cur, "SELECT coalesce(sum(fv.qt*pr.pesobruto),0) FROM faturamento_vendas fv "
                       "JOIN pcprodut pr ON pr.codprod=fv.codprod "
                       f"WHERE fv.dtsaida BETWEEN %s AND %s AND fv.codoper='S'"
                       f"{escopo_where(rbac, 'fv')}", (d0, d1))
        vmp = r["liquida"] / peso if peso else 0

        # YoY 12m (gráfico) e YoY mensal MTD (badges dos cards) — 4 percentuais cada, no shape
        # que o front lê (_yoy_parse). yoy_mes compara o mês corrente até o corte vs o mesmo
        # intervalo do ano anterior; se o corte não está no mês corrente, yoy_mes = None.
        yoy = _yoy4(cur, periodo_sql("12m"), periodo_sql("12m_anterior"), rbac)
        hoje = hoje_analitico()
        if (d0.year, d0.month) == (hoje.year, hoje.month):
            at, an = _janelas_yoy_mes(d0, d1)
            yoy_mes = _yoy4(cur, at, an, rbac)
            yoy_mes_info = _yoy_mes_info(at, an)
        else:
            yoy_mes, yoy_mes_info = None, None

    return {
        "ok": True,
        "primarios": {"venda_liquida": r["liquida"], "lucro_total": r["lucro"],
                      "margem": r["margem"], "ticket_medio": ticket},
        "secundarios": {"total_mix": total_mix, "clientes_novos": cli,
                        "valor_medio_peso": vmp, "clientes_positivados": cli},
        "yoy": yoy,
        "yoy_mes": yoy_mes,
        "yoy_mes_info": yoy_mes_info,
    }


# ───────────────────────── Carteira RFM (alimenta o rfm.py do app) ─────────────────────────
def top_produtos_cliente(codcli, limit=10):
    """Top N produtos (12m) de 1 cliente: [{codprod, descricao, qt_12m, venda_12m}]. Espelha _top_produtos_cliente."""
    d12 = hoje_analitico() - timedelta(days=365)
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"""SELECT codprod, max(descricao), coalesce(sum(qt) FILTER (WHERE codoper='S'),0), {VB}
            FROM faturamento_vendas WHERE codcli=%s AND dtsaida>=%s GROUP BY codprod
            ORDER BY {VB} DESC LIMIT %s""", (int(codcli), d12, int(limit)))
        return [{"codprod": cp, "descricao": desc or f"Produto {cp}", "qt_12m": float(qt or 0), "venda_12m": float(v)}
                for cp, desc, qt, v in cur.fetchall() if cp is not None]


# ───────────────────────── Fase 4b: endpoints comerciais que faltaram na Fase 2 ─────────────────────────
def fornecedores_map():
    """{codfornec_str: nome} — FORNECPRINC textual da faturamento_vendas (distinct). Espelha
    _carregar_fornecedores_map (usado no rodapé do PDF do Mix, etc.)."""
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute("SELECT DISTINCT codfornecprinc, fornecprinc FROM faturamento_vendas "
                    "WHERE codfornecprinc IS NOT NULL")
        return {str(cf): (fn or f"Fornec {cf}") for cf, fn in cur.fetchall()}


def carteira_evolucao(meses, rbac=None):
    """[{AnoMes, ClientesUnicos, Compras}] dos últimos `meses` (distinct codcli / distinct numnota)."""
    d0 = _primeiro_dia_meses_atras(int(meses))
    w = escopo_where(rbac, "faturamento_vendas")
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"SELECT {_AM_FAT} am, count(DISTINCT codcli), count(DISTINCT numnota) "
                    f"FROM faturamento_vendas WHERE dtsaida >= %s{w} GROUP BY am ORDER BY am", (d0,))
        return [{"AnoMes": am, "ClientesUnicos": int(cl or 0), "Compras": int(cp or 0)}
                for am, cl, cp in cur.fetchall()]


def vendedor_serie(codusur, d0, d1, rbac=None):
    """[{AnoMes, VendaLiquida, LucroTotal}] mensal de 1 vendedor no período."""
    w = escopo_where(rbac, "faturamento_vendas")
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"SELECT {_AM_FAT} am, {VB} v, {LUCRO} lu FROM faturamento_vendas "
                    f"WHERE codusur=%s AND dtsaida BETWEEN %s AND %s{w} GROUP BY am ORDER BY am",
                    (int(codusur), d0, d1))
        return [{"AnoMes": am, "VendaLiquida": float(v), "LucroTotal": float(lu)} for am, v, lu in cur.fetchall()]


def perfil_vendedor_row(codusur):
    """1 linha de pcusuari (chaves estilo clean_rows). None se não existir. A demo não tem CPF/email/
    comissão/admissão → o endpoint preenche None nesses campos."""
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute("SELECT codusur, nome, codsupervisor, tipovend, cidade, estado, bloqueio "
                    "FROM pcusuari WHERE codusur=%s", (int(codusur),))
        r = cur.fetchone()
    if not r:
        return None
    return {"CODUSUR": r[0], "NOME": r[1], "CODSUPERVISOR": r[2], "TIPOVEND": r[3],
            "CIDADE": r[4], "ESTADO": r[5], "BLOQUEIO": r[6]}


def categoria_clientes(codepto, limit, rbac=None):
    """Top clientes de 1 depto (12m): [{CODCLI, CLIENTE, UF, CODUSUR, VendaCat, LucroCat}]. codepto 'null'=sem depto."""
    d12 = hoje_analitico() - timedelta(days=365)
    w = escopo_where(rbac, "faturamento_vendas")
    dim = "codepto IS NULL" if str(codepto) == "null" else f"codepto = {int(codepto)}"
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"SELECT codcli, max(cliente), max(uf), max(codusur), {VB} v, {LUCRO} lu "
                    f"FROM faturamento_vendas WHERE {dim} AND dtsaida>=%s{w} "
                    f"GROUP BY codcli ORDER BY v DESC LIMIT %s", (d12, int(limit)))
        return [{"CODCLI": cc, "CLIENTE": cli, "UF": uf, "CODUSUR": cu,
                 "VendaCat": float(v), "LucroCat": float(lu)} for cc, cli, uf, cu, v, lu in cur.fetchall()]


def marcas_top(top, rbac=None):
    """[{CODMARCA, VendaLiquida, LucroTotal}] top marcas 12m."""
    d12 = hoje_analitico() - timedelta(days=365)
    w = escopo_where(rbac, "faturamento_vendas")
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"SELECT codmarca, {VB} v, {LUCRO} lu FROM faturamento_vendas "
                    f"WHERE dtsaida>=%s{w} GROUP BY codmarca ORDER BY v DESC LIMIT %s", (d12, int(top)))
        return [{"CODMARCA": cm, "VendaLiquida": float(v), "LucroTotal": float(lu)} for cm, v, lu in cur.fetchall()]


def fornecedores_top(top, rbac=None):
    """[{CODFORNECPRINC, FORNECPRINC, VendaLiquida, LucroTotal}] top fornecedores 12m."""
    d12 = hoje_analitico() - timedelta(days=365)
    w = escopo_where(rbac, "faturamento_vendas")
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"SELECT codfornecprinc, max(fornecprinc), {VB} v, {LUCRO} lu FROM faturamento_vendas "
                    f"WHERE dtsaida>=%s{w} GROUP BY codfornecprinc ORDER BY v DESC LIMIT %s", (d12, int(top)))
        return [{"CODFORNECPRINC": cf, "FORNECPRINC": fn, "VendaLiquida": float(v), "LucroTotal": float(lu)}
                for cf, fn, v, lu in cur.fetchall()]


def radar_produto_cliente_serie(codprod, codcli):
    """[{AnoMes, Venda, Qt}] série 12m de 1 produto × 1 cliente."""
    d12 = hoje_analitico() - timedelta(days=365)
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"SELECT {_AM_FAT} am, {VB} v, coalesce(sum(qt) FILTER (WHERE codoper='S'),0) "
                    f"FROM faturamento_vendas WHERE codprod=%s AND codcli=%s AND dtsaida>=%s GROUP BY am",
                    (int(codprod), int(codcli), d12))
        return [{"AnoMes": am, "Venda": float(v), "Qt": float(q)} for am, v, q in cur.fetchall()]


def carteira_mes(anomes, codclis=None, rbac=None):
    """Peças do drill de 1 mês (VB-based, calibração de demo). Devolve dict com resumo + tops (raw) +
    comparativos, pro server montar a resposta idêntica ao caminho DAX."""
    ano, mes = anomes // 100, anomes % 100
    w = escopo_where(rbac, "faturamento_vendas") + _in_frag(codclis, "codcli")

    def _mes_ant(a, m):
        return (a - 1, 12) if m == 1 else (a, m - 1)

    ya_a, ya_m = ano - 1, mes
    ma_a, ma_m = _mes_ant(ano, mes)
    wy = "extract(year FROM dtsaida)=%s AND extract(month FROM dtsaida)=%s"
    with analytics_conn() as c:
        cur = c.cursor()

        def _vl_cli(a, m):
            cur.execute(f"SELECT {VB}, count(DISTINCT codcli) FROM faturamento_vendas WHERE {wy}{w}", (a, m))
            return cur.fetchone()

        v_at, cli_at = _vl_cli(ano, mes)
        v_ma, cli_ma = _vl_cli(ma_a, ma_m)
        v_ya, cli_ya = _vl_cli(ya_a, ya_m)
        cur.execute(f"SELECT {LUCRO}, count(DISTINCT numtransvenda) FILTER (WHERE codoper='S'), "
                    f"count(DISTINCT codusur) FROM faturamento_vendas WHERE {wy}{w}", (ano, mes))
        lucro, nped, vends = cur.fetchone()
        venda = float(v_at or 0)
        resumo = {"venda_total": round(venda, 2), "lucro_total": round(float(lucro or 0), 2),
                  "clientes_unicos": int(cli_at or 0),
                  "ticket_medio": round(venda / nped, 2) if nped else 0,
                  "vendedores_ativos": int(vends or 0)}
        cur.execute(f"SELECT codcli, max(cliente), max(uf), max(codusur), {VB} v, {LUCRO} lu "
                    f"FROM faturamento_vendas WHERE {wy}{w} GROUP BY codcli ORDER BY v DESC LIMIT 10", (ano, mes))
        top_clientes = [{"CODCLI": cc, "CLIENTE": cl, "UF": uf, "CODUSUR": cu, "Venda": float(v), "Lucro": float(l)}
                        for cc, cl, uf, cu, v, l in cur.fetchall()]
        cur.execute(f"SELECT codepto, {VB} v FROM faturamento_vendas WHERE {wy}{w} "
                    f"GROUP BY codepto ORDER BY v DESC LIMIT 5", (ano, mes))
        top_deptos = [{"CODEPTO": cd, "Venda": float(v)} for cd, v in cur.fetchall()]
        cur.execute(f"SELECT codprod, max(descricao), {VB} v, coalesce(sum(qt) FILTER (WHERE codoper='S'),0) "
                    f"FROM faturamento_vendas WHERE {wy}{w} GROUP BY codprod ORDER BY v DESC LIMIT 20", (ano, mes))
        top_produtos = [{"CODPROD": cp, "DESCRICAO": ds, "Venda": float(v), "QtVenda": float(q)}
                        for cp, ds, v, q in cur.fetchall()]
    return {"resumo": resumo, "top_clientes": top_clientes, "top_deptos": top_deptos,
            "top_produtos": top_produtos, "venda_ma": round(float(v_ma or 0), 2),
            "venda_ya": round(float(v_ya or 0), 2), "cli_ma": int(cli_ma or 0), "cli_ya": int(cli_ya or 0)}


def _primeiro_dia_meses_atras(n):
    """1º dia do mês n meses atrás (âncora hoje_analitico). Espelha EOMONTH(TODAY(),-n)+1 do DAX."""
    h = hoje_analitico()
    idx = h.year * 12 + (h.month - 1) - n
    return date(idx // 12, idx % 12 + 1, 1)


def venda_mensal_por_cliente():
    """{codcli: {anomes_int: venda_bruta}} 24m — GLOBAL. Espelha _carregar_venda_mensal_por_cliente
    (VENDA BRUTA por DTSAIDA; o recorte por usuário é feito nos endpoints via codclis de cadastro)."""
    d0 = _primeiro_dia_meses_atras(24)
    out = {}
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"SELECT codcli, {_AM_FAT} am, {VB} FROM faturamento_vendas "
                    f"WHERE dtsaida >= %s GROUP BY codcli, am HAVING {VB} > 0", (d0,))
        for cc, am, v in cur.fetchall():
            if cc is not None:
                out.setdefault(int(cc), {})[int(am)] = float(v)
    return out


def devolucao_mensal_por_cliente():
    """{codcli: {anomes_int: devolucao}} 24m (DEV normal + avulsa, por DTENT) — GLOBAL.
    Espelha _carregar_devolucao_mensal_por_cliente."""
    d0 = _primeiro_dia_meses_atras(24)
    out = {}
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"SELECT codcli, {_AM_DEV} am, {DEV} FROM faturamento_devolucao "
                    f"WHERE dtent >= %s GROUP BY codcli, am HAVING {DEV} > 0", (d0,))
        for cc, am, v in cur.fetchall():
            if cc is not None:
                out.setdefault(int(cc), {})[int(am)] = float(v)
        cur.execute(f"SELECT codcli, {_AM_DEV} am, coalesce(sum(vldevolucao),0) "
                    f"FROM faturamento_devolucao_avulsa WHERE dtent >= %s GROUP BY codcli, am", (d0,))
        for cc, am, v in cur.fetchall():
            if cc is not None and float(v or 0) > 0:
                d = out.setdefault(int(cc), {})
                d[int(am)] = d.get(int(am), 0.0) + float(v)
    return out


def carteira_cliente_detalhe(codcli):
    """Drill 360° de 1 cliente (12m): (historico, deptos_rows). Espelha api_carteira_cliente.
    historico=[{AnoMes, VendaLiquida, LucroTotal}] mensal; deptos_rows=[{CODEPTO, VendaLiquida, LucroTotal}] top 5.
    Venda líq = bruta − devoluções − avulsa (mês a mês), como o caminho DAX."""
    h = hoje_analitico()
    d12 = h - timedelta(days=365)
    zero = {"b": 0.0, "ct": 0.0, "dv": 0.0, "cdv": 0.0, "dva": 0.0, "cdva": 0.0}
    with analytics_conn() as c:
        cur = c.cursor()
        hd = {}
        cur.execute(f"SELECT {_AM_FAT} am, {VB} b, {CT} ct FROM faturamento_vendas "
                    f"WHERE codcli=%s AND dtsaida>=%s GROUP BY am", (int(codcli), d12))
        for am, b, ct in cur.fetchall():
            hd.setdefault(am, dict(zero)); hd[am]["b"] = float(b); hd[am]["ct"] = float(ct)
        cur.execute(f"SELECT extract(year FROM dtent)::int*100+extract(month FROM dtent)::int am, {DEV} dv, {CDEV} cdv "
                    f"FROM faturamento_devolucao WHERE codcli=%s AND dtent>=%s GROUP BY am", (int(codcli), d12))
        for am, dv, cdv in cur.fetchall():
            if am is not None:
                hd.setdefault(am, dict(zero)); hd[am]["dv"] += float(dv); hd[am]["cdv"] += float(cdv)
        cur.execute("SELECT extract(year FROM dtent)::int*100+extract(month FROM dtent)::int am, "
                    "coalesce(sum(vldevolucao),0) dva, coalesce(sum(vlcusto),0) cdva "
                    "FROM faturamento_devolucao_avulsa WHERE codcli=%s AND dtent>=%s GROUP BY am", (int(codcli), d12))
        for am, dva, cdva in cur.fetchall():
            if am is not None:
                hd.setdefault(am, dict(zero)); hd[am]["dva"] += float(dva); hd[am]["cdva"] += float(cdva)
        historico = []
        for am in sorted(hd):
            d = hd[am]
            vl = round(d["b"] - d["dv"] - d["dva"], 2)
            historico.append({"AnoMes": am, "VendaLiquida": vl,
                              "LucroTotal": round(vl - (d["ct"] - d["cdv"] - d["cdva"]), 2)})
        cur.execute(f"SELECT codepto, {VB} v, {LUCRO} lu FROM faturamento_vendas "
                    f"WHERE codcli=%s AND dtsaida>=%s GROUP BY codepto ORDER BY v DESC LIMIT 5", (int(codcli), d12))
        deptos_rows = [{"CODEPTO": cd, "VendaLiquida": float(v), "LucroTotal": float(lu)}
                       for cd, v, lu in cur.fetchall()]
    return historico, deptos_rows


def carteira_dados():
    """Snapshot + datas + meta da carteira GLOBAL (todos os clientes), no formato que
    rfm.calcular_clientes() consome. Sem RBAC (recorte por cadastro é feito no app)."""
    hoje = hoje_analitico()
    d12 = hoje - timedelta(days=365)
    d24 = hoje - timedelta(days=730)
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute("""
            SELECT codcli,
                max(dtsaida) FILTER (WHERE codoper='S') ultima,
                count(DISTINCT numnota) FILTER (WHERE codoper='S' AND dtsaida>=%s) compras12m,
                coalesce(sum(vlvenda-icmsretido-vlfecp) FILTER (WHERE codoper='S' AND dtsaida>=%s),0) venda12m,
                coalesce(sum(vlvenda-icmsretido-vlfecp-vlcustofin) FILTER (WHERE codoper='S' AND dtsaida>=%s),0) lucro12m
            FROM faturamento_vendas WHERE dtsaida >= %s GROUP BY codcli""",
                    (d12, d12, d12, d24))
        snapshot = []
        for codcli, ultima, compras, venda, lucro in cur.fetchall():
            if ultima is None:
                continue
            snapshot.append({
                "CODCLI": codcli, "UltimaCompra": ultima.isoformat(),
                "DiasSemComprar": (hoje - ultima).days,
                "Compras12m": compras or 0, "Venda12m": float(venda), "Lucro12m": float(lucro),
            })
        cur.execute("""SELECT codcli, dtsaida FROM faturamento_vendas
                       WHERE codoper='S' AND dtsaida >= %s GROUP BY codcli, dtsaida""", (d12,))
        datas = {}
        for codcli, dt in cur.fetchall():
            datas.setdefault(codcli, []).append(dt)
        cur.execute("""SELECT codcli, cliente, fantasia, municent, municcob, estent,
                              telcelent, telent, codusur1, bloqueio FROM pcclient""")
        meta = {r[0]: {"cliente": r[1], "fantasia": r[2], "cidade": r[3] or r[4], "uf": r[5],
                       "telefone": (r[6] or r[7] or "").strip() or None,
                       "codusur1": r[8], "bloqueio": r[9]} for r in cur.fetchall()}
    return snapshot, datas, meta


# ───────────────────────── mapas de dimensão (PCUSUARI / PCSUPERV) ─────────────────────────
def supervisores_map():
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute("SELECT codsupervisor, nome, tiposupervisor FROM pcsuperv")
        return {str(cs): {"nome": nome or f"Time {cs}", "tipo": tipo}
                for cs, nome, tipo in cur.fetchall() if cs is not None}


def vendedores_map(tecnicos=()):
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute("SELECT codusur, nome, codsupervisor, tipovend, cidade, estado, bloqueio "
                    "FROM pcusuari")
        return {str(cu): {"nome": nome or f"RCA {cu}", "codsupervisor": cs, "tipo": tv,
                          "cidade": cid, "estado": est, "bloqueio": bl}
                for cu, nome, cs, tv, cid, est, bl in cur.fetchall()
                if cu is not None and cu not in tecnicos}


# ───────────────────────── Ranking de vendedores ─────────────────────────
def ranking_vendedores_dados(rbac):
    """(atual, anterior_idx, metricas_idx, carteira_idx) por CODUSUR, como o caminho DAX."""
    hoje = hoje_analitico()
    d12, d24 = hoje - timedelta(days=365), hoje - timedelta(days=730)
    w = escopo_where(rbac, "faturamento_vendas")
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"""SELECT codusur, {VB} vl,
                coalesce(sum(vlvenda-icmsretido-vlfecp-vlcustofin) FILTER (WHERE codoper='S'),0) lucro,
                count(DISTINCT codcli) FILTER (WHERE codoper='S') clientes,
                coalesce(round(avg(vlvenda-icmsretido-vlfecp) FILTER (WHERE codoper='S'))::numeric,2) ticket
            FROM faturamento_vendas WHERE dtsaida BETWEEN %s AND %s{w}
            GROUP BY codusur""", (d12, hoje))
        atual = [{"CODUSUR": cu, "VendaLiq": float(vl), "LucroTotal": float(lu),
                  "ClientesUnicos": cl or 0, "TicketMedio": float(tk or 0)}
                 for cu, vl, lu, cl, tk in cur.fetchall() if cu is not None]
        metricas_idx = {r["CODUSUR"]: r for r in atual}
        cur.execute(f"SELECT codusur, {VB} FROM faturamento_vendas "
                    f"WHERE dtsaida BETWEEN %s AND %s{w} GROUP BY codusur", (d24, d12))
        anterior_idx = {cu: {"VendaLiqAnt": float(v)} for cu, v in cur.fetchall() if cu is not None}
        cur.execute("SELECT codusur1, count(DISTINCT codcli) FROM pcclient GROUP BY codusur1")
        carteira_idx = {cu: {"CarteiraOficial": n} for cu, n in cur.fetchall() if cu is not None}
    return atual, anterior_idx, metricas_idx, carteira_idx


# ───────────────────────── Cohort (alimenta o cohort.py do app) ─────────────────────────
def cohort_compras(periodo_meses=12):
    """{codcli: [meses 'YYYY-MM']} das compras 'S' no período (+12m de histórico)."""
    corte = hoje_analitico() - timedelta(days=30 * (periodo_meses + 12))
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute("""SELECT codcli, to_char(date_trunc('month', dtsaida), 'YYYY-MM')
                       FROM faturamento_vendas WHERE codoper='S' AND dtsaida >= %s
                       GROUP BY codcli, 2""", (corte,))
        compras = {}
        for cc, am in cur.fetchall():
            if cc is not None:
                compras.setdefault(cc, []).append(am)
    return compras


# ───────────────────────── Categorias (por departamento) ─────────────────────────
def categorias_dados(rbac, codclis=None):
    """[(codepto, venda_liq, lucro, clientes, produtos)] últimos 12m. codclis = escopo de cadastro."""
    hoje = hoje_analitico()
    d12 = hoje - timedelta(days=365)
    w = escopo_where(rbac, "faturamento_vendas")
    if codclis is not None:
        ids = ",".join(str(int(x)) for x in codclis) or "-1"
        w += f" AND codcli IN ({ids})"
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"""SELECT codepto, {VB} venda,
                coalesce(sum(vlvenda-icmsretido-vlfecp-vlcustofin) FILTER (WHERE codoper='S'),0) lucro,
                count(DISTINCT codcli) FILTER (WHERE codoper='S') clientes,
                count(DISTINCT codprod) FILTER (WHERE codoper='S') produtos
            FROM faturamento_vendas WHERE dtsaida BETWEEN %s AND %s{w}
            GROUP BY codepto ORDER BY venda DESC""", (d12, hoje))
        return [{"CODEPTO": cd, "VendaLiquida": float(v), "LucroTotal": float(lu),
                 "ClientesUnicos": cl or 0, "ProdutosUnicos": pr or 0}
                for cd, v, lu, cl, pr in cur.fetchall()]


# ───────────────────────── Dashboard: sub-endpoints ─────────────────────────
def _dev_mes(cur, tab, vexpr, cexpr, d0, d1, w):
    cur.execute(f"SELECT to_char(date_trunc('month',dtent),'YYYYMM')::int am, {vexpr}, {cexpr} "
                f"FROM {tab} WHERE dtent BETWEEN %s AND %s{w} GROUP BY 1", (d0, d1))
    return {am: (float(v), float(cd)) for am, v, cd in cur.fetchall()}


def serie_mensal(rbac, d0, d1):
    """[{AnoMes, VendaLiquida, LucroTotal}] por mês — venda(dtsaida) − devoluções(dtent)."""
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"SELECT to_char(date_trunc('month',dtsaida),'YYYYMM')::int am, {VB} b, {CT} ct "
                    f"FROM faturamento_vendas WHERE dtsaida BETWEEN %s AND %s"
                    f"{escopo_where(rbac,'faturamento_vendas')} GROUP BY 1", (d0, d1))
        vpm = {am: (float(b), float(ct)) for am, b, ct in cur.fetchall()}
        dpm = _dev_mes(cur, "faturamento_devolucao", DEV, CDEV, d0, d1,
                       escopo_where(rbac, "faturamento_devolucao"))
        apm = _dev_mes(cur, "faturamento_devolucao_avulsa", "coalesce(sum(vldevolucao),0)",
                       "coalesce(sum(vlcusto),0)", d0, d1,
                       escopo_where(rbac, "faturamento_devolucao_avulsa"))
    rows = []
    for am in sorted(set(vpm) | set(dpm) | set(apm)):
        b, ct = vpm.get(am, (0, 0))
        dv, cdv = dpm.get(am, (0, 0))
        dva, cdva = apm.get(am, (0, 0))
        vl = round(b - dv - dva, 2)
        rows.append({"AnoMes": am, "VendaLiquida": vl,
                     "LucroTotal": round(vl - (ct - cdv - cdva), 2)})
    return rows


def sazonalidade(rbac):
    """[{Ano, MES, VendaLiquida}] dos últimos 24m."""
    d0, d1 = hoje_analitico() - timedelta(days=730), hoje_analitico()
    return [{"Ano": am // 100, "MES": am % 100, "VendaLiquida": r["VendaLiquida"]}
            for am, r in ((row["AnoMes"], row) for row in serie_mensal(rbac, d0, d1))]


def pareto_clientes(rbac, top=50):
    """Top N clientes por venda líquida 12m: [{CODCLI, CLIENTE, UF, Venda12m}]."""
    d0, d1 = periodo_sql("12m")
    wv = escopo_where(rbac, "faturamento_vendas")
    wd = escopo_where(rbac, "faturamento_devolucao")
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"SELECT codcli, max(cliente), max(uf), {VB} FROM faturamento_vendas "
                    f"WHERE dtsaida BETWEEN %s AND %s{wv} GROUP BY codcli", (d0, d1))
        rows = {cc: [cli, uf, float(b), 0.0] for cc, cli, uf, b in cur.fetchall()}
        cur.execute(f"SELECT codcli, {DEV} FROM faturamento_devolucao "
                    f"WHERE dtent BETWEEN %s AND %s{wd} GROUP BY codcli", (d0, d1))
        for cc, d in cur.fetchall():
            if cc in rows:
                rows[cc][3] += float(d)
    out = [{"CODCLI": cc, "CLIENTE": v[0], "UF": v[1], "Venda12m": round(v[2] - v[3], 2)}
           for cc, v in rows.items()]
    out.sort(key=lambda x: x["Venda12m"], reverse=True)
    return out[:top]


def yoy_dashboard(rbac):
    """{receita_liquida, lucro_bruto, positivacao_cliente, positivacao_mix} — 12m vs 12m anterior."""
    with analytics_conn() as c:
        cur = c.cursor()
        return _yoy4(cur, periodo_sql("12m"), periodo_sql("12m_anterior"), rbac)


def _in_frag(valores, col):
    """Fragmento ' AND col IN (...)' seguro. None → '' (sem filtro); vazio → impossível (-1)."""
    if valores is None:
        return ""
    ids = ",".join(str(int(v)) for v in valores)
    return f" AND {col} IN ({ids or '-1'})"


# ───────────────────────── Dashboard: top-clientes (por lucro/venda) ─────────────────────────
def top_clientes(rbac, metrica="lucro", limit=10):
    """Top N clientes por lucro/venda líquida 12m. Espelha api_dashboard_top_clientes:
    [{CODCLI, CLIENTE, UF, CODUSUR, Venda12m, Lucro12m}]. Venda líq = venda − devolução; lucro =
    (venda−dev) − (custo−custo_dev), somando devolução avulsa."""
    d0, d1 = periodo_sql("12m")
    wv = escopo_where(rbac, "faturamento_vendas")
    wd = escopo_where(rbac, "faturamento_devolucao")
    wa = escopo_where(rbac, "faturamento_devolucao_avulsa")
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"SELECT codcli, max(cliente), max(uf), max(codusur), {VB}, {CT} "
                    f"FROM faturamento_vendas WHERE dtsaida BETWEEN %s AND %s{wv} "
                    f"GROUP BY codcli", (d0, d1))
        rows = {cc: {"CLIENTE": cli, "UF": uf, "CODUSUR": cu, "b": float(b), "ct": float(ct),
                     "dv": 0.0, "cdv": 0.0}
                for cc, cli, uf, cu, b, ct in cur.fetchall()}
        cur.execute(f"SELECT codcli, {DEV}, {CDEV} FROM faturamento_devolucao "
                    f"WHERE dtent BETWEEN %s AND %s{wd} GROUP BY codcli", (d0, d1))
        for cc, dv, cdv in cur.fetchall():
            if cc in rows:
                rows[cc]["dv"] += float(dv); rows[cc]["cdv"] += float(cdv)
        cur.execute(f"SELECT codcli, coalesce(sum(vldevolucao),0), coalesce(sum(vlcusto),0) "
                    f"FROM faturamento_devolucao_avulsa WHERE dtent BETWEEN %s AND %s{wa} "
                    f"GROUP BY codcli", (d0, d1))
        for cc, dv, cdv in cur.fetchall():
            if cc in rows:
                rows[cc]["dv"] += float(dv); rows[cc]["cdv"] += float(cdv)
    out = []
    for cc, v in rows.items():
        vl = round(v["b"] - v["dv"], 2)
        out.append({"CODCLI": cc, "CLIENTE": v["CLIENTE"], "UF": v["UF"], "CODUSUR": v["CODUSUR"],
                    "Venda12m": vl, "Lucro12m": round(vl - (v["ct"] - v["cdv"]), 2)})
    chave = "Lucro12m" if metrica == "lucro" else "Venda12m"
    out.sort(key=lambda x: x[chave], reverse=True)
    return out[:limit]


# ───────────────────────── Metas (dataset META → pcpedc/pcpedi) ─────────────────────────
# Fonte do REALIZADO = PEDIDOS (pcpedc), não faturamento (memória multpel-metas-bi). Filtros de
# posição espelham _carregar_metas_realizado: venda/rentab por posicao IN ('F','L'); clientes por
# ('F','L','B'). Na demo, posicao é toda 'F' (gerar_fato) — os IN reduzem a 'F', contam tudo.
# MIX vem de faturamento_vendas (distinct codprod do mês): pcpedc é por pedido (sem codprod) e a
# pcpedi da demo está vazia — calibração de demo, coerente (o mês é o mesmo).
def _dias_uteis_meta(ano, mes):
    """Dias úteis (seg-sex) do mês: total/decorridos/restantes, ancorados em hoje_analitico.
    Sem feriados (a demo não tem calendário de feriado) — alinha com _dias_uteis_mes_fechado."""
    import calendar as _cal
    h = hoje_analitico()
    n = _cal.monthrange(ano, mes)[1]
    uteis = [date(ano, mes, d) for d in range(1, n + 1) if date(ano, mes, d).weekday() < 5]
    tot = len(uteis)
    if (ano, mes) < (h.year, h.month):
        return {"mes": tot, "decorridos": tot, "restantes": 0}
    if (ano, mes) > (h.year, h.month):
        return {"mes": tot, "decorridos": 0, "restantes": tot}
    dec = sum(1 for d in uteis if d <= h)
    return {"mes": tot, "decorridos": dec, "restantes": tot - dec}


def metas_realizado(ano, mes, escopo):
    """Realizado das 4 métricas por vendedor/supervisor/total, no formato de
    _carregar_metas_realizado: {dias, por_supervisor, por_vendedor, total}. escopo = set de codusur
    (ou None = tudo). proj_venda=None → server recalcula run-rate via metas.linha_metrica."""
    ano, mes = int(ano), int(mes)
    e_ped = _in_frag(escopo, "p.codusur")     # pcpedc alias p
    e_fat = _in_frag(escopo, "codusur")       # faturamento_vendas
    e_fatu = _in_frag(escopo, "fv.codusur")   # faturamento com alias fv (join)
    wy = "extract(year FROM p.data)=%s AND extract(month FROM p.data)=%s"
    wyf = "extract(year FROM dtsaida)=%s AND extract(month FROM dtsaida)=%s"
    fl = "p.posicao IN ('F','L')"
    flb = "p.posicao IN ('F','L','B')"
    # venda + clientes vêm de pcpedc (pedidos). rentabilidade(lucro R$) + mix vêm de
    # faturamento_vendas: pcpedc não tem custo (vlcustofin=NULL) nem codprod (grão=pedido) e a pcpedi
    # da demo está vazia. Calibração de demo, coerente (mesmo mês/escopo). venda_sb = venda (sem bônus).
    _vazio = {"venda": 0, "venda_sb": 0, "rentabilidade": 0, "clientes": 0, "mix": 0, "proj_venda": None}
    with analytics_conn() as c:
        cur = c.cursor()
        # ── por vendedor ──
        cur.execute(f"""SELECT p.codusur,
                coalesce(sum(p.vlatend) FILTER (WHERE {fl}),0) venda,
                count(DISTINCT p.codcli) FILTER (WHERE {flb}) clientes
            FROM pcpedc p WHERE {wy}{e_ped} GROUP BY p.codusur""", (ano, mes))
        por_vendedor = {}
        for cu, venda, cli in cur.fetchall():
            if cu is not None:
                por_vendedor[str(int(cu))] = {**_vazio, "venda": float(venda),
                                              "venda_sb": float(venda), "clientes": int(cli or 0)}
        cur.execute(f"""SELECT codusur, count(DISTINCT codprod) mix, {LUCRO} lucro
            FROM faturamento_vendas WHERE codoper='S' AND {wyf}{e_fat}
            GROUP BY codusur""", (ano, mes))
        for cu, mix, lucro in cur.fetchall():
            if cu is None:
                continue
            d = por_vendedor.setdefault(str(int(cu)), dict(_vazio))
            d["mix"] = int(mix or 0); d["rentabilidade"] = float(lucro)
        # ── por supervisor (join pcusuari; clientes/mix distinct no grão) ──
        cur.execute(f"""SELECT u.codsupervisor,
                coalesce(sum(p.vlatend) FILTER (WHERE {fl}),0) venda,
                count(DISTINCT p.codcli) FILTER (WHERE {flb}) clientes
            FROM pcpedc p JOIN pcusuari u ON u.codusur=p.codusur
            WHERE {wy}{e_ped} GROUP BY u.codsupervisor""", (ano, mes))
        por_supervisor = {}
        for cs, venda, cli in cur.fetchall():
            if cs is not None:
                por_supervisor[str(int(cs))] = {**_vazio, "nome": None, "venda": float(venda),
                                                "venda_sb": float(venda), "clientes": int(cli or 0)}
        cur.execute(f"""SELECT u.codsupervisor, count(DISTINCT fv.codprod) mix, {LUCRO} lucro
            FROM faturamento_vendas fv JOIN pcusuari u ON u.codusur=fv.codusur
            WHERE fv.codoper='S' AND extract(year FROM fv.dtsaida)=%s
              AND extract(month FROM fv.dtsaida)=%s{e_fatu} GROUP BY u.codsupervisor""", (ano, mes))
        for cs, mix, lucro in cur.fetchall():
            if cs is None:
                continue
            d = por_supervisor.setdefault(str(int(cs)), {**_vazio, "nome": None})
            d["mix"] = int(mix or 0); d["rentabilidade"] = float(lucro)
        # ── totais (distinct verdadeiro no escopo) ──
        cur.execute(f"""SELECT coalesce(sum(p.vlatend) FILTER (WHERE {fl}),0),
                count(DISTINCT p.codcli) FILTER (WHERE {flb})
            FROM pcpedc p WHERE {wy}{e_ped}""", (ano, mes))
        tv, tc = cur.fetchone()
        cur.execute(f"""SELECT count(DISTINCT codprod), {LUCRO} FROM faturamento_vendas
            WHERE codoper='S' AND {wyf}{e_fat}""", (ano, mes))
        tmix, tlucro = cur.fetchone()
    return {
        "dias": _dias_uteis_meta(ano, mes),
        "por_supervisor": por_supervisor,
        "por_vendedor": por_vendedor,
        "total": {"venda": float(tv), "venda_sb": float(tv), "rentabilidade": float(tlucro or 0),
                  "clientes": int(tc or 0), "mix": int(tmix or 0), "proj_venda": None},
    }


# ───────────────────────── nomes de depto sintéticos (demo) ─────────────────────────
# A demo não tem nome textual de depto (gerar_fato grava "DEVOLUCAO"). Damos nomes temáticos de
# atacadista, atribuídos por posição (sorted codepto) → estáveis e sem colisão até 40 deptos.
_DEPTO_TEMAS = [
    "Higiene Pessoal", "Alimentos", "Bebidas", "Limpeza", "Bazar", "Descartáveis", "Perfumaria",
    "Cosméticos", "Matinais", "Mercearia Doce", "Mercearia Salgada", "Laticínios", "Congelados",
    "Pet Shop", "Farmácia", "Papelaria", "Utilidades", "Automotivo", "Ferramentas", "Eletroportáteis",
    "Cama & Banho", "Brinquedos", "Festas", "Calçados", "Vestuário", "Jardinagem", "Construção",
    "Tabacaria", "Confeitaria", "Hortifruti", "Padaria", "Frios", "Sucos", "Snacks", "Higiene do Lar",
    "Beleza", "Infantil", "Saúde", "Bebidas Quentes", "Limpeza Pesada",
]


def deptos_map_sintetico():
    """{'deptos': {codepto_str: nome}, 'secoes': {}} — nomes temáticos p/ a demo (Categorias/Mix/Radar)."""
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute("SELECT DISTINCT codepto FROM faturamento_vendas WHERE codepto IS NOT NULL ORDER BY codepto")
        deptos = {str(cd): _DEPTO_TEMAS[i % len(_DEPTO_TEMAS)] for i, (cd,) in enumerate(cur.fetchall())}
    return {"deptos": deptos, "secoes": {}}


def metas_sugestao_historico(codusur):
    """Histórico mensal de 1 vendedor p/ a sugestão de meta (Admin): [{AnoMes, venda, rentab, cli, mix}].
    Proxy do alvo via faturamento (como o caminho DAX) — ponto de partida que o admin ajusta."""
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"""SELECT {_AM_FAT} am, {VB} venda,
                coalesce(sum(vlvenda-icmsretido-vlfecp-vlcustofin) FILTER (WHERE codoper='S'),0) rentab,
                count(DISTINCT codcli) FILTER (WHERE codoper='S') cli,
                count(DISTINCT codprod) FILTER (WHERE codoper='S') mix
            FROM faturamento_vendas WHERE codusur=%s GROUP BY am HAVING {VB} <> 0""", (int(codusur),))
        return [{"AnoMes": am, "venda": float(v), "rentab": float(r), "cli": int(cl or 0), "mix": int(mx or 0)}
                for am, v, r, cl, mx in cur.fetchall()]


def metas_serie(ano, mes, escopo):
    """Realizado diário (venda) do mês pra o gráfico de série: [{data:'YYYY-MM-DD', venda}]."""
    ano, mes = int(ano), int(mes)
    ei = _in_frag(escopo, "codusur")
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"""SELECT data, coalesce(sum(vlatend),0) FROM pcpedc
            WHERE extract(year FROM data)=%s AND extract(month FROM data)=%s
              AND posicao IN ('F','L','B'){ei} GROUP BY data ORDER BY data""", (ano, mes))
        return [{"data": d.isoformat(), "venda": float(v)} for d, v in cur.fetchall()]


# ───────────────────────── Mix abandonado (grão cliente×depto / cliente×fornecedor) ─────────────────────────
# GLOBAIS (sem RBAC): o recorte por cadastro é feito no app via carteira_idx. VENDA LIQUIDA≈VB,
# LUCRO≈venda−custo no grão (mesma simplificação de categorias). Chaves batem com clean_rows do DAX.
def _mix_agrupado(group_cols, agg_cols, where_extra, args):
    """Agrega faturamento 12m por group_cols; agg_cols = expressões agregadas EXTRA no SELECT (não
    entram no GROUP BY). Retorna as linhas cruas: (group_cols..., agg_cols..., ultima, venda, lucro)."""
    d0, _ = periodo_sql("12m")
    d1 = hoje_analitico()
    sel = group_cols + (", " + agg_cols if agg_cols else "")
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"""SELECT {sel},
                max(dtsaida) FILTER (WHERE codoper='S') ultima, {VB} venda, {LUCRO} lucro
            FROM faturamento_vendas
            WHERE dtsaida BETWEEN %s AND %s{where_extra}
            GROUP BY {group_cols}""", (d0, d1, *args))
        return cur.fetchall()


def mix_abandonado_raw(codepto=None, fornecedor=None):
    """Pares cliente×depto dos últimos 12m: [{CODCLI, CODEPTO, UltimaCompra, VendaCat12m, LucroCat12m}]."""
    we, args = "", []
    if codepto is not None:
        we += " AND codepto=%s"; args.append(int(codepto))
    if fornecedor is not None:
        we += " AND codfornecprinc=%s"; args.append(int(fornecedor))
    out = []
    for cc, cd, ultima, venda, lucro in _mix_agrupado("codcli, codepto", "", we, args):
        out.append({"CODCLI": cc, "CODEPTO": cd,
                    "UltimaCompra": ultima.isoformat() if ultima else None,
                    "VendaCat12m": float(venda), "LucroCat12m": float(lucro)})
    return out


def mix_cliente_deptos_raw(codcli):
    """Deptos de 1 cliente (12m): [{CODEPTO, UltimaCompra, VendaCat12m, LucroCat12m}]."""
    out = []
    for cd, ultima, venda, lucro in _mix_agrupado("codepto", "", " AND codcli=%s", (int(codcli),)):
        out.append({"CODEPTO": cd, "UltimaCompra": ultima.isoformat() if ultima else None,
                    "VendaCat12m": float(venda), "LucroCat12m": float(lucro)})
    return out


def mix_cliente_fornecedores_raw(codcli):
    """Fornecedores de 1 cliente (12m): [{CODFORNECPRINC, FORNECPRINC, UltimaCompra, VendaCat12m, LucroCat12m}]."""
    out = []
    for cf, fnome, ultima, venda, lucro in _mix_agrupado(
            "codfornecprinc", "max(fornecprinc)", " AND codcli=%s", (int(codcli),)):
        out.append({"CODFORNECPRINC": cf, "FORNECPRINC": fnome,
                    "UltimaCompra": ultima.isoformat() if ultima else None,
                    "VendaCat12m": float(venda), "LucroCat12m": float(lucro)})
    return out


# ───────────────────────── Radar (produtos sangrando / drills) ─────────────────────────
def produtos_map():
    """Índice {codprod_str: {descricao, codepto, codfornec, fornec_nome, venda_12m}} dos produtos
    vendidos nos últimos 12m. Espelha _carregar_produtos_map (linha de maior venda define atributos)."""
    d0, _ = periodo_sql("12m")
    d1 = hoje_analitico()
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"""SELECT codprod, max(descricao), max(codepto), max(codfornecprinc),
                max(fornecprinc), {VB} venda,
                (array_agg(descricao ORDER BY vlvenda DESC))[1] desc_top,
                (array_agg(codepto ORDER BY vlvenda DESC))[1] depto_top,
                (array_agg(codfornecprinc ORDER BY vlvenda DESC))[1] fornec_top,
                (array_agg(fornecprinc ORDER BY vlvenda DESC))[1] fnome_top
            FROM faturamento_vendas
            WHERE dtsaida BETWEEN %s AND %s AND codoper='S'
            GROUP BY codprod""", (d0, d1))
        idx = {}
        for row in cur.fetchall():
            cp = row[0]
            if cp is None:
                continue
            idx[str(int(cp))] = {"descricao": row[6] or row[1], "codepto": row[7] if row[7] is not None else row[2],
                                 "codfornec": row[8] if row[8] is not None else row[3],
                                 "fornec_nome": row[9] or row[4], "venda_12m": float(row[5])}
        return idx


def radar_board(dias, rbac=None, codclis=None):
    """(rec, ant, perdidos) por codprod: recente [hoje-dias, hoje] vs anterior [hoje-2d, hoje-dias).
    rec[cp]={'VendaRec','CliRec'}, ant[cp]={'VendaAnt','CliAnt'}, perdidos[cp]=int.
    Escopo via rbac e/ou codclis (espelha o rbac_frag do board — hoje sempre por CADASTRO)."""
    h = hoje_analitico()
    d_rec, d_ant = h - timedelta(days=dias), h - timedelta(days=2 * dias)
    d_mid = h - timedelta(days=dias)
    we = escopo_where(rbac, "faturamento_vendas") + _in_frag(codclis, "codcli")
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"""SELECT codprod, {VB} v, count(DISTINCT codcli) cli
            FROM faturamento_vendas WHERE codoper='S' AND dtsaida >= %s{we}
            GROUP BY codprod""", (d_rec,))
        rec = {cp: {"VendaRec": float(v), "CliRec": int(cli or 0)}
               for cp, v, cli in cur.fetchall() if cp is not None}
        cur.execute(f"""SELECT codprod, {VB} v, count(DISTINCT codcli) cli
            FROM faturamento_vendas WHERE codoper='S' AND dtsaida >= %s AND dtsaida < %s{we}
            GROUP BY codprod""", (d_ant, d_mid))
        ant = {cp: {"VendaAnt": float(v), "CliAnt": int(cli or 0)}
               for cp, v, cli in cur.fetchall() if cp is not None}
        # ⚠️ PERDIDOS de verdade: quem comprou o produto na janela ANTERIOR e cuja ULTIMA compra
        # DELE ficou antes da janela recente. NAO e `CliAnt - CliRec` (saldo de contagens) — se 20
        # clientes param e 20 novos entram, o saldo da zero e esconde os 20 que sairam. Medido no
        # BI real: 70,1% dos produtos do board subestimavam, escondendo 14.193 clientes no total,
        # e alguns mostravam ZERO com mais de 100 clientes parados.
        cur.execute(f"""SELECT codprod, count(*) FROM (
              SELECT codprod, codcli, max(dtsaida) ult
                FROM faturamento_vendas
               WHERE codoper='S' AND dtsaida >= %s{we}
               GROUP BY codprod, codcli) t
             WHERE ult < %s GROUP BY codprod""", (d_ant, d_mid))
        perdidos = {cp: int(n or 0) for cp, n in cur.fetchall() if cp is not None}
    return rec, ant, perdidos


def _radar_por_cli(cur, f_prod, d_ini, d_fim, val_alias, qt_alias):
    """Agrupa por codcli a venda líq (VB) + QT numa janela [d_ini, d_fim). d_fim None = aberto."""
    cur.execute(f"""SELECT codcli, {VB} {val_alias}, coalesce(sum(qt) FILTER (WHERE codoper='S'),0) {qt_alias}
        FROM faturamento_vendas
        WHERE {f_prod} AND dtsaida >= %s{' AND dtsaida < %s' if d_fim else ''}
        GROUP BY codcli""", (d_ini, *((d_fim,) if d_fim else ())))
    return {r[0]: {val_alias: float(r[1]), qt_alias: float(r[2])}
            for r in cur.fetchall() if r[0] is not None}


def radar_produto_detalhe(codprod, dias, codepto=None):
    """Formato consumido por _radar_detalhe_rows: {'cli':[...], 'rec':{...}, 'ant':{...}, 'canib':set}.
    GLOBAL (o recorte por cadastro é feito no app). cli agrupado por codcli (12m); rec/ant janelas."""
    h = hoje_analitico()
    d12 = h - timedelta(days=365)
    d_rec, d_ant, d_mid = h - timedelta(days=dias), h - timedelta(days=2 * dias), h - timedelta(days=dias)
    f_prod = f"codprod={int(codprod)}"
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"""SELECT codcli, max(dtsaida) FILTER (WHERE codoper='S') ultima,
                {VB} venda, coalesce(sum(qt) FILTER (WHERE codoper='S'),0) qt
            FROM faturamento_vendas WHERE {f_prod} AND dtsaida >= %s
            GROUP BY codcli""", (d12,))
        cli = [{"CODCLI": r[0], "Ultima": r[1].isoformat() if r[1] else None,
                "Venda12m": float(r[2]), "Qt12m": float(r[3])}
               for r in cur.fetchall() if r[0] is not None]
        rec = {k: {"VendaRec": v["VendaRec"], "QtRec": v["QtRec"]}
               for k, v in _radar_por_cli(cur, f_prod, d_rec, None, "VendaRec", "QtRec").items()}
        ant = {k: {"VendaAnt": v["VendaAnt"], "QtAnt": v["QtAnt"]}
               for k, v in _radar_por_cli(cur, f_prod, d_ant, d_mid, "VendaAnt", "QtAnt").items()}
        canib = set()
        if codepto is not None:
            cur.execute(f"""SELECT DISTINCT codcli FROM faturamento_vendas
                WHERE codepto=%s AND codprod<>%s AND dtsaida >= %s""",
                        (int(codepto), int(codprod), d_rec))
            canib = {r[0] for r in cur.fetchall() if r[0] is not None}
    return {"cli": cli, "rec": rec, "ant": ant, "canib": canib}


def radar_cliente_raw(codcli, dias):
    """Formato consumido por _radar_cliente_rows: {'prod':[...], 'rec':{...}, 'ant':{...}}.
    Fixa codcli, agrupa por codprod. GLOBAL (o app já valida cli_meta no escopo)."""
    h = hoje_analitico()
    d12 = h - timedelta(days=365)
    d_rec, d_ant, d_mid = h - timedelta(days=dias), h - timedelta(days=2 * dias), h - timedelta(days=dias)
    f_cli = f"codcli={int(codcli)}"
    with analytics_conn() as c:
        cur = c.cursor()
        cur.execute(f"""SELECT codprod, max(dtsaida) FILTER (WHERE codoper='S') ultima,
                {VB} venda, coalesce(sum(qt) FILTER (WHERE codoper='S'),0) qt
            FROM faturamento_vendas WHERE {f_cli} AND dtsaida >= %s
            GROUP BY codprod""", (d12,))
        prod = [{"CODPROD": r[0], "Ultima": r[1].isoformat() if r[1] else None,
                 "Venda12m": float(r[2]), "Qt12m": float(r[3])}
                for r in cur.fetchall() if r[0] is not None]
        rec = _radar_por_cli_prod(cur, f_cli, d_rec, None, "VendaRec", "QtRec")
        ant = _radar_por_cli_prod(cur, f_cli, d_ant, d_mid, "VendaAnt", "QtAnt")
    return {"prod": prod, "rec": rec, "ant": ant}


def _radar_por_cli_prod(cur, f_cli, d_ini, d_fim, val_alias, qt_alias):
    """Agrupa por codprod a venda líq + QT de UM cliente numa janela."""
    cur.execute(f"""SELECT codprod, {VB} {val_alias}, coalesce(sum(qt) FILTER (WHERE codoper='S'),0) {qt_alias}
        FROM faturamento_vendas
        WHERE {f_cli} AND dtsaida >= %s{' AND dtsaida < %s' if d_fim else ''}
        GROUP BY codprod""", (d_ini, *((d_fim,) if d_fim else ())))
    return {r[0]: {val_alias: float(r[1]), qt_alias: float(r[2])}
            for r in cur.fetchall() if r[0] is not None}
