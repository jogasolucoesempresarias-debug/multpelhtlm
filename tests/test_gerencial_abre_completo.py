"""Gate da abertura da aba "Painel gerencial" (08/2026).

A aba abria pela metade. As duas partes têm origens diferentes: a rosquinha "Participação de
lucro por comprador" sai do snapshot (já em memória, pinta na hora) e os 5 pilares vêm de
`/api/resumos` (rede). A 1ª versão pintava a rosquinha e chamava `injectResumos` SEM await, que
se injetava num placeholder quando a resposta chegasse — a tela crescia sozinha por segundos.

O efeito ruim não é a demora, é a ORDEM: durante a janela, o gerente lê um painel gerencial que
ainda vai mudar. Um número parcial que parece completo é pior que um spinner. Agora busca
primeiro e pinta uma vez só.

Gate de código porque render assíncrono não se testa em pytest: se alguém voltar a injetar o
resumo depois da pintura, o comportamento volta em silêncio e só reaparece quando o diretor
reclamar de novo.
"""
from pathlib import Path

JS = Path('static/estoque/estoque.js').read_text(encoding='utf-8')


def _corpo_render_gerencial():
    """Só o corpo da função — o fechamento é o primeiro `}` sozinho na coluna 0."""
    ini = JS.index('async function renderGerencial(P){')
    return JS[ini:JS.index('\n}\n', ini)]


def test_espera_os_resumos_antes_de_montar_a_tela():
    corpo = _corpo_render_gerencial()
    assert 'await fetchResumos()' in corpo, \
        'a aba tem de buscar os resumos ANTES de montar a tela'
    assert corpo.index('await fetchResumos()') < corpo.index('resumosHTML(o)'), \
        'montar o HTML antes do await é pintar sem dado'


def test_a_rosquinha_de_lucro_nao_pinta_antes_dos_pilares():
    """A metade que vinha do snapshot é justamente a que aparecia sozinha."""
    corpo = _corpo_render_gerencial()
    assert corpo.index('await fetchResumos()') < corpo.index('Participação de lucro por comprador'), \
        'o bloco de lucro voltou a ser pintado antes da resposta da rede — a aba abre pela metade'


def test_nao_ha_placeholder_que_se_completa_sozinho():
    assert 'Carregando resumos gerenciais' not in JS, \
        'placeholder de resumo = a tela abre incompleta e se completa depois'
    assert 'id="gg-resumos"' not in JS, \
        'injetar o resumo num alvo separado é o próprio padrão que fazia a tela crescer'


def test_resposta_lenta_de_filtro_antigo_nao_vence_a_nova():
    """Trocar filtro/aba durante a busca dispara outra render; sem selo, a lenta sobrescreve."""
    corpo = _corpo_render_gerencial()
    assert 'const meu=++_ggSeq;' in corpo and 'if(meu!==_ggSeq) return;' in corpo, \
        'sem o selo de sequência, a busca mais lenta vence a mais recente'


def test_o_erro_de_rede_nao_deixa_a_aba_em_branco():
    corpo = _corpo_render_gerencial()
    assert 'catch(e){' in corpo and 'indisponíveis' in corpo, \
        'falha de /api/resumos tem de virar mensagem, não spinner eterno'
