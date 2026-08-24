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
    """⚠️ A contagem é DERIVADA do cabeçalho, não cravada. A versão anterior travava em 8 e
    quebrou quando a coluna Cob.proj entrou (08/2026) — o que ela precisa provar é que colgroup
    e thead têm o MESMO número de colunas, senão as larguras desalinham e o corte volta."""
    js = Path('static/estoque/estoque.js').read_text(encoding='utf-8')
    # começa DEPOIS da abertura: `<colgroup` também casa com `<col` e inflaria a contagem
    ini = js.index('class="pd-tab"><colgroup>') + len('class="pd-tab"><colgroup>')
    trecho = js[ini:js.index('</colgroup>')]
    # ⚠️ procurar o fecho A PARTIR do início do cabeçalho: existe `</tr></thead>` de outras
    # tabelas ANTES desta no arquivo, e um `index` do zero recortaria uma fatia vazia — o teste
    # passaria a comparar 9 com 0 e a mensagem não denunciaria o motivo
    _h0 = js.index('</colgroup><thead><tr>')
    cab = js[_h0:js.index('</tr></thead>', _h0)]
    # cabeçalhos = os montados pelo helper `_th` + o `<th></th>` vazio do botão de remover
    n_th = cab.count('_th(') + cab.count('<th>')
    assert trecho.count('<col') == n_th, \
        f'o colgroup tem {trecho.count("<col")} colunas e o cabeçalho {n_th} — precisam casar'
    assert trecho.count('<col>') == 1, \
        'exatamente UMA coluna sem largura (Produto) — é ela que absorve a sobra e trunca'
    # e o CORPO também: colgroup e cabeçalho podem casar em 9 enquanto a linha tem 8 <td>, e aí
    # as células deslizam uma coluna para a esquerda sem erro nenhum — o valor cai debaixo do
    # cabeçalho errado. É a armadilha nº 20 do README (tupla posicional) na versão HTML.
    _b0 = js.index("itens.map((x,i)=>`<tr>")
    corpo = js[_b0:js.index("</tr>`).join('')", _b0)]
    assert corpo.count('<td') == n_th, \
        f'o cabeçalho tem {n_th} colunas e a linha {corpo.count("<td")} — as células deslizam'


def test_as_larguras_fixas_deixam_folga_para_o_produto_na_menor_tela():
    """A soma das colunas fixas não pode comer a elástica. O modal é `min(1240px, 96vw)` com
    22px de padding de cada lado; a menor tela em que isto foi verificado é 1280px (→ 1228,8px
    de modal, 1184,8px de conteúdo). Abaixo de ~300px o nome do produto vira reticências e a
    conferência linha a linha contra o 211, que é o uso real da tela, deixa de ser possível."""
    import re
    js = Path('static/estoque/estoque.js').read_text(encoding='utf-8')
    ini = js.index('class="pd-tab"><colgroup>') + len('class="pd-tab"><colgroup>')
    trecho = js[ini:js.index('</colgroup>')]
    fixas = sum(int(w) for w in re.findall(r'<col style="width:(\d+)px"', trecho))
    produto = 1280 * 0.96 - 44 - fixas
    assert produto >= 300, \
        f'sobram {produto:.0f}px para o nome do produto a 1280px de tela — coluna nova demais'


def test_celulas_truncam_em_vez_de_empurrar():
    css = Path('static/estoque/estoque.css').read_text(encoding='utf-8')
    assert 'text-overflow: ellipsis' in css and '.pd-tab td' in css
    # o limite global de 340px do .prod brigaria com a coluna elástica e voltaria a empurrar
    assert '.pd-tab td .prod { max-width: 100%; }' in css
