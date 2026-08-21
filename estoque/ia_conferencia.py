"""Conferência de saída do Agente — a rede que confere NÚMERO CITADO contra o contexto.

**Por que existe.** Todo o resto da defesa deste agente é PROSA: as regras 1, 1b e 1c mandam não
recalcular, não somar dois números do contexto e não contradizer um valor recebido. Prosa é a
defesa certa para orientar o modelo, mas não é verificação — e as duas regressões que já doeram
neste agente foram exatamente disto:

1. **Somou dois números** (`comprado_mes` + `aberto_mes`, que está DENTRO do primeiro) e produziu
   um terceiro que não existia em lugar nenhum;
2. **Inverteu o sinal** de um saldo positivo para encaixá-lo na conclusão "estourado".

As duas seriam pegas aqui: o número inventado não está ancorado em nada do contexto. É a mesma
ideia do gate `prompt x core` — o que o agente AFIRMA tem de corresponder ao que existe.

⚠️ **NÃO bloqueia a resposta, e isso é decisão, não preguiça.** O modelo reformata legitimamente:
o contexto traz `R$ 118.506,19` e ele escreve "R$ 118,5 mil". Bloquear com um casamento ingênuo
derrubaria resposta boa, que é pior que o problema. Aqui a conferência **registra** — o resultado
vai para o `multpel_log` junto da resposta, e aí existe a pergunta que hoje não tem resposta:
*"em quantas respostas ele citou número que não estava no contexto?"*. É o primeiro insumo real
de um harness de avaliação.

⚠️ **Função PURA**: recebe dois textos e devolve o achado. Sem I/O, sem estado, sem rede — pela
mesma razão que o `historico.agregar` é puro: é o que permite testá-la e re-rodá-la sobre o
histórico já gravado sem tocar em nada.
"""

import re

# ── extração ───────────────────────────────────────────────────────────────────────────────
# Valores em R$ no formato brasileiro, com o multiplicador por extenso que o modelo usa em prosa
# ("R$ 1,2 milhão"). Sem o multiplicador, "R$ 1,2" seria comparado contra 1,20 e nunca ancoraria.
_RE_BRL = re.compile(
    r"R\$\s*([0-9][0-9.]*(?:,[0-9]{1,2})?)\s*(mil|milh(?:ão|ões|oes|ao)|mi\b)?",
    re.IGNORECASE)
_RE_PCT = re.compile(r"([0-9]+(?:[.,][0-9]+)?)\s*%")
# Código de produto/fornecedor: 4 a 6 dígitos isolados. É a régua que a bateria já usa.
_RE_COD = re.compile(r"(?<![0-9.,])([0-9]{4,6})(?![0-9.,])")

_MULT = {"mil": 1_000.0, "milhão": 1e6, "milhao": 1e6, "milhões": 1e6, "milhoes": 1e6, "mi": 1e6}

# Anos não são código de produto. A faixa é generosa de propósito: um falso NEGATIVO aqui custa
# um achado; um falso POSITIVO treina quem lê o relatório a ignorá-lo, que é o defeito que a
# checagem quebrada da bateria tinha.
_ANO_MIN, _ANO_MAX = 1900, 2100


def _num_br(txt):
    """'118.506,19' -> 118506.19. Devolve None no que não for número."""
    if txt is None:
        return None
    t = txt.strip().replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def valores_brl(texto):
    out = []
    for m in _RE_BRL.finditer(texto or ""):
        v = _num_br(m.group(1))
        if v is None:
            continue
        mult = (m.group(2) or "").lower().strip()
        out.append(v * _MULT.get(mult, 1.0))
    return out


def percentuais(texto):
    return [v for v in (_num_br(m.group(1).replace(".", ","))
                        for m in _RE_PCT.finditer(texto or "")) if v is not None]


def codigos(texto):
    return [int(m.group(1)) for m in _RE_COD.finditer(texto or "")
            if not (_ANO_MIN <= int(m.group(1)) <= _ANO_MAX)]


# ── ancoragem ──────────────────────────────────────────────────────────────────────────────
# ⚠️ A tolerância existe para o ARREDONDAMENTO da prosa, não para acomodar erro. 0,5% cobre
# "R$ 118,5 mil" contra R$ 118.506,19 (0,005%) e continua MUITO mais apertada que qualquer das
# duas regressões reais: somar `aberto` dentro de `comprado` deslocou o valor em ordens de
# grandeza, e inverter sinal muda o número inteiro.
TOL_REL = 0.005
TOL_ABS_BRL = 0.01
TOL_ABS_PCT = 0.1


def _ancorado(v, universo, tol_abs):
    return any(abs(v - c) <= max(tol_abs, TOL_REL * abs(c)) for c in universo)


def conferir(resposta, contexto_txt):
    """Devolve o que a resposta AFIRMA e o contexto não sustenta.

    `{'brl': [...], 'pct': [...], 'cod': [...], 'total_citado': n, 'ok': bool}`

    Um item numa das listas NÃO é prova de erro — é um número que não se explica pelo contexto e
    que merece o olho humano. É exatamente o material que faltava para investigar "ele me falou
    uma coisa errada" sem depender de memória."""
    ctx_brl = valores_brl(contexto_txt)
    ctx_pct = percentuais(contexto_txt)
    ctx_cod = set(codigos(contexto_txt))

    r_brl = valores_brl(resposta)
    r_pct = percentuais(resposta)
    r_cod = codigos(resposta)

    brl = sorted({v for v in r_brl if not _ancorado(v, ctx_brl, TOL_ABS_BRL)})
    pct = sorted({v for v in r_pct if not _ancorado(v, ctx_pct, TOL_ABS_PCT)})
    cod = sorted({c for c in r_cod if c not in ctx_cod})
    return {"brl": brl, "pct": pct, "cod": cod,
            "total_citado": len(r_brl) + len(r_pct) + len(r_cod),
            "ok": not (brl or pct or cod)}


def resumo(conf):
    """Uma linha para o log. `None` quando está tudo ancorado — linha de log que só diz 'ok'
    ocupa espaço e não se lê."""
    if not conf or conf.get("ok"):
        return None
    partes = []
    for chave, rot in (("brl", "R$"), ("pct", "%"), ("cod", "cod")):
        if conf.get(chave):
            partes.append(f"{rot}: {conf[chave]}")
    return " · ".join(partes)
