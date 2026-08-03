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

# Aba Próximo Pedido — incluir a "Lista do Dia" (clientes a contatar + top produtos) no email
cur.execute("ALTER TABLE multpel_users ADD COLUMN IF NOT EXISTS email_proximo_pedido BOOLEAN DEFAULT false;")

# Página Gerencial (Cobertura) — opt-in do alerta de baixa performance de cobertura por email
cur.execute("ALTER TABLE multpel_users ADD COLUMN IF NOT EXISTS email_alerta_cobertura BOOLEAN DEFAULT false;")

# ── Fusão Comercial + Compras ──
# `areas` = o que a PESSOA acessa; o que a EMPRESA comprou vem da env MODULOS. O acesso
# efetivo é a interseção dos dois. DEFAULT ["comercial"] é deliberado: numa base existente
# ninguém ganha acesso ao Compras por acidente — o admin libera um a um.
cur.execute("""ALTER TABLE multpel_users ADD COLUMN IF NOT EXISTS areas JSONB
               DEFAULT '["comercial"]'::jsonb;""")
# Destino pós-login p/ quem tem as 2 áreas: 'portal' (escolhe toda vez) | 'comercial' | 'compras'.
# É o "fixar" da tela de portal.
cur.execute("ALTER TABLE multpel_users ADD COLUMN IF NOT EXISTS area_padrao VARCHAR(20) DEFAULT 'portal';")
# Comprador vinculado (PCEMPR.MATRICULA). Filtro DEFAULT do módulo Compras, não trava:
# o usuário pode trocar e ver os outros compradores.
cur.execute("ALTER TABLE multpel_users ADD COLUMN IF NOT EXISTS codcomprador INTEGER;")
# Quais relatórios de compras o usuário recebe por email (lista de views: ["reposicao",...]).
cur.execute("""ALTER TABLE multpel_users ADD COLUMN IF NOT EXISTS relatorios_estoque JSONB
               DEFAULT '[]'::jsonb;""")
# Preferência de tema: 'escuro' (padrão — ninguém é surpreendido) | 'claro'.
cur.execute("ALTER TABLE multpel_users ADD COLUMN IF NOT EXISTS tema VARCHAR(10) DEFAULT 'escuro';")

# ── Proteção contra força bruta no login ──
# Fonte de verdade no Postgres (e não só no Redis) de propósito: é o controle de segurança
# principal e precisa sobreviver a queda/restart do cache. Falha de login é evento raro,
# então o custo de escrita é irrelevante.
cur.execute("ALTER TABLE multpel_users ADD COLUMN IF NOT EXISTS tentativas_falhas INTEGER DEFAULT 0;")
cur.execute("ALTER TABLE multpel_users ADD COLUMN IF NOT EXISTS bloqueado_ate TIMESTAMP;")
# Quantos bloqueios a conta já sofreu — é o que faz o castigo escalonar (15min → 1h → 4h)
# em vez de o atacante poder tentar 5 senhas a cada 15 minutos, para sempre.
cur.execute("ALTER TABLE multpel_users ADD COLUMN IF NOT EXISTS bloqueios_seguidos INTEGER DEFAULT 0;")

# (as migrations de multpel_log ficam logo APÓS o CREATE dela, mais abaixo — em banco novo
#  a tabela ainda não existe neste ponto)

# Config global chave/valor (ex.: limiar de cobertura editável no Admin, sem redeploy)
cur.execute("""
    CREATE TABLE IF NOT EXISTS multpel_config (
        chave         TEXT PRIMARY KEY,
        valor         TEXT,
        atualizado_em TIMESTAMP DEFAULT NOW()
    );
""")
# Seeds idempotentes (não sobrescreve valor já ajustado pelo diretor)
cur.execute("INSERT INTO multpel_config (chave, valor) VALUES ('cobertura_limiar_pct', '60') ON CONFLICT (chave) DO NOTHING;")
cur.execute("INSERT INTO multpel_config (chave, valor) VALUES ('cobertura_coberto_dias', '30') ON CONFLICT (chave) DO NOTHING;")
# Limiares do bloqueio de login — ajustáveis sem redeploy, mesmo padrão da cobertura
cur.execute("INSERT INTO multpel_config (chave, valor) VALUES ('login_max_tentativas', '5') ON CONFLICT (chave) DO NOTHING;")
cur.execute("INSERT INTO multpel_config (chave, valor) VALUES ('login_bloqueio_min', '15') ON CONFLICT (chave) DO NOTHING;")
# 50 e não 20: um escritório inteiro costuma sair por um único IP (NAT). Força bruta faz
# centenas/milhares de tentativas, então 50 ainda pega o ataque sem punir quem só digitou errado.
cur.execute("INSERT INTO multpel_config (chave, valor) VALUES ('login_max_por_ip', '50') ON CONFLICT (chave) DO NOTHING;")

# Módulo Metas — meta (alvo) por vendedor/mês. Nosso app é dono da meta (input + sugestão).
# Realizado/projeção vêm do dataset META; aqui só guardamos o alvo digitado.
cur.execute("""
    CREATE TABLE IF NOT EXISTS multpel_metas (
        id                 SERIAL PRIMARY KEY,
        ano                INTEGER NOT NULL,
        mes                INTEGER NOT NULL,
        codusur            INTEGER NOT NULL,
        valor_meta         NUMERIC(14,2) DEFAULT 0,
        clientes_meta      INTEGER       DEFAULT 0,
        mix_meta           INTEGER       DEFAULT 0,
        rentabilidade_meta NUMERIC(14,2) DEFAULT 0,
        atualizado_em      TIMESTAMP     DEFAULT NOW(),
        atualizado_por     INTEGER,
        UNIQUE (ano, mes, codusur)
    );
""")
cur.execute("CREATE INDEX IF NOT EXISTS ix_metas_anomes ON multpel_metas(ano, mes);")

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

# ── Migrations de multpel_log ──
# Ficam AQUI, logo após o CREATE, e não junto das colunas de multpel_users lá em cima: num
# banco novo a tabela ainda não existe naquele ponto e o init_db quebrava com
# "relation multpel_log does not exist". Em base existente passava despercebido.

# Rastro de tentativas de login (com IP) — sem isso não há como constatar um ataque depois.
cur.execute("ALTER TABLE multpel_log ADD COLUMN IF NOT EXISTS ip VARCHAR(45);")

# A FK do log era RESTRICT (padrão): com o log de login gravando por usuário, apagar um
# usuário de fato passou a ser barrado pelas linhas de auditoria. Tabela de auditoria não pode
# impedir a exclusão — vira SET NULL: o evento e o e-mail (em `parametros`) permanecem, só o
# vínculo com a linha de usuário se desfaz.
cur.execute("ALTER TABLE multpel_log DROP CONSTRAINT IF EXISTS multpel_log_usuario_id_fkey;")
cur.execute("""ALTER TABLE multpel_log ADD CONSTRAINT multpel_log_usuario_id_fkey
               FOREIGN KEY (usuario_id) REFERENCES multpel_users(id) ON DELETE SET NULL;""")

# ── Tabelas do módulo Compras (estoque_*) ──
# O DDL vive no próprio módulo (estoque/store.py) e é importado aqui — fonte de verdade única.
# Antes ele rodava no import do app; migration é o lugar certo. O store.ensure() em runtime
# continua servindo de rede de segurança se o Postgres subir depois do app.
from estoque.store import DDL as ESTOQUE_DDL
cur.execute(ESTOQUE_DDL)
print("[OK] Tabelas estoque_* criadas/atualizadas.")

# Admin semeado. Email e senha são ENV porque a instância de DEMO não deve exibir o domínio do
# cliente na tela de login nem carregar uma senha em texto num repo PÚBLICO. Defaults preservados
# para não criar um segundo admin em produção, onde este usuário já existe e já trocou a senha.
admin_email = os.getenv('ADMIN_EMAIL', 'admin@multpel.com.br')
admin_senha = os.getenv('ADMIN_SENHA', 'admin123')
cur.execute("SELECT id FROM multpel_users WHERE email = %s", (admin_email,))
if not cur.fetchone():
    cur.execute(
        """INSERT INTO multpel_users (nome, email, password_hash, role, must_change_password)
           VALUES (%s, %s, %s, 'admin', true)""",
        ('Administrador', admin_email, generate_password_hash(admin_senha))
    )
    print(f"[OK] Admin criado: {admin_email} (senha via ADMIN_SENHA) -- TROCAR no primeiro login!")
else:
    print(f"[OK] Admin {admin_email} ja existe.")

conn.commit()
cur.close()
conn.close()
print("[OK] Banco pronto. Rode: python -X utf8 server.py")
