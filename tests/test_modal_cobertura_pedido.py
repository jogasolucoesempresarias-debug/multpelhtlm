"""Gate da coluna **Cob.proj** no modal "Gerar pedido" (08/2026, pedido do diretor:
"aqui eu pedi para colocar uma coluna de cobertura de estoque, hora de finalizar o pedido,
analisar o que aumentar para completar uma carga" — confirmado depois: *"seria a projetada"*).

A coluna é REATIVA: mostra `15 → 61d`, onde a esquerda é a cobertura projetada de hoje e a
direita é onde ela FICA com a quantidade que o comprador está digitando. Número que não se mexe
enquanto se digita seria o modo de falha do campo "Parado: dias parados (≥)", que parecia a
régua e era só filtro de listagem.

A conta vive no JS (`_cobBase`/`_cobResult` em estoque.js), mas a PREMISSA dela é do core e é
testável aqui:

    cobertura_proj + sugestao_bruta / giro_dia  ==  lead + cobertura_total

Ou seja: aceitar a sugestão inteira tem de pousar EXATAMENTE no alvo. É isso que autoriza o
front a ancorar no `cobertura_proj` do servidor e só somar o incremento, em vez de refazer o
absoluto `(estoque_projetado + qtd) / giro_dia` — que reproduziria o defeito do card "Em risco"
(789 SKUs no card × 791 na lista), porque `giro_dia` viaja arredondado a 3 casas e `qtdisp` a 2.

Os dois últimos testes são de CÓDIGO (como `test_modal_pedido_layout`): layout e ligação de
handlers não se testam em pytest, e sem gate voltam em silêncio.
"""
from datetime import date
from pathlib import Path

from estoque import core

HOJE = date(2026, 8, 21)
JS = Path('static/estoque/estoque.js')


def _produto(qtestger=100.0, giro=150.0, ja_pedida=None, qtbloq=0.0, dtultent=None):
    snap = [{"CODPROD": 41756, "codfilial": "3", "qtestger": qtestger, "qtbloq": qtbloq,
             "qtreserv": 0, "giro_m1": giro, "giro_m2": giro, "giro_m3": giro,
             "custofin": 4.53, "dtultent": dtultent}]
    cad = {41756: {"CODPROD": 41756, "DESCRICAO": "BOLA GRANF.LISA 6.5 LILAS",
                   "CODFORNEC": 6305, "QTUNITCX": 6}}
    return core.construir_produtos(snap, {}, cad, {}, {}, {}, core.merge_params({}),
                                   hoje=HOJE, ja_pedida_map=ja_pedida or {})[0]


# ───────── 1. a premissa da coluna: aceitar a sugestão pousa NO ALVO ─────────

# ⚠️ Só itens com sugestão POSITIVA. A identidade é `sugestao = est_alvo − estoque_projetado`,
# então ela só vale enquanto o alvo está acima do projetado; item sobre-estocado tem sugestão
# zero e fica onde está (caso coberto em `test_item_sobre_estocado_...` logo abaixo).
CASOS = ((100.0, 150.0), (1.0, 53.0), (43.0, 84.0), (66.0, 130.0), (3.0, 18.0))


def test_a_sugestao_CRUA_pousa_exatamente_no_alvo_lead_mais_cobertura():
    """A identidade que autoriza a ancoragem:

        cobertura_proj + sugestao_bruta / giro_dia == lead + cobertura_total

    porque `sugestao = est_alvo − estoque_projetado` e `est_alvo = giro_dia × (lead + cob)`.
    Se este teste cair, ou a régua da sugestão mudou ou a coluna passou a mentir."""
    par = core.merge_params({})
    alvo = par["lead_time"] + par["cobertura_total"]
    for qtestger, giro in CASOS:
        p = _produto(qtestger=qtestger, giro=giro)
        resultante = (p["cobertura_proj"] or 0.0) + p["sugestao_bruta"] / p["giro_dia"]
        assert abs(resultante - alvo) < 0.5, (
            f"qtestger={qtestger} giro={giro}: a sugestão crua pousa em {resultante:.2f}d, "
            f"não no alvo de {alvo}d")


def test_a_sugestao_QUE_O_MODAL_RECEBE_passa_do_alvo_por_causa_da_caixa_fechada():
    """⚠️ O que o modal pré-preenche NÃO é a sugestão crua: com `arredonda_cx` (default 1) ela
    sobe para a caixa fechada seguinte. Então a coluna mostra o alvo **ou um pouco acima** —
    nunca abaixo — e o excedente é de no máximo UMA caixa de giro.

    Isto está aqui porque é a razão de a coluna NÃO ter cor: pintar "passou do alvo" acenderia
    em quase toda linha, por construção, e alerta que acende sempre é alerta que ninguém lê.
    Quem decide o que é "cobertura demais" é o `excesso_cob` (120d), que vive no core e não
    viaja para o front — não se pinta contra um limiar duplicado na tela."""
    par = core.merge_params({})
    alvo = par["lead_time"] + par["cobertura_total"]
    for qtestger, giro in CASOS:
        p = _produto(qtestger=qtestger, giro=giro)
        resultante = (p["cobertura_proj"] or 0.0) + p["sugestao_compra"] / p["giro_dia"]
        excedente = resultante - alvo
        assert excedente >= -0.5, "arredondar em caixa fechada nunca compra MENOS que o alvo"
        # o teto é uma caixa: 6 un ÷ giro/dia
        assert excedente < 6 / p["giro_dia"] + 0.5, (
            f"qtestger={qtestger} giro={giro}: excedente de {excedente:.2f}d passa de uma "
            f"caixa — o arredondamento deixou de ser o único motivo")


def test_item_sobre_estocado_nao_tem_sugestao_e_a_coluna_nao_desenha_seta():
    """O contorno da identidade acima: com o projetado ACIMA do alvo não há o que comprar
    (`sugestao = max(0, …)`), e a coluna mostra só o número de hoje, sem `→`. É o caso que
    interessa na pergunta do diretor — item assim é candidato a NÃO entrar na carga."""
    p = _produto(qtestger=860.0, giro=92.0)      # 280d de cobertura contra alvo de 55d
    par = core.merge_params({})
    assert p["cobertura_proj"] > par["lead_time"] + par["cobertura_total"]
    assert p["sugestao_compra"] == 0 and p["sugestao_cx"] == 0
    assert p["status_abast"] == "excesso"


def test_o_erro_de_ancorar_no_numero_do_servidor_e_de_segunda_ordem():
    """Mede o que o docstring afirma. `giro_dia` chega ao front com 3 casas e `qtdisp` com 2;
    a coluna soma `qtd / giro_dia` sobre o `cobertura_proj` do servidor em vez de refazer o
    absoluto. A diferença entre as duas contas tem de ser < 0,5 dia — abaixo da casa que a
    tela exibe, ou seja, invisível. É a prova de que ancorar não introduz o defeito que
    recalcular introduziria."""
    for qtestger, giro in ((100.0, 150.0), (1.0, 53.0), (104.0, 130.0), (43.0, 84.0)):
        p = _produto(qtestger=qtestger, giro=giro)
        qtd = p["sugestao_compra"]
        ancorado = (p["cobertura_proj"] or 0.0) + qtd / p["giro_dia"]
        recalculado = (p["estoque_projetado"] + qtd) / p["giro_dia"]
        assert abs(ancorado - recalculado) < 0.5, (
            f"qtestger={qtestger} giro={giro}: ancorado {ancorado:.3f} × "
            f"recalculado {recalculado:.3f} — divergência visível na tela")


# ───────── 2. a régua é a PROJETADA, não a do disponível ─────────

def test_a_base_e_a_PROJETADA_e_ela_difere_do_disponivel_quando_ha_pedido_em_aberto():
    """O diretor confirmou "seria a projetada". A diferença não é cosmética: com pedido em
    aberto, usar o disponível infla a melhora que a coluna anuncia E faz o modal discordar da
    coluna Cob.proj da Abastecimento, que é lida um clique antes. Duas réguas com o mesmo
    rótulo é o defeito do capital parado 15d × 60d."""
    p = _produto(qtestger=100.0, giro=150.0, ja_pedida={41756: 200.0})
    assert p["qtd_ja_pedida"] == 200.0
    assert p["cobertura_proj"] > p["cobertura"], \
        "a projetada tem de contar o pedido em aberto — é o que a separa da cobertura simples"
    # e a diferença é grande o bastante para importar na decisão
    assert p["cobertura_proj"] - p["cobertura"] > 30


def test_item_sem_giro_nao_tem_cobertura_calculavel():
    """Giro 0 → `∞` na tela (é o 9999 da régua oficial). Somar dias de pedido a isso não
    significaria nada, e a coluna não pode fingir que significa."""
    p = _produto(qtestger=100.0, giro=0.0)
    assert p["giro_dia"] == 0
    assert p["cobertura_proj"] is None


def test_item_ZERADO_com_giro_tem_projetada_nula_e_a_base_cai_em_ZERO():
    """⚠️ Divergência conhecida e declarada. O core só calcula `cobertura_proj` com projetado
    > 0, então item em ruptura chega ao front com None — e o helper `cob()` da Abastecimento
    pinta os DOIS nulos (sem giro e sem estoque) como `∞`. No modal isso não dá: a seta sairia
    "∞ → 24d". A base cai em 0, que é o valor da régua oficial
    (`cobertura_dias_oficial(0, giro>0) == 0`), e o title da célula declara a régua."""
    p = _produto(qtestger=0.0, giro=150.0)
    assert p["cobertura_proj"] is None
    assert p["giro_dia"] > 0
    assert core.cobertura_dias_oficial(0, p["giro_dia"]) == 0, \
        "a régua oficial diz 0 para item zerado com giro — é essa que a coluna segue"


# ───────── 3. gates de código (o JS não roda em pytest) ─────────

def test_a_coluna_le_o_cobertura_proj_do_servidor_e_nao_refaz_o_absoluto():
    js = JS.read_text(encoding='utf-8')
    assert 'cobertura_proj:p.cobertura_proj,giro_dia:p.giro_dia,estoque_projetado:p.estoque_projetado' in js, \
        'sem esses campos no _prodItem a coluna não tem de onde sair'
    assert 'const _cobBase=x=>((+x.giro_dia||0)<=0?null:(x.cobertura_proj==null?0:+x.cobertura_proj));' in js, \
        'a base tem de ser o cobertura_proj do SERVIDOR (ancoragem), com 0 no item zerado'
    assert 'const _cobResult=x=>{const b=_cobBase(x); return b==null?null:b+(+x.qtd||0)/(+x.giro_dia||1);};' in js, \
        'o resultado é base + INCREMENTO; refazer o absoluto reintroduz o defeito do card "Em risco"'


def test_a_coluna_e_REATIVA_e_as_celulas_derivadas_saem_de_um_lugar_so():
    """Se `pintaLinha` sumir, ou a Cob.proj congela enquanto o comprador digita (e a coluna
    vira enfeite), ou o Valor NF volta a ser escrito por índice posicional — e como a Cob.proj
    entrou ANTES dele, o dinheiro passaria a ser impresso dentro da cobertura, sem erro."""
    js = JS.read_text(encoding='utf-8')
    assert "const cb=tr.querySelector('[data-cob]'); if(cb) cb.innerHTML=cobCel(x);" in js
    assert "const nf=tr.querySelector('[data-nf]'); if(nf) nf.textContent=money(linhaNF(x));" in js
    assert '.children[6].' not in js, \
        'acesso posicional às células voltou — é a armadilha nº 20 do README na versão DOM'
    assert 'const pintaLinha=(tr,x)=>{' in js, 'a definição sumiu'
    # os três handlers que mexem na linha (qtd, caixas, IPI) têm de repintar por lá
    assert js.count('pintaLinha(') == 3, \
        'esperado: 3 chamadas — quem editar a linha e não repintar deixa a coluna congelada'
