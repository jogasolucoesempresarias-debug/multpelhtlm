"""Seeder do HISTÓRICO de estoque da DEMO — enche a aba "Evolução do estoque".

Por que existe: a série real só pode ser fotografada dia a dia (estoque é POSIÇÃO — o saldo de
ontem é sobrescrito e não existe em lugar nenhum). Numa instância normal isso significa esperar
semanas. Na DEMO, esperar não faz sentido: ela existe para APRESENTAR a ferramenta, e uma aba
vazia na reunião de venda é o oposto do objetivo. Além disso a demo tem "hoje" ancorado
(`ANALYTICS_HOJE`), então o robô de foto nem roda lá — ver `estoque.historico._hoje_ancorado`.

O que faz: pega a posição ATUAL do `joga_demo` (a mesma que o painel mostra) e projeta N dias
para TRÁS, aplicando uma deriva plausível. O resultado é uma série que conta a história que a
aba foi feita para contar:

    estoque caindo devagar · capital parado caindo MAIS RÁPIDO · ruptura estável

⚠️ A ruptura estável é de propósito. Estoque caindo com ruptura subindo é desabastecimento, não
gestão — e a demo não pode ensinar a ler o gráfico errado.

────────────────────────────────────────────────────────────────────────────────────────────────
⚠️  TRAVA DE SEGURANÇA (mesma do `seed_metas_demo.py`): escreve no banco de AUTH (DB_*).
    1) exige  DEMO_SEED=1;
    2) RECUSA rodar se DB_NAME == 'multpel_db' (produção da Multpel).
────────────────────────────────────────────────────────────────────────────────────────────────

Uso:
    DEMO_SEED=1 python -X utf8 _seed_demo/seed_historico_demo.py [--dias 90] [--refazer]
"""
import argparse
import os
import random
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from estoque import core  # noqa: E402  (precisa do sys.path acima)

PROD_AUTH_DB = "multpel_db"
SEED = 42                      # mesma semente do resto do _seed_demo → série reprodutível


def _trava():
    if os.getenv("DEMO_SEED") != "1":
        sys.exit("recusado: exporte DEMO_SEED=1 para confirmar que o alvo é a DEMO.")
    alvo = os.getenv("DB_NAME", "")
    if alvo == PROD_AUTH_DB:
        sys.exit(f"recusado: DB_NAME={alvo} é a produção da Multpel.")
    print(f"[hist-demo] auth DB alvo: {alvo or '(default)'}")


def _produtos(app, unidade):
    """A posição de HOJE da demo, pela mesma porta que o painel usa."""
    from estoque import routes as R
    with app.test_request_context(f"/estoque/api/snapshot?unidade={unidade}"):
        produtos, _params, _fil = R._build_produtos()
    return produtos


def _deriva(p, d, rnd):
    """Como o item ERA `d` dias atrás. Determinístico por (codprod, dia) — reproduzível.

    ⚠️ A deriva é SELETIVA, não um fator único. Encolher tudo pelo mesmo percentual é o modelo
    ingênuo, e ele conta a história ERRADA: menos estoque com o mesmo giro = menos cobertura, e o
    "% na cobertura ideal" aparece PIORANDO conforme se aproxima de hoje (medido: 0,62 → 0,60).
    Comprar bem não é comprar menos de tudo — é cortar onde sobrava e repor onde faltava. Então:

      · cobertura alta (≥90d)  → tinha MUITO mais estoque no passado (excesso que foi drenado)
      · cobertura baixa (<45d) → tinha MENOS no passado (a falta que foi corrigida)
      · meio                   → praticamente estável

    Resultado: o total cai, o 121+ encolhe, o 31-60 engorda e o % ideal SOBE — que é a história
    que a aba existe para mostrar, e que corresponde ao que gestão de compra de fato faz.
    """
    giro_dia = p.get("giro_dia") or 0
    qt0 = p.get("qtdisp") or 0
    cob = p.get("cobertura_dias")
    if cob is None:
        cob = core.cobertura_dias_oficial(qt0, giro_dia)
    prog = d / 90.0                      # 0 hoje → 1 no início da janela
    # ⚠️ A faixa SAUDÁVEL (45-90) precisa ter tido MENOS estoque no passado, não mais. Se ela
    # crescer para trás, nenhum item cruza a linha dos 45 dias e o "% ideal" fica congelado —
    # foi o que aconteceu na 1ª versão (0,5998 idêntico nos 45 dias, ao lado de um gráfico que
    # se movia). É atravessar o limiar que faz o placar andar.
    if cob >= 90:
        k = 1.0 + 0.45 * prog            # excesso: bem maior lá atrás (foi drenado)
    elif cob >= 45:
        k = 1.0 - 0.25 * prog            # saudável hoje: parte dele estava ABAIXO do ideal antes
    else:
        k = 1.0 - 0.15 * prog            # em risco: melhorou de leve
    k = max(0.05, k + rnd.uniform(-0.03, 0.03))
    qt = qt0 * k
    custo = p.get("custo_unit") or 0
    # Item de giro rápido às vezes zera — sem isto a ruptura fica EXATAMENTE constante (escalar a
    # quantidade nunca troca o sinal) e uma linha reta por 90 dias denuncia dado fabricado. A
    # chance cai conforme se aproxima de hoje: a ruptura melhora de leve, sem virar milagre.
    if giro_dia > 0 and qt > 0 and (qt / giro_dia) <= 15 and rnd.random() < (0.06 + 0.05 * prog):
        qt = 0.0
    return {
        **p,
        "qtdisp": round(qt, 2),
        "valor": round(max(0.0, qt) * custo, 2),
        # ⚠️ RECALCULAR — não copiar o de hoje. Copiando, `qtdisp` andava e a cobertura ficava
        # congelada: faixas e % ideal idênticos em todos os dias, e o gráfico de composição (a
        # prova visual mais forte da aba) virava um bloco rígido. Achado no banco: qtdisp
        # 622,6 → 522,1 com cobertura fixa em 1302,3 nos 45 dias.
        "cobertura_dias": core.cobertura_dias_oficial(qt, giro_dia),
        # ⚠️ As DATAS andam junto com a foto. Fixas, elas invertem a história: numa foto de 44
        # dias atrás, `dia - dtultsaida` fica 44 dias MENOR, o item deixa de contar como parado e
        # o capital parado aparece CRESCENDO até hoje. Deslocando as duas pelo mesmo `d`, a idade
        # fica constante e a única variável é o nível de estoque. Medido antes: +97,6% na janela.
        "dtultsaida": _recua(p.get("dtultsaida"), d),
        "dtultent": _recua(p.get("dtultent"), d),
    }


def _recua(iso, d):
    """Data ISO deslocada `d` dias para trás (None passa direto)."""
    if not iso:
        return None
    try:
        return (date.fromisoformat(str(iso)[:10]) - timedelta(days=d)).isoformat()
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=90)
    ap.add_argument("--refazer", action="store_true", help="regrava mesmo se já houver histórico")
    args = ap.parse_args()
    _trava()

    from estoque import historico, store, routes as R
    import server                                   # registra o blueprint e dá o app Flask

    if not store.ensure():
        sys.exit("recusado: Postgres indisponível.")

    hoje = date.fromisoformat(os.getenv("ANALYTICS_HOJE") or date.today().isoformat())
    conn = store.get_db()
    with conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM estoque_foto_log")
        ja = cur.fetchone()[0]
    conn.close()
    if ja and not args.refazer:
        print(f"[hist-demo] já há {ja} fotos — nada a fazer (use --refazer para regravar).")
        return

    total = 0
    for unidade in R.UNIDADES:
        try:
            base = _produtos(server.app, unidade)
        except Exception as e:                                    # noqa: BLE001
            print(f"[hist-demo] {unidade}: sem posição ({e}); pulando.")
            continue
        if not base:
            continue
        for d in range(args.dias):
            dia = hoje - timedelta(days=d)
            rnd = random.Random(f"{SEED}:{unidade}:{dia}")
            prods = [_deriva(p, d, rnd) for p in base]
            total += historico.gravar(dia, unidade, prods,
                                      bi_refresh=f"{dia.strftime('%d/%m/%Y')} 03:00",
                                      params={"origem": "seed_demo"})
        print(f"[hist-demo] {unidade}: {args.dias} dias gravados.")
    print(f"[hist-demo] pronto — {total} linhas de foto.")


if __name__ == "__main__":
    main()
