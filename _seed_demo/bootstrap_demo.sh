#!/usr/bin/env bash
# Bootstrap idempotente da instância DEMO — roda 1x no primeiro deploy (serviço demo-seed).
# Monta a base sintética + auth/metas no MESMO banco `joga_demo` (as tabelas Winthor e as
# multpel_*/estoque_* não colidem), libera o admin e limpa o cache. Reexecuções pulam se já
# estiver populado. Tudo que ele usa já vem na imagem (psycopg2, dotenv, geradores).
set -eu
cd /app

echo "[bootstrap] aguardando Postgres em ${DB_HOST}..."
python -X utf8 - <<'PY'
import os, time, psycopg2
p = dict(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT", "5432"),
         dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"))
for _ in range(90):
    try:
        psycopg2.connect(**p).close(); print("  Postgres UP"); break
    except Exception:
        time.sleep(2)
else:
    raise SystemExit("Postgres nao subiu a tempo")
PY

JA=$(python -X utf8 - <<'PY'
import os, psycopg2
n = 0
try:
    c = psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT", "5432"),
                         dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"))
    cur = c.cursor()
    cur.execute("SELECT to_regclass('public.faturamento_vendas')")
    if cur.fetchone()[0] is not None:
        cur.execute("SELECT count(*) FROM faturamento_vendas")
        n = cur.fetchone()[0]
except Exception:
    n = 0
print(n)
PY
)
if [ "${JA:-0}" -gt 1000 ]; then
    echo "[bootstrap] joga_demo ja populado (${JA} linhas) — nada a fazer."
    exit 0
fi

echo "[bootstrap] 1/6 schema (joga_demo)..."   && python -X utf8 _seed_demo/setup_db.py
echo "[bootstrap] 2/6 dimensoes..."            && python -X utf8 _seed_demo/gerar.py
echo "[bootstrap] 3/6 fato de vendas (~1,17M)..." && python -X utf8 _seed_demo/gerar_fato.py
echo "[bootstrap] 4/6 estoque..."              && python -X utf8 _seed_demo/gerar_estoque.py
echo "[bootstrap] 5/6 auth/config (init_db)..." && python -X utf8 init_db.py
echo "[bootstrap] 6/6 metas + admin..."
DEMO_SEED=1 python -X utf8 _seed_demo/seed_metas_demo.py
python -X utf8 - <<'PY'
import os, json, psycopg2
c = psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT", "5432"),
                     dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"))
cur = c.cursor()
cur.execute("UPDATE multpel_users SET areas=%s::jsonb, must_change_password=false WHERE email=%s",
            (json.dumps(["comercial", "compras"]), os.getenv("ADMIN_EMAIL", "admin@multpel.com.br")))
c.commit(); print("  admin liberado (comercial+compras)")
PY

# limpa o cache (o prewarm do app pode ter cacheado vazio antes do seed terminar)
python -X utf8 - <<'PY'
import os
try:
    import redis
    redis.Redis(host=os.getenv("REDIS_HOST", "demo-redis"), port=int(os.getenv("REDIS_PORT", "6379"))).flushall()
    print("  redis limpo")
except Exception as e:
    print("  (redis flush pulado:", e, ")")
PY
# não imprime a senha: log de container é lido por quem tem acesso ao Portainer e vira print
echo "[bootstrap] DEMO PRONTA. Acesso: ${ADMIN_EMAIL:-admin@multpel.com.br} (senha = ADMIN_SENHA da stack)"
