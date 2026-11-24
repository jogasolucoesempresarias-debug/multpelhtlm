"""Gate da MÉDIA DO PERÍODO na Meta de ruptura (24/11/2026, pedido do João Victor).

*"A meta do comprador é em cima da compra, não em cima da meta real … trazer o registro diário
da ruptura e colocar a média do período no card."*

O placar só mostrava o retrato de agora. Meta de pessoa se fecha no período, então:

1. a foto diária passa a expor a régua do placar POR CURVA (`ruptura_curva[c].sem_prov`), ao
   lado da ruptura real — as duas com o mesmo denominador;
2. `historico.agregar_meta` tira a média simples dos % diários por comprador × curva, com a
   MESMA conta do `metaAgrega()` da tela (pré-entrada não conta, sem curva → C);
3. o rollup velho (sem a subchave) deixa de ser servido — subchave nova dentro de chave que já
   existe não é vista pela checagem por chaves, só pelo selo `_ROLLUP_VERSAO`.
"""
from datetime import date

import pytest

from estoque import historico, routes

from tests.conftest import _criar_usuario, _remover_usuario, login_as


D1, D2 = date(2026, 11, 2), date(2026, 11, 3)


def _cru(dia, qtdisp, giro_dia, curva="A", ja_ped=0, transito=0):
    """Linha na ordem posicional do `_SQL_CRU` (ver `test_historico_estoque._linha`)."""
    return (dia, qtdisp, 0.0, giro_dia, 9999 if giro_dia <= 0 else 0, None, None, curva,
            ja_ped, transito)


def _meta(dia, cod, curva, qtdisp, giro_dia, ja_ped=0, transito=0):
    """Linha na ordem do `_SQL_META`."""
    return (dia, cod, curva, qtdisp, giro_dia, ja_ped, transito)


# ───────────────── 1. a foto do dia, por curva ─────────────────

def test_foto_expoe_sem_pedido_por_curva_ao_lado_da_ruptura_real():
    s = historico.agregar([
        _cru(D1, 0, 1.0, "A"),                    # ruptura sem providência
        _cru(D1, 0, 1.0, "A", ja_ped=10),         # ruptura COM pedido em aberto
        _cru(D1, 0, 1.0, "A", transito=5),        # ruptura com pré-entrada (providência)
        _cru(D1, 8, 1.0, "A"),                    # tem estoque
        _cru(D1, 0, 1.0, None),                   # sem curva → C, sem providência
    ])[0]
    a, c = s["ruptura_curva"]["A"], s["ruptura_curva"]["C"]
    assert (a["n"], a["sem_prov"], a["skus"]) == (3, 1, 4)
    assert a["pct_sem_prov"] == 25.0 and a["pct"] == 75.0
    assert (c["sem_prov"], c["pct_sem_prov"]) == (1, 100.0)
    assert s["ruptura_curva"]["B"]["pct_sem_prov"] is None, "curva vazia = não medido, não zero"
    # o total da coluna antiga continua sendo a soma das curvas
    assert s["n_rup_sem_prov"] == sum(v["sem_prov"] for v in s["ruptura_curva"].values())


def test_rollup_sem_a_subchave_nao_e_servido():
    """O rollup gravado pela versão 6 tem TODAS as chaves de topo — só falta a subchave. Sem o
    selo, a coluna S/ ped. sairia "—" para o passado inteiro em silêncio."""
    velho = {k: 0 for k in historico._ROLLUP_CHAVES}
    velho["_v"] = 6
    assert historico._ROLLUP_VERSAO >= 7
    assert not historico._rollup_atual(velho)


# ───────────────── 2. a média do período ─────────────────

def test_media_e_simples_dos_percentuais_diarios():
    """Dia 1: 1 de 4 (25%). Dia 2: 0 de 2 (0%). Média simples = 12,5% — não 1/6 = 16,7%
    (que daria mais peso ao dia com mais SKUs)."""
    linhas = ([_meta(D1, 47, "A", 0, 1.0)] + [_meta(D1, 47, "A", 5, 1.0)] * 3
              + [_meta(D2, 47, "A", 5, 1.0)] * 2)
    r = historico.agregar_meta(linhas)
    m = r["compradores"]["47"]["A"]
    assert m["pct"] == 12.5 and m["dias"] == 2
    assert r["dias"] == ["2026-11-02", "2026-11-03"]
    assert r["compradores"]["47"]["B"] is None


def test_media_usa_a_regua_do_placar():
    """Pedido em aberto e pré-entrada são providência; item sem giro não é ruptura; sem curva
    entra na C; sem comprador vira o grupo 0 — tudo como o `metaAgrega()` da tela."""
    r = historico.agregar_meta([
        _meta(D1, 47, "A", 0, 1.0, ja_ped=3),     # tem pedido → não conta
        _meta(D1, 47, "A", 0, 1.0, transito=3),   # pré-entrada → não conta
        _meta(D1, 47, "A", 0, 0.0),               # sem giro → não é ruptura
        _meta(D1, 47, "A", 0, 2.0),               # conta
        _meta(D1, None, None, 0, 1.0),            # sem comprador, sem curva → grupo 0, curva C
    ])
    assert r["compradores"]["47"]["A"]["pct"] == 25.0
    assert r["compradores"]["0"]["C"]["pct"] == 100.0
    assert r["empresa"]["A"]["pct"] == 25.0 and r["empresa"]["C"]["pct"] == 100.0


def test_media_e_foto_contam_igual():
    """A média (por comprador) e a coluna da foto (empresa) são duas leituras do mesmo dia — se
    uma mudar de régua sem a outra, o card contradiz a tabela da Evolução."""
    casos = [(0, 1.0, "A", 0, 0), (0, 1.0, "A", 2, 0), (0, 1.0, "B", 0, 4), (3, 1.0, "B", 0, 0),
             (0, 1.0, "C", 0, 0), (0, 0.0, "C", 0, 0), (0, 1.0, "X", 0, 0)]
    foto = historico.agregar([_cru(D1, q, g, cv, jp, tr) for q, g, cv, jp, tr in casos])[0]
    meta = historico.agregar_meta([_meta(D1, 9, cv, q, g, jp, tr) for q, g, cv, jp, tr in casos])
    for cv in historico.CURVAS:
        # a foto arredonda a 1 casa (como a ruptura real), a média a 2 — compara-se na da foto
        assert round(meta["empresa"][cv]["pct"], 1) == foto["ruptura_curva"][cv]["pct_sem_prov"], cv


def test_comprador_e_o_da_foto_do_dia():
    """Item remanejado de comprador no meio do mês: cada dia conta para quem era o dono."""
    r = historico.agregar_meta([_meta(D1, 47, "A", 0, 1.0), _meta(D2, 52, "A", 0, 1.0)])
    assert r["compradores"]["47"]["A"]["dias"] == 1
    assert r["compradores"]["52"]["A"]["dias"] == 1


# ───────────────── 3. período ─────────────────

@pytest.mark.parametrize("periodo,hoje,esperado", [
    ("mes", date(2026, 11, 24), (date(2026, 11, 1), date(2026, 11, 24), "nov/26")),
    ("mes_ant", date(2026, 11, 24), (date(2026, 10, 1), date(2026, 10, 31), "out/26")),
    ("mes_ant", date(2027, 1, 5), (date(2026, 12, 1), date(2026, 12, 31), "dez/26")),
])
def test_periodo_da_media(periodo, hoje, esperado):
    assert routes._periodo_meta(periodo, hoje) == esperado


# ───────────────── 4. endpoint ─────────────────

@pytest.fixture
def comprador_logado(client):
    """Não-ADM com a área Compras: o placar é dele também, e a média não pode ser só do ADM."""
    email = "viewer-meta-media@teste.local"
    _criar_usuario(email, "senha123", role="viewer", must_change=False)
    import server
    conn = server.get_db()
    cur = conn.cursor()
    cur.execute("""UPDATE multpel_users SET areas = '["comercial","compras"]'::jsonb, ativo = true
                   WHERE email = %s""", (email,))
    conn.commit()
    cur.close()
    conn.close()
    login_as(client, email, "senha123")
    yield email
    _remover_usuario(email)


def test_endpoint_responde_bem_formado_para_quem_nao_e_admin(client, comprador_logado):
    r = client.get("/estoque/api/meta-ruptura/media?periodo=mes_ant")
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] is True and j["periodo"] == "mes_ant"
    # sem foto (ou sem Postgres) a resposta ainda é utilizável: a tela cai no número de agora
    assert "indisponivel" in j or {"dias", "compradores", "empresa"} <= set(j)
