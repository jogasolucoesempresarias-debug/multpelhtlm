"""Gate do "Deseja sair sem salvar?" no modal de pedido (10/2026: os compradores clicavam fora do
modal "Gerar pedido" e perdiam o pedido inteiro já editado).

O que este gate protege:

1. **Fechamento ACIDENTAL pergunta**: clique no fundo e Esc passam por `fecharModalAcidental`,
   nunca direto por `closeModal`. Cancelar e Lançar seguem fechando direto (intenção explícita).
2. **Só pergunta quando há o que perder**: o modal de pedido compara o CONTEÚDO com o da abertura
   — quem abre a sugestão e clica fora sem mexer não é interrompido.
3. **O guarda não vaza**: abrir ou fechar qualquer modal zera `_modalSujo`, senão o modal de meta
   ou de plano herdaria o "sujo" do último pedido.
"""
import re
from pathlib import Path

JS = Path('static/estoque/estoque.js').read_text(encoding='utf-8')


def test_clique_no_fundo_e_esc_passam_pela_confirmacao():
    assert "$('#modal-bg').onclick=e=>{if(e.target===$('#modal-bg'))fecharModalAcidental();}" in JS
    esc = re.search(r"e\.key==='Escape'\)\{(.*?)\}\}\);", JS)
    assert esc and 'fecharModalAcidental()' in esc.group(1)
    assert 'closeModal()' not in esc.group(1)


def test_confirmacao_so_quando_o_modal_esta_sujo():
    corpo = JS.split('function fecharModalAcidental(){', 1)[1].split('\n}', 1)[0]
    assert '_modalSujo&&_modalSujo()&&!confirm(' in corpo
    assert 'sair sem salvar' in corpo


def test_abrir_e_fechar_zeram_o_guarda():
    assert re.search(r'function openModal\(html,wide\)\{ _modalSujo=null;', JS)
    assert re.search(r'function closeModal\(\)\{ _modalSujo=null;', JS)


def test_modal_de_pedido_registra_o_guarda_por_conteudo():
    corpo = JS.split('function modalPedido(opts){', 1)[1].split('\nfunction ', 1)[0]
    assert '_modalSujo=()=>' in corpo
    assert '_assinatura()!==_inicial' in corpo
    # ordenar a tabela não é alteração: a assinatura ordena os itens por código
    assert '.sort((a,b)=>a[0]-b[0])' in corpo
    # Cancelar continua fechando direto
    assert "$('#m-cancel').onclick=closeModal;" in corpo


def test_f5_ou_fechar_aba_com_pedido_alterado_avisa():
    assert "addEventListener('beforeunload'" in JS
