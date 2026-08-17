"""Gate: o export NÃO pode levar filtro de outra aba (17/08/2026).

O diretor reportou a aba Análise → Produtos mostrando **50 itens** e o PDF/Excel saindo com
**18** — só os itens em ruptura. Não era o PDF nem o Excel: era a URL que a tela monta.

`exportQS()` mandava os filtros LOCAIS de todas as abas na querystring, sem checar em qual aba
o usuário estava. A tela ignora filtro que não é dela (cada render lê só os seus), mas o
`_aplicar_filtros_cliente` do servidor aplica tudo que chegar, em qualquer view. Quem passasse
pela aba "Estoque zerado", escolhesse um status e fosse para "Produtos" levava o `ez_status`
junto — e o export saía recortado sem ninguém pedir.

Medido no universo do print (6 itens que a tela mostraria inteiros):
    ez_status  → 2    ·    cob_sub → 0    ·    cob_ped → 0    ·    par_classe → 0

É o espelho do bug que o README já documenta ("filtro de tela tem de viajar no exportQS()") e
o lado mais perigoso dele: filtro faltando deixa o export grande demais, o que é óbvio; filtro
sobrando o deixa MENOR, e uma lista curta sai agrupada, somada e plausível.

Este gate é de código (como `test_modal_pedido_layout`) porque a montagem da querystring é JS:
se alguém emitir um filtro local fora do guarda, o recorte silencioso volta e só aparece quando
o cliente conferir um fornecedor que ele conhece de cor.
"""
import re
from pathlib import Path

JS = Path('static/estoque/estoque.js').read_text(encoding='utf-8')

# filtros de recorte que o servidor aplica em QUALQUER view (estoque/routes.py,
# `_aplicar_filtros_cliente`) e que, por isso, precisam de dono declarado no front.
LOCAIS_DO_SERVIDOR = {'abast', 'margem', 'cob_max', 'sem_ped', 'ez_status',
                      'cob_faixa', 'cob_sub', 'cob_ped', 'par_faixa', 'par_classe'}

# filtros GLOBAIS: a barra do topo vale para o painel inteiro, então viajam sempre.
GLOBAIS = {'comprador_cod', 'curva', 'xyz', 'fornec', 'depto', 'busca'}


def _bloco_export_qs():
    """Corpo do `exportQS`, SEM os comentários — eles citam de propósito os padrões errados
    ("o export emitia `f.parClasse`…") e fariam o gate acusar a própria explicação do bug."""
    ini = JS.index('function exportQS(){')
    corpo = JS[ini:JS.index('\n}', ini)]
    return '\n'.join(l.split('//')[0] for l in corpo.splitlines())


def _mapa_de_propriedade():
    """FILTROS_DA_ABA do JS → {aba: [filtros]}."""
    bloco = JS[JS.index('const FILTROS_DA_ABA={'):]
    bloco = bloco[:bloco.index('};')]
    return {aba: re.findall(r"'([a-z_]+)'", lista)
            for aba, lista in re.findall(r"(\w+):\s*\[([^\]]*)\]", bloco)}


def test_mapa_de_propriedade_existe():
    mapa = _mapa_de_propriedade()
    assert mapa, 'FILTROS_DA_ABA sumiu — sem ele o exportQS volta a emitir filtro de outra aba'
    assert 'produtos' in mapa and 'estoque_zero' in mapa


def test_cada_filtro_local_tem_UM_dono():
    """Dois donos = o filtro volta a vazar por uma das abas."""
    mapa = _mapa_de_propriedade()
    visto = {}
    for aba, filtros in mapa.items():
        for f in filtros:
            assert f not in visto, f'{f} declarado em {visto[f]} e em {aba}'
            visto[f] = aba


def test_todo_filtro_que_o_servidor_aplica_tem_dono_declarado():
    """Filtro sem dono no mapa nunca chega ao export → a tela recorta e o Excel não (o bug
    espelhado, que o README já documenta)."""
    donos = {f for fs in _mapa_de_propriedade().values() for f in fs}
    faltando = LOCAIS_DO_SERVIDOR - donos
    assert not faltando, f'sem dono em FILTROS_DA_ABA: {sorted(faltando)}'


def test_o_ez_status_pertence_a_aba_que_o_aplica():
    """O caso reportado: `ez_status` é da aba Estoque zerado e de mais nenhuma."""
    mapa = _mapa_de_propriedade()
    assert mapa['estoque_zero'] == ['ez_status']
    assert 'ez_status' not in mapa.get('produtos', [])


def test_nenhum_filtro_local_e_emitido_sem_passar_pelo_guarda():
    """O coração do gate: dentro do `exportQS`, todo `p.set('<local>')` tem de estar atrás do
    `meu('<local>')`. É isto que impede a volta do `if(f.ezStatus) p.set(...)` solto."""
    bloco = _bloco_export_qs()
    for linha in bloco.splitlines():
        for chave in re.findall(r"p\.set\('([a-z_]+)'", linha):
            if chave in GLOBAIS or chave.startswith('val_faixa'):
                continue
            if chave not in LOCAIS_DO_SERVIDOR and chave not in {
                    'ven_mes', 'ven_per', 'lt_min', 'val_dias', 'forn_classe'}:
                continue
            base = 'val_faixa' if chave.startswith('val_faixa') else chave
            assert f"meu('{base}')" in linha, \
                f"`{chave}` é emitido sem o guarda meu('{base}') — volta a vazar para outras abas"


def test_par_classe_sai_do_campo_que_o_drill_realmente_escreve():
    """3º bug da mesma família. O card "Parado 120+ dias" do Cockpit faz drill com
    `{parado:'muito_critico'}` e é `S.cli.parado` que o `filtered()` lê para recortar a TELA.
    O export emitia `f.parClasse` — campo que NADA no app escreve — então a tela mostrava só o
    120+ e o Excel/PDF saíam com todos os parados. Sentido inverso do vazamento: aqui o export
    ficava GRANDE demais, que é exatamente o caso já documentado no README."""
    bloco = _bloco_export_qs().replace(' ', '')
    assert "p.set('par_classe',f.parado)" in bloco, \
        'par_classe tem de sair de S.cli.parado — o campo que o drill escreve'
    assert 'parClasse' not in bloco, \
        'S.cli.parClasse é campo morto: emitir a partir dele é não emitir nunca'


def test_filtros_globais_continuam_viajando_sempre():
    """A correção não pode ter jogado fora o oposto: o filtro do topo TEM de ir no export."""
    bloco = _bloco_export_qs()
    for chave in GLOBAIS:
        assert f"p.set('{chave}'" in bloco, f'{chave} deixou de viajar no export'
        linha = next(l for l in bloco.splitlines() if f"p.set('{chave}'" in l)
        assert 'meu(' not in linha, f'{chave} é global — não pode depender da aba aberta'
