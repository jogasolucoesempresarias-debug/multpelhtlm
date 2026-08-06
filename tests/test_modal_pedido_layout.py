"""Gate do layout da tabela de itens do modal "Gerar pedido" (08/2026).

O diretor reportou DUAS vezes que a tela cortava: a coluna "Valor NF" ficava escondida atrás da
barra de rolagem lateral, então ele conferia o pedido sem enxergar o valor da linha.

Alargar o modal não resolveu (fui de 880px para 1240px e continuou cortando): com layout de
tabela automático, a largura é a do CONTEÚDO, então a tabela cresce junto com o modal. A
correção é estrutural — `table-layout: fixed` + `width: 100%` fazem a tabela ser exatamente a
largura disponível, e aí rolagem lateral vira impossível em qualquer resolução. O que cede é o
nome do produto, única coluna sem largura no `<colgroup>`.

Verificado no navegador em 1920/1600/1366/1280, com os nomes longos de produção (a base de
demonstração tem nomes curtos — foi o que mascarou o problema na primeira tentativa).

Este gate é de código porque layout não se testa em pytest: se alguém tirar o `table-layout` ou
o `<colgroup>`, o corte volta silenciosamente e só aparece quando o comprador reclamar de novo.
"""
from pathlib import Path


def test_tabela_do_pedido_tem_layout_fixo():
    css = Path('static/estoque/estoque.css').read_text(encoding='utf-8')
    assert '.pd-tab { table-layout: fixed; width: 100%; }' in css, \
        'sem table-layout fixo a tabela volta a ter a largura do conteúdo e a cortar'


def test_colgroup_declara_as_larguras_e_deixa_o_produto_elastico():
    js = Path('static/estoque/estoque.js').read_text(encoding='utf-8')
    # começa DEPOIS da abertura: `<colgroup` também casa com `<col` e inflaria a contagem
    ini = js.index('class="pd-tab"><colgroup>') + len('class="pd-tab"><colgroup>')
    trecho = js[ini:js.index('</colgroup>')]
    # 8 colunas: 7 com largura fixa + a do produto, que é a elástica (um <col> sem style)
    assert trecho.count('<col') == 8, 'a tabela tem 8 colunas; o colgroup precisa cobrir todas'
    assert trecho.count('<col>') == 1, \
        'exatamente UMA coluna sem largura (Produto) — é ela que absorve a sobra e trunca'


def test_celulas_truncam_em_vez_de_empurrar():
    css = Path('static/estoque/estoque.css').read_text(encoding='utf-8')
    assert 'text-overflow: ellipsis' in css and '.pd-tab td' in css
    # o limite global de 340px do .prod brigaria com a coluna elástica e voltaria a empurrar
    assert '.pd-tab td .prod { max-width: 100%; }' in css
