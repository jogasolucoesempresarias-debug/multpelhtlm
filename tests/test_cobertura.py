"""Testes do motor puro cobertura.py — matemática de cobertura, faixas, ranking."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cobertura as cob


def _cli(recencia, venda=0.0, status='ok', receita_perdida=0.0, codusur=1, vendedor='RCA 1',
         codsupervisor=10, time='Time A'):
    """Cliente mínimo com os campos que o motor consome."""
    return {
        'recencia_dias':       recencia,
        'venda_12m':           venda,
        'status_personalizada': status,
        'receita_perdida_proj': receita_perdida,
        'codusur':             codusur,
        'vendedor':            vendedor,
        'codsupervisor':       codsupervisor,
        'time':                time,
    }


# ── faixa_de ──────────────────────────────────────────────────────────

def test_faixa_de_fronteiras():
    assert cob.faixa_de(0) == '0-15'
    assert cob.faixa_de(15) == '0-15'
    assert cob.faixa_de(16) == '16-30'
    assert cob.faixa_de(30) == '16-30'
    assert cob.faixa_de(31) == '31-45'
    assert cob.faixa_de(45) == '31-45'
    assert cob.faixa_de(46) == '46-60'
    assert cob.faixa_de(60) == '46-60'
    assert cob.faixa_de(61) == '61-90'
    assert cob.faixa_de(90) == '61-90'
    assert cob.faixa_de(91) == '91+'
    assert cob.faixa_de(999) == '91+'


def test_faixa_de_none_vira_91_mais():
    assert cob.faixa_de(None) == '91+'
    assert cob.faixa_de(-5) == '91+'


# ── agregar_grupo ─────────────────────────────────────────────────────

def test_grupo_vazio_nao_quebra():
    g = cob.agregar_grupo([])
    assert g['total_clientes'] == 0
    assert g['cobertura_clientes'] == 0.0
    assert g['cobertura_valor'] == 0.0
    assert g['valor_total'] == 0.0
    assert sum(b['clientes'] for b in g['buckets']) == 0


def test_cobertura_clientes_e_valor():
    # 2 em dia (10, 20 dias) valendo 100+300; 2 fora (40, 100 dias) valendo 200+400
    clientes = [
        _cli(10, venda=100), _cli(20, venda=300),
        _cli(40, venda=200), _cli(100, venda=400),
    ]
    g = cob.agregar_grupo(clientes, coberto_dias=30)
    assert g['total_clientes'] == 4
    assert g['clientes_cobertos'] == 2
    assert g['cobertura_clientes'] == 0.5
    assert g['valor_total'] == 1000
    assert g['valor_coberto'] == 400          # 100 + 300
    assert g['cobertura_valor'] == 0.4        # 400 / 1000


def test_rollup_0_30_igual_soma_das_duas_primeiras_faixas():
    clientes = [_cli(5, venda=10), _cli(15, venda=20), _cli(25, venda=30), _cli(50, venda=40)]
    g = cob.agregar_grupo(clientes, coberto_dias=30)
    b = {x['faixa']: x for x in g['buckets']}
    assert g['rollup_0_30']['clientes'] == b['0-15']['clientes'] + b['16-30']['clientes']
    assert g['rollup_0_30']['valor'] == round(b['0-15']['valor'] + b['16-30']['valor'], 2)
    assert g['rollup_0_30']['clientes'] == 3   # 5, 15, 25
    assert g['rollup_0_30']['valor'] == 60     # 10 + 20 + 30


def test_toggle_coberto_dias_move_headline_mas_nao_as_faixas():
    clientes = [_cli(10, venda=100), _cli(40, venda=100), _cli(55, venda=100)]
    g30 = cob.agregar_grupo(clientes, coberto_dias=30)
    g60 = cob.agregar_grupo(clientes, coberto_dias=60)
    # Headline muda: com 60 dias, os de 40 e 55 entram como cobertos
    assert g30['clientes_cobertos'] == 1
    assert g60['clientes_cobertos'] == 3
    # Faixas fixas não mudam com o toggle
    assert [b['clientes'] for b in g30['buckets']] == [b['clientes'] for b in g60['buckets']]


def test_cobertura_ciclo_conta_ok_e_normal():
    clientes = [
        _cli(10, status='ok'), _cli(20, status='normal'),
        _cli(50, status='atencao'), _cli(200, status='urgente'),
    ]
    g = cob.agregar_grupo(clientes)
    assert g['cobertura_ciclo'] == 0.5   # ok + normal = 2 de 4


def test_receita_em_risco_soma():
    clientes = [_cli(40, receita_perdida=1000.0), _cli(90, receita_perdida=500.5), _cli(10)]
    g = cob.agregar_grupo(clientes)
    assert g['receita_em_risco'] == 1500.5


def test_base_morta_conta_91_mais():
    clientes = [_cli(10), _cli(100), _cli(999), _cli(200)]
    g = cob.agregar_grupo(clientes)
    assert g['base_morta'] == 3


def test_grupo_so_com_base_morta():
    clientes = [_cli(999, venda=100), _cli(150, venda=50)]
    g = cob.agregar_grupo(clientes, coberto_dias=30)
    assert g['cobertura_clientes'] == 0.0
    assert g['base_morta'] == 2
    b = {x['faixa']: x for x in g['buckets']}
    assert b['91+']['clientes'] == 2


# ── agregar_niveis / ranking ──────────────────────────────────────────

def test_niveis_reconciliam_com_empresa():
    clientes = [
        _cli(10, venda=100, codusur=1, vendedor='A', codsupervisor=10, time='T1'),
        _cli(50, venda=200, codusur=2, vendedor='B', codsupervisor=10, time='T1'),
        _cli(80, venda=300, codusur=3, vendedor='C', codsupervisor=20, time='T2'),
    ]
    n = cob.agregar_niveis(clientes, coberto_dias=30)
    assert n['empresa']['total_clientes'] == 3
    assert sum(t['total_clientes'] for t in n['times']) == 3
    assert sum(v['total_clientes'] for v in n['vendedores']) == 3
    assert n['empresa']['valor_total'] == sum(t['valor_total'] for t in n['times'])


def test_ranking_pior_primeiro():
    clientes = [
        # Time bom: 2 clientes em dia
        _cli(10, codusur=1, codsupervisor=10, time='Bom'),
        _cli(12, codusur=1, codsupervisor=10, time='Bom'),
        # Time ruim: 2 clientes atrasados
        _cli(80, codusur=2, codsupervisor=20, time='Ruim'),
        _cli(90, codusur=2, codsupervisor=20, time='Ruim'),
    ]
    n = cob.agregar_niveis(clientes, coberto_dias=30)
    assert n['times'][0]['nome'] == 'Ruim'     # pior primeiro
    assert n['times'][0]['cobertura_clientes'] == 0.0
    assert n['times'][-1]['nome'] == 'Bom'


def test_vendedor_carrega_codsupervisor_para_drill():
    clientes = [_cli(10, codusur=7, vendedor='V7', codsupervisor=99, time='T99')]
    n = cob.agregar_niveis(clientes)
    v = n['vendedores'][0]
    assert v['codsupervisor'] == 99
    assert v['time'] == 'T99'


def test_amostra_pequena_flag():
    poucos = [_cli(10, codusur=1, codsupervisor=10, time='T1')]  # 1 < MIN_AMOSTRA
    n = cob.agregar_niveis(poucos)
    assert n['times'][0]['amostra_pequena'] is True


def test_clientes_sem_time_viram_grupo_sem_time():
    clientes = [_cli(10, codsupervisor=None, time=None, codusur=None, vendedor=None)]
    n = cob.agregar_niveis(clientes)
    assert n['times'][0]['nome'] == '(Sem time)'
    assert n['vendedores'][0]['nome'] == '(Sem RCA)'


def test_times_rcas_abaixo_do_limiar():
    clientes = [
        _cli(10, codusur=1, codsupervisor=10, time='Bom'),
        _cli(12, codusur=1, codsupervisor=10, time='Bom'),
        _cli(80, codusur=2, codsupervisor=20, time='Ruim'),
        _cli(90, codusur=2, codsupervisor=20, time='Ruim'),
    ]
    n = cob.agregar_niveis(clientes, coberto_dias=30)
    baixos = cob.times_rcas_abaixo(n, limiar_pct=60)
    nomes = [t['nome'] for t in baixos['times']]
    assert 'Ruim' in nomes and 'Bom' not in nomes
