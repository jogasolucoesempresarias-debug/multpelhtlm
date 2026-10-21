"""Gate dos clientes EXCLUÍDOS no Winthor (10/2026, pedido do João Victor).

"Cliente 123770 não está na base do vendedor cod 29 mais, porém aparece aqui na JOGA... no Winthor
está excluído, não deveria aparecer. No sistema de força de vendas do Juliano tem 41 clientes
ativos, na JOGA tem 46; tirando os 5 citados, bate certinho."

Os 5 foram excluídos em 26/06/2026 como "CLIENTE NAO COMPRA MAIS" e seguiam com CODUSUR1 = 29.
Medido em 20/10/2026: 16.795 excluídos no cadastro, 223 na carteira de 24 meses, 39 na base de
cobertura — no JULIANO, 4 de 44, o que punha teto de ~90,9% numa meta de 100%.

O que estes testes protegem:
  · a RÉGUA NÃO MUDA (decisão do Gabriel): dono = cadastro; o excluído só sai da base;
  · ele sai PELA DATA: conta na base até a exclusão, e o passado não se reescreve;
  · na Recuperação ele vira PERDIDO na data, e a ponte segue fechando no zero;
  · ele não vira "dinheiro na mesa";
  · BI fora → nada cai; o modo BD (demo) tem espelho.
"""
import inspect
from datetime import date

import pytest

import cobertura as cob
import recuperacao as recup
import server

JUN26 = date(2026, 6, 26)


# ─────────────────────── Recuperação: excluído = perdido na data ───────────────────────
def _hist(*datas, excluido=None):
    h = recup.indexar([(1, d, 29, 100.0) for d in datas])
    if excluido:
        recup.marcar_excluidos(h, {1: excluido})
    return h[1]


def test_antes_da_exclusao_vale_a_regua_de_sempre():
    h = _hist(date(2026, 3, 17), excluido=JUN26)
    assert recup.estado_em(h, date(2026, 6, 1))['estado'] == recup.RISCO
    assert recup.estado_em(h, JUN26)['estado'] == recup.RISCO      # no próprio dia ainda não


def test_depois_da_exclusao_e_PERDIDO_mesmo_com_menos_de_365_dias():
    h = _hist(date(2026, 3, 17), excluido=JUN26)
    assert recup.estado_em(h, date(2026, 6, 27))['estado'] == recup.PERDIDO


def test_compra_DEPOIS_da_exclusao_volta_a_regua_normal():
    """Os 2 casos de 2025: excluído e vendido dias depois pela loja/e-commerce."""
    h = _hist(date(2025, 6, 1), date(2025, 9, 3), excluido=date(2025, 8, 30))
    assert recup.estado_em(h, date(2025, 9, 1))['estado'] == recup.PERDIDO
    assert recup.estado_em(h, date(2025, 9, 10))['estado'] == recup.ATIVO
    m = recup.movimento_cliente(h, 202509)
    assert m['recuperado'] and m['recuperado_de'] == recup.PERDIDO   # resgatado, não do risco


def test_em_risco_e_excluido_no_mes_sai_pelo_viraram_perdidos_e_a_ponte_fecha():
    hist = recup.indexar([(1, date(2026, 3, 17), 29, 100.0), (2, date(2026, 3, 20), 29, 50.0),
                          (3, date(2026, 6, 10), 29, 80.0)])
    recup.marcar_excluidos(hist, {1: JUN26})
    p = recup.ponte(hist, 202606)['clientes']
    assert p['viraram_perdidos'] == 1 and p['risco_fim'] == 1          # o 2 segue em risco
    assert p['risco_fim'] == p['risco_ini'] + p['entraram'] - p['recuperados'] - p['viraram_perdidos']


def test_comprou_e_foi_excluido_no_MESMO_mes_sai_uma_vez_so():
    """Achado no BI real (4 clientes, out/25–abr/26): em risco no início, comprou no dia 5 e foi
    excluído no dia 20. Saía pelos recuperados E pelos viraram perdidos e a ponte errava em 1."""
    hist = recup.indexar([(1, date(2026, 2, 1), 29, 100.0), (1, date(2026, 6, 5), 29, 100.0)])
    recup.marcar_excluidos(hist, {1: date(2026, 6, 20)})
    m = recup.movimento_cliente(hist[1], 202606)
    assert (m['ini'], m['fim'], m['recuperado']) == (recup.RISCO, recup.PERDIDO, True)
    assert not m['virou_perdido']
    p = recup.ponte(hist, 202606)['clientes']
    assert p['risco_fim'] == p['risco_ini'] + p['entraram'] - p['recuperados'] - p['viraram_perdidos']


def test_ativo_excluido_nao_passa_pelo_risco():
    hist = recup.indexar([(1, date(2026, 6, 1), 29, 100.0)])
    recup.marcar_excluidos(hist, {1: JUN26})
    assert 1 not in recup.movimentos(hist, 202606)
    assert recup.em_risco_em(hist, date(2026, 10, 20)) == []           # some da lista de risco


def test_sem_marca_nada_muda():
    hist = recup.indexar([(1, date(2026, 3, 17), 29, 100.0)])
    assert recup.estado_em(hist[1], date(2026, 6, 27))['estado'] == recup.RISCO


# ─────────────────────── a base pela data ───────────────────────
EXCL = {123770: (JUN26, 29), 105914: (JUN26, 29), 777: (date(2026, 10, 5), 29)}


@pytest.fixture
def excluidos(monkeypatch):
    monkeypatch.setattr(server, '_clientes_excluidos', lambda: dict(EXCL))
    monkeypatch.setattr(server, '_clientes_genericos', lambda: {1})
    return EXCL


def test_dono_em_traz_o_excluido_so_ANTES_da_exclusao(excluidos):
    atual = {10: 29}
    assert server._dono_em(date(2026, 5, 31), atual) == {10: 29, 123770: 29, 105914: 29, 777: 29}
    assert server._dono_em(date(2026, 9, 30), atual) == {10: 29, 777: 29}   # excluído em outubro
    assert server._dono_em(date(2026, 10, 31), atual) == {10: 29}


def test_dono_em_nao_sobrescreve_o_dono_atual_nem_traz_generico(excluidos, monkeypatch):
    monkeypatch.setattr(server, '_clientes_excluidos', lambda: {10: (JUN26, 5), 1: (JUN26, 5)})
    assert server._dono_em(date(2026, 1, 31), {10: 29}) == {10: 29}


def test_foto_do_mes_perde_quem_ja_estava_excluido(excluidos):
    foto = {10: 29, 123770: 29, 777: 29}
    assert server._sem_excluidos_ate(foto, date(2026, 9, 30)) == {10: 29, 777: 29}


def test_o_teto_do_juliano_some():
    """4 excluídos na base que nunca vão comprar: teto de 40/44 = 90,9% com a meta em 100%."""
    fim = date(2026, 9, 30)
    ultima = {c: date(2026, 9, 1) for c in range(40)}
    ultima.update({c: date(2026, 3, 1) for c in (123770, 105914, 121353, 118641)})
    com = cob.cobertura_por_dono(ultima, {c: 29 for c in ultima}, fim)[29]
    assert (com['base'], com['positivados']) == (44, 40)
    sem = cob.cobertura_por_dono(ultima, {c: 29 for c in range(40)}, fim)[29]
    assert sem['pct'] == 1.0


# ─────────────────────── a carteira de hoje ───────────────────────
def test_carteira_de_hoje_tira_o_excluido_e_o_generico(excluidos, monkeypatch):
    monkeypatch.setattr(server, '_carregar_vendedores_map', lambda: {})
    monkeypatch.setattr(server, '_carregar_supervisores_map', lambda: {})
    monkeypatch.setattr(server, '_normalizar_cidades', lambda c: {})
    monkeypatch.setattr(server, '_cache_set', lambda *a, **k: None)
    cart = [{'codcli': 10, 'cliente': 'PADARIA', 'codusur': 29},
            {'codcli': 123770, 'cliente': 'CLIENTE EXCLUIDO', 'codusur': 29},
            {'codcli': 2, 'cliente': 'CONSUMIDOR FINAL', 'codusur': 4}]
    assert [c['codcli'] for c in server._finalizar_carteira(cart, 'k')] == [10]


# ─────────────────────── a consulta ───────────────────────
def _mock_bi(monkeypatch, payload, gravou):
    monkeypatch.setitem(server.CONFIG, 'data_source', 'powerbi')
    monkeypatch.setattr(server, '_cache_get', lambda k: None)
    monkeypatch.setattr(server, '_cache_set', lambda k, v, *a: gravou.append(v))
    monkeypatch.setattr(server, 'get_token_cached', lambda: 't')
    monkeypatch.setattr(server, 'execute_dax', lambda tok, q, *a, **k: payload(q))


def test_consulta_a_parte_e_o_parse(monkeypatch):
    gravou, consultas = [], []

    def payload(q):
        consultas.append(q)
        return {'results': [{'tables': [{'rows': [
            {'[CODCLI]': 123770.0, '[CODUSUR1]': 29.0, '[DTEXCLUSAO]': '2026-06-26T10:10:20'},
            {'[CODCLI]': 9.0, '[CODUSUR1]': None, '[DTEXCLUSAO]': '2011-03-18T00:00:00'}]}]}]}
    _mock_bi(monkeypatch, payload, gravou)
    assert server._clientes_excluidos() == {123770: (JUN26, 29), 9: (date(2011, 3, 18), None)}
    assert 'NOT ISBLANK(PCCLIENT[DTEXCLUSAO])' in consultas[0]
    assert gravou   # cacheou


def test_erro_do_power_BI_dentro_do_200_nao_vira_lista_vazia_cacheada(monkeypatch):
    """O Power BI devolve HTTP 200 com o erro dentro. Cachear [] traria os excluídos de volta
    pelo TTL inteiro do cache, sem ninguém saber."""
    gravou = []
    _mock_bi(monkeypatch, lambda q: {'results': [{'error': {'code': 'X'}}]}, gravou)
    assert server._clientes_excluidos() == {} and gravou == []


def test_BI_fora_a_carteira_segue(monkeypatch):
    gravou = []

    def payload(q):
        raise RuntimeError('BI fora')
    _mock_bi(monkeypatch, payload, gravou)
    monkeypatch.setattr(server, 'retry_dax', lambda fn: fn)
    assert server._clientes_excluidos() == {} and gravou == []


def test_NAO_entra_na_query_de_cadastro_da_carteira():
    """A `meta` já está no limite em que o executeQueries trunca (~260 clientes sem cadastro)."""
    src = inspect.getsource(server._carregar_carteira_full)
    assert 'DTEXCLUSAO' not in src
    assert "'multpel:carteira:full:global:v4'" in src


# ─────────────────────── quem usa ───────────────────────
def test_contas_de_mes_passado_usam_a_base_pela_data():
    assert '_dono_em(fim,' in inspect.getsource(server._cobertura_mes_fechado)
    assert '_dono_em(evol.fim_do_mes(ref)' in inspect.getsource(server._positivacao_insumos)
    src = inspect.getsource(server._evolucao_dados)
    assert '_sem_excluidos_ate(' in src and '_dono_em(fim, dono_atual)' in src
    assert "'multpel:evolucao:{ref}:{limiar}:v4'" in src.replace("f'", "'")


def test_recuperacao_marca_os_excluidos_e_nao_os_conta_como_dinheiro_na_mesa():
    assert 'recup.marcar_excluidos(hist' in inspect.getsource(server._recup_base)
    assert '_dono_em(_date.min' in inspect.getsource(server._recup_mapas)
    assert '_sem_excluidos_ate(dono_de' in inspect.getsource(server._recup_potencial)


def test_modo_postgres_tem_espelho_que_tolera_a_coluna_ausente():
    import provider_sql
    src = inspect.getsource(provider_sql.clientes_excluidos)
    assert "column_name='dtexclusao'" in src and 'return []' in src
