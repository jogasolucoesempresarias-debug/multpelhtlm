"""Seeder das TRATATIVAS da DEMO — enche a página Tratativas (e o selo do plano nas listas).

Por que existe: na instância do cliente as tratativas nascem do uso (o vendedor registra no plano do
cliente). Na DEMO ninguém registra, e a página do líder abriria vazia justamente na apresentação.

O que gera (~8 semanas até o "hoje" da demo, que é o do DADO — `max(dtsaida)`): o time do usuário
`supervisor@` (o mesmo que o `seed_usuarios_demo.py` escolhe) e mais alguns vendedores de outros
times, cada um com um PERFIL de uso, para a página contar a história que existe para contar:
    · constante (todo dia útil, poucas por dia)   · forte no começo e caindo   · lança em lote
    · parou depois de duas semanas                · moderados
Os status saem COERENTES com as vendas sintéticas: "pedido feito" logo antes de uma compra que
aconteceu, "não compra mais" em cliente que não voltou, retornos cumpridos e esquecidos,
transferências assumidas por outra pessoa e outras sem dono. A conversão da página é calculada pelo
app sobre essas vendas — nada aqui é número pronto.

O `vendedor@` da demo registra na própria carteira e o `supervisor@` assume parte das transferências,
então cada login de demonstração vê o seu escopo.

⚠️ A demo ANDA SOZINHA (`avancar_demo.py`): `cliente_plano` está na lista dele, senão as tratativas
ficariam para trás das vendas e a página esvaziaria em poucas semanas.

────────────────────────────────────────────────────────────────────────────────────────────────────
⚠️  TRAVA DE SEGURANÇA (mesma política do seed_usuarios_demo / avancar_demo):
    1) exige  DEMO_SEED=1;   2) RECUSA o auth de produção (multpel_db, painel_db);
    3) RECUSA banco sem "demo" no nome;   4) com tratativas já existentes, só roda com --refazer
       (que APAGA todas as tratativas do banco — é a demo).
────────────────────────────────────────────────────────────────────────────────────────────────────
Uso:  DEMO_SEED=1 DB_NAME=joga_demo python -X utf8 _seed_demo/seed_tratativas_demo.py [--refazer]
"""
import argparse
import os
import random
import sys
from collections import defaultdict
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

try:
    import psycopg2 as _pg
except ImportError:
    import psycopg as _pg

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
load_dotenv(HERE.parent / ".env")

import provider_sql  # noqa: E402

BRT = ZoneInfo("America/Sao_Paulo")
SEED = 42
SEMANAS = 8
PROIBIDOS = {"multpel_db", "painel_db", "postgres"}
DOMINIO = "jogasolucoes.com.br"

TEXTOS = {
    "ligacao_feita": ["Falou com o comprador, vai olhar o estoque e retorna",
                      "Mandei a tabela nova pelo WhatsApp", "Estoque alto, volta a comprar mês que vem",
                      "Ofereci o mix de limpeza, gostou do preço", "Conversei com o dono, pedido deve sair na semana",
                      "Pediu prazo maior para fechar"],
    "sem_contato": ["Não atendeu", "Telefone só chama", "Caixa postal, deixei mensagem no WhatsApp"],
    "retorno_agendado": ["Pediu para ligar depois do dia 10", "Comprador de férias, retornar",
                         "Vai fechar o balanço e me retorna"],
    "pedido_prometido": ["Prometeu pedido para sexta", "Manda o pedido depois da reunião",
                         "Fecha quando receber o pagamento"],
    "pedido_feito": ["Pedido digitado", "Pedido fechado por telefone", "Pedido com itens novos do mix"],
    "nao_compra_mais": ["Fechou a loja", "Passou a comprar direto da indústria", "Mudou de ramo"],
    "transferir": ["Mudou para outra região", "Pediu atendimento pelo televendas", "Fica melhor com o vendedor da rota"],
}

# perfis de uso: (dias por semana nas semanas iniciais, por semana nas finais, registros por dia)
PERFIS = {
    "constante": (5, 5, (3, 6)),
    "caindo": (5, 1, (5, 9)),
    "lote": (1, 1, (18, 28)),
    "parou": (4, 0, (3, 6)),
    "moderado": (3, 2, (2, 5)),
}
ORDEM_PERFIS = ["constante", "caindo", "lote", "moderado", "parou", "moderado", "constante", "moderado", "caindo"]


_MIGRACAO = """DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'cliente_plano' AND column_name = 'criado_em'
                  AND data_type = 'timestamp without time zone') THEN
        ALTER TABLE cliente_plano
            ALTER COLUMN criado_em TYPE TIMESTAMPTZ
            USING criado_em AT TIME ZONE current_setting('TimeZone');
    END IF;
END $$;
ALTER TABLE cliente_plano ADD COLUMN IF NOT EXISTS excluido_em TIMESTAMPTZ;
ALTER TABLE cliente_plano ADD COLUMN IF NOT EXISTS excluido_por INTEGER;"""


def guardas(alvo, env=None):
    env = os.environ if env is None else env
    if env.get("DEMO_SEED") != "1":
        return "ABORTADO: defina DEMO_SEED=1 para confirmar que quer semear tratativas SINTÉTICAS."
    if (alvo or "") in PROIBIDOS:
        return f"ABORTADO: DB_NAME='{alvo}' é banco de produção."
    if "demo" not in (alvo or "").lower():
        return f"ABORTADO: DB_NAME='{alvo}' não tem 'demo' no nome — este script só roda na demonstração."
    return None


def _auth_conn():
    return _pg.connect(host=os.getenv("DB_HOST", "localhost"), port=os.getenv("DB_PORT", "5432"),
                       dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER", "postgres"),
                       password=os.getenv("DB_PASSWORD", ""))


def _base(hoje):
    """Vendedores escolhidos, carteira de cada um e as datas de compra dos clientes."""
    from seed_usuarios_demo import _escolher_perfis
    sup, _nome_sup, cu_demo, _nome_cu = _escolher_perfis()
    c = provider_sql.analytics_conn()
    try:
        cur = c.cursor()
        cur.execute("""SELECT u.codusur, u.nome, u.codsupervisor, count(cl.codcli) n
                         FROM pcusuari u JOIN pcclient cl ON cl.codusur1 = u.codusur
                        WHERE coalesce(u.bloqueio, 'N') <> 'S'
                        GROUP BY 1, 2, 3 HAVING count(cl.codcli) >= 25 ORDER BY n DESC""")
        todos = cur.fetchall()
        do_time = [r for r in todos if r[2] == sup][:6]
        if not any(r[0] == cu_demo for r in do_time):
            do_time = [r for r in todos if r[0] == cu_demo] + do_time[:5]
        outros = [r for r in todos if r[2] != sup][:3]
        vend = do_time + outros
        cods = [r[0] for r in vend]
        cur.execute("SELECT codcli, codusur1 FROM pcclient WHERE codusur1 = ANY(%s)", (cods,))
        carteira = defaultdict(list)
        for cc, u in cur.fetchall():
            carteira[u].append(cc)
        clis = [cc for xs in carteira.values() for cc in xs]
        cur.execute("""SELECT codcli, dtsaida FROM faturamento_vendas
                        WHERE codcli = ANY(%s) AND dtsaida >= %s GROUP BY 1, 2""",
                    (clis, hoje - timedelta(days=300)))
        compras = defaultdict(list)
        for cc, d in cur.fetchall():
            compras[cc].append(d)
        for xs in compras.values():
            xs.sort()
        return sup, cu_demo, vend, carteira, compras
    finally:
        c.close()


def _situacao(compras, cc, d):
    """(dias sem compra no dia d, dias até a próxima compra a partir de d ou None)."""
    xs = compras.get(cc, [])
    antes = [x for x in xs if x < d]
    depois = [x for x in xs if x >= d]
    return ((d - antes[-1]).days if antes else None), ((depois[0] - d).days if depois else None)


def _status(rng, sem, prox, d, hoje):
    """Status coerente com o que de fato aconteceu depois (as vendas sintéticas)."""
    visivel = (hoje - d).days        # quanto do futuro do registro já é conhecido
    if prox is not None and prox <= 3:
        return rng.choices(["pedido_feito", "pedido_prometido", "ligacao_feita"], [5, 2, 3])[0]
    if prox is not None and prox <= 15:
        return rng.choices(["ligacao_feita", "pedido_prometido", "retorno_agendado"], [6, 2, 2])[0]
    if visivel < 15:                 # futuro ainda aberto: não dá para dizer que não comprou
        return rng.choices(["ligacao_feita", "retorno_agendado", "sem_contato"], [6, 2, 2])[0]
    if sem is None or sem > 90:
        return rng.choices(["ligacao_feita", "sem_contato", "nao_compra_mais", "transferir"], [5, 3, 2, 1])[0]
    return rng.choices(["ligacao_feita", "sem_contato", "retorno_agendado", "transferir"], [6, 3, 2, 0.5])[0]


# Peso de cada cliente no sorteio do dia, pela situação: a lista do dia (Próximo Pedido) puxa quem
# está no ciclo ou começando a atrasar; o parado há meses aparece, mas menos. Sem isto a demo, que tem
# muito cliente inativo, punha metade das tratativas em cliente de 90+ dias.
def _peso(sem):
    if sem is None:
        return 0.4
    if sem <= 60:
        return 3.0
    if sem <= 90:
        return 2.5
    return 1.2


def _sortear(rng, compras, cart, d, k):
    """k clientes sem repetição, ponderados pela situação no dia (Efraimidis–Spirakis)."""
    chave = {cc: rng.random() ** (1.0 / _peso(_situacao(compras, cc, d)[0])) for cc in cart}
    return sorted(cart, key=chave.get, reverse=True)[:k]


def _hora(rng, d, base=None):
    h = base or time(rng.randint(8, 17), rng.randint(0, 59), rng.randint(0, 59))
    return datetime.combine(d, h, tzinfo=BRT)


def gerar(hoje, sup, cu_demo, vend, carteira, compras, ids_demo, rng):
    """[(codcli, data_acao, status, descricao, autor_id, autor_nome, criado_em, excluido_em, excluido_por)]."""
    inicio = hoje - timedelta(weeks=SEMANAS)
    regs = []
    autores = []
    for i, (cu, nome, _sup, _n) in enumerate(vend):
        aid, anome = ids_demo.get(("vendedor", cu)) or (900000 + int(cu), str(nome).title())
        autores.append((cu, aid, anome, ORDEM_PERFIS[i % len(ORDEM_PERFIS)]))
    for cu, aid, anome, perfil in autores:
        dias_ini, dias_fim, (qmin, qmax) = PERFIS[perfil]
        cart = carteira.get(cu) or []
        if not cart:
            continue
        for sem in range(SEMANAS):
            seg = inicio + timedelta(weeks=sem)
            n_dias = dias_ini if sem < SEMANAS // 2 else dias_fim
            if perfil == "parou" and sem >= 2:
                n_dias = 0
            uteis = [seg + timedelta(days=k) for k in range(5) if seg + timedelta(days=k) <= hoje]
            for d in sorted(rng.sample(uteis, min(n_dias, len(uteis)))):
                lote = time(rng.randint(16, 18), rng.randint(0, 59)) if perfil == "lote" else None
                for cc in _sortear(rng, compras, cart, d, min(rng.randint(qmin, qmax), len(cart))):
                    sem_c, prox = _situacao(compras, cc, d)
                    st = _status(rng, sem_c, prox, d, hoje)
                    quando = _hora(rng, d, (lote.replace(second=rng.randint(0, 59)) if lote else None))
                    acao = d
                    if st == "retorno_agendado":
                        acao = d + timedelta(days=rng.randint(3, 7))
                    desc = rng.choice(TEXTOS[st]) if rng.random() < 0.6 else None
                    exc = quando + timedelta(minutes=2) if rng.random() < 0.03 else None
                    regs.append([cc, acao, st, desc, aid, anome, quando, exc, aid if exc else None])
    # seguimentos: parte dos retornos é cumprida no dia marcado; parte das transferências é assumida
    sup_id, sup_nome = ids_demo.get(("supervisor", sup)) or (None, None)
    extra = []
    for r in regs:
        cc, acao, st, _desc, aid, anome, quando, exc, _ = r
        if exc:
            continue
        if st == "retorno_agendado" and acao < hoje and rng.random() < 0.6:
            extra.append([cc, acao, "ligacao_feita", "Retorno feito como combinado", aid, anome,
                          _hora(rng, acao), None, None])
        if st == "transferir" and rng.random() < 0.45:
            outros = [x for x in autores if x[1] != aid]
            if sup_id and rng.random() < 0.5:
                quem = (sup_id, sup_nome)                 # o supervisor assume
            elif outros:
                o = rng.choice(outros)
                quem = (o[1], o[2])                       # outro vendedor assume
            else:
                quem = (None, None)
            dia = quando.date() + timedelta(days=rng.randint(1, 3))
            if quem[0] and dia <= hoje:
                extra.append([cc, dia, "ligacao_feita", "Assumi o cliente transferido", quem[0], quem[1],
                              _hora(rng, dia), None, None])
    return regs + extra


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refazer", action="store_true", help="apaga TODAS as tratativas do banco antes")
    a = ap.parse_args()
    erro = guardas(os.getenv("DB_NAME"))
    if erro:
        sys.exit(erro)
    hoje = provider_sql.hoje_analitico()
    conn = _auth_conn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.cliente_plano') IS NOT NULL")
            if not cur.fetchone()[0]:
                sys.exit("[trat-demo] tabela cliente_plano não existe — rode o init_db.py antes.")
            # a MESMA migração do init_db.py / `_PLANO_DDL` do app: a tabela pode ter nascido antes
            # da exclusão lógica (10/2026) e nunca ter sido aberta pelo app nesta demo
            cur.execute(_MIGRACAO)
            cur.execute("SELECT count(*) FROM cliente_plano")
            ja = cur.fetchone()[0]
            if ja and not a.refazer:
                print(f"[trat-demo] já há {ja} tratativas — nada feito (use --refazer para regerar).")
                return
            if ja:
                cur.execute("DELETE FROM cliente_plano")
            cur.execute("SELECT email, id, nome FROM multpel_users WHERE email = ANY(%s)",
                        ([f"vendedor@{DOMINIO}", f"supervisor@{DOMINIO}"],))
            usuarios = {e: (i, n) for e, i, n in cur.fetchall()}
            sup, cu_demo, vend, carteira, compras = _base(hoje)
            ids = {}
            if f"vendedor@{DOMINIO}" in usuarios:
                ids[("vendedor", cu_demo)] = usuarios[f"vendedor@{DOMINIO}"]
            if f"supervisor@{DOMINIO}" in usuarios:
                ids[("supervisor", sup)] = usuarios[f"supervisor@{DOMINIO}"]
            regs = gerar(hoje, sup, cu_demo, vend, carteira, compras, ids, random.Random(SEED))
            cur.executemany("""INSERT INTO cliente_plano (codcli, data_acao, status, descricao, autor_id,
                                   autor_nome, criado_em, excluido_em, excluido_por)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""", regs)
        st = defaultdict(int)
        for r in regs:
            st[r[2]] += 1
        print(f"[trat-demo] hoje da demo {hoje} · {len(regs)} tratativas de {len({r[4] for r in regs})} pessoas "
              f"em {len({r[0] for r in regs})} clientes · " + ", ".join(f"{k} {v}" for k, v in sorted(st.items())))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
