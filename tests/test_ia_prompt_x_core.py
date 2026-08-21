"""Gate: o PROMPT afirma fatos sobre o código — e nada travava a correspondência.

⚠️ **A doença, não o sintoma.** O glossário do agente declara réguas ("parado = sem venda há 60+
dias"), horários ("a foto é tirada de manhã") e contenções. Esses fatos vivem no `core` e no
`historico`, e quando o código mudou o prompt ficou para trás **em silêncio** — o agente seguiu
explicando ao comprador uma diferença que não existe mais.

Duas defasagens reais encontradas em 08/2026, as duas no MESMO parágrafo:

1. **A régua.** A série passou a usar `core.status_parado_de` (piso 60) para casar com o Cockpit,
   mas o prompt seguia dizendo que ela conta a partir de **15 dias** e que "por isso o valor da
   série é sempre bem MAIOR". Efeito: o agente explicaria como "réguas diferentes" uma divergência
   que hoje só pode vir de outra causa — e mandaria o comprador ignorar um número que talvez
   precisasse de atenção.
2. **O horário.** A foto passou de 6h-12h para 18h-22h (minuto 40) para sair depois do último
   refresh do dia. O prompt seguia dizendo "foto tirada de manhã".

Os testes abaixo MEDEM o comportamento do código e conferem contra o texto do prompt. São o
equivalente, para o prompt, do que o `_ROLLUP_VERSAO` é para o rollup: o mecanismo que impede
que uma mudança de significado passe despercebida.
"""
import datetime as _dt
import io
import re

from estoque import core, historico, ia


def _ctx(**over):
    """Contexto mínimo — LOCAL de propósito. Importar a fixture do `test_ia_compras` acoplaria
    os dois arquivos: mudar o fixture de lá quebraria o gate daqui por motivo nenhum."""
    base = dict(produtos=[], cockpit={"valor_total": 1000.0},
                cobertura=None, estoque_ideal={}, ruptura={"itens": 1, "total": 10, "perc": 0.1},
                orcamento={}, recorte={"unidade": "atacado"})
    base.update(over)
    return ia.montar_contexto(**base)


# ── sondagem: qual é o piso REAL de cada lado ──────────────────────────────────────────────
def _piso_do_placar():
    """Menor `dias_sem_venda` que o PLACAR (Cockpit) já considera parado.

    Sonda o comportamento em vez de ler a constante: constante renomeada não quebra o teste, mas
    régua alterada quebra — que é exatamente o que se quer travar."""
    for d in range(0, 400):
        if core.eh_parado({"status_parado": core.status_parado_de(d, 10, None, 15)}):
            return d
    return None


def _piso_da_serie():
    """Menor `dias_sem_venda` que a SÉRIE (foto diária) já soma em `valor_parado`."""
    hoje = _dt.date(2026, 8, 19)
    for d in range(0, 400):
        # tupla POSICIONAL, na ordem que o `agregar` espera (ver test_historico_estoque)
        linha = (hoje, 10, 1000.0, 1.0, 10, hoje - _dt.timedelta(days=d), None, "A", 0, 0)
        out = historico.agregar([linha])
        if out and out[0]["valor_parado"] > 0:
            return d
    return None


def test_a_serie_e_o_placar_usam_o_MESMO_piso():
    """Se estes dois divergirem de novo, o parágrafo do glossário tem de voltar a explicar a
    diferença — e é por isso que o teste seguinte lê o número daqui, não de uma constante."""
    placar, serie = _piso_do_placar(), _piso_da_serie()
    assert placar is not None and serie is not None, "não achei o piso — a sondagem quebrou"
    assert placar == serie, (
        f"o placar conta parado a partir de {placar} dias e a série a partir de {serie}. "
        f"Divergiram — o glossário do agente PRECISA explicar isso (era o caso até 08/2026, "
        f"quando eram 60 e 15).")
    print(f"\n  piso medido: placar={placar}d · série={serie}d")


def _bloco_serie_x_placar():
    """O parágrafo do glossário que fala da série contra o placar."""
    g = ia.GLOSSARIO
    ini = g.index("SÉRIE DE EVOLUÇÃO")
    resto = g[ini:]
    # até a próxima entrada em CAIXA ALTA no começo de linha, ou o fim
    m = re.search(r"\n(?=[A-ZÀ-Ú][A-ZÀ-Ú ]{6,}—)", resto[1:])
    return resto[: m.start() + 1] if m else resto


def test_o_prompt_NAO_afirma_um_piso_que_o_codigo_nao_pratica():
    """⚠️ O gate que pega a defasagem nº 1.

    Varre os "N dias" que o parágrafo série×placar afirma e exige que cada um seja um piso que o
    código realmente pratica. Prompt que cita 15 quando o código pratica 60 falha aqui."""
    piso = _piso_do_placar()
    bloco = _bloco_serie_x_placar()
    # números apresentados como régua de dias, em qualquer das formas que o texto usa
    citados = {int(n) for n in re.findall(r"\*\*(\d+)\s*dias?", bloco)}
    citados |= {int(n) for n in re.findall(r"(\d+)\s*dias? ou mais", bloco)}
    citados |= {int(n) for n in re.findall(r"partir de\s*\*{0,2}(\d+)", bloco)}
    assert citados, "o parágrafo não cita régua nenhuma — âncora do teste sumiu"
    erradas = citados - {piso}
    assert not erradas, (
        f"o glossário afirma a(s) régua(s) {sorted(erradas)} dias, mas o código pratica {piso}. "
        f"Parágrafo:\n{bloco.strip()[:600]}")
    print(f"\n  réguas citadas no prompt: {sorted(citados)} · praticada: {piso}")


def test_o_rotulo_da_serie_no_contexto_nao_promete_uma_regua_errada():
    """O render rotula cada ponto da série. O rótulo era `parado-15d` — nome que afirma a régua.
    Se afirmar, tem de afirmar a que o código pratica."""
    piso = _piso_do_placar()
    ctx = _ctx(tendencia=[{"data": "2026-08-19", "valor_estoque": 1.0, "valor_parado": 2.0,
                           "n_ruptura": 3}])
    txt = ia.renderizar_contexto(ctx)
    for n in {int(x) for x in re.findall(r"parado[- ]?(\d+)\s*d", txt)}:
        assert n == piso, (f"o rótulo da série diz 'parado-{n}d' e o código pratica {piso}d")


# ── defasagem nº 2: o horário da foto ──────────────────────────────────────────────────────
def _hora_da_foto():
    """Lê do `server.py` a janela do job da foto. Fonte de verdade é o agendamento real."""
    src = io.open("server.py", encoding="utf-8").read()
    m = re.search(r"add_job\([^)]*id=['\"]foto_estoque['\"]", src, re.S)
    if not m:
        m = re.search(r"add_job\(_fotografar_estoque[^)]*\)", src, re.S)
    bloco = m.group(0)
    h = re.search(r"hour=['\"](\d+)", bloco)
    return int(h.group(1)) if h else None


def test_o_prompt_nao_diz_MANHA_de_uma_foto_tirada_a_noite():
    """⚠️ O gate que pega a defasagem nº 2.

    A foto passou de 6h-12h para 18h-22h em 08/2026 — a mudança inverteu o significado de cada
    ponto ("início do dia" → "fim do dia"), e o prompt seguiu dizendo "de manhã". O agente
    descrevia ao comprador um dado que já não era aquele."""
    h = _hora_da_foto()
    assert h is not None, "não achei a hora do job da foto — âncora do teste sumiu"
    txt = (ia.GLOSSARIO + "\n" + ia.renderizar_contexto(_ctx(tendencia=[
        {"data": "2026-08-19", "valor_estoque": 1.0, "valor_parado": 2.0, "n_ruptura": 3}])))
    manha = re.findall(r"foto[^.\n]{0,40}manh[ãa]", txt, re.I)
    if h >= 12:
        assert not manha, (f"o job da foto roda às {h}h e o prompt a descreve como da manhã: "
                           f"{manha}")
    print(f"\n  foto agendada para {h}h · prompt coerente")


# ── qualidade do prompt como ARTEFATO ──────────────────────────────────────────────────────
def test_o_prompt_nao_repete_o_simbolo_de_moeda():
    """⚠️ O prompt trazia `R$ R$ 118.506,19` em 70 linhas de 4 pilares (parado, cobertura,
    fornecedores, ocupação) — os mais consultados. O rótulo da métrica era "R$" e o `_brl` já
    emite o símbolo.

    Só apareceu quando o rastro de auditoria passou a gravar o prompt e alguém finalmente LEU o
    artefato que o modelo recebe, em vez do código que o monta. É a lição embutida no teste:
    prompt é entregável, não subproduto."""
    from estoque import ia_pilares as P

    prod = [{"codprod": 69398, "descricao": "FILME ULTRAPLAST", "fornecedor": "ULTRAPLAST",
             "comprador": "ANA", "codfornec": 7, "valor": 118506.19, "qtdisp": 10.0,
             "giro_dia": 1.0, "giro_mes": 30.0, "cobertura_dias": 191, "dias_sem_venda": 64,
             "venda": 900.0, "lucro": 90.0, "venda_perdida": 0.0, "curva_abc": "A",
             "status_parado": "critico", "pos_end": 4, "m3_end": 2.0, "espaco_morto": True,
             "sugestao_compra": 1, "valor_sugerido_nf": 500.0}]
    txt = P.renderizar({"parado": P.parado(prod), "cobertura": P.cobertura(prod),
                        "fornecedores": P.fornecedores(prod), "ocupacao": P.ocupacao(prod)})
    ruins = [l.strip() for l in txt.split(chr(10)) if "R$ R$" in l]
    assert not ruins, f"{len(ruins)} linha(s) com moeda duplicada, ex.: {ruins[:2]}"
    assert "R$ 118.506,19" in txt, "âncora do teste: o valor tem de continuar saindo formatado"
