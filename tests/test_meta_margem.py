# -*- coding: utf-8 -*-
"""Gate da meta de margem por comprador × competência (`store.metas_margem`).

A meta é a ÚNICA peça da Nota do comprador que não se recalcula do dado — é decisão, não medição.
Por isso ela é a única que precisa de tabela, de competência e de auditoria; e por isso um erro
aqui não aparece como número errado na tela, e sim como a nota de setembro mudando em dezembro.

Roda contra o Postgres REAL (como o resto da suíte). Limpa o que cria.
"""
import pytest

from estoque import store

# faixa de códigos que não existe no PCEMPR real — testar com o código de um comprador de verdade
# deixaria meta sintética viva num cadastro que a tela lê
CC_A, CC_B = 990001, 990002


@pytest.fixture
def limpo():
    if not store.ensure():
        pytest.skip("Postgres indisponível")

    def _apaga():
        conn = store.get_db()
        try:
            with conn, conn.cursor() as cur:
                cur.execute("DELETE FROM estoque_meta_margem WHERE codcomprador IN %s",
                            ((CC_A, CC_B),))
                cur.execute("DELETE FROM multpel_log WHERE rota='estoque:meta_margem' "
                            "  AND parametros LIKE %s", ("%99000%",))
        finally:
            conn.close()

    _apaga()
    yield
    _apaga()


# ───────────────── competência ─────────────────

def test_grava_e_le_na_competencia(limpo):
    store.meta_margem_set(CC_A, 17.0, 2026, 9, usuario_id=None)
    m = store.metas_margem(2026, 9)
    assert m[CC_A]["margem_meta"] == 17.0
    assert (m[CC_A]["ano"], m[CC_A]["mes"]) == (2026, 9)


def test_competencia_sem_linha_HERDA_a_anterior(limpo):
    """⚠️ Sem a herança, o diretor teria de recadastrar as metas todo dia 1º — e o mês que ele
    esquecesse apagaria a nota de TODO MUNDO (margem sai None ⇒ nota final deixa de existir).
    Recadastrar só é necessário quando a meta MUDA."""
    store.meta_margem_set(CC_A, 17.0, 2026, 9)
    for ano, mes in ((2026, 10), (2026, 12), (2027, 3)):
        assert store.metas_margem(ano, mes)[CC_A]["margem_meta"] == 17.0, (ano, mes)


def test_a_meta_NAO_vale_para_tras(limpo):
    """A competência é um piso, não um carimbo retroativo: meta criada em setembro não pode
    reescrever a nota de agosto, que foi calculada quando ela não existia."""
    store.meta_margem_set(CC_A, 17.0, 2026, 9)
    assert CC_A not in store.metas_margem(2026, 8)


def test_mudar_a_meta_de_NOVEMBRO_nao_mexe_na_de_SETEMBRO(limpo):
    """⚠️ O teste central do desenho. Com um campo único de "meta do comprador", subir a meta em
    novembro DERRUBARIA a nota de setembro — e ninguém saberia dizer se a pessoa piorou ou se a
    régua mudou. Mesmo princípio do `meta_ant` do Orçamento: 'a base da meta do mês passado é a
    venda medida NAQUELE fechamento, não a de hoje'."""
    store.meta_margem_set(CC_A, 17.0, 2026, 9)
    store.meta_margem_set(CC_A, 18.0, 2026, 11)
    assert store.metas_margem(2026, 9)[CC_A]["margem_meta"] == 17.0
    assert store.metas_margem(2026, 10)[CC_A]["margem_meta"] == 17.0     # ainda herda setembro
    assert store.metas_margem(2026, 11)[CC_A]["margem_meta"] == 18.0
    assert store.metas_margem(2026, 12)[CC_A]["margem_meta"] == 18.0


def test_regravar_a_mesma_competencia_ATUALIZA_em_vez_de_duplicar(limpo):
    store.meta_margem_set(CC_A, 17.0, 2026, 9)
    store.meta_margem_set(CC_A, 19.5, 2026, 9)
    assert store.metas_margem(2026, 9)[CC_A]["margem_meta"] == 19.5
    linhas = [h for h in store.metas_margem(historico=True) if h["codcomprador"] == CC_A]
    assert len(linhas) == 1, "UNIQUE (codcomprador, ano, mes) não segurou"


def test_apagar_a_meta_deixa_o_comprador_SEM_meta_e_nao_com_zero(limpo):
    """⚠️ Meta 0 dividiria a margem por zero e o comprador cairia em 'não atingiu' — punido por
    um cadastro apagado. Ausência tem de ser ausência."""
    store.meta_margem_set(CC_A, 17.0, 2026, 9)
    store.meta_margem_set(CC_A, None, 2026, 9)
    assert CC_A not in store.metas_margem(2026, 9)


def test_compradores_nao_se_contaminam(limpo):
    store.meta_margem_set(CC_A, 17.0, 2026, 9)
    store.meta_margem_set(CC_B, 22.0, 2026, 9)
    m = store.metas_margem(2026, 9)
    assert (m[CC_A]["margem_meta"], m[CC_B]["margem_meta"]) == (17.0, 22.0)


# ───────────────── auditoria ─────────────────

def test_toda_gravacao_deixa_rastro_no_log(limpo):
    """⚠️ Isto é a régua que avalia uma PESSOA. Sem histórico de quem mudou o quê e quando, uma
    nota contestada não tem como ser explicada — foi a falta desse rastro que deixou a cobertura
    alvo andar 45 → 40 → 30 em cinco semanas sem registro em lugar nenhum."""
    store.meta_margem_set(CC_A, 17.0, 2026, 9)
    store.meta_margem_set(CC_A, 18.0, 2026, 10)
    conn = store.get_db()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT parametros FROM multpel_log "
                        " WHERE rota='estoque:meta_margem' AND parametros LIKE %s",
                        (f"%{CC_A}%",))
            rastro = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()
    assert len(rastro) == 2
    assert any('"margem_meta": 17.0' in r for r in rastro)


# ───────────────── validação ─────────────────

def test_mes_fora_da_faixa_e_recusado(limpo):
    """⚠️ `mes=0` tem de ESTOURAR, não virar o mês corrente. A 1ª versão usava `if mes` (falsy),
    então o zero caía no mês de hoje em silêncio: a meta ia parar numa competência que ninguém
    pediu e o único sintoma seria a nota de um mês qualquer mudando sozinha."""
    for mes in (0, 13, 99, -1):
        with pytest.raises(ValueError):
            store.meta_margem_set(CC_A, 17.0, 2026, mes)
    with pytest.raises(ValueError):
        store.meta_margem_set(CC_A, 17.0, 0, 9)


def test_historico_lista_todas_as_competencias(limpo):
    """A tela do Admin mostra 'vigente desde' — precisa das linhas, não do colapso."""
    store.meta_margem_set(CC_A, 17.0, 2026, 9)
    store.meta_margem_set(CC_A, 18.0, 2026, 11)
    linhas = [h for h in store.metas_margem(historico=True) if h["codcomprador"] == CC_A]
    assert [(h["ano"], h["mes"], h["margem_meta"]) for h in linhas] == \
           [(2026, 11, 18.0), (2026, 9, 17.0)]        # mais recente primeiro
