"""
Reconstrução das 6 medidas nativas do dataset RCA — para clientes que têm BI mas NÃO têm
as medidas prontas no modelo (MEDIDAS=joga). Traz a inteligência JOGA em vez de depender do
modelo do cliente. As fórmulas foram reverse-engineeradas e validadas contra o BI da Multpel
(ver memória multpel-rca-medidas-reconstruidas): 5/6 exatas no centavo, VENDA BRUTA a 99,99%
(cauda de ST de ~0,012% que só fecha com o DAX real da medida).

Uso: `reconstruir_medidas(query)` troca os tokens `[VENDA BRUTA]` etc. pela expressão em coluna
crua. É um POST-PROCESSOR aplicado no executor SOMENTE quando MEDIDAS=joga. Com MEDIDAS=cliente
nada é chamado → o DAX sai byte a byte igual ao de hoje (garantia de zero impacto na Multpel).

Segurança do replace: os tokens são delimitados por colchetes e terminam em `]`, então
`[CUSTO TOTAL]` NUNCA casa dentro de `[CUSTO TOTAL DEVOLUCAO]`. Ainda assim, ordenamos do mais
longo pro mais curto por robustez. As expressões de substituição não contêm tokens (sem recasar).
"""

FV = "FATURAMENTO_VENDAS"
FD = "FATURAMENTO_DEVOLUCAO"
FA = "FATURAMENTO_DEVOLUCAO_AVULSA"

# devolução: exclui transferência interna de filial (CODATIV=37 E CODDEVOL<>9)
_EXCL_DEV = f'NOT({FD}[CODATIV]=37 && {FD}[CODDEVOL]<>9)'

RECONSTRUCOES = {
    "[VENDA BRUTA]":
        f'CALCULATE(SUM({FV}[VLVENDA]) - SUM({FV}[ICMSRETIDO]) - SUM({FV}[VLFECP]), '
        f'{FV}[CODOPER]="S")',
    "[CUSTO TOTAL]":
        f'CALCULATE(SUMX({FV}, {FV}[VLCUSTOFIN] + {FV}[VLCUSTOFINBONIF]), '
        f'{FV}[CODOPER] IN {{"S","SB"}})',
    "[TOTAL DEVOLUCAO]":
        f'CALCULATE(SUM({FD}[VLDEVOLUCAO]), {_EXCL_DEV})',
    "[CUSTO TOTAL DEVOLUCAO]":
        f'CALCULATE(SUM({FD}[VLCUSTOFIN]), {_EXCL_DEV})',
    "[TOTAL DEVOLUCAO AVULSA]":
        f'SUM({FA}[VLDEVOLUCAO])',
    "[CUSTO TOTAL DEVOLUCAO AVULSA]":
        f'SUM({FA}[VLCUSTOFIN])',
    # positivação de clientes (validado: = distinct CODCLI com venda 'S'; "NOVO" é só o nome no modelo)
    "[TOTAL CLIENTES NOVO]":
        f'CALCULATE(DISTINCTCOUNT({FV}[CODCLI]), {FV}[CODOPER]="S")',
    # mix = soma POR DIA dos produtos distintos vendidos (VLCUSTOFINB>0 e CONDVENDA<>10).
    # Validado contra o BI (15.711 = 15.711, Abr/26). Def. exata do modelo do cliente.
    "[TOTAL MIX]":
        (f'SUMX(SUMMARIZE(FILTER({FV}, {FV}[VLCUSTOFINB] > 0 && {FV}[CONDVENDA] <> 10), '
         f'{FV}[DTSAIDA], "pd", DISTINCTCOUNT({FV}[CODPROD])), [pd])'),
    # ticket = ROUND(AVERAGEX(FV, [VENDA LIQUIDA]), 2). Como [VENDA LIQUIDA] é MEDIDA (transição de
    # contexto por linha), reproduzimos a parte de venda líquida por linha 'S'. ~0,6% do real
    # (154,24 × 153,29 no BI) — a diferença é a devolução por-linha, imaterial p/ KPI secundário.
    "[TICKET MEDIO]":
        (f'ROUND(AVERAGEX(FILTER({FV}, {FV}[CODOPER]="S"), '
         f'{FV}[VLVENDA]-{FV}[ICMSRETIDO]-{FV}[VLFECP]), 2)'),
    # positivação = clientes distintos 'S' / total de linhas com CONDVENDA<>10. Validado (0,07=0,07).
    "[TAXA POSITIVACAO CLIENTE]":
        (f'ROUND(DIVIDE(CALCULATE(DISTINCTCOUNT({FV}[CODCLI]), {FV}[CODOPER]="S"), '
         f'CALCULATE(COUNT({FV}[CODCLI]), {FV}[CONDVENDA] <> 10), 0), 2)'),
}

# ── medidas COMPOSTAS (derivadas das 6 base; alinhamento RCA do README) ──
# Usam o contexto de filtro ambiente (período/RBAC que a query já aplica), igual à medida real.
_VL = (f'({RECONSTRUCOES["[VENDA BRUTA]"]} - {RECONSTRUCOES["[TOTAL DEVOLUCAO]"]}'
       f' - {RECONSTRUCOES["[TOTAL DEVOLUCAO AVULSA]"]})')
_CUSTO = (f'({RECONSTRUCOES["[CUSTO TOTAL]"]} - {RECONSTRUCOES["[CUSTO TOTAL DEVOLUCAO]"]}'
          f' - {RECONSTRUCOES["[CUSTO TOTAL DEVOLUCAO AVULSA]"]})')
RECONSTRUCOES["[VENDA LIQUIDA]"] = _VL
RECONSTRUCOES["[LUCRO TOTAL]"] = f'({_VL} - {_CUSTO})'
# valor médio por peso = venda líquida / peso de venda (bruto CONDVENDA<>10 − devoluções por TOTPESO).
# Denominador exato (166.566 no BI); razão ~1,8% do real (venda líquida em contexto aninhado). KPI secundário.
RECONSTRUCOES["[VALOR MEDIO PESO]"] = (
    f'DIVIDE({_VL}, (CALCULATE(SUM({FV}[PESOBRUTO]), {FV}[CONDVENDA]<>10)'
    f' - CALCULATE(SUM(FATURAMENTO_DEVOLUCAO[TOTPESO]), FATURAMENTO_DEVOLUCAO[CONDVENDA]<>10)'
    f' - SUM(FATURAMENTO_DEVOLUCAO_AVULSA[TOTPESO])))')

# TODAS as medidas do app reconstruídas (13/13). Exatas: 6 base (VENDA BRUTA venda a 99,99% por ST),
# VENDA LIQUIDA, LUCRO TOTAL, TOTAL CLIENTES NOVO, TOTAL MIX, TAXA POSITIVACAO CLIENTE.
# Aproximações (KPIs secundários, sutileza da venda líquida em contexto aninhado): TICKET MEDIO (~0,6%),
# VALOR MEDIO PESO (~1,8%). MEDIDAS=joga agora cobre o app inteiro.
MEDIDAS_PENDENTES = []

# ordena por comprimento do token desc (defesa extra contra colisão de substring)
_ORDEM = sorted(RECONSTRUCOES.items(), key=lambda kv: -len(kv[0]))


def reconstruir_medidas(query: str) -> str:
    """Troca os tokens de medida do RCA pelas expressões em coluna crua. No-op se não houver
    token (queries do META/Estoque que não usam essas medidas passam intactas)."""
    if not query or "[" not in query:
        return query
    for token, expr in _ORDEM:
        if token in query:
            query = query.replace(token, expr)
    return query
