"""Gate da pesquisa de preço em campo (08/2026).

**O que a aba mede** (diretor): o comprador vai a atacados CONCORRENTES ver por quanto ELES
vendem o mesmo item — "para saber se o meu preço de venda está dentro da prática usada no
mercado em geral ou não; meu preço está abaixo, igual ou acima". Faz parte da compra porque a
margem se calcula sobre o custo: custo alto entra, preço de venda sai fora da praça.

⚠️ **A 1ª versão comparava com o nosso CUSTO (`CUSTOFIN`) — pedido lido errado.** O estrago era
duplo e silencioso:

1. como o custo é sempre MENOR que o preço de venda, o gap saía enviesado para o VERDE: a tela
   dizia "estamos bem" mesmo quando vendíamos acima do mercado. Não é imprecisão, é inversão da
   conclusão — e na direção que não gera ação;
2. o Excel/PDF que vai ao FORNECEDOR levava o nosso custo de aquisição na coluna "Nosso preço".

O que estes testes travam é a régua nova e as armadilhas que sobreviveram à troca.
"""
import inspect

import pytest

from estoque import core


# ───────────────── unidade (a régua que continua valendo) ─────────────────

def test_preco_por_unidade_passa_direto():
    r = core.normaliza_pesquisa(10.0, "un")
    assert r["comparavel"] is True and r["preco_un"] == 10.0


def test_preco_por_caixa_vira_preco_por_unidade():
    """Quem pesquisa lê a etiqueta da CAIXA e o nosso preço é por unidade. R$ 120 a caixa de 12
    = R$ 10/un; sem esta divisão o gap sairia 12x errado. Mesma família do pedido que saía ~50x
    errado por converter quantidade sem converter preço (`core.item_master`)."""
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


# ───────────────── imposto: a régua que MORREU ─────────────────

def test_a_normalizacao_NAO_desconta_imposto():
    """⚠️ Este é o teste que impede a volta do bug.

    A função descontava IPI/ST para levar o preço a MERCADORIA, porque comparava com o
    `CUSTOFIN`. Com a referência no preço de VENDA os dois lados são cheios — a gôndola do
    concorrente e o nosso realizado ("o nosso preço de venda é com imposto", diretor).
    Reintroduzir o desconto faria a tela dizer que estamos baratos justamente onde estamos
    caros: um preço de venda dividido por 1,15 vira artificialmente competitivo.
    """
    assert core.normaliza_pesquisa(11.5, "un")["preco_un"] == 11.5
    fonte = inspect.getsource(core.normaliza_pesquisa)
    assert "perc_ipi" not in fonte.split('"""')[-1], \
        "o parâmetro morreu junto com a conversão; deixá-lo convida a replugá-lo"


def test_com_imposto_nao_e_mais_parametro_da_conta():
    """A coluna segue GRAVADA na medição (histórico não se regenera e as linhas antigas precisam
    dizer sob que régua entraram), mas não é mais entrada do cálculo. Passá-la tem de estourar,
    não ser aceita e ignorada em silêncio."""
    with pytest.raises(TypeError):
        core.normaliza_pesquisa(11.5, "un", com_imposto=True)


def test_a_caixa_continua_convertendo_sem_o_imposto_no_caminho():
    """A conversão que sobrou tem de continuar exata: caixa de 12 a R$ 138 = R$ 11,50/un."""
    assert core.normaliza_pesquisa(138.0, "cx", qtunitcx=12)["preco_un"] == 11.5


# ───────────────── preço inválido ─────────────────

@pytest.mark.parametrize("p", [0, -5, None, ""])
def test_preco_nao_positivo_e_recusado(p):
    r = core.normaliza_pesquisa(p, "un")
    assert r["comparavel"] is False and r["motivo"] == "preco_invalido"


# ───────────────── gap contra o NOSSO PREÇO DE VENDA ─────────────────

def test_gap_negativo_quando_o_concorrente_vende_mais_barato():
    """O caso que pede ação: eles vendem a 9,00 e nós a 10,00 — o NOSSO está caro.

    ⚠️ A cor é pela perspectiva de quem compra: negativo = VERMELHO. A convenção não mudou
    quando a referência passou de custo para preço de venda, porque o sinal continua
    significando a mesma coisa — e a 1ª versão da tela saiu com ela invertida."""
    g = core.gap_pesquisa(9.0, 10.0)
    assert g["delta"] == -1.0 and g["delta_pct"] == -10.0


def test_gap_positivo_quando_o_nosso_preco_esta_abaixo_do_mercado():
    g = core.gap_pesquisa(12.0, 10.0)
    assert g["delta"] == 2.0 and g["delta_pct"] == 20.0


@pytest.mark.parametrize("a,b", [(0, 10), (10, 0), (None, 10), (10, None)])
def test_gap_sem_um_dos_lados_nao_inventa_comparacao(a, b):
    g = core.gap_pesquisa(a, b)
    assert g["delta"] is None and g["delta_pct"] is None


# ───────────────── a referência (gate de código) ─────────────────

def test_a_comparacao_nao_volta_a_usar_o_custo():
    """⚠️ O gate central desta correção. `_pesquisa_enriquecida` é a fonte única da tela de
    campo, do drawer 360° e dos exports — se ela voltar ao `custofin`, os três voltam juntos,
    e o documento que vai ao fornecedor volta a expor o nosso custo de aquisição."""
    from estoque import routes
    fonte = inspect.getsource(routes._pesquisa_enriquecida)
    # só o CORPO: o docstring cita o `custofin` justamente para explicar por que ele saiu daqui,
    # e sem este recorte o gate reprovaria a própria correção
    corpo = fonte.split('"""')[-1]
    assert "_preco_venda_map" in corpo, "a referência é o preço de venda realizado (3m)"
    assert "custofin" not in corpo, "custo de compra não é a régua desta tela"
    assert '"preco_venda_unit"' in corpo


def test_nao_usa_o_preco_venda_do_produto_por_causa_do_fallback_no_custo():
    """⚠️ `core.construir_produtos` faz `preco_venda = mapa.get(cod) or custofin` — o fallback
    existe para a VENDA PERDIDA e é correto lá. Aqui ele devolveria o CUSTO rotulado como
    "nosso preço de venda": o vazamento de volta, agora com etiqueta errada. Sem preço
    realizado, a coluna sai vazia."""
    from estoque import routes
    fonte = inspect.getsource(routes._pesquisa_enriquecida)
    assert 'p["preco_venda"]' not in fonte and "p.get(\"preco_venda\")" not in fonte
    assert "or None" in fonte, "sem preço realizado a coluna fica vazia, não cai em outra régua"
