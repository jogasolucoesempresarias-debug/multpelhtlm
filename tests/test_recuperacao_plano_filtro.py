"""Filtro por PLANO DE AÇÃO nas listas da Recuperação (pedido do Gabriel, 06/10/2026: "o supervisor
precisa saber quais clientes precisam ser transferidos, por exemplo").

O que trava:
- o filtro é aplicado NO SERVIDOR, antes do corte de 300 linhas da lista — filtrar só na tela
  esconderia em silêncio os clientes marcados que estão depois da 300ª linha;
- valores: `sem` (sem plano atual) · `com` (qualquer plano) · o código do último status
  (`transferir`, `retorno_agendado`…). Valor desconhecido = sem filtro (não esvazia a lista);
- o total informado é o do conjunto FILTRADO;
- o CSV sai com o mesmo filtro e com as colunas do plano (último status e data).
"""
import csv
import io
from pathlib import Path

import pytest

from tests.test_plano_cliente import _limpar, pg, supervisor12  # noqa: F401 (fixtures)


def _primeiro_em_risco(client):
    j = client.get('/api/recuperacao/listas?tipo=risco&limit=2000').get_json()
    assert j['ok'] and j['rows'], 'sem clientes em risco no escopo da demo'
    return j, j['rows'][0]['codcli']


def test_filtro_transferir_traz_so_os_marcados(client, supervisor12, pg):  # noqa: F811
    server = pg
    todos, cc = _primeiro_em_risco(client)
    _limpar(server, [cc])
    try:
        assert client.post(f'/api/plano/{cc}', json={'status': 'transferir'}).status_code == 200
        j = client.get('/api/recuperacao/listas?tipo=risco&plano=transferir&limit=2000').get_json()
        assert [r['codcli'] for r in j['rows']] == [cc] and j['total'] == 1
        assert j['rows'][0]['plano']['ultimo']['status'] == 'transferir'
        sem = client.get('/api/recuperacao/listas?tipo=risco&plano=sem&limit=2000').get_json()
        assert cc not in {r['codcli'] for r in sem['rows']}
        com = client.get('/api/recuperacao/listas?tipo=risco&plano=com&limit=2000').get_json()
        assert cc in {r['codcli'] for r in com['rows']}
        assert sem['total'] + com['total'] == todos['total']           # partição sem buraco
        # valor desconhecido não esvazia a lista
        lixo = client.get('/api/recuperacao/listas?tipo=risco&plano=xpto&limit=2000').get_json()
        assert lixo['total'] == todos['total']
    finally:
        _limpar(server, [cc])


def test_filtro_vale_antes_do_limite(client, supervisor12, pg):  # noqa: F811
    server = pg
    todos, _ = _primeiro_em_risco(client)
    ultimo = todos['rows'][-1]['codcli']                              # o último da lista inteira
    _limpar(server, [ultimo])
    try:
        client.post(f'/api/plano/{ultimo}', json={'status': 'transferir'})
        j = client.get('/api/recuperacao/listas?tipo=risco&plano=transferir&limit=1').get_json()
        assert [r['codcli'] for r in j['rows']] == [ultimo]
    finally:
        _limpar(server, [ultimo])


def test_csv_honra_o_filtro_e_traz_o_plano(client, supervisor12, pg):  # noqa: F811
    server = pg
    _, cc = _primeiro_em_risco(client)
    _limpar(server, [cc])
    try:
        client.post(f'/api/plano/{cc}', json={'status': 'transferir', 'descricao': 'para o 1422'})
        r = client.get('/api/recuperacao/listas/csv?tipo=risco&plano=transferir')
        texto = r.get_data(as_text=True).lstrip('﻿')
        linhas = [ln for ln in texto.splitlines() if ln and not ln.startswith('sep=')]
        rows = list(csv.reader(io.StringIO('\n'.join(linhas)), delimiter=';'))
        cab = rows[0]
        assert 'Plano (último)' in cab and 'Data do plano' in cab
        assert len(rows) == 2 and rows[1][0] == str(cc)
        assert rows[1][cab.index('Plano (último)')] == 'Transferir'
    finally:
        _limpar(server, [cc])


def test_tela_tem_o_filtro():
    h = Path('recuperacao.html').read_text(encoding='utf-8')
    assert 'id="selPlano"' in h and "plano:_plano" in h
    for status in ('transferir', 'retorno_agendado', 'nao_compra_mais', 'sem', 'com'):
        assert f'value="{status}"' in h, status
