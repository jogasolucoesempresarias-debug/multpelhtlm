"""Radar: SUPERVISOR também filtra por time/vendedor — só dentro do próprio escopo (18/11/2026).

Pedido do João: os filtros Supervisor/Vendedor do Radar não apareciam para o supervisor. A regra é
a da Curva ABC e da Recuperação (`_abc_supervisores_filtro`/`_abc_vendedor_filtro`): admin/viewer
escolhe qualquer um; supervisor só ESTREITA (um RCA do time dele); código de fora é ignorado — a
querystring nunca amplia. Fixtures: sup 18 = RCAs 573 (clientes 1 e 3) e 100 (cliente 2); o RCA
820 (cliente 4) é do sup 99.
"""
import pathlib

from tests.conftest import login_as
from tests.test_regua_ciclo_harmonia import _radar_produto_routes

RADAR_HTML = (pathlib.Path(__file__).resolve().parent.parent / 'radar.html').read_text(encoding='utf-8')


def _clientes(client, qs=''):
    d = client.get(f'/api/radar/produto/900001?dias=60{qs}').get_json()
    assert d['ok'], d
    return {r['codcli'] for r in d['rows']}


def test_supervisor_estreita_pelo_vendedor_do_proprio_time(client, usuario_supervisor, mock_dax_capture, clean_redis):
    mock_dax_capture.set_routes(_radar_produto_routes())
    login_as(client, usuario_supervisor['email'], usuario_supervisor['senha'])
    assert _clientes(client) == {1, 2, 3}                    # escopo do sup 18 (o 4 é de outro time)
    assert _clientes(client, '&vendedor=573') == {1, 3}      # estreita para um RCA dele
    assert _clientes(client, '&vendedor=100') == {2}         # o cache não mistura os filtros


def test_supervisor_nao_amplia_com_vendedor_de_outro_time(client, usuario_supervisor, mock_dax_capture, clean_redis):
    mock_dax_capture.set_routes(_radar_produto_routes())
    login_as(client, usuario_supervisor['email'], usuario_supervisor['senha'])
    assert _clientes(client, '&vendedor=820') == {1, 2, 3}   # RCA do sup 99: ignorado
    assert _clientes(client, '&supervisor=99') == {1, 2, 3}  # time de fora: ignorado


def test_board_do_supervisor_filtrado_pelo_vendedor(client, usuario_supervisor, mock_dax_capture, clean_redis):
    mock_dax_capture.set_routes(_radar_produto_routes())
    login_as(client, usuario_supervisor['email'], usuario_supervisor['senha'])
    client.get('/api/radar/board?dias=60&vendedor=573')
    q = next(q for q in mock_dax_capture.queries if '"VendaRec"' in q and 'CliRec' in q)
    assert 'FATURAMENTO_VENDAS[CODCLI] IN {1, 3}' in q


def test_vendedor_continua_sem_filtro(client, usuario_vendedor, mock_dax_capture, clean_redis):
    mock_dax_capture.set_routes(_radar_produto_routes())
    login_as(client, usuario_vendedor['email'], usuario_vendedor['senha'])
    assert _clientes(client, '&vendedor=100') == {1, 3}      # o 573 só vê os dele, sempre


def test_tela_mostra_os_filtros_ao_supervisor():
    assert "_role === 'supervisor' && meusTimes.length" in RADAR_HTML
    assert 'meusTimes.includes(String(v.codsupervisor))' in RADAR_HTML
