"""Gate do MÊS ANTERIOR no Orçamento de compras (pergunta do diretor, 08/2026).

    "Quando vira o mês, o orçamento do comprador zera... se ele estourou o orçamento do mês
     passado, ele não deveria arrastar o valor que ele estourou para diminuir do orçamento do
     mês atual dele?"

Decisões que estes testes travam:
  · o mês fechado é APURADO SEMPRE e devolvido no resumo — era ele ser invisível o problema
    imediato; descontá-lo da meta é outra pergunta, e é opção;
  · `arrastar` é OPT-IN. A meta é `pct × venda dos últimos 30 dias`: régua de FLUXO, não budget
    anual. Ligado por default, puniria duas vezes quem estourou porque a venda subiu;
  · SOBRA não vira crédito — só o estouro viaja. Creditar sobra deixaria acumular um mês fraco
    inteiro para estourar o seguinte;
  · a base da meta do mês passado é a venda de 30d medida NAQUELE fechamento, não a de hoje —
    reconstruir com a venda atual daria um estouro contra uma meta que nunca existiu;
  · o arraste NÃO cascateia: se o estouro passar da meta do mês, o excedente morre ali (senão a
    régua de fluxo vira budget anual pela porta dos fundos).

⚠️ Até 08/2026 o Orçamento não tinha NENHUM teste — as mudanças aqui não tinham rede.
"""
from datetime import date

from estoque import core

HOJE = date(2026, 8, 3)
MES = "2026-08"
MES_ANT = "2026-07"
COMP = {7: "JOAO"}
FORN = {1: {"FORNECEDOR": "FORN", "CGC": "11.111.111/0001-11"}}


def _ped(numped, data, valor, entregue=None):
    return {"NUMPED": numped, "DTEMISSAO": data, "CODFORNEC": 1, "CODCOMPRADOR": 7,
            "VLTOTAL": valor, "VLENTREGUE": valor if entregue is None else entregue,
            "DTPREVENT": None}


# venda líq. 30d por comprador → meta = 65%
VENDA_HOJE = {"JOAO": 100_000.0}      # meta do mês corrente = 65.000
VENDA_FECHAMENTO = {"JOAO": 80_000.0}  # meta que valia no fim de julho = 52.000


def _orc(cab, arrastar=False, venda_ant=VENDA_FECHAMENTO):
    return core.orcamento_winthor(cab, VENDA_HOJE, COMP, FORN, MES, "TODOS",
                                  pct=0.65, hoje=HOJE, venda_comp_ant=venda_ant,
                                  arrastar=arrastar)


def test_mes_anterior_e_apurado_mesmo_com_o_arraste_desligado():
    """O pedido imediato: o número tem de APARECER. Com o arraste desligado ele não mexe na
    meta, mas o diretor passa a ver que julho fechou estourado."""
    cab = [_ped(1, "2026-07-10", 70_000.0),      # julho: 70.000 contra meta de 52.000
           _ped(2, "2026-08-01", 10_000.0)]
    r = _orc(cab)["resumo"]
    assert r["mes_ant"] == MES_ANT
    assert r["meta_ant"] == 52_000.0
    assert r["comprado_ant"] == 70_000.0
    assert r["saldo_ant"] == -18_000.0            # estourou 18k
    assert r["meta"] == 65_000.0, "sem arrastar, a meta do mês não muda"
    assert r["arrasto_aplicado"] == 0.0


def test_arraste_desconta_o_estouro_da_meta_do_mes():
    cab = [_ped(1, "2026-07-10", 70_000.0), _ped(2, "2026-08-01", 10_000.0)]
    r = _orc(cab, arrastar=True)["resumo"]
    assert r["meta_base"] == 65_000.0
    assert r["arrasto_aplicado"] == -18_000.0
    assert r["meta"] == 47_000.0                  # 65.000 − 18.000
    assert r["saldo"] == 37_000.0                 # 47.000 − 10.000 comprado em agosto


def test_sobra_do_mes_passado_NAO_vira_credito():
    """Assimetria proposital: só o estouro viaja. Creditar a sobra deixaria guardar um mês fraco
    inteiro para estourar o seguinte — o oposto do que uma régua de fluxo mede."""
    cab = [_ped(1, "2026-07-10", 20_000.0)]       # julho: sobrou 32.000
    r = _orc(cab, arrastar=True)["resumo"]
    assert r["saldo_ant"] == 32_000.0
    assert r["arrasto_aplicado"] == 0.0
    assert r["meta"] == 65_000.0, "sobra não aumenta a meta do mês seguinte"


def test_estouro_maior_que_a_meta_zera_mas_nao_cascateia():
    """Meta negativa faria o % consumido explodir e o card mentir de outro jeito. E o excedente
    morre no mês: arrastar em cascata viraria budget anual sem ninguém ter decidido isso."""
    cab = [_ped(1, "2026-07-10", 200_000.0)]      # estourou 148.000, mais que a meta de agosto
    r = _orc(cab, arrastar=True)["resumo"]
    assert r["saldo_ant"] == -148_000.0
    assert r["meta"] == 0.0
    assert r["pct_consumido"] is None             # sem meta não há % — não inventa número


def test_sem_a_venda_do_fechamento_o_bloco_sai_VAZIO_e_nao_errado():
    """Se a venda de 30d do fechamento não vier, não se reconstrói a meta de julho com a venda
    de hoje: sairia um "estouro" contra uma meta que nunca existiu. Melhor não responder."""
    cab = [_ped(1, "2026-07-10", 70_000.0)]
    r = _orc(cab, arrastar=True, venda_ant=None)["resumo"]
    assert r["meta_ant"] is None
    assert r["saldo_ant"] is None
    assert r["comprado_ant"] == 70_000.0, "o comprado é fato — esse continua sendo informado"
    assert r["meta"] == 65_000.0, "sem base, o arraste não desconta nada"


def test_o_mes_corrente_nao_muda_em_nada_sem_os_parametros_novos():
    """Rede de segurança da produção: chamada antiga (sem venda_comp_ant/arrastar) tem de sair
    idêntica ao que saía antes desta funcionalidade existir."""
    cab = [_ped(1, "2026-07-10", 70_000.0), _ped(2, "2026-08-01", 10_000.0)]
    r = core.orcamento_winthor(cab, VENDA_HOJE, COMP, FORN, MES, "TODOS",
                               pct=0.65, hoje=HOJE)["resumo"]
    assert r["meta"] == 65_000.0
    assert r["comprado"] == 10_000.0
    assert r["saldo"] == 55_000.0
    assert r["meta_ant"] is None and r["arrastar"] is False


def test_quebra_por_comprador_carrega_o_mes_anterior():
    cab = [_ped(1, "2026-07-10", 70_000.0), _ped(2, "2026-08-01", 10_000.0)]
    linha = _orc(cab, arrastar=True)["por_comprador"][0]
    assert linha["comprador"] == "JOAO"
    assert linha["meta_ant"] == 52_000.0 and linha["comprado_ant"] == 70_000.0
    assert linha["saldo_ant"] == -18_000.0
    assert linha["meta_base"] == 65_000.0 and linha["meta"] == 47_000.0


def test_comprador_que_so_estourou_no_mes_passado_ainda_aparece():
    """Quem estourou julho e não comprou nada em agosto não pode sumir da tabela: é justamente
    quem o arraste afeta."""
    cab = [_ped(1, "2026-07-10", 70_000.0)]
    linhas = core.orcamento_winthor(cab, {}, COMP, FORN, MES, "TODOS", pct=0.65, hoje=HOJE,
                                    venda_comp_ant=VENDA_FECHAMENTO,
                                    arrastar=True)["por_comprador"]
    assert [l["comprador"] for l in linhas] == ["JOAO"]
    assert linhas[0]["saldo_ant"] == -18_000.0


def test_transferencia_entre_filiais_fica_fora_do_mes_anterior_tambem():
    """A regra do CNPJ raiz vale para os dois meses — senão o "estouro" de julho incluiria
    transferência, que nunca foi compra."""
    forn = {1: {"FORNECEDOR": "FORN", "CGC": "11.111.111/0001-11"},
            2: {"FORNECEDOR": "FILIAL", "CGC": "02.262.785/0002-95"}}
    cab = [_ped(1, "2026-07-10", 40_000.0),
           dict(_ped(2, "2026-07-15", 30_000.0), CODFORNEC=2)]
    r = core.orcamento_winthor(cab, VENDA_HOJE, COMP, forn, MES, "TODOS", pct=0.65, hoje=HOJE,
                               cnpj_empresa="02.262.785/0001-04",
                               venda_comp_ant=VENDA_FECHAMENTO)["resumo"]
    assert r["comprado_ant"] == 40_000.0, "os 30k de transferência não são compra"
    assert r["saldo_ant"] == 12_000.0    # 52.000 − 40.000: julho fechou dentro


def test_mes_anterior_atravessa_a_virada_do_ano():
    assert core.mes_anterior("2026-01") == "2025-12"
    assert core.mes_anterior("2026-08") == "2026-07"
    assert core.mes_anterior("lixo") is None
