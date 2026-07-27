"""
Passo 4b — Carteira RFM no modo BD. Puxa o snapshot do joga_demo via SQL e ALIMENTA
o rfm.py REAL do app (matemática pura) → segmentação idêntica à produção, zero regra reescrita.
Prova que o Comercial (parte Python) roda igual, só trocando a fonte de dados (DAX → SQL).
"""
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))     # p/ importar o rfm.py do app
import db      # noqa: E402
import rfm     # noqa: E402  (módulo puro do app, reutilizado sem alteração)

REF = date(2026, 7, 24)
D12 = date(2025, 7, 24)     # janela de 12 meses

SEG_LABEL = {
    "champions": "Campeões", "loyal": "Fiéis", "cant_lose": "Não posso perder",
    "at_risk": "Em risco", "new": "Novos", "potential_loyalist": "Promissores",
    "lost": "Perdidos", "hibernating": "Hibernando",
}


def carregar_snapshot(cur):
    cur.execute("""
        SELECT codcli, max(dtsaida) ultima,
               count(DISTINCT numtransvenda) FILTER (WHERE dtsaida >= %s) compras12m,
               coalesce(sum(vlvenda-icmsretido-vlfecp) FILTER (WHERE dtsaida >= %s),0) venda12m,
               coalesce(sum((vlvenda-icmsretido-vlfecp)-vlcustofin) FILTER (WHERE dtsaida >= %s),0) lucro12m
        FROM faturamento_vendas WHERE codoper='S' GROUP BY codcli""", (D12, D12, D12))
    snap = []
    for codcli, ultima, compras, venda, lucro in cur.fetchall():
        snap.append({
            "CODCLI": codcli, "DiasSemComprar": (REF - ultima).days,
            "Compras12m": compras or 0, "Venda12m": float(venda), "Lucro12m": float(lucro),
            "UltimaCompra": ultima.isoformat(),
        })
    return snap


def carregar_datas(cur):
    cur.execute("""SELECT codcli, dtsaida FROM faturamento_vendas
                   WHERE codoper='S' AND dtsaida >= %s GROUP BY codcli, dtsaida""", (D12,))
    datas = {}
    for codcli, dt in cur.fetchall():
        datas.setdefault(codcli, []).append(dt)
    return datas


def carregar_meta(cur):
    cur.execute("""SELECT codcli, cliente, municent, estent, codusur1, telcelent, bloqueio
                   FROM pcclient""")
    return {r[0]: {"cliente": r[1], "cidade": r[2], "uf": r[3], "codusur1": r[4],
                   "telefone": r[5], "bloqueio": r[6]} for r in cur.fetchall()}


if __name__ == "__main__":
    with db.conn() as c:
        cur = c.cursor()
        snapshot = carregar_snapshot(cur)
        datas = carregar_datas(cur)
        meta = carregar_meta(cur)

    clientes = rfm.calcular_clientes(snapshot, datas, meta)   # <-- rfm.py do app, intacto
    dist = rfm.agregar_distribuicoes(clientes)

    # agrega receita + positivação por segmento
    por_seg = {}
    for cl in clientes:
        s = por_seg.setdefault(cl["segmento"], {"n": 0, "receita": 0.0, "positiv": 0})
        s["n"] += 1
        s["receita"] += cl["venda_12m"]
        if cl["frequencia_12m"] >= 1:
            s["positiv"] += 1

    total = len(clientes)
    print(f"========== CARTEIRA RFM (joga_demo via SQL → rfm.py do app) ==========")
    print(f"  {total:,} clientes na base\n")
    print(f"  {'segmento':20} {'clientes':>9} {'% base':>7} {'receita 12m':>16} {'positiv.':>9}")
    for seg in rfm.SEGMENTOS_ORDEM:
        s = por_seg.get(seg, {"n": 0, "receita": 0.0, "positiv": 0})
        pct = s["n"] / total * 100 if total else 0
        print(f"  {SEG_LABEL[seg]:20} {s['n']:>9,} {pct:>6.1f}% "
              f"R$ {s['receita']:>13,.0f} {s['positiv']:>9,}")

    print("\n  --- Régua de status (personalizada) ---")
    for st in rfm.STATUS_ORDEM:
        print(f"  {st:10} {dist['regua'][st]:>6,}")
    print(f"  % ok+normal (em dia): {dist['regua']['pct_ok_normal']*100:.1f}%")

    # matriz R×F (amostra de células p/ conferir spread)
    matriz = rfm.matriz_rf(clientes)
    print(f"\n  --- Matriz R×F: {len(matriz)} células preenchidas (de 25 possíveis) ---")
