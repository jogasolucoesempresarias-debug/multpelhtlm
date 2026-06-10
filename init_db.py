"""Inicializa banco multpel_db. Rode 1x: python -X utf8 init_db.py
Idempotente: pode rodar quantas vezes quiser sem efeito colateral."""
import os
import psycopg2
from werkzeug.security import generate_password_hash
from dotenv import load_dotenv

load_dotenv()

conn = psycopg2.connect(
    host=os.getenv('DB_HOST', 'localhost'),
    port=os.getenv('DB_PORT', '5432'),
    dbname=os.getenv('DB_NAME', 'multpel_db'),
    user=os.getenv('DB_USER', 'postgres'),
    password=os.getenv('DB_PASSWORD', ''),
)
cur = conn.cursor()

cur.execute("""
    CREATE TABLE IF NOT EXISTS multpel_users (
        id              SERIAL PRIMARY KEY,
        nome            VARCHAR(255) NOT NULL,
        email           VARCHAR(255) UNIQUE NOT NULL,
        password_hash   VARCHAR(255) NOT NULL,
        role            VARCHAR(20) DEFAULT 'viewer',
        ativo           BOOLEAN DEFAULT true,
        must_change_password BOOLEAN DEFAULT true,
        codusur         INTEGER,
        codsupervisor   INTEGER,
        criado_em       TIMESTAMP DEFAULT NOW()
    );
""")
cur.execute("CREATE INDEX IF NOT EXISTS ix_users_email ON multpel_users(email);")

# Migrations Onda F — campos pra envio de email/cron
cur.execute("ALTER TABLE multpel_users ADD COLUMN IF NOT EXISTS telefone        VARCHAR(30);")
cur.execute("ALTER TABLE multpel_users ADD COLUMN IF NOT EXISTS cron_enabled    BOOLEAN DEFAULT false;")
cur.execute("ALTER TABLE multpel_users ADD COLUMN IF NOT EXISTS cron_horario    TIME DEFAULT '08:00';")
cur.execute("ALTER TABLE multpel_users ADD COLUMN IF NOT EXISTS cron_frequencia VARCHAR(15) DEFAULT 'diaria';")

# Patch J — destinatários extras (CC) pro envio de relatório
cur.execute("ALTER TABLE multpel_users ADD COLUMN IF NOT EXISTS email_cc        JSONB DEFAULT '[]'::jsonb;")

# Patch K — filtro de segmento RFM (comma-separated: 'champions,loyal,lost' ou '' = carteira completa)
cur.execute("ALTER TABLE multpel_users ADD COLUMN IF NOT EXISTS segmentos_rfm   TEXT DEFAULT '';")

# Supervisor multi-área — lista de codsupervisores (JSONB array). Coluna legada codsupervisor
# segue existindo e recebe o 1º elemento (compatibilidade com RBAC single).
cur.execute("ALTER TABLE multpel_users ADD COLUMN IF NOT EXISTS codsupervisores JSONB DEFAULT '[]'::jsonb;")

cur.execute("""
    CREATE TABLE IF NOT EXISTS multpel_log (
        id            SERIAL PRIMARY KEY,
        usuario_id    INTEGER REFERENCES multpel_users(id),
        rota          VARCHAR(120),
        parametros    TEXT,
        duracao_ms    INTEGER,
        erro          TEXT,
        acessado_em   TIMESTAMP DEFAULT NOW()
    );
""")

admin_email = 'admin@multpel.com.br'
cur.execute("SELECT id FROM multpel_users WHERE email = %s", (admin_email,))
if not cur.fetchone():
    cur.execute(
        """INSERT INTO multpel_users (nome, email, password_hash, role, must_change_password)
           VALUES (%s, %s, %s, 'admin', true)""",
        ('Administrador', admin_email, generate_password_hash('admin123'))
    )
    print(f"[OK] Admin criado: {admin_email} / admin123 -- TROCAR no primeiro login!")
else:
    print(f"[OK] Admin {admin_email} ja existe.")

conn.commit()
cur.close()
conn.close()
print("[OK] Banco pronto. Rode: python -X utf8 server.py")
