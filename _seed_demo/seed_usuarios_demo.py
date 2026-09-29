"""Usuários de demonstração por PERFIL + metas do mês — para mostrar o Agente de IA do Comercial
(e o painel inteiro) como diretor, supervisor e vendedor na instância DEMO.

Por que existe: a demo só tinha o ADMIN, e o agente responde no ESCOPO de quem pergunta — sem um
supervisor e um vendedor de verdade não há como mostrar que o supervisor só enxerga o time dele
(docs/comercial/IA_COMERCIAL_CONTEUDO.md §0, Fase 1 = gestão).

Cria/atualiza (idempotente, upsert por e-mail):
  · diretor@jogasolucoes.com.br     role=viewer  → empresa inteira, SEM /admin (um prospect com o
                                                    login da apresentação não vê a gestão de usuários)
  · supervisor@jogasolucoes.com.br  role=supervisor → o time com mais vendedores (e carteira) da base
  · vendedor@jogasolucoes.com.br    role=vendedor   → o RCA com maior carteira desse time
Senha: env `DEMO_USUARIOS_SENHA` (vem do ENV DA STACK — este repo é público; nunca literal aqui).

E garante METAS do mês fechado e do corrente (`--so-metas` faz só isto). ⚠️ A demo anda sozinha
(`avancar_demo.py`, todo dia) e o `seed_metas_demo.py` só semeia o mês do bootstrap: na virada do
mês a demo ficava "sem meta" — Metas vazias e a nota da Performance parcial para todos. Aqui a
meta de cada vendedor = realizado do MÊS ANTERIOR ao alvo × um fator próprio (0,90–1,30, fixo por
código): tem quem bate e quem não bate, que é o que a gestão quer ver. Só semeia mês que está
SEM nenhuma meta — nunca sobrescreve meta que alguém editou no Admin.

────────────────────────────────────────────────────────────────────────────────────────────────────
⚠️  TRAVA DE SEGURANÇA (mesma política do seed_metas_demo / avancar_demo):
    1) exige  DEMO_SEED=1;
    2) RECUSA o auth de produção da Multpel (DB_NAME == 'multpel_db');
    3) RECUSA banco sem "demo" no nome.
────────────────────────────────────────────────────────────────────────────────────────────────────

Uso:  DEMO_SEED=1 DEMO_USUARIOS_SENHA=... DB_NAME=joga_demo python -X utf8 _seed_demo/seed_usuarios_demo.py
      DEMO_SEED=1 DB_NAME=joga_demo python -X utf8 _seed_demo/seed_usuarios_demo.py --so-metas
"""
import argparse
import json
import os
import sys
import zlib
from pathlib import Path

from dotenv import load_dotenv

try:
    import psycopg2 as _pg
except ImportError:
    import psycopg as _pg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
import provider_sql  # noqa: E402  (lê o joga_demo analítico)

PROD_AUTH_DB = "multpel_db"
DOMINIO = "jogasolucoes.com.br"


def guardas(alvo, env=None):
    """Mensagem de recusa, ou None se pode rodar. Separada para ser testável sem banco."""
    env = os.environ if env is None else env
    if env.get("DEMO_SEED") != "1":
        return "ABORTADO: defina DEMO_SEED=1 para confirmar que quer semear usuários/metas SINTÉTICOS."
    if (alvo or "") == PROD_AUTH_DB:
        return f"ABORTADO: DB_NAME='{alvo}' é a AUTH de PRODUÇÃO da Multpel."
    if "demo" not in (alvo or "").lower():
        return f"ABORTADO: DB_NAME='{alvo}' não tem 'demo' no nome — este script só roda na instância de demonstração."
    return None


def fator_meta(codusur):
    """0,90–1,30, fixo por código (reprodutível; não depende de random/seed global)."""
    return 0.90 + (zlib.crc32(str(int(codusur)).encode()) % 41) / 100.0


def _auth_conn():
    return _pg.connect(host=os.getenv("DB_HOST", "localhost"), port=os.getenv("DB_PORT", "5432"),
                       dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER", "postgres"),
                       password=os.getenv("DB_PASSWORD", ""))


def _escolher_perfis():
    """(codsupervisor, nome_time, codusur, nome_vendedor) — o time com mais vendedores ativos (e
    carteira) e, nele, o RCA com a maior carteira. Medido na base, não cravado: se o gerador
    mudar, a escolha acompanha."""
    c = provider_sql.analytics_conn()
    try:
        cur = c.cursor()
        cur.execute("""
            SELECT s.codsupervisor, s.nome, count(DISTINCT u.codusur) AS n_rca, count(DISTINCT cl.codcli) AS n_cli
              FROM pcsuperv s
              JOIN pcusuari u ON u.codsupervisor = s.codsupervisor AND coalesce(u.bloqueio, 'N') <> 'S'
              LEFT JOIN pcclient cl ON cl.codusur1 = u.codusur
             GROUP BY 1, 2 ORDER BY n_rca DESC, n_cli DESC LIMIT 1""")
        sup, nome_sup, _n, _c = cur.fetchone()
        cur.execute("""
            SELECT u.codusur, u.nome, count(cl.codcli) AS n_cli
              FROM pcusuari u LEFT JOIN pcclient cl ON cl.codusur1 = u.codusur
             WHERE u.codsupervisor = %s AND coalesce(u.bloqueio, 'N') <> 'S'
             GROUP BY 1, 2 ORDER BY n_cli DESC LIMIT 1""", (sup,))
        cu, nome_cu, _n = cur.fetchone()
        return int(sup), nome_sup, int(cu), nome_cu
    finally:
        c.close()


def semear_usuarios(conn, senha):
    from werkzeug.security import generate_password_hash
    sup, nome_sup, cu, nome_cu = _escolher_perfis()
    usuarios = [
        ("Diretoria (demo)", f"diretor@{DOMINIO}", "viewer", None, None, []),
        (f"Supervisor {nome_sup} (demo)", f"supervisor@{DOMINIO}", "supervisor", None, sup, [sup]),
        (f"{nome_cu} (demo)", f"vendedor@{DOMINIO}", "vendedor", cu, sup, []),
    ]
    h = generate_password_hash(senha)
    with conn.cursor() as cur:
        for nome, email, role, codusur, codsup, sups in usuarios:
            cur.execute("SELECT id FROM multpel_users WHERE email = %s", (email,))
            row = cur.fetchone()
            if row:
                cur.execute("""UPDATE multpel_users SET nome=%s, password_hash=%s, role=%s, ativo=true, codusur=%s,
                               codsupervisor=%s, codsupervisores=%s::jsonb, areas=%s::jsonb,
                               must_change_password=false WHERE id=%s""",
                            (nome, h, role, codusur, codsup, json.dumps(sups), json.dumps(["comercial"]), row[0]))
            else:
                cur.execute("""INSERT INTO multpel_users (nome, email, password_hash, role, ativo, codusur,
                               codsupervisor, codsupervisores, areas, must_change_password)
                               VALUES (%s,%s,%s,%s,true,%s,%s,%s::jsonb,%s::jsonb,false)""",
                            (nome, email, h, role, codusur, codsup, json.dumps(sups), json.dumps(["comercial"])))
            print(f"  {email:34s} {role:10s} codusur={codusur} time={codsup}")
    conn.commit()
    return sup, cu


def _mes_add(ano, mes, n):
    i = ano * 12 + (mes - 1) + n
    return i // 12, i % 12 + 1


def semear_metas(conn):
    """Metas do mês fechado e do corrente, só onde o mês está SEM nenhuma meta."""
    h = provider_sql.hoje_analitico()
    alvos = [_mes_add(h.year, h.month, -1), (h.year, h.month)]
    with conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS multpel_metas (
            id SERIAL PRIMARY KEY, ano INTEGER NOT NULL, mes INTEGER NOT NULL, codusur INTEGER NOT NULL,
            valor_meta NUMERIC(14,2) DEFAULT 0, clientes_meta INTEGER DEFAULT 0, mix_meta INTEGER DEFAULT 0,
            rentabilidade_meta NUMERIC(14,2) DEFAULT 0, atualizado_em TIMESTAMP DEFAULT NOW(),
            atualizado_por INTEGER, UNIQUE (ano, mes, codusur))""")
        for ano, mes in alvos:
            cur.execute("SELECT count(*) FROM multpel_metas WHERE ano=%s AND mes=%s", (ano, mes))
            if cur.fetchone()[0]:
                print(f"  metas {mes:02d}/{ano}: já existem — mantidas")
                continue
            a0, m0 = _mes_add(ano, mes, -1)
            base = provider_sql.metas_realizado(a0, m0, None)["por_vendedor"]
            linhas = []
            for k, r in base.items():
                venda = float(r.get("venda") or 0)
                if venda <= 0:
                    continue
                f = fator_meta(k)
                linhas.append((ano, mes, int(k), round(venda * f, 2), int(round((r.get("clientes") or 0) * f)),
                               int(round((r.get("mix") or 0) * f)), round(float(r.get("rentabilidade") or 0) * f, 2)))
            cur.executemany("""INSERT INTO multpel_metas (ano, mes, codusur, valor_meta, clientes_meta, mix_meta,
                               rentabilidade_meta) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (ano, mes, codusur) DO NOTHING""",
                            linhas)
            print(f"  metas {mes:02d}/{ano}: {len(linhas)} vendedores (base = realizado de {m0:02d}/{a0} × 0,90–1,30)")
    conn.commit()


def main():
    ap = argparse.ArgumentParser(description="Usuários de demonstração por perfil + metas do mês (DEMO).")
    ap.add_argument("--so-metas", action="store_true", help="só garante as metas (job diário da demo)")
    args = ap.parse_args()
    alvo = os.getenv("DB_NAME")
    erro = guardas(alvo)
    if erro:
        sys.exit(erro)
    print(f"[seed_usuarios_demo] AUTH DB alvo = '{alvo}'")
    conn = _auth_conn()
    try:
        if not args.so_metas:
            senha = (os.getenv("DEMO_USUARIOS_SENHA") or "").strip()
            if len(senha) < 8:
                print("  (usuários pulados: defina DEMO_USUARIOS_SENHA com 8+ caracteres na stack)")
            else:
                semear_usuarios(conn, senha)
        semear_metas(conn)
    finally:
        conn.close()
    print("[seed_usuarios_demo] OK")


if __name__ == "__main__":
    main()
