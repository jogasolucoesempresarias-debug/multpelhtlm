"""Cobertura de estoque: os cards de faixa somam o RECORTE da tela (18/11/2026).

João Victor: com "Entrou nos últimos 1 dia" a tabela mostrava só o que chegou e os cards seguiam no
total do estoque. Os cards são o seletor de faixa (mostram todas as faixas), mas do que está
filtrado — só a faixa marcada não os afeta. Guarda de fonte: o JS não tem harness de teste.
"""
import pathlib
import re

JS = (pathlib.Path(__file__).resolve().parent.parent / 'static' / 'estoque' / 'estoque.js').read_text(encoding='utf-8')
CORPO = JS[JS.index('function renderRuptura('):JS.index('const ENTRADA_JANELA')]


def test_cards_saem_do_recorte_e_nao_da_base_inteira():
    assert re.search(r"const faixas=FX_COB\.map\(f=>\{const it=recorte\.filter", CORPO)
    assert 'const faixas=FX_COB.map(f=>{const it=P.filter' not in CORPO


def test_recorte_tem_pedido_e_entrada_mas_nao_a_faixa():
    i_rec, i_fx = CORPO.index('let recorte='), CORPO.index('const faixas=')
    trecho = CORPO[i_rec:i_fx]
    assert 'passaPed' in trecho and 'dias_sem_entrada' in trecho
    assert 'cobertura_faixa' not in trecho.replace("p.cobertura_faixa===f.key", '')
    # a faixa marcada só filtra a tabela, depois dos cards
    assert CORPO.index('if(cf.length) rf=rf.filter', i_fx) > i_fx
