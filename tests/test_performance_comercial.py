"""Performance Comercial (performance_comercial.py) — gates das decisões de 24/09/2026."""
import pytest

import performance_comercial as pc


def test_universo_por_tipovend():
    assert pc.universo_de('I') == pc.LOJAS
    assert pc.universo_de('E') == pc.TELEMARKETING
    assert pc.universo_de('R') == pc.CAMPO and pc.universo_de(None) == pc.CAMPO


def test_escala_linear_com_trava():
    assert pc.escala(0.70, 0.70, 1.10) == 0 and pc.escala(1.10, 0.70, 1.10) == 10
    assert pc.escala(0.90, 0.70, 1.10) == pytest.approx(5)
    assert pc.escala(2.0, 0.70, 1.10) == 10 and pc.escala(0.1, 0.70, 1.10) == 0
    assert pc.escala(None, 0.70, 1.10) is None


def test_nota_parcial_e_renormalizada_sem_meta():
    """Sem meta cadastrada, rentabilidade/receita/mix saem da conta: a nota fica na escala 0–10
    sobre o peso medido (35%) em vez de ter teto 3,5 — e declara o que falta."""
    v = {'cobertura': 1.00, 'frequencia': 3.51}         # 100% da carteira + frequência no topo
    n = pc.nota(v, pc.CAMPO, pc.PESOS_PADRAO)
    assert n['nota'] == pytest.approx(10) and n['parcial'] is True
    assert n['peso_medido'] == pytest.approx(0.35)
    assert set(n['faltando']) == {'rentabilidade', 'receita', 'mix'}


def test_nota_completa_pondera_pelos_pesos():
    v = {'rentabilidade': 1.10, 'receita': 0.70, 'mix': 0.95, 'cobertura': 1.05, 'frequencia': 1.50}
    n = pc.nota(v, pc.CAMPO, pc.PESOS_PADRAO)
    # cobertura 1,05 = 105% da carteira? não existe: o valor é fração (0–1); acima de 1 satura em 10
    esperado = (10 * 35 + 0 * 10 + 9 * 20 + 10 * 25 + 0 * 10) / 100
    assert n['nota'] == pytest.approx(esperado, abs=0.01) and n['parcial'] is False


@pytest.mark.parametrize('ating, nota', [
    (0.83, 0.0),      # Marcio (print): abaixo do piso de 85%
    (0.8499, 0.0),
    (0.85, 6.0),      # piso: a rampa começa em 6
    (0.875, 6.5),     # meio da rampa
    (0.8999, 7.0),    # "morrendo no 89,99% em nota 7" (6,998 → 7,0 na tela)
    (0.90, 9.0),
    (0.97, 9.0),      # 90% e 99% valem o mesmo, de propósito
    (0.9999, 9.0),
    (1.00, 10.0),     # bateu a meta = 10 (era 7,5 na escala linear 70→110)
    (1.004, 10.0),    # Tarik (print): "100%" com nota 7,6
    (1.47, 10.0),
])
def test_faixas_de_atingimento_do_cliente(ating, nota):
    """João, 28/09/2026: piso 85%, rampa 6→7 até 89,99%, 90–99,99% = 9, 100%+ = 10.
    Vale para rentabilidade, receita e mix, nos três universos."""
    assert pc.nota_atingimento(ating) == pytest.approx(nota, abs=0.01)
    for k in pc.ATINGIMENTO:
        for u in pc.UNIVERSOS:
            assert pc.nota({k: ating}, u, {k: 100})['notas'][k] == pytest.approx(nota, abs=0.01)


def test_atingimento_exato_na_fronteira_nao_cai_por_ponto_flutuante():
    """180.000 ÷ 200.000 é 90% e tem de dar 9 — sem o round, o resíduo de float o jogaria na rampa."""
    assert pc.nota_atingimento(pc.atingimento(180_000, 200_000)) == 9.0
    assert pc.nota_atingimento(pc.atingimento(0.1 + 0.2, 0.3)) == 10.0     # 1,0000000000000002 ≈ 1
    assert pc.nota_atingimento(pc.atingimento(0.85 * 3, 3)) == 6.0


def test_sem_nenhum_indicador_nota_none():
    assert pc.nota({}, pc.CAMPO, pc.PESOS_PADRAO)['nota'] is None


def test_escala_depende_do_universo():
    """Frequência 3,0 é topo no campo e meio na loja (loja atende balcão)."""
    assert pc.nota({'frequencia': 3.51}, pc.CAMPO, pc.PESOS_PADRAO)['notas']['frequencia'] == 10
    assert pc.nota({'frequencia': 3.51}, pc.LOJAS, pc.PESOS_PADRAO)['notas']['frequencia'] < 5


def test_cobertura_e_pontuada_nas_faixas_de_meta_com_meta_100():
    """Régua única (João, 29/09/2026): a cobertura é o % da carteira ativa positivada em 60 d, com
    META DE 100% nas MESMAS faixas das metas. Sai o índice relativo ao mix ABC (NOTA_VERSAO 3)."""
    assert pc.NOTA_VERSAO == 3 and 'cobertura' in pc.ATINGIMENTO_COBERTURA
    assert all('cobertura' not in e for e in pc.ESCALAS.values())
    for pct_, nota_ in ((0.80, 0.0), (0.8499, 0.0), (0.85, 6.0), (0.95, 9.0), (1.0, 10.0)):
        assert pc.nota({'cobertura': pct_}, pc.CAMPO, pc.PESOS_PADRAO)['notas']['cobertura'] == nota_
    assert pc.nota({'cobertura': 0.80}, pc.LOJAS, pc.PESOS_PADRAO)['notas']['cobertura'] == 0.0   # igual em todo universo


def test_cobertura_INFORMATIVA_aparece_mas_nao_entra_na_nota():
    """Mês de limpeza: a cobertura é calculada e mostrada, mas sai da nota — e sai também do peso
    total, senão a nota de TODO MUNDO ficaria "parcial" (e sem classificação) no período."""
    v = {'rentabilidade': 1.0, 'receita': 1.0, 'mix': 1.0, 'cobertura': 0.50, 'frequencia': 3.51}
    n = pc.nota(v, pc.CAMPO, pc.PESOS_PADRAO, fora_da_nota=('cobertura',))
    assert n['notas']['cobertura'] == 0.0 and n['informativos'] == ['cobertura']
    assert n['nota'] == 10.0 and n['parcial'] is False and n['peso_medido'] == pytest.approx(1.0)
    assert 'cobertura' not in n['faltando']
    com = pc.nota(v, pc.CAMPO, pc.PESOS_PADRAO)
    assert com['nota'] == pytest.approx(7.5) and com['informativos'] == []


def test_pesos_por_competencia_nao_reescrevem_o_passado():
    hist = {'202611': {'rentabilidade': 20, 'cobertura': 40, 'mix': 20, 'receita': 10, 'frequencia': 10}}
    p, comp = pc.pesos_vigentes(hist, 202609)
    assert p == pc.PESOS_PADRAO and comp is None                 # setembro segue no padrão
    p, comp = pc.pesos_vigentes(hist, 202612)
    assert p['cobertura'] == 40 and comp == 202611


def test_validar_pesos():
    assert pc.validar_pesos(pc.PESOS_PADRAO) is None
    assert pc.validar_pesos({**pc.PESOS_PADRAO, 'mix': 30}) == 'os pesos precisam somar 100'
    assert pc.validar_pesos({'lucro': 100}) == 'indicador desconhecido'


def test_nota_parcial_fica_SEM_CLASSIFICACAO():
    """João, 28/09/2026: "quando faltar nota, deixar sem classificação — vale para todos". A Ellen
    (só cobertura + frequência, 7,1) estava em 7º de 46 à frente de quem tem os 5 indicadores.
    A parcial segue com nota, mas sem posição, e não empurra a posição de ninguém."""
    linhas = [{'universo': pc.CAMPO, 'nota': 9.2, 'parcial': False},
              {'universo': pc.CAMPO, 'nota': 7.2, 'parcial': True},     # 90% do peso — também sai
              {'universo': pc.CAMPO, 'nota': 7.1, 'parcial': True},
              {'universo': pc.CAMPO, 'nota': 7.0, 'parcial': False},
              {'universo': pc.CAMPO, 'nota': None, 'parcial': True}]
    r = pc.ranquear(linhas)
    assert [(l['nota'], l['posicao']) for l in r] == [
        (9.2, 1), (7.0, 2), (7.2, None), (7.1, None), (None, None)]


def test_ranquear_por_universo():
    linhas = [{'universo': pc.LOJAS, 'nota': 9}, {'universo': pc.CAMPO, 'nota': 5},
              {'universo': pc.CAMPO, 'nota': 7}, {'universo': pc.CAMPO, 'nota': None}]
    r = pc.ranquear(linhas)
    assert [(l['universo'], l['posicao']) for l in r] == [
        (pc.CAMPO, 1), (pc.CAMPO, 2), (pc.CAMPO, None), (pc.LOJAS, 1)]


def test_nota_com_menos_de_35_por_cento_do_peso_nao_ranqueia():
    """Só a cobertura (25%) não basta para comparar pessoas."""
    n = pc.nota({'cobertura': 1.2}, pc.CAMPO, pc.PESOS_PADRAO)
    assert n['nota'] is None and n['peso_medido'] == pytest.approx(0.25)
