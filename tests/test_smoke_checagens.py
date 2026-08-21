"""Testes das CHECAGENS da bateria — a bateria também erra, e já errou duas vezes.

⚠️ **Por que este arquivo existe.** O `smoke_ia_real.py` é quem diz se o agente regrediu. Mas as
asserções dele já produziram sinal falso duas vezes, nas duas direções possíveis:

1. **Falso negativo permanente** — o `\\b` da regex do código do produto foi gravado como byte
   0x08 (backspace). A checagem nunca casava: as perguntas 4/5/6/12 reprovavam SEMPRE, com ou sem
   código na resposta. A regra de auditoria do diretor nunca chegou a ser verificada.
2. **Falso positivo** — a marca de recusa contava em qualquer posição do texto, então uma
   resposta que entregou a matriz ABC×XYZ inteira e ressalvou no fim ("o painel não fornece o
   total exato") foi reprovada por OBEDECER a regra 2 do prompt. E a exigência de código
   reprovou uma resposta correta de "não há espaço morto", que não tem item para citar.

Bateria que reprova resposta boa treina quem a roda a ignorar o relatório — o mesmo defeito, de
novo. Os casos abaixo são respostas REAIS, copiadas das rodadas contra o Power BI e contra o
Postgres da demo.
"""
import pathlib
import sys

import pytest

# o `conftest` põe a RAIZ no path, não o `tests/` — e a bateria mora aqui
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import smoke_ia_real as S  # noqa: E402


# ── respostas reais ────────────────────────────────────────────────────────────────────────
ABC_XYZ_COM_RESSALVA = (
    "No recorte da unidade atacado em 2026-07-24, a matriz ABC x XYZ apresenta o seguinte:\n"
    "- A curva A tem 1.614 SKUs (868 AX + 154 AY + 18 AZ + 574 outros), com estoque relevante "
    "em AX (R$ 1.294.685,32) e vendas altas.\n"
    "- A curva B tem 904 SKUs (536 BX + 291 BY + 77 BZ).\n"
    "Rupturas estão distribuídas principalmente em C (91 SKUs), B (37) e A (28). O painel não "
    "fornece o total exato de SKUs por classe XYZ fora dos dados listados.")

SEM_ESPACO_MORTO = (
    "No recorte da unidade atacado, não há espaço morto no armazém. O painel indica **0 SKUs**, "
    "**0 posições** e **R$ 0,00** de espaço morto.")

RECUSA_PURA = "Não tenho essa informação no painel. Consulte a aba do produto."

ITEM_COM_CODIGO = (
    "O item com maior valor parado é o código **69398 FILME ULTRAPLAST 38X1000X9**, com "
    "**R$ 118.506,19** em estoque parado.")

ITEM_SEM_CODIGO = ("O item com maior valor parado é o filme ultraplast, com R$ 118,5 mil "
                   "parados em estoque.")


# ── recusa: distinguir RECUSAR de RESSALVAR ────────────────────────────────────────────────
def test_ressalva_no_fim_de_resposta_completa_NAO_e_recusa():
    """⚠️ O falso positivo real do modo Postgres. A resposta entrega a matriz inteira e termina
    dizendo o que não tem — que é a regra 2 do prompt ("diga o que você tem e o que falta")."""
    assert not S.eh_recusa(ABC_XYZ_COM_RESSALVA)


def test_recusa_PURA_continua_sendo_pega():
    """A regressão nº 1 (8 de 9 perguntas respondidas com "consulte a aba do produto"). Se este
    teste cair, afrouxar a checagem cegou a bateria."""
    assert S.eh_recusa(RECUSA_PURA)


def test_recusa_sem_marca_nenhuma_nao_e_recusa():
    assert not S.eh_recusa(ITEM_COM_CODIGO)


@pytest.mark.parametrize("txt", [
    "Não tenho esse dado.",
    "Não consigo analisar isso aqui.",
    "Consulte o sistema específico de controle de validade.",
])
def test_variantes_de_recusa_curta_sao_pegas(txt):
    assert S.eh_recusa(txt)


def test_o_criterio_e_a_AUSENCIA_de_dado_e_nao_o_tamanho():
    """A separação é "traz número ou não", não "é longo ou não" — uma recusa prolixa continua
    sendo recusa."""
    prolixa = ("Infelizmente não tenho essa informação disponível neste painel. " * 6)
    assert S.eh_recusa(prolixa)


# ── código do produto: exigir só quando há item ────────────────────────────────────────────
def test_resposta_de_lista_VAZIA_nao_precisa_citar_codigo():
    """⚠️ O outro falso positivo do modo Postgres: a base sintética não tem espaço morto, a
    resposta correta é "não há", e a checagem exigia um código de produto inexistente."""
    assert not S.exige_codigo(SEM_ESPACO_MORTO)


def test_resposta_que_CITA_item_continua_obrigada_ao_codigo():
    assert S.exige_codigo(ITEM_SEM_CODIGO)
    assert S.exige_codigo(ITEM_COM_CODIGO)


def test_a_regex_do_codigo_discrimina():
    """A regex que passou a existir de fato depois do byte 0x08. Sem estes dois casos, um `\\b`
    corrompido de novo passaria despercebido."""
    import re
    rx = re.compile(r"\b\d{4,6}\b")
    assert rx.search(ITEM_COM_CODIGO), "código de 5 dígitos tem de casar"
    assert not rx.search(ITEM_SEM_CODIGO), "valor em R$ não pode passar por código"


def test_a_checagem_de_codigo_nao_tem_byte_de_controle():
    """Gate direto contra a reincidência do bug que quebrou esta verificação por meses."""
    import io
    fonte = io.open(S.__file__, encoding="utf-8").read()
    maus = [hex(ord(c)) for c in fonte if ord(c) < 32 and c not in "\n\r\t"]
    assert not maus, f"byte(s) de controle no arquivo da bateria: {set(maus)}"
