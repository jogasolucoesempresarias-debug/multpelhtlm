"""
Fase 1 (multi-fonte) — garante que a reconstrução de medidas é correta E que o modo padrão
(MEDIDAS=cliente) não mexe em nada (zero impacto na Multpel).
"""
import medidas_dax as M


def test_query_sem_token_fica_inalterada():
    q = 'EVALUATE ROW("x", SUM(FATURAMENTO_VENDAS[QT]))'
    assert M.reconstruir_medidas(q) == q


def test_cada_token_e_substituido():
    for token in M.RECONSTRUCOES:
        out = M.reconstruir_medidas(f'EVALUATE ROW("v", {token})')
        assert token not in out, f"{token} não foi substituído"


def test_tokens_aninhados_nao_corrompem():
    # [CUSTO TOTAL] vs [CUSTO TOTAL DEVOLUCAO] / [TOTAL DEVOLUCAO] vs [... AVULSA]
    q = ('ROW("a",[CUSTO TOTAL],"b",[CUSTO TOTAL DEVOLUCAO],"c",[TOTAL DEVOLUCAO],'
         '"d",[TOTAL DEVOLUCAO AVULSA],"e",[CUSTO TOTAL DEVOLUCAO AVULSA],"f",[VENDA BRUTA])')
    out = M.reconstruir_medidas(q)
    for token in M.RECONSTRUCOES:
        assert token not in out
    assert 'VLCUSTOFINBONIF' in out                               # CUSTO TOTAL
    assert 'CODATIV' in out and 'CODDEVOL' in out                 # devoluções
    assert 'FATURAMENTO_DEVOLUCAO_AVULSA[VLDEVOLUCAO]' in out     # devol avulsa
    assert 'FATURAMENTO_VENDAS[VLVENDA]' in out                   # venda bruta


def test_venda_bruta_reconstruida_esperada():
    """A fórmula é `VLVENDA − ICMSRETIDO`, e o **FECP fica de fora**.

    Até 09/2026 a reconstrução subtraía também o `VLFECP` e este teste travava isso. Decodificada
    medindo a medida do cliente contra as colunas cruas, produto a produto, no BI real:

        42253 → 590.061,96 − 135,81 = 589.926,15  (= [VENDA BRUTA], exato)
        58511 →  63.661,81 − 143,28 =  63.518,53  (exato)
        57433 →  30.344,27 −  17,95 =  30.326,32  (exato)

    Nos três o FECP (23,69 / 21,92 / 3,09) fica FORA. Era ele o resíduo de ~0,012% que o
    `medidas_dax.py` registrava como "cauda de ST que só fecha com o DAX real da medida" — a
    hipótese estava errada, e o jeito de descobrir foi conferir num grão pequeno o bastante para
    o resíduo aparecer sozinho. No agregado, 0,012% se lê como arredondamento."""
    out = M.reconstruir_medidas('[VENDA BRUTA]')
    assert out == ('CALCULATE(SUM(FATURAMENTO_VENDAS[VLVENDA]) - SUM(FATURAMENTO_VENDAS[ICMSRETIDO])'
                   ', FATURAMENTO_VENDAS[CODOPER]="S")')
    assert 'VLFECP' not in out


def test_default_medidas_e_cliente():
    """A garantia de zero impacto: sem env, o modo é 'cliente' → executor não reconstrói."""
    import estoque.pbi as pbi
    assert pbi.CONFIG['medidas'] == 'cliente'
