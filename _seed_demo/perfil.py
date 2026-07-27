"""
Perfil do gerador — parâmetros calibrados no MOLDE real (Multpel 2025, via extract_molde.py).
NÃO contém dado do cliente: só as FORMAS das distribuições (escala, mix, sazonalidade, margem).
Ajuste ESCALA aqui para deixar a demo mais gorda/enxuta.
"""
from datetime import date

# ── Período (2,5 anos → cobre YoY, série 12m e cohort M+0..M+12) ──
DATA_INICIO = date(2024, 1, 1)
DATA_FIM    = date(2026, 7, 24)   # ~hoje

# ── Escala (real 2025; ~670k linhas/ano) ──
N_CLIENTES     = 7000
N_PRODUTOS     = 3800
N_VENDEDORES   = 114
N_SUPERVISORES = 14
N_FORNECEDORES = 245
N_COMPRADORES  = 8
N_DEPTOS       = 33
N_SECOES       = 80
LINHAS_POR_ANO       = 670_000
LINHAS_POR_NOTA_MEDIA = 5.35     # itens por nota

# ── Filiais (real tem 7; simplifico p/ 2 que cobrem os 2 regimes fiscais) ──
#   3 = ES/matriz (sem ICMS-ST) · 5 = interestadual (com ST → desconta da venda bruta)
FILIAIS = {
    "3": {"uf": "ES", "st": False, "peso_venda": 0.75},
    "5": {"uf": "BA", "st": True,  "peso_venda": 0.25},
}

# ── Mix de operação (CODOPER) — do molde ──
#   S = venda (conta na venda bruta e no custo)
#   SB = bonificação (venda 0, só custo, no CUSTO TOTAL)
#   ST/SR = fora das medidas (existem no fato p/ realismo)
COPER_MIX = {"S": 0.9317, "ST": 0.0626, "SR": 0.0057}
SB_FRAC_LINHAS = 0.005           # ~3149/670k linhas são bonificação

# ── Sazonalidade (fração da venda por mês, jan..dez) — quase plana ──
SAZONALIDADE = [0.0904, 0.0857, 0.0761, 0.0758, 0.0869, 0.0819,
                0.0835, 0.0812, 0.0825, 0.0884, 0.0782, 0.0895]

# ── Margem bruta por produto (faixa real 11%–26%; centro ~19%) ──
MARGEM_MIN, MARGEM_MAX = 0.11, 0.26

# ── ICMS-ST: aplicado só na filial com st=True, sobre parte dos produtos ──
ST_FRAC_PRODUTOS = 0.35          # % de SKUs sujeitos a ST
ST_ALIQUOTA      = 0.05          # alíquota efetiva média sobre a venda

# ── Devolução (real 2025: 1,375M / 88,4M ≈ 1,55% da venda) ──
DEVOL_FRAC_VENDA = 0.0155
# CODDEVOL comerciais (contam) vs internos (CODATIV=37 → não contam)
DEVOL_CODDEVOL_COMERCIAIS = [9, 8, 7, 1, 17, 16, 19, 6]
DEVOL_INTERNA_FRAC = 0.0         # fração de devolução interna (transferência); 0 = demo limpa

# ── Ciclo de vida dos clientes (p/ EMERGIR os 8 segmentos RFM canônicos) ──
#   cada perfil define recência/frequência/valor típicos
CLIENTE_PERFIS = {
    "campeao":    {"frac": 0.08, "freq_mes": 3.5, "ticket": 2.0,  "ativo_ate_fim": True},
    "leal":       {"frac": 0.15, "freq_mes": 2.0, "ticket": 1.3,  "ativo_ate_fim": True},
    "promissor":  {"frac": 0.12, "freq_mes": 1.2, "ticket": 1.0,  "ativo_ate_fim": True},
    "novo":       {"frac": 0.10, "freq_mes": 1.0, "ticket": 0.8,  "novo": True},
    "atencao":    {"frac": 0.15, "freq_mes": 1.0, "ticket": 1.1,  "esfria": True},
    "em_risco":   {"frac": 0.12, "freq_mes": 0.8, "ticket": 1.4,  "churn_meados": True},
    "hibernando": {"frac": 0.13, "freq_mes": 0.6, "ticket": 0.9,  "churn_cedo": True},
    "perdido":    {"frac": 0.15, "freq_mes": 0.5, "ticket": 0.7,  "churn_cedo": True},
}

# ── ABC dos produtos (Pareto: poucos SKUs = maior parte da venda) ──
ABC_A_FRAC_PROD, ABC_A_FRAC_VENDA = 0.20, 0.80   # 20% dos SKUs ~ 80% da venda

SEED = 42                        # reprodutibilidade
