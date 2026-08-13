"""Gate da (%) MARGEM da aba Rentabilidade: denominador = realizado BRUTO (com bônus).

É a régua da medida oficial `[MARGEM(%)]` do dataset META (= [LUCRO TOTAL] ÷ [VENDA TOTAL],
com [VENDA TOTAL] ≡ [Tem Pedido]). Números reais medidos no BI em 13/08/2026, SUPERVISOR LOJAS:

    lucro     [MARGEM META(%)]      =  62.981,69
    bruto     [Tem Pedido]          = 317.295,31  → 19,85%  ← o que o BI mostra
    sem bônus [Realizado Sem Bonus] = 275.011,25  → 22,90%  ← o que o app mostrava até 08/2026

⚠️ O fixture PRECISA trazer `[venda_sb]` diferente de `[venda]`. Sem isso o gate não morde: era
exatamente esse o furo que deixou a margem errada 4 semanas em produção — o teste antigo já
afirmava a fórmula certa, mas o mock sem `venda_sb` caía no fallback e passava por acidente.
"""
from datetime import date as _date

import pytest

from tests.conftest import login_as

_HOJE = _date.today()
_AM = f'ano={_HOJE.year}&mes={_HOJE.month}'

LUCRO, BRUTO, SEM_BONUS = 62981.69, 317295.31, 275011.25
V_LUCRO, V_BRUTO, V_SEM_BONUS = 18725.26, 88244.18, 77288.61   # ANTONIO CARLOS (cod 750)


def _pl(rows):
    return {'results': [{'tables': [{'rows': rows}]}]}


def _rotas():
    sup_map = _pl([{'PCSUPERV[CODSUPERVISOR]': 4, 'PCSUPERV[NOME]': 'SUPERVISOR LOJAS',
                    'PCSUPERV[TIPOSUPERVISOR]': 'P'}])
    vend_map = _pl([{'PCUSUARI[CODUSUR]': 750, 'PCUSUARI[NOME]': 'ANTONIO CARLOS',
                     'PCUSUARI[CODSUPERVISOR]': 4, 'PCUSUARI[TIPOVEND]': 'R',
                     'PCUSUARI[CIDADE]': 'VITORIA', 'PCUSUARI[ESTADO]': 'ES',
                     'PCUSUARI[BLOQUEIO]': 'N'}])
    dias = _pl([{'[mes]': 20, '[decorridos]': 8, '[restantes]': 12}])
    por_sup = _pl([{'PCSUPERV[CODSUPERVISOR]': 4, 'PCSUPERV[NOME]': 'SUPERVISOR LOJAS',
                    '[venda]': BRUTO, '[venda_sb]': SEM_BONUS, '[rentab]': LUCRO,
                    '[proj]': BRUTO * 2.5, '[cli]': 100, '[mix]': 300}])
    totais = _pl([{'[venda]': BRUTO, '[venda_sb]': SEM_BONUS, '[rentab]': LUCRO,
                   '[proj]': BRUTO * 2.5, '[cli]': 100, '[mix]': 300}])
    vr_usur = _pl([{'PCUSUARI[CODUSUR]': 750, '[venda]': V_BRUTO, '[venda_sb]': V_SEM_BONUS,
                    '[rentab]': V_LUCRO, '[proj]': V_BRUTO * 2.5}])
    cli_usur = _pl([{'PCPEDC[CODUSUR]': 750, '[v]': 30}])
    mix_usur = _pl([{'PCPEDI[CODUSUR]': 750, '[v]': 90}])
    # ordem importa (first match): PCSUPERV/ROW antes das rotas genéricas PCPEDC/PCPEDI
    return [('TIPOSUPERVISOR', sup_map), ('TIPOVEND', vend_map), ('CALENDARIO', dias),
            ('PCSUPERV[CODSUPERVISOR]', por_sup), ('ROW("venda"', totais),
            ('PCUSUARI[CODUSUR]', vr_usur), ('PCPEDC[CODUSUR]', cli_usur),
            ('PCPEDI[CODUSUR]', mix_usur)]


def test_margem_total_e_supervisor_usam_realizado_bruto(client, usuario_admin,
                                                        mock_dax_capture, clean_redis):
    mock_dax_capture.set_routes(_rotas())
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    d = client.get(f'/api/metas?{_AM}').get_json()
    assert d['ok']

    esperado = LUCRO / BRUTO            # 19,85% — coluna (%) MARGEM do BI
    inflado = LUCRO / SEM_BONUS         # 22,90% — o bug: 3 p.p. a mais
    assert d['total']['margem'] == pytest.approx(esperado, abs=1e-6), (
        f"total {d['total']['margem']:.4%} — esperado {esperado:.4%}; "
        f"sem-bônus daria {inflado:.4%}")
    assert d['supervisores'][0]['margem'] == pytest.approx(esperado, abs=1e-6)


def test_margem_vendedor_usa_realizado_bruto(client, usuario_admin, mock_dax_capture, clean_redis):
    mock_dax_capture.set_routes(_rotas())
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    d = client.get(f'/api/metas/vendedores?{_AM}&codsupervisor=4').get_json()
    v = next(x for x in d['vendedores'] if x['codusur'] == 750)

    esperado = V_LUCRO / V_BRUTO        # 21,22% — o que o BI mostra na linha do ANTONIO
    assert v['margem'] == pytest.approx(esperado, abs=1e-6), (
        f"vendedor {v['margem']:.4%} — esperado {esperado:.4%}; "
        f"sem-bônus daria {V_LUCRO / V_SEM_BONUS:.4%}")


def test_margem_ignora_venda_sb_mesmo_quando_o_bonus_e_enorme(client, usuario_admin,
                                                              mock_dax_capture, clean_redis):
    """Bônus de 50% não pode mexer na margem — o denominador é o bruto, sempre."""
    rotas = dict(_rotas())
    rotas['ROW("venda"'] = _pl([{'[venda]': 1000.0, '[venda_sb]': 500.0, '[rentab]': 200.0,
                                 '[proj]': 2500.0, '[cli]': 10, '[mix]': 20}])
    mock_dax_capture.set_routes(list(rotas.items()))
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    d = client.get(f'/api/metas?{_AM}').get_json()
    assert d['total']['margem'] == pytest.approx(0.20)   # 200/1000, não 200/500
