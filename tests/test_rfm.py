"""Testes puros do módulo rfm. Não dependem de Flask/DAX/DB — rodam em ms."""
import pytest

from rfm import (
    REGUA_FIXA, SEGMENTOS_ORDEM,
    ciclo_pessoal,
    status_regua_fixa, status_regua_personalizada,
    lucro_perdido_projetado,
    receita_perdida_projetada,
    quintis, quintil_de,
    segmento_canonico,
    calcular_clientes, agregar_distribuicoes, matriz_rf,
)


def test_ciclo_pessoal_mediana_simples():
    # Compras a cada 10, 20, 30 dias -> mediana = 20
    datas = ['2026-01-01', '2026-01-11', '2026-01-31', '2026-03-02']
    assert ciclo_pessoal(datas) == 20


def test_ciclo_pessoal_floor_7():
    # Datas muito próximas (intervalo 1d) -> floor 7
    datas = ['2026-05-01', '2026-05-02', '2026-05-03']
    assert ciclo_pessoal(datas) == 7


def test_ciclo_pessoal_uma_compra_retorna_none():
    assert ciclo_pessoal(['2026-05-01']) is None
    assert ciclo_pessoal([]) is None


def test_ciclo_pessoal_remove_intervalos_zero():
    # Duas compras no mesmo dia + uma 30d depois -> intervalo único = 30
    datas = ['2026-01-01', '2026-01-01', '2026-01-31']
    assert ciclo_pessoal(datas) == 30


def test_status_regua_fixa_limites():
    assert status_regua_fixa(0)  == 'ok'
    assert status_regua_fixa(10) == 'ok'           # incl no limite
    assert status_regua_fixa(11) == 'normal'
    assert status_regua_fixa(30) == 'normal'
    assert status_regua_fixa(31) == 'atencao'
    assert status_regua_fixa(45) == 'atencao'
    assert status_regua_fixa(46) == 'urgente'
    assert status_regua_fixa(999) == 'urgente'


def test_status_regua_personalizada_fallback_quando_ciclo_none():
    # ciclo None -> aplica régua fixa
    assert status_regua_personalizada(50, None) == 'urgente'
    assert status_regua_personalizada(20, None) == 'normal'
    assert status_regua_personalizada(0, 0)     == 'ok'


def test_status_regua_personalizada_razao():
    # ciclo=30; dias=15 (razao 0.5) -> ok
    assert status_regua_personalizada(15, 30) == 'ok'
    # dias=45 (razao 1.5) -> normal
    assert status_regua_personalizada(45, 30) == 'normal'
    # dias=75 (razao 2.5) -> atencao
    assert status_regua_personalizada(75, 30) == 'atencao'
    # dias=120 (razao 4) -> urgente
    assert status_regua_personalizada(120, 30) == 'urgente'


def test_lucro_perdido_zero_se_dentro_ciclo():
    # Cliente em dia: dias < ciclo -> 0
    assert lucro_perdido_projetado(12000, 15, 30) == 0.0
    # Lucro 0 -> 0
    assert lucro_perdido_projetado(0, 90, 30) == 0.0
    # Sem ciclo -> 0
    assert lucro_perdido_projetado(12000, 90, None) == 0.0


def test_lucro_perdido_atrasado():
    # Lucro 12000/ano (1000/mês), ciclo 30d, atrasado 90d:
    # meses_atrasado = (90 - 30) / 30 = 2 meses
    # perdido = 1000 * 2 = 2000
    assert lucro_perdido_projetado(12000, 90, 30) == pytest.approx(2000.0)


def test_receita_perdida_projetada_simetrica_ao_lucro():
    """Mesma fórmula do lucro_perdido_projetado mas com venda 12m."""
    # Venda 60000/ano (5000/mês), ciclo 30d, atrasado 90d → 2 meses × 5000 = 10000
    assert receita_perdida_projetada(60000, 90, 30) == pytest.approx(10000.0)
    # Dentro do ciclo → 0
    assert receita_perdida_projetada(60000, 15, 30) == 0.0
    # Sem ciclo → 0
    assert receita_perdida_projetada(60000, 90, None) == 0.0
    # Venda 0/None → 0
    assert receita_perdida_projetada(0, 90, 30) == 0.0
    assert receita_perdida_projetada(None, 90, 30) == 0.0


def test_quintis_distribuicao_uniforme():
    valores = list(range(1, 101))  # 1..100
    cuts = quintis(valores)
    assert cuts == [20, 40, 60, 80]


def test_quintil_de_normal_e_invertido():
    cuts = [20, 40, 60, 80]
    # Normal (maior valor = quintil maior)
    assert quintil_de(5, cuts)  == 1
    assert quintil_de(25, cuts) == 2
    assert quintil_de(45, cuts) == 3
    assert quintil_de(65, cuts) == 4
    assert quintil_de(95, cuts) == 5
    # Invertido (menor valor = quintil maior — recência)
    assert quintil_de(5, cuts, invertido=True)  == 5
    assert quintil_de(95, cuts, invertido=True) == 1


def test_segmento_canonico_principais():
    assert segmento_canonico(5, 5, 5) == 'champions'
    assert segmento_canonico(4, 5, 4) == 'loyal'
    assert segmento_canonico(2, 5, 5) == 'cant_lose'
    # Patch M.2: (3, 4, 3) agora casa em cant_lose primeiro (f>=4 sem requisito M).
    # Antes caía em at_risk porque cant_lose exigia M>=4.
    assert segmento_canonico(3, 4, 3) == 'cant_lose'
    assert segmento_canonico(3, 3, 4) == 'at_risk'  # F=3 cai em at_risk
    assert segmento_canonico(5, 1, 1) == 'new'
    assert segmento_canonico(4, 2, 2) == 'potential_loyalist'
    assert segmento_canonico(1, 1, 1) == 'lost'
    # Patch M: cliente com R/F médios e M baixo continua em hibernating (não casa loyal pq R<4)
    assert segmento_canonico(1, 3, 3) == 'hibernating'
    # Patch M: mercadinhos/padarias (alta R+F, baixo M) agora são loyal — antes eram hibernating
    assert segmento_canonico(5, 5, 1) == 'loyal'   # mercadinho diário, ticket pequeno
    assert segmento_canonico(4, 4, 1) == 'loyal'   # idem
    assert segmento_canonico(4, 5, 2) == 'loyal'   # padaria
    assert segmento_canonico(5, 4, 1) == 'loyal'   # alta R + F bom + M baixo
    # Patch M.2: R médio (sumindo) + F alto + M baixo agora viram alerta de resgate
    assert segmento_canonico(3, 5, 1) == 'cant_lose'  # alta freq sumindo, ticket pequeno
    assert segmento_canonico(3, 4, 1) == 'cant_lose'
    assert segmento_canonico(2, 4, 1) == 'cant_lose'
    assert segmento_canonico(3, 3, 1) == 'at_risk'    # F médio sumindo
    assert segmento_canonico(2, 3, 1) == 'at_risk'


def test_segmento_canonico_cobre_todas_combinacoes():
    """Garantia: nenhum R/F/M ∈ {1..5} cai num segmento NÃO listado."""
    for r in range(1, 6):
        for f in range(1, 6):
            for m in range(1, 6):
                seg = segmento_canonico(r, f, m)
                assert seg in SEGMENTOS_ORDEM, f'R={r}, F={f}, M={m} -> {seg!r} fora dos canônicos'


def test_calcular_clientes_pipeline_completo():
    """25 clientes distribuídos: cliente 1 = melhor (R=5/F=5/M=5), cliente 25 = pior."""
    snapshot = []
    datas_por_cliente = {}
    meta = {}
    for i in range(1, 26):
        # i=1: dias=5 (recente), compras=50, lucro=100000 → top
        # i=25: dias=250 (antigo), compras=1, lucro=200 → bottom
        snapshot.append({
            'CODCLI': i,
            'DiasSemComprar': i * 10,
            'Compras12m':     max(1, 51 - i * 2),
            'Lucro12m':       max(200, 100000 - i * 4000),
            'Venda12m':       max(1000, 500000 - i * 20000),
            'UltimaCompra':   '2026-05-01',
        })
        datas_por_cliente[i] = ['2026-04-01', '2026-04-15', '2026-05-01']  # ciclo ~14d
        meta[i] = {'cliente': f'C{i}', 'cidade': '-', 'uf': '-', 'codusur1': 100, 'telefone': None}

    out = calcular_clientes(snapshot, datas_por_cliente, meta)
    assert len(out) == 25
    cli1 = next(c for c in out if c['codcli'] == 1)
    cli25 = next(c for c in out if c['codcli'] == 25)

    # Cliente 1: melhor cliente — R/F/M devem ser altos (quintil 5)
    assert cli1['r'] == 5
    assert cli1['f'] == 5
    assert cli1['m'] == 5
    assert cli1['segmento'] == 'champions'
    # Mediana de intervalos [14, 16] = 15
    assert cli1['ciclo_pessoal'] == 15
    assert cli1['lucro_perdido_proj'] == 0  # dentro do ciclo

    # Cliente 25: pior cliente — R/F/M devem ser baixos (quintil 1)
    assert cli25['r'] == 1
    assert cli25['f'] == 1
    assert cli25['m'] == 1
    assert cli25['segmento'] == 'lost'
    assert cli25['status_fixa'] == 'urgente'  # 250 dias > 45
    assert cli25['lucro_perdido_proj'] > 0     # MUITO atrasado


def test_agregar_distribuicoes_e_matriz():
    snapshot = [
        {'CODCLI': i, 'DiasSemComprar': i, 'Compras12m': i * 2, 'Lucro12m': i * 1000, 'Venda12m': i * 5000, 'UltimaCompra': '2026-05-01'}
        for i in range(1, 11)
    ]
    datas = {i: ['2026-04-01', '2026-04-15', '2026-05-01'] for i in range(1, 11)}
    meta = {i: {'cliente': f'C{i}', 'cidade': '-', 'uf': '-', 'codusur1': 1, 'telefone': None} for i in range(1, 11)}
    clientes = calcular_clientes(snapshot, datas, meta)
    agg = agregar_distribuicoes(clientes, modo='personalizada')
    assert agg['total_clientes'] == 10
    assert sum(agg['regua'][k] for k in ('ok', 'normal', 'atencao', 'urgente')) == 10
    assert sum(agg['segmentos'].values()) == 10
    mat = matriz_rf(clientes)
    # Todas as células devem ter contagens consistentes
    assert sum(c['count'] for c in mat) == 10


# ─────────────────────────────────────────────────────────────────────
# Tests da correção: quintis F/M só sobre clientes ativos (Compras12m >= 1)
# Bug original: inativos (Compras12m=0) inflavam quintil F=1 →
# célula (R=5, F=1) sempre vazia → segmento 'new' impossível.
# ─────────────────────────────────────────────────────────────────────

def test_quintis_f_m_excluem_inativos():
    """1000 ativos (Compras12m=1..1000) + 200 inativos (Compras12m=0).
    Quintil F=1 dos ativos deve ter ~200 clientes (1000/5), NÃO ~0."""
    snapshot = (
        [{'CODCLI': i, 'DiasSemComprar': 30, 'Compras12m': i,
          'Lucro12m': i*100, 'Venda12m': i*500, 'UltimaCompra': '2026-05-01'}
         for i in range(1, 1001)]
        + [{'CODCLI': 2000+j, 'DiasSemComprar': 500, 'Compras12m': 0,
            'Lucro12m': 0, 'Venda12m': 0, 'UltimaCompra': '2025-01-01'}
           for j in range(200)]
    )
    datas = {c['CODCLI']: ['2026-04-01', '2026-05-01'] for c in snapshot}
    meta = {c['CODCLI']: {'cliente': f'C{c["CODCLI"]}', 'codusur1': 1} for c in snapshot}
    out = calcular_clientes(snapshot, datas, meta)
    # Ativos em F=1 devem ter Compras12m baixo (≤ ~200, que é cutoff Q1 dos 1000 ativos)
    ativos_f1 = [c for c in out if c['frequencia_12m'] >= 1 and c['f'] == 1]
    assert len(ativos_f1) > 0, 'Sem ativos em F=1 — quintil F está vazio'
    assert all(c['frequencia_12m'] <= 250 for c in ativos_f1), \
        'Ativos em F=1 deveriam ter Compras12m baixo'


def test_inativos_classificados_como_lost():
    """Cliente Compras12m=0 recebe F=1, M=1 atribuídos → cai em 'lost' (R≤2, F≤2, M≤2)."""
    snapshot = [
        {'CODCLI': 1, 'DiasSemComprar': 500, 'Compras12m': 0,
         'Lucro12m': 0, 'Venda12m': 0, 'UltimaCompra': '2025-01-01'},
        {'CODCLI': 2, 'DiasSemComprar': 30, 'Compras12m': 5,
         'Lucro12m': 1000, 'Venda12m': 5000, 'UltimaCompra': '2026-05-01'},
    ]
    datas = {1: [], 2: ['2026-04-01', '2026-05-01']}
    meta = {i: {'cliente': f'C{i}', 'codusur1': 1} for i in [1, 2]}
    out = calcular_clientes(snapshot, datas, meta)
    inativo = next(c for c in out if c['codcli'] == 1)
    assert inativo['f'] == 1, 'Inativo deve ter F=1 atribuído'
    assert inativo['m'] == 1, 'Inativo deve ter M=1 atribuído'
    assert inativo['segmento'] == 'lost', f"Inativo deve ser 'lost', obteve {inativo['segmento']!r}"


def test_new_segment_emerge_com_dados_realistas():
    """Cliente recente (R=5) com 1 compra (F=1 dos ativos) deve cair em 'new'.
    Bug original impossibilitava esse cenário."""
    snapshot = (
        # 1 cliente NOVO: recente + 1 compra
        [{'CODCLI': 1, 'DiasSemComprar': 5, 'Compras12m': 1,
          'Lucro12m': 500, 'Venda12m': 2000, 'UltimaCompra': '2026-05-25'}]
        # 100 outros ativos com mais compras (pra dar distribuição nos quintis F/M)
        + [{'CODCLI': i, 'DiasSemComprar': i, 'Compras12m': 5+i,
            'Lucro12m': i*200, 'Venda12m': i*1000, 'UltimaCompra': '2026-05-01'}
           for i in range(2, 102)]
        # 50 inativos (pra simular base real)
        + [{'CODCLI': 200+j, 'DiasSemComprar': 400, 'Compras12m': 0,
            'Lucro12m': 0, 'Venda12m': 0, 'UltimaCompra': '2025-01-01'}
           for j in range(50)]
    )
    datas = {c['CODCLI']: ['2026-04-01', '2026-05-01'] for c in snapshot}
    meta = {c['CODCLI']: {'cliente': f'C{c["CODCLI"]}', 'codusur1': 1} for c in snapshot}
    out = calcular_clientes(snapshot, datas, meta)
    cli_novo = next(c for c in out if c['codcli'] == 1)
    assert cli_novo['r'] == 5, f"Esperado R=5, obteve {cli_novo['r']}"
    assert cli_novo['f'] == 1, f"Esperado F=1 (entre os ativos), obteve {cli_novo['f']}"
    assert cli_novo['segmento'] == 'new', f"Esperado 'new', obteve {cli_novo['segmento']!r}"


def test_total_clientes_inalterado_apos_fix():
    """Regressão: total processados não muda + soma dos segmentos = total + todos canônicos."""
    snapshot = [
        {'CODCLI': i, 'DiasSemComprar': i*5, 'Compras12m': max(0, 10 - i),
         'Lucro12m': i*100, 'Venda12m': i*500, 'UltimaCompra': '2026-05-01'}
        for i in range(1, 51)
    ]
    datas = {c['CODCLI']: ['2026-04-01', '2026-05-01'] for c in snapshot}
    meta = {c['CODCLI']: {'cliente': f'C{c["CODCLI"]}', 'codusur1': 1} for c in snapshot}
    out = calcular_clientes(snapshot, datas, meta)
    assert len(out) == 50
    from collections import Counter
    segs = Counter(c['segmento'] for c in out)
    assert sum(segs.values()) == 50, 'Soma de segmentos != total de clientes'
    for s in segs:
        assert s in SEGMENTOS_ORDEM, f"segmento {s!r} fora da lista canônica"
