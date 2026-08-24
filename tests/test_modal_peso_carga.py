"""Gate do peso/cubagem VIVOS no rodapé do modal "Gerar pedido" (08/2026, pedido do diretor:
*"consegue colocar para aparecer o kg aqui, pois quando vamos alterando o pedido, não aparece o
peso total mais"*).

O peso já existia — no cabeçalho do fornecedor da Abastecimento (`gr.peso`), calculado sobre a
SUGESTÃO e congelado. Ele morria exatamente ao abrir o modal, que é onde a quantidade passa a
mudar. Agora o rodapé soma a cada tecla, ao lado de "Mercadoria / impostos / Total da NF".

As duas coisas que este gate protege:

1. **A régua é por UNIDADE**, a mesma do PDF (`_gerar_pdf_pedido` soma `qtd_un × unitário`), que é
   a que reproduz o rodapé do 211 ao centavo — 14.482,02 líquido / 14.497,64 bruto / 23,50 m³ no
   pedido 565848. Somar por CAIXA divergiria no primeiro item digitado fora do múltiplo do fator,
   e item SEM fator de caixa (o caso do papel kraft do print) não tem múltiplo nenhum.
2. **O total é PISO, não medida**: item com cadastro ausente ou reprovado pela guarda de
   plausibilidade entra como ZERO. Sem o aviso, o comprador fecha carga com um total incompleto
   achando que está completo — e descobre na doca.
"""
from datetime import date
from pathlib import Path

from estoque import core

HOJE = date(2026, 8, 21)
JS = Path('static/estoque/estoque.js')


def _produto(cod=58564, qtunitcx=1.0, volume=0.0008, peso=0.94, pesoliq=0.92, qtestger=100.0):
    snap = [{"CODPROD": cod, "codfilial": "3", "qtestger": qtestger, "qtbloq": 0, "qtreserv": 0,
             "giro_m1": 300.0, "giro_m2": 300.0, "giro_m3": 300.0, "custofin": 8.52,
             "dtultent": None}]
    cad = {cod: {"CODPROD": cod, "DESCRICAO": "PAPEL KRAFT NATURAL 35G/M2-LARG.60CM",
                 "CODFORNEC": 9406, "QTUNITCX": qtunitcx,
                 "VOLUME": volume, "PESOBRUTO": peso, "PESOLIQ": pesoliq}}
    return core.construir_produtos(snap, {}, cad, {}, {}, {}, core.merge_params({}),
                                   hoje=HOJE)[0]


# ───────── 1. o campo por UNIDADE existe e é a régua do PDF ─────────

def test_o_produto_publica_peso_e_volume_por_UNIDADE():
    """Sem eles o front teria de dividir `peso_caixa_kg ÷ caixa` — e aquele já vem arredondado
    a 3 casas. Re-derivar de número arredondado é o defeito do card "Em risco" (789 × 791)."""
    p = _produto()
    assert p["peso_un_kg"] == 0.94
    assert p["volume_un_m3"] == 0.0008


def test_o_peso_por_unidade_casa_com_a_conta_do_PDF():
    """A invariante que faz o rodapé e o documento nunca divergirem: o PDF soma
    `qtd em unidades × medidas_unitarias(cad, fator)["bruto"]`. O campo publicado tem de ser
    exatamente esse número, não o da caixa."""
    for fator in (1.0, 6.0, 24.0):
        p = _produto(qtunitcx=fator, peso=0.94, volume=0.0008)
        med = core.medidas_unitarias(
            {"VOLUME": 0.0008, "PESOBRUTO": 0.94, "PESOLIQ": 0.92}, fator)
        assert p["peso_un_kg"] == core._round(med["bruto"], 4)
        # 6500 un é a quantidade real do 58564 no pedido do print
        assert abs(6500 * p["peso_un_kg"] - 6500 * med["bruto"]) < 0.01


def test_somar_por_CAIXA_daria_outro_numero_em_quantidade_fora_do_multiplo():
    """Prova de que a escolha da régua não é estética. Com fator 24 e 6.500 un (que não é
    múltiplo de 24), a conta por caixa arredonda para cima e infla o peso da carga."""
    p = _produto(qtunitcx=24.0, peso=0.94)
    import math
    por_unidade = 6500 * p["peso_un_kg"]
    por_caixa = math.ceil(6500 / 24) * p["peso_caixa_kg"]
    assert por_caixa > por_unidade
    assert por_caixa - por_unidade > 3, \
        "a divergência entre as duas réguas tem de ser mensurável — é por isso que ela importa"


# ───────── 2. cadastro ruim vira ZERO declarado, nunca número confiante ─────────

def test_cadastro_reprovado_pela_guarda_NAO_publica_peso():
    """Caixa implicada impossível (o caso do 66919: 5,3 kg × 100 un = 530 kg/caixa) — o item
    entra como zero no total, e é por isso que o rodapé precisa dizer quantos ficaram de fora."""
    p = _produto(qtunitcx=100.0, peso=5.3, volume=0.09879)
    assert p["medidas_confiaveis"] is False
    assert p["peso_un_kg"] is None and p["volume_un_m3"] is None


def test_produto_sem_cadastro_de_peso_sai_None_e_nao_zero_mudo():
    p = _produto(peso=0.0, volume=0.0)
    assert p["peso_un_kg"] is None and p["volume_un_m3"] is None
    assert p["medidas_confiaveis"] is True, \
        "ausência de cadastro não é cadastro REPROVADO — são coisas diferentes"


# ───────── 3. gates de código (o rodapé é JS) ─────────

def test_o_rodape_soma_por_unidade_e_reage_a_digitacao():
    js = JS.read_text(encoding='utf-8')
    assert "const pesoTotal=()=>itens.reduce((s,x)=>s+(+x.qtd||0)*(+x.peso_un_kg||0),0);" in js, \
        'o peso tem de sair de qtd em UNIDADES × unitário — a régua do PDF'
    assert "const cubTotal=()=>itens.reduce((s,x)=>s+(+x.qtd||0)*(+x.volume_un_m3||0),0);" in js
    # os campos precisam viajar até o item do modal
    assert 'peso_un_kg:p.peso_un_kg,volume_un_m3:p.volume_un_m3,medidas_confiaveis:p.medidas_confiaveis' in js
    # e o rodapé é o que o refreshTotals repinta a cada tecla
    assert '+ carga();' in js, 'a carga tem de entrar no rodapé, que é o repintado a cada tecla'
    assert "const cl=$('#pd-itens .count-line'); if(cl) cl.innerHTML=rodape();" in js


def test_o_total_avisa_quando_e_PISO():
    """Sem este aviso o número mente por omissão — e é um número que decide caminhão."""
    js = JS.read_text(encoding='utf-8')
    assert "const _semMed=x=>x.medidas_confiaveis===false||!(+x.peso_un_kg>0);" in js
    assert 'sem cadastro</span>' in js, 'o rodapé tem de dizer QUANTOS itens ficaram de fora'
    assert 'PISO, não a medida da carga' in js, 'e explicar o que isso significa'
