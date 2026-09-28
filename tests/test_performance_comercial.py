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
    v = {'cobertura': 1.34, 'frequencia': 3.51}         # os dois no topo da escala do campo
    n = pc.nota(v, pc.CAMPO, pc.PESOS_PADRAO)
    assert n['nota'] == pytest.approx(10) and n['parcial'] is True
    assert n['peso_medido'] == pytest.approx(0.35)
    assert set(n['faltando']) == {'rentabilidade', 'receita', 'mix'}


def test_nota_completa_pondera_pelos_pesos():
    v = {'rentabilidade': 1.10, 'receita': 0.70, 'mix': 0.95, 'cobertura': 1.05, 'frequencia': 1.50}
    n = pc.nota(v, pc.CAMPO, pc.PESOS_PADRAO)
    esperado = (10 * 35 + 0 * 10 + 9 * 20 + pc.escala(1.05, 0.76, 1.34) * 25 + 0 * 10) / 100
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


def test_cobertura_ajustada_nao_pune_carteira_de_classe_C():
    """Dois vendedores com o MESMO desempenho relativo: um só com clientes A, outro só com C.
    Sem o ajuste, o de C perderia; com o ajuste, os dois ficam em 1,0."""
    classes = {**{i: 'A' for i in range(10)}, **{i: 'C' for i in range(100, 120)}}
    bases = {1: set(range(10)), 2: set(range(100, 120))}
    atend = {1: set(range(8)), 2: set(range(100, 105))}          # 80% dos A · 25% dos C
    r = pc.cobertura_ajustada(bases, atend, classes, {1: pc.CAMPO, 2: pc.CAMPO})
    assert r[1]['indice'] == pytest.approx(1.0) and r[2]['indice'] == pytest.approx(1.0)


def test_por_classe_explica_o_indice_sem_mudar_a_nota():
    """`por_classe` (09/2026, Agente de IA) é só explicação: base e atendidos por classe somam o
    `obs`, a taxa do universo reproduz o `esperado`, e índice/nota ficam idênticos."""
    classes = {**{i: 'A' for i in range(10)}, **{i: 'B' for i in range(10, 20)},
               **{i: 'C' for i in range(100, 120)}}
    bases = {1: set(range(10)) | set(range(100, 105)), 2: set(range(10, 20)) | set(range(105, 120))}
    atend = {1: set(range(9)) | {100}, 2: {10, 11, 105}}
    un = {1: pc.CAMPO, 2: pc.CAMPO}
    r = pc.cobertura_ajustada(bases, atend, classes, un)
    for u in (1, 2):
        pcl = r[u]['por_classe']
        assert sum(x['atendidos'] for x in pcl.values()) == r[u]['obs']
        assert sum(x['base'] for x in pcl.values()) == len(bases[u])
        assert sum(x['base'] * x['taxa_universo'] for x in pcl.values()) == pytest.approx(r[u]['esperado'], abs=0.01)
    assert r[1]['por_classe']['A'] == {'base': 10, 'atendidos': 9, 'taxa_universo': 0.9}
    # a nota vem só de `indice` — mesmo resultado com ou sem o campo novo
    sem = {u: {k: v for k, v in d.items() if k != 'por_classe'} for u, d in r.items()}
    for u in (1, 2):
        n1 = pc.nota({'cobertura': r[u]['indice']}, pc.CAMPO, pc.PESOS_PADRAO)
        n2 = pc.nota({'cobertura': sem[u]['indice']}, pc.CAMPO, pc.PESOS_PADRAO)
        assert n1 == n2


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
