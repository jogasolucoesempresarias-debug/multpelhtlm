"""Positivação por RCA (positivacao.py) — régua acordada com o João Victor em 24/09/2026.

Base ativa = cadastro (CODUSUR1) com compra em 12m; cobertura = base atendida POR ELE no mês
fechado ÷ base (0..1); fora da base e alcance são informativos. Os gates travam os dois
defeitos da conta antiga (Patch L): universos diferentes nas duas pontas (37 de 97 RCAs acima
de 100%) e o cadastro inteiro — com bloqueados e clientes mortos — como denominador.
"""
from datetime import date

import pytest

import positivacao as pos


# ───────────────────────── calendário ─────────────────────────
def test_mes_fechado_vira_o_ano():
    assert pos.mes_fechado(date(2026, 1, 15)) == 202512
    assert pos.mes_fechado(date(2026, 9, 24)) == 202608


def test_mes_add():
    assert pos.mes_add(202608, -11) == 202509
    assert pos.mes_add(202612, 1) == 202701
    assert pos.mes_add(202601, -1) == 202512


# ───────────────────────── base ativa ─────────────────────────
def test_base_ativa_e_12_meses_inclusive_e_por_cadastro():
    venda = {
        1: {202608: 100},            # comprou no mês de referência
        2: {202509: 50},             # 12º mês da janela → entra
        3: {202508: 80},             # 13º mês → fora (cliente morto não conta)
        4: {202607: 10},             # dono é outro RCA
        5: {202608: 0},              # venda zero não é compra
        6: {202608: 30},             # sem RCA no cadastro → fora
    }
    dono = {1: 10, 2: 10, 3: 10, 4: 20, 5: 10}
    b = pos.base_ativa(venda, dono, 202608)
    assert b == {10: {1, 2}, 20: {4}}


# ───────────────────────── placar ─────────────────────────
def _placar(bases, atendidos, dono, ficticios=(999,), ativos=(10, 20, 30)):
    return pos.positivacao_por_rca(bases, atendidos, dono, ficticios, ativos)


def test_cobertura_conta_so_a_base_atendida_POR_ELE():
    bases = {10: {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}}
    # 1..4 atendidos por ele; 5 e 6 comprados de OUTRO vendedor — não cobrem a base dele
    atend = {10: {1, 2, 3, 4}, 20: {5, 6}}
    p = _placar(bases, atend, {c: 10 for c in range(1, 11)})
    assert p[10]['cobertos'] == 4
    assert p[10]['cobertura'] == pytest.approx(0.4)


def test_cobertura_NUNCA_passa_de_100_mesmo_atendendo_outras_bases():
    """O defeito do Patch L: numerador de VENDA sobre denominador de CADASTRO passava de 100%.
    Aqui o que vem de fora vai para `fora_base`/`alcance`, nunca para a cobertura."""
    bases = {10: set(range(1, 11)), 20: set(range(100, 150))}
    atend = {10: set(range(1, 11)) | set(range(100, 140))}   # base toda + 40 de outra base
    dono = {**{c: 10 for c in range(1, 11)}, **{c: 20 for c in range(100, 150)}}
    p = _placar(bases, atend, dono)
    assert p[10]['cobertura'] == 1.0
    assert p[10]['fora_base'] == 40
    assert p[10]['alcance'] == pytest.approx(5.0)   # informativo: pode passar de 100%


def test_fora_da_base_separa_o_tipo_do_dono():
    """Cliente de código fictício ou de RCA inativo sendo atendido é CADASTRO a transferir
    (caso VALDELI × MATEUS em ago/26); cliente com mais de 60 dias sem comprar está LIBERADO
    pela regra comercial; só o resto é "cliente ativo de colega"."""
    bases = {10: set(range(1, 6))}
    atend = {10: set(range(1, 6)) | {101, 102, 103, 201, 301, 302, 303}}
    dono = {**{c: 10 for c in range(1, 6)}, 101: 20, 102: 20, 103: 20, 201: 999, 301: 77, 302: 77}
    # 303 não tem dono no cadastro → inativo (não há quem cuide dele)
    # 103 é de colega ATIVO mas estava liberado; 201 é de fictício E liberado → fictício vence
    p = pos.positivacao_por_rca(bases, atend, dono, (999,), (10, 20), {10: {103, 201}})
    assert p[10]['fora_por_tipo'] == {'colega': 2, 'liberado': 1, 'ficticio': 1, 'rca_inativo': 3}
    assert p[10]['fora_base'] == 7


# ───────────────────────── regra comercial dos 60 dias ─────────────────────────
def test_liberado_quando_passou_de_60_dias_sem_comprar():
    d = date
    primeira = {20: {1: d(2026, 8, 20), 2: d(2026, 8, 20), 3: d(2026, 8, 20)}}
    ultima = {1: d(2026, 6, 20),     # 61 dias antes → liberado
              2: d(2026, 6, 21)}     # 60 dias → ainda do dono (a regra é MAIS de 60)
    # 3 nunca comprou em 24m → ninguém cuida dele → liberado
    assert pos.liberados_no_mes(primeira, ultima) == {20: {1, 3}}


def test_nao_esta_liberado_se_o_dono_vendeu_antes_no_mesmo_mes():
    """Se o dono atendeu dia 3, quem vende dia 20 não pegou cliente parado."""
    d = date
    primeira = {10: {1: d(2026, 8, 3)}, 20: {1: d(2026, 8, 20)}}
    ultima = {1: d(2026, 1, 10)}
    lib = pos.liberados_no_mes(primeira, ultima)
    assert 1 in lib.get(10, set())        # o próprio dono reativou um cliente parado
    assert 1 not in lib.get(20, set())


def test_base_pequena_nao_e_parametro():
    """Caixa/balcão (LARISSA, base 0; WAGNER, base 1): sem cobertura nem alcance, mas o que
    ele atendeu continua visível."""
    bases = {10: {1, 2}}
    atend = {10: {1}, 30: {7, 8, 9}}
    p = _placar(bases, atend, {1: 10, 2: 10, 7: 20, 8: 20, 9: 20})
    assert p[10]['amostra_pequena'] and p[10]['cobertura'] is None and p[10]['alcance'] is None
    assert p[30]['base_ativa'] == 0 and p[30]['cobertura'] is None
    assert p[30]['atendidos'] == 3 and p[30]['fora_base'] == 3


def test_limite_da_amostra_e_inclusivo_em_5():
    bases = {10: set(range(5))}
    p = _placar(bases, {10: {0}}, {c: 10 for c in range(5)})
    assert p[10]['amostra_pequena'] is False
    assert p[10]['cobertura'] == pytest.approx(0.2)


def test_rca_com_base_sem_atendimento_aparece_com_zero():
    bases = {10: set(range(10))}
    p = _placar(bases, {}, {c: 10 for c in range(10)})
    assert p[10]['cobertura'] == 0.0 and p[10]['fora_base'] == 0


# ───────────────────────── integração com a tela Vendedores ─────────────────────────
def test_ranking_usa_a_cobertura_e_NAO_consulta_o_cadastro_inteiro(
        client, usuario_admin, mock_dax_capture, clean_redis, monkeypatch):
    import json
    import pathlib
    import server
    from tests.conftest import login_as

    fx = pathlib.Path(__file__).resolve().parent / 'fixtures'
    load = lambda n: json.load(open(fx / f'{n}.json', encoding='utf-8'))
    mock_dax_capture.set_routes([
        ('PCUSUARI[CODUSUR]', load('dax_vendedores_map')),
        ('VendaLiqAnt',       load('dax_vendedores_anterior')),
        ('TicketMedio',       load('dax_vendedores_metricas')),
        ('VendaLiq',          load('dax_vendedores_ranking')),
    ])
    placar = {
        573: {'base_ativa': 100, 'cobertos': 59, 'cobertura': 0.59, 'fora_base': 4,
              'fora_por_tipo': {'colega': 1, 'liberado': 1, 'ficticio': 1, 'rca_inativo': 1},
              'atendidos': 63, 'alcance': 0.63, 'amostra_pequena': False},
        100: {'base_ativa': 1, 'cobertos': 0, 'cobertura': None, 'fora_base': 12,
              'fora_por_tipo': {'colega': 12, 'liberado': 0, 'ficticio': 0, 'rca_inativo': 0},
              'atendidos': 12, 'alcance': None, 'amostra_pequena': True},
    }
    monkeypatch.setattr(server, '_positivacao_rcas', lambda: (202608, placar))
    login_as(client, usuario_admin['email'], usuario_admin['senha'])

    d = client.get('/api/vendedores?tipovend=R').get_json()
    por = {v['codusur']: v for v in d['vendedores']}
    assert por[573]['taxa_positivacao'] == pytest.approx(0.59)
    assert por[573]['base_ativa'] == 100 and por[573]['base_coberta'] == 59
    assert por[573]['fora_base_tipo'] == {'colega': 1, 'liberado': 1, 'ficticio': 1, 'rca_inativo': 1}
    assert por[573]['positivacao_mes'] == 202608
    assert por[100]['taxa_positivacao'] is None and por[100]['positivacao_amostra_pequena']
    # RCA sem placar (820): sem base → None, nunca 0% inventado
    assert por[820]['taxa_positivacao'] is None and por[820]['base_ativa'] == 0
    assert all(v['taxa_positivacao'] is None or v['taxa_positivacao'] <= 1 for v in d['vendedores'])
    assert not any('CarteiraOficial' in q or 'PCCLIENT[CODUSUR1]' in q
                   for q in mock_dax_capture.queries)


def test_media_da_equipe_ignora_base_pequena(monkeypatch):
    """Um caixa com 1 cliente (0% ou 100%) não pode puxar a média do time no cockpit."""
    import server
    monkeypatch.setattr(server, '_carregar_vendedores_map',
                        lambda: {'1': {'codsupervisor': 18}})
    monkeypatch.setattr(server, '_carregar_ranking_vendedores', lambda: [
        {'codusur': 2, 'codsupervisor': 18, 'taxa_positivacao': 0.60, 'positivacao_amostra_pequena': False},
        {'codusur': 3, 'codsupervisor': 18, 'taxa_positivacao': 0.0, 'positivacao_amostra_pequena': True},
        {'codusur': 4, 'codsupervisor': 18, 'taxa_positivacao': None, 'positivacao_amostra_pequena': True},
    ])
    ce = server._comparativo_equipe(1, 0.5)
    assert ce['media_equipe'] == pytest.approx(0.60)
