"""Gate da lista de clientes DENTRO do Gerencial (10/2026, pedido do João Victor).

"Quando vem para essa tela, seria ideal abrir os clientes do vendedor, pois acaba que temos que ir
para outra tela para dar tratativa… trazer os clientes abaixo dessa tabela e, de acordo que o
usuário fosse clicando nas linhas acima, filtrava automaticamente os clientes."

O que estes testes protegem:
  · a lista sai da MESMA API e do MESMO RBAC da Carteira (`/api/carteira/clientes`);
  · a linha clicada e a lista contam o MESMO número de clientes — inclusive o 91+, que tem teto de
    365 dias porque o placar só conta a base ativa (`cobertura.na_base`). Sem o teto, o deep-link
    antigo trazia clientes de até 24 meses: mais do que a linha dizia;
  · a tratativa acontece na própria tela (selo do plano de ação);
  · na Empresa a lista não aparece (seriam milhares de linhas sem dono) e o clique segue para a Carteira.
"""
import re
from pathlib import Path

import cobertura as cob

HTML = Path('gerencial.html').read_text(encoding='utf-8')


def _faixa_range():
    bloco = re.search(r"const FAIXA_RANGE = \{(.*?)\};", HTML).group(1)
    base = int(re.search(r"const BASE_DIAS = (\d+);", HTML).group(1))
    out = {}
    for k, lo, hi in re.findall(r"'([^']+)':\[(\d+),(\w+)\]", bloco):
        out[k] = (int(lo), base if hi == 'BASE_DIAS' else int(hi))
    return out, base


def _cliente(codcli, dias, codusur=29, sup=18):
    return {'codcli': codcli, 'cliente': f'CLI {codcli}', 'recencia_dias': dias, 'codusur': codusur,
            'codsupervisor': sup, 'venda_12m': 100.0, 'lucro_12m': 10.0, 'segmento': 'loyal',
            'receita_perdida_proj': 0.0, 'status_personalizada': 'ok'}


def test_a_lista_conta_o_mesmo_que_a_linha_clicada():
    import server
    rng, base = _faixa_range()
    assert base == cob.BASE_DIAS
    dias = [0, 15, 16, 30, 31, 45, 46, 60, 61, 90, 91, 200, 365, 366, 500, 729]
    clientes = [_cliente(i, d) for i, d in enumerate(dias, start=1)]
    niveis = cob.agregar_niveis(clientes, coberto_dias=60)
    vend = next(v for v in niveis['vendedores'] if v['id'] == 29)
    for b in vend['buckets']:
        lo, hi = rng[b['faixa']]
        r = server._filtrar_carteira(clientes, {'vendedor': '29', 'dias_min': str(lo), 'dias_max': str(hi)})
        assert r['total'] == b['clientes'], b['faixa']
    lo, hi = rng['0-30']
    r = server._filtrar_carteira(clientes, {'vendedor': '29', 'dias_min': str(lo), 'dias_max': str(hi)})
    assert r['total'] == vend['rollup_0_30']['clientes']
    toda = server._filtrar_carteira(clientes, {'vendedor': '29', 'dias_min': '0', 'dias_max': str(base)})
    assert toda['total'] == vend['total_clientes'], "sem faixa = a base inteira do escopo"


def test_o_91_mais_tem_teto_na_base_ativa():
    rng, base = _faixa_range()
    assert rng['91+'] == (91, base)


def test_a_lista_usa_a_api_da_carteira_e_o_plano_de_acao():
    assert "fetchJSON('/api/carteira/clientes?'" in HTML
    assert '/static/plano-cliente.js' in HTML
    assert 'PlanoCliente.selo(c.codcli' in HTML and 'PlanoCliente.resumos(' in HTML


def test_na_empresa_o_clique_segue_para_a_carteira():
    corpo = HTML[HTML.index('function drillFaixa'):HTML.index('function drillRollup0_30')]
    assert "_scope.level === 'empresa'" in corpo and "'/carteira?'" in corpo
    assert "card.classList.toggle('hidden', naEmpresa)" in HTML
