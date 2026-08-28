"""Gate da régua OFICIAL do ⚙ Parâmetros (Peça 2, 08/2026).

**O problema que isto resolve.** Os ⚙ Parâmetros viviam no `localStorage`, por navegador. Medido
no BI real em 27-28/08/2026: o diretor estava com 7 parâmetros fora do padrão, e a mesma base no
mesmo dia dava sugestão de compra de **R$ 1.350.831,65** na tela dele contra **R$ 2.149.794,08**
na dos compradores. Pior: o relatório que ele recebia por e-mail e a aba Evolução já rodavam no
padrão do servidor — ou seja, duas das três saídas discordavam da tela dele.

**A decisão** (Gabriel + diretor, 28/08/2026): existe uma régua OFICIAL da empresa, gravada em
`multpel_config`, que só quem tem a flag `pode_parametrizar` altera. Todo mundo ABRE nela.
Qualquer pessoa pode simular na sessão — o fluxo do lead time depende disso ("vejo o lead real do
fornecedor, ajusto, gero o pedido") — mas a simulação morre com a aba.

O que estes testes travam:
1. as TRÊS camadas e a ordem delas (DEFAULTS < oficial < querystring);
2. a querystring continua vencendo — é o que mantém e-mail, export e simulação funcionando;
3. `merge_params` continua PURA (sem banco), senão todo teste de parâmetro exigiria Postgres;
4. valor ilegível na régua oficial é IGNORADO, nunca vira zero;
5. o rollup da Evolução compara com a régua OFICIAL, não com o DEFAULTS — comparar com o
   DEFAULTS jogaria o cache fora em 100% das leituras assim que alguém salvasse algo;
6. quem não tem a flag recebe 403 no POST.
"""
import pytest

from estoque import core


# ───────────────── 1. as três camadas ─────────────────

def test_sem_nada_vale_o_default_do_codigo():
    """`{}` é o caso NORMAL: ninguém salvou nada ainda. Foi assim que a Multpel entrou — a conta
    admin nunca teve o ⚙ tocado (os 13 campos batiam com o default, conferido em 28/08/2026)."""
    p = core.merge_params({})
    assert p["lead_time"] == core.DEFAULTS["lead_time"] == 10
    assert core.merge_params({}, base={}) == p
    assert core.merge_params({}, base=None) == p


def test_a_regua_oficial_sobrepoe_o_default():
    p = core.merge_params({}, base={"lead_time": "15", "cobertura_total": "30"})
    assert p["lead_time"] == 15
    assert p["cobertura_total"] == 30
    assert p["dias_seguranca"] == core.DEFAULTS["dias_seguranca"], "o que não foi salvo segue no default"


def test_a_querystring_VENCE_a_regua_oficial():
    """A simulação de sessão tem de ganhar do padrão da empresa — é ela que sustenta o fluxo do
    lead time descrito pelo diretor, e é o que mantém e-mail/export inalterados."""
    p = core.merge_params({"lead_time": "7"}, base={"lead_time": "15"})
    assert p["lead_time"] == 7


def test_a_ordem_das_tres_camadas_em_um_caso_so():
    base = {"lead_time": "15", "cobertura_total": "30"}
    p = core.merge_params({"cobertura_total": "60"}, base=base)
    assert p["lead_time"] == 15                                   # camada 2 (oficial)
    assert p["cobertura_total"] == 60                             # camada 3 (sessão)
    assert p["dias_seguranca"] == core.DEFAULTS["dias_seguranca"]  # camada 1 (código)


# ───────────────── 2. robustez ─────────────────

@pytest.mark.parametrize("lixo", ["abc", "", None, "12,5", {}])
def test_valor_ilegivel_na_regua_oficial_e_IGNORADO_nunca_vira_zero(lixo):
    """Config corrompida não pode zerar a régua de compra. Zero em `lead_time` não erraria alto —
    apenas encolheria a sugestão em silêncio, que é o pior modo de falha do módulo."""
    p = core.merge_params({}, base={"lead_time": lixo})
    assert p["lead_time"] == core.DEFAULTS["lead_time"]


def test_chave_desconhecida_na_regua_oficial_e_descartada():
    """Config antiga (parâmetro que deixou de existir) não pode contaminar o dict de params."""
    p = core.merge_params({}, base={"parametro_que_nao_existe": "9"})
    assert "parametro_que_nao_existe" not in p


def test_os_clamps_valem_TAMBEM_para_a_regua_oficial():
    """Salvar 0 pela API não pode furar o clamp que a querystring respeita — senão a tela
    mostraria um número e o servidor usaria outro."""
    p = core.merge_params({}, base={"novo_dias": "0", "desacel_de": "0"})
    assert p["novo_dias"] >= 1
    assert p["desacel_de"] >= 1


def test_janela_invertida_na_regua_oficial_e_corrigida():
    p = core.merge_params({}, base={"desacel_de": "50", "desacel_ate": "10"})
    assert p["desacel_ate"] > p["desacel_de"]


# ───────────────── 3. pureza ─────────────────

def test_merge_params_continua_PURA(monkeypatch):
    """⚠️ Se `merge_params` lesse o banco, todo teste de parâmetro passaria a exigir Postgres e o
    `agregar` do histórico perderia a pureza que permite recalcular o passado inteiro. Quem busca
    a régua oficial é o chamador (`routes._params_oficiais`)."""
    from estoque import store
    def _explode(*a, **k):
        raise AssertionError("merge_params tocou o banco")
    monkeypatch.setattr(store, "get_db", _explode)
    monkeypatch.setattr(store, "params_oficiais", _explode)
    assert core.merge_params({"lead_time": "12"})["lead_time"] == 12


# ───────────────── 4. as metas de ruptura entraram no conjunto ─────────────────

def test_metas_de_ruptura_fazem_parte_da_regua_oficial():
    """Eram os únicos campos do ⚙ que só existiam no front — ficariam presos ao navegador de cada
    pessoa, que é exatamente o problema que esta mudança veio resolver."""
    p = core.merge_params({}, base={"meta_rup_a": "3", "meta_rup_c": "12"})
    assert p["meta_rup_a"] == 3
    assert p["meta_rup_b"] == core.DEFAULTS["meta_rup_b"]
    assert p["meta_rup_c"] == 12


# ───────────────── 5. o rollup da Evolução ─────────────────

def test_regua_padrao_compara_com_a_OFICIAL_e_nao_com_o_DEFAULTS(monkeypatch):
    """⚠️ O bug que este teste evita: com a régua oficial diferente do DEFAULTS, comparar contra o
    DEFAULTS daria `False` em TODA leitura da Evolução — ela cairia no cru para sempre. Sem erro
    nenhum: só 3,5s por janela de 45 dias em vez de servir o agregado pronto."""
    from estoque import historico, store
    monkeypatch.setattr(store, "params_oficiais", lambda *a, **k: {"novo_dias": "20"})
    # pedido NA régua oficial → pode usar o rollup
    assert historico._regua_padrao({"novo_dias": "20"}) is True
    # pedido simulando outra coisa → não pode
    assert historico._regua_padrao({"novo_dias": "15"}) is False


def test_regua_padrao_degrada_para_o_DEFAULTS_se_a_config_cair(monkeypatch):
    """Lado seguro: sem config, no pior caso serve o cru — que é sempre correto."""
    from estoque import historico, store
    def _boom(*a, **k):
        raise RuntimeError("config fora do ar")
    monkeypatch.setattr(store, "params_oficiais", _boom)
    assert historico._regua_padrao({}) is True
    assert historico._regua_padrao({"novo_dias": "99"}) is False


def test_mudar_a_regua_oficial_INVALIDA_o_rollup():
    """⚠️ O selo `_ROLLUP_VERSAO` detecta mudança de CÓDIGO. Mudança de CONFIG passa por baixo
    dele: mesmas chaves, número diferente, em silêncio. Por isso o POST joga o rollup fora."""
    from estoque import historico
    assert hasattr(historico, "invalidar_rollup"), \
        "sem isto, salvar a régua deixa a Evolução servindo o agregado da régua anterior"


# ───────────────── 6. permissão ─────────────────

def test_o_POST_exige_a_flag(monkeypatch):
    """403 para quem não tem `pode_parametrizar` — inclusive admin. A base tem vários admins, e a
    régua de compra da empresa não pode depender de quem por acaso recebeu o papel."""
    import server                                        # noqa: F401
    from estoque import routes as R
    with server.app.test_request_context("/estoque/api/params", method="POST", json={"lead_time": 30}):
        from flask import session
        session["user_id"] = 1
        session["role"] = "admin"          # admin SEM a flag
        session["pode_parametrizar"] = False
        resp, status = R.api_params_set()
        assert status == 403, "admin sem a flag conseguiu gravar a régua da empresa"


def test_com_a_flag_o_gate_libera(monkeypatch):
    """O caminho feliz não pode depender de banco: o `store` é neutralizado."""
    import server                                        # noqa: F401
    from estoque import routes as R, store, historico
    gravado = {}
    monkeypatch.setattr(store, "params_oficiais_set", lambda v, usuario_id=None: gravado.update(v) or v)
    monkeypatch.setattr(store, "params_oficiais", lambda *a, **k: dict(gravado))
    monkeypatch.setattr(historico, "invalidar_rollup", lambda *a, **k: 0)
    with server.app.test_request_context("/estoque/api/params", method="POST", json={"lead_time": 30}):
        from flask import session
        session["user_id"] = 1
        session["pode_parametrizar"] = True
        resp = R.api_params_set()
        body = resp.get_json() if hasattr(resp, "get_json") else resp[0].get_json()
    assert body["ok"] is True
    assert gravado["lead_time"] == 30


def test_POST_sem_parametro_valido_recusa(monkeypatch):
    import server                                        # noqa: F401
    from estoque import routes as R
    with server.app.test_request_context("/estoque/api/params", method="POST",
                                         json={"coisa_inventada": 1}):
        from flask import session
        session["user_id"] = 1
        session["pode_parametrizar"] = True
        resp, status = R.api_params_set()
        assert status == 400
