"""Gate da Curva ABC — régua do RCA e cards no recorte da tela (12/11/2026).

João Victor: *"esses valores totais estão errados, não está correto. Parece não respeitar o filtro
de fornecedor ali ou do time"*. Medido no BI, eram dois defeitos:

1. **A venda saía ~1% ACIMA do Dashboard em todo time.** A curva usava `[VENDA LIQUIDA]` e
   `[LUCRO TOTAL]` com `FILTER(FATURAMENTO_VENDAS, …)`, e a devolução não saía na régua do RCA
   (DTENT + escopo nas três tabelas): no time 19, R$ 28 mil descontados de R$ 170 mil de
   devolução. Hoje é a conta do `expr_venda_liquida_rca`, com FILTRO DE COLUNA — `FILTER(tabela)`
   por produto deu o mesmo número em 124 s contra 1 s.
2. **Os cards eram sempre do escopo inteiro**; fornecedor/depto/busca só recortavam a tabela.
   Hoje os cards (e o resumo do PDF) somam o recorte. A CLASSE do item segue a da curva do escopo.
"""
import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

import server
import curva_abc
from tests.conftest import login_as
from tests.test_curva_abc import _rotas, _payload, _q_abc

TABELAS = ('FATURAMENTO_VENDAS', 'FATURAMENTO_DEVOLUCAO', 'FATURAMENTO_DEVOLUCAO_AVULSA')


# ───────────────────────── 1. régua do RCA na query ─────────────────────────
def test_venda_desconta_devolucao_por_DTENT_com_o_time_nas_tres_tabelas(client, usuario_admin, mock_dax_capture, clean_redis):
    _rotas(mock_dax_capture)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    assert client.get('/api/abc?supervisor=18').status_code == 200
    q = _q_abc(mock_dax_capture)[-1]
    assert 'FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12)' in q
    assert 'FATURAMENTO_DEVOLUCAO[DTENT] >= EDATE(TODAY(), -12)' in q
    assert 'FATURAMENTO_DEVOLUCAO_AVULSA[DTENT] >= EDATE(TODAY(), -12)' in q
    for t in TABELAS:   # sem o escopo, a devolução sairia da empresa inteira
        assert f'{t}[CODSUPERVISOR] IN {{18}}' in q
    for m in ('[VENDA BRUTA]', '[TOTAL DEVOLUCAO]', '[TOTAL DEVOLUCAO AVULSA]',
              '[CUSTO TOTAL]', '[CUSTO TOTAL DEVOLUCAO]', '[CUSTO TOTAL DEVOLUCAO AVULSA]'):
        assert m in q
    # a medida nativa é o defeito; a dimensão é o que filtra as três tabelas
    assert '[VENDA LIQUIDA]' not in q and '[LUCRO TOTAL]' not in q
    assert 'PCPRODUT[CODPROD]' in q


def test_filtro_de_COLUNA_e_nao_FILTER_da_tabela_de_fatos(client, usuario_admin, mock_dax_capture, clean_redis):
    """Mesmo número, 124 s × 1 s no BI real (empresa inteira, 12m)."""
    _rotas(mock_dax_capture)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    client.get('/api/abc?supervisor=18&codcli=7')
    q = _q_abc(mock_dax_capture)[-1]
    assert 'FILTER(FATURAMENTO_VENDAS' not in q and 'FILTER(FATURAMENTO_DEVOLUCAO' not in q
    assert '&&' not in q        # cada predicado vira um argumento de coluna


def test_vendedor_e_cliente_valem_nas_devolucoes(client, usuario_vendedor, mock_dax_capture, clean_redis):
    _rotas(mock_dax_capture)
    login_as(client, usuario_vendedor['email'], usuario_vendedor['senha'])
    client.get('/api/abc?codcli=4242&periodo=mes_atual')
    q = _q_abc(mock_dax_capture)[-1]
    for t in TABELAS:
        assert f'{t}[CODUSUR] = 573' in q and f'{t}[CODCLI] = 4242' in q
    assert 'FATURAMENTO_DEVOLUCAO[DTENT] >= DATE(YEAR(TODAY()), MONTH(TODAY()), 1)' in q


def test_drawer_na_mesma_regua_da_tabela(client, usuario_admin, mock_dax_capture, clean_redis):
    """Série e "quem vende" na régua do RCA — senão a série não fecha com a venda da linha."""
    _rotas(mock_dax_capture)
    mock_dax_capture.routes = [
        ('CALENDARIO[AnoMes]', _payload([{'CALENDARIO[AnoMes]': 202607, '[Venda]': 90.0, '[Lucro]': 20.0,
                                          '[Qt]': 9.0, '[Clientes]': 3}])),
        ('PCUSUARI[CODUSUR],', _payload([{'PCUSUARI[CODUSUR]': 573.0, '[Venda]': 90.0, '[Qt]': 9.0}])),
    ] + mock_dax_capture.routes
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    d = client.get('/api/abc/produto/1000?supervisor=18&periodo=6m').get_json()
    assert d['ok'] and d['vendedores'][0]['codusur'] == 573
    qd = [q for q in mock_dax_capture.queries if 'CODPROD] = 1000' in q]
    assert len(qd) == 2
    for q in qd:
        assert '[VENDA LIQUIDA]' not in q and '[TOTAL DEVOLUCAO AVULSA]' in q
        for t in TABELAS:
            assert f'{t}[CODPROD] = 1000' in q and f'{t}[CODSUPERVISOR] IN {{18}}' in q
    q_serie = next(q for q in qd if 'CALENDARIO[AnoMes]' in q)
    q_vend = next(q for q in qd if 'PCUSUARI[CODUSUR],' in q)
    assert 'FATURAMENTO_DEVOLUCAO[DTENT] >= EDATE(TODAY(), -12)' in q_serie
    assert 'FATURAMENTO_DEVOLUCAO[DTENT] >= EDATE(TODAY(), -6)' in q_vend


# ───────────────────────── 2. resumo do PDF no recorte ─────────────────────────
@pytest.fixture
def pdf_capturado(monkeypatch):
    cap = {}

    def _fake(rows, resumo, escopo, filtros='', periodo=None, recorte=False):
        cap.update(rows=rows, resumo=resumo, recorte=recorte)
        return b'%PDF-fake'
    monkeypatch.setattr(server, '_gerar_pdf_abc', _fake)
    return cap


def test_resumo_do_pdf_segue_o_fornecedor(client, usuario_admin, mock_dax_capture, clean_redis, pdf_capturado):
    _rotas(mock_dax_capture)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    assert client.get('/api/abc/pdf?codfornec=113').status_code == 200
    assert pdf_capturado['recorte'] is True
    assert pdf_capturado['resumo']['total_produtos'] == 1 == len(pdf_capturado['rows'])
    assert pdf_capturado['resumo']['total_venda'] == pdf_capturado['rows'][0]['venda']


def test_classe_recorta_a_tabela_mas_NAO_o_resumo(client, usuario_admin, mock_dax_capture, clean_redis, pdf_capturado):
    """Com "só A" no resumo, os cards B e C sairiam zerados — a classe é o que os cards separam."""
    _rotas(mock_dax_capture)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    client.get('/api/abc/pdf?classe=A')
    r = pdf_capturado['resumo']
    assert pdf_capturado['recorte'] is False and r['total_produtos'] == 300
    assert r['classes']['B']['qt'] > 0 and r['classes']['C']['qt'] > 0
    assert {x['classe'] for x in pdf_capturado['rows']} == {'A'}


# ───────────────────────── 3. cards no recorte (JS executado) ─────────────────────────
HTML = Path('abc.html').read_text(encoding='utf-8')
NODE = shutil.which('node')
needs_node = pytest.mark.skipif(NODE is None, reason='node não instalado')


def _fonte(nome):
    ini = HTML.index(f'function {nome}(')
    return HTML[ini:HTML.index('\n}\n', ini) + 2]


def _linha(nome):
    """Função de UMA linha (`function x() { … }`) — a linha inteira."""
    ini = HTML.index(f'function {nome}(')
    return HTML[ini:HTML.index('\n', ini) + 1]


def _resumo_js(rows):
    script = _linha('margemPct') + _fonte('resumoDe') + \
        f'\nconsole.log(JSON.stringify(resumoDe({json.dumps(rows)})));'
    out = subprocess.run([NODE, '-e', script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@needs_node
def test_resumo_do_front_e_o_mesmo_do_python_no_recorte():
    rnd = random.Random(7)
    itens = [{'codprod': i, 'venda': round(rnd.expovariate(1 / 900), 2) if i % 17 else -15.0,
              'lucro': round(rnd.uniform(-50, 400), 2), 'codfornec': rnd.choice([113, 5, 8])}
             for i in range(400)]
    curva = curva_abc.classificar(itens)
    recorte = curva_abc.filtrar(curva, codfornec='113')
    js, py = _resumo_js(recorte), curva_abc.resumo(recorte)
    assert js['total_produtos'] == py['total_produtos']
    assert js['total_venda'] == pytest.approx(py['total_venda'], abs=0.01)
    assert js['margem'] == pytest.approx(py['margem'], abs=0.01)
    assert js['concentracao_pct_itens'] == pytest.approx(py['concentracao_pct_itens'], abs=0.05)
    for c in 'ABC':
        assert js['classes'][c]['qt'] == py['classes'][c]['qt']
        assert js['classes'][c]['venda'] == pytest.approx(py['classes'][c]['venda'], abs=0.01)
        assert js['classes'][c]['pct_venda'] == pytest.approx(py['classes'][c]['pct_venda'], abs=0.05)
        assert (js['classes'][c]['margem'] is None) == (py['classes'][c]['margem'] is None)


def test_cards_somam_o_recorte_e_reagem_ao_filtro_de_tela():
    corpo = _fonte('renderKpis')
    assert 'resumoDe(rowsRecorte())' in corpo
    assert 'renderKpis()' in _linha('onFiltroTela')        # filtro de tela redesenha os cards
    assert 'aplicarFiltros(_rows, true)' in _linha('rowsRecorte')   # o recorte ignora a classe
