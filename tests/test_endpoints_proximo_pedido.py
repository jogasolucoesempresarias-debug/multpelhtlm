"""Aba Próximo Pedido: filtro de janela (pure) + smoke dos endpoints.
Carteira mockada via _carteira_no_escopo → determinístico, sem depender de datas reais."""
import server
from tests.conftest import login_as


def _cli(codcli, ciclo, dias_atraso, prioridade, venda=10000, codusur=573):
    """Monta um dict de cliente no formato da carteira enriquecida (campos usados pelos endpoints)."""
    recencia = (ciclo + dias_atraso) if (ciclo is not None and dias_atraso is not None) else 0
    return {
        'codcli': codcli, 'cliente': f'Cliente {codcli}', 'cidade': 'ILHEUS', 'uf': 'BA',
        'codusur': codusur, 'codsupervisor': 19, 'telefone': '7399999', 'vendedor': 'JOAO',
        'time': 'TIME', 'segmento': 'loyal',
        'status_personalizada': 'urgente' if (dias_atraso or 0) > 0 else 'normal',
        'status_fixa': 'normal',
        'venda_12m': venda, 'lucro_12m': venda * 0.2,
        'lucro_perdido_proj': 0, 'receita_perdida_proj': max(0, (dias_atraso or 0)) * 10,
        'recencia_dias': recencia, 'frequencia_12m': 12,
        'ciclo_pessoal': ciclo, 'dias_atraso': dias_atraso,
        'proximo_pedido_previsto': None if ciclo is None else '2026-06-20',
        'prioridade_contato': prioridade,
    }


def _carteira():
    return [
        _cli(1, ciclo=10, dias_atraso=0,  prioridade=100),   # vence hoje
        _cli(2, ciclo=7,  dias_atraso=5,  prioridade=200),   # atrasado
        _cli(3, ciclo=30, dias_atraso=-3, prioridade=0),     # dentro do ciclo (não vencido)
        _cli(4, ciclo=None, dias_atraso=None, prioridade=0), # sem ciclo (1 compra) → fora sempre
    ]


# ── filtro puro ──
def test_clientes_proximo_pedido_exclui_sem_ciclo_e_nao_vencidos():
    out = server._clientes_proximo_pedido(_carteira(), 'vencidos')
    cods = {c['codcli'] for c in out}
    assert cods == {1, 2}          # 3 (dentro do ciclo) e 4 (sem ciclo) fora


def test_clientes_proximo_pedido_janelas():
    cart = _carteira()
    assert {c['codcli'] for c in server._clientes_proximo_pedido(cart, 'hoje')} == {1}
    assert {c['codcli'] for c in server._clientes_proximo_pedido(cart, 'atrasados')} == {2}
    assert {c['codcli'] for c in server._clientes_proximo_pedido(cart, 'proximos', 3)} == {1, 2, 3}
    # vencido15 = hoje (da=0) + vencidos 1..15 → inclui c1 (da=0) e c2 (da=5); c3 (da=-3) fora
    assert {c['codcli'] for c in server._clientes_proximo_pedido(cart, 'vencido15')} == {1, 2}


# ── endpoint /api/carteira/proximo-pedido ──
def test_endpoint_proximo_pedido_ordena_por_prioridade(client, usuario_admin, clean_redis, monkeypatch):
    monkeypatch.setattr(server, '_carteira_no_escopo', lambda: _carteira())
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    d = client.get('/api/carteira/proximo-pedido?janela=vencidos').get_json()
    assert d['ok']
    assert d['total'] == 2
    # ordenado por prioridade desc → cliente 2 (200) antes do 1 (100)
    assert [r['codcli'] for r in d['rows']] == [2, 1]
    # cards: c1 (da=0) hoje; c3 (da=-3) próximos 7; c2 (da=5) vencido 1-15; c4 sem ciclo fora.
    # Receita/oportunidade sobre os acionáveis (da 0-15): c1, c2.
    cards = d['cards']
    assert cards['hoje'] == 1
    assert cards['proximos7'] == 1                 # c3, da=-3
    assert cards['vencido15'] == 1                 # c2, da=5 (1..15)
    assert cards['receita_risco'] == 50.0          # c1=0 + c2=5*10
    assert cards['maior_oportunidade']['codcli'] == 2  # maior prioridade


def test_endpoint_proximo_pedido_filtra_vendedor(client, usuario_admin, clean_redis, monkeypatch):
    cart = _carteira() + [_cli(9, ciclo=10, dias_atraso=2, prioridade=50, codusur=999)]
    monkeypatch.setattr(server, '_carteira_no_escopo', lambda: cart)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    d = client.get('/api/carteira/proximo-pedido?janela=vencidos&vendedor=999').get_json()
    assert d['total'] == 1
    assert d['rows'][0]['codcli'] == 9


# ── endpoint de produtos (lazy) ──
def _pl(rows):
    return {'results': [{'tables': [{'rows': rows}]}]}


def test_endpoint_produtos_cliente(client, usuario_admin, clean_redis, monkeypatch):
    monkeypatch.setattr(server, '_carteira_no_escopo', lambda: _carteira())
    monkeypatch.setattr(server, 'get_token_cached', lambda: 'fake')
    monkeypatch.setattr(server, 'execute_dax', lambda tok, q, dataset_id=None: _pl([
        {'FATURAMENTO_VENDAS[CODPROD]': 10, 'PCPRODUT[DESCRICAO]': 'COPO 200ML', '[Venda]': 5000, '[Qt]': 100},
        {'FATURAMENTO_VENDAS[CODPROD]': 20, 'PCPRODUT[DESCRICAO]': 'TOALHA',     '[Venda]': 3000, '[Qt]': 50},
    ]))
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    r = client.get('/api/carteira/cliente/1/produtos?limit=10')
    assert r.status_code == 200
    d = r.get_json()
    assert d['ok'] and len(d['produtos']) == 2
    assert d['produtos'][0]['descricao'] == 'COPO 200ML'


def test_endpoint_produtos_cliente_fora_do_escopo_404(client, usuario_admin, clean_redis, monkeypatch):
    monkeypatch.setattr(server, '_carteira_no_escopo', lambda: _carteira())
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    assert client.get('/api/carteira/cliente/99999/produtos').status_code == 404


def test_proximo_pedido_exige_login(client, clean_redis):
    assert client.get('/api/carteira/proximo-pedido').status_code == 401
