"""Alimentador diário da DEMO — avança a base sintética até HOJE.

**O problema que resolve.** A base da demo é uma fotografia: ela termina numa data e não envelhece.
Como o app em modo `postgres` ancora o "hoje" no `max(dtsaida)`, tudo continua coerente — mas o
painel passa a mostrar uma posição de semanas atrás. Numa apresentação comercial isso aparece: o
Orçamento fala de um mês que já passou, "vence nos próximos 10 dias" é de outro tempo, e alguém
pergunta por que a data está velha. Medido em 21/08/2026: a base terminava em **24/07**.

**Por que DESLOCAR em vez de REGERAR.** Regerar leva ~4 min e produz números novos todo dia — o
que faz a demo mudar debaixo de quem a está apresentando (quem decorou "R$ 6,1 mi de estoque"
descobre outro número na frente do cliente). Deslocar custa segundos, preserva **todos** os
números e as relações entre datas, e é o suficiente: o que envelhece na demo é o calendário, não
o conteúdo. Para trocar o conteúdo existe a regeração (`bootstrap_demo.sh`), que agora também sai
sempre atual porque o `perfil.DATA_FIM` virou `date.today()`.

**Idempotente:** se a base já termina hoje, não faz nada. Rodar duas vezes no mesmo dia é inócuo.

Uso:
    python -X utf8 _seed_demo/avancar_demo.py                # avança até hoje
    python -X utf8 _seed_demo/avancar_demo.py --dias 7       # avança 7 dias
    python -X utf8 _seed_demo/avancar_demo.py --dry-run      # só mostra o que faria
"""
import os
import sys
from datetime import date, timedelta

import psycopg2
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
load_dotenv(os.path.join(BASE, ".env"))

# ⚠️ LISTA BRANCA, não descoberta automática. Na demo publicada o `joga_demo` serve o analítico
# **e** a autenticação do app (`multpel_users`, `multpel_log`, `estoque_pedidos`…), e deslocar a
# data de criação de um usuário ou de um log de auditoria seria corromper dado de verdade para
# consertar dado de mentira. Só o que o seeder cria entra aqui.
# ⚠️ `calendario` NÃO entra aqui: `data` é chave primária, e deslocar linha a linha colide com
# as datas que já existem ("chave (data)=(2024-01-29) já existe"). Calendário é DERIVÁVEL — é
# reconstruído no fim, sobre a faixa nova do fato. Ver `reconstruir_calendario`.
TABELAS = {
    "faturamento_vendas": ["dtsaida", "dtcancel"],
    "faturamento_devolucao": ["dtent"],
    "faturamento_devolucao_avulsa": ["dtent"],
    "pcnfsaid": ["dtsaida"],
    "pcest": ["dtultsaida", "dtultent"],
    "pcestendereco": ["dtval"],
    "pcpedido": ["dtemissao", "dtentradaestoque", "dtprevent", "dtvenc"],
    "pedido_entrada": ["dtentrada"],
    "pcverba": ["dtemissao", "dtvenc", "dtcancel"],
    "pcaplicverba": ["dtaplic", "dtestorno"],
    "pcpedc": ["data"],
    "pcpedi": ["data"],
    # A série da aba Evolução mora no mesmo banco na demo e é ancorada no `max(dtsaida)`.
    # Deixá-la para trás faria o gráfico "terminar" antes do resto do painel.
    "estoque_foto_item": ["data", "dtultsaida", "dtultent"],
    "estoque_foto_log": ["data"],
    "estoque_foto_dia": ["data"],
}

# A referência do "fim" da base — é o que o app usa para ancorar o `hoje` (provider_sql.hoje_analitico)
REF_TABELA, REF_COLUNA = "faturamento_vendas", "dtsaida"


# ⚠️ TRAVA. Este script reescreve TODAS as datas do banco em que roda. Apontado por engano para
# a base de um cliente, corromperia o histórico dele de forma irreversível — e o erro é fácil:
# basta um `ANALYTICS_DB_NAME` errado no ambiente. Mesma política do `seed_metas_demo.py`, que
# recusa `multpel_db`. Só bancos com cara de demo passam.
BANCOS_PROIBIDOS = {"multpel_db", "painel_db", "postgres"}


def _checar_alvo(dbname):
    if dbname in BANCOS_PROIBIDOS:
        raise SystemExit(f"RECUSADO: '{dbname}' não é banco de demo. "
                         "Este script reescreve todas as datas — aponte para o joga_demo.")
    if "demo" not in dbname:
        raise SystemExit(f"RECUSADO: '{dbname}' não tem 'demo' no nome. "
                         "Se for demo mesmo, renomeie ou ajuste BANCOS_PROIBIDOS conscientemente.")
    return dbname


def conectar():
    _checar_alvo(os.getenv("ANALYTICS_DB_NAME", "joga_demo"))
    return psycopg2.connect(
        host=os.getenv("ANALYTICS_DB_HOST") or os.getenv("DB_HOST", "localhost"),
        port=os.getenv("ANALYTICS_DB_PORT") or os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("ANALYTICS_DB_NAME", "joga_demo"),
        user=os.getenv("ANALYTICS_DB_USER") or os.getenv("DB_USER"),
        password=os.getenv("ANALYTICS_DB_PASSWORD") or os.getenv("DB_PASSWORD"),
    )


def colunas_existentes(cur, tabela):
    """Só as colunas que a tabela REALMENTE tem.

    ⚠️ A base da demo é regerada por scripts que evoluem; uma coluna que sumiu não pode derrubar
    o alimentador inteiro e deixar a demo velha em silêncio."""
    cur.execute("""SELECT column_name FROM information_schema.columns
                    WHERE table_schema='public' AND table_name=%s""", (tabela,))
    return {r[0] for r in cur.fetchall()}


def reconstruir_calendario(cur):
    """Recria a dimensão calendário sobre a faixa ATUAL do fato.

    ⚠️ Calendário não se desloca, se deriva. `data` é PK, então um UPDATE linha a linha colide
    com as datas já existentes; e não há informação nenhuma nele que valha preservar — ano, mês e
    anomes saem da própria data. `ehdiameta` marca os dias úteis (a régua do módulo Metas)."""
    cur.execute("SELECT min(dtsaida), max(dtsaida) FROM faturamento_vendas")
    ini, fim = cur.fetchone()
    if not ini:
        return
    cur.execute("TRUNCATE calendario")
    linhas, d = [], ini
    while d <= fim:
        # domingo (weekday 6) não é dia de meta; o resto é
        linhas.append((d, d.year * 100 + d.month, d.year, d.month, "N" if d.weekday() == 6 else "S"))
        d += timedelta(days=1)
    from psycopg2.extras import execute_values
    execute_values(cur, "INSERT INTO calendario (data, anomes, ano, mes, ehdiameta) VALUES %s",
                   linhas, page_size=1000)
    print(f"  {'calendario':30s} {len(linhas):>9,} dias reconstruídos ({ini} → {fim})")


def main():
    dias_arg = None
    if "--dias" in sys.argv:
        dias_arg = int(sys.argv[sys.argv.index("--dias") + 1])
    dry = "--dry-run" in sys.argv

    conn = conectar()
    conn.autocommit = False
    cur = conn.cursor()

    cur.execute(f"SELECT max({REF_COLUNA}) FROM {REF_TABELA}")
    fim = cur.fetchone()[0]
    if fim is None:
        print("base vazia — rode o bootstrap primeiro.")
        return 1

    hoje = date.today()
    dias = dias_arg if dias_arg is not None else (hoje - fim).days
    print(f"base termina em {fim} · hoje é {hoje} · deslocamento = {dias} dia(s)")
    if dias <= 0:
        print("nada a fazer (a base já está em dia).")
        return 0
    if dry:
        print("--dry-run: nada foi alterado.")
        return 0

    total = 0
    for tabela, colunas in TABELAS.items():
        existentes = colunas_existentes(cur, tabela)
        if not existentes:
            print(f"  {tabela:30s} — não existe, pulando")
            continue
        alvo = [c for c in colunas if c in existentes]
        if not alvo:
            continue
        sets = ", ".join(f"{c} = {c} + %s" for c in alvo)
        # `WHERE ... IS NOT NULL` em qualquer uma: evita reescrever linha que não tem data
        onde = " OR ".join(f"{c} IS NOT NULL" for c in alvo)
        cur.execute(f"UPDATE {tabela} SET {sets} WHERE {onde}", [dias] * len(alvo))
        print(f"  {tabela:30s} {cur.rowcount:>9,} linhas · {', '.join(alvo)}")
        total += cur.rowcount

    conn.commit()
    print(f"\n{total:,} linhas deslocadas em {dias} dia(s).")

    # ⚠️ UPDATE em massa deixa tupla morta; rodando todo dia, a tabela do fato incha rápido.
    # O VACUUM tem de sair FORA da transação.
    conn.autocommit = True
    for tabela in ("faturamento_vendas", "pcest", "pcnfsaid"):
        try:
            cur.execute(f"VACUUM ANALYZE {tabela}")
        except Exception as e:                                    # noqa: BLE001
            print(f"  [vacuum] {tabela}: {e}")
    reconstruir_calendario(cur)
    cur.execute(f"SELECT max({REF_COLUNA}) FROM {REF_TABELA}")
    print(f"novo fim da base: {cur.fetchone()[0]}")
    conn.close()

    # ⚠️ O Redis guarda o snapshot e os agregados por 30 min. Sem limpar, o painel continua
    # mostrando a posição anterior por meia hora depois de a base ter andado — que é justamente
    # o intervalo em que alguém abriria a demo para apresentar.
    try:
        import redis
        r = redis.Redis(host=os.getenv("REDIS_HOST", "localhost"),
                        port=int(os.getenv("REDIS_PORT", 6379)),
                        password=os.getenv("REDIS_PASSWORD") or None,
                        socket_connect_timeout=3)
        r.flushall()
        print("cache Redis limpo.")
    except Exception as e:                                        # noqa: BLE001
        print(f"[redis] não consegui limpar ({e}) — o painel pode levar até 30 min para virar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
