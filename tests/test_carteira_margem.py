"""Margem 12m do cliente na tabela da Carteira (pedido do lead na apresentação de 03/10/2026).

Régua do Comercial: LUCRO 12m ÷ VENDA LÍQUIDA 12m — a mesma do Dashboard e da Curva ABC. A Carteira
não tem seletor de período: tudo nela é 12m móveis, então "a margem do período filtrado" é a margem
12m recortada pelos filtros da tela. Zero query: o `lucro_12m` já viaja na carteira (é o M do RFM).

O que trava:
- a conta e o "sem margem" (venda ≤ 0 → None, nunca 0% nem percentual absurdo);
- a margem viaja em toda linha do `_filtrar_carteira` e ordena — com "sem margem" sempre no FIM,
  nos dois sentidos (senão o desc abre a tabela com uma página de "—");
- a coluna está na tela, no CSV e no PDF (filtro/coluna de tela tem de viajar no export).
"""
from pathlib import Path

import pytest

import rfm
import server
from tests.conftest import login_as


def _cli(codcli, venda, lucro, seg='loyal'):
    return {'codcli': codcli, 'cliente': f'CLIENTE {codcli}', 'uf': 'ES', 'cidade': 'VITORIA',
            'segmento': seg, 'recencia_dias': 5, 'frequencia_12m': 3, 'venda_12m': venda,
            'lucro_12m': lucro, 'receita_perdida_proj': 0.0, 'lucro_perdido_proj': 0.0,
            'codusur': 1, 'codsupervisor': 10, 'vendedor': 'RCA 1', 'time': 'T'}


CLIENTES = [
    _cli(1, 1000.0, 250.0),     # 25%
    _cli(2, 400.0, 20.0),       # 5%
    _cli(3, 0.0, 0.0),          # sem venda → sem margem
    _cli(4, 500.0, -50.0),      # -10% (vendeu abaixo do custo: informação, não erro)
    _cli(5, -80.0, -10.0),      # só devolução → sem margem (não 12,5%)
]


# ───────────────────────── a conta ─────────────────────────
def test_margem_e_lucro_sobre_venda_liquida():
    assert rfm.margem(250.0, 1000.0) == pytest.approx(0.25)
    assert rfm.margem(-50.0, 500.0) == pytest.approx(-0.10)


@pytest.mark.parametrize('lucro,venda', [(0.0, 0.0), (-10.0, -80.0), (5.0, None), (None, None)])
def test_sem_venda_positiva_nao_tem_margem(lucro, venda):
    assert rfm.margem(lucro, venda) is None


def test_lucro_ausente_com_venda_e_margem_zero():
    assert rfm.margem(None, 100.0) == 0.0


# ───────────────────────── tabela ─────────────────────────
def test_toda_linha_filtrada_carrega_a_margem():
    rows = server._filtrar_carteira(CLIENTES, {'limit': 100})['rows']
    m = {r['codcli']: r['margem_12m'] for r in rows}
    assert m[1] == pytest.approx(0.25) and m[2] == pytest.approx(0.05) and m[4] == pytest.approx(-0.10)
    assert m[3] is None and m[5] is None


def test_margem_respeita_os_filtros_da_tela():
    rows = server._filtrar_carteira([*CLIENTES, _cli(9, 100.0, 30.0, seg='lost')],
                                    {'segmento': 'lost', 'limit': 100})['rows']
    assert [(r['codcli'], r['margem_12m']) for r in rows] == [(9, pytest.approx(0.30))]


@pytest.mark.parametrize('direcao,esperado', [('desc', [1, 2, 4, 3, 5]), ('asc', [4, 2, 1, 3, 5])])
def test_ordena_por_margem_com_sem_margem_sempre_no_fim(direcao, esperado):
    rows = server._filtrar_carteira(CLIENTES, {'sort': 'margem', 'dir': direcao, 'limit': 100})['rows']
    ordem = [r['codcli'] for r in rows]
    assert ordem[:3] == esperado[:3]
    assert set(ordem[3:]) == {3, 5}


def test_tela_tem_a_coluna_ordenavel():
    html = Path('carteira.html').read_text(encoding='utf-8')
    assert 'data-sort="margem"' in html and "ordenarPor('margem')" in html
    assert 'margem_12m' in html                                  # a linha lê o campo do servidor


# ───────────────────────── export ─────────────────────────
def test_csv_leva_a_margem(client, usuario_admin, clean_redis, monkeypatch):
    monkeypatch.setattr(server, '_carteira_no_escopo', lambda *a, **k: [dict(c) for c in CLIENTES])
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    r = client.get('/api/carteira/csv?sort=venda_12m&dir=desc')
    assert r.status_code == 200
    linhas = [ln for ln in r.get_data(as_text=True).lstrip('﻿').splitlines()
              if not ln.startswith('sep=')]
    cab = linhas[0].split(';')
    assert 'Margem12m(%)' in cab
    i = cab.index('Margem12m(%)')
    por_cod = {ln.split(';')[0]: ln.split(';')[i] for ln in linhas[1:]}
    assert por_cod['1'] in ('25,0', '25.0') and por_cod['3'] == ''


def test_pdf_leva_a_margem():
    header, data = server._linhas_pdf_carteira([dict(c) for c in CLIENTES])
    assert 'Margem 12m' in header
    i = header.index('Margem 12m')
    por_cod = {row[0]: row[i] for row in data}
    assert por_cod[1] == '25,0%' and por_cod[4] == '-10,0%' and por_cod[3] == '—'
    assert server._gerar_pdf_carteira([dict(c) for c in CLIENTES])[:4] == b'%PDF'
