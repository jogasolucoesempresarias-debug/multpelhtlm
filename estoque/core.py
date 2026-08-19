"""
Motor de cálculo do painel de estoque — metodologia OFICIAL (query do TI).

Giro = média de 3 meses (QTVENDMES1..3); QTDISP = estoque endereçado (default) ou
gerencial; custo = CUSTOFIN. Produz lista de produtos enriquecida + cockpit +
ranking de fornecedores + FEFO de validade.

Técnicas: Days of Supply, ABC (Pareto), XYZ (variabilidade), matriz ABC-XYZ,
ponto de reposição (ROP) com lead time por fornecedor, ruptura, dead stock, FEFO.
"""

import calendar
import math
import statistics
from datetime import datetime, date, timedelta


# ───────────────────────── parâmetros (configuráveis) ─────────────────────────
DEFAULTS = {
    "giro_base":        "media3",  # media3 (oficial) | m1 (último mês)
    "base_estoque":     "gerencial",  # gerencial (QTESTGER cru, oficial v3) | endereco (WMS)
    "lead_time":        10,        # dias (fallback quando o fornecedor não tem prazo)
    "dias_seguranca":   25,        # dias de estoque de segurança
    "cobertura_total":  45,        # dias-alvo de cobertura p/ COMPRA (N2 da planilha = 45d)
    "ruptura_dias":     30,        # cobertura <= isso = ruptura
    "horizonte_val":    30,        # janela de risco de vencimento
    "parado_atencao":   60,        # dias sem venda
    "parado_critico":   90,
    "parado_mcritico":  120,
    "excesso_cob":      120,       # cobertura acima disso = excesso
    "abc_a":            80.0,      # % acumulado
    "abc_b":            95.0,
    "xyz_x":            0.5,       # coeficiente de variação
    "xyz_y":            1.0,
    "forecast":         0,         # 1 = giro vem do forecast (RCA mensal); 0 = média3 oficial
    "forecast_meses":   6,         # janela da média móvel simples do forecast bruto
    "forecast_sazonal": 0,         # 1 = aplica fator sazonal ano-a-ano (implica forecast on)
    "arredonda_cx":     1,         # 1 = arredonda sugestão/pedido p/ caixa fechada (QTUNITCX)
    # ⚠️ Os dois abaixo são a RÉGUA DE MEDIÇÃO do "Estoque ideal" (Painel gerencial) — NÃO alteram
    # a sugestão de compra. `cobertura_total` (alvo de COMPRA) segue independente de propósito:
    # um diz até onde comprar, o outro diz a partir de quanto o SKU conta como coberto. Antes de
    # 07/2026 os dois valores estavam cravados em resumo_estoque_ideal(); viraram parâmetro a
    # pedido do diretor p/ calibrar na prática antes de fixar o número.
    "ideal_dias":       45,        # cobertura mínima (dias) p/ o SKU contar como "ideal"
    "ideal_meta_pct":   90,        # % dos SKUs que giram que precisa estar na faixa ideal
    # Janela do "produto novo" no Estoque parado (⚙ Parâmetros → "Produtos novos"). Ver
    # `parado_faixa_de`: item que nunca vendeu e chegou dentro dela não é dead stock, é
    # mercadoria que ainda não teve chance. Parâmetro (e não 15 cravado) pelo mesmo motivo do
    # `ideal_dias`: é calibração de negócio, e o diretor quer olhar o número antes de fixá-lo.
    "novo_dias":        15,        # dias desde a ENTRADA p/ o item contar como "produto novo"
}
_STR_PARAMS = {"giro_base", "base_estoque"}


def merge_params(q):
    """Mescla querystring (dict) sobre os defaults, com cast numérico."""
    p = dict(DEFAULTS)
    for k, v in (q or {}).items():
        if k not in p or v in (None, ""):
            continue
        if k in _STR_PARAMS:
            p[k] = str(v)
        else:
            try:
                p[k] = float(v) if "." in str(v) else int(v)
            except (TypeError, ValueError):
                pass
    # Clamp do que vem do usuário. `novo_dias` = 0 não erraria alto: simplesmente esvaziaria o
    # card "Novos" e devolveria os itens ao 121+ sem ninguém perceber — o mesmo modo de falha
    # silenciosa que o `regua_estoque_ideal` já trata para o limiar do Estoque ideal.
    p["novo_dias"] = max(1, int(_n(p["novo_dias"]) or DEFAULTS["novo_dias"]))
    return p


# ───────────────────────── helpers ─────────────────────────
def _n(x):
    if x in (None, ""):
        return 0.0
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "")).date()
    except ValueError:
        return None


def _giro_mensal(row, base):
    g1, g2, g3 = _n(row.get("giro_m1")), _n(row.get("giro_m2")), _n(row.get("giro_m3"))
    if base == "m1":
        return g1
    return round((g1 + g2 + g3) / 3)  # media3 — oficial do TI


def _meses_anteriores(hoje, n):
    """Lista dos N AnoMes (YYYYMM) imediatamente anteriores ao mês de `hoje` (mais recente 1º)."""
    out, ano, mes = [], hoje.year, hoje.month
    for _ in range(n):
        mes -= 1
        if mes == 0:
            mes, ano = 12, ano - 1
        out.append(ano * 100 + mes)
    return out


def _meses_ate(hoje, n):
    """Lista dos N AnoMes terminando NO MÊS CORRENTE (ordem cronológica, mais antigo 1º).
    Usada SÓ pelo gráfico de venda 12m do 360°, que precisa mostrar o mês em andamento
    (o comprador quer ver a venda de hoje). NÃO usar em giro/forecast/sazonal: lá a janela
    é de meses FECHADOS de propósito — um mês pela metade subestimaria a previsão."""
    out, ano, mes = [], hoje.year, hoje.month
    for _ in range(int(n)):
        out.append(ano * 100 + mes)
        mes -= 1
        if mes == 0:
            mes, ano = 12, ano - 1
    return list(reversed(out))


def previsao_giro_mensal(serie_am, meses, hoje):
    """Forecast bruto: média móvel SIMPLES da QT vendida nos N meses fechados anteriores.
    serie_am: {AnoMes: qtd}. Retorna giro mensal previsto (qtd/mês) ou None se sem histórico."""
    if not serie_am:
        return None
    chaves = _meses_anteriores(hoje, int(meses))
    total = sum(_n(serie_am.get(am)) for am in chaves)
    return round(total / len(chaves)) if chaves else None


def giro_novo_item(serie_am, hoje, janela=6):
    """Giro mensal p/ ITEM NOVO cujo giro média-3m oficial deu 0 (os campos QTVENDMES1..3 do
    PCEST são os 3 meses FECHADOS anteriores — ainda zerados p/ item com <3 meses de casa),
    mas que já teve venda REAL recente (RCA). Média da qtd vendida sobre os meses ativos, do
    1º mês com venda (dentro da janela) até o mês fechado mais recente — não dilui pelos meses
    anteriores ao lançamento. serie_am: {AnoMes: qtd}. None se nunca vendeu na janela."""
    if not serie_am:
        return None
    chaves = _meses_anteriores(hoje, int(janela))          # meses fechados, mais recente 1º
    com_dado = [am for am in chaves if _n(serie_am.get(am)) > 0]
    if not com_dado:
        return None
    prim = min(com_dado)                                    # 1º mês com venda dentro da janela
    ativos = [am for am in chaves if am >= prim]            # do lançamento até o mês fechado atual
    total = sum(_n(serie_am.get(am)) for am in ativos)
    return round(total / len(ativos)) if ativos else None


def giro_mes_corrente(serie_am, hoje):
    """Giro mensal = venda CRUA acumulada do MÊS CORRENTE (RCA). Último recurso quando não há
    média-3m NEM meses fechados com venda — item que só começou a girar no mês em andamento.
    Sem isto o giro fica 0, o item some do abastecimento e gera ruptura mesmo vendendo (decisão
    do sócio 07/2026: 'trazer a venda do item no mês quando não tiver a média dos 3 meses').
    NÃO anualiza — usa a venda do mês como está; o número sobe conforme o mês avança.
    serie_am: {AnoMes: qtd}. None se o mês corrente ainda não teve venda."""
    if not serie_am:
        return None
    am = hoje.year * 100 + hoje.month
    q = _n(serie_am.get(am))
    return round(q) if q > 0 else None


def fatores_sazonais(serie_am, hoje, janela=24, min_meses=12):
    """Índices sazonais ano-a-ano a partir da venda mensal (RCA).
    media_mensal = média dos últimos `janela` meses (naturalmente dessazonalizada);
    fator[m] = média do mês-calendário m ÷ media_mensal, clampado a [0.3, 3.0].
    Retorna {"media_mensal", "fatores": {1..12}} ou None se histórico < min_meses."""
    if not serie_am:
        return None
    chaves = _meses_anteriores(hoje, int(janela))
    com_dado = [am for am in chaves if am in serie_am]
    if len(com_dado) < int(min_meses):
        return None
    media_mensal = sum(_n(serie_am.get(am)) for am in chaves) / len(chaves)
    if media_mensal <= 0:
        return None
    fatores = {}
    for m in range(1, 13):
        obs = [_n(serie_am.get(am)) for am in chaves if am % 100 == m and am in serie_am]
        fatores[m] = max(0.3, min(3.0, (sum(obs) / len(obs)) / media_mensal)) if obs else 1.0
    return {"media_mensal": media_mensal, "fatores": fatores}


def previsao_giro_sazonal(saz, mes):
    """Giro mensal previsto p/ um mês-calendário: nível dessazonalizado × fator do mês."""
    return round(saz["media_mensal"] * saz["fatores"].get(mes, 1.0))


DIAS_TRANSICAO = 7        # janela do bloqueio recente (ver qt_em_transicao)


def qt_em_transicao(qtbloq, dtultent, hoje=None, dias=DIAS_TRANSICAO, linhas=None):
    """Mercadoria que JÁ CHEGOU e está entre o pedido e o estoque disponível ("pré-entrada":
    recebida, aguardando conferência/liberação). Nas palavras do diretor: *"tá bloqueado pq sai
    de pedido e fica na transição para estoque disponível"*.

    **Por que existe:** o Winthor, na pré-entrada, baixa o `PCITEM[QTENTREGUE]` (o item sai de
    "já pedido") e lança a quantidade em `QTESTGER` **e** em `QTBLOQUEADA` — disponível fica 0.
    Sem isto, a mercadoria some das DUAS contas ao mesmo tempo e o item volta a aparecer como
    "ruptura sem pedido" → o app **sugere comprar de novo o que já está no armazém**. Medido em
    07/2026: 130 linhas, **R$ 198.683** de mercadoria em transição (22 viravam ruptura falsa,
    108 inflavam a sugestão em silêncio).

    ⚠️ **Heurística, não fato.** O Winthor usa o MESMO `QTBLOQUEADA` para avaria e para
    pré-entrada, e o `MOTIVOBLOQESTOQUE` vem vazio nesta base — não há campo que os separe.
    Discrimina-se pela DATA: bloqueio com entrada recente é transição; bloqueio velho é avaria
    de verdade (e essa deve mesmo continuar sugerindo compra). Validado contra uma 2ª fonte
    independente (`PEDIDO_ENTRADA`, a data em que a NF do pedido entrou): **21 de 21** itens com
    bloqueio ≤3d têm NF entrando junto, contra **2 de 122** no bloqueio >30d.

    `linhas` = posição do item POR FILIAL (`[{qtbloq, dtultent, qtultent}, ...]`) — o caminho bom
    desde 08/2026, quando o diretor reportou itens em AVARIA aparecendo como pré-entrada.
    Duas correções, e nenhuma delas é ajuste de limiar:

    1. **Parear filial a filial.** O snapshot agrega o produto com `SUM(QTBLOQUEADA)` e
       `MAX(DTULTENT)` — some as filiais dos dois lados e cruza o bloqueio de UMA com a data de
       OUTRA. No Atacado (filiais 3+5) bastava uma entrada recente na Matriz para carimbar como
       "chegando" uma avaria velha parada no Depósito. Não era mudança de rotina no ERP: a
       agregação já misturava as duas coisas desde sempre.
    2. **Teto na quantidade da última entrada (`QTULTENT`).** `DTULTENT` é a data da última
       entrada de QUALQUER natureza, e nada garante que ela explique o que está bloqueado hoje.
       Se há 200 un bloqueadas e a última entrada trouxe 12, no máximo 12 podem ser pré-entrada —
       as outras 188 são avaria e devem continuar contando como ruptura (e sugerindo compra).
       Cap, não filtro: descartar a linha inteira jogaria fora a parte legítima.

    Sem `linhas` (fixtures antigas, modo BD sem a query) cai no comportamento anterior — errado
    a favor de comprar de novo, que é o erro que já existia e não uma regressão nova.

    🔁 **Trocar por dado exato quando der:** `PCMOVPREENT` (107.566 linhas no Oracle) é a tabela
    da pré-entrada. Falta descobrir como marcar a que ainda está pendente e publicá-la no dataset
    (mesmo caminho da `TRIB_ENTRADA`). Aí só o corpo desta função muda — o resto do app não sabe
    de onde vem o número. (`PCNFENT.CONFERIDO` foi testado e é campo morto: 'N' em 2.287 linhas,
    nulo em 26, nenhum 'S'.) Pista forte ainda não explorada: no WMS a **RUA 99** é o endereço de
    avaria (o app já a exclui do endereçado) — bloqueio endereçado ali é avaria por definição.

    Retorna a quantidade em transição (float)."""
    hoje = hoje or date.today()
    lim = int(dias)

    def _uma(qb, dt_raw, qult=None):
        q = _n(qb)
        if q <= 0:
            return 0.0
        dt = _parse_dt(dt_raw)
        if dt is None or (hoje - dt).days > lim:
            return 0.0
        qu = _n(qult)
        return min(q, qu) if qu > 0 else q

    if linhas:
        return float(sum(_uma(l.get("qtbloq"), l.get("dtultent"), l.get("qtultent"))
                         for l in linhas))
    return _uma(qtbloq, dtultent)


def arredonda_caixa(qt, qtunitcx):
    """Arredonda `qt` PRA CIMA em caixas fechadas. Retorna (qt_arredondado, n_caixas).
    No-op (qt, None) se qtunitcx<=1 ou qt<=0."""
    if not qtunitcx or qtunitcx <= 1 or qt <= 0:
        return qt, None
    cx = math.ceil(qt / qtunitcx)
    return cx * qtunitcx, cx


# ───────────────── medidas logísticas (peso e cubagem) — FONTE ÚNICA ─────────────────
# Caixa maior que isto é fisicamente impossível no negócio: não passa em palete/porta, e
# ninguém movimenta na mão. Serve de GUARDA contra cadastro em que alguém gravou o dado do
# MÁSTER no registro da unidade — aí o "× fator de caixa" infla o resultado pelo fator inteiro.
MAX_M3_CAIXA = 1.5
MAX_KG_CAIXA = 50.0


def medidas_unitarias(cad, fator=1):
    """Volume (m³), peso bruto e peso líquido de UMA unidade + se o cadastro é confiável.

    ⚠️ A fonte é o **PCPRODUT**, não a PCEMBALAGEM. Validado contra o pedido 565848 do
    Winthor (fornecedor 9406, 22 itens): `PCPRODUT × quantidade em UNIDADES` reproduz os
    TRÊS totais do rodapé do 211 ao centavo — líquido 14.482,02, bruto 14.497,64 e volume
    23,50 m³. A PCEMBALAGEM não serve: `PESOBRUTO` está vazio em **75,6%** dos produtos de
    revenda e `VOLUME` em **100%**. Era ela a fonte do peso até 08/2026, e por isso o PDF
    saía com 6.758 kg onde o ERP dizia 14.497,64 (−53%) — o item de maior peso do pedido
    (49447, 350 caixas) simplesmente contava zero.

    ⚠️ Havia um segundo defeito na fonte antiga: `qtunit` e `pesobruto` vinham de dois
    `MAX()` INDEPENDENTES da PCEMBALAGEM, que tem uma linha por embalagem. O fator de uma
    linha casava com o peso de outra (cód. 46661: fator 24 com o peso do pacote de 12).

    `confiavel=False` quando a caixa implicada é impossível — a tela mostra "—" e diz
    quantos itens ficaram de fora, em vez de exibir 730 kg/caixa como se fosse fato.
    Os cadastros nessa situação estão em `cubagem_a_corrigir.csv` (70 produtos, 08/2026).
    """
    cad = cad or {}
    f = fator if fator and fator > 1 else 1
    vol, bruto, liq = _n(cad.get("VOLUME")), _n(cad.get("PESOBRUTO")), _n(cad.get("PESOLIQ"))
    if vol <= 0:   # sem VOLUME, deriva das dimensões (A×L×C em cm → m³)
        a, l, c = _n(cad.get("ALTURAM3")), _n(cad.get("LARGURAM3")), _n(cad.get("COMPRIMENTOM3"))
        vol = (a * l * c) / 1e6 if (a > 0 and l > 0 and c > 0) else 0.0
    confiavel = not ((vol > 0 and vol * f > MAX_M3_CAIXA)
                     or (bruto > 0 and bruto * f > MAX_KG_CAIXA))
    return {"vol": vol, "bruto": bruto, "liq": liq, "confiavel": confiavel}


def item_master(qtd_un, qtunitcx, custo_unit, embalagem=None):
    """Converte um item do pedido (gravado sempre em UNIDADES) para a **unidade master**,
    que é como o pedido tem de sair no PDF e na planilha de importação do Winthor.

    Fonte única de verdade dos dois documentos — antes a regra estava duplicada no PDF e na
    planilha e elas divergiram: o PDF saía em caixa e o Excel em unidade (pedido do diretor
    07/2026, "tem que sair tudo em unidade Master").

    Devolve `(qtd, preco, unidade)`, com **`qtd × preco` sempre igual a `qtd_un × custo_unit`** —
    o valor da linha não pode mudar por causa da conversão.
    • com fator (`qtunitcx` > 1): qtd em CAIXAS e preço da CAIXA (`custo_unit × fator`);
    • sem fator: a unidade do Winthor já É a master → devolve como está ("un").
    O fator vem do `QTUNITCX`/`QTUNIT`, não do texto da embalagem — os dois divergem em alguns
    cadastros (ex.: cód. 57474, embalagem diz CX/0100/UN mas o fator real é 10).

    ⚠️ Só o RÓTULO da unidade sai do texto da `embalagem` (`FD/8X192/UN` → "FD"); o NÚMERO
    continua vindo do fator, pelo motivo acima. Antes devolvia "CX" sempre que houvesse
    fator, e o comprador — que confere o PDF da JOGA contra o 211 linha a linha — via "CX"
    onde o Winthor imprime "FD" (achado 08/2026, comparando o pedido 565848)."""
    q = _n(qtd_un)
    cx = _n(qtunitcx)
    custo = _n(custo_unit)
    if cx > 1 and q > 0:
        n_cx = math.ceil(q / cx)
        return n_cx, _round(custo * cx, 4), _rotulo_master(embalagem)
    return int(round(q)), _round(custo, 4), "UN"


def _rotulo_master(embalagem):
    """"FD/8X192/UN" → "FD". Só letras, no máx. 3; qualquer coisa estranha cai em "CX"."""
    pref = (str(embalagem or "").split("/")[0] or "").strip().upper()
    return pref if (pref.isalpha() and 1 <= len(pref) <= 3) else "CX"


def _round(v, n=2):
    return round(v, n) if isinstance(v, (int, float)) else v


# cobertura em dias (regra OFICIAL da planilha: ROUNDUP(QTDISP/(GIROMESUNID/30));
# giro<=0 -> 9999 não calculável; estoque<=0 com giro -> 0). Faixas fixas (independem de
# parâmetro) — espelham GRAFICO COBERTURA ESTOQUE / resumo_cobertura.
_FAIXAS_COB_LIM = [("0-30", 30), ("31-60", 60), ("61-90", 90), ("91-120", 120), ("121+", 10**9)]


def cobertura_dias_oficial(qtdisp, giro_dia):
    if giro_dia <= 0:
        return 9999
    if qtdisp <= 0:
        return 0
    return math.ceil(qtdisp / giro_dia)


def cobertura_faixa_de(cob_dias):
    for nome, hi in _FAIXAS_COB_LIM:
        if cob_dias <= hi:
            return nome
    return "121+"


# Status/faixa de "produto novo": item que NUNCA vendeu e acabou de chegar. Vale nos dois eixos
# (`parado_faixa`, da aba Estoque parado, e `status_parado`, do Cockpit) — é o mesmo conceito, e
# duas grafias diferentes era como a Ruptura acabou com três implementações fora de sincronia.
PARADO_NOVO = "novo"


def eh_parado(p):
    """True quando o item conta como CAPITAL PARADO de verdade.

    FONTE ÚNICA da pergunta "isto está parado?" — a resposta é lida em 4 lugares (KPI de capital
    parado e alerta do Cockpit, "maiores ofensores", coluna de valor parado da aba Fornecedores).
    `status_parado` deixou de ser booleano quando ganhou o `novo`: quem testar só a verdade do
    campo volta a somar produto recém-chegado como dead stock."""
    st = p.get("status_parado")
    return bool(st) and st != PARADO_NOVO


# faixa de "dias parado" p/ o relatório de Estoque Parado — indicador.
# Partição inteira ≥ início (sem gap/overlap); <15 dias ou sem estoque = fora do parado (None).
def parado_faixa_de(dias_sem_venda, qtdisp, dias_sem_entrada=None, novo_dias=15):
    """⚠️ **Quem nunca vendeu conta os dias a partir da ENTRADA, não do infinito.**

    Até 08/2026 `dias_sem_venda is None` virava 10**9 e caía direto em "121+" — mesmo que a
    mercadoria tivesse chegado ontem. Reclamação do diretor: "os produtos novos estão caindo
    como itens parados, sem venda". Medido no BI real: dos 272 itens em 121+, **41 nunca
    venderam**, e só 23 desses tinham entrada de fato antiga; os outros 18 estavam rotulados
    "parado 121+ dias" com chegada de 3 a 90 dias — o rótulo era simplesmente falso.

    Agora: nunca vendeu + entrada dentro de `novo_dias` → **`novo`** (10 itens, R$ 10,3k);
    nunca vendeu + entrada mais velha → a faixa VERDADEIRA da chegada (mais 8 itens saem do
    121+). Total da aba intacto: 927 itens / R$ 447.784,24 nos dois lados — a invariante
    "as faixas somam o total" continua valendo.

    ⚠️ **NÃO basta "chegou há menos de 15 dias"** (foi a leitura literal do pedido). Ela pega
    85 itens, e 75 já venderam antes — é reposição de item normal, não produto novo. Pior: o
    cód. 57071 (última venda há **1.249 dias**, chegado há 9, R$ 2.607) sairia do 121+ para um
    card chamado "Produtos novos", escondendo exatamente a compra que precisa aparecer. É por
    isso que a regra exige NUNCA TER VENDIDO, e não só a entrada recente.

    ⚠️ Sem data de entrada o item continua em "121+" (comportamento de antes). São 0 casos hoje,
    mas é o lado conservador: na dúvida ele aparece como parado, não some atrás de "novo".

    ⚠️ Isto **afrouxa o 121+ sem ninguém mexer na operação** (R$ 116.744 → R$ 102.680).
    Comparar antes×depois uma vez, senão parece ganho de gestão.
    """
    if qtdisp <= 0:
        return None
    if dias_sem_venda is None:
        if dias_sem_entrada is None:
            return "121+"
        if dias_sem_entrada < novo_dias:
            return PARADO_NOVO
        d = dias_sem_entrada
    else:
        d = dias_sem_venda
    if d < 15:
        return None
    if d <= 30:
        return "15-30"
    if d <= 60:
        return "31-60"
    if d <= 90:
        return "61-90"
    if d <= 120:
        return "91-120"
    return "121+"


# ───────────────────────── pedido de compra real (Winthor) ─────────────────────────
def montar_ja_pedida(cab_rows, item_rows, hoje=None, dias=180):
    """Pedido de compra REAL em ABERTO por produto, a partir do Winthor (PCPEDIDO×PCITEM).
    Ativo = emitido nos últimos `dias` (DTEMISSAO) — regra v3 da planilha (validada).
    Aberto = max(0, QTPEDIDA − QTENTREGUE): o gerencial já reflete o recebido, então só o
    aberto entra na projeção (não duplica estoque). Retorna {cod: qt_aberta}."""
    hoje = hoje or date.today()
    corte = hoje - timedelta(days=int(dias))
    ativos = {int(_n(r.get("NUMPED"))) for r in cab_rows
              if (_parse_dt(r.get("DTEMISSAO")) or date.min) >= corte}
    out = {}
    for r in item_rows:
        if int(_n(r.get("NUMPED"))) not in ativos:
            continue
        aberto = _n(r.get("qtped")) - _n(r.get("qtentregue"))
        if aberto <= 0:
            continue
        cod = int(_n(r.get("CODPROD")))
        out[cod] = out.get(cod, 0.0) + aberto
    return out


# ───────────────────────── tributação prevista do pedido ─────────────────────────
# O Orçamento mede o realizado por PCPEDIDO[VLTOTAL], que é a NF CHEIA (mercadoria + IPI + ST) —
# validado no pedido 565684: VLTOTAL 44.982,01 = 39.536,28 de mercadoria + 5.445,73 de IPI.
# A sugestão de compra saía só em mercadoria, então o comprador planejava numa régua e consumia
# a meta em outra (7,1% no agregado de 120d; 13,8% num fornecedor com IPI cheio).
#
# FONTE PRIMÁRIA = a TRIBUTAÇÃO DE ENTRADA do próprio ERP (rotina 212), publicada no dataset
# como `TRIB_ENTRADA` (PCTRIBENTPROD × PCTRIBFIGURA): produto × filial × UF de origem × tipo de
# fornecedor → figura → PERIPI/PERCST. Medida em pedidos reais: **acerta 100%** das linhas em
# que a figura existe (54% dos itens). É a única fonte determinística — e, por ser cadastro
# fiscal, muda ANTES do histórico, que é justamente onde toda previsão por histórico falha.
#
# ⚠️ NÃO troque a ordem da cascata sem medir. O que já foi testado e REPROVADO como primária:
#   · `PCPRODUT[PERCIPI]` cru — é o IPI de VENDA (rotina 271), não o de compra. Erra as viradas.
#   · histórico de `PCITEM[PERIPI]` — é o passado; em 21/07/2026 o redutor de 35% do IPI caiu
#     (9,75 = 15 × 0,65) e o histórico continuou prevendo a alíquota velha por semanas.
#   · `PCEST[PERCIPIULTENT]`, pedido anterior, NCM, figura sem o cadastro — todos abaixo.
# O cadastro entra só nos dois papéis em que é bom: dizer quem é ISENTO (alíquota 0, acerto
# 97%) e cobrir o item sem figura. Regra completa e números em `docs/estoque/planilha_v3.md`.
def montar_tributacao(cab_rows, item_rows, hoje=None, dias=180):
    """Alíquotas EFETIVAS de IPI/ST praticadas, extraídas do pedido de compra REAL
    (PCPEDIDO×PCITEM — os mesmos dados que o já-pedido já carrega, sem query nova).

    `VLIPI`/`VLST` do PCITEM são UNITÁRIOS (validado: Σ QTPEDIDA×VLIPI = 5.445,73 = o IPI do
    relatório 211 do pedido 565684). Como `PCITEM[PTABELA]` vem vazio nesta base, o preço
    unitário é derivado de `VLIPI ÷ (PERIPI/100)` — e com ele sai o **ST efetivo sobre a
    mercadoria** (`VLST ÷ preço`), que é o que a sugestão precisa: evita reconstruir MVA/base
    reduzida/crédito de ICMS. Medido no fornecedor 113: VLST/preço = 20,71% constante em todas
    as linhas, contra PERCST=20,05% (a diferença é a majoração da base, que o fator já embute).

    Retorna {"par": {(codfornec, codprod): {ipi, st, fonte}}, "forn": {codfornec: {ipi, st}}},
    com as alíquotas em % (15.0 = 15%). Alíquota do par = MODA das ocorrências na janela."""
    hoje = hoje or date.today()
    corte = hoje - timedelta(days=int(dias))
    # NUMPED é sequencial → serve de desempate "mais recente" sem depender da data do item
    forn_de, emissao_de = {}, {}
    for r in cab_rows:
        dt = _parse_dt(r.get("DTEMISSAO")) or date.min
        if dt < corte:
            continue
        n = int(_n(r.get("NUMPED")))
        forn_de[n] = int(_n(r.get("CODFORNEC")))
        emissao_de[n] = dt

    par, forn_acc = {}, {}
    for r in item_rows:
        n = int(_n(r.get("NUMPED")))
        if n not in forn_de:
            continue
        ipi = _n(r.get("periipi"))
        st_perc = _n(r.get("percst"))
        vlipi, vlst = _n(r.get("vlipi")), _n(r.get("vlst"))
        # ST efetivo sobre a mercadoria: só dá pra derivar o preço quando a linha tem IPI.
        # Sem isso, cai no PERCST cru (aproximação — subestima um pouco, não inventa imposto).
        if vlst > 0 and ipi > 0 and vlipi > 0:
            st = vlst / (vlipi / (ipi / 100.0)) * 100.0
        elif vlst > 0:
            st = st_perc
        else:
            st = 0.0
        cod = int(_n(r.get("CODPROD")))
        f = forn_de[n]
        par.setdefault((f, cod), {"ipi": [], "st": []})
        par[(f, cod)]["ipi"].append(ipi)
        par[(f, cod)]["st"].append(st)
        a = forn_acc.setdefault(f, {"ipi": [], "st": []})
        a["ipi"].append(ipi)
        a["st"].append(st)

    # MODA das ocorrências do par, não a do último pedido: medindo 1.487 linhas fora da amostra,
    # a moda acerta 86,4% contra 85,7% do "último vence" e é mais estável — a alíquota do MESMO
    # produto oscila (a GALVANOTEK alterna 9,75% e 15% no cód. 42334 entre pedidos da mesma
    # semana), então o último pedido pode ser o atípico.
    par = {k: {"ipi": _moda(a["ipi"]), "st": _moda(a["st"]), "fonte": "pedido_real"}
           for k, a in par.items()}
    # perfil do fornecedor = MODA das linhas (não a média): o fornecedor que nunca cobra IPI
    # tem de projetar 0 mesmo tendo um item atípico, e o que cobra sempre tem de projetar a
    # alíquota cheia mesmo com uma linha isenta no meio.
    forn = {}
    for f, a in forn_acc.items():
        forn[f] = {"ipi": _moda(a["ipi"]), "st": _moda(a["st"]), "n": len(a["ipi"])}
    return {"par": par, "forn": forn}


def _moda(vals):
    """Valor mais frequente (arredondado a 2 casas); 0.0 se vazio. Empate → o maior."""
    if not vals:
        return 0.0
    cont = {}
    for v in vals:
        k = round(float(v or 0), 2)
        cont[k] = cont.get(k, 0) + 1
    return max(cont.items(), key=lambda kv: (kv[1], kv[0]))[0]


def montar_trib_entrada(rows):
    """Indexa a `TRIB_ENTRADA` (tributação de entrada do ERP) por
    (codprod, codfilial, uforigem, tipofornec) → {ipi, st}.

    A tabela vem do dataset já com o join PCTRIBENTPROD × PCTRIBFIGURA resolvido e filtrada
    nas filiais do estoque. Chave completa importa: o MESMO produto tem figuras diferentes por
    UF de origem (cód. 42313 vindo de SP → figura 91 = 15%; vindo de SC → figura 33 = 0%)."""
    out = {}
    for r in (rows or []):
        cod = int(_n(r.get("CODPROD")))
        chave = (cod, str(r.get("CODFILIAL") or "").strip(),
                 str(r.get("UFORIGEM") or "").strip().upper(),
                 str(r.get("TIPOFORNEC") or "").strip().upper())
        out[chave] = {"ipi": _round(_n(r.get("PERIPI")), 2), "st": _round(_n(r.get("PERCST")), 2)}
    return out


def tributacao_de(trib_map, codfornec, codprod, percipi_cadastro=None,
                  trib_entrada=None, uf_fornec=None, tipo_fornec="I", filiais=None):
    """(ipi%, st%, fonte) para um item que ainda NÃO foi pedido.

    Cascata — a ordem foi medida em pedidos reais, não escolhida por intuição:
      1. cadastro = 0            → **isento**  (acerto 97%; o cadastro é confiável p/ dizer quem
                                   NÃO tem IPI, mesmo não servindo p/ dizer a alíquota)
      2. TRIB_ENTRADA (figura)   → alíquota do ERP (acerto **100%** onde existe)
      3. cadastro                → item sem figura para aquela UF (acerto baixo — vira estimativa)
      4. histórico do par        → sem cadastro
      5. zero                    → nunca inventa imposto

    A `fonte` viaja até a tela: os degraus 3 e 4 saem marcados como estimativa, com o percentual
    editável no pedido, porque é neles que mora todo o erro residual."""
    cad = _n(percipi_cadastro) if percipi_cadastro is not None else None
    # 1) isento no cadastro manda em tudo
    if cad is not None and cad == 0:
        return (0.0, 0.0, "isento_cadastro")
    # 2) tributação de entrada do ERP (a única fonte determinística)
    if trib_entrada and uf_fornec:
        cod = int(_n(codprod))
        uf = str(uf_fornec).strip().upper()
        tipo = str(tipo_fornec or "I").strip().upper()
        for fil in (filiais or FILIAIS_TRIB_PADRAO):
            hit = trib_entrada.get((cod, str(fil), uf, tipo))
            if hit and hit["ipi"] > 0:
                return (hit["ipi"], hit["st"], "trib_entrada")
    # 3) cadastro (sem figura para esta origem)
    if cad:
        return (cad, 0.0, "cadastro")
    # 4) histórico do pedido real
    f, c = int(_n(codfornec)), int(_n(codprod))
    hit = ((trib_map or {}).get("par") or {}).get((f, c))
    if hit:
        return (hit["ipi"], hit["st"], "pedido_real")
    pf = ((trib_map or {}).get("forn") or {}).get(f)
    if pf:
        return (pf["ipi"], pf["st"], "perfil_fornecedor")
    return (0.0, 0.0, "sem_dado")


# filiais do estoque, na ordem de preferência da busca da figura (o snapshot é agregado por
# produto, então a figura é procurada na 1ª filial que tiver regra cadastrada)
FILIAIS_TRIB_PADRAO = ("3", "5")

# fontes em que o número é CONFIÁVEL (o resto vira "estimativa" na tela e no PDF)
TRIB_FONTES_FIRMES = ("isento_cadastro", "trib_entrada")


# ───────────────────────── produtos ─────────────────────────
def construir_produtos(snapshot, end_map, prod_map, forn_map, comprador_map, venda_map, params,
                       hoje=None, venda_mensal_map=None, ja_pedida_map=None, embalagem_map=None,
                       preco_venda_map=None, venda_ant_map=None, venda_mensal_rs_map=None,
                       tributacao_map=None, trib_entrada_map=None, bloqueio_map=None):
    """snapshot: linhas do PCEST; end_map: {cod: qt_end}; prod_map/forn_map: cadastro;
    comprador_map: {matricula: nome}; venda_map: {cod:{venda,custo,qtd}} líquido do RCA.
    venda_mensal_map: {cod:{AnoMes:qtd}} p/ forecast (opcional; só quando forecast ligado).
    ja_pedida_map: {cod: qt} pedido de compra REAL em ABERTO (Winthor, qtped−entregue, 180d).
    embalagem_map: {cod: {qtunit, volume, ...}} caixa/cubagem do PCEMBALAGEM.
    tributacao_map: saída de `montar_tributacao` — histórico de IPI/ST (fallback da cascata).
    trib_entrada_map: saída de `montar_trib_entrada` — tributação de entrada do ERP (primária).
    bloqueio_map: {cod: [{qtbloq, dtultent, qtultent}]} — posição de bloqueio POR FILIAL, para
      separar pré-entrada de avaria sem o falso positivo da agregação (ver `qt_em_transicao`).
      Opcional: sem ele, a pré-entrada cai no cálculo agregado antigo.
    Mantém só produtos do cadastro (revenda/não-FL)."""
    hoje = hoje or date.today()
    base = params["base_estoque"]
    forecast_on = bool(params.get("forecast"))
    sazonal_on = bool(params.get("forecast_sazonal")) and forecast_on
    fc_meses = int(params.get("forecast_meses", 6))
    arred_cx = bool(params.get("arredonda_cx"))
    out = []
    for r in snapshot:
        cod = int(_n(r.get("CODPROD")))
        cad = prod_map.get(cod)
        if cad is None:
            continue  # fora do universo (não-revenda / FL)

        qtestger   = _n(r.get("qtestger"))
        qtreserv   = _n(r.get("qtreserv"))
        qtbloq     = _n(r.get("qtbloq"))
        qtpend     = _n(r.get("qtpend"))
        qttransito = _n(r.get("qttransito"))
        custofin   = _n(r.get("custofin"))
        qt_end     = _n(end_map.get(cod))

        # QTDISP conforme a base escolhida
        # gerencial = DISPONÍVEL = QTESTGER − avaria (QTBLOQUEADA) − reserva (QTRESERV): itens em
        # avaria ou reservados não estão disponíveis p/ venda (decisão do diretor 2026-07;
        # substitui o "QTESTGER cru" anterior). Ex.: item 44094 = 86 − 81 − 5 = 0.
        # endereco = estoque WMS endereçado (usado só na validade/FEFO).
        if base == "endereco":
            qtdisp = qt_end
        else:  # gerencial (default v3) — desconta avaria e reserva
            qtdisp = qtestger - qtbloq - qtreserv
        # valor financeiro com piso em zero: estoque negativo é erro de saldo, não vale R$ negativo
        # (alinha o total ao BASE PRODUTOS; mantém qtdisp negativo visível na tela)
        valor = max(0.0, qtdisp) * custofin

        # pedido de compra REAL em aberto (Winthor) — já descontado o que foi entregue
        qtd_ja_pedida = _n((ja_pedida_map or {}).get(cod))
        # caixa: QTUNIT do PCEMBALAGEM (oficial v3), fallback QTUNITCX do cadastro
        emb = (embalagem_map or {}).get(cod) or {}
        qtunit_emb = _n(emb.get("qtunit"))

        giro_media3 = _giro_mensal(r, params["giro_base"])
        serie_am = (venda_mensal_map or {}).get(cod)
        giro_forecast = previsao_giro_mensal(serie_am, fc_meses, hoje) if forecast_on else None
        saz = fatores_sazonais(serie_am, hoje) if sazonal_on else None
        nivel_base_dia = None
        if saz is not None:
            giro_mes, giro_fonte = previsao_giro_sazonal(saz, hoje.month), "sazonal"
            nivel_base_dia = saz["media_mensal"] / 30.0
        elif forecast_on and giro_forecast is not None:
            giro_mes, giro_fonte = giro_forecast, "forecast"
        else:
            giro_mes, giro_fonte = giro_media3, "media3"
        # fallback ITEM NOVO: giro média-3m deu 0 (3 meses fechados ainda zerados p/ item novo)
        # mas há venda real recente no RCA → deriva o giro dos meses desde o lançamento. Só no
        # caminho oficial (media3); forecast/sazonal já tratam item novo pela própria série RCA.
        if giro_fonte == "media3" and giro_mes <= 0:
            gnovo = giro_novo_item(serie_am, hoje)
            if gnovo and gnovo > 0:
                giro_mes, giro_fonte = gnovo, "novo_item"
            else:
                # ainda 0: item que só vendeu no mês corrente → usa a venda crua do mês (sócio 07/2026)
                gmes = giro_mes_corrente(serie_am, hoje)
                if gmes and gmes > 0:
                    giro_mes, giro_fonte = gmes, "mes_corrente"
        giro_dia = giro_mes / 30.0
        serie = [_n(r.get("giro_m1")), _n(r.get("giro_m2")), _n(r.get("giro_m3"))]
        # série mensal (12 meses, ordem cronológica) p/ sparkline e gráfico do 360°.
        # INCLUI o mês corrente (_meses_ate) — o diretor precisa ver a venda do mês em
        # andamento; a tela marca a última barra como parcial p/ não parecer queda.
        serie_meses = _meses_ate(hoje, 12)
        serie_mensal = ([_round(_n(serie_am.get(am))) for am in serie_meses]
                        if serie_am else None)
        # mesma janela em R$ — alimenta SÓ o gráfico "venda 12m" do 360°. Mapa separado de
        # propósito: o de quantidade continua intocado alimentando giro/forecast/item novo.
        serie_am_rs = (venda_mensal_rs_map or {}).get(cod)
        serie_mensal_rs = ([_round(_n(serie_am_rs.get(am)), 2) for am in serie_meses]
                           if serie_am_rs else None)

        cobertura = (qtdisp / giro_dia) if giro_dia > 0 and qtdisp > 0 else None
        # cobertura em dias inteiros + faixa (regra oficial da planilha; faixa fixa)
        cobertura_dias = cobertura_dias_oficial(qtdisp, giro_dia)
        cobertura_faixa = cobertura_faixa_de(cobertura_dias)
        # excesso real só quando a cobertura é CALCULÁVEL e alta (separa de "sem giro" no 121+)
        excesso_real = giro_dia > 0 and cobertura_dias > 120

        dt_saida = _parse_dt(r.get("dtultsaida"))
        dias_sem_venda = (hoje - dt_saida).days if dt_saida else None
        # última ENTRADA (PCEST[DTULTENT]) — pedido do diretor 07/2026: sem ela dá pra ver que o
        # item não sai, mas não dá pra saber se é estoque velho parado ou compra recente errada.
        # Já vinha na query do snapshot (as duas fontes); só não era repassada.
        dt_entrada = _parse_dt(r.get("dtultent"))
        dias_sem_entrada = (hoje - dt_entrada).days if dt_entrada else None

        # fornecedor / comprador
        fornec_cod = cad.get("CODFORNEC")
        forn = forn_map.get(int(_n(fornec_cod))) if fornec_cod not in (None, "") else None
        codcomprador = int(_n((forn or {}).get("CODCOMPRADOR"))) if forn and (forn.get("CODCOMPRADOR") not in (None, "")) else None
        comprador = comprador_map.get(codcomprador) if codcomprador is not None else None

        # lead time do abastecimento = parâmetro da tela (o comprador controla manual;
        # decisão 07/2026). Antes priorizava o PRAZOENTREGA do fornecedor, mas mexer no
        # slider não afetava quem já tinha prazo cadastrado (~95% dos itens) e parecia
        # "não funcionar" — agora o slider vale p/ todos. (A previsão de ENTREGA do
        # orçamento segue usando o prazo real do fornecedor; é outra finalidade.)
        lead = params["lead_time"]
        est_seg = giro_dia * params["dias_seguranca"]
        rop = giro_dia * lead + est_seg
        # alvo medido NO MOMENTO DA ENTREGA: soma o consumo do lead time. O pedido só
        # chega em `lead` dias e o estoque continua caindo até lá — sem isso a compra
        # assume "entrega hoje" e sub-dimensiona (verificado pelo diretor 07/2026).
        # Ex.: lead 10 + cobertura 45 = alvo de 55 dias de giro.
        est_alvo = giro_dia * (lead + params["cobertura_total"])
        # posição efetiva = disponível + pedido de compra REAL em aberto (Winthor).
        # Como o gerencial já reflete o que foi recebido, o "já pedido" é só o ABERTO
        # (qtped−entregue) — evita comprar de novo o que já está pedido e não duplica estoque.
        # + o que está EM TRANSIÇÃO (pré-entrada: chegou, aguarda liberação). Entra aqui e NÃO no
        # `qtdisp` de propósito: a mercadoria ainda não pode ser vendida, então continua contando
        # como ruptura e fora do valor de estoque — mas não se compra de novo o que já chegou.
        qt_transicao = qt_em_transicao(r.get("qtbloq"), r.get("dtultent"), hoje=hoje,
                                       linhas=(bloqueio_map or {}).get(cod))
        estoque_projetado = qtdisp + qtd_ja_pedida + qt_transicao
        cobertura_proj = (estoque_projetado / giro_dia) if giro_dia > 0 and estoque_projetado > 0 else None
        sugestao = max(0.0, est_alvo - estoque_projetado)

        # prioridade de abastecimento sobre o ESTOQUE PROJETADO (metodologia v3)
        lead_un = giro_dia * lead
        seg_un = giro_dia * params["dias_seguranca"]
        if giro_dia <= 0:
            status_abast = "sem_giro" if qtdisp > 0 else "ok"
        elif estoque_projetado <= lead_un:
            status_abast = "urgente"
        elif estoque_projetado <= lead_un + seg_un:
            status_abast = "alta"
        elif estoque_projetado < est_alvo:
            status_abast = "atencao"
        elif cobertura_proj is not None and cobertura_proj > params["excesso_cob"]:
            status_abast = "excesso"
        else:
            status_abast = "ok"

        # cobertura crítica / atenção de abastecimento — bandas FIXAS (manual: cobertura até 30
        # dias = atenção, dividida em 0-15 urgente / 16-30). Não depende de parâmetro de compra.
        estoque_zero = qtdisp <= 0
        if giro_dia <= 0:
            status_ruptura = None
        else:
            cob_eff = cobertura if (cobertura is not None) else 0.0
            if cob_eff <= 15:
                status_ruptura = "0-15"
            elif cob_eff <= 30:
                status_ruptura = "16-30"
            else:
                status_ruptura = None

        # estoque parado / dead stock — bandas FIXAS por dias sem venda (manual da planilha:
        # ATENCAO 60-90, CRITICO 90-120, MUITO CRITICO 120+). Independe de parâmetro — o campo
        # "parado_atencao" vira só filtro de exibição (mín. dias) na tela/export, não desloca faixa.
        # ⚠️ Mesma régua do `parado_faixa_de`: quem NUNCA vendeu conta os dias a partir da
        # ENTRADA. Os dois eixos têm de concordar — a aba Estoque parado lê `parado_faixa` e o
        # Cockpit lê `status_parado`, e deixar só um corrigido é a armadilha da Ruptura de novo
        # (a tela dizia 4 e o drill 3 porque duas das três implementações não foram atualizadas).
        sem_giro = giro_dia <= 0 and qtdisp > 0
        _d_parado = dias_sem_venda
        if _d_parado is None and dias_sem_entrada is not None:
            _d_parado = dias_sem_entrada
        if qtdisp <= 0:
            status_parado = None
        elif dias_sem_venda is None and dias_sem_entrada is not None and dias_sem_entrada < params["novo_dias"]:
            status_parado = PARADO_NOVO          # chegou agora e ainda não teve chance de vender
        elif _d_parado is None:
            status_parado = "muito_critico"      # nunca vendeu e sem data de entrada → pior caso
        elif _d_parado >= 120:
            status_parado = "muito_critico"
        elif _d_parado >= 90:
            status_parado = "critico"
        elif _d_parado >= 60:
            status_parado = "atencao"
        else:
            status_parado = None

        if dt_saida is None:
            status_saida = "sem_saida"
        elif dias_sem_venda <= 30:
            status_saida = "recente"
        elif dias_sem_venda <= 90:
            status_saida = "media"
        else:
            status_saida = "antiga"

        # compra suspensa: tem giro (média 3m, defasada) mas parou de vender há tempo
        # → não sugerir comprar estoque morto (giro está "preso" no histórico). Limiar FIXO 60d
        # (alinha com a faixa "atenção" do parado; não depende mais do parâmetro de exibição).
        compra_suspensa = (giro_dia > 0 and dias_sem_venda is not None
                           and dias_sem_venda >= 60)

        # venda real (RCA, líquida) do período
        vd = venda_map.get(cod) or {}
        venda = vd.get("venda", 0.0)
        custo_vendido = vd.get("custo", 0.0)
        qtd_vendida = vd.get("qtd", 0.0)
        lucro = venda - custo_vendido
        margem = (lucro / venda) if venda else None
        # Preço médio REALIZADO no período do seletor (pedido do diretor 08/2026: "o preço é muito
        # volátil"). Média ponderada pela quantidade — um cliente grande comprando barato puxa
        # para baixo, que é o certo: responde "quanto eu realizo por unidade", não preço de tabela.
        # ⚠️ As duas pontas são LÍQUIDAS. Enquanto a quantidade vinha bruta, esta divisão saía
        # ~12% baixa (a devolução foi 11,7% da quantidade em julho/2026) — foi por isso que abater
        # a quantidade veio junto com este número, e não depois.
        # Difere do `preco_venda` abaixo, que é fixo em 3 meses porque alimenta a venda perdida
        # (tem de acompanhar a janela do giro, não a do seletor).
        preco_medio = (venda / qtd_vendida) if qtd_vendida else None

        # crescimento vs. o MESMO período do ANO ANTERIOR (líquida × líquida — comparar com a
        # bruta inflaria o número). Sem venda no ano passado (item novo, ou período anterior a
        # 2024, que é onde o RCA começa) → None ⇒ a tela mostra "—", nunca −100% nem infinito.
        venda_ant = _n(((venda_ant_map or {}).get(cod) or {}).get("venda"))
        crescimento = ((venda - venda_ant) / venda_ant) if venda_ant > 0 else None

        # XYZ — coeficiente de variação da série de 3 meses
        media = statistics.mean(serie) if serie else 0.0
        if media > 0:
            cv = statistics.pstdev(serie) / media
            xyz = "X" if cv < params["xyz_x"] else ("Y" if cv < params["xyz_y"] else "Z")
        else:
            cv, xyz = None, None

        qtunitcx = _n(cad.get("QTUNITCX"))
        # caixa oficial v3 = QTUNIT do PCEMBALAGEM; fallback no QTUNITCX do cadastro
        caixa = qtunit_emb if qtunit_emb > 1 else qtunitcx
        # sugestão em CAIXAS quando há fator (>1); SEM fator (=1, cadastro incompleto) fica em
        # UNIDADES — não força "1 cx". Normaliza sozinho quando o TI cadastrar o QTUNIT no Winthor.
        sugestao_bruta = sugestao
        if sugestao <= 0:
            sugestao_cx = 0
        elif caixa > 1:
            sugestao_cx = math.ceil(sugestao / caixa)   # caixas fechadas
        else:
            sugestao_cx = math.ceil(sugestao)           # sem fator: unidades (o front mostra "un")
        if arred_cx and caixa > 1 and sugestao > 0:
            sugestao = sugestao_cx * caixa  # sugestão em unidades, arredondada p/ caixa fechada
        # valor da compra líquida sugerida (caixa fechada × custo, ou unidades × custo se sem fator).
        # ⚠️ Usa o custo JÁ ARREDONDADO a 4 casas — o mesmo que sai em `custo_unit` e vai virar
        # preço no pedido/PDF/planilha. Com o `custofin` cru, a tela somava numa precisão e o
        # documento em outra: no pedido 45 (GALVANOTEK, 49 itens) a tela dizia R$ 39.536,38 e o
        # PDF R$ 39.536,28 — e quem bate com o Winthor é o PDF, porque é o preço arredondado que
        # a planilha de importação entrega ao ERP.
        custo_doc = _round(custofin, 4)
        valor_sugerido_liq = (sugestao_cx * caixa * custo_doc) if caixa > 1 else (sugestao_cx * custo_doc)
        # régua da NF (= a do Orçamento, que lê PCPEDIDO[VLTOTAL]): mercadoria + IPI + ST previstos.
        # A UF/tipo do FORNECEDOR entram na chave: a mesma peça tem alíquota diferente conforme a
        # origem (é o cadastro fiscal de entrada do ERP que manda, não o cadastro do produto).
        _f_cad = (forn_map or {}).get(int(_n(cad.get("CODFORNEC")))) or {}
        perc_ipi, perc_st, trib_fonte = tributacao_de(
            tributacao_map, cad.get("CODFORNEC"), cod, cad.get("PERCIPI"),
            trib_entrada=trib_entrada_map, uf_fornec=_f_cad.get("ESTADO"),
            tipo_fornec=_f_cad.get("TIPOFORNEC") or "I")
        vl_ipi = valor_sugerido_liq * perc_ipi / 100.0
        vl_st = valor_sugerido_liq * perc_st / 100.0
        valor_sugerido_nf = valor_sugerido_liq + vl_ipi + vl_st

        # cubagem da caixa: PCEMBALAGEM[VOLUME] (oficial); se faltar (muito item sem cadastro
        # na embalagem), deriva do PCPRODUT[VOLUME] (unitário × fator de caixa) — mesma fonte
        # que a aba Logística usa. Assim a cubagem deixa de vir vazia p/ a maioria.
        _fator_cx = caixa if caixa and caixa > 1 else 1
        _med = medidas_unitarias(cad, _fator_cx)
        cub_caixa = _med["vol"] * _fator_cx if _med["confiavel"] else 0.0
        peso_caixa = _med["bruto"] * _fator_cx if _med["confiavel"] else 0.0

        # VENDA PERDIDA acumulada na ruptura = dias em ruptura × giro/dia × PREÇO DE VENDA.
        # dias em ruptura: proxy = dias_sem_venda (dias desde a última venda), com TETO de 60 dias.
        # preço de venda: realizado médio de 3m (janela FIXA, estável — não muda com o filtro de
        # período); o preço de tabela do BI (PCPRODUT[PVENDA]) está vazio. Fallback no custo se
        # não houver preço realizado. Só p/ item em ruptura (estoque<=0 e giro>0); senão 0.
        preco_venda = _n((preco_venda_map or {}).get(cod)) or custofin
        if qtdisp <= 0 and giro_dia > 0:
            _dias_rup = min(dias_sem_venda if dias_sem_venda is not None else 30, 60)
            venda_perdida = _dias_rup * giro_dia * preco_venda
        else:
            venda_perdida = 0.0

        # status executivo + ação recomendada (taxonomia v3 — clareza pro comprador)
        tem_compra = sugestao_cx > 0
        if qtdisp <= 0:
            if qt_transicao > 0:
                # Chegou e aguarda liberação. Ganha da rotulagem de ruptura MESMO quando ainda
                # sobra compra a fazer: dizer "ruptura sem pedido" para mercadoria que está no
                # armazém foi exatamente o que levou o comprador a pedir de novo. O quanto ainda
                # falta comprar continua visível na coluna Sugerido.
                status_exec = "aguardando_liberacao"
            elif qtd_ja_pedida <= 0:
                status_exec = "ruptura_sem_pedido"
            else:
                status_exec = "ruptura_pedido_parcial" if tem_compra else "ruptura_pedido_cobre"
        elif tem_compra:
            if qtd_ja_pedida > 0:
                status_exec = "compra_complementar"
            else:
                status_exec = {"urgente": "compra_urgente", "alta": "compra_alta"}.get(status_abast, "programar_compra")
        else:
            status_exec = "pedido_cobre" if qtd_ja_pedida > 0 else "estoque_ok"
        if not tem_compra:
            acao_rec = "acompanhar_entrega" if qtd_ja_pedida > 0 else "sem_compra"
        elif estoque_projetado <= lead_un:
            acao_rec = "comprar_imediato"
        elif estoque_projetado <= lead_un + seg_un:
            acao_rec = "negociar_pedido"
        else:
            acao_rec = "programar_compra"
        out.append({
            "codprod": cod,
            "descricao": cad.get("DESCRICAO") or f"PRODUTO {cod}",
            "codfornec": int(_n(fornec_cod)) if fornec_cod not in (None, "") else None,
            "fornecedor": (forn or {}).get("FORNECEDOR") if forn else None,
            "codcomprador": codcomprador,
            "comprador": comprador,
            "codepto": cad.get("CODEPTO"),
            "ncm": cad.get("NCM"), "marca": cad.get("MARCA"),
            "embalagem": cad.get("EMBALAGEM"),
            "qtunitcx": qtunitcx or None,
            "qtdisp": _round(qtdisp), "disponivel": _round(qtdisp),
            "qtestger": _round(qtestger), "qt_end": _round(qt_end),
            "qtreserv": _round(qtreserv), "qtbloq": _round(qtbloq),
            "qttransito": _round(qttransito), "qtpend": _round(qtpend),
            "custo_unit": _round(custofin, 4),
            "valor": _round(valor),
            "giro_mes": _round(giro_mes), "giro_dia": _round(giro_dia, 3),
            "giro_media3": _round(giro_media3), "giro_forecast": _round(giro_forecast) if giro_forecast is not None else None,
            "giro_fonte": giro_fonte, "serie_mensal": serie_mensal,
            "nivel_base_dia": _round(nivel_base_dia, 3) if nivel_base_dia is not None else None,
            "fatores_sazonais": saz["fatores"] if saz else None,
            "giro_cx": _round(giro_mes / qtunitcx, 2) if qtunitcx else None,
            "venda": _round(venda), "lucro": _round(lucro), "qtd_vendida": _round(qtd_vendida),
            "preco_medio": _round(preco_medio, 4) if preco_medio is not None else None,
            "venda_ano_ant": _round(venda_ant) if venda_ant else None,
            "crescimento": _round(crescimento * 100, 1) if crescimento is not None else None,
            "serie_mensal_rs": serie_mensal_rs, "serie_mensal_meses": serie_meses,
            "margem": _round(margem * 100, 1) if margem is not None else None,
            "preco_venda": _round(preco_venda, 4), "venda_perdida": _round(venda_perdida),
            "serie_giro": [_round(x) for x in serie],
            "cobertura": _round(cobertura, 1) if cobertura is not None else None,
            "cobertura_dias": cobertura_dias, "cobertura_faixa": cobertura_faixa,
            "excesso_real": excesso_real,
            "dias_sem_venda": dias_sem_venda,
            "dtultsaida": dt_saida.isoformat() if dt_saida else None,
            "dias_sem_entrada": dias_sem_entrada,
            "dtultent": dt_entrada.isoformat() if dt_entrada else None,
            "cv": _round(cv, 3) if cv is not None else None,
            "xyz": xyz,
            "lead_efetivo": _round(lead),
            "rop": _round(rop), "est_seguranca": _round(est_seg),
            "est_alvo": _round(est_alvo), "sugestao_compra": _round(sugestao),
            "sugestao_bruta": _round(sugestao_bruta), "sugestao_cx": sugestao_cx,
            "caixa": _round(caixa) if caixa else None,
            "embalagem_caixa": emb.get("embalagem"),
            "qtd_ja_pedida": _round(qtd_ja_pedida),
            "estoque_projetado": _round(estoque_projetado),
            "qt_transicao": _round(qt_transicao),   # pré-entrada: chegou, aguarda liberação
            "cobertura_proj": _round(cobertura_proj, 1) if cobertura_proj is not None else None,
            "valor_sugerido_liq": _round(valor_sugerido_liq),
            # régua da NF — é ESTA que consome a meta do Orçamento (PCPEDIDO[VLTOTAL])
            "valor_sugerido_nf": _round(valor_sugerido_nf),
            "vl_ipi_sug": _round(vl_ipi), "vl_st_sug": _round(vl_st),
            "perc_ipi": perc_ipi, "perc_st": perc_st, "trib_fonte": trib_fonte,
            # False = veio de estimativa (item sem regra fiscal p/ esta origem) → a tela avisa
            # e o comprador pode corrigir o % antes de gerar o pedido
            "trib_firme": trib_fonte in TRIB_FONTES_FIRMES,
            "status_exec": status_exec, "acao_rec": acao_rec,
            "cubagem_caixa_m3": _round(cub_caixa, 5) if cub_caixa else None,
            "peso_caixa_kg": _round(peso_caixa, 3) if peso_caixa else None,
            "medidas_confiaveis": _med["confiavel"],
            "compra_suspensa": compra_suspensa,
            "status_abast": status_abast,
            "status_ruptura": status_ruptura, "estoque_zero": estoque_zero,
            "status_parado": status_parado,
            "status_saida": status_saida,
            "sem_giro": sem_giro,
            "parado_faixa": parado_faixa_de(dias_sem_venda, qtdisp, dias_sem_entrada,
                                            params["novo_dias"]),
            "curva_abc": None, "curva_giro": None, "abc_xyz": None,
        })

    # curva ABC por VENDA (faturamento do período selecionado) — leitura clássica: A = campeões
    # de venda. Segue o seletor de período (o campo `venda` já é do período). O valor de estoque
    # continua nas visões de capital (Cobertura/Parado/Fornecedores) e `curva_giro` é outra lente.
    _aplicar_curva(out, "venda", "curva_abc", params["abc_a"], params["abc_b"])
    _aplicar_curva(out, "giro_mes", "curva_giro", params["abc_a"], params["abc_b"])
    for p in out:
        if p["curva_abc"] and p["xyz"]:
            p["abc_xyz"] = p["curva_abc"] + p["xyz"]
    return out


def _aplicar_curva(produtos, chave_valor, campo, a, b):
    """Classifica curva ABC por Pareto (% acumulado) sobre `chave_valor`."""
    total = sum(p[chave_valor] or 0 for p in produtos)
    if total <= 0:
        for p in produtos:
            p[campo] = "C"
        return
    acum = 0.0
    for p in sorted(produtos, key=lambda x: x[chave_valor] or 0, reverse=True):
        acum += (p[chave_valor] or 0)
        pct = acum / total * 100
        p[campo] = "A" if pct <= a else ("B" if pct <= b else "C")


# ───────────────────────── cockpit ─────────────────────────
FAIXAS_COB = [
    ("0-30", 0, 30), ("31-60", 31, 60), ("61-90", 61, 90),
    ("91-120", 91, 120), ("121+", 121, float("inf")),
]


def cockpit(produtos):
    valor_total = sum(p["valor"] or 0 for p in produtos)
    venda_total = sum(p["venda"] or 0 for p in produtos)
    lucro_total = sum(p["lucro"] or 0 for p in produtos)
    com_estoque = [p for p in produtos if (p["qtdisp"] or 0) > 0]
    com_giro = [p for p in produtos if (p["giro_dia"] or 0) > 0]
    sem_giro = [p for p in com_estoque if (p["giro_dia"] or 0) <= 0]

    valor_parado = sum(p["valor"] or 0 for p in produtos if eh_parado(p))   # `novo` fica fora
    valor_sem_giro = sum(p["valor"] or 0 for p in sem_giro)

    faixas = []
    for nome, lo, hi in FAIXAS_COB:
        itens = [p for p in com_giro if p["cobertura"] is not None and lo <= p["cobertura"] <= hi]
        faixas.append({"faixa": nome, "qt": len(itens),
                       "valor": _round(sum(p["valor"] or 0 for p in itens))})
    faixas.append({"faixa": "sem giro", "qt": len(sem_giro), "valor": _round(valor_sem_giro)})

    abc = {}
    for c in ("A", "B", "C"):
        itens = [p for p in produtos if p["curva_abc"] == c]
        abc[c] = {"qt": len(itens), "valor": _round(sum(p["valor"] or 0 for p in itens)),
                  "venda": _round(sum(p["venda"] or 0 for p in itens))}

    matriz = {}
    for p in produtos:
        if p["abc_xyz"]:
            cell = matriz.setdefault(p["abc_xyz"], {"qt": 0, "valor": 0.0, "venda": 0.0})
            cell["qt"] += 1
            cell["valor"] += (p["valor"] or 0)
            cell["venda"] += (p["venda"] or 0)
    for v in matriz.values():
        v["valor"] = _round(v["valor"]); v["venda"] = _round(v["venda"])

    def _cont(field, val):
        itens = [p for p in produtos if p[field] == val]
        return {"qt": len(itens), "valor": _round(sum(p["valor"] or 0 for p in itens))}

    repor = [p for p in produtos if (p["sugestao_compra"] or 0) > 0
             and (p["giro_dia"] or 0) > 0 and not p.get("compra_suspensa")]
    suspensos = [p for p in produtos if p.get("compra_suspensa")]
    em_ruptura = [p for p in produtos if p["status_ruptura"]]

    return {
        "valor_total": _round(valor_total),
        "venda_total": _round(venda_total),
        "lucro_total": _round(lucro_total),
        "margem_total": _round(lucro_total / venda_total * 100, 1) if venda_total else None,
        "n_total": len(produtos),
        "n_com_estoque": len(com_estoque),
        "n_com_giro": len(com_giro),
        "n_sem_giro": len(sem_giro),
        "valor_parado": _round(valor_parado),
        "pct_capital_parado": _round(valor_parado / valor_total * 100, 1) if valor_total else 0,
        "valor_sem_giro": _round(valor_sem_giro),
        "faixas_cobertura": faixas,
        "abc": abc,
        "matriz_abc_xyz": matriz,
        "parado": {
            "atencao": _cont("status_parado", "atencao"),
            "critico": _cont("status_parado", "critico"),
            "muito_critico": _cont("status_parado", "muito_critico"),
            # reportado à parte, como o "sem giro": é mercadoria recém-chegada que ainda não
            # vendeu — não entra no capital parado nem no alerta de 120+ dias
            "novo": _cont("status_parado", PARADO_NOVO),
            "sem_giro": {"qt": len(sem_giro), "valor": _round(valor_sem_giro)},
        },
        "ruptura": {
            "f0_15": _cont("status_ruptura", "0-15"),
            "f16_30": _cont("status_ruptura", "16-30"),
            "estoque_zero": sum(1 for p in produtos if p["estoque_zero"] and (p["giro_dia"] or 0) > 0),
            "total": len(em_ruptura),
            "valor": _round(sum(p["valor"] or 0 for p in em_ruptura)),
        },
        "abastecimento": {
            "urgente": _cont("status_abast", "urgente"),
            "alta": _cont("status_abast", "alta"),
            "atencao": _cont("status_abast", "atencao"),
            "excesso": _cont("status_abast", "excesso"),
            "n_repor": len(repor),
            "qt_sugerida": _round(sum(p["sugestao_compra"] or 0 for p in repor)),
            "valor_sugerido": _round(sum((p["sugestao_compra"] or 0) * (p["custo_unit"] or 0) for p in repor)),
            # o mesmo total na régua da NF (mercadoria + IPI + ST), p/ confrontar com o Orçamento
            "valor_sugerido_nf": _round(sum((p["sugestao_compra"] or 0) * (p["custo_unit"] or 0)
                                            * (1 + ((p.get("perc_ipi") or 0) + (p.get("perc_st") or 0)) / 100)
                                            for p in repor)),
            "n_suspensos": len(suspensos),
            "valor_suspenso": _round(sum((p["sugestao_compra"] or 0) * (p["custo_unit"] or 0) for p in suspensos)),
        },
        "valor_risco_venc": None,  # preenchido pelo app a partir do FEFO
    }


# ───────── ciclo de compras + verba por fornecedor (colunas da aba Fornecedores) ─────────
# Pedido do consultor/diretor 07/2026. As duas alimentam `fornecedores()` por um mapa `extra`,
# porque NENHUMA das duas sai de `produtos` (posição de estoque): compra vem de PCPEDIDO e verba
# de PCVERBA. Ambas reusam raw que a aba Lead time / Verbas JÁ carregam — zero query nova.

def ciclo_compras(cab, ini, fim, filiais=None, forn_map=None, cnpj_empresa=None, ciclo_desde=None):
    """{codfornec: {n_pedidos, ciclo_dias, ultima_compra, n_ciclo}} a partir do cabeçalho de
    pedidos (PCPEDIDO).

    Duas perguntas DIFERENTES, de propósito em janelas diferentes:
    • `n_pedidos` = QUANTAS VEZES compramos — conta pedidos (NUMPED distintos) dentro de
      [ini, fim], que é a janela do seletor "Venda" do topo (o "período filtrado no parâmetro"
      que o consultor pediu).
    • `ciclo_dias` = de quanto em quanto tempo compramos — média dos intervalos, apurada sobre
      `ciclo_desde` (12m fixo). Ciclo é COMPORTAMENTO do fornecedor, igual ao lead time: numa
      janela de "mês atual" quase todo fornecedor teria 0 ou 1 pedido e o ciclo sairia nulo,
      então ele andaria conforme o filtro e deixaria de ser comparável.

    ⚠️ O ciclo conta DATAS distintas de emissão, não pedidos: o mesmo fornecedor costuma receber
    vários NUMPED no mesmo dia (um por filial/condição) e contá-los criaria intervalos de 0 dia
    que derrubam a média artificialmente. `n_pedidos` conta pedidos porque a pergunta é outra.

    Transferência entre filiais (mesma raiz de CNPJ da empresa) fica fora — não é compra, mesma
    régua do orçamento e da aba Verbas.
    """
    forn_map = forn_map or {}
    raiz = _cnpj_raiz(cnpj_empresa)
    fil = {str(f) for f in filiais} if filiais else None
    peds, datas = {}, {}
    for r in cab or []:
        cod = int(_n(r.get("CODFORNEC")))
        if not cod:
            continue
        if raiz and _cnpj_raiz((forn_map.get(cod) or {}).get("CGC")) == raiz:
            continue
        if fil is not None and str(r.get("CODFILIAL") or "").strip() not in fil:
            continue
        dt = _parse_dt(r.get("DTEMISSAO"))
        if not dt:
            continue
        if ciclo_desde is None or dt >= ciclo_desde:
            datas.setdefault(cod, set()).add(dt)
        if ini <= dt <= fim:
            peds.setdefault(cod, set()).add(int(_n(r.get("NUMPED"))))

    out = {}
    for cod in set(peds) | set(datas):
        ds = sorted(datas.get(cod, ()))
        # média dos intervalos = (última − primeira) ÷ (nº de intervalos). Com 1 só data não há
        # intervalo: ciclo indefinido (None), não zero — zero mentiria "compra todo dia".
        ciclo = _round((ds[-1] - ds[0]).days / (len(ds) - 1), 1) if len(ds) >= 2 else None
        out[cod] = {
            "n_pedidos": len(peds.get(cod, ())),
            "ciclo_dias": ciclo,
            "n_ciclo": len(ds),
            "ultima_compra": ds[-1].isoformat() if ds else None,
        }
    return out


# conta de verba que NÃO é redução de custo do produto (é ação comercial). Fica separada para o
# refinamento pedido pelo diretor 07/2026 ("nem toda verba vai pro preço, algumas vão pra
# campanha") — hoje ENTRA no total, por decisão dele; virar opt-out é trocar o filtro abaixo.
CONTAS_VERBA_CAMPANHA = {200013}


def verba_por_fornecedor(verbas, aplic_rows, ini, fim, forn_map=None, cnpj_empresa=None):
    """{codfornec: {verba, verba_campanha}} — verba NEGOCIADA (PCVERBA.VALOR) por data de
    emissão dentro de [ini, fim].

    ⚠️ A janela é a MESMA do lucro (seletor "Venda" do topo), não os 12m fixos da aba Verbas.
    Somar lucro de 1 mês com verba de 12 meses daria um "lucro com verba" inflado e sem
    significado — é o erro mais fácil de cometer aqui.

    Usa NEGOCIADO (o que o fornecedor concedeu no período), não APLICADO: aplicado é quando a
    verba foi consumida, um evento de caixa/acerto que pode cair meses depois e descolaria da
    competência do lucro. Canceladas/estornos já saem no `_verbas_prep`.
    """
    forn_map = forn_map or {}
    raiz = _cnpj_raiz(cnpj_empresa)
    V, _, _, _ = _verbas_prep(verbas, aplic_rows)
    out = {}
    for v in V:
        cod = v["_forn"]
        if not cod:
            continue
        if raiz and _cnpj_raiz((forn_map.get(cod) or {}).get("CGC")) == raiz:
            continue
        emis = v["_emis"]
        if not emis or not (ini <= emis <= fim):
            continue
        o = out.setdefault(cod, {"verba": 0.0, "verba_campanha": 0.0})
        o["verba"] += v["_valor"]
        if int(_n(v.get("CODCONTA"))) in CONTAS_VERBA_CAMPANHA:
            o["verba_campanha"] += v["_valor"]
    return {c: {"verba": _round(o["verba"]), "verba_campanha": _round(o["verba_campanha"])}
            for c, o in out.items()}


def yoy_fornecedor(venda_map, venda_ant_map, fornec_de_produto):
    """{codfornec: {venda_yoy, venda_ant_yoy}} — venda líquida do fornecedor nas DUAS janelas,
    somada sobre TODOS os produtos vendidos, não só os que ainda estão em estoque hoje.

    ⚠️ É a correção do crescimento (bug achado pelo diretor 07/2026). O caminho antigo somava o
    `venda_ano_ant` dos produtos da tela, e a tela só tem o que está no snapshot de estoque ATUAL.
    O numerador saía completo (o que vende hoje está no catálogo hoje) e o denominador perdia todo
    item descontinuado nos últimos 12 meses — universos diferentes nos dois lados da divisão, com
    o crescimento inflado. Medido no BI real: 18 fornecedores erravam >10 p.p. e 6 trocavam de
    SINAL (o app dizia +27% num fornecedor que caíra 61%).

    Produto sem cadastro de revenda não tem fornecedor e fica fora dos dois lados (0,49% da venda
    do ano anterior, medido) — é o teto de precisão deste método.
    """
    out = {}
    for mapa, campo in ((venda_map, "venda_yoy"), (venda_ant_map, "venda_ant_yoy")):
        for cod, d in (mapa or {}).items():
            cf = (fornec_de_produto or {}).get(cod)
            if cf is None:
                continue
            o = out.setdefault(cf, {"venda_yoy": 0.0, "venda_ant_yoy": 0.0})
            o[campo] += _n(d.get("venda") if isinstance(d, dict) else d)
    return {c: {k: _round(v) for k, v in o.items()} for c, o in out.items()}


def _extra_fornecedor(g, ex):
    """Colunas que NÃO saem da posição de estoque: ciclo de compras + verba + lucro com verba.
    `ex` = linha do mapa `extra` (ciclo_compras ⊕ verba_por_fornecedor) daquele fornecedor.

    `lucro` (venda líq. − custo) já era calculado e só não era EXIBIDO — a coluna nova apenas o
    mostra. NÃO se recalcula como `venda × margem`: margem é `lucro ÷ venda` arredondada a 1
    casa, então o caminho de volta devolveria o mesmo número com erro de arredondamento e a aba
    passaria a divergir do Comercial (que bate centavo-a-centavo com o RCA).

    `lucro_verba` = lucro + verba negociada na MESMA janela. Sem verba no período o valor é o
    próprio lucro (não é None): fornecedor sem verba tem lucro com verba = lucro.
    """
    ex = ex or {}
    verba = _n(ex.get("verba"))
    lucro = g["lucro"]
    # crescimento pela régua completa (yoy_fornecedor) quando disponível; sem ela cai no
    # somatório dos produtos da tela — que é o caminho antigo, incompleto no ano anterior.
    yoy = {}
    if ex.get("venda_ant_yoy") is not None:
        v_at, v_an = _n(ex.get("venda_yoy")), _n(ex.get("venda_ant_yoy"))
        yoy = {
            "venda_ano_ant": _round(v_an) if v_an else None,
            "crescimento": _round((v_at - v_an) / v_an * 100, 1) if v_an > 0 else None,
            "yoy_completo": True,     # a tela avisa que a coluna ignora os filtros de recorte
        }
    return {
        **yoy,
        "n_pedidos": ex.get("n_pedidos") or 0,
        "ciclo_dias": ex.get("ciclo_dias"),
        "ultima_compra": ex.get("ultima_compra"),
        "verba": _round(verba),
        "verba_campanha": _round(_n(ex.get("verba_campanha"))),
        "lucro_verba": _round(lucro + verba),
        # margem com verba: mede o fornecedor como ele realmente remunera (é a dor do diretor)
        "margem_verba": _round((lucro + verba) / g["venda"] * 100, 1) if g["venda"] else None,
    }


# ───────────────────────── fornecedores ─────────────────────────
def curva_abc_fornecedores(produtos, params=None):
    """{codfornec: 'A'|'B'|'C'} por Pareto da venda, sobre o conjunto RECEBIDO.

    Existe para que a curva seja calculada no UNIVERSO e não no recorte da tela. Pareto sobre
    lista filtrada é matematicamente sem sentido: com um fornecedor só, o acumulado dele é 100%
    e ele cai em C — foi o que o diretor viu, a BOMBRIL virando C ao filtrar por ela e voltando
    a A com todos na tela. Mesma política que os PRODUTOS já seguem (a curva sai do conjunto
    inteiro no servidor; os filtros só recortam a lista)."""
    _a = params["abc_a"] if params else DEFAULTS["abc_a"]
    _b = params["abc_b"] if params else DEFAULTS["abc_b"]
    agg = {}
    for p in produtos:
        cf = p.get("codfornec")
        if cf is None:
            continue
        agg[cf] = agg.get(cf, 0.0) + (p.get("venda") or 0)
    linhas = [{"codfornec": k, "venda": v} for k, v in agg.items()]
    _aplicar_curva(linhas, "venda", "curva_abc", _a, _b)
    return {l["codfornec"]: l["curva_abc"] for l in linhas}


def fornecedores(produtos, params=None, extra=None, curva_map=None):
    total_valor = sum(p["valor"] or 0 for p in produtos) or 1
    total_giro = sum(p["giro_mes"] or 0 for p in produtos) or 1
    total_venda = sum(p["venda"] or 0 for p in produtos) or 1
    grupos = {}
    for p in produtos:
        cf = p["codfornec"]
        if cf is None:
            continue
        g = grupos.setdefault(cf, {
            "codfornec": cf, "fornecedor": p["fornecedor"] or f"FORN {cf}",
            "comprador": p.get("comprador"),
            "n_produtos": 0, "valor": 0.0, "giro": 0.0, "venda": 0.0, "lucro": 0.0,
            "disponivel": 0.0, "giro_dia": 0.0, "n_sem_giro": 0, "venda_ant": 0.0,
        })
        g["n_produtos"] += 1
        g["valor"] += (p["valor"] or 0)
        g["giro"] += (p["giro_mes"] or 0)
        g["venda"] += (p["venda"] or 0)
        g["venda_ant"] += (p.get("venda_ano_ant") or 0)
        g["lucro"] += (p["lucro"] or 0)
        g["disponivel"] += (p["qtdisp"] or 0)
        g["giro_dia"] += (p["giro_dia"] or 0)
        if (p["giro_dia"] or 0) <= 0 and (p["qtdisp"] or 0) > 0:
            g["n_sem_giro"] += 1

    lead = params["lead_time"] if params else DEFAULTS["lead_time"]
    saida = []
    for g in grupos.values():
        perc_giro = g["giro"] / total_giro * 100
        perc_venda = g["venda"] / total_venda * 100
        perc_est = g["valor"] / total_valor * 100
        # índice = participação na VENDA (R$) ÷ participação no ESTOQUE (R$) — "vende mais do que
        # pesa em estoque". Antes usava giro em UNIDADES, o que distorcia fornecedores de alto
        # valor/baixo volume (ex.: embalagem cara vendendo bem virava "estoque alto").
        indice = (perc_venda / perc_est) if perc_est > 0 else (999.0 if perc_venda > 0 else 0.0)
        # cobertura média do fornecedor (dias) — distingue eficiência real de desabastecimento
        cobertura = (g["disponivel"] / g["giro_dia"]) if g["giro_dia"] > 0 else None
        if g["giro"] <= 0 and g["venda"] <= 0:
            classif = "critico_sem_giro"
        elif cobertura is not None and cobertura < lead:
            classif = "ruptura"            # gira mas quase sem estoque (não é performance)
        elif indice >= 1.2:
            classif = "alta_performance"
        elif indice >= 0.8:
            classif = "equilibrado"
        else:
            classif = "estoque_alto"
        saida.append({
            **g,
            "valor": _round(g["valor"]), "giro": _round(g["giro"]),
            "venda": _round(g["venda"]), "lucro": _round(g["lucro"]),
            "margem": _round(g["lucro"] / g["venda"] * 100, 1) if g["venda"] else None,
            "cobertura": _round(cobertura, 1) if cobertura is not None else None,
            "venda_ano_ant": _round(g["venda_ant"]) if g["venda_ant"] else None,
            "crescimento": _round((g["venda"] - g["venda_ant"]) / g["venda_ant"] * 100, 1) if g["venda_ant"] > 0 else None,
            "perc_giro": _round(perc_giro, 2), "perc_venda": _round(perc_venda, 2),
            "perc_estoque": _round(perc_est, 2),
            "indice": _round(indice, 2), "classificacao": classif,
            **_extra_fornecedor(g, (extra or {}).get(g["codfornec"])),
        })
    # Curva ABC do FORNECEDOR por venda (Pareto do faturamento) — mesma leitura dos produtos.
    # `curva_map` vem do UNIVERSO (todos os fornecedores), não do recorte: Pareto sobre lista
    # filtrada dá resultado sem sentido — com um fornecedor só, o acumulado é 100% e ele vira C.
    # Sem o mapa, cai no cálculo local (é o caso de quem chama sem filtro nenhum).
    if curva_map:
        for r in saida:
            r["curva_abc"] = curva_map.get(r["codfornec"], "C")
    else:
        _a = params["abc_a"] if params else DEFAULTS["abc_a"]
        _b = params["abc_b"] if params else DEFAULTS["abc_b"]
        _aplicar_curva(saida, "venda", "curva_abc", _a, _b)
    saida.sort(key=lambda x: x["valor"], reverse=True)
    return saida


# ───────────────────────── compras × vendas por comprador ─────────────────────────
def por_comprador(produtos):
    """Agrega compras (estoque/custo) × vendas (faturamento) por comprador."""
    grupos = {}
    for p in produtos:
        cc = p.get("codcomprador")
        chave = cc if cc is not None else 0
        g = grupos.setdefault(chave, {
            "codcomprador": cc, "comprador": p.get("comprador") or "Sem comprador",
            "n_produtos": 0, "estoque": 0.0, "venda": 0.0, "lucro": 0.0,
            "n_ruptura": 0, "valor_parado": 0.0, "sugestao_valor": 0.0, "sugestao_valor_nf": 0.0,
        })
        g["n_produtos"] += 1
        g["estoque"] += (p["valor"] or 0)
        g["venda"] += (p["venda"] or 0)
        g["lucro"] += (p["lucro"] or 0)
        # ruptura = critério OFICIAL (estoque <= 0 E giro > 0); cobertura baixa é atenção, não ruptura
        if (p.get("qtdisp") or 0) <= 0 and (p.get("giro_dia") or 0) > 0:
            g["n_ruptura"] += 1
        # fonte única `eh_parado` — NÃO testar a verdade do campo: `status_parado` deixou de ser
        # booleano quando ganhou o `novo`, e o truthy somava mercadoria recém-chegada como dead
        # stock. Este valor sai no relatório "Compradores" (export e email), então a divergência
        # ia parar na mão do cliente enquanto o Cockpit dizia outro número.
        if eh_parado(p):
            g["valor_parado"] += (p["valor"] or 0)
        # fonte única (caixa fechada), nas duas réguas — ver _valor_sugerido_compra
        g["sugestao_valor"] += _valor_sugerido_compra(p)
        g["sugestao_valor_nf"] += _valor_sugerido_compra(p, "valor_sugerido_nf")
    saida = []
    for g in grupos.values():
        saida.append({
            **g,
            "estoque": _round(g["estoque"]), "venda": _round(g["venda"]), "lucro": _round(g["lucro"]),
            "margem": _round(g["lucro"] / g["venda"] * 100, 1) if g["venda"] else None,
            "giro_estoque": _round(g["venda"] / g["estoque"], 2) if g["estoque"] else None,  # venda/estoque (turn)
            "valor_parado": _round(g["valor_parado"]), "sugestao_valor": _round(g["sugestao_valor"]),
            "sugestao_valor_nf": _round(g["sugestao_valor_nf"]),
        })
    saida.sort(key=lambda x: x["venda"], reverse=True)
    return saida


def _valor_sugerido_compra(p, campo="valor_sugerido_liq"):
    """Valor da sugestão de compra de um item, IGUAL à aba Comprar→Abastecimento:
    valor_sugerido_liq (caixa fechada × custo) dos itens a comprar (sugestao_cx>0, giro>0,
    não suspensos). Já embute Lead time + Cobertura alvo (via est_alvo). Fonte única p/ o
    número não divergir entre as duas telas.
    `campo="valor_sugerido_nf"` devolve a mesma coisa na régua da NF (com IPI/ST) — a que se
    compara com o saldo do Orçamento."""
    if (p.get("sugestao_cx") or 0) > 0 and (p.get("giro_dia") or 0) > 0 and not p.get("compra_suspensa"):
        return p.get(campo) or p.get("valor_sugerido_liq") or 0.0
    return 0.0


def _sem_providencia(p):
    """Item em ruptura para o qual NADA foi feito — a definição de "sem pedido".

    ⚠️ Mercadoria em PRÉ-ENTRADA (`qt_transicao > 0`) não conta: ela já está no armazém,
    aguardando liberação. Sem pedido em aberto? Sim — porque o Winthor já baixou o pedido ao
    receber. Mas providência tomada há, e é isso que a métrica mede.

    Achado pelo diretor em 08/2026: a aba Ruptura dizia "4 itens sem pedido" na curva A e a tela
    Estoque zerado, filtrada em "Ruptura s/ pedido", mostrava 3 — porque `status_exec`
    (`construir_produtos`) já tratava a pré-entrada como estado PRÓPRIO e exclusivo, e as
    agregações de ruptura nunca foram atualizadas quando isso entrou. Duas contagens do mesmo
    conceito com critérios diferentes; esta função passa a ser a fonte única do lado Python.

    O item CONTINUA contando em `n_ruptura`: não há estoque vendável, a venda perdida é real.
    O que ele deixa de ser é risco de omissão."""
    return (p.get("qtd_ja_pedida") or 0) <= 0 and (p.get("qt_transicao") or 0) <= 0


def ruptura_por_comprador(produtos):
    """Ruptura agregada por comprador (a mais rica). Ruptura = estoque ≤ 0 e giro > 0.
    n_sem_pedido = ruptura ainda sem providência — sem pedido em aberto E sem mercadoria em
    pré-entrada (ver `_sem_providencia`);
    venda_perdida = Σ giro_mes × custo (venda potencial/mês não atendida);
    sugestao_compra_valor = Σ valor_sugerido_liq de TODOS os itens a comprar do comprador —
    exatamente o total da aba Comprar→Abastecimento (decisão do diretor 07/2026: o card e a
    coluna passam a mostrar a SUGESTÃO DE COMPRA completa, não só o custo dos itens zerados).
    Mantém `custo_reposicao` como alias para não quebrar exports/consumidores antigos."""
    grupos = {}
    for p in produtos:
        cc = p.get("codcomprador")
        g = grupos.setdefault(cc if cc is not None else 0, {
            "codcomprador": cc, "comprador": p.get("comprador") or "Sem comprador",
            "n_produtos": 0, "n_ruptura": 0, "n_sem_pedido": 0,
            "venda_perdida": 0.0, "sugestao_compra_valor": 0.0, "sugestao_compra_nf": 0.0,
        })
        g["n_produtos"] += 1
        # sugestão de compra (Abastecimento) — TODO item a comprar do comprador, não só os em ruptura
        g["sugestao_compra_valor"] += _valor_sugerido_compra(p)
        # mesma sugestão na régua da NF (c/ IPI e ST): é a que se compara com o saldo do Orçamento
        g["sugestao_compra_nf"] += _valor_sugerido_compra(p, "valor_sugerido_nf")
        if (p.get("qtdisp") or 0) <= 0 and (p.get("giro_dia") or 0) > 0:
            g["n_ruptura"] += 1
            if _sem_providencia(p):
                g["n_sem_pedido"] += 1
            g["venda_perdida"] += (p.get("venda_perdida") or 0)   # acumulada na ruptura, a preço de venda
    saida = []
    for g in grupos.values():
        sug = _round(g["sugestao_compra_valor"])
        saida.append({
            **g,
            "pct_ruptura": _round(g["n_ruptura"] / g["n_produtos"] * 100, 1) if g["n_produtos"] else 0,
            # % dos itens SEM pedido sobre o TOTAL de produtos do comprador (base da meta —
            # todo item do comprador conta, não só os em ruptura). Complementa o pct_ruptura.
            "pct_sem_pedido": _round(g["n_sem_pedido"] / g["n_produtos"] * 100, 1) if g["n_produtos"] else 0,
            "venda_perdida": _round(g["venda_perdida"]),
            "sugestao_compra_valor": sug,
            "sugestao_compra_nf": _round(g["sugestao_compra_nf"]),
            "custo_reposicao": sug,   # alias retrocompatível (export/consumidores antigos)
        })
    saida.sort(key=lambda x: x["n_ruptura"], reverse=True)
    return saida


# ───────────────────────── desempenho comercial por comprador (RCA) ─────────────────────────
def desempenho_comprador(receita_rows, devol_map, comp_map, venda_ant_map=None, custo_ant_map=None,
                         custo_dev_map=None):
    """Espelha a aba RECEITA COMPRADOR: venda líquida, lucro bruto, margem ponderada,
    positivação (clientes distintos), devolução e comparativo ano×ano (venda E lucro) por comprador.
    receita_rows: [{CODCOMPRADOR, venda, custo, qtd, clientes_pos, fornecedores}];
    devol_map: {codcomprador: valor_devolvido}; custo_dev_map: {cc: custo_da_devolução} (RCA);
    venda_ant_map/custo_ant_map: {cc: valor} do ano ant."""
    venda_ant_map = venda_ant_map or {}
    custo_ant_map = custo_ant_map or {}
    custo_dev_map = custo_dev_map or {}
    linhas = []
    for r in receita_rows:
        cc = int(_n(r.get("CODCOMPRADOR"))) if r.get("CODCOMPRADOR") not in (None, "") else None
        if cc is None:
            continue
        venda_bruta = _n(r.get("venda"))
        custo = _n(r.get("custo"))
        dev = _n(devol_map.get(cc))
        cdev = _n(custo_dev_map.get(cc))
        venda_liq = venda_bruta - dev
        # Alinhamento RCA: a devolução tira o valor de venda da receita E devolve o
        # custo da mercadoria devolvida ao lucro (senão o custo é contado em dobro).
        lucro = venda_liq - (custo - cdev)
        margem = (lucro / venda_liq) if venda_liq else None
        venda_ant = _n(venda_ant_map.get(cc))
        yoy = ((venda_bruta - venda_ant) / venda_ant) if venda_ant > 0 else None
        # lucro do ano anterior (bruto = venda_ant − custo_ant) p/ o ano×ano do lucro
        lucro_ant = venda_ant - _n(custo_ant_map.get(cc))
        yoy_lucro = ((lucro - lucro_ant) / abs(lucro_ant)) if lucro_ant > 0 else None
        linhas.append({
            "codcomprador": cc,
            "comprador": comp_map.get(cc) or f"COMPRADOR {cc}",
            "fornecedores": int(_n(r.get("fornecedores"))),
            "clientes_pos": int(_n(r.get("clientes_pos"))),
            "qtd": _round(_n(r.get("qtd"))),
            "venda_bruta": _round(venda_bruta),
            "devolucao": _round(dev),
            "venda_liquida": _round(venda_liq),
            "lucro_bruto": _round(lucro),
            "margem": _round(margem * 100, 1) if margem is not None else None,
            "venda_ano_ant": _round(venda_ant) if venda_ant else None,
            "yoy": _round(yoy * 100, 1) if yoy is not None else None,
            "lucro_ano_ant": _round(lucro_ant) if lucro_ant else None,
            "yoy_lucro": _round(yoy_lucro * 100, 1) if yoy_lucro is not None else None,
        })
    tot_v = sum(l["venda_liquida"] for l in linhas) or 0
    tot_l = sum(l["lucro_bruto"] for l in linhas) or 0
    for l in linhas:
        l["part_receita"] = _round(l["venda_liquida"] / tot_v * 100, 1) if tot_v else 0
        l["part_lucro"] = _round(l["lucro_bruto"] / tot_l * 100, 1) if tot_l else 0
        if l["lucro_bruto"] < 0:
            l["status_lucro"] = "negativo"
        elif (l["part_lucro"] or 0) >= 30:
            l["status_lucro"] = "alta"
        elif (l["part_lucro"] or 0) >= 8:
            l["status_lucro"] = "boa"
        else:
            l["status_lucro"] = "baixa"
    linhas.sort(key=lambda x: x["lucro_bruto"], reverse=True)
    for i, l in enumerate(linhas, 1):
        l["ranking"] = i
    resumo = {
        "venda_liquida": _round(tot_v), "lucro_bruto": _round(tot_l),
        "margem": _round(tot_l / tot_v * 100, 1) if tot_v else None,
        "clientes_pos": sum(l["clientes_pos"] for l in linhas),
        "devolucao": _round(sum(l["devolucao"] for l in linhas)),
        "n_compradores": len(linhas),
    }
    return {"resumo": resumo, "compradores": linhas}


# ───────────────────────── orçamento de compras (pedido real Winthor) ─────────────────────────
def _cnpj_raiz(v):
    """8 primeiros dígitos do CNPJ (a 'raiz' da empresa) — filiais compartilham a raiz."""
    d = "".join(ch for ch in str(v or "") if ch.isdigit())
    return d[:8] if len(d) >= 8 else ""


def mes_anterior(mes):
    """'2026-08' → '2026-07'. Aceita 'YYYY-MM'; devolve None se não for esse formato."""
    try:
        a, m = str(mes).split("-")
        a, m = int(a), int(m)
    except (ValueError, AttributeError):
        return None
    return f"{a - 1}-12" if m == 1 else f"{a}-{m - 1:02d}"


def orcamento_winthor(cab, venda_comp, comp_map, forn_map, mes, comprador="TODOS",
                      pct=0.65, hoje=None, meta_override=None, lead_padrao=10,
                      cnpj_empresa=None, venda_comp_ant=None, arrastar=False):
    """Orçamento de compras a partir do pedido de compra REAL (PCPEDIDO).
    cab: linhas do cabeçalho; venda_comp: {nome_comprador: venda_liq_30d} (p/ a meta);
    comp_map: {matricula:nome}; forn_map: {codfornec: row}; mes: 'YYYY-MM'.
    meta = pct × venda_liq (override manual opcional); realizado = Σ VLTOTAL dos pedidos do mês;
    aberto = pedidos do mês ainda não recebidos (sem DTENTRADAESTOQUE).

    **Mês anterior** (pergunta do diretor em 08/2026: *"se ele estourou o orçamento do mês
    passado, não deveria arrastar o valor para diminuir do mês atual?"*). O mês fechado é sempre
    APURADO e devolvido no resumo (`meta_ant`/`comprado_ant`/`saldo_ant`) — o número aparece na
    tela mesmo com o arraste desligado, porque o problema imediato era ele ser invisível.

    `arrastar=True` desconta o ESTOURO da meta do mês corrente. Fica desligado por default de
    propósito: a meta é `pct × venda dos últimos 30 dias`, ou seja uma régua de FLUXO (repor o
    que girou), não um budget anual. Quem estourou porque a venda subiu seria punido duas vezes.
    Quem estourou por antecipação (lote/oportunidade) tem a mercadoria no estoque e aí o desconto
    é exatamente o controle de capital que se quer — por isso é uma escolha, não um default.

    Só o estouro viaja; SOBRA não vira crédito. Creditar sobra deixaria acumular um mês fraco
    inteiro para estourar o seguinte, que é o oposto do que a régua de fluxo mede.

    `venda_comp_ant`: {comprador: venda_liq_30d} medida no FIM do mês anterior — é a base que
    valia naquele momento. Usar a venda de hoje para reconstruir a meta de ontem daria um estouro
    contra uma meta que nunca existiu. Sem ele, o bloco do mês anterior sai zerado (`meta_ant`
    None) em vez de sair errado.

    `cnpj_empresa`: CNPJ da própria empresa. Pedido cujo FORNECEDOR tem a **mesma raiz de CNPJ**
    é **transferência entre filiais**, não compra (o CD abastecendo as lojas) — fica FORA do
    orçamento (decisão do diretor 07/2026: "não deve contabilizar como compra, pois de fato é
    transferência"). Compara pela raiz (8 dígitos) p/ pegar qualquer filial da mesma empresa,
    e não pelo CODFORNEC, que muda. Os excluídos voltam em `transferencias` — sem isso o valor
    do card cai sem explicação e vira a próxima desconfiança."""
    hoje = hoje or date.today()
    todos = (not comprador or comprador == "TODOS")
    raiz_empresa = _cnpj_raiz(cnpj_empresa)
    m_ant = mes_anterior(mes)
    pedidos = []
    realizado = aberto = 0.0
    realizado_ant = 0.0
    real_ant_c = {}                 # {comprador: comprado no mês anterior}
    tr_n = 0
    tr_valor = 0.0
    for r in cab:
        nome = comp_map.get(int(_n(r.get("CODCOMPRADOR"))))
        if not todos and nome != comprador:
            continue
        dtem = _parse_dt(r.get("DTEMISSAO"))
        forn = forn_map.get(int(_n(r.get("CODFORNEC"))))
        # transferência entre filiais: fornecedor é a própria empresa → não é compra
        if raiz_empresa and _cnpj_raiz((forn or {}).get("CGC")) == raiz_empresa:
            if bool(dtem) and dtem.strftime("%Y-%m") == mes:
                tr_n += 1
                tr_valor += _n(r.get("VLTOTAL"))
            continue
        vlt = _n(r.get("VLTOTAL"))
        vle = _n(r.get("VLENTREGUE"))            # valor já entregue (DTENTRADAESTOQUE é vazio aqui)
        aberto_val = max(0.0, vlt - vle)
        # recebido se o que falta entregar é desprezível (tolera resíduo de centavos)
        recebido = vlt > 0 and aberto_val <= max(1.0, vlt * 0.005)
        if recebido:
            aberto_val = 0.0
        mes_do_pedido = dtem.strftime("%Y-%m") if dtem else None
        no_mes = mes_do_pedido == mes
        if no_mes:
            realizado += vlt                     # comprado válido = tudo que foi pedido no mês
            aberto += aberto_val                 # comprometido aberto = ainda não entregue
        elif m_ant and mes_do_pedido == m_ant:
            # mês fechado: mesma régua do corrente (VLTOTAL, transferência já excluída acima)
            realizado_ant += vlt
            real_ant_c[nome or "Sem comprador"] = real_ant_c.get(nome or "Sem comprador", 0.0) + vlt
        # previsão de entrega (HÍBRIDO): usa a DTPREVENT do Winthor quando é previsão REAL
        # (posterior à emissão); senão = data do pedido + lead time do fornecedor (PRAZOENTREGA,
        # ou padrão). Evita marcar como atrasado pedido em que o Winthor só repetiu a emissão.
        dtprev_raw = _parse_dt(r.get("DTPREVENT"))
        lead = _n((forn or {}).get("PRAZOENTREGA"))
        lead = int(lead) if lead > 0 else int(lead_padrao)
        if dtprev_raw and dtem and dtprev_raw > dtem:
            dtprev = dtprev_raw
        elif dtem:
            dtprev = dtem + timedelta(days=lead)
        else:
            dtprev = dtprev_raw
        dias_prev = (dtprev - hoje).days if (dtprev and not recebido) else None
        if recebido:
            status_prazo = "recebido"
        elif dias_prev is None:
            status_prazo = "sem_prev"
        elif dias_prev < 0:
            status_prazo = "atrasado"
        elif dias_prev <= 7:
            status_prazo = "chega_7"
        else:
            status_prazo = "no_prazo"
        pedidos.append({
            "numped": int(_n(r.get("NUMPED"))),
            "data_pedido": dtem.isoformat() if dtem else None,
            "mes": dtem.strftime("%Y-%m") if dtem else None,
            "codfornec": int(_n(r.get("CODFORNEC"))),
            "fornecedor": (forn or {}).get("FORNECEDOR") if forn else None,
            "comprador": nome,
            "valor": _round(vlt),
            "valor_aberto": _round(aberto_val),
            "dt_previsao": dtprev.isoformat() if dtprev else None,
            "dias_para_chegar": dias_prev,
            "status_prazo": status_prazo,
            "recebido": recebido,
        })
    if meta_override is not None and _n(meta_override) > 0:
        meta_base = _n(meta_override)
    elif todos:
        meta_base = sum(_n(v) for v in venda_comp.values()) * pct
    else:
        meta_base = _n(venda_comp.get(comprador)) * pct

    # ── mês anterior: apurado SEMPRE, descontado só se `arrastar` ──
    def _meta_ant(vc):
        if vc is None:
            return None
        return (sum(_n(v) for v in vc.values()) if todos else _n(vc.get(comprador))) * pct

    meta_ant = _meta_ant(venda_comp_ant)
    saldo_ant = (meta_ant - realizado_ant) if meta_ant is not None else None
    # estouro é sempre ≤ 0 (sobra não vira crédito — ver docstring)
    estouro_ant = min(0.0, saldo_ant) if saldo_ant is not None else 0.0
    # piso em zero: meta negativa faria o % consumido explodir e o card mentir de outro jeito.
    # Se o estouro passar da meta do mês, o excedente NÃO se acumula para o mês seguinte —
    # arrastar em cascata transformaria a régua de fluxo num budget anual pela porta dos fundos.
    meta = max(0.0, meta_base + estouro_ant) if arrastar else meta_base
    saldo = meta - realizado
    # quebra por comprador (tabela do Orçamento): meta = venda_liq×pct de cada comprador;
    # comprado/aberto = pedidos do mês. Inclui compradores com venda mas sem pedido.
    # Só na visão "Empresa toda" (TODOS) — com filtro por comprador a quebra não faz sentido.
    agg_c = {}
    for p in (pedidos if todos else []):
        if p["mes"] != mes:
            continue
        nome_c = p["comprador"] or "Sem comprador"
        a = agg_c.setdefault(nome_c, {"comprador": nome_c, "meta": 0.0, "comprado": 0.0, "aberto": 0.0})
        a["comprado"] += _n(p["valor"])
        a["aberto"] += _n(p["valor_aberto"])
    for nome_c, v in ((venda_comp or {}).items() if todos else []):
        a = agg_c.setdefault(nome_c, {"comprador": nome_c, "meta": 0.0, "comprado": 0.0, "aberto": 0.0})
        a["meta"] = _n(v) * pct
    # o comprador que estourou o mês passado e não comprou nada neste ainda precisa aparecer,
    # senão o arraste sumiria justamente com quem ele afeta
    for nome_c in ((real_ant_c if todos else {})):
        agg_c.setdefault(nome_c, {"comprador": nome_c, "meta": 0.0, "comprado": 0.0, "aberto": 0.0})
    por_comprador = []
    for a in agg_c.values():
        m_a = (_n((venda_comp_ant or {}).get(a["comprador"])) * pct
               if venda_comp_ant is not None else None)
        c_a = _n(real_ant_c.get(a["comprador"]))
        s_a = (m_a - c_a) if m_a is not None else None
        a["meta_base"] = _round(a["meta"])
        a["meta_ant"] = _round(m_a) if m_a is not None else None
        a["comprado_ant"] = _round(c_a)
        a["saldo_ant"] = _round(s_a) if s_a is not None else None
        if arrastar and s_a is not None:
            a["meta"] = max(0.0, a["meta"] + min(0.0, s_a))
        a["saldo"] = _round(a["meta"] - a["comprado"])
        a["pct_consumido"] = _round(a["comprado"] / a["meta"], 4) if a["meta"] > 0 else None
        a["meta"] = _round(a["meta"]); a["comprado"] = _round(a["comprado"]); a["aberto"] = _round(a["aberto"])
        por_comprador.append(a)
    por_comprador.sort(key=lambda x: (x["meta"] or 0), reverse=True)
    abertos = [p for p in pedidos if not p["recebido"]]
    abertos.sort(key=lambda p: (p["dias_para_chegar"] if p["dias_para_chegar"] is not None else 9999))
    pedidos.sort(key=lambda p: (p["data_pedido"] or ""), reverse=True)
    resumo = {
        "mes": mes, "comprador": comprador, "pct": pct,
        "meta": _round(meta), "comprado": _round(realizado), "aberto": _round(aberto),
        "saldo": _round(saldo),
        "pct_consumido": _round(realizado / meta, 4) if meta > 0 else None,
        "n_pedidos": sum(1 for p in pedidos if p["mes"] == mes),
        "n_abertos": len(abertos),
        "n_atrasados": sum(1 for p in abertos if p["status_prazo"] == "atrasado"),
        "n_chega7": sum(1 for p in abertos if p["status_prazo"] == "chega_7"),
        "valor_aberto": _round(sum(p["valor_aberto"] for p in abertos)),
        "meta_auto": meta_override is None,
        # transferências entre filiais do mês, excluídas do orçamento (front avisa na tela)
        "transf_n": tr_n, "transf_valor": _round(tr_valor),
        # ── mês fechado: informado sempre; só entra na meta se `arrastar` (ver docstring) ──
        "mes_ant": m_ant,
        "meta_ant": _round(meta_ant) if meta_ant is not None else None,
        "comprado_ant": _round(realizado_ant),
        "saldo_ant": _round(saldo_ant) if saldo_ant is not None else None,
        "arrastar": bool(arrastar),
        "meta_base": _round(meta_base),
        "arrasto_aplicado": _round(estouro_ant) if arrastar else 0.0,
    }
    return {"resumo": resumo, "pedidos": pedidos, "abertos": abertos, "por_comprador": por_comprador}


# ───────────────────────── lead time por fornecedor (pedido → entrada) ─────────────────────────
# Faixas do histograma da aba. "0–1 dia" = pedido DIGITADO NA HORA da entrega (o pedido real
# nasceu fora do ERP — telefone/WhatsApp — e foi lançado junto com a NF). Decisão do diretor
# 07/2026: NÃO esconder esses pedidos; a aba mostra os DOIS lead times lado a lado e o
# % na hora vira o medidor do "serviço errado" caindo ao longo do tempo.
FAIXAS_LEADTIME = [("0-1", 0, 1), ("2-3", 2, 3), ("4-7", 4, 7),
                   ("8-15", 8, 15), ("16-30", 16, 30), ("31+", 31, 10**9)]
_LEAD_REAL_MIN = 2      # lead >= 2 dias = pedido digitado ANTES da entrega (lead "real")
_LEAD_MIN_PED = 5       # mínimo de pedidos reais p/ a mediana ser confiável


def leadtime_fornecedores(cab, entrada_rows, forn_map, comp_map, hoje=None,
                          min_ped=_LEAD_MIN_PED, cnpj_empresa=None, comprador=None):
    """Lead time por fornecedor: 1º recebimento (PEDIDO_ENTRADA) − emissão (PCPEDIDO).

    cab: linhas do q_pedido_cab (12m); entrada_rows: linhas do q_pedido_entrada
    (ponte NUMPED→DTENTRADA); forn_map: {codfornec: row PCFORNEC}; comp_map: {matricula: nome}.

    `comprador` (matrícula) recorta a base ANTES de agregar. Precisa ser aqui e não no cliente:
    `mediana_real` é mediana de PEDIDOS, e filtrar as linhas prontas daria mediana de medianas —
    número diferente, silenciosamente. Fornecedor sem CODCOMPRADOR no cadastro fica fora do
    recorte (não tem a quem pertencer), como já acontece nas demais telas por comprador.

    Dois lead times por fornecedor (validado com o diretor 07/2026):
    - lead_todos = MÉDIA de TODOS os pedidos recebidos (inclui digitado na hora). Média, não
      mediana: com >50% de pedidos "na hora" a mediana colapsa pra 0 e vira número binário;
      a média pondera cada pedido e a distância até o lead_real mede a distorção do processo.
    - lead_real  = MEDIANA só dos pedidos com lead >= 2d (>= min_ped pedidos, senão None
      = "sem lead confiável" e o PRAZOENTREGA manual segue valendo como fallback). Mediana
      aqui de propósito: é o número de planejamento, imune às caudas de 100+ dias.
    Transferência entre filiais (raiz de CNPJ da empresa) fica fora — não é fornecedor.
    Lead negativo (NF antes do pedido) é descartado das medianas e contado no resumo."""
    hoje = hoje or date.today()
    raiz_empresa = _cnpj_raiz(cnpj_empresa)
    comprador = int(_n(comprador)) or None
    entrada = {}
    for r in entrada_rows or []:
        numped = int(_n(r.get("NUMPED")))
        dt = _parse_dt(r.get("DTENTRADA"))
        if numped and dt:
            entrada[numped] = dt

    por_forn = {}                     # {codfornec: [lead, ...]}
    n_analisados = n_transfer = n_sem_entrada = n_negativos = 0
    faixas = {nome: 0 for nome, *_ in FAIXAS_LEADTIME}
    for r in cab:
        cod = int(_n(r.get("CODFORNEC")))
        forn = forn_map.get(cod)
        if raiz_empresa and _cnpj_raiz((forn or {}).get("CGC")) == raiz_empresa:
            n_transfer += 1
            continue
        # recorte por comprador: sai da base, não do resultado (ver docstring)
        if comprador and int(_n((forn or {}).get("CODCOMPRADOR"))) != comprador:
            continue
        emis = _parse_dt(r.get("DTEMISSAO"))
        ent = entrada.get(int(_n(r.get("NUMPED"))))
        if not emis or not ent:
            n_sem_entrada += 1        # aberto (ainda sem NF) ou fora da ponte
            continue
        lead = (ent - emis).days
        if lead < 0:
            n_negativos += 1
            continue
        n_analisados += 1
        por_forn.setdefault(cod, []).append(lead)
        for nome, lo, hi in FAIXAS_LEADTIME:
            if lo <= lead <= hi:
                faixas[nome] += 1
                break

    linhas = []
    for cod, leads in por_forn.items():
        forn = forn_map.get(cod) or {}
        reais = [l for l in leads if l >= _LEAD_REAL_MIN]
        confiavel = len(reais) >= int(min_ped)
        lead_real = _round(statistics.median(reais), 1) if confiavel else None
        prazo = _n(forn.get("PRAZOENTREGA"))
        prazo = int(prazo) if prazo > 0 else None
        codcomp = int(_n(forn.get("CODCOMPRADOR"))) or None
        na_hora = sum(1 for l in leads if l < _LEAD_REAL_MIN)
        linhas.append({
            "codfornec": cod,
            "fornecedor": forn.get("FORNECEDOR") or f"FORN {cod}",
            "codcomprador": codcomp,
            "comprador": comp_map.get(codcomp) if codcomp else None,
            "n": len(leads),
            "na_hora": na_hora,
            "pct_na_hora": _round(100.0 * na_hora / len(leads), 1),
            "lead_todos": _round(statistics.mean(leads), 1),
            "lead_real": lead_real,
            "n_reais": len(reais),
            "confiavel": confiavel,
            "prazo_manual": prazo,
            # Δ = manual − real: positivo grande = cadastro inflado (estoque de segurança
            # desnecessário / capital parado); negativo = prazo otimista (risco de ruptura).
            "delta": _round(prazo - lead_real, 1) if (prazo is not None and lead_real is not None) else None,
        })
    linhas.sort(key=lambda x: -x["n"])

    todos_leads = [l for ls in por_forn.values() for l in ls]
    reais_glob = [l for l in todos_leads if l >= _LEAD_REAL_MIN]
    resumo = {
        "n_pedidos": n_analisados,
        "n_sem_entrada": n_sem_entrada,
        "n_transfer": n_transfer,
        "n_negativos": n_negativos,
        "pct_na_hora": _round(100.0 * sum(1 for l in todos_leads if l < _LEAD_REAL_MIN)
                              / len(todos_leads), 1) if todos_leads else None,
        "media_todos": _round(statistics.mean(todos_leads), 1) if todos_leads else None,
        "mediana_real": _round(statistics.median(reais_glob), 1) if reais_glob else None,
        "n_fornec": len(linhas),
        "n_confiavel": sum(1 for l in linhas if l["confiavel"]),
        # cadastro defasado = |Δ| >= 3 dias entre o prazo manual e o lead real
        "n_defasados": sum(1 for l in linhas if l["delta"] is not None and abs(l["delta"]) >= 3),
    }
    return {"resumo": resumo,
            "faixas": [{"faixa": nome, "qtd": faixas[nome]} for nome, *_ in FAIXAS_LEADTIME],
            "fornecedores": linhas}


def _trimestre(d):
    return f"{d.year}-T{(d.month - 1) // 3 + 1}"


def leadtime_detalhe(cab, entrada_rows, codfornec, forn_map, comp_map, hoje=None,
                     min_ped=_LEAD_MIN_PED):
    """Drill da aba Lead time: TUDO que compõe o número de UM fornecedor (auditoria).

    Devolve: stats (o resumo da linha + p90/min/max/valores), pedidos (um a um, com as
    duas datas, lead, valor e se contou na mediana — inclui os ABERTOS e os negativos,
    nada escondido), trimestres (lead real + % na hora ao longo do tempo — o medidor do
    processo melhorando), faixas (histograma local) e promessa (DTPREVENT × entrada real,
    mesma regra híbrida do Orçamento: só conta previsão REAL, posterior à emissão —
    quando o Winthor repete emissão+1 é preenchimento automático, não promessa)."""
    hoje = hoje or date.today()
    codfornec = int(codfornec)
    forn = forn_map.get(codfornec) or {}
    entrada = {}
    for r in entrada_rows or []:
        numped = int(_n(r.get("NUMPED")))
        dt = _parse_dt(r.get("DTENTRADA"))
        if numped and dt:
            entrada[numped] = dt

    pedidos = []
    leads = []                      # só os válidos (>= 0), p/ stats
    tri_agg = {}                    # {tri: [leads]}
    faixas = {nome: 0 for nome, *_ in FAIXAS_LEADTIME}
    prom_aval = prom_prazo = prom_auto = 0
    atrasos_prom = []
    valor_12m = valor_aberto = 0.0
    for r in cab:
        if int(_n(r.get("CODFORNEC"))) != codfornec:
            continue
        emis = _parse_dt(r.get("DTEMISSAO"))
        if not emis:
            continue
        numped = int(_n(r.get("NUMPED")))
        ent = entrada.get(numped)
        vlt = _n(r.get("VLTOTAL"))
        vle = _n(r.get("VLENTREGUE"))
        valor_12m += vlt
        dtprev = _parse_dt(r.get("DTPREVENT"))
        # promessa de verdade = mais de 1 dia após a emissão. Mais rígido que o híbrido do
        # Orçamento (> emissão) de propósito: nesta base o Winthor preenche DTPREVENT
        # automaticamente com emissão+1 (validado 07/2026), o que não é promessa nenhuma.
        prev_real = bool(dtprev and (dtprev - emis).days > 1)
        p = {"numped": numped,
             "codfilial": str(r.get("CODFILIAL") or "").strip() or None,
             "emissao": emis.isoformat(),
             "entrada": ent.isoformat() if ent else None,
             "lead": None, "valor": _round(vlt),
             "dtprevent": dtprev.isoformat() if prev_real else None,
             "atraso_promessa": None}
        if not ent:
            aberto_val = max(0.0, vlt - vle)
            valor_aberto += aberto_val
            p["tipo"] = "aberto"
            p["dias_aberto"] = (hoje - emis).days
            p["atrasado"] = bool(prev_real and hoje > dtprev)
        else:
            lead = (ent - emis).days
            p["lead"] = lead
            if lead < 0:
                p["tipo"] = "negativo"               # NF antes do pedido — dado sujo, fora da mediana
            else:
                p["tipo"] = "real" if lead >= _LEAD_REAL_MIN else "na_hora"
                leads.append(lead)
                tri_agg.setdefault(_trimestre(emis), []).append(lead)
                for nome, lo, hi in FAIXAS_LEADTIME:
                    if lo <= lead <= hi:
                        faixas[nome] += 1
                        break
                if prev_real:
                    prom_aval += 1
                    if ent <= dtprev:
                        prom_prazo += 1
                    else:
                        atrasos_prom.append((ent - dtprev).days)
                        p["atraso_promessa"] = (ent - dtprev).days
                elif dtprev:
                    prom_auto += 1
        pedidos.append(p)
    pedidos.sort(key=lambda p: p["emissao"], reverse=True)

    reais = sorted(l for l in leads if l >= _LEAD_REAL_MIN)
    confiavel = len(reais) >= int(min_ped)
    lead_real = _round(statistics.median(reais), 1) if confiavel else None
    prazo = _n(forn.get("PRAZOENTREGA"))
    prazo = int(prazo) if prazo > 0 else None
    codcomp = int(_n(forn.get("CODCOMPRADOR"))) or None
    na_hora = sum(1 for l in leads if l < _LEAD_REAL_MIN)
    stats = {
        "codfornec": codfornec,
        "fornecedor": forn.get("FORNECEDOR") or f"FORN {codfornec}",
        "comprador": comp_map.get(codcomp) if codcomp else None,
        "n": len(leads), "na_hora": na_hora,
        "pct_na_hora": _round(100.0 * na_hora / len(leads), 1) if leads else None,
        "lead_todos": _round(statistics.mean(leads), 1) if leads else None,   # média (regra da aba)
        "lead_real": lead_real, "n_reais": len(reais), "confiavel": confiavel,
        "prazo_manual": prazo,
        "delta": _round(prazo - lead_real, 1) if (prazo is not None and lead_real is not None) else None,
        "lead_min": reais[0] if reais else None,
        "lead_max": reais[-1] if reais else None,
        "lead_p90": reais[int(len(reais) * 0.9)] if len(reais) > 3 else (reais[-1] if reais else None),
        "valor_12m": _round(valor_12m),
        "n_abertos": sum(1 for p in pedidos if p["tipo"] == "aberto"),
        "valor_aberto": _round(valor_aberto),
        "n_negativos": sum(1 for p in pedidos if p["tipo"] == "negativo"),
    }
    trimestres = []
    for tri in sorted(tri_agg):
        ls = tri_agg[tri]
        rs = [l for l in ls if l >= _LEAD_REAL_MIN]
        trimestres.append({
            "tri": tri, "n": len(ls),
            "pct_na_hora": _round(100.0 * sum(1 for l in ls if l < _LEAD_REAL_MIN) / len(ls), 1),
            "lead_real": _round(statistics.median(rs), 1) if rs else None,
        })
    promessa = {
        "n_avaliaveis": prom_aval,                   # entregues COM promessa real de data
        "n_auto": prom_auto,                         # DTPREVENT automática (emissão+1) — fora
        "pct_no_prazo": _round(100.0 * prom_prazo / prom_aval, 1) if prom_aval else None,
        "atraso_medio": _round(sum(atrasos_prom) / len(atrasos_prom), 1) if atrasos_prom else None,
    }
    return {"stats": stats, "pedidos": pedidos, "trimestres": trimestres,
            "faixas": [{"faixa": nome, "qtd": faixas[nome]} for nome, *_ in FAIXAS_LEADTIME],
            "promessa": promessa}


# ───────────────────────── verbas de fornecedor (rotina 1801) ─────────────────────────
# Nomes das contas do 1826 (o PCCONTA do dataset é escopado na 200042 e não cobre estas).
CONTAS_VERBA = {250009: "Rebaixa de custo", 250008: "Conta corrente",
                200013: "Premiações e campanhas", 200042: "Perda validade"}
_VERBA_COMPRA_MIN_ALERTA = 300000   # compra 12m acima disso sem verba nenhuma → alerta
_VERBA_IDADE_PARADA = 120           # saldo em aberto há mais que isso = "parado" (crítico)


def _verbas_prep(verbas, aplic_rows):
    """Normaliza: exclui canceladas (DTCANCEL) e estornos (DTESTORNO); calcula aplicado e
    saldo por verba. Devolve (válidas, n_cancel, aplicações_válidas, n_estornos)."""
    apl_por_verba = {}
    apl_validas = []
    n_estornos = 0
    for a in aplic_rows or []:
        if a.get("DTESTORNO"):
            n_estornos += 1
            continue
        nv = int(_n(a.get("NUMVERBA")))
        apl_por_verba[nv] = apl_por_verba.get(nv, 0.0) + _n(a.get("VLAPLIC"))
        apl_validas.append(a)
    out = []
    n_cancel = 0
    for v in verbas or []:
        if v.get("DTCANCEL"):
            n_cancel += 1
            continue
        nv = int(_n(v.get("NUMVERBA")))
        valor = _n(v.get("VALOR"))
        aplicado = apl_por_verba.get(nv, 0.0)
        out.append({**v, "_nv": nv, "_valor": valor, "_aplicado": aplicado,
                    "_saldo": valor - aplicado,
                    "_emis": _parse_dt(v.get("DTEMISSAO")),
                    "_venc": _parse_dt(v.get("DTVENC")),
                    "_forn": int(_n(v.get("CODFORNEC")))})
    return out, n_cancel, apl_validas, n_estornos


def verbas_fornecedores(verbas, aplic_rows, forn_map, comp_map, compras_map=None,
                        lead_map=None, hoje=None, cnpj_empresa=None,
                        compra_min_alerta=_VERBA_COMPRA_MIN_ALERTA, comprador=None,
                        fornec=None):
    """Visão Verbas: negociado × aplicado × saldo por fornecedor + consolidados.

    verbas/aplic_rows: linhas cruas de PCVERBA/PCAPLICVERBA (2024+); compras_map:
    {codfornec: compra_12m} (do cab de pedidos do lead time, transferências já fora);
    lead_map: {codfornec: lead_real} — fecha o TRIPÉ (compra × lead × verba).

    Regras (validadas contra o relatório 1826, BOMBRIL centavo a centavo no recorte 2024+):
    - cancelada (DTCANCEL) e estorno (DTESTORNO) ficam fora;
    - saldo a aplicar = VALOR − Σ aplicações (posição ATUAL, qualquer emissão — saldo é
      estoque, não fluxo; DTQUITACAO é campo morto nesta base, o status vem do saldo);
    - negociado/aplicado do placar = últimos 12 MESES (casa com a compra 12m do %V/C);
    - fornecedor entra no placar se tem verba 12m OU saldo em aberto (saldo antigo não some);
    - compra alta sem verba nenhuma → lista de alerta (o argumento de negociação).

    `comprador` (matrícula) e `fornec` (CODFORNEC) recortam a base ANTES de agregar, para que o
    resumo (cards), o gráfico mensal e o "por conta" falem do mesmo universo que a tabela —
    filtrar só as linhas prontas deixaria os totais no valor da empresa inteira. Fornecedor sem
    CODCOMPRADOR no cadastro fica fora do recorte por comprador.

    ⚠️ Os dois COMPÕEM (interseção): comprador X olhando o fornecedor Y vê Y só se Y for dele.
    O `fornec` nasceu em 08/2026 porque a tela filtrava fornecedor só no cliente: com a RAZZO
    selecionada, a tabela mostrava 1 fornecedor e o gráfico ao lado seguia somando a empresa
    inteira (a barra de julho dizia ~R$ 40k onde a verba da RAZZO era R$ 10.054,80) — o MESMO
    defeito que motivou o recorte por comprador vir para cá."""
    hoje = hoje or date.today()
    corte_12m = hoje - timedelta(days=365)
    raiz_empresa = _cnpj_raiz(cnpj_empresa)
    comprador = int(_n(comprador)) or None
    fornec = int(_n(fornec)) or None
    V, n_cancel, apl_validas, n_estornos = _verbas_prep(verbas, aplic_rows)
    V = [v for v in V if not (raiz_empresa
                              and _cnpj_raiz((forn_map.get(v["_forn"]) or {}).get("CGC")) == raiz_empresa)]
    # Escopo = interseção dos filtros ativos, aplicada UMA vez. `None` = sem recorte (universo
    # inteiro); conjunto vazio = recorte que não casa com nada, e aí a tela sai vazia de propósito
    # — diferente de "sem filtro", que é o que um `if escopo:` faria por engano.
    escopo = None
    if comprador:
        escopo = {cod for cod, f in (forn_map or {}).items()
                  if int(_n((f or {}).get("CODCOMPRADOR"))) == comprador}
    if fornec:
        escopo = {fornec} if escopo is None else (escopo & {fornec})
    if escopo is not None:
        V = [v for v in V if v["_forn"] in escopo]
        # as APLICAÇÕES também: elas alimentam o "Aplicado 12m" e o gráfico mensal, que ficariam
        # no valor da empresa inteira ao lado de um negociado já recortado — o pior dos mundos.
        # A ponte é NUMVERBA → CODFORNEC sobre as verbas CRUAS (inclui canceladas, como no global).
        nv_forn = {int(_n(v.get("NUMVERBA"))): int(_n(v.get("CODFORNEC"))) for v in (verbas or [])}
        apl_validas = [a for a in apl_validas
                       if nv_forn.get(int(_n(a.get("NUMVERBA")))) in escopo]
        compras_map = {cod: val for cod, val in (compras_map or {}).items() if cod in escopo}

    # por fornecedor
    agg = {}
    for v in V:
        a = agg.setdefault(v["_forn"], {"n": 0, "neg": 0.0, "apl": 0.0,
                                        "saldo": 0.0, "idade_max": None})
        if v["_emis"] and v["_emis"] >= corte_12m:
            a["n"] += 1
            a["neg"] += v["_valor"]
            a["apl"] += min(v["_aplicado"], v["_valor"]) if v["_valor"] else v["_aplicado"]
        if v["_saldo"] > 0.01:
            a["saldo"] += v["_saldo"]
            if v["_emis"]:
                idade = (hoje - v["_emis"]).days
                a["idade_max"] = max(a["idade_max"] or 0, idade)

    compras_map = compras_map or {}
    lead_map = lead_map or {}
    linhas = []
    for cod, a in agg.items():
        if a["n"] == 0 and a["saldo"] <= 0.01:
            continue
        f = forn_map.get(cod) or {}
        codcomp = int(_n(f.get("CODCOMPRADOR"))) or None
        compra = _n(compras_map.get(cod))
        linhas.append({
            "codfornec": cod,
            "fornecedor": f.get("FORNECEDOR") or f"FORN {cod}",
            "codcomprador": codcomp,
            "comprador": comp_map.get(codcomp) if codcomp else None,
            "n_verbas": a["n"],
            "negociado": _round(a["neg"]),
            "aplicado": _round(a["apl"]),
            "saldo": _round(a["saldo"]),
            "idade_saldo": a["idade_max"],
            "compra_12m": _round(compra),
            "pct_vc": _round(100.0 * a["neg"] / compra, 1) if (compra > 0 and a["neg"] > 0) else None,
            "lead_real": lead_map.get(cod),
        })
    linhas.sort(key=lambda x: -x["negociado"])

    # grandes compradores sem verba nenhuma (nem 12m, nem saldo)
    com_verba = set(agg)
    grandes = []
    for cod, compra in compras_map.items():
        if cod in com_verba or _n(compra) < compra_min_alerta:
            continue
        f = forn_map.get(cod) or {}
        codcomp = int(_n(f.get("CODCOMPRADOR"))) or None
        grandes.append({"codfornec": cod, "fornecedor": f.get("FORNECEDOR") or f"FORN {cod}",
                        "codcomprador": codcomp,
                        "comprador": comp_map.get(codcomp) if codcomp else None,
                        "compra_12m": _round(_n(compra))})
    grandes.sort(key=lambda x: -x["compra_12m"])

    # ── por conta e evolução mensal: MESMA janela dos cards (12m) ──
    # ⚠️ INVARIANTE DA PÁGINA: `negociado_12m` do card = Σ coluna Negociado da tabela
    # = Σ barras azuis do gráfico = Σ do "por conta". Um número, quatro lugares.
    # Até 08/2026 os dois últimos somavam a base inteira (2024+) e não fechavam com nada:
    # medido no BI, o "por conta" da empresa dava R$ 2.137.441 contra R$ 819.002 do card
    # (2,6×) e o gráfico dava R$ 915.676. O rodapé da aba já prometia "negociado/aplicado =
    # últimos 12 meses" — eram estes dois que não cumpriam.
    # O SALDO é exceção proposital e continua sendo POSIÇÃO (qualquer emissão): saldo é
    # estoque, não fluxo — verba velha em aberto não pode sumir por causa da janela.
    por_conta = {}
    for v in V:
        c = int(_n(v.get("CODCONTA")))
        pc = por_conta.setdefault(c, {"codconta": c, "conta": CONTAS_VERBA.get(c, f"Conta {c}"),
                                      "n": 0, "negociado": 0.0, "saldo": 0.0})
        if v["_emis"] and v["_emis"] >= corte_12m:
            pc["n"] += 1
            pc["negociado"] += v["_valor"]
        pc["saldo"] += v["_saldo"] if v["_saldo"] > 0.01 else 0.0
    contas = sorted(({**c, "negociado": _round(c["negociado"]), "saldo": _round(c["saldo"])}
                     for c in por_conta.values()
                     # conta só com verba antiga já aplicada não vira linha de zeros
                     if c["negociado"] > 0 or c["saldo"] > 0.01),
                    key=lambda x: -x["negociado"])

    # ⚠️ O eixo é de CALENDÁRIO, montado a partir do PERÍODO — não dos dados. A 1ª versão
    # listava os 14 meses QUE TIVERAM MOVIMENTO: mês sem verba não existia, e o Chart.js
    # desenhava as barras coladas como se fossem consecutivas. Na RAZZO isso virou 27 meses
    # de calendário em 14 barras (13 escondidos) — 96% dos fornecedores têm buraco, então
    # o defeito aparecia em quase todo filtro. Mês zerado é INFORMAÇÃO num gráfico de verba.
    neg_mes, apl_mes = {}, {}
    for v in V:
        if v["_emis"] and v["_emis"] >= corte_12m:
            m = v["_emis"].strftime("%Y-%m")
            neg_mes[m] = neg_mes.get(m, 0.0) + v["_valor"]
    for a in apl_validas:
        d = _parse_dt(a.get("DTAPLIC"))
        if d and d >= corte_12m:
            m = d.strftime("%Y-%m")
            apl_mes[m] = apl_mes.get(m, 0.0) + _n(a.get("VLAPLIC"))
    meses, _m = [], date(corte_12m.year, corte_12m.month, 1)
    while _m <= hoje:
        k = _m.strftime("%Y-%m")
        # ⚠️ AS DUAS PONTAS são parciais, e as duas precisam avisar:
        # · a 1ª porque a janela são 365 DIAS corridos e começa no meio do mês (medido:
        #   R$ 13.019,12 emitidos antes do dia do corte ficam de fora);
        # · a ÚLTIMA porque o mês corrente ainda está correndo — em 09/08 ela tinha 9 de 31
        #   dias e R$ 2.970 contra R$ 63.261 de julho, o que se lê como desabamento da
        #   negociação quando é só o mês pela metade.
        # Número que parece um fato sem ser comparável é o mesmo defeito das duas vezes.
        _ult = calendar.monthrange(_m.year, _m.month)[1]
        _ini = corte_12m.day if k == corte_12m.strftime("%Y-%m") else 1
        _fim = hoje.day if k == hoje.strftime("%Y-%m") else _ult
        _cob = max(0, _fim - _ini + 1)
        meses.append({
            "mes": k,
            "negociado": _round(neg_mes.get(k, 0.0)),
            "aplicado": _round(apl_mes.get(k, 0.0)),
            "dias_cobertos": _cob,
            "dias_mes": _ult,
            "parcial": _cob < _ult,
        })
        _m = date(_m.year + (_m.month == 12), (_m.month % 12) + 1, 1)

    abertas = [v for v in V if v["_saldo"] > 0.01]
    idades = [(hoje - v["_emis"]).days for v in abertas if v["_emis"]]
    neg_12m = sum(v["_valor"] for v in V if v["_emis"] and v["_emis"] >= corte_12m)
    apl_12m = sum(_n(a.get("VLAPLIC")) for a in apl_validas
                  if (_parse_dt(a.get("DTAPLIC")) or date.min) >= corte_12m)
    resumo = {
        "saldo_aberto": _round(sum(v["_saldo"] for v in abertas)),
        "n_abertas": len(abertas),
        "saldo_vencido": _round(sum(v["_saldo"] for v in abertas
                                    if v["_venc"] and v["_venc"] < hoje)),
        "idade_mediana": _round(statistics.median(idades), 0) if idades else None,
        "idade_max": max(idades) if idades else None,
        "negociado_12m": _round(neg_12m),
        "aplicado_12m": _round(apl_12m),
        "n_verbas_12m": sum(1 for v in V if v["_emis"] and v["_emis"] >= corte_12m),
        "n_cancel": n_cancel,
        "n_estornos": n_estornos,
        "n_fornec": len(linhas),
        "n_grandes_sem_verba": len(grandes),
        "compra_min_alerta": compra_min_alerta,
    }
    return {"resumo": resumo, "contas": contas, "meses": meses,
            "fornecedores": linhas, "grandes_sem_verba": grandes[:10]}


def verbas_detalhe(verbas, aplic_rows, codfornec, hoje=None):
    """Drill da aba Verbas: as verbas de UM fornecedor, uma a uma (auditoria) — número,
    campanha (REFERENCIA), conta, valor × aplicado × saldo, idade e aplicações."""
    hoje = hoje or date.today()
    codfornec = int(codfornec)
    V, _, apl_validas, _ = _verbas_prep(verbas, aplic_rows)
    apls = {}
    for a in apl_validas:
        nv = int(_n(a.get("NUMVERBA")))
        d = _parse_dt(a.get("DTAPLIC"))
        st = apls.setdefault(nv, {"n": 0, "ult": None})
        st["n"] += 1
        if d and (st["ult"] is None or d > st["ult"]):
            st["ult"] = d
    linhas = []
    tot_neg = tot_apl = tot_saldo = 0.0
    for v in V:
        if v["_forn"] != codfornec:
            continue
        saldo = v["_saldo"] if v["_saldo"] > 0.01 else 0.0
        tot_neg += v["_valor"]
        tot_apl += min(v["_aplicado"], v["_valor"]) if v["_valor"] else v["_aplicado"]
        tot_saldo += saldo
        ap = apls.get(v["_nv"], {"n": 0, "ult": None})
        c = int(_n(v.get("CODCONTA")))
        linhas.append({
            "numverba": v["_nv"],
            "emissao": v["_emis"].isoformat() if v["_emis"] else None,
            "venc": v["_venc"].isoformat() if v["_venc"] else None,
            "codconta": c, "conta": CONTAS_VERBA.get(c, f"Conta {c}"),
            "campanha": (v.get("REFERENCIA") or "").strip() or None,
            "formapgto": v.get("FORMAPGTO"),
            "valor": _round(v["_valor"]),
            "aplicado": _round(v["_aplicado"]),
            "saldo": _round(saldo),
            "idade_saldo": (hoje - v["_emis"]).days if (saldo > 0 and v["_emis"]) else None,
            "n_aplic": ap["n"],
            "ult_aplic": ap["ult"].isoformat() if ap["ult"] else None,
        })
    linhas.sort(key=lambda x: x["emissao"] or "", reverse=True)
    stats = {"codfornec": codfornec, "n_verbas": len(linhas),
             "negociado": _round(tot_neg), "aplicado": _round(tot_apl),
             "saldo": _round(tot_saldo),
             "n_abertas": sum(1 for l in linhas if l["saldo"] > 0)}
    return {"stats": stats, "verbas": linhas}


# ───────────────────────── resumos gerenciais (painel do diretor) ─────────────────────────
_FX_VALIDADE = [("0 a 15 dias", 0, 15, "URGENTE"), ("16 a 30 dias", 16, 30, "ALTO"),
                ("31 a 60 dias", 31, 60, "ATENCAO"), ("61 a 90 dias", 61, 90, "BAIXO"),
                ("Acima de 90 dias", 91, 10**9, "OK")]
_FX_COBERTURA = [("0 a 30 dias", 0, 30, "RISCO RUPTURA"), ("31 a 60 dias", 31, 60, "OK"),
                 ("61 a 90 dias", 61, 90, "ATENCAO"), ("91 a 120 dias", 91, 120, "URGENTE"),
                 ("Acima de 120 dias", 121, 10**9, "CRITICO")]


def resumo_validade(lotes, produtos_idx, hoje=None):
    """Bloco 'Itens a vencer por faixa de validade' (RELATORIO GERENCIAL do diretor).
    Consolida os lotes por (CODPROD, DTVAL) e classifica por dias até vencer.
    valor = qt consolidada × custo do produto. Devolve {faixas:[...], total:{...}}."""
    hoje = hoje or date.today()
    agg = {}
    for r in lotes:
        cod = int(_n(r.get("CODPROD")))
        dtval = _parse_dt(r.get("DTVAL"))
        if not dtval:
            continue
        agg[(cod, dtval)] = agg.get((cod, dtval), 0.0) + _n(r.get("qt"))
    faixas = []
    tot_itens = 0
    tot_valor = 0.0
    buckets = {nome: [0, 0.0] for nome, *_ in _FX_VALIDADE}
    for (cod, dtval), qt in agg.items():
        dias = (dtval - hoje).days
        custo = (produtos_idx.get(cod) or {}).get("custo_unit") or 0
        for nome, lo, hi, _status in _FX_VALIDADE:
            if lo <= dias <= hi:
                buckets[nome][0] += 1
                buckets[nome][1] += qt * custo
                break
    for nome, lo, hi, status in _FX_VALIDADE:
        n, v = buckets[nome]
        tot_itens += n
        tot_valor += v
        faixas.append({"faixa": nome, "itens": n, "valor": _round(v), "status": status})
    for f in faixas:
        f["perc"] = _round(f["itens"] / tot_itens, 4) if tot_itens else 0
    return {"faixas": faixas,
            "total": {"itens": tot_itens, "valor": _round(tot_valor)}}


def resumo_cobertura(produtos):
    """Bloco 'Cobertura de estoque por faixa de dias' (RELATORIO GERENCIAL do diretor).
    Cobertura no critério dele: giro<=0 → 9999; senão ceil(qtdisp/giro_dia); qtdisp<=0 → 0.
    valor = p['valor'] (já com piso zero). Devolve {faixas:[...], total:{...}}."""
    buckets = {nome: [0, 0.0] for nome, *_ in _FX_COBERTURA}
    tot_prod = 0
    tot_valor = 0.0
    for p in produtos:
        giro_dia = p.get("giro_dia") or 0
        qtdisp = p.get("qtdisp") or 0
        if giro_dia <= 0:
            cob = 9999
        elif qtdisp <= 0:
            cob = 0
        else:
            cob = math.ceil(qtdisp / giro_dia)
        for nome, lo, hi, _status in _FX_COBERTURA:
            if lo <= cob <= hi:
                buckets[nome][0] += 1
                buckets[nome][1] += (p.get("valor") or 0)
                break
    faixas = []
    for nome, lo, hi, status in _FX_COBERTURA:
        n, v = buckets[nome]
        tot_prod += n
        tot_valor += v
        faixas.append({"faixa": nome, "produtos": n, "valor": _round(v), "status": status})
    for f in faixas:
        f["perc"] = _round(f["produtos"] / tot_prod, 4) if tot_prod else 0
    return {"faixas": faixas,
            "total": {"produtos": tot_prod, "valor": _round(tot_valor)}}


def resumo_ruptura(produtos):
    """Bloco 'Ruptura de produtos' (RELATORIO GERENCIAL). Critério oficial do diretor:
    estoque ≤ 0 e giro mensal > 0. % sobre o universo construído (revenda com posição).
    venda_perdida = giro_mes × custo dos itens em ruptura (o "valor" da ruptura — o antigo
    "valor de estoque" era sempre ~0, pois item em ruptura tem estoque ≤ 0)."""
    total = len(produtos)
    rup = [p for p in produtos if (p.get("qtdisp") or 0) <= 0 and (p.get("giro_dia") or 0) > 0]
    return {
        "itens": len(rup),
        "total": total,
        "perc": _round(len(rup) / total, 4) if total else 0,
        "venda_perdida": _round(sum((p.get("venda_perdida") or 0) for p in rup)),
        "valor": _round(sum(p.get("valor") or 0 for p in rup)),  # mantido p/ compat (≈0)
        "criterio": "ESTOQUE <= 0 E GIRO MENSAL > 0",
    }


def regua_estoque_ideal(params):
    """(limiar_dias, meta_pct) do 'Estoque ideal' a partir dos params (⚙ Parâmetros).

    Os dois chegam pela querystring, então clampa aqui — é a única porta:
    • limiar 0/vazio/lixo → volta ao default 45. Zero faria TODO item virar "ideal" e o painel
      passaria a dizer 100% sempre (mentira silenciosa, o pior tipo).
    • meta viaja em % (0-100) porque é assim que o campo da tela é natural; o resumo recebe
      FRAÇÃO. Fora da faixa satura em 0/1 — meta 0 é válida (nunca alerta), 100 exige tudo.
    """
    lim = int(_n((params or {}).get("ideal_dias")) or DEFAULTS["ideal_dias"])
    meta = _n((params or {}).get("ideal_meta_pct"))
    return max(1, lim), min(1.0, max(0.0, meta / 100.0))


def resumo_estoque_ideal(produtos, limiar_dias=45, meta_pct=0.90):
    """Cobertura MÍNIMA de estoque (pedido do diretor) — % de SKUs por faixa de cobertura.
    `limiar_dias` = MÍNIMO de dias para o item contar como ideal (fronteira inclusiva):
    • Em risco  = giro > 0 e cobertura < `limiar_dias` (≤44d)
    • Ideal     = giro > 0 e cobertura ≥ `limiar_dias` (45d+)
    A fronteira é inclusiva de propósito (ajuste 07/2026): o item que pousa EXATAMENTE no limiar
    atingiu a meta — contá-lo como "em risco" punia justamente quem comprou certo (28 SKUs pousavam
    exatamente em 45d, ~2× os dias vizinhos). O default 45 nasceu de coincidir com o alvo de compra
    (`cobertura_total`), mas os dois são INDEPENDENTES: desde 07/2026 o limiar é o parâmetro
    `ideal_dias` (⚙ Parâmetros) e pode ser calibrado sem mexer na sugestão de compra.
    • Sem giro  = giro ≤ 0 (reportado à parte; NÃO entra no % ideal p/ não distorcer)
    O % ideal é medido só sobre os itens QUE GIRAM (base da 'cobertura mínima'); o gatilho de
    alerta dispara quando ideal% < `meta_pct` (90%). Cobertura na regra oficial da planilha.

    ⚠️ **A cobertura vem do `cobertura_dias` do produto, não de um recálculo.** O card agora é
    clicável (leva à lista dos itens em risco), então card e lista TÊM de dar o mesmo número —
    e recalcular aqui não dava. `construir_produtos` calcula `cobertura_dias` com o `qtdisp` e o
    `giro_dia` CRUS; o dict do produto guarda os dois já ARREDONDADOS, e recalcular a partir
    deles muda o `ceil`. Medido no BI real com limiar 25: o card dizia **789 SKUs** e a lista
    (que sempre leu `cobertura_dias`, como o export e a aba Cobertura) tinha **791** —
    cód. 44398, 104 un ÷ giro 4,3333… = 24d exatos, mas ÷ 4,333 arredondado = 24,0018 → 25d.
    No limiar 45 os dois coincidiam por sorte, e foi por isso que passou despercebido.
    O recálculo fica só como fallback, para quem chama a função com produtos sintéticos
    (os testes montam `{giro_dia, qtdisp, valor}` sem `cobertura_dias`)."""
    risco = ideal = semgiro = 0
    v_risco = v_ideal = v_semgiro = 0.0
    for p in produtos:
        giro_dia = p.get("giro_dia") or 0
        valor = p.get("valor") or 0
        if giro_dia <= 0:
            semgiro += 1; v_semgiro += valor
            continue
        cob = p.get("cobertura_dias")
        if cob is None:
            cob = cobertura_dias_oficial(p.get("qtdisp") or 0, giro_dia)
        if cob < limiar_dias:          # fronteira inclusiva: cobertura == limiar já é ideal
            risco += 1; v_risco += valor
        else:
            ideal += 1; v_ideal += valor
    com_giro = risco + ideal
    total = com_giro + semgiro
    pct_ideal = (ideal / com_giro) if com_giro else None
    return {
        "limiar": limiar_dias, "meta_pct": meta_pct,
        "em_risco": {"n": risco, "valor": _round(v_risco),
                     "pct": _round(risco / com_giro, 4) if com_giro else None},
        "ideal": {"n": ideal, "valor": _round(v_ideal),
                  "pct": _round(pct_ideal, 4) if pct_ideal is not None else None},
        "sem_giro": {"n": semgiro, "valor": _round(v_semgiro),
                     "pct": _round(semgiro / total, 4) if total else None},
        "com_giro": com_giro, "total": total,
        "alerta": bool(pct_ideal is not None and pct_ideal < meta_pct),
    }


# ───────────────────────── logística / cubagem (pedido real) ─────────────────────────
# `vol_unitario` foi absorvida por `medidas_unitarias` (08/2026): peso e volume saem da MESMA
# fonte e passam pela MESMA guarda. Duas funções para o mesmo conceito foi o que deixou o
# volume com fallback pro cadastro e o peso preso na PCEMBALAGEM vazia por meses.
# ── qualidade do CADASTRO logístico (base inteira) ──
# Categorias e a ordem em que a tela as mostra. O `fn` recebe (cad, medidas, fator).
QUAL_CADASTRO = [
    ("cadastro_impossivel", "Cadastro impossível",
     lambda cad, med, fat: not med["confiavel"]),
    ("sem_cubagem", "Sem cubagem",
     lambda cad, med, fat: med["vol"] <= 0),
]


def qualidade_cadastro(prod_map, emb_map=None, forn_map=None, comp_map=None, comprador=None):
    """Produtos com problema no CADASTRO LOGÍSTICO, sobre a **base inteira**.

    ⚠️ Universo DIFERENTE do resto da aba Qualidade de propósito, e a tela escreve isso.
    As checagens antigas dependem de estoque ("sem giro c/ estoque", "estoque negativo") e por
    isso rodam sobre o snapshot, que é recortado por FILIAL. Estas não dependem: o erro está no
    produto, exista ele em qual filial for. Medido em 08/2026: 72 produtos na base inteira
    contra **21** dentro do snapshot do Atacado — ligar a checagem no snapshot faria a tela
    mostrar 21 e a planilha enviada ao cliente dizer 70, e alguém perguntaria cadê o resto.

    Substitui o `cubagem_a_corrigir.csv` gerado à mão: duas fontes da mesma lista divergem no
    primeiro cadastro que o TI corrigir.
    """
    emb_map = emb_map or {}
    forn_map = forn_map or {}
    comp_map = comp_map or {}
    comprador = int(_n(comprador)) or None
    linhas, contagem = [], {k: 0 for k, _, _ in QUAL_CADASTRO}
    for cod, cad in (prod_map or {}).items():
        f = forn_map.get(int(_n((cad or {}).get("CODFORNEC")))) or {}
        codcomp = int(_n(f.get("CODCOMPRADOR"))) or None
        if comprador and codcomp != comprador:
            continue
        qe = _n((emb_map.get(cod) or {}).get("qtunit"))
        fat = (qe if qe > 1 else _n((cad or {}).get("QTUNITCX"))) or 1
        med = medidas_unitarias(cad, fat)
        probs = [(k, lbl) for k, lbl, fn in QUAL_CADASTRO if fn(cad, med, fat)]
        if not probs:
            continue
        for k, _lbl in probs:
            contagem[k] += 1
        linhas.append({
            "codprod": cod,
            "descricao": (cad.get("DESCRICAO") or "").strip(),
            "fornecedor": f.get("FORNECEDOR"),
            "codcomprador": codcomp,
            "comprador": comp_map.get(codcomp) if codcomp else None,
            "un_por_cx": int(fat) if fat > 1 else None,
            "peso_un_kg": _round(med["bruto"], 4) or None,
            "volume_un_m3": _round(med["vol"], 6) or None,
            # é a CAIXA implicada que denuncia o cadastro — 5,3 kg por unidade parece
            # plausível; 530 kg por caixa, não.
            "caixa_kg": _round(med["bruto"] * fat, 1) if med["bruto"] > 0 else None,
            "caixa_m3": _round(med["vol"] * fat, 3) if med["vol"] > 0 else None,
            "categorias": [k for k, _ in probs],
            "problemas": " · ".join(lbl for _, lbl in probs),
        })
    linhas.sort(key=lambda x: (-(x["caixa_m3"] or 0), -(x["caixa_kg"] or 0), x["codprod"]))
    return {"resumo": {"total": len(linhas), "base": len(prod_map or {}),
                       "contagem": contagem,
                       "rotulos": {k: lbl for k, lbl, _ in QUAL_CADASTRO},
                       "max_m3_caixa": MAX_M3_CAIXA, "max_kg_caixa": MAX_KG_CAIXA},
            "produtos": linhas}


# ───────────────────────── pesquisa de preço (comparação) ─────────────────────────
def normaliza_pesquisa(preco, unidade="un", com_imposto=False, qtunitcx=None,
                       perc_ipi=0.0, perc_st=0.0):
    """Traz o preço digitado em campo para a MESMA régua do `custo_unit`: R$ por UNIDADE, em
    MERCADORIA (sem imposto). Função pura — é aqui que moram as duas armadilhas do módulo.

    ⚠️ **Unidade.** Quem pesquisa lê a etiqueta da caixa. Comparar "R$ 45 a caixa" com um custo
    unitário erra pelo fator inteiro — é a mesma família do pedido que saía ~50x errado por
    converter quantidade sem converter preço (ver `item_master`).

    ⚠️ **Imposto.** `CUSTOFIN` é MERCADORIA; preço de gôndola tem tributo dentro. É a "duas
    réguas" (mercadoria × NF) que já mordeu no Orçamento.

    ⚠️ **Sem fator de caixa não se chuta.** Preço em `cx` sem `qtunitcx` > 1 devolve
    `comparavel=False` e valor None — a tela mostra "—". Mesma política do `medidas_confiaveis`:
    número errado que parece plausível é pior que célula vazia.

    Retorna {preco_un, comparavel, motivo}.
    """
    p = _n(preco)
    if p <= 0:
        return {"preco_un": None, "comparavel": False, "motivo": "preco_invalido"}
    if str(unidade or "un").lower() == "cx":
        f = _n(qtunitcx)
        if f <= 1:
            return {"preco_un": None, "comparavel": False, "motivo": "sem_fator_caixa"}
        p = p / f
    if com_imposto:
        # o digitado é NF; volta para mercadoria dividindo pelos mesmos % que a sugestão soma
        div = 1 + (_n(perc_ipi) + _n(perc_st)) / 100.0
        if div <= 0:
            return {"preco_un": None, "comparavel": False, "motivo": "imposto_invalido"}
        p = p / div
    return {"preco_un": _round(p, 4), "comparavel": True, "motivo": None}


def gap_pesquisa(preco_un, custo_unit):
    """Diferença entre o preço pesquisado e o NOSSO custo, na régua já normalizada.

    Positivo = pesquisamos MAIS CARO que o nosso custo (o fornecedor atual está melhor).
    Negativo = achamos mais barato — é a linha que vira argumento de negociação.
    `None` quando falta um dos lados: não se inventa comparação."""
    a, b = _n(preco_un), _n(custo_unit)
    if a <= 0 or b <= 0:
        return {"delta": None, "delta_pct": None}
    return {"delta": _round(a - b, 4), "delta_pct": _round((a - b) / b * 100, 1)}


def casa_busca(cad, busca):
    """Mesma regra de busca da tela e do export: `codprod` OU `descricao`, case-insensitive.
    Regra única de propósito — três implementações do mesmo filtro é como as telas divergem."""
    b = (busca or "").strip().lower()
    if not b:
        return True
    return b in str((cad or {}).get("CODPROD") or "") or b in str((cad or {}).get("DESCRICAO") or "").lower()


def recorta_abertos_por_produto(res, por_pedido):
    """Aplica o recorte de PRODUTO ao bloco de pedidos em aberto do Orçamento.

    `por_pedido`: {numped: {"qt_pedida", "qt_aberta"}} — de `logistica_pedidos(busca=…)`.

    ⚠️ Recorta a lista E **recalcula** os agregados que a tela mostra ao lado dela
    (`n_abertos`, `n_atrasados`, `n_chega7`, `valor_aberto`). Os cards de atraso leem a
    CONTAGEM do resumo e o VALOR da lista no cliente: recortar só um dos dois deixaria "15
    entregas atrasadas" ao lado de uma tabela com um pedido — o mesmo defeito de dois
    universos que a aba Verbas teve duas vezes em 08/2026.

    ⚠️ Os KPIs de ORÇAMENTO (meta, comprado, saldo, consumido) ficam INTACTOS de propósito:
    são do comprador no mês, não do item. Recortá-los por produto não significaria nada.
    """
    abertos = [p for p in res["abertos"] if p["numped"] in por_pedido]
    for p in abertos:
        p["qt_produto_pedida"] = _round(por_pedido[p["numped"]]["qt_pedida"], 2)
        p["qt_produto_aberta"] = _round(por_pedido[p["numped"]]["qt_aberta"], 2)
    r = dict(res["resumo"])
    r["n_abertos"] = len(abertos)
    r["n_atrasados"] = sum(1 for p in abertos if p["status_prazo"] == "atrasado")
    r["n_chega7"] = sum(1 for p in abertos if p["status_prazo"] == "chega_7")
    r["valor_aberto"] = _round(sum(p["valor_aberto"] for p in abertos))
    r["filtro_produto"] = True
    return {**res, "resumo": r, "abertos": abertos}


def logistica_pedidos(cab, itens, prod_map, embalagem_map, comp_map, forn_map, hoje=None,
                      capacidade_m3=60.0, baixa_ate=0.1, dias=180, busca=None):
    """Cubagem/ocupação por pedido em ABERTO (o que ainda vai chegar).
    cubagem = Σ (qtd_aberta × volume_unitário); ocupação = cubagem ÷ capacidade do veículo."""
    hoje = hoje or date.today()
    corte = hoje - timedelta(days=int(dias))
    cab_by = {}
    for r in cab:
        dtem = _parse_dt(r.get("DTEMISSAO"))
        if not dtem or dtem < corte:
            continue
        vlt, vle = _n(r.get("VLTOTAL")), _n(r.get("VLENTREGUE"))
        if max(0.0, vlt - vle) <= max(1.0, vlt * 0.005):
            continue  # já recebido
        cab_by[int(_n(r.get("NUMPED")))] = r
    ped = {}
    # {numped: {qt_pedida, qt_aberta}} do produto buscado — alimenta a coluna de quantidade
    # do Orçamento. Preenchido ANTES do corte de `oq > 0`: item já entregue dentro de um
    # pedido ainda aberto responde "eu já pedi?" mesmo não respondendo "está chegando?".
    do_produto = {}
    for r in itens:
        np_ = int(_n(r.get("NUMPED")))
        if np_ not in cab_by:
            continue
        cod = int(_n(r.get("CODPROD")))
        cad = prod_map.get(cod) or {}
        oq = _n(r.get("qtped")) - _n(r.get("qtentregue"))
        if busca and casa_busca(cad, busca):
            _d = do_produto.setdefault(np_, {"qt_pedida": 0.0, "qt_aberta": 0.0})
            _d["qt_pedida"] += _n(r.get("qtped"))
            _d["qt_aberta"] += max(0.0, oq)
        if oq <= 0:
            continue
        emb = (embalagem_map or {}).get(cod) or {}
        _cx_emb = _n(emb.get("qtunit"))
        cx = (_cx_emb if _cx_emb > 1 else _n(cad.get("QTUNITCX"))) or 1
        # peso e volume por UNIDADE, do PCPRODUT (ver medidas_unitarias): a PCEMBALAGEM tem
        # PESOBRUTO vazio em 75,6% e VOLUME em 100%, e era ela a fonte do peso aqui.
        med = medidas_unitarias(cad, cx)
        uv = med["vol"] if med["confiavel"] else 0.0
        peso_un = med["bruto"] if med["confiavel"] else 0.0
        cxs = (oq / cx if cx > 1 else oq)
        d = ped.setdefault(np_, {"cubagem": 0.0, "skus": 0, "caixas": 0.0, "unid": 0.0,
                                 "sem_vol": 0, "peso": 0.0, "sem_peso": 0})
        d["cubagem"] += oq * uv
        d["unid"] += oq
        d["caixas"] += cxs
        d["peso"] += oq * peso_un
        d["skus"] += 1
        if uv <= 0:
            d["sem_vol"] += 1
        if peso_un <= 0:
            d["sem_peso"] += 1
    out = []
    for np_, d in ped.items():
        r = cab_by[np_]
        valor_aberto = max(0.0, _n(r.get("VLTOTAL")) - _n(r.get("VLENTREGUE")))
        cub = d["cubagem"]
        ocup = (cub / capacidade_m3) if capacidade_m3 > 0 else 0.0
        if cub <= 0:
            status = "sem_cubagem"
        elif ocup <= baixa_ate:
            status = "baixa"
        elif ocup <= 0.3:
            status = "media"
        else:
            status = "ok"
        forn = forn_map.get(int(_n(r.get("CODFORNEC"))))
        dtprev = _parse_dt(r.get("DTPREVENT"))
        out.append({
            "numped": np_,
            "data_pedido": (_parse_dt(r.get("DTEMISSAO")) or date.min).isoformat(),
            "fornecedor": (forn or {}).get("FORNECEDOR") if forn else None,
            "comprador": comp_map.get(int(_n(r.get("CODCOMPRADOR")))),
            "skus": d["skus"], "caixas": _round(d["caixas"]), "unidades": _round(d["unid"]),
            "cubagem_m3": _round(cub, 3), "valor_aberto": _round(valor_aberto),
            "peso_kg": _round(d["peso"], 2),
            "valor_m3": _round(valor_aberto / cub) if cub > 0 else None,
            "ocupacao": _round(ocup, 3), "status": status,
            "sem_cubagem_itens": d["sem_vol"], "sem_peso_itens": d["sem_peso"],
            "dt_previsao": dtprev.isoformat() if dtprev else None,
        })
    out.sort(key=lambda x: x["cubagem_m3"], reverse=True)
    resumo = {
        "n_pedidos": len(out),
        "cubagem_total": _round(sum(p["cubagem_m3"] for p in out), 2),
        "valor_total": _round(sum(p["valor_aberto"] for p in out)),
        "n_baixa": sum(1 for p in out if p["status"] == "baixa"),
        "capacidade_m3": capacidade_m3, "baixa_ate": baixa_ate,
    }
    return {"resumo": resumo, "pedidos": out, "do_produto": do_produto}


# ───────────────────────── validade / FEFO ─────────────────────────
def vencidos_por_mes(rows, produtos_idx=None, venda_mes_map=None, venda_comp_map=None,
                     venda_comp_mes_map=None):
    """Perda por VALIDADE (conta 200042) por mês — espelha a planilha VENCIDOS do diretor.

    rows: saída de q_vencidos (grão = item da nota, já escopado na conta 200042).
    produtos_idx: {codprod: produto} do snapshot atual. Serve p/ marcar o item que já
    venceu e **ainda está na casa** (qtdisp > 0) = risco de vencer de novo — é o
    contraponto do validade_fefo (que olha risco futuro; aqui é perda realizada).
    venda_mes_map/venda_comp_map: venda líquida por mês ('YYYY-MM') e por comprador (cc),
    p/ o % da perda sobre a venda. Só há venda ≥2024 → meses antigos saem com pct=None.
    venda_comp_mes_map: venda líquida CRUZADA {'cc|YYYY-MM': liq}. É o denominador que faltava
    para o % responder ao filtro de comprador — sem ele a tela ou escondia a linha de % ou
    mostrava o percentual all-time do comprador ignorando o período selecionado (07/2026).
    Viaja cru para o front, que é quem sabe qual comprador e qual período estão na tela.
    """
    idx = produtos_idx or {}
    vmes = venda_mes_map or {}
    vcomp = venda_comp_map or {}

    def _pct(perda, venda):
        return _round(perda / venda * 100, 3) if venda else None
    itens = []
    for r in rows:
        dt = _parse_dt(r.get("dtsaida"))
        cod = r.get("codprod")
        cod = int(_n(cod)) if cod is not None else None
        qt, punit = _n(r.get("qt")), _n(r.get("punit"))
        p = idx.get(cod) or {}
        qtdisp = _n(p.get("qtdisp"))
        itens.append({
            "dtsaida": dt.isoformat() if dt else None,
            "mes": dt.strftime("%Y-%m") if dt else None,
            "numnota": int(_n(r.get("numnota"))),
            "codprod": cod,
            "descricao": r.get("descricao") or p.get("descricao") or "—",
            "qt": _round(qt),
            "punit": _round(punit, 6),
            "total": _round(_n(r.get("total")) or qt * punit),
            "codfornec": int(_n(r.get("codfornec"))) or None,
            "fornecedor": r.get("fornecedor") or "—",
            "codcomprador": int(_n(r.get("codcomprador"))) or None,
            "comprador": r.get("comprador") or "Sem comprador",
            "codfilial": str(r.get("codfilial") or ""),
            "curva_abc": p.get("curva_abc"),
            "qtdisp": _round(qtdisp),
            "em_estoque": qtdisp > 0,
        })
    itens.sort(key=lambda x: (x["dtsaida"] or "", x["total"]), reverse=True)

    # por mês (o pedido do diretor)
    mm = {}
    for i in itens:
        if not i["mes"]:
            continue
        g = mm.setdefault(i["mes"], {"mes": i["mes"], "itens": 0, "qt": 0.0, "valor": 0.0, "produtos": set()})
        g["itens"] += 1
        g["qt"] += i["qt"]
        g["valor"] += i["total"]
        g["produtos"].add(i["codprod"])
    meses = []
    for g in mm.values():
        venda = vmes.get(g["mes"])
        meses.append({"mes": g["mes"], "itens": g["itens"], "qt": _round(g["qt"]),
                      "valor": _round(g["valor"]), "produtos": len(g["produtos"]),
                      "venda": _round(venda) if venda is not None else None,
                      "pct": _pct(g["valor"], venda)})
    meses.sort(key=lambda x: x["mes"], reverse=True)

    # rankings (comprador / fornecedor)
    def _rank(cod_key, nome_key, venda_map=None):
        # venda_map (só p/ comprador): venda líq → % da perda sobre a venda. O numerador
        # do % conta só a perda dos meses COM venda (≥2024), p/ casar com o denominador.
        g = {}
        for i in itens:
            d = g.setdefault(i[nome_key], {"nome": i[nome_key], "cod": i.get(cod_key),
                                           "itens": 0, "qt": 0.0, "valor": 0.0, "valor_com_venda": 0.0})
            d["itens"] += 1
            d["qt"] += i["qt"]
            d["valor"] += i["total"]
            if i.get("mes") in vmes:
                d["valor_com_venda"] += i["total"]
        out = []
        for d in g.values():
            d["qt"], d["valor"] = _round(d["qt"]), _round(d["valor"])
            if venda_map is not None:
                venda = venda_map.get(d["cod"])
                d["venda"] = _round(venda) if venda is not None else None
                d["pct"] = _pct(d.pop("valor_com_venda"), venda)
            else:
                d.pop("valor_com_venda", None)
            out.append(d)
        return sorted(out, key=lambda x: -x["valor"])

    # por produto — base do painel "ainda em estoque"
    pp = {}
    for i in itens:
        d = pp.setdefault(i["codprod"], {
            "codprod": i["codprod"], "descricao": i["descricao"],
            "fornecedor": i["fornecedor"], "codfornec": i["codfornec"],
            "comprador": i["comprador"], "codcomprador": i["codcomprador"], "curva_abc": i["curva_abc"],
            "vezes": 0, "qt": 0.0, "valor": 0.0, "ultima": None,
            "qtdisp": i["qtdisp"], "em_estoque": i["em_estoque"]})
        d["vezes"] += 1
        d["qt"] += i["qt"]
        d["valor"] += i["total"]
        if (i["dtsaida"] or "") > (d["ultima"] or ""):
            d["ultima"] = i["dtsaida"]
    for d in pp.values():
        d["qt"], d["valor"] = _round(d["qt"]), _round(d["valor"])
    produtos = sorted(pp.values(), key=lambda x: -x["valor"])
    em_estoque = [p for p in produtos if p["em_estoque"]]

    pior = max(meses, key=lambda m: m["valor"]) if meses else None
    # % global: perda dos meses COM venda ÷ venda líquida total (mesmo período)
    perda_com_venda = sum(i["total"] for i in itens if i.get("mes") in vmes)
    venda_total = sum(vmes.values())
    return {
        "resumo": {
            "itens": len(itens),
            "qt": _round(sum(i["qt"] for i in itens)),
            "valor": _round(sum(i["total"] for i in itens)),
            "produtos": len(pp),
            "meses": len(meses),
            "mes_pior": pior["mes"] if pior else None,
            "mes_pior_valor": pior["valor"] if pior else 0,
            "em_estoque_n": len(em_estoque),
            "em_estoque_valor": _round(sum(p["valor"] for p in em_estoque)),
            "venda_total": _round(venda_total) if venda_total else None,
            "pct_total": _pct(perda_com_venda, venda_total),
        },
        "meses": meses,
        # denominador cruzado (comprador × mês) — o front recorta por comprador E por período
        "venda_comp_mes": venda_comp_mes_map or {},
        "itens": itens,
        "produtos": produtos,
        "em_estoque": em_estoque,
        "por_comprador": _rank("codcomprador", "comprador", venda_map=vcomp),
        "por_fornecedor": _rank("codfornec", "fornecedor"),
    }


def validade_fefo(lotes, produtos_idx, params, hoje=None):
    hoje = hoje or date.today()
    # unifica lotes do MESMO produto + validade (soma a qtd) — evita linhas repetidas do mesmo
    # item e deixa o saldo/risco correto (consumo projetado incide sobre o total, não por lote).
    agg = {}
    for r in lotes:
        cod = int(_n(r.get("CODPROD")))
        dtval = _parse_dt(r.get("DTVAL"))
        if not dtval:
            continue
        a = agg.setdefault((cod, dtval), {"qt": 0.0, "n_lotes": 0, "lote": None, "desc": None})
        a["qt"] += _n(r.get("qt"))
        a["n_lotes"] += 1
        a["lote"] = r.get("NUMLOTE") or a["lote"]
        a["desc"] = a["desc"] or r.get("DESCRICAO")   # nome vindo do próprio lote (LOOKUPVALUE)

    out = []
    for (cod, dtval), a in agg.items():
        qt = a["qt"]
        p = produtos_idx.get(cod, {})
        giro_dia = p.get("giro_dia") or 0
        custo_unit = p.get("custo_unit") or 0

        dias = (dtval - hoje).days
        consumo_proj = giro_dia * max(dias, 0)
        saldo_proj = qt - consumo_proj
        valor_risco = max(0.0, saldo_proj) * custo_unit

        if dias <= 7:
            classif = "critico"
        elif dias <= 15:
            classif = "atencao"
        else:
            classif = "planejar"
        if giro_dia <= 0:
            risco = "giro_zero"
        elif saldo_proj > 0:
            risco = "alto" if dias <= 15 else "medio"
        else:
            risco = "baixo"

        # rótulo do lote: mostra o nº quando é um só; senão indica quantos foram unificados
        numlote = (a["lote"] or "—") if a["n_lotes"] == 1 else f"{a['n_lotes']} lotes"

        out.append({
            "codprod": cod,
            "descricao": p.get("descricao") or a.get("desc") or f"PRODUTO {cod}",
            "fornecedor": p.get("fornecedor"),
            "comprador": p.get("comprador"),
            "curva_abc": p.get("curva_abc"),
            # XYZ do PRODUTO na linha do lote (pedido do diretor 19/08). Não é enfeite: a aba
            # projeta `saldo_proj`/`valor_risco` com o giro MÉDIO, e num item Z (demanda errática)
            # essa média é justamente o número menos confiável. A coluna é o qualificador da
            # própria estimativa que a tela já mostra — item X com 30 dias é administrável, item
            # Z com os mesmos 30 dias não se projeta.
            # Vem vazio para produto sem média de 3 meses (sem CV) — a tela mostra "—", igual ao
            # ABC, que já se comporta assim para lote fora do snapshot da unidade.
            "xyz": p.get("xyz"),
            "numlote": numlote,
            "n_lotes": a["n_lotes"],
            "dtval": dtval.isoformat(),
            "dias_para_vencer": dias,
            "qt": _round(qt),
            "giro_dia": _round(giro_dia, 3),
            "consumo_proj": _round(consumo_proj),
            "saldo_proj": _round(saldo_proj),
            "custo_unit": _round(custo_unit, 4),
            "valor_risco": _round(valor_risco),
            "classificacao": classif,
            "risco": risco,
        })
    out.sort(key=lambda x: (x["dias_para_vencer"], -x["valor_risco"]))
    return out


# ───────────────────────── plano de reposição (time-phased / DRP) ─────────────────────────
def plano_reposicao(p, params, hoje=None, semanas=12):
    """Grade DRP semanal de um produto: projeta o saldo semana a semana, gera pedidos
    planejados quando cruza o estoque de segurança e calcula QUANDO o pedido precisa SAIR
    (liberação = recebimento − lead time).

    Ressalva: sem dados de trânsito no BI (QTTRANSITO=0) → inbound só pelo pendente (raro).
    Demanda herda o giro escolhido (média3/forecast); no modo sazonal varia por mês na grade."""
    hoje = hoje or date.today()
    giro_dia = p.get("giro_dia") or 0
    seg = p.get("est_seguranca") or 0
    alvo = p.get("est_alvo") or 0
    custo = p.get("custo_unit") or 0
    lead = p.get("lead_efetivo") or params.get("lead_time", 10)
    lead_sem = max(1, math.ceil(lead / 7.0))
    receb_prog_total = (p.get("qttransito") or 0) + (p.get("qtpend") or 0)
    # sazonalidade: demanda da semana varia pelo mês quando há fatores; senão constante
    nivel_base_dia = p.get("nivel_base_dia")
    fatores = p.get("fatores_sazonais")
    # caixa fechada: arredonda o pedido planejado p/ múltiplo de QTUNITCX
    arred = bool(params.get("arredonda_cx")) and (p.get("qtunitcx") or 0) > 1
    qtcx = p.get("qtunitcx") or 0

    if giro_dia <= 0:
        return {"semanas": [], "liberacoes": [], "inbound_zero": receb_prog_total <= 0,
                "lead_semanas": lead_sem, "sem_giro": True}

    def _dem_sem(data_ini):
        if nivel_base_dia and fatores:
            return nivel_base_dia * (fatores.get(data_ini.month) or fatores.get(str(data_ini.month)) or 1.0) * 7.0
        return giro_dia * 7.0

    saldo = p.get("qtdisp") or 0
    grade, liberacoes = [], []
    for s in range(1, semanas + 1):
        data_ini = hoje + timedelta(days=(s - 1) * 7)
        dem_sem = _dem_sem(data_ini)
        receb_prog = receb_prog_total if s == lead_sem else 0.0
        saldo = saldo - dem_sem + receb_prog
        receb_plan = 0.0
        if saldo < seg:
            receb_plan = max(0.0, round(alvo - saldo))
            n_cx = None
            if arred and receb_plan > 0:
                receb_plan, n_cx = arredonda_caixa(receb_plan, qtcx)
            saldo += receb_plan
            sem_lib = max(0, s - lead_sem)
            liberacoes.append({
                "semana": sem_lib,
                "data": (hoje + timedelta(days=sem_lib * 7)).isoformat(),
                "qt": _round(receb_plan),
                "qt_cx": n_cx,
                "valor": _round(receb_plan * custo),
            })
        grade.append({
            "semana": s,
            "data_ini": data_ini.isoformat(),
            "demanda": _round(dem_sem),
            "receb_prog": _round(receb_prog),
            "receb_plan": _round(receb_plan),
            "saldo_proj": _round(saldo),
            "abaixo_seg": saldo < seg,
        })
    return {
        "semanas": grade,
        "liberacoes": liberacoes,
        "estoque_seguranca": _round(seg),
        "estoque_alvo": _round(alvo),
        "lead_semanas": lead_sem,
        "inbound_zero": receb_prog_total <= 0,
        "sem_giro": False,
    }


# ───────────────────────── ocupação / WMS ─────────────────────────
TIPO_WMS = {"AP": "Picking", "AE": "Pulmão"}


def rua_conferencia(rua, itens_rows, vazias_rows):
    """Lista de conferência de uma RUA, em ORDEM DE CAMINHADA (prédio → nível → apto).
    Junta as posições COM estoque e as reservadas VAZIAS (que devem ser conferidas como
    vazias) — é o que valida o endereçamento na prateleira."""
    def _pos(r, situacao, qt, dtval):
        p, n, a = int(_n(r.get("predio"))), int(_n(r.get("nivel"))), int(_n(r.get("apto")))
        cp = r.get("codprod")
        return {"_p": p, "_n": n, "_a": a,
                "endereco": "R%d·P%d·N%d·A%d" % (int(rua), p, n, a),
                "tipo": TIPO_WMS.get(r.get("tipo"), r.get("tipo") or "—"),
                "codprod": int(_n(cp)) if cp is not None else None,
                "qt": qt, "dtval": dtval, "situacao": situacao}

    out = [_pos(r, "com estoque", _round(_n(r.get("qt"))), (str(r.get("dtval")) or "")[:10] if r.get("dtval") else None)
           for r in (itens_rows or [])]
    out += [_pos(r, "VAZIA (reservada)", 0, None) for r in (vazias_rows or [])]
    out.sort(key=lambda x: (x["_p"], x["_n"], x["_a"], -(x["qt"] or 0)))
    for x in out:
        x.pop("_p"); x.pop("_n"); x.pop("_a")
    return out


def ocupacao_resumo(kpi_rows, rua_rows, tipo_rows=None, vazias_rows=None):
    """KPIs de ocupação do depósito + ocupação por RUA e por tipo (picking/pulmão).
    Denominador = posições ativas (PCENDERECO); ocupadas = posições distintas com QT>0.
    media_pos = pares (produto×posição) / produtos endereçados."""
    k = kpi_rows[0] if kpi_rows else {}
    pos = int(_n(k.get("posicoes")))          # régua WMS: não-bloqueadas, todas as ruas
    occ = int(_n(k.get("ocupadas")))          # OFICIAL: WMS SITUACAO="O"
    ce = int(_n(k.get("com_estoque")))        # secundário: com estoque físico (QT>0)
    blq = int(_n(k.get("bloqueados")))        # à parte: BLOQUEIO="S"
    prod = int(_n(k.get("produtos")))
    pares = int(_n(k.get("pares")))
    livres = max(0, pos - occ)
    ruas = []
    for r in (rua_rows or []):
        rp = int(_n(r.get("posicoes")))
        ro = int(_n(r.get("ocupadas")))
        if rp <= 0:
            continue
        rua = r.get("RUA")
        ruas.append({
            "rua": int(_n(rua)) if rua is not None else None,
            "posicoes": rp, "ocupadas": ro,
            "pct": _round(ro / rp, 4) if rp else 0,
        })
    ruas.sort(key=lambda x: (x["pct"], x["posicoes"]), reverse=True)
    tipos = []
    for r in (tipo_rows or []):
        rp = int(_n(r.get("posicoes")))
        if rp <= 0:
            continue
        ro = int(_n(r.get("ocupadas")))
        cod = r.get("TIPOENDER")
        tipos.append({
            "tipo": cod, "label": TIPO_WMS.get(cod, cod or "—"),
            "posicoes": rp, "ocupadas": ro, "pct": _round(ro / rp, 4) if rp else 0,
        })
    tipos.sort(key=lambda x: x["posicoes"], reverse=True)
    # posições ocupadas pelo WMS mas sem estoque físico ("reservado vazio") + produto alocado
    vazias = []
    for r in (vazias_rows or []):
        cp = r.get("codprod")
        vazias.append({
            "end": "R%d·P%d·N%d·A%d" % (int(_n(r.get("rua"))), int(_n(r.get("predio"))),
                                        int(_n(r.get("nivel"))), int(_n(r.get("apto")))),
            "tipo": TIPO_WMS.get(r.get("tipo"), r.get("tipo") or "—"),
            "codprod": int(_n(cp)) if cp is not None else None,
            "nprod": int(_n(r.get("nprod"))),
        })
    vazias.sort(key=lambda x: (x["codprod"] is None, x["end"]))
    vazias_com_prod = sum(1 for v in vazias if v["codprod"] is not None)
    return {
        "posicoes": pos, "ocupadas": occ, "livres": livres, "produtos": prod,
        "com_estoque": ce, "bloqueados": blq,
        "vazias": vazias, "vazias_total": len(vazias), "vazias_com_prod": vazias_com_prod,
        "pct_ocupado": _round(occ / pos, 4) if pos else 0,
        "pct_livre": _round(livres / pos, 4) if pos else 0,
        "pct_com_estoque": _round(ce / pos, 4) if pos else 0,
        "media_pos": _round(pares / prod, 2) if prod else 0,
        "ruas": ruas, "tipos": tipos,
    }
