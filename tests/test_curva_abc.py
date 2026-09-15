"""Gate da aba Curva ABC (Comercial) — motor puro, endpoints com DAX mockado e modo postgres.

O que trava:
- a régua é a MESMA do Compras (`estoque.core._aplicar_curva`), para o app não ter duas curvas A;
- o escopo é de VENDA e a querystring NÃO amplia o escopo de quem não é admin;
- o export sai com o filtro da tela (classe/depto/fornecedor/busca), não com o universo;
- amostra pequena AVISA em vez de esconder;
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
    return [{'FATURAMENTO_VENDAS[CODPROD]': 1000 + i, '[Venda]': round(rnd.expovariate(1 / 2000), 2) + 1,
             '[Clientes]': rnd.randint(1, 50)} for i in range(n)]


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
    assert {'codprod', 'descricao', 'depto_nome', 'fornec_nome', 'venda', 'clientes',
            'rank', 'pct', 'pct_acum', 'classe'} <= set(d['rows'][0])
    assert d['regua']['corte_a'] == 80 and d['regua']['meses'] == 12
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

    # drawer + exports no modo BD
    dp = client.get(f'/api/abc/produto/{cp}?supervisor={sup}').get_json()
    assert dp['ok'] and dp['serie'] and dp['vendedores']
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
