"""Catálogo de relatórios do módulo Compras.

Fonte ÚNICA para os dois consumidores: os checkboxes do Admin e o envio por email. Manter
os dois lendo daqui evita a divergência clássica (aparece no Admin mas o cron não sabe gerar).

Regra de corte: só entra tela em formato de TABELA, que é o que `_export_data(view)` sabe
gerar em CSV/XLSX/PDF. Painéis (Cockpit, Painel gerencial, Meta de ruptura, Plano reposição,
Orçamento) não são relatórios e por definição ficam de fora — email leva relatório.
"""

# (view, rótulo, grupo) — a ordem aqui é a ordem que aparece no Admin.
RELATORIOS = [
    ("reposicao",         "Abastecimento (o que comprar)", "Comprar"),
    ("estoque_zero",      "Estoque zerado",                "Comprar"),
    ("ruptura",           "Cobertura",                     "Comprar"),
    ("ruptura_comprador", "Ruptura por comprador",         "Comprar"),

    ("parado",            "Estoque parado",                "Risco"),
    ("validade",          "Validade (FEFO)",               "Risco"),
    ("vencidos",          "Vencidos (perda realizada)",    "Risco"),

    ("desempenho",        "Desempenho comercial",          "Análise"),
    ("comprasvendas",     "Compras × Vendas",              "Análise"),
    ("fornecedores",      "Fornecedores",                  "Análise"),
    ("compradores",       "Compradores",                   "Análise"),
    ("produtos",          "Produtos",                      "Análise"),
    ("abcxyz",            "ABC-XYZ",                       "Análise"),
    ("qualidade",         "Qualidade da base",             "Análise"),

    ("conferencia",       "Conferência de endereços",      "Ocupação"),
    ("vazias",            "Vagas vazias",                  "Ocupação"),
]

VIEWS = [v for v, _, _ in RELATORIOS]
ROTULOS = {v: r for v, r, _ in RELATORIOS}
GRUPOS = {v: g for v, _, g in RELATORIOS}


def catalogo():
    """Lista serializável pro Admin montar os checkboxes."""
    return [{"view": v, "rotulo": r, "grupo": g} for v, r, g in RELATORIOS]


def normalizar(bruto):
    """Sanitiza o que veio do Admin: mantém só views conhecidas, sem duplicar, na ordem do
    catálogo (assim o email sai sempre na mesma sequência, independente da ordem do clique)."""
    if isinstance(bruto, str):
        escolhidas = {x.strip() for x in bruto.split(",") if x.strip()}
    elif isinstance(bruto, (list, tuple, set)):
        escolhidas = {str(x).strip() for x in bruto if str(x).strip()}
    else:
        escolhidas = set()
    return [v for v in VIEWS if v in escolhidas]
