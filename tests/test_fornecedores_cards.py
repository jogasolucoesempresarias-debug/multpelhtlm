"""Gate dos 3 cards da aba Fornecedores (09/2026, pedido do João Victor).

"Fui fazer uma análise ali naquela tela de rentabilidade por curva ABC e não tem o total em
lugar nenhum, seja da margem, receita ou lucro — aí tenho que ir em outra tela da JOGA olhar."

Os cards são os MESMOS do Cockpit (Venda · Lucro · Margem), mas somados sobre o que a TABELA da
aba lista. As duas regras que este gate trava:

1. **Margem é Σlucro ÷ Σvenda**, nunca a média das margens das linhas — um fornecedor de R$ 2 mi
   e um de R$ 500 não pesam igual. É a régua do `margem_total` do Cockpit.
2. **O card soma as linhas EXIBIDAS** (`Ff`, depois de Curva e Classe), não a base nem as 300
   que a render corta. Card e lista com universos diferentes é o defeito do "Em risco" (789 SKUs
   no card × 791 na lista) — aqui a fonte é uma só.

`fornTotais` é pura, então o teste a executa de verdade no Node (extraída do estoque.js) em vez
de só conferir o texto. Os testes de código (grep) travam o que a execução não vê: de onde
vêm as linhas e se a tela declara a divergência com o Cockpit sob o filtro Curva.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path('static/estoque/estoque.js').read_text(encoding='utf-8')
NODE = shutil.which('node')


def _fonte(nome):
    """Corpo da função — o fechamento é o primeiro `}` sozinho na coluna 0."""
    ini = JS.index(f'function {nome}(')
    return JS[ini:JS.index('\n}\n', ini) + 2]


def _totais(rows, extra_pronto=True):
    script = _fonte('fornTotais') + \
        f'\nconsole.log(JSON.stringify(fornTotais({json.dumps(rows)}, {json.dumps(extra_pronto)})));'
    out = subprocess.run([NODE, '-e', script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


needs_node = pytest.mark.skipif(NODE is None, reason='node não instalado')

LINHAS = [
    {'venda': 2_000_000.0, 'lucro': 100_000.0, 'verba': 50_000.0},   # margem 5%
    {'venda': 500.0, 'lucro': 250.0, 'verba': 0.0},                 # margem 50%
]


@needs_node
def test_venda_e_lucro_sao_a_soma_das_linhas():
    t = _totais(LINHAS)
    assert t['n'] == 2
    assert t['venda'] == pytest.approx(2_000_500.0)
    assert t['lucro'] == pytest.approx(100_250.0)


@needs_node
def test_margem_e_ponderada_e_NAO_a_media_das_margens():
    """Média simples daria (5 + 50) / 2 = 27,5%. A ponderada é 100.250 ÷ 2.000.500 = 5,01%."""
    t = _totais(LINHAS)
    assert t['margem'] == pytest.approx(100_250 / 2_000_500 * 100, abs=1e-6)
    assert abs(t['margem'] - 27.5) > 20, 'virou média das margens das linhas'


@needs_node
def test_verba_entra_no_lucro_e_na_margem_c_verba():
    t = _totais(LINHAS)
    assert t['lucro_verba'] == pytest.approx(150_250.0)
    assert t['margem_verba'] == pytest.approx(150_250 / 2_000_500 * 100, abs=1e-6)


@needs_node
def test_extra_ainda_carregando_deixa_a_versao_c_verba_em_branco():
    """Mesma política do Cresc. AA: enquanto o /fornecedores_extra não chegou, `—`, nunca o
    lucro bruto disfarçado de 'c/ verba'."""
    t = _totais(LINHAS, extra_pronto=False)
    assert t['lucro_verba'] is None and t['margem_verba'] is None
    assert t['lucro'] == pytest.approx(100_250.0), 'o bruto não depende do extra'


@needs_node
def test_sem_venda_a_margem_sai_None_e_nao_zero():
    t = _totais([{'venda': 0, 'lucro': 0, 'verba': 10}])
    assert t['margem'] is None and t['margem_verba'] is None


@needs_node
def test_lista_vazia_nao_quebra():
    """Com o extra pronto, lucro c/ verba = 0 é MEDIÇÃO (não há linha); só a margem sai `—`."""
    t = _totais([])
    assert t == {'n': 0, 'venda': 0, 'lucro': 0, 'margem': None, 'lucro_verba': 0,
                 'margem_verba': None}


@needs_node
def test_campo_ausente_conta_zero():
    """Linha sem `verba` (fornecedor fora do extra) não pode virar NaN e apagar o card."""
    t = _totais([{'venda': 100.0, 'lucro': 10.0}])
    assert t['lucro_verba'] == pytest.approx(10.0)


# ───────── gates de código: o que a execução da função pura não enxerga ─────────

def _corpo_render():
    ini = JS.index('function renderFornecedores(P){')
    return JS[ini:JS.index('\n}\n', ini)]


def test_o_card_soma_as_linhas_EXIBIDAS_e_nao_a_base():
    corpo = _corpo_render()
    assert 'fornTotais(Ff,' in corpo, \
        'o total tem de sair de `Ff` (depois de Curva e Classe) — base/F/rows dão card ≠ tabela'
    assert 'fornTotais(base' not in corpo and 'fornTotais(F,' not in corpo \
        and 'fornTotais(rows' not in corpo


def test_os_tres_cards_existem_com_os_rotulos_do_cockpit():
    corpo = _corpo_render()
    for rotulo in ("kpi('Venda '+periodoLbl", "kpi('Lucro bruto'", "kpi('Margem'"):
        assert rotulo in corpo, f'card {rotulo} sumiu da aba'


def test_a_tela_declara_que_com_curva_ativa_o_total_nao_bate_com_o_cockpit():
    """Curva aqui é ABC do FORNECEDOR; no Cockpit é a do produto. Sem o aviso, a primeira
    comparação entre as duas telas vira chamado de 'número errado'."""
    corpo = _corpo_render()
    assert 'curvaAtiva' in corpo and 'ABC do <b>fornecedor</b>' in corpo


def test_o_c_verba_so_aparece_com_o_extra_pronto():
    corpo = _corpo_render()
    assert 'fornTotais(Ff, !exLoading&&!_fx.erro)' in corpo, \
        'o extra em carga/erro tem de zerar a versão c/ verba (nunca número provisório)'
