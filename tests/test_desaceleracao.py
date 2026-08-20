"""Gate da watchlist "Em desaceleração" — 08/2026.

CONTEXTO (a razão de a feature existir com esta forma): o pedido original do diretor foi
*"60 dias sem venda é uma janela muito alta, desce isso para 20 dias"*. Medido no BI real, a
janela 20-59 dias é **100% curva C** (o item de curva A que está há mais tempo sem vender está
há 14 dias; o de B, há 16) e 392 dos 413 itens TÊM giro — eles vendem, só não venderam nas
últimas semanas. Baixar o piso do capital parado dobraria o KPI (R$ 179k → R$ 366k) com rotação
normal de curva C. A saída acordada foi uma lista À PARTE.

O que estes testes travam:
1. **o capital parado NÃO se mexe** — é a razão de a feature existir; se este cair, a decisão
   inteira foi revertida sem querer;
2. **os dois conjuntos são DISJUNTOS**, e continuam disjuntos mesmo com parâmetro maluco (é a
   guarda `eh_parado` na 1ª linha de `em_desaceleracao`);
3. as três condições valem — sem a cobertura a lista vira "todo item C do mês"; sem o piso de
   valor, 41% dela é poeira;
4. o piso de valor é PISO, não "top N" — lista de tamanho fixo nunca melhora, e a série da
   Evolução usa esta métrica para responder "está melhorando?";
5. os parâmetros movem o resultado (senão os campos de ⚙ Parâmetros mentem), e nenhum valor
   de entrada esvazia a lista em silêncio;
6. a série da Evolução usa a MESMA função da tela e recalcula o passado.
"""
import inspect
from datetime import date, timedelta

import pytest

from estoque import core, historico


def _p(cod="1", dsv=30, cob=200, valor=1000.0, qtdisp=10, status_parado=None):
    """Item com as chaves que `em_desaceleracao` lê + o mínimo que o `cockpit()` exige.

    ⚠️ As chaves extras não são enfeite: o `cockpit` acessa vários campos por `p["x"]` (sem
    `.get`), então um fixture curto rebenta com KeyError em vez de testar a métrica."""
    return {"codprod": cod, "dias_sem_venda": dsv, "cobertura_dias": cob,
            "valor": valor, "qtdisp": qtdisp, "status_parado": status_parado,
            # campos exigidos pelo cockpit
            "venda": 0.0, "lucro": 0.0, "giro_dia": 0.05, "cobertura": cob,
            "curva_abc": "C", "abc_xyz": None, "status_ruptura": None,
            "estoque_zero": False, "sugestao_compra": 0, "custo_unit": 1.0,
            "status_abast": None, "compra_suspensa": False}


# ───────────────────────── 1. o capital parado não se mexe ─────────────────────────

def test_a_watchlist_NAO_altera_o_capital_parado():
    """A feature nasceu de um pedido para baixar o piso do parado; ela existe justamente para
    NÃO baixá-lo. Se o capital parado mudar, o acordo foi desfeito em silêncio."""
    produtos = [
        _p("a", dsv=30, cob=200),                              # desacelerando
        _p("b", dsv=70, status_parado="atencao"),              # parado
        _p("c", dsv=200, status_parado="muito_critico"),       # parado
    ]
    antes = sum(p["valor"] for p in produtos if core.eh_parado(p))
    ck = core.cockpit(produtos)
    assert ck["valor_parado"] == pytest.approx(antes)
    assert ck["valor_parado"] == pytest.approx(2000.0)         # só b e c
    assert ck["desaceleracao"]["qt"] == 1                      # só a


def test_status_parado_de_20_dias_continua_NAO_sendo_parado():
    """A régua do dead stock segue em 60 dias. Este é o assert que o pedido original queria
    derrubar — ele fica de pé de propósito."""
    assert core.status_parado_de(20, 10, None, 15) is None
    assert core.status_parado_de(59, 10, None, 15) is None
    assert core.status_parado_de(60, 10, None, 15) == "atencao"


# ───────────────────────── 2. disjunção, o invariante duro ─────────────────────────

def test_item_parado_NUNCA_entra_na_watchlist():
    parado = _p(dsv=200, cob=500, status_parado="muito_critico")
    assert core.eh_parado(parado)
    assert core.em_desaceleracao(parado) is False


def test_disjuncao_resiste_a_parametro_que_invade_a_faixa_do_parado():
    """⚠️ O caso que a guarda `eh_parado` protege: alguém sobe `desacel_ate` para 400 e a janela
    passa a cobrir o dead stock inteiro. Sem a guarda, os dois cards somariam o mesmo item e
    "parado + desacelerando" poderia passar do valor de estoque."""
    pr = core.merge_params({"desacel_ate": 400})
    produtos = [_p("a", dsv=30, cob=200),
                _p("b", dsv=200, cob=500, status_parado="muito_critico")]
    par = {p["codprod"] for p in produtos if core.eh_parado(p)}
    des = {p["codprod"] for p in produtos if core.em_desaceleracao(p, pr)}
    assert par & des == set()
    assert des == {"a"}


# ───────────────────────── 3. as três condições ─────────────────────────

@pytest.mark.parametrize("dsv,esperado", [(19, False), (20, True), (59, True), (60, False),
                                          (None, False)])
def test_janela_de_dias_sem_venda_e_fechada_embaixo_e_aberta_em_cima(dsv, esperado):
    """[de, ate) — `ate` é EXCLUSIVO porque 60 já é o 1º dia do capital parado. Fronteira
    inclusiva dos dois lados criaria um item nos dois cards."""
    assert core.em_desaceleracao(_p(dsv=dsv, cob=200)) is esperado


def test_cobertura_no_limiar_NAO_entra_a_fronteira_e_estrita():
    assert core.em_desaceleracao(_p(cob=90)) is False    # > 90, não >=
    assert core.em_desaceleracao(_p(cob=91)) is True


def test_sem_a_cobertura_a_lista_viraria_todo_item_C_do_mes():
    """Medido no BI: os 72 itens da janela com cobertura ≤45d somavam R$ 9.773. Item de curva C
    com pouco estoque que não vendeu esse mês é o ciclo dele, não um problema."""
    assert core.em_desaceleracao(_p(cob=30, valor=5000)) is False


def test_piso_de_valor_corta_a_poeira():
    assert core.em_desaceleracao(_p(valor=199.99)) is False
    assert core.em_desaceleracao(_p(valor=200.0)) is True     # fronteira inclusiva


def test_item_sem_estoque_fica_fora():
    """Sem saldo não há capital a vigiar — o item é ruptura, e a aba dele é outra."""
    assert core.em_desaceleracao(_p(qtdisp=0)) is False


def test_cobertura_desconhecida_fica_fora_em_vez_de_entrar_por_engano():
    assert core.em_desaceleracao(_p(cob=None)) is False


# ───────────────────────── 4. piso de valor ≠ top N ─────────────────────────

def test_a_lista_ENCOLHE_quando_o_problema_diminui():
    """⚠️ O motivo de ser um PISO DE VALOR e não um "top 50": lista de tamanho fixo devolveria
    50 itens hoje e 50 depois do problema resolvido pela metade. A série da Evolução usa esta
    métrica para responder "está melhorando?" — número constante por construção não responde."""
    muitos = [_p(str(i), valor=1000.0) for i in range(60)]
    poucos = [_p(str(i), valor=1000.0) for i in range(5)]
    assert core.resumo_desaceleracao(muitos)["qt"] == 60      # não trunca em 50
    assert core.resumo_desaceleracao(poucos)["qt"] == 5


def test_a_lista_sai_ordenada_por_valor():
    """A ordem vive no core porque o export lê do mesmo lugar que a tela."""
    produtos = [_p("a", valor=500.0), _p("b", valor=9000.0), _p("c", valor=1500.0)]
    itens = core.resumo_desaceleracao(produtos)["itens"]
    assert [p["codprod"] for p in itens] == ["b", "c", "a"]


# ───────────────────────── 5. parâmetros ─────────────────────────

def test_os_parametros_movem_o_resultado():
    """Se este cair, os campos de ⚙ Parâmetros mudam de valor e não mudam nada na tela — a
    falha silenciosa que o `parado_atencao` teve por meses."""
    produtos = [_p("a", dsv=30, cob=100, valor=1000.0)]
    assert core.resumo_desaceleracao(produtos, core.merge_params({}))["qt"] == 1
    assert core.resumo_desaceleracao(produtos, core.merge_params({"desacel_cob": 150}))["qt"] == 0
    assert core.resumo_desaceleracao(produtos, core.merge_params({"desacel_valor_min": 5000}))["qt"] == 0
    assert core.resumo_desaceleracao(produtos, core.merge_params({"desacel_de": 40}))["qt"] == 0


def test_janela_invertida_e_corrigida_em_vez_de_esvaziar_a_lista_em_silencio():
    """`ate` <= `de` daria uma janela vazia sem erro nenhum — e card vazio se lê como "não há
    problema", nunca como "o parâmetro está quebrado". Mesmo clamp do `novo_dias`."""
    p = core.merge_params({"desacel_de": 50, "desacel_ate": 10})
    assert p["desacel_ate"] > p["desacel_de"]


def test_valores_negativos_nao_passam():
    p = core.merge_params({"desacel_cob": -5, "desacel_valor_min": -100, "desacel_de": 0})
    assert p["desacel_cob"] == 0
    assert p["desacel_valor_min"] == 0
    assert p["desacel_de"] >= 1


def test_cockpit_sem_params_usa_os_defaults():
    """`params` é opcional: torná-lo obrigatório quebraria todo chamador e fixture existente."""
    assert core.cockpit([_p()])["desaceleracao"]["qt"] == 1


# ───────────────────────── 6. a série da Evolução ─────────────────────────

def test_a_serie_usa_a_MESMA_funcao_da_tela():
    """Terceira implementação do mesmo conceito é como a Ruptura divergiu em 3 lugares."""
    assert "em_desaceleracao" in inspect.getsource(historico.agregar)


def test_a_serie_recalcula_o_passado_com_o_parametro_novo():
    """A foto guarda o INGREDIENTE (dias sem venda, cobertura, valor), então a métrica nasce com
    todo o histórico que já existe — e muda inteira quando a régua muda."""
    hoje = date(2026, 8, 20)
    # (data, qtdisp, valor, giro_dia, cobertura_dias, dtultsaida, dtultent, curva_abc)
    linhas = [(hoje - timedelta(days=n), 10, 1000.0, 0.05, 200,
               hoje - timedelta(days=n + 30), hoje - timedelta(days=n + 300), "C", 0, 0)
              for n in range(3)]
    base = historico.agregar(linhas, {})
    assert all(d["valor_desacel"] == pytest.approx(1000.0) for d in base), "todos os dias entram"
    assert all(d["n_desacel"] == 1 for d in base)
    # sobe a cobertura exigida acima da do item: o passado INTEIRO se redesenha
    alto = historico.agregar(linhas, {"desacel_cob": 500})
    assert all(d["valor_desacel"] == 0 for d in alto)


def test_a_serie_nao_conta_o_item_duas_vezes():
    """`valor_parado` e `valor_desacel` são disjuntos também na foto."""
    hoje = date(2026, 8, 20)
    linhas = [(hoje, 10, 1000.0, 0.05, 200, hoje - timedelta(days=200),
               hoje - timedelta(days=300), "C", 0, 0)]   # 200 dias sem venda = parado
    d = historico.agregar(linhas, {})[0]
    assert d["valor_parado"] == pytest.approx(1000.0)
    assert d["valor_desacel"] == 0


def test_o_selo_de_versao_do_rollup_subiu():
    """⚠️ A checagem por CHAVES não vê métrica que muda de SIGNIFICADO. Depender só dela é como
    o `valor_parado` serviu o número velho em silêncio."""
    assert historico._ROLLUP_VERSAO >= 3
    assert "valor_desacel" in historico._ROLLUP_CHAVES
    assert "n_desacel" in historico._ROLLUP_CHAVES


def test_os_params_da_watchlist_invalidam_o_cache_da_serie():
    """Fora do `_PARAMS_DA_SERIE`, mexer nos campos serviria a série cacheada com a régua antiga."""
    for k in ("desacel_de", "desacel_ate", "desacel_cob", "desacel_valor_min"):
        assert k in historico._PARAMS_DA_SERIE


# ───────────────────────── ocupação do WMS na série ─────────────────────────

def test_a_ocupacao_da_serie_e_a_MESMA_fracao_da_aba_ocupacao():
    """⚠️ Fonte única do percentual. `ocupacao_resumo` devolve `pct_ocupado` como FRAÇÃO
    arredondada a 4 casas, e é ela que a aba Ocupação renderiza com o helper `pct()`.

    Recalcular na foto como `ocupadas/posicoes*100` arredondado a 1 casa parecia inofensivo e
    dava OUTRO número: com 4.446 de 5.290 posições a aba mostra **84,1%** (0,8405 pelo Intl,
    half-expand) e a reconta dava **84,0%**. Mesmo dia, mesmo dado, duas telas discordando —
    é o defeito do card "Em risco" (789 SKUs no card × 791 na lista) de novo.
    """
    oc = core.ocupacao_resumo([{"posicoes": 5290, "ocupadas": 4446}], [])
    assert oc["pct_ocupado"] == 0.8405, "a régua da aba mudou; a foto tem de acompanhar"
    # a reconta que NÃO pode voltar a existir
    assert core._round(4446 / 5290 * 100, 1) == 84.0
    # o que a foto guarda é a fração, não a reconta
    fonte = inspect.getsource(historico._ocupacao_do_dia)
    assert 'oc.get("pct_ocupado")' in fonte, "a foto tem de copiar a chave, não refazer a conta"


def test_a_ocupacao_nao_viaja_com_recorte_ativo():
    """O grão é POSIÇÃO do WMS: não se decompõe por comprador/fornecedor/curva/XYZ. Servir o
    número do depósito inteiro ao lado de um gráfico filtrado pela curva A é a falha clássica
    do módulo (a tabela de Verbas de um fornecedor ao lado do gráfico da empresa toda)."""
    dias = [{"data": "2026-08-20", "valor_estoque": 1.0}]
    assert historico._com_estado(dias, "atacado", None, None, sem_recorte=False) is dias
    assert "ocupacao_pct" not in dias[0]


def test_a_ocupacao_e_dado_PRIMARIO_e_nao_mora_no_rollup():
    """⚠️ O rollup (`estoque_foto_dia`) é CACHE: `rebuild_rollup` o joga fora e refaz do cru.
    A ocupação não se refaz — a posição do WMS de ontem não existe no Winthor. Se ela migrar
    para o rollup, um rebuild de rotina apaga histórico irrecuperável, e sem erro nenhum."""
    assert "ocupacao" not in historico._ROLLUP_CHAVES
    assert "estoque_foto_estado" in inspect.getsource(historico.gravar_estado)
    assert "estoque_foto_estado" in inspect.getsource(historico._estado_por_dia)


# ───────────────────────── vencidos: o EVENTO que não se fotografa ─────────────────────────

def test_vencido_NAO_entra_na_foto_porque_e_evento_datado():
    """⚠️ O par vencido × validade é o critério inteiro da aba de estado.

    Baixa por validade é lançamento contábil DATADO (conta 200042): fica no livro e continua lá
    para sempre, então já existe mês a mês desde antes de a foto existir. Fotografá-lo criaria
    uma segunda cópia de um dado que a contabilidade já tem — e no dia em que as duas
    divergissem, a errada seria a nossa.

    Já a VALIDADE (quanto está *a vencer*) é saldo de lote: sobrescrito, e ninguém consegue
    dizer depois quanto estava vencendo numa data passada. Essa é fotografada.

    Pedido do diretor 08/2026, que corrigiu "validade" por "vencido" na lista do que salvar —
    e é justamente a troca que tiraria a única das duas que precisa ser salva.
    """
    assert "validade" in historico._ESTADO_DO_DIA, "a que SOME tem de ser fotografada"
    assert "vencido" not in historico._ESTADO_DO_DIA, "a que FICA no livro não se fotografa"
    assert "vencidos" not in historico._ESTADO_DO_DIA


def test_vencido_sai_ausente_quando_curva_ou_xyz_estao_ativos():
    """Curva e XYZ viriam do cadastro de HOJE e reclassificariam baixas antigas com a régua de
    agora — o oposto do que a foto faz ao gravar a curva no dia. Ausente (a tela mostra "—") é
    melhor que um número que ignora o recorte em silêncio, que é a falha clássica do módulo."""
    from estoque.routes import _juntar_vencidos
    for recorte in ({"curva": "A"}, {"xyz": "X"}):
        dias = [{"data": "2026-08-20"}]
        _juntar_vencidos(dias, ["3", "5"], **recorte)
        assert "vencido_dia" not in dias[0], f"{list(recorte)[0]} não pode ser ignorado em silêncio"


def test_dia_sem_baixa_recebe_ZERO_e_nao_None(monkeypatch):
    """⚠️ Zero e "não medido" são coisas diferentes e não podem virar o mesmo símbolo na tela.

    Aqui zero é MEDIÇÃO ("não perdemos nada nesse dia") — e é o que a aba quer poder mostrar.
    `None` é o que as colunas de ESTADO usam quando a foto daquele dia não saiu. Confundir os
    dois faria "não perdemos nada" parecer buraco de medição, e vice-versa."""
    from estoque import pbi, routes
    monkeypatch.setattr(pbi._CACHE, "get",
                        lambda k: {"dia": {"2026-08-19": 1105.14}, "mes": {"2026-08": 6272.68}})
    dias = [{"data": "2026-08-19"}, {"data": "2026-08-20"}]
    routes._juntar_vencidos(dias, ["3", "5"])
    assert dias[0]["vencido_dia"] == 1105.14
    assert dias[1]["vencido_dia"] == 0.0 and dias[1]["vencido_dia"] is not None
    # o acumulado do mês acompanha os dois dias (mesma competência)
    assert dias[0]["vencido_mes"] == dias[1]["vencido_mes"] == 6272.68
