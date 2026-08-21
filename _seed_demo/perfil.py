"""
Perfil do gerador — parâmetros calibrados no MOLDE real (Multpel 2025, via extract_molde.py).
NÃO contém dado do cliente: só as FORMAS das distribuições (escala, mix, sazonalidade, margem).
Ajuste ESCALA aqui para deixar a demo mais gorda/enxuta.
"""
import os
from datetime import date, timedelta

# ── Período — JANELA DESLIZANTE, sempre terminando HOJE ──
# ⚠️ Isto já foi `date(2026, 7, 24)` cravado, e era a causa de a demo "estar desatualizada":
# a base não envelhecia porque terminava numa data fixa. Quanto mais tempo passava, mais longe
# o "hoje" do dado ficava do hoje real — e a aba Evolução, o Orçamento do mês e qualquer
# pergunta sobre "agora" iam ficando estranhos numa apresentação comercial.
#
# A janela DESLIZA: o início acompanha o fim, então o volume de linhas fica constante em vez de
# crescer sem limite a cada regeração. 940 dias (~31 meses) é o que havia antes — cobre YoY,
# série de 12 meses e o cohort M+0..M+12 com folga.
#
# `DEMO_DATA_FIM` (YYYY-MM-DD) fixa a data quando for preciso reproduzir uma geração antiga.
DIAS_HISTORICO = 940

_fim_env = (os.getenv("DEMO_DATA_FIM") or "").strip()
DATA_FIM    = date.fromisoformat(_fim_env) if _fim_env else date.today()
DATA_INICIO = DATA_FIM - timedelta(days=DIAS_HISTORICO)


def meses_giro(fim=None):
    """Os 3 MESES CHEIOS anteriores ao fim — a janela de `QTVENDMES1..3` do Winthor.

    ⚠️ Estava cravada como abr/mai/jun de 2026 dentro do `gerar_estoque.py`. Com a data virando
    deslizante, aquilo passaria a calcular giro de meses que não existem mais na base — e giro
    zerado vira "sem giro" em massa, que é justamente o número que a demo precisa ter realista.
    Devolve [(ini, fim_exclusivo), ...] do mais antigo para o mais recente."""
    fim = fim or DATA_FIM
    primeiro_do_mes = fim.replace(day=1)
    limites = []
    for _ in range(3):
        fim_ex = primeiro_do_mes
        primeiro_do_mes = (primeiro_do_mes - timedelta(days=1)).replace(day=1)
        limites.append((primeiro_do_mes, fim_ex))
    return list(reversed(limites))

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

# ── Mix de SITUAÇÃO do estoque ──────────────────────────────────────────────────────────────
# ⚠️ Calibrado nas proporções MEDIDAS na Multpel real (08/2026), porque a demo estava mansa
# demais para demonstrar: capital parado 0,1% (real: 2,9%), quase nenhum item sem giro (real:
# 10,2%) e cobertura ideal 26% (real: 59,7%). Uma base sem dor não vende a ferramenta — o
# comprador pergunta "onde está meu capital parado?" e a resposta era R$ 2 mil.
#
# Frações sobre o total de SKUs. O resto é "saudável".
ESTOQUE_MIX = {
    "sem_giro": 0.10,    # real: 293/2.881 = 10,2% — tem estoque e não vende
    "parado":   0.15,    # real: 425/2.881 = 14,7% — 60+ dias sem venda, com estoque
    "ruptura":  0.11,    # real: 308/2.881 = 10,7% — zerado com giro
    "excesso":  0.12,    # cobertura muito alta (capital preso)
}
# Faixas de cobertura (dias) por situação. "saudável" mira acima do limiar do Estoque ideal (45),
# que é o que faz o painel gerencial sair perto dos 60% reais em vez dos 26% de antes.
COBERTURA_FAIXAS = {"saudavel": (46, 110), "excesso": (130, 400), "risco": (5, 40)}
PARADO_DIAS = (60, 420)          # há quanto tempo o item parado não vende
RISCO_FRAC  = 0.20               # dos "saudáveis", quantos ficam abaixo do ideal (a comprar)

# ── Medidas UNITÁRIAS (m³ e kg por unidade, não por caixa) ─────────────────────────────────
# ⚠️ Estava `volume 0,2–5 m³` e `peso 0,2–8 kg` POR UNIDADE — uma unidade de mercadoria com o
# volume de uma geladeira. Multiplicado pelo fator de caixa (6 a 48), a caixa implicada
# estourava a guarda de plausibilidade do app (`core.MAX_M3_CAIXA` = 1,5 m³ / `MAX_KG_CAIXA` =
# 50 kg) em praticamente todo o catálogo: a aba Qualidade da demo acusava **3.795 de 3.800**
# cadastros impossíveis (o real da Multpel são 72 de 4.519). Numa demonstração isso diz
# "a base inteira está errada", que é o oposto da mensagem.
#
# Faixas realistas de item de supermercado/atacado. Uma pequena parte estoura de propósito —
# a aba Qualidade precisa ter o que mostrar, só não pode ser tudo.
VOLUME_UN_M3 = (0.0005, 0.022)   # 0,5 a 22 litros por unidade
PESO_UN_KG   = (0.05, 1.30)      # 50 g a 1,3 kg por unidade

# ── Compras (pedido de compra) ──────────────────────────────────────────────────────────────
# ⚠️ O Orçamento mede o comprado do mês contra a meta (65% da venda líquida de 30 dias). Com o
# volume antigo a demo abria com **189% consumido** — todo comprador estourado, o que não é
# demonstração, é caricatura (o real da Multpel estava em 84,4%). Estes dois são os botões:
# menos itens por pedido e quantidade menor por item.
COMPRAS_N_PEDIDOS   = 1200
COMPRAS_ITENS_PED   = (4, 14)      # itens por pedido de compra
COMPRAS_QTD_ITEM    = (16, 320)    # unidades por item

# ── ABC dos produtos (Pareto: poucos SKUs = maior parte da venda) ──
ABC_A_FRAC_PROD, ABC_A_FRAC_VENDA = 0.20, 0.80   # 20% dos SKUs ~ 80% da venda

SEED = 42                        # reprodutibilidade
