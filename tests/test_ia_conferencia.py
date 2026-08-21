"""Conferência de saída — a rede que confere número citado contra o contexto.

⚠️ **Estes testes seguem a regra do gate visto VERMELHO.** Três gates deste agente passavam sem
testar nada (a checagem de código da bateria, quebrada por um byte de controle; o teste de
sugestões com `parametrize` ignorado; e o prompt afirmando duas coisas falsas sobre o próprio
código). Gate que nasce verde não provou nada — provou só que passa.

Por isso a bateria abaixo é montada ao contrário: cada caso PROIBIDO é uma regressão que já
aconteceu de verdade neste agente, com os números reais dela. Se a conferência parar de pegá-las,
estes testes ficam vermelhos.
"""
import pytest

from estoque import ia_conferencia as C


# Contexto mínimo, com os números REAIS medidos no BI em 20/08/2026.
CTX = """
--- PLACAR DO ESTOQUE ---
Valor em estoque: R$ 6.223.410,55
Capital parado: R$ 179.395,32
Capital parado (%): 2,9%

--- ORÇAMENTO DO MÊS ---
Meta de compra do mês: R$ 2.100.000,00
Comprado no mês (Winthor): R$ 1.389.812,23
Pedidos em aberto do mês: R$ 200.000,00
Saldo da meta: R$ 710.187,77
Meta consumida (%): 66,2%

=== PILAR: ESTOQUE PARADO ===
  69398 FILME ULTRAPLAST 38X1000X9 [ULTRAPLAST IND. E COM. DE SA] -> R$ 118.506,19 · cob. 191
  57417 COPO PLAST.TRANSP.200ML TOTALPLAST+ [GRUPO TOTAL BRASIL] -> R$ 51.248,90 · cob. 283
"""


# ── o que TEM de passar: respostas reais, do jeito que o modelo escreve ────────────────────

def test_resposta_REAL_da_bateria_nao_gera_alarme_falso():
    """Resposta literal da pergunta 4 da bateria de hoje. Se a conferência acusar aqui, ela é
    ruído — e ruído treina quem lê a ignorar, que é o defeito da checagem quebrada da bateria."""
    r = ("No recorte da unidade atacado, o item com maior valor parado é o código "
         "**69398 FILME ULTRAPLAST 38X1000X9 [ULTRAPLAST IND. E COM. DE SA]**, com "
         "**R$ 118.506,19** em estoque parado.")
    conf = C.conferir(r, CTX)
    assert conf["ok"], conf
    assert C.resumo(conf) is None


def test_arredondamento_em_prosa_NAO_e_acusado():
    """⚠️ O motivo de a conferência não bloquear: o contexto diz R$ 118.506,19 e o modelo escreve
    'R$ 118,5 mil'. Casamento ingênuo derrubaria resposta boa."""
    for r in ["o item vale cerca de R$ 118,5 mil",
              "aproximadamente R$ 118.506,00 parados",
              "o capital parado é de R$ 179,4 mil"]:
        conf = C.conferir(r, CTX)
        assert conf["ok"], f"{r!r} -> {conf}"


def test_ANO_nao_e_confundido_com_codigo_de_produto():
    """'Qual foi o faturamento em 2019?' é a pergunta de controle da bateria: o modelo repete o
    ano ao recusar, e 2019 tem 4 dígitos."""
    conf = C.conferir("Não tenho o faturamento de 2019 nem de 2026 neste painel.", CTX)
    assert conf["cod"] == [], conf


def test_citar_codigo_que_ESTA_no_contexto_passa():
    conf = C.conferir("Veja os códigos 69398 e 57417.", CTX)
    assert conf["ok"], conf


# ── o que TEM de ser pego: as duas regressões que já aconteceram ───────────────────────────

def test_REGRESSAO_1_somar_dois_numeros_do_contexto():
    """⚠️ A regressão real: o modelo somou `comprado_mes` (R$ 1.389.812,23) com `aberto_mes`
    (R$ 200.000,00) — que está DENTRO do primeiro — e anunciou o total. R$ 1.589.812,23 não
    existe em lugar nenhum do contexto, e é isso que a conferência enxerga.

    A regra 1b proíbe em prosa; aqui a proibição vira verificável."""
    r = "Somando o comprado com os pedidos em aberto, já comprometemos R$ 1.589.812,23 da meta."
    conf = C.conferir(r, CTX)
    assert not conf["ok"], "a soma inventada passou batido"
    assert 1589812.23 in conf["brl"], conf
    assert "R$" in C.resumo(conf)


def test_REGRESSAO_2_inverter_o_sinal_de_um_saldo_positivo():
    """⚠️ A outra regressão real: o saldo era POSITIVO (R$ 710.187,77 sobrando) e o modelo
    concluiu 'estourado', citando um valor negativo que ninguém lhe deu."""
    r = "O orçamento está estourado em R$ 679.812,23 acima da meta."
    conf = C.conferir(r, CTX)
    assert not conf["ok"], "o valor invertido passou batido"
    assert 679812.23 in conf["brl"], conf


def test_codigo_de_produto_INVENTADO_e_pego():
    """Sem isto, a regra do diretor ('sempre trazer o cod') protege contra omissão mas não contra
    um código errado — e um código errado é PIOR que nenhum: manda a pessoa conferir o item
    errado no ERP e o número volta 'confirmado'."""
    conf = C.conferir("O maior ofensor é o código 88888 CAIXA GENERICA, com R$ 118.506,19.", CTX)
    assert not conf["ok"]
    assert conf["cod"] == [88888], conf


def test_percentual_inventado_e_pego():
    conf = C.conferir("O capital parado representa 47,3% do estoque.", CTX)
    assert not conf["ok"]
    assert 47.3 in conf["pct"], conf


@pytest.mark.parametrize("valor", [118506.19, 179395.32, 710187.77, 6223410.55])
def test_todo_valor_do_proprio_contexto_ancora_em_si_mesmo(valor):
    """Invariante de sanidade: se um valor do contexto não ancorasse contra o próprio contexto, a
    conferência acusaria toda resposta correta."""
    conf = C.conferir(f"o valor e de R$ {valor:,.2f}".replace(",", "@")
                      .replace(".", ",").replace("@", "."), CTX)
    assert conf["ok"], conf


def test_a_conferencia_e_PURA():
    """Sem I/O e sem estado: é o que permite re-rodá-la depois sobre o histórico já gravado, sem
    tocar em banco nenhum (mesma razão da pureza do `historico.agregar`)."""
    import inspect
    fonte = inspect.getsource(C)
    for proibido in ("import os", "import psycopg2", "requests", "open("):
        assert proibido not in fonte, f"a conferência deixou de ser pura: {proibido}"
