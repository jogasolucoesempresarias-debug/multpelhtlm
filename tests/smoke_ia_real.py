"""Bateria de qualidade do Agente de Compras — contra a API e o BI REAIS.

⚠️ **NÃO é pytest** (o nome não começa com `test_`, então não é coletado). Roda à mão, porque
consome API paga e depende do Power BI:

    python -X utf8 tests/smoke_ia_real.py            # bateria completa
    python -X utf8 tests/smoke_ia_real.py --so 3     # só a pergunta 3

**Por que existe.** Os 39 testes unitários travam a FORMA do contexto; nenhum deles pega a
regressão que mais dói neste agente, que é comportamental. Já aconteceram duas, as duas
descobertas por gente clicando:

1. **Recusa tudo** — a 1ª versão respondeu "consulte a aba do produto" a 8 de 9 perguntas reais.
   Honesto e inútil: mata a adoção tão rápido quanto invenção.
2. **Contradiz o dado** — perguntado sobre compradores, somou `comprado` com `aberto` (que está
   DENTRO do comprado), concluiu "estourado" e inverteu o sinal de um saldo positivo.

As asserções abaixo não julgam eloquência — só essas duas famílias, que são estáveis e
verificáveis: *recusou quando tinha o dado?* e *disse "estourado" com saldo positivo?*
"""
import json
import os
import re
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(BASE)
sys.path.insert(0, BASE)

from dotenv import load_dotenv                                      # noqa: E402
load_dotenv(os.path.join(BASE, ".env"))
import requests                                                     # noqa: E402

# ⚠️ Aponte para um servidor em modo DEV (sem FLASK_ENV=production). Em produção o cookie de
# sessão sai com `Secure` e o `requests` — ao contrário do curl — não o envia por HTTP simples:
# o sintoma é um 401 logo depois de um login 200, que engana muito. Contra HTTPS, tanto faz.
URL = os.getenv("IA_SMOKE_URL", "http://127.0.0.1:5001")
EMAIL = os.getenv("IA_SMOKE_EMAIL", "gabriel@joga.local")
SENHA = os.getenv("IA_SMOKE_SENHA", "joga123")
QS = "unidade=atacado&base_estoque=gerencial&venda_periodo=mes"

# Marcas de recusa. Se aparecerem numa pergunta cujo pilar EXISTE, é a regressão nº 1 voltando.
_RECUSA = ("não tenho", "nao tenho", "não fornece", "nao fornece", "não traz", "nao traz",
           "não informa", "nao informa", "não consigo", "nao consigo", "não está disponível",
           "consulte o sistema", "relatório específico", "relatorio especifico")

# ⚠️ Perguntas que devolvem ITEM têm de trazer o CÓDIGO do produto na resposta (pedido do
# diretor: sem o código ninguém confere no ERP, e número que não se confere não vira decisão —
# o caso real foi um item aparecendo como maior espaço morto na semana em que chegou carga).
_EXIGE_CODIGO = {4, 5, 6, 12}

# Uma resposta que legitimamente diz "não há nada nessa categoria" NÃO tem item para citar. Sem
# esta saída, a checagem de código exige um código onde a resposta certa é "zero" — foi o que
# aconteceu no modo Postgres, onde a base sintética não tem espaço morto.
_VAZIO = ("não há", "nao ha", "nenhum", "não existe", "nao existe", "0 skus", "r$ 0,00",
          "não foram encontrados", "nao foram encontrados")


def eh_recusa(resp):
    """A resposta é uma RECUSA de verdade, ou só traz uma ressalva no fim?

    ⚠️ A 1ª versão marcava recusa a qualquer aparição das marcas, em qualquer posição. Isso
    reprovou uma resposta que entregou a matriz ABC×XYZ inteira e terminou com "o painel não
    fornece o total exato…" — que é a regra 2 do prompt sendo OBEDECIDA ("diga o que você tem e
    o que falta"). Reprovar a obediência é o pior sinal que uma bateria pode dar.

    A regressão que esta checagem existe para pegar era outra coisa: respostas SEM DADO NENHUM
    ("consulte a aba do produto"). Por isso a marca só conta quando a resposta também não traz
    números — é o que separa recusar de ressalvar."""
    low = (resp or "").lower()
    if not any(m in low for m in _RECUSA):
        return False
    return len(re.findall(r"\d", resp or "")) < 3


def exige_codigo(resp):
    """A resposta cita item e portanto deve trazer o código — ou afirma que não há item algum?"""
    low = (resp or "").lower()
    return not any(m in low for m in _VAZIO)

# (pergunta, pilar que deve responder, proibições extras)
BATERIA = [
    ("Como está a saúde do estoque agora?", "placar", []),
    ("Como está o desempenho dos compradores?", "compradores", ["estourado" ]),
    ("Como está a ruptura por comprador?", "ruptura", []),
    ("Qual o item com mais de 30 dias sem vendas com maior estoque financeiro?", "parado", []),
    ("Qual o item com maior cobertura de estoque hoje?", "cobertura", []),
    ("Quais itens vencem nos próximos 10 dias?", "validade", []),
    ("Quanto perdemos por validade nos últimos meses?", "vencidos", []),
    ("Quais os top 5 fornecedores com maior lucro bruto?", "fornecedores", []),
    ("Qual o fornecedor com maior estoque financeiro hoje?", "fornecedores", []),
    ("Estamos dentro da meta de ruptura por curva?", "meta_ruptura", []),
    ("Como está a matriz ABC XYZ do estoque?", "abc_xyz", []),
    ("Tenho espaço morto no armazém?", "ocupacao", []),
    ("Quais fornecedores demoram mais para entregar?", "leadtime", []),
    ("Quanto temos de verba negociada e com quem?", "verbas", []),
    ("Tem problema de cadastro na base?", "qualidade", []),
    ("Quanto vou gastar se comprar tudo que está sugerido?", "compra", []),
    # controle: fora do escopo → recusar É o certo aqui
    ("Qual foi o faturamento da empresa em 2019?", None, []),
]


def _sse(sess, pergunta):
    t0 = time.time()
    txt = ""
    r = sess.post(f"{URL}/estoque/api/ia/chat?{QS}",
                  json={"pergunta": pergunta, "historico": []}, stream=True, timeout=300)
    if r.status_code != 200:
        return f"[HTTP {r.status_code}] {r.text[:200]}", time.time() - t0
    for linha in r.iter_lines(decode_unicode=True):
        if not linha or not linha.startswith("data: "):
            continue
        bruto = linha[6:]
        if bruto == "[DONE]":
            continue
        try:
            o = json.loads(bruto)
        except Exception:
            continue
        if o.get("erro"):
            return f"[ERRO] {o['erro']}", time.time() - t0
        txt += o.get("token") or ""
    return txt, time.time() - t0


def main():
    so = None
    if "--so" in sys.argv:
        so = int(sys.argv[sys.argv.index("--so") + 1])

    s = requests.Session()
    r = s.post(f"{URL}/api/login", json={"email": EMAIL, "senha": SENHA}, timeout=30)
    if r.status_code != 200:
        print(f"login falhou: {r.status_code} {r.text[:200]}")
        return 1
    ctx = s.get(f"{URL}/estoque/api/ia/contexto?{QS}", timeout=900).json()
    pilares = {k for k, v in (ctx["contexto"].get("pilares") or {}).items() if v}
    orc = ctx["contexto"].get("orcamento") or {}
    saldo_positivo = (orc.get("saldo") or 0) > 0
    print(f"pilares ativos: {len(pilares)} · saldo do orçamento "
          f"{'POSITIVO' if saldo_positivo else 'negativo'}\n" + "=" * 78)

    falhas = []
    for i, (q, pilar, proibidas) in enumerate(BATERIA, 1):
        if so and i != so:
            continue
        resp, dt = _sse(s, q)
        low = resp.lower()
        print(f"\n[{i}/{len(BATERIA)}] {q}   ({dt:.1f}s)")
        print("-" * 78)
        print(resp)

        # 1) recusou tendo o pilar?
        tem_pilar = pilar in pilares or pilar in ("placar", "compra")
        if tem_pilar and eh_recusa(resp):
            falhas.append(f"[{i}] RECUSOU tendo o pilar '{pilar}': {q}")
        if pilar is None and not any(m in low for m in _RECUSA + ("só consigo", "so consigo")):
            falhas.append(f"[{i}] NÃO recusou pergunta fora de escopo: {q}")

        # 2) contradisse o dado? (a regressão do orçamento)
        if saldo_positivo and re.search(r"orçamento (está |esta )?estourad", low):
            falhas.append(f"[{i}] disse 'orçamento estourado' com saldo POSITIVO")
        for pr in proibidas:
            if pr in low and saldo_positivo:
                falhas.append(f"[{i}] termo proibido '{pr}' com saldo positivo")

        # 3) respondeu sem nenhum número? (sintoma de resposta vazia)
        if tem_pilar and not re.search(r"\d", resp):
            falhas.append(f"[{i}] resposta sem NENHUM número: {q}")

        # 4) citou item sem o CÓDIGO? (regra do diretor — auditoria)
        # ⚠️ o `\b` desta regex ja foi gravado como BYTE 0x08 uma vez (backspace), e aí
        # ela nunca casava: as perguntas 4/5/6/12 falhavam sempre, com ou sem código na
        # resposta. Falha constante ensina quem roda a bateria a ignorar o relatório.
        # ⚠️ e só vale quando a resposta CITA item: "não há espaço morto" não tem código a citar
        # (aconteceu no modo Postgres, cuja base sintética não tem nenhum).
        if i in _EXIGE_CODIGO and exige_codigo(resp) and not re.search(r"\b\d{4,6}\b", resp):
            falhas.append(f"[{i}] citou item SEM o código do produto: {q}")

    print("\n" + "=" * 78)
    if falhas:
        print(f"❌ {len(falhas)} FALHA(S):")
        for f in falhas:
            print("  " + f)
        return 2
    print("✅ bateria completa sem falhas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
