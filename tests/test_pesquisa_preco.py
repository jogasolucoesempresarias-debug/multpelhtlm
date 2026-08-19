"""Gate da pesquisa de preço em campo (08/2026).

Pedido do diretor: preencher o preço pesquisado direto no item, durante a visita. É a primeira
feature em que a ferramenta vira FONTE de um dado que o Winthor não tem.

O que estes testes travam são as **duas armadilhas que decidem se o dado presta** — e as duas
já custaram caro neste módulo antes:

1. **Unidade.** Quem pesquisa lê a etiqueta da CAIXA. Comparar "R$ 120 a caixa" com um custo
   unitário erra pelo fator inteiro. É a mesma família do pedido que saía ~50x errado por
   converter quantidade sem converter preço (`core.item_master`).
2. **Imposto.** `CUSTOFIN` é MERCADORIA; preço de gôndola tem tributo dentro. Comparar os dois
   direto não significa nada — é a "duas réguas" (mercadoria × NF) do Orçamento de novo.

E a terceira, que é de postura: **sem fator de caixa não se chuta**. Devolve `comparavel=False`
e a tela mostra "—". Número errado que parece plausível é pior que célula vazia (mesma política
do `medidas_confiaveis` e do crescimento AA da aba Fornecedores).
"""
import pytest

from estoque import core


# ───────────────── unidade ─────────────────

def test_preco_por_unidade_passa_direto():
    r = core.normaliza_pesquisa(10.0, "un")
    assert r["comparavel"] is True and r["preco_un"] == 10.0


def test_preco_por_caixa_vira_preco_por_unidade():
    """R$ 120 a caixa de 12 = R$ 10/un. Sem esta divisão o gap sairia 12x errado."""
    r = core.normaliza_pesquisa(120.0, "cx", qtunitcx=12)
    assert r["preco_un"] == 10.0


def test_caixa_SEM_fator_nao_chuta_numero():
    """O caso perigoso: dá para dividir por 1 e devolver 120 como se fosse o preço unitário."""
    r = core.normaliza_pesquisa(120.0, "cx")
    assert r["comparavel"] is False
    assert r["preco_un"] is None
    assert r["motivo"] == "sem_fator_caixa"


@pytest.mark.parametrize("fator", [0, 1, None])
def test_fator_1_ou_ausente_conta_como_sem_fator(fator):
    """`qtunitcx = 1` é cadastro incompleto, não caixa de uma unidade — mesma leitura que o
    `core.construir_produtos` faz (`caixa > 1`)."""
    assert core.normaliza_pesquisa(50.0, "cx", qtunitcx=fator)["comparavel"] is False


# ───────────────── imposto ─────────────────

def test_preco_com_imposto_volta_para_mercadoria():
    """11,50 com 15% de IPI = 10,00 de mercadoria — a régua do CUSTOFIN."""
    r = core.normaliza_pesquisa(11.5, "un", com_imposto=True, perc_ipi=15)
    assert r["preco_un"] == 10.0


def test_imposto_soma_ipi_e_st():
    r = core.normaliza_pesquisa(12.0, "un", com_imposto=True, perc_ipi=10, perc_st=10)
    assert r["preco_un"] == 10.0


def test_sem_imposto_nao_mexe_no_valor():
    assert core.normaliza_pesquisa(11.5, "un", com_imposto=False, perc_ipi=15)["preco_un"] == 11.5


def test_as_duas_conversoes_juntas():
    """Caixa de 12 a R$ 138 com 15% de imposto → R$ 10/un mercadoria."""
    r = core.normaliza_pesquisa(138.0, "cx", com_imposto=True, qtunitcx=12, perc_ipi=15)
    assert r["preco_un"] == 10.0


# ───────────────── preço inválido ─────────────────

@pytest.mark.parametrize("p", [0, -5, None, ""])
def test_preco_nao_positivo_e_recusado(p):
    r = core.normaliza_pesquisa(p, "un")
    assert r["comparavel"] is False and r["motivo"] == "preco_invalido"


# ───────────────── gap contra o nosso custo ─────────────────

def test_gap_negativo_quando_achamos_mais_barato():
    """É a linha que vira argumento de negociação."""
    g = core.gap_pesquisa(9.0, 10.0)
    assert g["delta"] == -1.0 and g["delta_pct"] == -10.0


def test_gap_positivo_quando_o_nosso_custo_ja_e_melhor():
    g = core.gap_pesquisa(12.0, 10.0)
    assert g["delta"] == 2.0 and g["delta_pct"] == 20.0


@pytest.mark.parametrize("a,b", [(0, 10), (10, 0), (None, 10), (10, None)])
def test_gap_sem_um_dos_lados_nao_inventa_comparacao(a, b):
    g = core.gap_pesquisa(a, b)
    assert g["delta"] is None and g["delta_pct"] is None
