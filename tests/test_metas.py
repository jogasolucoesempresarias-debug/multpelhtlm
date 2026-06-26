"""Testes unitários do módulo puro metas.py (sem Flask/DAX/DB)."""
import metas


def test_projecao_run_rate():
    # FABIANE real (calibração): bruto 951779.80 × 21/19 = 1.051.967,15 (oficial)
    assert round(metas.projecao(951779.80, 21, 19), 2) == 1051967.15


def test_projecao_sem_dias_decorridos():
    # nenhum dia útil decorrido → não extrapola, devolve o próprio realizado
    assert metas.projecao(1000, 21, 0) == 1000


def test_falta_nunca_negativa():
    assert metas.falta(100, 30) == 70
    assert metas.falta(100, 130) == 0  # superou a meta → falta 0


def test_necessidade_dia():
    assert metas.necessidade_dia(100, 40, 6) == 10  # (100-40)/6
    assert metas.necessidade_dia(100, 40, 0) == 0   # mês encerrado


def test_pct_seguro():
    assert metas.pct(50, 100) == 0.5
    assert metas.pct(10, 0) is None  # denominador 0 → None


def test_linha_metrica_completa():
    l = metas.linha_metrica(meta=1330000, realizado=929049.65,
                            dias_mes=21, dias_decorridos=19, dias_restantes=2)
    assert l['meta'] == 1330000
    assert l['realizado'] == 929049.65
    assert round(l['falta'], 2) == round(1330000 - 929049.65, 2)
    assert round(l['pct_realizado'], 4) == round(929049.65 / 1330000, 4)
    # projeção recalculada (sem oficial) = 929049.65 × 21/19
    assert round(l['projecao'], 2) == round(929049.65 * 21 / 19, 2)


def test_linha_metrica_usa_projecao_oficial():
    # quando a projeção oficial é fornecida, ela prevalece (bate com o BI)
    l = metas.linha_metrica(1330000, 929049.65, 21, 19, 2, projecao_oficial=1051967.15)
    assert l['projecao'] == 1051967.15
    assert round(l['pct_projecao'], 4) == round(1051967.15 / 1330000, 4)


def test_sugerir_ano_anterior():
    hist = {202506: 100000.0, 202606: 120000.0}
    # alvo jun/2026 usa jun/2025 (202506) × (1+0.10)
    assert metas.sugerir(hist, 2026, 6, 'ano_anterior', 0.10) == 110000.0


def test_sugerir_ano_anterior_sem_base():
    assert metas.sugerir({202606: 1}, 2026, 6, 'ano_anterior') is None  # falta 202506


def test_sugerir_media_3m():
    hist = {202603: 30000.0, 202604: 40000.0, 202605: 50000.0}
    # alvo jun/2026 → média de mar/abr/mai 2026 = 40000
    assert metas.sugerir(hist, 2026, 6, 'media_3m') == 40000.0


def test_sugerir_media_3m_parcial():
    # só 1 mês disponível → média desse mês
    assert metas.sugerir({202605: 50000.0}, 2026, 6, 'media_3m') == 50000.0


def test_mes_anterior_atravessa_ano():
    assert metas._mes_anterior(2026, 1, 1) == (2025, 12)
    assert metas._mes_anterior(2026, 3, 3) == (2025, 12)
