"""Cinco defeitos achados no levantamento para o Agente de IA do Comercial (25/09/2026) —
docs/comercial/IA_COMERCIAL_CONTEUDO.md §1. Cada gate trava a regra corrigida."""
import pytest

import cobertura as cob
from tests.conftest import login_as


# ── 1) "Clientes Novos" do Dashboard não pode ser a medida do BI (= total de clientes) ──
def test_kpis_nao_usam_a_medida_TOTAL_CLIENTES_NOVO(client, usuario_admin, mock_dax_capture, clean_redis):
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    client.get('/api/dashboard/kpis')
    q = next(x for x in mock_dax_capture.queries if '[TOTAL MIX]' in x)
    assert '[TOTAL CLIENTES NOVO]' not in q
    # novo = no mês E sem compra antes (empresa inteira) E sem 1ª compra anterior no cadastro
    assert 'EXCEPT' in q and 'DTPRIMCOMPRA' in q and 'ALL(FATURAMENTO_VENDAS)' in q


# ── 2) Alerta do cockpit: o valor é ACUMULADO, não "/ano" ──
def test_alerta_at_risk_fala_de_lucro_mensal_e_rotula_o_acumulado(client, usuario_admin, monkeypatch, clean_redis):
    import server
    carteira = [
        {'codcli': 1, 'codusur': 50, 'segmento': 'at_risk', 'lucro_12m': 1200.0, 'lucro_perdido_proj': 300.0},
        {'codcli': 2, 'codusur': 50, 'segmento': 'at_risk', 'lucro_12m': 2400.0, 'lucro_perdido_proj': 0.0},
        {'codcli': 3, 'codusur': 50, 'segmento': 'champions', 'lucro_12m': 9000.0, 'recencia_dias': 3},
    ]
    monkeypatch.setattr(server, '_carregar_carteira_full', lambda: carteira)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    a = client.get('/api/vendedor/50/alertas').get_json()
    at = next(x for x in a['alertas'] if x['tipo'] == 'at_risk')
    assert '/ano' not in at['msg']
    assert at['lucro_mensal_total'] == pytest.approx(300.0)      # (1200 + 2400) / 12
    assert 'R$ 300/mês' in at['msg'] and 'já deixaram de entrar' in at['msg']


# ── 3) Gerencial: só PESSOA entra em "abaixo do limiar" ──
def _cli(dias, **kw):
    base = {'recencia_dias': dias, 'venda_12m': 100.0, 'lucro_12m': 10.0, 'status_personalizada': 'ok',
            'receita_perdida_proj': 0.0, 'codusur': 1, 'vendedor': 'V1', 'codsupervisor': 10, 'time': 'T'}
    base.update(kw)
    return base


def test_abaixo_do_limiar_ignora_amostra_pequena_ficticio_e_canal():
    clientes = (
        [_cli(90, codusur=1, vendedor='PESSOA') for _ in range(6)]            # pessoa ruim → alerta
        + [_cli(90, codusur=2, vendedor='PEQUENO') for _ in range(2)]         # base < 5 → fora
        + [_cli(90, codusur=999, vendedor='RCA TRANSFERENCIA') for _ in range(6)]
        + [_cli(90, codusur=744, vendedor='E-COMMERCE') for _ in range(6)]
    )
    n = cob.agregar_niveis(clientes, coberto_dias=30)
    b = cob.times_rcas_abaixo(n, 60, ignorar={999, 744})
    assert [v['id'] for v in b['vendedores']] == [1]
    marc = {v['id']: v['alerta'] for v in n['vendedores']}
    assert marc == {1: True, 2: False, 999: False, 744: False}   # a tela usa o MESMO flag


def test_nao_pessoas_do_gerencial_pega_ficticio_e_commerce():
    import server
    niv = {'vendedores': [{'id': 751, 'nome': 'MARTINS'}, {'id': 744, 'nome': 'E-COMMERCE'}],
           'times': [{'id': 34, 'nome': 'E COMMERCE MARTINS'}]}
    ids = server._gerencial_nao_pessoas(niv)
    assert {744, 34, 999} <= ids and 751 not in ids


# ── 4) Próximo Pedido: a API sem janela usa a MESMA janela acionável da tela (até 15 d) ──
def test_proximo_pedido_janela_padrao_e_ate_15_dias(client, usuario_admin, monkeypatch, clean_redis):
    import server
    base = {'ciclo_pessoal': 7, 'proximo_pedido_previsto': '2026-09-01', 'codusur': 1, 'codsupervisor': 10,
            'segmento': 'loyal', 'status_personalizada': 'ok', 'status_fixa': 'ok', 'venda_12m': 100.0,
            'lucro_12m': 10.0, 'receita_perdida_proj': 0.0, 'lucro_perdido_proj': 0.0, 'prioridade_contato': 1.0,
            'recencia_dias': 10, 'frequencia_12m': 5, 'cliente': 'X', 'uf': 'ES', 'cidade': 'C'}
    carteira = [{**base, 'codcli': 1, 'dias_atraso': 142}, {**base, 'codcli': 2, 'dias_atraso': 10}]
    monkeypatch.setattr(server, '_carteira_no_escopo', lambda: carteira)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    d = client.get('/api/carteira/proximo-pedido').get_json()
    assert d['janela'] == 'vencido15'
    assert [r['codcli'] for r in d['rows']] == [2]


# ── 5) Cadastro genérico (CONSUMIDOR FINAL) fora da análise por cliente ──
def test_eh_cliente_generico():
    import server
    assert server._eh_cliente_generico('CONSUMIDOR FINAL')
    assert server._eh_cliente_generico('  consumidor final lojas')
    assert not server._eh_cliente_generico('INSTITUTO EST.DE PROT. E DEF. CONSUMIDOR')
    assert not server._eh_cliente_generico(None)


def test_carteira_tira_o_generico_e_guarda_a_lista(monkeypatch, clean_redis):
    import server
    monkeypatch.setattr(server, '_carregar_vendedores_map', lambda: {})
    monkeypatch.setattr(server, '_carregar_supervisores_map', lambda: {})
    monkeypatch.setattr(server, '_normalizar_cidades', lambda cs: {})
    clientes = [{'codcli': 1, 'cliente': 'CONSUMIDOR FINAL', 'codusur': 4},
                {'codcli': 7, 'cliente': 'MERCADO BOM', 'codusur': 10}]
    out = server._finalizar_carteira(clientes, 'teste:carteira')
    assert [c['codcli'] for c in out] == [7]
    assert server._cache_get('multpel:clientes_genericos:v1') == [1]
