"""Gate da aba Curva ABC (Comercial) — motor puro, endpoints com DAX mockado e modo postgres.

O que trava:
- a régua é a MESMA do Compras (`estoque.core._aplicar_curva`), para o app não ter duas curvas A;
- o escopo é de VENDA e a querystring NÃO amplia o escopo de quem não é admin;
- o export sai com o filtro da tela (classe/depto/fornecedor/busca), não com o universo;
- amostra pequena AVISA em vez de esconder;
- a janela (12m/6m/3m/mês atual) muda a query, o cache e o piso da amostra — e a curva é DA JANELA;
- a margem é lucro ÷ venda líquida (régua do Comercial), por item e por classe, e viaja no export;
- supervisor ESTREITA o próprio escopo (time dele / RCA do time dele) e nunca amplia;
- modo BD responde nas mesmas formas (produtização).
"""
import random

import pytest

import server
import curva_abc
from tests.conftest import login_as


# ───────────────────────── motor puro ─────────────────────────
def _itens(vendas):
    return [{'codprod': i + 1, 'venda': v} for i, v in enumerate(vendas)]


def test_pareto_basico_ordena_ranqueia_e_classifica():
    out = curva_abc.classificar(_itens([10, 70, 15, 5]))
    assert [i['codprod'] for i in out] == [2, 3, 1, 4]        # desc por venda
    assert [i['rank'] for i in out] == [1, 2, 3, 4]
    # 70 → 70% (A) · +15 → 85% (B) · +10 → 95% (B, fronteira inclusiva) · +5 → 100% (C)
    assert [i['classe'] for i in out] == ['A', 'B', 'B', 'C']
    assert [round(i['pct_acum']) for i in out] == [70, 85, 95, 100]


def test_fronteira_inclusiva_igual_ao_compras():
    """Item que pousa EXATAMENTE em 80% ainda é A; em 95% ainda é B."""
    out = curva_abc.classificar(_itens([80, 15, 5]))
    assert [i['classe'] for i in out] == ['A', 'B', 'C']


def test_pct_soma_100_e_nao_muta_entrada():
    entrada = _itens([3, 1, 2])
    copia = [dict(i) for i in entrada]
    out = curva_abc.classificar(entrada)
    assert entrada == copia
    assert sum(i['pct'] for i in out) == pytest.approx(100, abs=0.01)


def test_venda_zero_ou_negativa_vira_C_sem_quebrar():
    out = curva_abc.classificar(_itens([60, 30, 10, 0, -5]))
    assert [i['classe'] for i in out] == ['A', 'B', 'C', 'C', 'C']
    assert out[3]['pct'] == 0 and out[4]['pct'] == 0
    # ⚠️ Pareto degenerado: 1 item = 100% da venda → cai em C (é o comportamento do Compras
    # também, e é por isso que a amostra pequena AVISA). Travado para ninguém "consertar" só aqui.
    assert curva_abc.classificar(_itens([100]))[0]['classe'] == 'C'
    assert curva_abc.classificar([]) == []
    assert all(i['classe'] == 'C' for i in curva_abc.classificar(_itens([0, 0])))


def test_mesma_regua_do_compras():
    """Fonte única de 'curva A' no app: o Comercial tem de classificar exatamente como o
    `estoque/core._aplicar_curva` (cortes 80/95). Se um dia divergirem, a mesma gerente vê um
    item A numa aba e B na outra."""
    from estoque import core
    rnd = random.Random(42)
    for _ in range(20):
        vendas = [round(rnd.expovariate(1 / 500), 2) for _ in range(rnd.randint(1, 400))]
        # alguns empates e zeros de propósito
        vendas += [vendas[0]] * 3 + [0.0] * 2
        nosso = {i['codprod']: i['classe'] for i in curva_abc.classificar(_itens(vendas))}
        deles = [{'codprod': i + 1, 'venda': v} for i, v in enumerate(vendas)]
        core._aplicar_curva(deles, 'venda', 'curva_abc', curva_abc.CORTE_A, curva_abc.CORTE_B)
        assert nosso == {i['codprod']: i['curva_abc'] for i in deles}


def test_resumo_fecha_com_o_total():
    out = curva_abc.classificar(_itens([50, 30, 10, 5, 3, 2]))
    r = curva_abc.resumo(out)
    c = r['classes']
    assert c['A']['qt'] + c['B']['qt'] + c['C']['qt'] == r['total_produtos'] == 6
    assert c['A']['venda'] + c['B']['venda'] + c['C']['venda'] == pytest.approx(r['total_venda'])
    assert c['A']['pct_venda'] + c['B']['pct_venda'] + c['C']['pct_venda'] == pytest.approx(100, abs=0.2)
    assert r['concentracao_pct_itens'] == pytest.approx(c['A']['qt'] / 6 * 100, abs=0.1)


def test_amostra_pequena_avisa():
    ok, motivo = curva_abc.amostra_confiavel(_itens([1000] * 50))
    assert ok is False and '50 produtos' in motivo
    ok, motivo = curva_abc.amostra_confiavel(_itens([10] * 300))
    assert ok is False and 'venda' in motivo
    ok, _ = curva_abc.amostra_confiavel(_itens([1000] * 300))
    assert ok is True


def test_filtrar_normaliza_codepto_float_x_str():
    """CODEPTO vem float do BI ('7.0') e int do Postgres ('7'): o filtro tem de casar os dois."""
    itens = curva_abc.classificar([
        {'codprod': 1, 'venda': 70, 'codepto': 7.0, 'codfornec': 113, 'descricao': 'ISOPOR X'},
        {'codprod': 3, 'venda': 20, 'codepto': 9, 'codfornec': 8, 'descricao': 'PRATO Z'},
        {'codprod': 2, 'venda': 10, 'codepto': 2, 'codfornec': 5, 'descricao': 'COPO Y'},
    ])   # A (70%) · B (90%) · C (100%)
    assert [i['codprod'] for i in curva_abc.filtrar(itens, codepto='7')] == [1]
    assert [i['codprod'] for i in curva_abc.filtrar(itens, codepto=7.0)] == [1]
    assert [i['codprod'] for i in curva_abc.filtrar(itens, codfornec='113')] == [1]
    assert [i['codprod'] for i in curva_abc.filtrar(itens, classe='C')] == [2]
    assert [i['codprod'] for i in curva_abc.filtrar(itens, classe='a, c')] == [1, 2]   # tolera caixa/espaço
    assert [i['codprod'] for i in curva_abc.filtrar(itens, busca='copo')] == [2]
    assert [i['codprod'] for i in curva_abc.filtrar(itens, busca='1')] == [1]      # código exato
    assert curva_abc.filtrar(itens) == itens


# ───────────────────────── endpoints (DAX mockado) ─────────────────────────
def _payload(rows):
    return {'results': [{'tables': [{'rows': rows}]}]}


def _abc_rows(n=300, seed=1):
    rnd = random.Random(seed)
    rows = []
    for i in range(n):
        venda = round(rnd.expovariate(1 / 2000), 2) + 1
        # ⚠️ lucro DIFERENTE por item (e um negativo) — fixture com lucro = k×venda testaria nada de margem
        rows.append({'FATURAMENTO_VENDAS[CODPROD]': 1000 + i, '[Venda]': venda,
                     '[Lucro]': round(venda * (rnd.uniform(-0.1, 0.4)), 2), '[Clientes]': rnd.randint(1, 50)})
    return rows


def _rotas(cap, n=300):
    cap.set_routes([
        ('[Venda] > 0', _payload(_abc_rows(n))),
        ('FORNECPRINC', _payload([
            {'FATURAMENTO_VENDAS[CODPROD]': 1000, 'FATURAMENTO_VENDAS[DESCRICAO]': 'EMB.GALV.G32',
             'FATURAMENTO_VENDAS[CODEPTO]': 7.0, 'FATURAMENTO_VENDAS[CODFORNECPRINC]': 113.0,
             'FATURAMENTO_VENDAS[FORNECPRINC]': 'GALVANOTEK', '[Venda]': 1.0},
            {'FATURAMENTO_VENDAS[CODPROD]': 1001, 'FATURAMENTO_VENDAS[DESCRICAO]': 'COPO 300',
             'FATURAMENTO_VENDAS[CODEPTO]': 2.0, 'FATURAMENTO_VENDAS[CODFORNECPRINC]': 5.0,
             'FATURAMENTO_VENDAS[FORNECPRINC]': 'COPAZA', '[Venda]': 1.0},
        ])),
        ('DEPARTAMENTO', _payload([{'FATURAMENTO_DEVOLUCAO[CODEPTO]': 7, 'FATURAMENTO_DEVOLUCAO[DEPARTAMENTO]': 'DESCARTAVEIS'}])),
    ])


def _q_abc(cap):
    return [q for q in cap.queries if '[Venda] > 0' in q]


def test_api_abc_admin_com_filtro_de_time(client, usuario_admin, mock_dax_capture, clean_redis):
    _rotas(mock_dax_capture)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    r = client.get('/api/abc?supervisor=18')
    assert r.status_code == 200, r.get_data(as_text=True)
    d = r.get_json()
    assert d['ok'] and d['total'] == 300 and len(d['rows']) == 300
    assert '[CODSUPERVISOR] IN {18}' in _q_abc(mock_dax_capture)[-1]
    assert 'EDATE(TODAY(), -12)' in _q_abc(mock_dax_capture)[-1]
    # forma de cada linha + régua declarada
    assert {'codprod', 'descricao', 'depto_nome', 'fornec_nome', 'venda', 'lucro', 'margem', 'clientes',
            'rank', 'pct', 'pct_acum', 'classe'} <= set(d['rows'][0])
    assert d['regua']['corte_a'] == 80 and d['regua']['meses'] == 12
    assert d['periodo']['tok'] == '12m' and d['periodo']['rotulo'] == 'últimos 12 meses'
    assert '[LUCRO TOTAL]' in _q_abc(mock_dax_capture)[-1]
    assert d['rows'][0]['rank'] == 1 and d['rows'][0]['classe'] == 'A'
    c = d['resumo']['classes']
    assert c['A']['qt'] + c['B']['qt'] + c['C']['qt'] == 300
    # nome/depto resolvidos pelo catálogo; CODEPTO float casou com o mapa de nomes
    p = next(x for x in d['rows'] if x['codprod'] == 1000)
    assert p['descricao'] == 'EMB.GALV.G32' and p['depto_nome'] == 'DESCARTAVEIS' and p['fornec_nome'] == 'GALVANOTEK'


def test_admin_sem_filtro_e_empresa_inteira(client, usuario_admin, mock_dax_capture, clean_redis):
    _rotas(mock_dax_capture)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    d = client.get('/api/abc').get_json()
    assert d['escopo'] == 'Empresa inteira'
    q = _q_abc(mock_dax_capture)[-1]
    assert 'CODSUPERVISOR' not in q and 'CODUSUR' not in q


def test_vendedor_fica_no_proprio_escopo_e_ignora_querystring(client, usuario_vendedor, mock_dax_capture, clean_redis):
    """Vendedor passando ?supervisor=/?vendedor= NÃO amplia o escopo — a curva é só dele."""
    _rotas(mock_dax_capture)
    login_as(client, usuario_vendedor['email'], usuario_vendedor['senha'])
    r = client.get('/api/abc?supervisor=18&vendedor=999')
    assert r.status_code == 200
    q = _q_abc(mock_dax_capture)[-1]
    assert 'FATURAMENTO_VENDAS[CODUSUR] = 573' in q
    assert 'CODSUPERVISOR' not in q and '999' not in q


def test_supervisor_ve_o_time_dele(client, usuario_supervisor, mock_dax_capture, clean_redis):
    _rotas(mock_dax_capture)
    login_as(client, usuario_supervisor['email'], usuario_supervisor['senha'])
    r = client.get('/api/abc?supervisor=99')          # tenta outro time: ignorado
    assert r.status_code == 200
    q = _q_abc(mock_dax_capture)[-1]
    assert '[CODSUPERVISOR] IN {18}' in q and '99' not in q


def test_cache_separa_escopos(client, usuario_admin, mock_dax_capture, clean_redis):
    """Time A consultado primeiro não pode ser servido ao time B (lição da aba Verbas)."""
    _rotas(mock_dax_capture)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    client.get('/api/abc?supervisor=18')
    client.get('/api/abc?supervisor=18')      # cache
    client.get('/api/abc?supervisor=19')
    qs = _q_abc(mock_dax_capture)
    assert len(qs) == 2
    assert 'IN {18}' in qs[0] and 'IN {19}' in qs[1]


def test_export_csv_sai_com_o_filtro_da_tela(client, usuario_admin, mock_dax_capture, clean_redis):
    _rotas(mock_dax_capture)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    d = client.get('/api/abc').get_json()
    n_a = d['resumo']['classes']['A']['qt']
    r = client.get('/api/abc/csv?classe=A')
    assert r.status_code == 200 and 'text/csv' in r.content_type
    linhas = [l for l in r.get_data(as_text=True).splitlines() if l.strip()]
    assert linhas[0].lstrip('﻿') == 'sep=;'
    assert linhas[1].startswith('Rank;Classe;CodProd')
    assert 'Lucro12m;MargemPct;Clientes' in linhas[1]
    assert len(linhas) - 2 == n_a                      # só as A
    assert all(l.split(';')[1] == 'A' for l in linhas[2:])
    assert 'classe-A' in r.headers['Content-Disposition']
    # filtro de depto: só o 1000 tem depto 7
    r2 = client.get('/api/abc/csv?codepto=7')
    assert len([l for l in r2.get_data(as_text=True).splitlines() if l.strip()]) - 2 == 1


def test_export_pdf(client, usuario_admin, mock_dax_capture, clean_redis):
    _rotas(mock_dax_capture)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    r = client.get('/api/abc/pdf?supervisor=18&classe=A,B')
    assert r.status_code == 200 and r.content_type == 'application/pdf'
    assert r.data[:4] == b'%PDF'


def test_amostra_pequena_vem_avisada_e_nao_escondida(client, usuario_admin, mock_dax_capture, clean_redis):
    _rotas(mock_dax_capture, n=40)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    d = client.get('/api/abc').get_json()
    assert d['ok'] and d['total'] == 40 and len(d['rows']) == 40     # continua entregando
    assert d['amostra_ok'] is False and '40 produtos' in d['amostra_motivo']


def test_produto_drawer_serie_e_vendedores_sem_tecnicos(client, usuario_admin, mock_dax_capture, clean_redis):
    _rotas(mock_dax_capture)
    # ⚠️ as rotas do drawer vão NA FRENTE: a query de vendedores também contém '[Venda] > 0'
    mock_dax_capture.routes = [
        ('CALENDARIO[AnoMes]', _payload([{'CALENDARIO[AnoMes]': 202607, '[Venda]': 100.0, '[Qt]': 10.0, '[Clientes]': 4},
                                         {'CALENDARIO[AnoMes]': 202606, '[Venda]': 50.0, '[Qt]': 5.0, '[Clientes]': 2}])),
        ('FATURAMENTO_VENDAS[CODUSUR],', _payload([{'FATURAMENTO_VENDAS[CODUSUR]': 573.0, '[Venda]': 120.0, '[Qt]': 12.0},
                                                   {'FATURAMENTO_VENDAS[CODUSUR]': 999.0, '[Venda]': 30.0, '[Qt]': 3.0}])),
    ] + mock_dax_capture.routes
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    d = client.get('/api/abc/produto/1000?supervisor=18').get_json()
    assert d['ok'] and d['codprod'] == 1000
    assert [s['anomes'] for s in d['serie']] == [202606, 202607]      # ordenada
    assert [v['codusur'] for v in d['vendedores']] == [573]            # 999 (transferência) fora
    assert d['vendedores'][0]['pct'] == 100.0
    assert d['item'] and d['item']['descricao'] == 'EMB.GALV.G32'
    # a query do drawer carrega o escopo e o produto
    qd = [q for q in mock_dax_capture.queries if 'CODPROD] = 1000' in q]
    assert len(qd) == 2 and all('[CODSUPERVISOR] IN {18}' in q for q in qd)
    assert all('CODOPER] = "S"' in q for q in qd)                     # qtd na régua da medida


def test_pagina_exige_login(client, usuario_admin):
    r = client.get('/abc')
    assert r.status_code in (302, 401)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    r = client.get('/abc')
    assert r.status_code == 200 and b'Curva ABC' in r.data


def test_menu_tem_a_aba():
    js = open('static/joga-header.js', encoding='utf-8').read()
    assert "href: '/abc'" in js


# ───────────────────────── margem ─────────────────────────
def test_margem_por_item_e_por_classe_na_regua_do_comercial():
    """Margem = lucro ÷ venda líquida (a conta do Dashboard/Categorias), ponderada — nunca média
    de margens. Item sem venda: None. Classe: Σ lucro ÷ Σ venda dos itens dela."""
    out = curva_abc.classificar([
        {'codprod': 1, 'venda': 100.0, 'lucro': 30.0},   # A (50%) — 30%
        {'codprod': 2, 'venda': 60.0,  'lucro': -6.0},   # A (80%) — -10%
        {'codprod': 3, 'venda': 30.0,  'lucro': 3.0},    # B (95%) — 10%
        {'codprod': 4, 'venda': 10.0,  'lucro': 5.0},    # C — 50%
        {'codprod': 5, 'venda': 0.0,   'lucro': 2.0},    # C, sem venda
    ])
    m = {i['codprod']: i['margem'] for i in out}
    assert m == {1: 30.0, 2: -10.0, 3: 10.0, 4: 50.0, 5: None}
    r = curva_abc.resumo(out)
    assert r['classes']['A']['margem'] == pytest.approx(24 / 160 * 100, abs=0.01)   # ponderada, não (30-10)/2
    assert r['classes']['B']['margem'] == 10.0 and r['classes']['C']['margem'] == 50.0
    assert r['classes']['C']['qt'] == 2                                          # o sem venda conta como item
    assert r['margem'] == pytest.approx(32 / 200 * 100, abs=0.01) and r['total_lucro'] == 32.0
    # item sem 'lucro' (fixture antiga / provider sem a coluna) não ganha a chave — sem KeyError
    assert 'margem' not in curva_abc.classificar([{'codprod': 9, 'venda': 5}])[0]


def test_api_abc_margem_bate_com_lucro_dividido_por_venda(client, usuario_admin, mock_dax_capture, clean_redis):
    _rotas(mock_dax_capture)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    d = client.get('/api/abc').get_json()
    for r in d['rows'][:20]:
        assert r['margem'] == pytest.approx(r['lucro'] / r['venda'] * 100, abs=0.02)
    c = d['resumo']['classes']
    for k in 'ABC':
        assert c[k]['margem'] == pytest.approx(c[k]['lucro'] / c[k]['venda'] * 100, abs=0.02)
    assert any(r['margem'] < 0 for r in d['rows'])           # negativa aparece, não é escondida
    # CSV leva lucro e margem do item
    linhas = [l for l in client.get('/api/abc/csv').get_data(as_text=True).splitlines() if l.strip()]
    cab = linhas[1].split(';'); i_l, i_m, i_c = cab.index('Lucro12m'), cab.index('MargemPct'), cab.index('CodProd')
    prim = linhas[2].split(';'); row = next(r for r in d['rows'] if str(r['codprod']) == prim[i_c])
    assert float(prim[i_l].replace(',', '.')) == pytest.approx(row['lucro']) and float(prim[i_m].replace(',', '.')) == pytest.approx(row['margem'])


# ───────────────────────── período ─────────────────────────
def test_normalizar_periodo_e_piso_da_amostra_escala_com_a_janela():
    assert curva_abc.normalizar_periodo(None) == '12m'
    assert curva_abc.normalizar_periodo('xyz') == '12m'
    assert curva_abc.normalizar_periodo('MES_ATUAL') == 'mes_atual'
    # 300 itens × R$ 100 = R$ 30 mil: pouco p/ 12m (piso 100 mil), suficiente p/ 3m (25 mil)
    itens = _itens([100] * 300)
    assert curva_abc.amostra_confiavel(itens)[0] is False
    assert curva_abc.amostra_confiavel(itens, periodo='3m')[0] is True
    assert curva_abc.amostra_confiavel(itens, periodo='mes_atual')[0] is True


def test_api_abc_periodo_muda_a_query_o_cache_e_o_rotulo(client, usuario_admin, mock_dax_capture, clean_redis):
    _rotas(mock_dax_capture)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    d6 = client.get('/api/abc?periodo=6m').get_json()
    assert 'EDATE(TODAY(), -6)' in _q_abc(mock_dax_capture)[-1]
    assert d6['periodo'] == {**d6['periodo'], 'tok': '6m', 'meses': 6, 'rotulo': 'últimos 6 meses', 'curto': '6m'}
    assert d6['regua']['meses'] == 6
    dm = client.get('/api/abc?periodo=mes_atual').get_json()
    q = _q_abc(mock_dax_capture)[-1]
    assert 'DATE(YEAR(TODAY()), MONTH(TODAY()), 1)' in q and 'EDATE' not in q
    assert dm['periodo']['tok'] == 'mes_atual' and dm['periodo']['rotulo'].startswith('mês atual · 01–')
    assert dm['periodo']['anomes_inicio'] == dm['periodo']['anomes_fim']
    # token inválido cai no padrão E reaproveita o cache do 12m (uma query só p/ os dois)
    client.get('/api/abc'); client.get('/api/abc?periodo=lixo')
    assert len(_q_abc(mock_dax_capture)) == 3
    # export carrega a janela no nome do arquivo e no cabeçalho
    r = client.get('/api/abc/csv?periodo=3m')
    assert 'curva_abc_3m_' in r.headers['Content-Disposition']
    assert 'Venda3m;PctVenda;PctAcumulado;Lucro3m' in r.get_data(as_text=True)
    assert client.get('/api/abc/pdf?periodo=mes_atual').data[:4] == b'%PDF'


def test_drawer_serie_fica_em_12m_e_vendedores_seguem_a_janela(client, usuario_admin, mock_dax_capture, clean_redis):
    _rotas(mock_dax_capture)
    mock_dax_capture.routes = [
        ('CALENDARIO[AnoMes]', _payload([{'CALENDARIO[AnoMes]': 202607, '[Venda]': 100.0, '[Lucro]': 25.0, '[Qt]': 10.0, '[Clientes]': 4}])),
        ('FATURAMENTO_VENDAS[CODUSUR],', _payload([{'FATURAMENTO_VENDAS[CODUSUR]': 573.0, '[Venda]': 120.0, '[Qt]': 12.0}])),
    ] + mock_dax_capture.routes
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    d = client.get('/api/abc/produto/1000?periodo=3m').get_json()
    assert d['ok'] and d['periodo']['tok'] == '3m'
    assert d['serie'][0]['margem'] == 25.0 and d['serie'][0]['lucro'] == 25.0
    qd = [q for q in mock_dax_capture.queries if 'CODPROD] = 1000' in q]
    q_serie = next(q for q in qd if 'CALENDARIO[AnoMes]' in q)
    q_vend = next(q for q in qd if 'FATURAMENTO_VENDAS[CODUSUR],' in q)
    assert 'EDATE(TODAY(), -12)' in q_serie and '[LUCRO TOTAL]' in q_serie      # contexto: sempre 12m
    assert 'EDATE(TODAY(), -3)' in q_vend                                       # quem vende: a janela


# ───────────────────────── supervisor estreita o escopo ─────────────────────────
def _vendedores_map_fake(monkeypatch):
    monkeypatch.setattr(server, '_carregar_vendedores_map', lambda: {
        '573': {'nome': 'RCA DO 18', 'codsupervisor': 18},
        '700': {'nome': 'RCA DO 19', 'codsupervisor': 19},
        '800': {'nome': 'RCA DO 99', 'codsupervisor': 99},
    })


def test_supervisor_filtra_rca_do_proprio_time_e_ignora_rca_de_fora(client, usuario_supervisor, mock_dax_capture, clean_redis, monkeypatch):
    """O supervisor da loja pediu "filtro de time/RCA" (17/09/2026): ele estreita o escopo dele.
    O RBAC continua no filtro (um RCA que mudou de time no fato não traz venda de outro time)."""
    _rotas(mock_dax_capture); _vendedores_map_fake(monkeypatch)
    login_as(client, usuario_supervisor['email'], usuario_supervisor['senha'])
    d = client.get('/api/abc?vendedor=573').get_json()
    q = _q_abc(mock_dax_capture)[-1]
    assert '[CODSUPERVISOR] IN {18}' in q and 'FATURAMENTO_VENDAS[CODUSUR] = 573' in q
    assert d['escopo'] == 'Vendedor: RCA DO 18'
    # RCA de outro time: ignorado → curva do time (e cache separado do anterior)
    d2 = client.get('/api/abc?vendedor=800').get_json()
    q2 = _q_abc(mock_dax_capture)[-1]
    assert '[CODSUPERVISOR] IN {18}' in q2 and 'CODUSUR' not in q2 and '800' not in q2
    assert d2['escopo'].startswith('Time: ')
    assert len(_q_abc(mock_dax_capture)) == 2


def test_supervisor_multi_area_escolhe_um_dos_times_dele(client, usuario_supervisor_multi, mock_dax_capture, clean_redis, monkeypatch):
    _rotas(mock_dax_capture); _vendedores_map_fake(monkeypatch)
    login_as(client, usuario_supervisor_multi['email'], usuario_supervisor_multi['senha'])
    client.get('/api/abc')
    assert '[CODSUPERVISOR] IN {18, 19}' in _q_abc(mock_dax_capture)[-1]
    client.get('/api/abc?supervisor=19')
    q = _q_abc(mock_dax_capture)[-1]
    assert q.count('CODSUPERVISOR') == 2 and '[CODSUPERVISOR] IN {19}' in q      # RBAC + estreitamento
    client.get('/api/abc?supervisor=99')                                          # de fora: ignorado
    q = _q_abc(mock_dax_capture)[-1]
    assert '99' not in q and '[CODSUPERVISOR] IN {18, 19}' in q
    client.get('/api/abc?supervisor=19,99')                                       # interseção
    assert '[CODSUPERVISOR] IN {19}' in _q_abc(mock_dax_capture)[-1]
    # RCA do 19 vale (é área dele); ?supervisor= junto é ignorado porque vendedor tem precedência
    client.get('/api/abc?vendedor=700&supervisor=18')
    q = _q_abc(mock_dax_capture)[-1]
    assert 'CODUSUR] = 700' in q and '[CODSUPERVISOR] IN {18, 19}' in q


def test_vendedor_nao_estreita_nem_amplia(client, usuario_vendedor, mock_dax_capture, clean_redis, monkeypatch):
    _rotas(mock_dax_capture); _vendedores_map_fake(monkeypatch)
    login_as(client, usuario_vendedor['email'], usuario_vendedor['senha'])
    client.get('/api/abc?vendedor=700&supervisor=18&periodo=3m')
    q = _q_abc(mock_dax_capture)[-1]
    assert 'FATURAMENTO_VENDAS[CODUSUR] = 573' in q and '700' not in q and 'CODSUPERVISOR' not in q
    assert 'EDATE(TODAY(), -3)' in q                                              # período ele pode


# ───────────────────────── modo postgres (joga_demo local) ─────────────────────────
def _postgres(monkeypatch):
    server._R.flushall()
    monkeypatch.setitem(server.CONFIG, 'data_source', 'postgres')


def test_abc_modo_postgres_admin_e_supervisor(client, usuario_admin, usuario_supervisor, monkeypatch):
    _postgres(monkeypatch)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    d = client.get('/api/abc').get_json()
    assert d['ok'] and d['total'] > 100
    assert d['rows'] == sorted(d['rows'], key=lambda r: r['venda'], reverse=True)
    assert {r['classe'] for r in d['rows']} == {'A', 'B', 'C'}
    assert d['rows'][0]['descricao'] and not d['rows'][0]['descricao'].startswith('Produto ')
    total_admin = d['resumo']['total_venda']

    # recorte de time pelo admin: subconjunto da empresa
    import provider_sql
    conn = provider_sql.analytics_conn(); cur = conn.cursor()
    cur.execute("SELECT codsupervisor FROM faturamento_vendas WHERE codoper='S' AND codsupervisor IS NOT NULL "
                "GROUP BY 1 ORDER BY count(*) DESC LIMIT 1")
    sup = cur.fetchone()[0]
    cur.execute("SELECT codprod FROM faturamento_vendas WHERE codoper='S' AND codsupervisor=%s "
                "GROUP BY 1 ORDER BY count(*) DESC LIMIT 1", (sup,))
    cp = cur.fetchone()[0]
    conn.close()
    dt = client.get(f'/api/abc?supervisor={sup}').get_json()
    assert dt['ok'] and 0 < dt['resumo']['total_venda'] < total_admin
    assert dt['escopo'].startswith('Time: ')
    assert all(r['margem'] == pytest.approx(r['lucro'] / r['venda'] * 100, abs=0.02) for r in dt['rows'][:30])
    assert dt['resumo']['classes']['A']['margem'] is not None
    # janela menor = venda menor ou igual, e a curva é da janela (rank pode mudar)
    d3 = client.get(f'/api/abc?supervisor={sup}&periodo=3m').get_json()
    assert d3['ok'] and d3['periodo']['tok'] == '3m' and d3['resumo']['total_venda'] <= dt['resumo']['total_venda']
    dm = client.get(f'/api/abc?supervisor={sup}&periodo=mes_atual').get_json()
    assert dm['ok'] and dm['resumo']['total_venda'] <= d3['resumo']['total_venda']

    # drawer + exports no modo BD
    dp = client.get(f'/api/abc/produto/{cp}?supervisor={sup}').get_json()
    assert dp['ok'] and dp['serie'] and dp['vendedores']
    assert 'margem' in dp['serie'][0] and 'lucro' in dp['serie'][0]
    dp3 = client.get(f'/api/abc/produto/{cp}?supervisor={sup}&periodo=3m').get_json()
    assert dp3['ok'] and len(dp3['serie']) == len(dp['serie'])                    # série continua 12m
    assert client.get(f'/api/abc/csv?supervisor={sup}&classe=A').status_code == 200
    assert client.get(f'/api/abc/pdf?supervisor={sup}').status_code == 200

    # supervisor logado vê só o dele (e não muda passando outro time)
    client.get('/logout')
    login_as(client, usuario_supervisor['email'], usuario_supervisor['senha'])
    ds = client.get(f'/api/abc?supervisor={sup}').get_json()
    assert ds['ok'] and ds['escopo'].startswith('Time: ')
    assert ds['resumo']['total_venda'] <= total_admin


def test_nomes_de_depto_da_demo_espelham_o_seed():
    """`provider_sql._DEPTO_NOMES_SEED` é cópia de `_seed_demo/gerar.py:DEPTO_NOMES` (o seed não grava
    o nome). Lê o fonte do seed (importá-lo tem efeitos colaterais) e compara."""
    import ast, pathlib, provider_sql
    src = pathlib.Path('_seed_demo/gerar.py').read_text(encoding='utf-8')
    tree = ast.parse(src)
    nomes = next(ast.literal_eval(n.value) for n in ast.walk(tree)
                 if isinstance(n, ast.Assign) and any(getattr(t, 'id', '') == 'DEPTO_NOMES' for t in n.targets))
    assert provider_sql._DEPTO_NOMES_SEED == nomes


def test_abc_default_powerbi_intocado():
    assert server.CONFIG['data_source'] == 'powerbi'


# ───────────────────────── cliente como ESCOPO (09/2026, pedido do João Victor) ─────────────────────────
# "vc vai conseguir colocar o filtro de cliente aqui?" — cliente NÃO é filtro de tela (as linhas são por
# produto, sem dimensão de cliente): é a 4ª dimensão de escopo, recalculada no servidor como time/vendedor.
# O RBAC fica no filtro (régua de VENDA): o vendedor vê o que ELE vendeu ao cliente, nunca mais que isso.

def _carteira_fake(monkeypatch):
    monkeypatch.setattr(server, '_carregar_carteira_full', lambda: [
        {'codcli': 4242, 'cliente': 'PADARIA DO ZE', 'codusur': 573},
        {'codcli': 7, 'cliente': 'MERCADO SETE', 'codusur': 999},
    ])


def test_cliente_entra_no_dax_no_rotulo_e_no_cache(client, usuario_admin, mock_dax_capture, clean_redis, monkeypatch):
    _rotas(mock_dax_capture)
    _carteira_fake(monkeypatch)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    d = client.get('/api/abc?codcli=4242').get_json()
    assert d['ok'] and d['codcli'] == 4242
    q = _q_abc(mock_dax_capture)[-1]
    assert 'FATURAMENTO_VENDAS[CODCLI] = 4242' in q
    assert d['escopo'] == 'Cliente: PADARIA DO ZE'
    assert 'cliente' in d['regua'] and 'concentração' in d['regua']['cliente']
    # cliente + time: os dois no filtro, rótulo composto
    d2 = client.get('/api/abc?codcli=4242&supervisor=18').get_json()
    q2 = _q_abc(mock_dax_capture)[-1]
    assert '[CODCLI] = 4242' in q2 and '[CODSUPERVISOR] IN {18}' in q2
    assert d2['escopo'].startswith('Cliente: PADARIA DO ZE · Time: ')
    # cache separa clientes (lição da aba Verbas): 2 clientes = 2 queries, repetir = 0
    n = len(_q_abc(mock_dax_capture))
    client.get('/api/abc?codcli=4242'); client.get('/api/abc?codcli=7')
    assert len(_q_abc(mock_dax_capture)) == n + 1
    assert '[CODCLI] = 7' in _q_abc(mock_dax_capture)[-1]
    # cliente sem nome no cadastro sai com o código, nunca com erro
    assert client.get('/api/abc?codcli=31337').get_json()['escopo'] == 'Cliente: #31337'


def test_cliente_dispensa_o_aviso_de_amostra_mas_declara_a_regua(client, usuario_admin, mock_dax_capture, clean_redis, monkeypatch):
    """Um cliente compra 30-80 itens: o aviso amarelo acenderia SEMPRE e viraria ruído. Com cliente
    ativo a curva sai colorida e a régua diz o que ela é (decisão 21/09/2026)."""
    _rotas(mock_dax_capture, n=40)
    _carteira_fake(monkeypatch)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    sem = client.get('/api/abc').get_json()
    assert sem['amostra_ok'] is False                       # sem cliente: a regra continua
    com = client.get('/api/abc?codcli=4242').get_json()
    assert com['amostra_ok'] is True and com['amostra_motivo'] == ''
    assert com['regua']['cliente']
    assert 'cliente' not in sem['regua']


def test_vendedor_com_cliente_mantem_o_rbac_no_filtro(client, usuario_vendedor, mock_dax_capture, clean_redis, monkeypatch):
    """O cliente restringe, nunca substitui: o CODUSUR do vendedor continua no DAX."""
    _rotas(mock_dax_capture)
    _carteira_fake(monkeypatch)
    login_as(client, usuario_vendedor['email'], usuario_vendedor['senha'])
    d = client.get('/api/abc?codcli=7&supervisor=18').get_json()
    assert d['ok']
    q = _q_abc(mock_dax_capture)[-1]
    assert 'FATURAMENTO_VENDAS[CODUSUR] = 573' in q and '[CODCLI] = 7' in q
    assert 'CODSUPERVISOR' not in q


def test_codcli_invalido_e_ignorado(client, usuario_admin, mock_dax_capture, clean_redis):
    _rotas(mock_dax_capture)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    d = client.get('/api/abc?codcli=abc').get_json()
    assert d['ok'] and d['codcli'] is None and d['escopo'] == 'Empresa inteira'
    assert 'CODCLI] =' not in _q_abc(mock_dax_capture)[-1]


def test_drawer_e_export_seguem_o_cliente(client, usuario_admin, mock_dax_capture, clean_redis, monkeypatch):
    """Tabela do cliente com drawer da empresa seria o defeito de dois universos."""
    _rotas(mock_dax_capture)
    _carteira_fake(monkeypatch)
    mock_dax_capture.routes = [
        ('CALENDARIO[AnoMes]', _payload([{'CALENDARIO[AnoMes]': 202607, '[Venda]': 100.0, '[Qt]': 10.0, '[Clientes]': 1}])),
        ('FATURAMENTO_VENDAS[CODUSUR],', _payload([{'FATURAMENTO_VENDAS[CODUSUR]': 573.0, '[Venda]': 100.0, '[Qt]': 10.0}])),
    ] + mock_dax_capture.routes
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    d = client.get('/api/abc/produto/1000?codcli=4242').get_json()
    assert d['ok'] and d['escopo'] == 'Cliente: PADARIA DO ZE'
    qd = [q for q in mock_dax_capture.queries if 'CODPROD] = 1000' in q]
    assert len(qd) == 2 and all('[CODCLI] = 4242' in q for q in qd)
    r = client.get('/api/abc/csv?codcli=4242')
    assert r.status_code == 200 and 'PADARIA' in r.headers.get('Content-Disposition', '')
    assert client.get('/api/abc/pdf?codcli=4242').status_code == 200


def test_abc_modo_postgres_cliente(client, usuario_admin, monkeypatch):
    _postgres(monkeypatch)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    import provider_sql
    conn = provider_sql.analytics_conn(); cur = conn.cursor()
    cur.execute("SELECT codcli FROM faturamento_vendas WHERE codoper='S' AND codcli IS NOT NULL "
                "GROUP BY 1 ORDER BY count(*) DESC LIMIT 1")
    cc = cur.fetchone()[0]
    cur.execute("SELECT codprod FROM faturamento_vendas WHERE codoper='S' AND codcli=%s "
                "GROUP BY 1 ORDER BY count(*) DESC LIMIT 1", (cc,))
    cp = cur.fetchone()[0]
    conn.close()
    tudo = client.get('/api/abc').get_json()
    d = client.get(f'/api/abc?codcli={cc}').get_json()
    assert d['ok'] and d['codcli'] == cc and d['escopo'].startswith('Cliente: ')
    assert 0 < d['resumo']['total_venda'] < tudo['resumo']['total_venda']
    assert d['total'] < tudo['total']
    assert all(r['clientes'] == 1 for r in d['rows'])        # a coluna vira constante (a tela a esconde)
    assert d['amostra_ok'] is True and d['regua']['cliente']
    dp = client.get(f'/api/abc/produto/{cp}?codcli={cc}').get_json()
    assert dp['ok'] and dp['serie'] and all(s['clientes'] <= 1 for s in dp['serie'])
    assert client.get(f'/api/abc/csv?codcli={cc}').status_code == 200


def test_front_tem_o_filtro_de_cliente_e_o_leva_no_escopo():
    html = open('abc.html', encoding='utf-8').read()
    assert 'id="filt_cli"' in html and '/api/_internal/clientes-busca' in html
    assert "if (_cliSel) qs += '&codcli=' + _cliSel;" in html, 'o cliente tem de viajar em TODA chamada (/api/abc, drawer, export)'
    assert "'filt_cli'" in html.split('function limparTudo')[1].split('\n}')[0], 'Limpar filtros tem de zerar o cliente'
