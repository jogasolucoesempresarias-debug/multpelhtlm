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
    out = M.reconstruir_medidas('[VENDA BRUTA]')
    assert out == ('CALCULATE(SUM(FATURAMENTO_VENDAS[VLVENDA]) - SUM(FATURAMENTO_VENDAS[ICMSRETIDO])'
                   ' - SUM(FATURAMENTO_VENDAS[VLFECP]), FATURAMENTO_VENDAS[CODOPER]="S")')


def test_default_medidas_e_cliente():
    """A garantia de zero impacto: sem env, o modo é 'cliente' → executor não reconstrói."""
    import estoque.pbi as pbi
    assert pbi.CONFIG['medidas'] == 'cliente'
