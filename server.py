"""
JOGA Analytics — Backend
Rode: python -X utf8 server.py
Acesse: http://localhost:5000
"""

import os
import re
import time
import json
import functools
import traceback
import secrets
import base64
from datetime import datetime, timedelta, timezone
import psycopg2
from psycopg2.extras import Json
import redis
import requests
import resend
from concurrent.futures import ThreadPoolExecutor
from flask import (
    Flask, Response, jsonify, send_from_directory,
    request, session, redirect, stream_with_context
)
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

load_dotenv()

# Resend (envio de email)
resend.api_key = os.getenv('RESEND_API_KEY', '')
RESEND_FROM = os.getenv('RESEND_FROM', 'onboarding@resend.dev')
CRON_HABILITADO = os.getenv('CRON_HABILITADO', 'false').lower() == 'true'

app = Flask(__name__, static_folder='.')

# ── Chave de sessão ──
# Em produção a SECRET_KEY é OBRIGATÓRIA. Antes havia um fallback fixo ('dev-secret-change-me'):
# se a variável faltasse no deploy, o app subia "funcionando" com chave pública e QUALQUER UM
# podia forjar um cookie de sessão de admin. Falha silenciosa em controle de acesso é pior que
# app fora do ar, então aqui ela derruba o boot.
_EM_PRODUCAO = os.getenv('FLASK_ENV', '').lower() == 'production'
_SECRET = os.getenv('SECRET_KEY', '')
if not _SECRET:
    if _EM_PRODUCAO:
        raise RuntimeError(
            'SECRET_KEY não definida. Em produção ela é obrigatória — sem ela os cookies de '
            'sessão seriam forjáveis. Defina a variável na stack (valor fixo, aleatório e longo; '
            'trocá-la desloga todos os usuários).'
        )
    _SECRET = 'dev-secret-change-me'
    print('[AVISO] SECRET_KEY ausente — usando chave de desenvolvimento. NUNCA em produção.')
app.secret_key = _SECRET

# ── Endurecimento do cookie de sessão ──
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,     # JS não lê o cookie (reduz roubo de sessão via XSS)
    SESSION_COOKIE_SAMESITE='Lax',    # não vai em requisição cross-site (anti-CSRF)
    # Secure só em produção: o dev local roda em HTTP e o flag impediria o login na própria
    # máquina. Em produção os domínios são TLS via Traefik.
    SESSION_COOKIE_SECURE=_EM_PRODUCAO,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)


@app.before_request
def _sessao_expira():
    """Antes a sessão não expirava nunca. Passa a durar 12h, renovando a cada uso — quem está
    trabalhando não é deslogado, quem esqueceu o navegador aberto perde a sessão."""
    session.permanent = True


CORS(app, supports_credentials=True)

# ── Config Power BI ──
CONFIG = {
    'tenant_id':     os.getenv('POWERBI_TENANT_ID', ''),
    'client_id':     os.getenv('POWERBI_CLIENT_ID', ''),
    'client_secret': os.getenv('POWERBI_CLIENT_SECRET', ''),
    'dataset_id':    os.getenv('POWERBI_DATASET_ID', ''),
    'group_id':      os.getenv('POWERBI_GROUP_ID', ''),
}

# ── Redis (cache compartilhado entre workers) ──
_R = redis.Redis(
    host=os.getenv('REDIS_HOST', 'localhost'),
    port=int(os.getenv('REDIS_PORT', '6379')),
    decode_responses=True,
    socket_connect_timeout=2,
)

# Handle separado, com timeout curto de LEITURA, usado só no caminho do login.
# Motivo: o cliente acima não define socket_timeout — se o Redis aceitar a conexão e travar
# (sobrecarga, rede ruim), a chamada fica pendurada. Isso é tolerável num cache de dashboard,
# mas inaceitável no login, que passaria a não responder. Aqui a camada por IP é acessória:
# melhor perdê-la em 1s do que segurar a autenticação. O cache principal segue com a folga
# maior, porque lá há payloads grandes (carteira completa) que legitimamente demoram mais.
_R_LOGIN = redis.Redis(
    host=os.getenv('REDIS_HOST', 'localhost'),
    port=int(os.getenv('REDIS_PORT', '6379')),
    decode_responses=True,
    socket_connect_timeout=1,
    socket_timeout=1,
)
_CACHE_TTLS = {
    'dax_agregado':  3600,
    'dax_lista':      300,
    'metadata':     86400,
    'token_pbi':     3000,
}


def _cache_get(key):
    try:
        raw = _R.get(key)
        return json.loads(raw) if raw else None
    except redis.RedisError:
        return None


def _cache_set(key, data, ttl_tipo='dax_agregado'):
    try:
        _R.setex(key, _CACHE_TTLS[ttl_tipo], json.dumps(data, default=str))
    except redis.RedisError:
        pass


def cache_key_for_user(endpoint, params=None):
    sups = _session_supervisores()
    parts = [
        'multpel', endpoint,
        f"role={session.get('role', 'anon')}",
        f"usur={session.get('codusur', '-')}",
        f"supv={','.join(str(s) for s in sups) if sups else '-'}",
    ]
    if params:
        parts.append(json.dumps(params, sort_keys=True))
    return ':'.join(parts)


# ── Banco de dados ──
def get_db():
    return psycopg2.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        port=os.getenv('DB_PORT', '5432'),
        dbname=os.getenv('DB_NAME', 'multpel_db'),
        user=os.getenv('DB_USER', 'postgres'),
        password=os.getenv('DB_PASSWORD', ''),
    )


# ── Config global (tabela multpel_config, chave/valor) ──
def _config_get(chave, default=None):
    """Lê um valor de multpel_config. Silencioso em erro → default (nunca derruba request).
    Fecha a conexão mesmo em erro (ex.: tabela ainda não migrada) pra não vazar conexão."""
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT valor FROM multpel_config WHERE chave = %s", (chave,))
        row = cur.fetchone()
        cur.close()
        return row[0] if row and row[0] is not None else default
    except Exception:
        return default
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _config_set(chave, valor):
    """Upsert de um valor em multpel_config."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO multpel_config (chave, valor, atualizado_em) VALUES (%s, %s, NOW()) "
        "ON CONFLICT (chave) DO UPDATE SET valor = EXCLUDED.valor, atualizado_em = NOW()",
        (chave, str(valor)),
    )
    conn.commit()
    cur.close()
    conn.close()


def _cobertura_limiar_pct():
    """Limiar de baixa performance (%) — configurável no Admin, default 60."""
    try:
        return float(_config_get('cobertura_limiar_pct', '60'))
    except (TypeError, ValueError):
        return 60.0


def _cobertura_coberto_dias():
    """Janela 'em dia' default (dias) — configurável, default 30."""
    try:
        return int(float(_config_get('cobertura_coberto_dias', '30')))
    except (TypeError, ValueError):
        return 30


# ── Decorators de autenticação ──
def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            if '/api/' in request.path:
                return jsonify({'ok': False, 'error': 'Não autenticado'}), 401
            return redirect('/login')
        if session.get('must_change_password') and request.path not in ('/trocar-senha', '/api/trocar-senha'):
            if '/api/' in request.path:
                return jsonify({'ok': False, 'error': 'Troque a senha antes de continuar', 'redirect': '/trocar-senha'}), 403
            return redirect('/trocar-senha')
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'ok': False, 'error': 'Não autenticado'}), 401
        if session.get('role') != 'admin':
            return jsonify({'ok': False, 'error': 'Acesso negado'}), 403
        return f(*args, **kwargs)
    return decorated


# ── Áreas do sistema (Comercial × Compras) ──
# Duas coisas diferentes que se combinam:
#   MODULOS         → o que a EMPRESA comprou (env var da stack; uma instância por cliente)
#   multpel_users.areas → o que a PESSOA pode acessar
# O acesso real é a interseção. Assim, num cliente que só comprou o Estoque, todo usuário
# fica com 1 área efetiva e cai direto no Compras, sem nenhum caso especial no código.
MODULOS = [m.strip() for m in os.getenv('MODULOS', 'comercial,compras').split(',') if m.strip()]

AREAS_VALIDAS = ('comercial', 'compras')


def normalizar_areas(bruto):
    """Normaliza a coluna `areas` (JSONB) numa lista. Base legada/NULL/vazio → ['comercial'],
    que preserva o comportamento de antes da fusão: ninguém ganha Compras por acidente."""
    if isinstance(bruto, list):
        lista = [str(a).strip() for a in bruto if str(a).strip()]
    elif isinstance(bruto, str) and bruto.strip():
        try:
            lista = [str(a).strip() for a in json.loads(bruto)]
        except (ValueError, TypeError):
            lista = [a.strip() for a in bruto.split(',') if a.strip()]
    else:
        lista = []
    lista = [a for a in lista if a in AREAS_VALIDAS]
    return lista or ['comercial']


def areas_efetivas():
    """O que o usuário logado realmente enxerga: suas áreas ∩ os módulos contratados."""
    return [a for a in (session.get('areas') or []) if a in MODULOS]


def tem_area(area):
    return area in areas_efetivas()


def destino_pos_login():
    """Para onde mandar o usuário logado. Uma área só → direto pra ela (sem portal nem
    seletor). Duas áreas → respeita o 'fixar' do portal (area_padrao)."""
    efetivas = areas_efetivas()
    if not efetivas:
        return None                     # sem área contratada/liberada — chamador trata
    if len(efetivas) == 1:
        return '/estoque/' if efetivas[0] == 'compras' else '/'
    padrao = session.get('area_padrao') or 'portal'
    if padrao in ('comercial', 'compras'):
        return '/estoque/' if padrao == 'compras' else '/'
    return '/portal'


# ── Módulo Compras (Estoque) ──
# O pacote estoque/ é autocontido e NÃO importa o server — por isso a guarda de acesso é
# atada aqui, do lado do app. O contrário criaria import circular (server → estoque → server)
# e amarraria o módulo ao servidor, atrapalhando vendê-lo separado.
from estoque import bp as estoque_bp


@estoque_bp.before_request
def _guard_estoque():
    """Login + acesso à área de Compras, para tudo sob /estoque."""
    # Reusa login_required aplicando-o a um no-op: se a autenticação falha ele devolve a
    # resposta pronta (401 ou redirect); se passa, devolve None e seguimos.
    negado = login_required(lambda: None)()
    if negado is not None:
        return negado
    if not tem_area('compras'):
        if '/api/' in request.path:
            return jsonify({'ok': False, 'error': 'Sem acesso ao módulo Compras'}), 403
        return Response('Sem acesso ao módulo Compras', status=403)


if 'compras' in MODULOS:
    app.register_blueprint(estoque_bp)
else:
    # Módulo não contratado: nem entra no mapa de rotas. /estoque/* devolve 404 — não existe,
    # em vez de "existe mas você não pode", que já entregaria informação sobre o produto.
    print('[MODULOS] Compras desativado nesta instância — blueprint /estoque não registrado.')


# ── Guarda de módulo do Comercial ──
# O Comercial tem ~92 rotas declaradas direto no app (não é blueprint), então não dá pra
# "não registrar". A guarda nega no request. Regra DENY BY DEFAULT: em vez de listar o que
# bloquear (e esquecer a rota nova de amanhã), listamos o pouco que é neutro e barramos o resto.
_ROTAS_NEUTRAS = {
    '/login', '/api/login', '/logout', '/health', '/portal', '/api/me',
    '/api/me/area-padrao', '/trocar-senha', '/api/trocar-senha', '/api/status',
    '/multpel-logo.png',
}

# Administração do sistema NÃO pertence a nenhuma área: a tela gerencia usuários das duas
# (é lá que se libera a área Compras, o comprador vinculado e os relatórios de Compras).
# Tratá-la como página do Comercial travava em 403 o admin que só tem Compras — que ficaria
# sem conseguir administrar o próprio sistema. Quem protege aqui é o @admin_required.
_PREFIXOS_NEUTROS = ('/admin', '/api/admin/')

# Metadados (mapas de vendedor/supervisor/comprador) alimentam os campos do Admin, então um
# admin sem a área Comercial precisa deles. Mas são @login_required, não @admin_required —
# liberar o prefixo para todos deixaria um usuário comum de Compras listar a força de vendas.
# Por isso a exceção é condicional: admin sempre, os demais só com a área Comercial.
_PREFIXO_METADADOS = '/api/_internal/'


def _rota_neutra(p):
    return p in _ROTAS_NEUTRAS or p.startswith(_PREFIXOS_NEUTROS)


@app.before_request
def _guard_comercial():
    """Duas checagens para as rotas do Comercial, na ordem certa:

    1) MÓDULO — a empresa contratou o Comercial? Se não, a rota nem existe (404).
    2) ÁREA   — este usuário tem acesso ao Comercial? Se não, 403.

    O (2) é indispensável: as ~92 rotas do Comercial só tinham @login_required, então um
    usuário exclusivo de Compras abria /carteira normalmente. O lado do Estoque já barrava
    pelo guard do blueprint; aqui é o espelho que faltava.
    """
    p = request.path
    if _rota_neutra(p) or p.startswith('/static/') or p.startswith('/estoque'):
        return

    # 1) Módulo não contratado nesta instância
    if 'comercial' not in MODULOS:
        # A raiz é o endereço que o usuário digita. Num cliente que só tem Compras ela deve
        # levar ao produto, não a um 404 — 404 na home passa impressão de sistema quebrado.
        if p == '/':
            return redirect('/estoque/' if 'compras' in MODULOS else '/login')
        if '/api/' in p:
            return jsonify({'ok': False, 'error': 'Módulo Comercial não contratado'}), 404
        return Response('Módulo Comercial não contratado', status=404)

    # 2) Usuário sem a área Comercial (só decide para quem já está logado; quem não está cai
    #    no login_required de cada rota, que trata redirect/401).
    if 'user_id' in session and not tem_area('comercial'):
        # Metadados liberados para admin (precisa deles para editar usuários do Comercial).
        if p.startswith(_PREFIXO_METADADOS) and session.get('role') == 'admin':
            return
        if p == '/':
            destino = destino_pos_login()
            if destino and destino != '/':
                return redirect(destino)
        if '/api/' in p:
            return jsonify({'ok': False, 'error': 'Sem acesso ao módulo Comercial'}), 403
        return Response('Sem acesso ao módulo Comercial', status=403)


# ── Power BI: token cacheado ──
def get_token_cached():
    cached = _cache_get('multpel:pbi:token')
    if cached:
        return cached
    url = f"https://login.microsoftonline.com/{CONFIG['tenant_id']}/oauth2/v2.0/token"
    resp = requests.post(url, data={
        'grant_type': 'client_credentials',
        'client_id': CONFIG['client_id'],
        'client_secret': CONFIG['client_secret'],
        'scope': 'https://analysis.windows.net/powerbi/api/.default'
    }, timeout=30)
    resp.raise_for_status()
    token = resp.json()['access_token']
    _cache_set('multpel:pbi:token', token, 'token_pbi')
    return token


def execute_dax(token, query, dataset_id=None):
    ds = dataset_id or CONFIG['dataset_id']
    url = (
        f"https://api.powerbi.com/v1.0/myorg/groups/"
        f"{CONFIG['group_id']}/datasets/{ds}/executeQueries"
    )
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
    body = {'queries': [{'query': query}], 'serializerSettings': {'includeNulls': True}}
    resp = requests.post(url, json=body, headers=headers, timeout=120)
    resp.raise_for_status()
    return resp.json()


def retry_dax(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        ultima = None
        for tent in range(3):
            try:
                return fn(*args, **kwargs)
            except requests.HTTPError as e:
                ultima = e
                code = e.response.status_code
                msg = (e.response.text or '').lower()
                if 'refresh' in msg or 'processing' in msg:
                    time.sleep(30)
                elif code in (401, 502, 503, 504):
                    time.sleep(2 ** tent)
                else:
                    raise
        raise ultima
    return wrapper


def executar_dax_paralelo(queries: dict) -> dict:
    token = get_token_cached()

    @retry_dax
    def _run(q):
        return execute_dax(token, q)

    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {nome: ex.submit(_run, q) for nome, q in queries.items()}
        return {nome: f.result() for nome, f in futures.items()}


# ── Dataset META (telas de meta) ──
# Dataset separado, no MESMO workspace/grupo que o RCA (mesmo Service Principal/token).
# Contém PCPEDC/PCPEDI (pedidos), CALENDARIO (EhDiaMeta) e as medidas oficiais de realizado.
# Ver memória multpel-metas-bi pra o racional. execute_dax já aceita dataset_id override.
META_DATASET_ID = os.getenv('POWERBI_META_DATASET_ID', '801d7d87-292a-430a-b02a-946ff5fc8c58')


def run_dax_meta(query):
    """Executa 1 query DAX no dataset META (com retry). Token/grupo reusados do RCA."""
    token = get_token_cached()
    return retry_dax(execute_dax)(token, query, dataset_id=META_DATASET_ID)


def run_dax_meta_paralelo(queries: dict) -> dict:
    """Versão paralela de run_dax_meta (dict nome→query → dict nome→payload)."""
    token = get_token_cached()

    @retry_dax
    def _run(q):
        return execute_dax(token, q, dataset_id=META_DATASET_ID)

    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {nome: ex.submit(_run, q) for nome, q in queries.items()}
        return {nome: f.result() for nome, f in futures.items()}


def clean_rows(rows):
    result = []
    for row in rows:
        clean = {}
        for k, v in row.items():
            short_key = k.split('[')[-1].rstrip(']') if '[' in k else k
            clean[short_key] = v
        result.append(clean)
    return result


def _csv_linha(valores):
    out = []
    for v in valores:
        if v is None:
            out.append('')
        else:
            s = str(v).replace('"', '""')
            if ';' in s or '"' in s or '\n' in s:
                s = f'"{s}"'
            out.append(s)
    return ';'.join(out) + '\n'


# Preâmbulo de TODO CSV: BOM UTF-8 + dica de separador. O "sep=;" força o Excel a abrir
# em colunas em QUALQUER máquina (independe do separador de listas das configs regionais).
# O Excel consome essa 1ª linha (não vira dado). Ambos os helpers de linha usam ';'.
CSV_PREAMBULO = '﻿sep=;\n'


def _csv_linha_br(valores):
    """Versão BR de _csv_linha: separador ;, decimal vírgula (Excel pt-BR não
    confunde ponto/vírgula). Para campos numéricos, troca '.' por ',' no decimal."""
    out = []
    for v in valores:
        if v is None:
            out.append('')
        elif isinstance(v, bool):
            out.append('1' if v else '0')
        elif isinstance(v, (int, float)):
            # Garante 2 casas decimais pra valores fracionários (lucro/venda)
            if isinstance(v, float):
                s = f'{v:.2f}'.replace('.', ',')
            else:
                s = str(v)
            out.append(s)
        else:
            s = str(v).replace('"', '""')
            if ';' in s or '"' in s or '\n' in s:
                s = f'"{s}"'
            out.append(s)
    return ';'.join(out) + '\n'


# ── RBAC ──
def _session_supervisores():
    """Lista de codsupervisores (int) do usuário logado. Suporta o novo
    session['codsupervisores'] (lista, supervisor multi-área) e o legado
    session['codsupervisor'] (único). Retorna [] se não houver."""
    lista = session.get('codsupervisores')
    if lista:
        out = []
        for s in lista:
            try:
                out.append(int(s))
            except (TypeError, ValueError):
                pass
        if out:
            return sorted(set(out))
    single = session.get('codsupervisor')
    if single:
        try:
            return [int(single)]
        except (TypeError, ValueError):
            return []
    return []


def _como_lista_supervisores(valor):
    """Normaliza int único / lista / None → list[int] ordenada e sem duplicatas."""
    if valor is None:
        return []
    if isinstance(valor, (list, tuple)):
        out = []
        for x in valor:
            try:
                out.append(int(x))
            except (TypeError, ValueError):
                pass
        return sorted(set(out))
    try:
        return [int(valor)]
    except (TypeError, ValueError):
        return []


def aplicar_rbac_dax():
    """Fragmento DAX a concatenar via && em FILTER, conforme RBAC.
    Supervisor multi-área → CODSUPERVISOR IN {a,b,c} (via _frag_supervisores)."""
    if session.get('role') == 'admin':
        return ""
    if session.get('codusur'):
        return f"FATURAMENTO_VENDAS[CODUSUR] = {int(session['codusur'])}"
    return _frag_supervisores('FATURAMENTO_VENDAS', _session_supervisores())


def rbac_devol_dax():
    """Fragmento RBAC pra colar em FILTER(FATURAMENTO_DEVOLUCAO, ...).
    Sem isso, devoluções vão da empresa toda → valores negativos absurdos pro vendedor."""
    if session.get('role') == 'admin':
        return ""
    if session.get('codusur'):
        return f"FATURAMENTO_DEVOLUCAO[CODUSUR] = {int(session['codusur'])}"
    return _frag_supervisores('FATURAMENTO_DEVOLUCAO', _session_supervisores())


def rbac_devol_av_dax():
    """Fragmento RBAC pra colar em FILTER(FATURAMENTO_DEVOLUCAO_AVULSA, ...)."""
    if session.get('role') == 'admin':
        return ""
    if session.get('codusur'):
        return f"FATURAMENTO_DEVOLUCAO_AVULSA[CODUSUR] = {int(session['codusur'])}"
    return _frag_supervisores('FATURAMENTO_DEVOLUCAO_AVULSA', _session_supervisores())


# ── Helper temporal ──
def _range_dax(tipo, tabela: str, coluna: str):
    """Traduz o token 'range:AAAA-MM-DD:AAAA-MM-DD' num filtro de intervalo FECHADO.
    Retorna None se `tipo` não for um range (aí o chamador segue no fluxo normal).

    Existe pro YoY mensal (MTD vs mesmo MTD do ano anterior): como as janelas dependem do
    último dia COM DADO no BI, elas não cabem nos períodos nomeados. Passando por aqui,
    expr_venda_liquida_rca/expr_lucro_rca e todo o RBAC funcionam sem alteração."""
    if not isinstance(tipo, str) or not tipo.startswith('range:'):
        return None
    _, ini, fim = tipo.split(':')

    def _d(s):
        a, m, d = s.split('-')
        return f'DATE({int(a)},{int(m)},{int(d)})'

    c = f'{tabela}[{coluna}]'
    return f"{c} >= {_d(ini)} && {c} <= {_d(fim)}"


def filtro_periodo(tipo: str) -> str:
    r = _range_dax(tipo, 'FATURAMENTO_VENDAS', 'DTSAIDA')
    if r:
        return r
    if tipo == 'mes_atual':
        return "MONTH(FATURAMENTO_VENDAS[DTSAIDA])=MONTH(TODAY()) && YEAR(FATURAMENTO_VENDAS[DTSAIDA])=YEAR(TODAY())"
    if tipo == 'mes_anterior':
        return ("FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(EOMONTH(TODAY(), -2)+1, 0) "
                "&& FATURAMENTO_VENDAS[DTSAIDA] <= EOMONTH(TODAY(), -1)")
    if tipo == 'ytd':
        return "YEAR(FATURAMENTO_VENDAS[DTSAIDA])=YEAR(TODAY())"
    if tipo == 'ano_anterior':
        return "YEAR(FATURAMENTO_VENDAS[DTSAIDA])=YEAR(TODAY())-1"
    if tipo == '12m':
        return "FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12)"
    if tipo == '12m_anterior':
        return "FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -24) && FATURAMENTO_VENDAS[DTSAIDA] < EDATE(TODAY(), -12)"
    if tipo == '24m':
        return "FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -24)"
    ano, mes = tipo.split('-')
    return f"YEAR(FATURAMENTO_VENDAS[DTSAIDA])={int(ano)} && MONTH(FATURAMENTO_VENDAS[DTSAIDA])={int(mes)}"


def filtro_periodo_devol(tipo: str) -> str:
    """Mesmo que filtro_periodo, mas pra FATURAMENTO_DEVOLUCAO via DTENT (alinha RCA)."""
    r = _range_dax(tipo, 'FATURAMENTO_DEVOLUCAO', 'DTENT')
    if r:
        return r
    if tipo == 'mes_atual':
        return "MONTH(FATURAMENTO_DEVOLUCAO[DTENT])=MONTH(TODAY()) && YEAR(FATURAMENTO_DEVOLUCAO[DTENT])=YEAR(TODAY())"
    if tipo == 'mes_anterior':
        return ("FATURAMENTO_DEVOLUCAO[DTENT] >= EDATE(EOMONTH(TODAY(), -2)+1, 0) "
                "&& FATURAMENTO_DEVOLUCAO[DTENT] <= EOMONTH(TODAY(), -1)")
    if tipo == 'ytd':
        return "YEAR(FATURAMENTO_DEVOLUCAO[DTENT])=YEAR(TODAY())"
    if tipo == 'ano_anterior':
        return "YEAR(FATURAMENTO_DEVOLUCAO[DTENT])=YEAR(TODAY())-1"
    if tipo == '12m':
        return "FATURAMENTO_DEVOLUCAO[DTENT] >= EDATE(TODAY(), -12)"
    if tipo == '12m_anterior':
        return "FATURAMENTO_DEVOLUCAO[DTENT] >= EDATE(TODAY(), -24) && FATURAMENTO_DEVOLUCAO[DTENT] < EDATE(TODAY(), -12)"
    if tipo == '24m':
        return "FATURAMENTO_DEVOLUCAO[DTENT] >= EDATE(TODAY(), -24)"
    ano, mes = tipo.split('-')
    return f"YEAR(FATURAMENTO_DEVOLUCAO[DTENT])={int(ano)} && MONTH(FATURAMENTO_DEVOLUCAO[DTENT])={int(mes)}"


def filtro_periodo_devol_av(tipo: str) -> str:
    """Mesmo que filtro_periodo, mas pra FATURAMENTO_DEVOLUCAO_AVULSA via DTENT."""
    r = _range_dax(tipo, 'FATURAMENTO_DEVOLUCAO_AVULSA', 'DTENT')
    if r:
        return r
    if tipo == 'mes_atual':
        return "MONTH(FATURAMENTO_DEVOLUCAO_AVULSA[DTENT])=MONTH(TODAY()) && YEAR(FATURAMENTO_DEVOLUCAO_AVULSA[DTENT])=YEAR(TODAY())"
    if tipo == 'mes_anterior':
        return ("FATURAMENTO_DEVOLUCAO_AVULSA[DTENT] >= EDATE(EOMONTH(TODAY(), -2)+1, 0) "
                "&& FATURAMENTO_DEVOLUCAO_AVULSA[DTENT] <= EOMONTH(TODAY(), -1)")
    if tipo == 'ytd':
        return "YEAR(FATURAMENTO_DEVOLUCAO_AVULSA[DTENT])=YEAR(TODAY())"
    if tipo == 'ano_anterior':
        return "YEAR(FATURAMENTO_DEVOLUCAO_AVULSA[DTENT])=YEAR(TODAY())-1"
    if tipo == '12m':
        return "FATURAMENTO_DEVOLUCAO_AVULSA[DTENT] >= EDATE(TODAY(), -12)"
    if tipo == '12m_anterior':
        return "FATURAMENTO_DEVOLUCAO_AVULSA[DTENT] >= EDATE(TODAY(), -24) && FATURAMENTO_DEVOLUCAO_AVULSA[DTENT] < EDATE(TODAY(), -12)"
    if tipo == '24m':
        return "FATURAMENTO_DEVOLUCAO_AVULSA[DTENT] >= EDATE(TODAY(), -24)"
    ano, mes = tipo.split('-')
    return f"YEAR(FATURAMENTO_DEVOLUCAO_AVULSA[DTENT])={int(ano)} && MONTH(FATURAMENTO_DEVOLUCAO_AVULSA[DTENT])={int(mes)}"


def _frag_supervisores(tabela, supervisores):
    """Fragmento DAX '<tabela>[CODSUPERVISOR] IN {a,b,c}' ou '' se lista vazia/None.
    Usado como override de filtro de supervisor selecionado no Dashboard (admin/viewer)."""
    if not supervisores:
        return ""
    ids = ", ".join(str(int(s)) for s in supervisores)
    return f"{tabela}[CODSUPERVISOR] IN {{{ids}}}"


def _construir_filtro_3tabelas(periodo='mes_atual', supervisores=None):
    """Retorna (f_vendas, f_devol, f_devol_av) com filtro temporal + RBAC pra cada tabela.
    Pra usar em CALCULATE/FILTER quando a métrica envolve devoluções (VL/Lucro alinhado RCA).
    `supervisores` (lista) adiciona CODSUPERVISOR IN {...} em cada tabela (override admin)."""
    base_v = filtro_periodo(periodo)
    base_d = filtro_periodo_devol(periodo)
    base_da = filtro_periodo_devol_av(periodo)
    rbac_v = aplicar_rbac_dax()
    rbac_d = rbac_devol_dax()
    rbac_da = rbac_devol_av_dax()
    f_v = f"{base_v} && {rbac_v}" if rbac_v else base_v
    f_d = f"{base_d} && {rbac_d}" if rbac_d else base_d
    f_da = f"{base_da} && {rbac_da}" if rbac_da else base_da
    sup_v = _frag_supervisores('FATURAMENTO_VENDAS', supervisores)
    sup_d = _frag_supervisores('FATURAMENTO_DEVOLUCAO', supervisores)
    sup_da = _frag_supervisores('FATURAMENTO_DEVOLUCAO_AVULSA', supervisores)
    if sup_v:  f_v = f"{f_v} && {sup_v}"
    if sup_d:  f_d = f"{f_d} && {sup_d}"
    if sup_da: f_da = f"{f_da} && {sup_da}"
    return (f_v, f_d, f_da)


def expr_venda_liquida_rca(periodo, supervisores=None):
    """Expressão DAX escalar [VENDA LIQUIDA] alinhada com RCA (F.3) + RBAC nas 3 tabelas.
    VL = BRUTA(DTSAIDA) - DEVOL(DTENT) - DEVOL_AV(DTENT)."""
    f_v, f_d, f_da = _construir_filtro_3tabelas(periodo, supervisores)
    return (
        f"(CALCULATE([VENDA BRUTA], FILTER(FATURAMENTO_VENDAS, {f_v}))"
        f" - CALCULATE([TOTAL DEVOLUCAO], FILTER(FATURAMENTO_DEVOLUCAO, {f_d}))"
        f" - CALCULATE([TOTAL DEVOLUCAO AVULSA], FILTER(FATURAMENTO_DEVOLUCAO_AVULSA, {f_da})))"
    )


def expr_lucro_rca(periodo, supervisores=None):
    """Expressão DAX escalar [LUCRO TOTAL] alinhada com RCA (F.4) + RBAC nas 3 tabelas.
    LUCRO = VL - (CUSTO - CUSTO_DEVOL_DTENT - CUSTO_DEVOL_AV_DTENT)."""
    f_v, f_d, f_da = _construir_filtro_3tabelas(periodo, supervisores)
    vl = (
        f"(CALCULATE([VENDA BRUTA], FILTER(FATURAMENTO_VENDAS, {f_v}))"
        f" - CALCULATE([TOTAL DEVOLUCAO], FILTER(FATURAMENTO_DEVOLUCAO, {f_d}))"
        f" - CALCULATE([TOTAL DEVOLUCAO AVULSA], FILTER(FATURAMENTO_DEVOLUCAO_AVULSA, {f_da})))"
    )
    custo = (
        f"(CALCULATE([CUSTO TOTAL], FILTER(FATURAMENTO_VENDAS, {f_v}))"
        f" - CALCULATE([CUSTO TOTAL DEVOLUCAO], FILTER(FATURAMENTO_DEVOLUCAO, {f_d}))"
        f" - CALCULATE([CUSTO TOTAL DEVOLUCAO AVULSA], FILTER(FATURAMENTO_DEVOLUCAO_AVULSA, {f_da})))"
    )
    return f"({vl} - {custo})"


def log_request(rota, parametros=None, duracao_ms=None, erro=None):
    """Persiste em multpel_log. Silencioso em erro pra não derrubar request."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO multpel_log (usuario_id, rota, parametros, duracao_ms, erro) "
            "VALUES (%s, %s, %s, %s, %s)",
            (session.get('user_id'), rota, json.dumps(parametros) if parametros else None, duracao_ms, erro)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass


def _log_background(rota, duracao_ms=None, erro=None):
    """Variante de log_request fora de request context (sem session). Usado pelo prewarm."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO multpel_log (usuario_id, rota, parametros, duracao_ms, erro) "
            "VALUES (%s, %s, %s, %s, %s)",
            (None, rota, None, duracao_ms, erro)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass


_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _normalizar_emails_cc(lista, email_principal=None, limite=5):
    """Normaliza lista de CC: strip, lowercase, dedup, remove principal, limita.
    Levanta ValueError em formato inválido. Retorna lista de strings."""
    if lista is None:
        return []
    if not isinstance(lista, list):
        raise ValueError('email_cc deve ser lista')
    saida = []
    vistos = set()
    if email_principal:
        vistos.add(email_principal.strip().lower())
    for item in lista:
        e = (item or '').strip().lower() if isinstance(item, str) else ''
        if not e:
            continue
        if not _EMAIL_RE.match(e):
            raise ValueError(f'email inválido: {item}')
        if e in vistos:
            continue
        vistos.add(e)
        saida.append(e)
        if len(saida) >= limite:
            break
    return saida


SEGMENTOS_RFM_VALIDOS = {
    'champions', 'loyal', 'cant_lose', 'at_risk',
    'new', 'potential_loyalist', 'lost', 'hibernating',
}


def _normalizar_segmentos_rfm(entrada):
    """Patch K: aceita string comma-separated ou lista. Retorna string canônica
    (ex: 'champions,loyal') ou '' se vazio. Filtra valores fora de SEGMENTOS_RFM_VALIDOS."""
    if not entrada:
        return ''
    if isinstance(entrada, str):
        items = [s.strip() for s in entrada.split(',') if s.strip()]
    elif isinstance(entrada, list):
        items = [str(s).strip() for s in entrada if s]
    else:
        return ''
    validos = [s for s in items if s in SEGMENTOS_RFM_VALIDOS]
    return ','.join(sorted(set(validos)))


# ══════════════════════════════════════════════════════════════════════
# Proteção do login contra força bruta
#
# Duas camadas com papéis distintos:
#   • CONTA (Postgres) — proteção principal. Sobrevive a queda/restart do Redis.
#   • IP    (Redis)    — camada de volume, descartável: se o Redis cair, degrada sozinha
#                        sem derrubar o login, porque a proteção que importa é a de cima.
#
# O bloqueio é TEMPORÁRIO e escalona (15min → 1h → 4h). Bloqueio permanente até um admin
# destravar seria pior: qualquer um poderia travar usuários legítimos de propósito só errando
# a senha deles — vira negação de serviço e enxurrada de chamado no suporte.
# ══════════════════════════════════════════════════════════════════════

# Hash descartável de uma senha aleatória. Serve só para gastar, no caminho "e-mail não
# existe", o mesmo tempo que o hash de um usuário real gastaria (ver login_post).
_HASH_ISCA = generate_password_hash(secrets.token_urlsafe(16))


def _cfg_int(chave, default):
    try:
        return max(1, int(_config_get(chave, default)))
    except (TypeError, ValueError):
        return default


def _ip_do_request():
    """IP real do cliente. Atrás de Traefik/Cloudflare o remote_addr é o do proxy, então o
    X-Forwarded-For (1º da cadeia) é quem identifica o cliente."""
    xff = request.headers.get('X-Forwarded-For', '')
    if xff:
        return xff.split(',')[0].strip()[:45]
    return (request.remote_addr or '')[:45]


def _login_bloqueio_restante(bloqueado_ate):
    """Segundos restantes de bloqueio, ou 0 se liberado."""
    if not bloqueado_ate:
        return 0
    restante = (bloqueado_ate - datetime.now()).total_seconds()
    return int(restante) if restante > 0 else 0


def _chave_ip(ip):
    return f'multpel:login:ip:{ip}'


def _login_ip_excedido(ip):
    """Só CONSULTA o contador do IP — não incrementa.

    ⚠️ Conta apenas tentativas que FALHARAM (ver _login_ip_falha). Contar todo login faria o
    contador estourar num escritório atrás de um único IP (NAT corporativo): a partir do 21º
    acesso legítimo do dia, a empresa inteira ficaria de fora. O sinal de ataque é erro
    repetido, não uso normal.

    Fail-open: sem Redis devolve False e a trava por conta (Postgres) assume sozinha."""
    if not ip or app.config.get('TESTING'):
        # Em teste a camada de IP fica fora: a suíte exercita senha errada de propósito, de um
        # "IP" único, e estouraria o contador derrubando todos os testes seguintes. A proteção
        # que importa (por conta, no Postgres) continua ativa e testada.
        return False
    try:
        n = _R_LOGIN.get(_chave_ip(ip))
        return int(n or 0) > _cfg_int('login_max_por_ip', 50)
    except Exception:      # noqa: BLE001 — inclui timeout/rede, não só RedisError
        return False


def _login_ip_falha(ip):
    """Incrementa o contador do IP. Chamado só quando a tentativa falhou."""
    if not ip or app.config.get('TESTING'):
        return
    try:
        n = _R_LOGIN.incr(_chave_ip(ip))
        if n == 1:
            _R_LOGIN.expire(_chave_ip(ip), _cfg_int('login_bloqueio_min', 15) * 60)
    except Exception:      # noqa: BLE001
        pass


def _login_ip_ok(ip):
    """Login válido limpa o contador: se alguém do escritório acertou a senha, aquele IP não
    está sob ataque e não deve carregar os erros de digitação dos colegas."""
    if not ip:
        return
    try:
        _R_LOGIN.delete(_chave_ip(ip))
    except Exception:      # noqa: BLE001
        pass


def _login_registrar_falha(user_id, email, ip):
    """Incrementa o contador da conta e bloqueia ao atingir o limite, com castigo crescente."""
    max_tent = _cfg_int('login_max_tentativas', 5)
    base_min = _cfg_int('login_bloqueio_min', 15)
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE multpel_users SET tentativas_falhas = COALESCE(tentativas_falhas, 0) + 1 "
            "WHERE id = %s RETURNING tentativas_falhas, COALESCE(bloqueios_seguidos, 0)",
            (user_id,)
        )
        linha = cur.fetchone()
        if linha and linha[0] >= max_tent:
            rodada = linha[1]                      # 0 no 1º bloqueio, 1 no 2º, ...
            minutos = base_min * (4 ** min(rodada, 2))   # 15min → 1h → 4h (teto)
            cur.execute(
                "UPDATE multpel_users SET bloqueado_ate = %s, tentativas_falhas = 0, "
                "bloqueios_seguidos = COALESCE(bloqueios_seguidos, 0) + 1 WHERE id = %s",
                (datetime.now() + timedelta(minutes=minutos), user_id)
            )
            conn.commit()
            _log_login(user_id, email, ip, f'bloqueado:{minutos}min')
            return minutos * 60
        conn.commit()
    finally:
        cur.close()
        conn.close()
    _log_login(user_id, email, ip, 'senha_incorreta')
    return 0


def _login_registrar_sucesso(user_id):
    """Login válido zera o contador e o escalonamento — quem acerta não carrega histórico."""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE multpel_users SET tentativas_falhas = 0, bloqueado_ate = NULL, "
            "bloqueios_seguidos = 0 WHERE id = %s", (user_id,)
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def _log_login(user_id, email, ip, evento):
    """Rastro de tentativa de login. Nunca derruba o login se o log falhar."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO multpel_log (usuario_id, rota, parametros, ip) VALUES (%s, %s, %s, %s)",
            (user_id, f'login:{evento}', json.dumps({'email': email}), ip)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass


def _msg_bloqueio(segundos):
    minutos = max(1, round(segundos / 60))
    if minutos < 60:
        quando = f'{minutos} minuto(s)'
    else:
        quando = f'{round(minutos / 60, 1)} hora(s)'
    return f'Muitas tentativas. Tente novamente em {quando}.'


def _normalizar_relatorios_estoque(entrada):
    """Views de relatório de Compras que o usuário recebe por email. Sanitiza contra o
    catálogo do próprio módulo (estoque/relatorios.py), que é a fonte única."""
    from estoque import relatorios as rel_estoque
    return rel_estoque.normalizar(entrada)


def _validar_area_compras(areas, relatorios_estoque):
    """Impede o estado inconsistente de marcar relatórios de Compras para quem não tem a área
    (o cron tentaria gerar um relatório que o usuário não pode nem abrir). Devolve msg ou None."""
    if relatorios_estoque and 'compras' not in (areas or []):
        return 'Relatórios de Compras exigem acesso à área Compras'
    return None


def _normalizar_codsupervisores(entrada):
    """Aceita lista, int único ou CSV string → list[int] ordenada e sem duplicatas.
    Usado no CRUD de usuários (supervisor multi-área)."""
    if isinstance(entrada, str):
        entrada = [s.strip() for s in entrada.split(',') if s.strip()]
    return _como_lista_supervisores(entrada)


def _ler_usuario(usuario_id):
    """Carrega 1 user do multpel_users por id. Retorna dict ou None."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, nome, email, role, codusur, codsupervisor, telefone, ativo, "
        "cron_enabled, cron_horario, cron_frequencia, email_cc, segmentos_rfm, codsupervisores, "
        "email_proximo_pedido, email_alerta_cobertura, areas, codcomprador, relatorios_estoque "
        "FROM multpel_users WHERE id = %s",
        (usuario_id,)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return None
    # Multi-área: lista normalizada (legado single → [single])
    sups = _como_lista_supervisores(row[13]) or _como_lista_supervisores(row[5])
    return {
        'id': row[0], 'nome': row[1], 'email': row[2], 'role': row[3],
        'codusur': row[4], 'codsupervisor': row[5], 'telefone': row[6], 'ativo': row[7],
        'cron_enabled': row[8], 'cron_horario': row[9], 'cron_frequencia': row[10],
        'email_cc': row[11] if row[11] is not None else [],
        'segmentos_rfm': row[12] or '',
        'codsupervisores': sups,
        'email_proximo_pedido': bool(row[14]),
        'email_alerta_cobertura': bool(row[15]),
        'areas': normalizar_areas(row[16]),
        'codcomprador': row[17],
        'relatorios_estoque': row[18] if row[18] is not None else [],
    }


def _gerar_relatorio_para_usuario(usuario):
    """Gera (csv_bytes, [(filename_pdf, pdf_bytes), ...], total) filtrados pelo escopo do usuário.
    - vendedor → 1 PDF ordenado por Lucro 12m desc.
    - supervisor multi-área → 1 PDF por área (vendedores em ordem alfabética).
    - admin/viewer → 1 PDF da carteira inteira.
    O CSV é sempre 1 só (combinado, cobre todas as áreas)."""
    import re
    from datetime import date as _date

    # Escopo de CADASTRO do destinatário (a sessão é simulada em enviar_relatorio_email)
    clientes = _carteira_no_escopo()
    seg = usuario.get('segmentos_rfm') or None
    role = usuario.get('role')
    nome_user = usuario.get('nome') or 'user'
    data_iso = _date.today().isoformat()

    _NOMES_SEG = {
        'champions': 'Campeões', 'loyal': 'Fiéis', 'cant_lose': 'Não Perder',
        'at_risk': 'Em Risco', 'potential_loyalist': 'Promissores',
        'new': 'Novos', 'hibernating': 'Inativos', 'lost': 'Perdidos',
    }
    filtros_seg = ''
    if seg:
        nomes = [_NOMES_SEG.get(s, s) for s in seg.split(',') if s]
        filtros_seg = f" · Segmentos: {', '.join(nomes)}"

    def _slug(s):
        return re.sub(r'[\\/:*?"<>|]+', '', str(s)).strip()[:40] or 'area'

    def _args(extra):
        a = {'modo': 'personalizada', 'limit': 100000, 'offset': 0, '_interno': True}
        if seg:
            a['segmento'] = seg
        a.update(extra)
        return a

    pdfs = []  # [(filename, bytes)]

    if role == 'vendedor' and usuario.get('codusur') is not None:
        rows = _filtrar_carteira(clientes, _args({'vendedor': str(usuario['codusur']),
                                                  'sort': 'lucro_12m', 'dir': 'desc'}))['rows']
        pdf = _gerar_pdf_carteira(rows, filtros_resumo=f"Relatório de {nome_user}{filtros_seg}")
        pdfs.append((f"carteira_{_slug(nome_user)}_{data_iso}.pdf", pdf))
        csv_rows = rows

    elif role == 'supervisor' and usuario.get('codsupervisores'):
        sup_map = _carregar_supervisores_map()
        for area in usuario['codsupervisores']:
            rows = _filtrar_carteira(clientes, _args({'time': str(area),
                                                      'sort': 'vendedor', 'dir': 'asc'}))['rows']
            area_nome = (sup_map.get(str(area)) or {}).get('nome') or f'Time {area}'
            pdf = _gerar_pdf_carteira(rows, filtros_resumo=f"Área: {area_nome} · {nome_user}{filtros_seg}")
            pdfs.append((f"carteira_{_slug(area_nome)}_{data_iso}.pdf", pdf))
        # CSV combinado: a carteira já é a união das áreas (RBAC), ordena por vendedor A→Z
        csv_rows = _filtrar_carteira(clientes, _args({'sort': 'vendedor', 'dir': 'asc'}))['rows']

    else:
        # admin/viewer → carteira inteira (raro por email)
        rows = _filtrar_carteira(clientes, _args({'sort': 'receita_perdida', 'dir': 'desc'}))['rows']
        pdf = _gerar_pdf_carteira(rows, filtros_resumo=f"Relatório de {nome_user}{filtros_seg}")
        pdfs.append((f"carteira_{_slug(nome_user)}_{data_iso}.pdf", pdf))
        csv_rows = rows

    # CSV (1 só, combinado)
    cabecalho = ['CodCli', 'Cliente', 'Cidade', 'UF', 'Vendedor', 'Telefone',
                 'R(dias)', 'Segmento', 'Venda12m', 'MediaVenda12m', 'ReceitaPerdidaProj']
    csv_lines = [CSV_PREAMBULO + _csv_linha(cabecalho).rstrip('\n')]
    for c in csv_rows:
        v = c.get('venda_12m') or 0
        csv_lines.append(_csv_linha([
            c.get('codcli'), c.get('cliente'), c.get('cidade'), c.get('uf'),
            c.get('vendedor'), c.get('telefone'),
            c.get('recencia_dias'), c.get('segmento'),
            v, round(v / 12, 2), c.get('receita_perdida_proj'),
        ]).rstrip('\n'))
    csv_bytes = ('\n'.join(csv_lines) + '\n').encode('utf-8')

    # Anexo extra opcional: "Lista do Dia" (Próximo Pedido) — clientes vencidos do escopo
    # do usuário + top 5 produtos a oferecer. Escopado por vendedor/área = leve.
    if usuario.get('email_proximo_pedido'):
        # Hoje + vencidos 1-15 dias (16+ o diretor não quer — mesma regra da tela). Relatório
        # único ordenado de hoje pra frente (atraso crescente): hoje (0), 1d, 2d... 15d.
        due = [c for c in _clientes_proximo_pedido(clientes, 'vencidos') if (c.get('dias_atraso') or 0) <= 15]
        due.sort(key=lambda c: c.get('dias_atraso') or 0)
        due = due[:200]
        prods = _top_produtos_varios([c['codcli'] for c in due], 5) if due else {}
        pp_head = ['CodCli', 'Cliente', 'Cidade', 'UF', 'Vendedor', 'Telefone', 'UltimaCompra',
                   'Ciclo', 'Previsao', 'DiasAtraso', 'Status', 'Venda12m', 'ReceitaEmRisco', 'Top5Produtos']
        pp_lines = [CSV_PREAMBULO + _csv_linha(pp_head).rstrip('\n')]
        for c in due:
            tp = ' | '.join(p['descricao'] for p in prods.get(c['codcli'], []))
            pp_lines.append(_csv_linha([
                c.get('codcli'), c.get('cliente'), c.get('cidade'), c.get('uf'), c.get('vendedor'),
                c.get('telefone'), c.get('ultima_compra'), c.get('ciclo_pessoal'),
                c.get('proximo_pedido_previsto'), c.get('dias_atraso'), c.get('status_personalizada'),
                c.get('venda_12m') or 0, c.get('receita_perdida_proj') or 0, tp,
            ]).rstrip('\n'))
        pp_bytes = ('\n'.join(pp_lines) + '\n').encode('utf-8')
        pdfs.append((f"lista_do_dia_{_slug(nome_user)}_{data_iso}.csv", pp_bytes))

    return csv_bytes, pdfs, len(csv_rows)


def enviar_relatorio_email(usuario_id):
    """Pipeline completo: lê user → gera PDF+CSV filtrados → envia via Resend.
    Retorna {ok, message_id?, error?, clientes_no_anexo?}."""
    if not resend.api_key:
        return {'ok': False, 'error': 'RESEND_API_KEY não configurado'}

    user = _ler_usuario(usuario_id)
    if not user:
        return {'ok': False, 'error': f'Usuário {usuario_id} não encontrado'}
    if not user.get('email'):
        return {'ok': False, 'error': f'Usuário {usuario_id} sem email'}
    if not user.get('ativo'):
        return {'ok': False, 'error': f'Usuário {usuario_id} desativado'}

    # Cron roda em background SEM request HTTP. Funções como _carregar_carteira_full
    # usam session.get() pra RBAC e cache_key_for_user. Simula contexto com a session
    # do próprio usuário pra RBAC filtrar corretamente (e o cache não colidir entre users).
    with app.test_request_context():
        session['user_id'] = user['id']
        session['role'] = user.get('role')
        session['codusur'] = user.get('codusur')
        session['codsupervisores'] = user.get('codsupervisores') or []   # multi-área → união no RBAC
        session['codsupervisor'] = user.get('codsupervisor')
        try:
            csv_bytes, pdfs, count = _gerar_relatorio_para_usuario(user)
        except Exception as e:
            return {'ok': False, 'error': f'Falha ao gerar relatório: {e}'}

    from datetime import date as _date
    data_iso = _date.today().isoformat()
    base_nome = f"carteira_{user.get('nome','user').replace(' ','_').lower()}_{data_iso}"

    n_pdfs = len(pdfs)
    plural_pdf = f"{n_pdfs} PDFs (1 por área)" if n_pdfs > 1 else "1 PDF (visual)"

    # Descrição dos filtros aplicados (em vez do genérico "modo de classificação")
    _NOMES_SEG = {
        'champions': 'Campeões', 'loyal': 'Fiéis', 'cant_lose': 'Não Perder',
        'at_risk': 'Em Risco', 'potential_loyalist': 'Promissores',
        'new': 'Novos', 'hibernating': 'Inativos', 'lost': 'Perdidos',
    }
    seg = user.get('segmentos_rfm') or ''
    if seg:
        seg_txt = ', '.join(_NOMES_SEG.get(s, s) for s in seg.split(',') if s)
    else:
        seg_txt = 'Carteira completa (todos os segmentos)'
    areas_li = ''
    if user.get('role') == 'supervisor' and user.get('codsupervisores'):
        sup_map = _carregar_supervisores_map()
        nomes_areas = ', '.join((sup_map.get(str(a)) or {}).get('nome') or f'Time {a}'
                                for a in user['codsupervisores'])
        areas_li = f"  <li>Áreas: {nomes_areas}</li>\n"

    html = f"""<html><body style="font-family:Arial,sans-serif;color:#0a0e17;">
<h2 style="color:#38bdf8;">JOGA Analytics</h2>
<p>Olá <strong>{user.get('nome')}</strong>,</p>
<p>Segue seu relatório de carteira atualizado em {_date.today().strftime('%d/%m/%Y')}.</p>
<ul>
  <li><strong>{count}</strong> clientes na sua carteira</li>
{areas_li}  <li>Segmentos: {seg_txt}</li>
</ul>
<p>Anexos: {plural_pdf} + 1 CSV (Excel-compatível).</p>
<p style="color:#94a3b8;font-size:12px;">Email automatizado — JOGA Analytics</p>
</body></html>"""

    # Patch J — sanitiza lista de CC (remove principal duplicado, valida formato, limita)
    try:
        emails_cc = _normalizar_emails_cc(user.get('email_cc') or [], user['email'])
    except ValueError:
        emails_cc = []  # CC inválido no banco não bloqueia o envio principal

    attachments = [
        {'filename': fname, 'content': base64.b64encode(pdf_b).decode()}
        for (fname, pdf_b) in pdfs
    ]
    attachments.append({'filename': base_nome + '.csv', 'content': base64.b64encode(csv_bytes).decode()})

    payload = {
        'from': RESEND_FROM,
        'to': [user['email']],
        'subject': f"JOGA Analytics — Carteira {_date.today().strftime('%d/%m/%Y')}",
        'html': html,
        'attachments': attachments,
    }
    if emails_cc:
        payload['cc'] = emails_cc

    try:
        resp = resend.Emails.send(payload)
        message_id = resp.get('id') if isinstance(resp, dict) else getattr(resp, 'id', None)
        _log_background(f'email:enviado:user{usuario_id}', duracao_ms=None)
        total_bytes = sum(len(p) for _, p in pdfs) + len(csv_bytes)
        return {'ok': True, 'message_id': message_id, 'clientes_no_anexo': count,
                'anexos_kb': round(total_bytes / 1024, 1),
                'pdfs': n_pdfs,
                'destinatarios_total': 1 + len(emails_cc)}
    except Exception as e:
        erro_str = str(e)[:500]
        _log_background(f'email:erro:user{usuario_id}', erro=erro_str)
        return {'ok': False, 'error': erro_str}


def enviar_relatorios_estoque_email(usuario_id):
    """Envia os relatórios de Compras marcados para o usuário (PDF + XLSX de cada um).

    Espelha enviar_relatorio_email(), mas o conteúdo vem do módulo Compras. Retorna
    {ok, message_id?, error?, skipped?} — `skipped` quando não há nada a enviar, para o cron
    conseguir distinguir "não tinha o que mandar" de "falhou"."""
    if not resend.api_key:
        return {'ok': False, 'error': 'RESEND_API_KEY não configurado'}

    user = _ler_usuario(usuario_id)
    if not user:
        return {'ok': False, 'error': f'Usuário {usuario_id} não encontrado'}
    if not user.get('email'):
        return {'ok': False, 'error': f'Usuário {usuario_id} sem email'}
    if not user.get('ativo'):
        return {'ok': False, 'error': f'Usuário {usuario_id} desativado'}

    # Respeita os dois níveis: o que a empresa contratou e o que a pessoa acessa.
    if 'compras' not in MODULOS:
        return {'ok': True, 'skipped': 'módulo Compras não contratado nesta instância'}
    if 'compras' not in (user.get('areas') or []):
        return {'ok': True, 'skipped': 'usuário sem acesso à área Compras'}

    from estoque import emails as est_emails
    from estoque import relatorios as rel_estoque
    views = rel_estoque.normalizar(user.get('relatorios_estoque'))
    if not views:
        return {'ok': True, 'skipped': 'nenhum relatório de Compras marcado'}

    from datetime import date as _date
    data_iso = _date.today().isoformat()
    try:
        anexos, erros = est_emails.gerar_anexos(
            app, views, codcomprador=user.get('codcomprador'), data_iso=data_iso)
    except Exception as e:
        return {'ok': False, 'error': f'Falha ao gerar relatórios de Compras: {e}'}
    if not anexos:
        return {'ok': False, 'error': 'Nenhum relatório pôde ser gerado. ' + ('; '.join(erros))[:300]}

    comp_txt = 'Empresa toda (todos os compradores)'
    if user.get('codcomprador'):
        try:
            from estoque.routes import _compradores_map
            comp_txt = _compradores_map().get(int(user['codcomprador'])) or f"Comprador {user['codcomprador']}"
        except Exception:
            comp_txt = f"Comprador {user['codcomprador']}"

    itens_li = '\n'.join(f"  <li>{rel_estoque.ROTULOS.get(v, v)}</li>" for v in views)
    aviso = ''
    if erros:
        aviso = ('<p style="color:#b45309;font-size:12px;">Alguns relatórios não puderam ser gerados '
                 'nesta execução e ficaram de fora dos anexos.</p>')

    html = f"""<html><body style="font-family:Arial,sans-serif;color:#0a0e17;">
<h2 style="color:#38bdf8;">JOGA Analytics — Compras</h2>
<p>Olá <strong>{user.get('nome')}</strong>,</p>
<p>Seguem seus relatórios de compras de {_date.today().strftime('%d/%m/%Y')}.</p>
<ul>
  <li>Recorte: {comp_txt}</li>
</ul>
<p>Relatórios incluídos:</p>
<ul>
{itens_li}
</ul>
<p>Cada relatório vai em PDF (leitura) e XLSX (para trabalhar os dados).</p>
{aviso}<p style="color:#94a3b8;font-size:12px;">Email automatizado — JOGA Analytics</p>
</body></html>"""

    try:
        emails_cc = _normalizar_emails_cc(user.get('email_cc') or [], user['email'])
    except ValueError:
        emails_cc = []

    payload = {
        'from': RESEND_FROM,
        'to': [user['email']],
        'subject': f"JOGA Analytics — Compras {_date.today().strftime('%d/%m/%Y')}",
        'html': html,
        'attachments': [{'filename': fn, 'content': base64.b64encode(b).decode()} for fn, b in anexos],
    }
    if emails_cc:
        payload['cc'] = emails_cc

    try:
        resp = resend.Emails.send(payload)
        message_id = resp.get('id') if isinstance(resp, dict) else getattr(resp, 'id', None)
        _log_background(f'email_estoque:enviado:user{usuario_id}')
        return {'ok': True, 'message_id': message_id, 'relatorios': len(views),
                'anexos': len(anexos), 'falhas': erros,
                'anexos_kb': round(sum(len(b) for _, b in anexos) / 1024, 1)}
    except Exception as e:
        erro_str = str(e)[:500]
        _log_background(f'email_estoque:erro:user{usuario_id}', erro=erro_str)
        return {'ok': False, 'error': erro_str}


def enviar_alerta_cobertura_email(usuario_id):
    """Alerta de baixa performance de cobertura. Calcula o placar NO ESCOPO do destinatário
    (admin/diretor → empresa; supervisor → suas áreas) e envia digest de Times e RCAs abaixo
    do limiar, pior→melhor. Retorna {ok, ...} ou {ok: False, error}. Não envia se não houver
    ninguém abaixo do limiar (silêncio = tudo em dia)."""
    if not resend.api_key:
        return {'ok': False, 'error': 'RESEND_API_KEY não configurado'}

    user = _ler_usuario(usuario_id)
    if not user:
        return {'ok': False, 'error': f'Usuário {usuario_id} não encontrado'}
    if not user.get('email') or not user.get('ativo'):
        return {'ok': False, 'error': f'Usuário {usuario_id} sem email ou desativado'}

    coberto_dias = _cobertura_coberto_dias()
    limiar_pct = _cobertura_limiar_pct()

    # Simula a sessão do destinatário para _carteira_no_escopo() recortar corretamente (RBAC).
    with app.test_request_context():
        session['user_id'] = user['id']
        session['role'] = user.get('role')
        session['codusur'] = user.get('codusur')
        session['codsupervisores'] = user.get('codsupervisores') or []
        session['codsupervisor'] = user.get('codsupervisor')
        try:
            clientes = _carteira_no_escopo()
            niveis = cob.agregar_niveis(clientes, coberto_dias=coberto_dias)
            baixos = cob.times_rcas_abaixo(niveis, limiar_pct)
        except Exception as e:
            return {'ok': False, 'error': f'Falha ao calcular cobertura: {e}'}

    if not baixos['times'] and not baixos['vendedores']:
        return {'ok': True, 'skipped': 'nada abaixo do limiar'}

    from datetime import date as _date
    emp = niveis['empresa']

    def _pct(v):
        return f"{(v or 0) * 100:.1f}%".replace('.', ',')

    def _brl(v):
        return f"R$ {(v or 0):,.0f}".replace(',', '.')

    def _linhas(itens, rotulo):
        if not itens:
            return f"<p style='color:#94a3b8;'>Nenhum {rotulo} abaixo do limiar. 👏</p>"
        li = ''.join(
            f"<tr><td style='padding:4px 8px;'>{(g.get('nome') or '')}"
            + (" <span style='color:#94a3b8;'>(amostra pequena)</span>" if g.get('amostra_pequena') else "")
            + f"</td><td style='padding:4px 8px;text-align:center;color:#dc2626;font-weight:bold;'>{_pct(g['cobertura_clientes'])}</td>"
            f"<td style='padding:4px 8px;text-align:center;'>{g['clientes_cobertos']}</td>"
            f"<td style='padding:4px 8px;text-align:center;color:#94a3b8;'>{g['total_clientes']}</td>"
            f"<td style='padding:4px 8px;text-align:right;'>{_brl(g['receita_em_risco'])}</td></tr>"
            for g in itens
        )
        return (
            f"<h3 style='color:#0a0e17;margin:14px 0 4px;'>{rotulo} abaixo de {limiar_pct:.0f}% "
            f"({len(itens)})</h3>"
            "<table style='border-collapse:collapse;width:100%;font-size:13px;'>"
            "<tr style='background:#1e293b;color:#fff;'>"
            "<th style='padding:5px 8px;text-align:left;'>Nome</th>"
            "<th style='padding:5px 8px;'>Cobertura</th>"
            "<th style='padding:5px 8px;'>Positivados (&le;" + str(coberto_dias) + "d)</th>"
            "<th style='padding:5px 8px;'>Carteira (total)</th>"
            "<th style='padding:5px 8px;text-align:right;'>Receita em risco</th></tr>"
            f"{li}</table>"
        )

    html = f"""<html><body style="font-family:Arial,sans-serif;color:#0a0e17;">
<h2 style="color:#dc2626;">⚠ Alerta de Cobertura de Carteira</h2>
<p>Olá <strong>{user.get('nome')}</strong>, em {_date.today().strftime('%d/%m/%Y')} há
<strong>{len(baixos['times'])} time(s)</strong> e <strong>{len(baixos['vendedores'])} RCA(s)</strong>
abaixo do limiar de <strong>{limiar_pct:.0f}%</strong> de cobertura (compra ≤ {coberto_dias} dias).</p>
<p style="background:#f1f5f9;padding:10px;border-radius:6px;">
  <strong>Empresa (seu escopo):</strong> cobertura {_pct(emp['cobertura_clientes'])} por clientes ·
  {_pct(emp['cobertura_valor'])} por valor · receita em risco <strong>{_brl(emp['receita_em_risco'])}</strong>.
</p>
<p style="color:#475569;font-size:12px;">
  <strong>Como ler:</strong> <em>Cobertura</em> = % da carteira que <strong>comprou</strong> nos últimos
  {coberto_dias} dias (positivados). <em>Positivados</em> = clientes que compraram na janela;
  <em>Carteira (total)</em> = universo do time/RCA. Ex.: cobertura 30% de 584 = ~175 positivados,
  ~409 sem positivação.
</p>
{_linhas(baixos['times'], 'Times')}
{_linhas(baixos['vendedores'], 'RCAs / Vendedores')}
<p style="color:#94a3b8;font-size:12px;margin-top:16px;">Ordenado do pior para o melhor. Acesse o painel
Gerencial para o detalhamento por faixa. Email automatizado — JOGA Analytics.</p>
</body></html>"""

    try:
        emails_cc = _normalizar_emails_cc(user.get('email_cc') or [], user['email'])
    except ValueError:
        emails_cc = []

    payload = {
        'from': RESEND_FROM,
        'to': [user['email']],
        'subject': f"⚠ Cobertura abaixo de {limiar_pct:.0f}% — {_date.today().strftime('%d/%m/%Y')}",
        'html': html,
    }
    if emails_cc:
        payload['cc'] = emails_cc

    try:
        resp = resend.Emails.send(payload)
        message_id = resp.get('id') if isinstance(resp, dict) else getattr(resp, 'id', None)
        _log_background(f'alerta_cobertura:enviado:user{usuario_id}')
        return {'ok': True, 'message_id': message_id,
                'times_abaixo': len(baixos['times']), 'rcas_abaixo': len(baixos['vendedores'])}
    except Exception as e:
        erro_str = str(e)[:500]
        _log_background(f'alerta_cobertura:erro:user{usuario_id}', erro=erro_str)
        return {'ok': False, 'error': erro_str}


# ── Rotas: páginas estáticas ──
@app.route('/static/<path:filename>')
def static_assets(filename):
    """Serve arquivos do diretório static/ (drill-cliente.css, drill-cliente.js, etc).
    Necessário porque o Flask foi configurado com static_folder='.' (raiz)."""
    return send_from_directory('static', filename)


@app.route('/login', methods=['GET'])
def login_page():
    if 'user_id' in session and not session.get('must_change_password'):
        return redirect(destino_pos_login() or '/')
    return send_from_directory('.', 'login.html')


@app.route('/health')
def health():
    """Liveness check pro Docker/Traefik. Sem auth, retorna 200 quando o processo está up.
    Informa os módulos ativos — com uma instância por cliente, é o jeito mais rápido de
    conferir remotamente o que aquela instalação está servindo."""
    return jsonify({'ok': True, 'service': 'joga-analytics', 'modulos': MODULOS}), 200


@app.route('/trocar-senha', methods=['GET'])
def trocar_senha_page():
    if 'user_id' not in session:
        return redirect('/login')
    return send_from_directory('.', 'trocar-senha.html')


@app.route('/')
@login_required
def index_page():
    # Quem não tem a área Comercial (ex.: cliente que só comprou o Estoque, ou usuário só de
    # compras) não pode cair aqui — manda pro destino dele em vez de mostrar um 403 seco.
    if not tem_area('comercial'):
        destino = destino_pos_login()
        if destino and destino != '/':
            return redirect(destino)
        return Response('Sem acesso ao módulo Comercial', status=403)
    return send_from_directory('.', 'index.html')


@app.route('/portal')
@login_required
def portal_page():
    """Tela de escolha de área. Só faz sentido com 2 áreas efetivas — com uma só, manda direto
    (o próprio portal.html também redireciona, mas resolver no servidor evita o piscar)."""
    destino = destino_pos_login()
    if destino is None:
        return Response('Usuário sem área liberada. Procure o administrador.', status=403)
    if len(areas_efetivas()) < 2:
        return redirect(destino)
    return send_from_directory('.', 'portal.html')


@app.route('/api/me/area-padrao', methods=['PUT'])
@login_required
def set_area_padrao():
    """Grava o 'fixar' do portal: 'portal' (perguntar sempre) | 'comercial' | 'compras'."""
    valor = (request.get_json() or {}).get('area_padrao', 'portal')
    if valor not in ('portal',) + AREAS_VALIDAS:
        return jsonify({'ok': False, 'error': 'Área inválida'}), 400
    # Não deixa fixar numa área que o usuário não acessa (viraria um loop de redirect).
    if valor in AREAS_VALIDAS and valor not in areas_efetivas():
        return jsonify({'ok': False, 'error': 'Você não tem acesso a essa área'}), 403
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE multpel_users SET area_padrao = %s WHERE id = %s", (valor, session['user_id']))
    conn.commit()
    cur.close()
    conn.close()
    session['area_padrao'] = valor
    return jsonify({'ok': True, 'area_padrao': valor})


@app.route('/api/me/tema', methods=['PUT'])
@login_required
def set_tema():
    """Preferência de tema do usuário: 'escuro' (padrão) ou 'claro'. Persistida no banco para
    seguir a pessoa em qualquer máquina; o localStorage cuida da aplicação instantânea."""
    valor = (request.get_json() or {}).get('tema', 'escuro')
    if valor not in ('escuro', 'claro'):
        return jsonify({'ok': False, 'error': 'Tema inválido'}), 400
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE multpel_users SET tema = %s WHERE id = %s", (valor, session['user_id']))
    conn.commit()
    cur.close()
    conn.close()
    session['tema'] = valor
    return jsonify({'ok': True, 'tema': valor})


# ── Auth API ──
@app.route('/api/login', methods=['POST'])
def login_post():
    data = request.get_json() or {}
    email = data.get('email', '').strip().lower()
    senha = data.get('senha', '')
    if not email or not senha:
        return jsonify({'ok': False, 'error': 'Preencha e-mail e senha'}), 400

    ip = _ip_do_request()
    # Camada de volume: barra o atacante antes mesmo de tocar no banco.
    if _login_ip_excedido(ip):
        _log_login(None, email, ip, 'ip_bloqueado')
        return jsonify({'ok': False, 'error': _msg_bloqueio(_cfg_int('login_bloqueio_min', 15) * 60)}), 429

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, nome, password_hash, role, ativo, codusur, codsupervisor, must_change_password, codsupervisores, "
        "areas, area_padrao, codcomprador, bloqueado_ate, tema "
        "FROM multpel_users WHERE email = %s", (email,)
    )
    user = cur.fetchone()
    cur.close()
    conn.close()
    if not user:
        # Gasta o mesmo tempo de um hash real. Sem isso, e-mail inexistente responde na hora e
        # e-mail válido demora o check_password_hash — a diferença permite descobrir por
        # cronometragem quais contas existem.
        check_password_hash(_HASH_ISCA, senha)
        _login_ip_falha(ip)
        _log_login(None, email, ip, 'email_inexistente')
        return jsonify({'ok': False, 'error': 'E-mail ou senha inválidos'}), 401
    (uid, nome, pw_hash, role, ativo, codusur, codsupervisor, mcp, codsupervisores,
     areas, area_padrao, codcomprador, bloqueado_ate, tema) = user
    if not ativo:
        return jsonify({'ok': False, 'error': 'Conta desativada'}), 403

    # Bloqueio vale antes de conferir a senha: durante a janela nem a senha correta entra —
    # é o que impede continuar adivinhando.
    restante = _login_bloqueio_restante(bloqueado_ate)
    if restante:
        _log_login(uid, email, ip, 'tentativa_durante_bloqueio')
        return jsonify({'ok': False, 'error': _msg_bloqueio(restante)}), 429

    if not check_password_hash(pw_hash, senha):
        _login_ip_falha(ip)
        bloqueou = _login_registrar_falha(uid, email, ip)
        if bloqueou:
            return jsonify({'ok': False, 'error': _msg_bloqueio(bloqueou)}), 429
        return jsonify({'ok': False, 'error': 'E-mail ou senha inválidos'}), 401

    _login_registrar_sucesso(uid)
    _login_ip_ok(ip)
    _log_login(uid, email, ip, 'sucesso')
    # Supervisor multi-área: lista normalizada (legado single → [single]); session guarda
    # a lista + o 1º elemento em codsupervisor (compatibilidade com qualquer caminho legado).
    sups = _como_lista_supervisores(codsupervisores) or _como_lista_supervisores(codsupervisor)
    session['user_id']              = uid
    session['nome']                 = nome
    session['role']                 = role
    session['codusur']              = codusur
    session['codsupervisores']      = sups
    session['codsupervisor']        = sups[0] if sups else None
    session['must_change_password'] = bool(mcp)
    session['areas']                = normalizar_areas(areas)
    session['area_padrao']          = area_padrao or 'portal'
    session['codcomprador']         = codcomprador   # filtro default do Compras (não é trava)
    session['tema']                 = tema or 'escuro'
    if mcp:
        return jsonify({'ok': True, 'redirect': '/trocar-senha'})
    destino = destino_pos_login()
    if destino is None:
        session.clear()
        return jsonify({'ok': False, 'error': 'Usuário sem área liberada. Procure o administrador.'}), 403
    return jsonify({'ok': True, 'redirect': destino})


@app.route('/api/trocar-senha', methods=['POST'])
@login_required
def trocar_senha_post():
    data = request.get_json() or {}
    nova = data.get('nova', '')
    confirma = data.get('confirma', '')
    if len(nova) < 6:
        return jsonify({'ok': False, 'error': 'Senha deve ter no mínimo 6 caracteres'}), 400
    if nova != confirma:
        return jsonify({'ok': False, 'error': 'Confirmação não bate com a nova senha'}), 400
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE multpel_users SET password_hash = %s, must_change_password = false WHERE id = %s",
        (generate_password_hash(nova), session['user_id'])
    )
    conn.commit()
    cur.close()
    conn.close()
    session['must_change_password'] = False
    # Respeita as áreas do usuário, como o login faz. Cravar '/' aqui mandava todo mundo pro
    # Comercial depois da troca obrigatória — quem tinha as duas áreas nunca via o portal no
    # primeiro acesso, e quem só tinha Compras entrava por um redirect a mais.
    return jsonify({'ok': True, 'redirect': destino_pos_login() or '/'})


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


@app.route('/api/me')
@login_required
def me():
    return jsonify({
        'ok': True,
        'nome': session.get('nome'),
        'role': session.get('role'),
        'codusur': session.get('codusur'),
        'codsupervisor': session.get('codsupervisor'),
        'codsupervisores': _session_supervisores(),
        # Front usa isto pra montar o seletor de área e esconder o menu do que não é liberado.
        'areas': areas_efetivas(),
        'area_padrao': session.get('area_padrao') or 'portal',
        'codcomprador': session.get('codcomprador'),
        'modulos': MODULOS,
        'tema': session.get('tema') or 'escuro',
    })


@app.route('/api/status')
def status():
    """Saúde do sistema: Redis + token PBI cacheado."""
    redis_ok = False
    try:
        _R.ping()
        redis_ok = True
    except redis.RedisError:
        pass
    token_cached = bool(_cache_get('multpel:pbi:token')) if redis_ok else False
    pbi_configured = all(CONFIG.values())
    return jsonify({
        'ok': True,
        'redis': 'ok' if redis_ok else 'down',
        'pbi_token': 'cached' if token_cached else 'cold',
        'pbi_configured': pbi_configured,
    })


def _get_dataset_refresh(dataset_id=None, cache_key='multpel:pbi:refresh'):
    """Última atualização CONCLUÍDA de um dataset via refresh history do Power BI (mesmo método
    do projeto MultpelEstoque). Retorna {'end','end_fmt','in_progress'} ou None (degrada
    silenciosamente). Cache Redis 5min. `dataset_id`/`cache_key` permitem consultar o dataset
    META (separado do RCA) sem colidir de cache."""
    from datetime import datetime, timezone, timedelta
    ds = dataset_id or CONFIG['dataset_id']
    key = cache_key
    cached = _cache_get(key)
    if cached is not None:
        return cached or None
    out = None
    try:
        token = get_token_cached()
        url = (f"https://api.powerbi.com/v1.0/myorg/groups/{CONFIG['group_id']}"
               f"/datasets/{ds}/refreshes?$top=10")
        resp = requests.get(url, headers={'Authorization': f'Bearer {token}'}, timeout=30)
        resp.raise_for_status()
        rows = resp.json().get('value', [])
        in_progress = any(not r.get('endTime')
                          and (r.get('status') or '').lower() in ('unknown', 'inprogress', 'notstarted')
                          for r in rows)
        last = next((r for r in rows if r.get('status') == 'Completed' and r.get('endTime')), None)
        dtloc = None
        if last:
            s = (last['endTime'] or '').replace('Z', '').split('.')[0]  # descarta fração de seg
            try:
                dt = datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
                try:
                    from zoneinfo import ZoneInfo
                    dtloc = dt.astimezone(ZoneInfo('America/Sao_Paulo'))
                except Exception:
                    dtloc = dt.astimezone(timezone(timedelta(hours=-3)))  # Brasil é UTC-3 o ano todo
            except ValueError:
                dtloc = None
        if dtloc:
            out = {'end': dtloc.isoformat(),
                   'end_fmt': dtloc.strftime('%d/%m/%Y %H:%M'),
                   'in_progress': in_progress}
    except Exception as e:
        print(f"[pbi] refresh history indisponível ({e})")
    _cache_set(key, out or False, 'dax_lista')  # 300s; False = 'consultado, sem dado'
    return out


@app.route('/api/pbi/refresh')
@login_required
def api_pbi_refresh():
    """Data/hora da última atualização do dataset Power BI (pra exibir no topo do painel)."""
    return jsonify({'ok': True, 'refresh': _get_dataset_refresh()})


def _get_meta_refresh():
    """Última atualização concluída do dataset META (separado do RCA — refresh próprio, ~2h)."""
    return _get_dataset_refresh(META_DATASET_ID, 'multpel:pbi:refresh:meta')


def _meta_refresh_tag():
    """Identificador que muda a cada refresh do dataset META. Entra na chave de cache do
    realizado de metas: quando o BI atualiza, a chave muda e o realizado/projeção voltam a bater
    com o BI na hora (sem esperar o TTL). Sem refresh history disponível → bucket de 30min."""
    r = _get_meta_refresh()
    if r and r.get('end'):
        return str(r['end'])
    from datetime import datetime
    n = datetime.now()
    return n.strftime('%Y%m%d%H') + ('H' if n.minute >= 30 else 'L')


# ──────────────────────────────────────────────────────────────────────
# Dashboard Executivo — 6 endpoints
# Todos: @login_required + cache Redis 1h + RBAC aplicado (quando faz sentido)
# ──────────────────────────────────────────────────────────────────────


def _construir_filtro(periodo='mes_atual', supervisores=None):
    """Combina filtro temporal + RBAC (+ override de supervisores) pra usar em CALCULATE/FILTER."""
    base = filtro_periodo(periodo)
    rbac = aplicar_rbac_dax()
    f = f"{base} && {rbac}" if rbac else base
    sup = _frag_supervisores('FATURAMENTO_VENDAS', supervisores)
    return f"{f} && {sup}" if sup else f


def _supervisores_filtro():
    """Lê ?supervisor=CSV de códigos e retorna lista de ints — SÓ pra admin/viewer.
    Pra outros roles retorna None (o param é ignorado; RBAC da sessão já trava o escopo).
    Evita que vendedor/supervisor logado amplie acesso passando ?supervisor=X."""
    if session.get('role') not in ('admin', 'viewer'):
        return None
    raw = request.args.get('supervisor', '').strip()
    if not raw:
        return None
    ids = []
    for parte in raw.split(','):
        parte = parte.strip()
        if not parte:
            continue
        try:
            ids.append(int(parte))
        except (TypeError, ValueError):
            continue
    return sorted(set(ids)) or None


def _filtro_rbac_only():
    """Só fragmento RBAC (sem temporal). Vazio se admin."""
    return aplicar_rbac_dax()


def _primeira_linha(payload):
    """Pega a primeira linha do resultado executeQueries (estrutura padrão Power BI)."""
    try:
        return payload['results'][0]['tables'][0]['rows'][0]
    except (KeyError, IndexError):
        return {}


def _todas_linhas(payload):
    """Pega todas as linhas (sem clean_rows). Use clean_rows pra normalizar chaves."""
    try:
        return payload['results'][0]['tables'][0]['rows']
    except (KeyError, IndexError):
        return []


def _sup_cache_key(supervisores):
    """Componente de cache key pro filtro de supervisores (ordenado, '-' se vazio)."""
    return ','.join(str(s) for s in supervisores) if supervisores else '-'


def _yoy_query(supervisores=None):
    """DAX do YoY — SEMPRE recalculado 12m vs 12m_anterior alinhado RCA (Patch F.3),
    pra ser idêntico aos cards e à mesma conta global/por-supervisor (só muda o escopo).
    Não usa as medidas nativas [Crescimento Ano a Ano ...] porque elas usam a venda
    líquida antiga (devolução por DTSAIDA) que diverge ~1-2% do RCA do cliente, além de
    não aceitarem filtro de supervisor. Retorna 8 valores (atual+anterior das 4 métricas).
    `supervisores=None` → janela pura (+ RBAC da sessão); com lista → adiciona CODSUPERVISOR IN."""
    vl_at = expr_venda_liquida_rca('12m', supervisores)
    vl_an = expr_venda_liquida_rca('12m_anterior', supervisores)
    lu_at = expr_lucro_rca('12m', supervisores)
    lu_an = expr_lucro_rca('12m_anterior', supervisores)
    f_at = _construir_filtro('12m', supervisores)
    f_an = _construir_filtro('12m_anterior', supervisores)
    return f"""EVALUATE {{(
        {vl_at},
        {vl_an},
        {lu_at},
        {lu_an},
        CALCULATE(DISTINCTCOUNT(FATURAMENTO_VENDAS[CODCLI]), FILTER(FATURAMENTO_VENDAS, {f_at})),
        CALCULATE(DISTINCTCOUNT(FATURAMENTO_VENDAS[CODCLI]), FILTER(FATURAMENTO_VENDAS, {f_an})),
        CALCULATE([TOTAL MIX], FILTER(FATURAMENTO_VENDAS, {f_at})),
        CALCULATE([TOTAL MIX], FILTER(FATURAMENTO_VENDAS, {f_an}))
    )}}"""


def _yoy_parse(row):
    """Transforma a linha do _yoy_query (8 valores atual/anterior) no dict de 4 percentuais."""
    def g(i):
        v = row.get(f'[Value{i}]')
        return v if v is not None else 0

    def yoy(atual, ant):
        return ((atual - ant) / ant) if ant else None

    return {
        'receita_liquida':     yoy(g(1), g(2)),
        'lucro_bruto':         yoy(g(3), g(4)),
        'positivacao_cliente': yoy(g(5), g(6)),
        'positivacao_mix':     yoy(g(7), g(8)),
    }


# ── YoY MENSAL (cards do Dashboard) ────────────────────────────────────────
# Os cards mostram o MÊS ATUAL, então o % embaixo deles tem que comparar MÊS com MÊS.
# Antes vinha do _yoy_query (12m vs 12m): um número que mal se mexe (a janela troca 1 dia
# de 365 por vez) e que chegou a ficar com o SINAL TROCADO em relação ao mês — jul/26
# marcava -6,7% no card enquanto o mês estava +10,6%. O 12m vs 12m continua vivo no
# gráfico YoY (/api/dashboard/yoy), onde o rótulo diz o que ele é.
MESES_ABREV = ('jan', 'fev', 'mar', 'abr', 'mai', 'jun',
               'jul', 'ago', 'set', 'out', 'nov', 'dez')


def _corte_dados():
    """Último dia COM DADO em FATURAMENTO_VENDAS (não TODAY()).

    O BI roda com atraso — em 20/07/26 o MAX(DTSAIDA) era 18/07. Ancorar em TODAY()
    compararia 18 dias deste ano contra 20 do ano passado e derrubaria o YoY ~10pp de
    graça. Cache curto: passa a valer assim que o dataset atualiza."""
    from datetime import date as _date
    key = 'multpel:corte_dados'
    cached = _cache_get(key)
    if cached:
        try:
            return _date.fromisoformat(cached)
        except (TypeError, ValueError):
            pass
    row = _primeira_linha(retry_dax(execute_dax)(
        get_token_cached(), 'EVALUATE ROW("d", MAX(FATURAMENTO_VENDAS[DTSAIDA]))'))
    bruto = row.get('[d]')
    if not bruto:
        return None
    corte = _date.fromisoformat(str(bruto)[:10])
    _cache_set(key, corte.isoformat(), 'dax_lista')
    return corte


def _janelas_yoy_mes():
    """Janelas do YoY mensal: (mês corrente 1→corte) vs (mesmo mês do ano anterior, 1→corte).

    Comparação por DIA DO MÊS — é a régua que o diretor consegue conferir na mão contra o
    RCA. Retorna None quando a comparação não é honesta:
      - sem corte (dataset vazio);
      - corte fora do mês corrente (virou o mês e o BI ainda não carregou nada) — aí o card
        mostra R$ 0 e qualquer % seria ruído.
    O dia é clampado ao fim do mês do ano anterior (29/02 → 28/02 em ano não bissexto)."""
    import calendar
    from datetime import date as _date
    corte = _corte_dados()
    hoje = _date.today()
    if corte is None or (corte.year, corte.month) != (hoje.year, hoje.month):
        return None
    ano, mes, dia = corte.year, corte.month, corte.day
    dia_ant = min(dia, calendar.monthrange(ano - 1, mes)[1])
    return {
        'atual':    (_date(ano, mes, 1), _date(ano, mes, dia)),
        'anterior': (_date(ano - 1, mes, 1), _date(ano - 1, mes, dia_ant)),
    }


def _dias_uteis_entre(ini, fim):
    """Dias úteis (seg-sex) no intervalo fechado. Só informativo — vai no tooltip do card
    pra explicar distorção de calendário (jul/26 teve 13 dias úteis até o dia 18; jul/25, 14)."""
    from datetime import timedelta as _td
    return sum(1 for n in range((fim - ini).days + 1) if (ini + _td(n)).weekday() < 5)


def _yoy_mes_query(janelas, supervisores=None):
    """Mesma estrutura do _yoy_query (8 valores), mas nas janelas MTD em vez de 12m."""
    t_at = f"range:{janelas['atual'][0].isoformat()}:{janelas['atual'][1].isoformat()}"
    t_an = f"range:{janelas['anterior'][0].isoformat()}:{janelas['anterior'][1].isoformat()}"
    f_at = _construir_filtro(t_at, supervisores)
    f_an = _construir_filtro(t_an, supervisores)
    return f"""EVALUATE {{(
        {expr_venda_liquida_rca(t_at, supervisores)},
        {expr_venda_liquida_rca(t_an, supervisores)},
        {expr_lucro_rca(t_at, supervisores)},
        {expr_lucro_rca(t_an, supervisores)},
        CALCULATE(DISTINCTCOUNT(FATURAMENTO_VENDAS[CODCLI]), FILTER(FATURAMENTO_VENDAS, {f_at})),
        CALCULATE(DISTINCTCOUNT(FATURAMENTO_VENDAS[CODCLI]), FILTER(FATURAMENTO_VENDAS, {f_an})),
        CALCULATE([TOTAL MIX], FILTER(FATURAMENTO_VENDAS, {f_at})),
        CALCULATE([TOTAL MIX], FILTER(FATURAMENTO_VENDAS, {f_an}))
    )}}"""


def _yoy_mes_meta(janelas):
    """Contexto do YoY mensal pro frontend: rótulo curto do card + detalhe do tooltip."""
    (ia, fa), (ip, fp) = janelas['atual'], janelas['anterior']
    du_at, du_an = _dias_uteis_entre(ia, fa), _dias_uteis_entre(ip, fp)
    return {
        'rotulo':      f"vs {MESES_ABREV[ip.month - 1]}/{ip.year % 100:02d}",
        'periodo':     f"{ia.day:02d}–{fa.day:02d}/{MESES_ABREV[ia.month - 1]}/{ia.year % 100:02d} "
                       f"vs {ip.day:02d}–{fp.day:02d}/{MESES_ABREV[ip.month - 1]}/{ip.year % 100:02d}",
        'dias_uteis':  du_at,
        'dias_uteis_anterior': du_an,
    }


@app.route('/api/dashboard/kpis')
@login_required
def api_dashboard_kpis():
    sup = _supervisores_filtro()
    key = cache_key_for_user('dashboard:kpis', {'supervisor': _sup_cache_key(sup)})
    cached = _cache_get(key)
    if cached:
        return jsonify(cached)

    f_atual = _construir_filtro('mes_atual', sup)
    # Patch G.2: VL e LUCRO alinhados RCA c/ RBAC nas 3 tabelas (vendedor/supervisor logado).
    # MARGEM e TICKET MEDIO recalculados em Python a partir do VL alinhado.
    vl_rca   = expr_venda_liquida_rca('mes_atual', sup)
    lucro_rca = expr_lucro_rca('mes_atual', sup)

    queries = {
        'primarios': f"""EVALUATE {{(
            {vl_rca},
            {lucro_rca},
            CALCULATE(DISTINCTCOUNT(FATURAMENTO_VENDAS[NUMTRANSVENDA]), {f_atual})
        )}}""",
        'secundarios': f"""EVALUATE {{(
            CALCULATE([TOTAL MIX], {f_atual}),
            CALCULATE([TOTAL CLIENTES NOVO], {f_atual}),
            CALCULATE([VALOR MEDIO PESO], {f_atual}),
            CALCULATE(DISTINCTCOUNT(FATURAMENTO_VENDAS[CODCLI]), {f_atual})
        )}}""",
        'yoy': _yoy_query(sup),
    }
    # YoY MENSAL (o % que vai embaixo dos cards de mês). None = comparação não honesta
    # (virada de mês sem carga no BI) → o frontend mostra '—' em vez de um número inventado.
    janelas = _janelas_yoy_mes()
    if janelas:
        queries['yoy_mes'] = _yoy_mes_query(janelas, sup)

    resultados = executar_dax_paralelo(queries)

    p = _primeira_linha(resultados['primarios'])
    s = _primeira_linha(resultados['secundarios'])
    y = _primeira_linha(resultados['yoy'])

    def _vals(row, n=4):
        return [row.get(f'[Value{i}]') for i in range(1, n+1)]

    vp = _vals(p, 3)
    vs = _vals(s)

    vl_val   = vp[0] or 0
    lucro_val = vp[1] or 0
    num_pedidos = vp[2] or 0
    margem_val   = (lucro_val / vl_val) if vl_val else 0
    ticket_val   = (vl_val / num_pedidos) if num_pedidos else 0

    resp = {
        'ok': True,
        'primarios': {
            'venda_liquida':  vl_val,
            'lucro_total':    lucro_val,
            'margem':         margem_val,
            'ticket_medio':   ticket_val,
        },
        'secundarios': {
            'total_mix':              vs[0],
            'clientes_novos':         vs[1],
            'valor_medio_peso':       vs[2],
            'clientes_positivados':   vs[3],
        },
        # 12m vs 12m — mantido pro gráfico YoY e por compatibilidade de contrato.
        'yoy': _yoy_parse(y),
        # Mês vs mesmo período do mês do ano anterior — é este que alimenta os cards.
        'yoy_mes': _yoy_parse(_primeira_linha(resultados['yoy_mes'])) if janelas else None,
        'yoy_mes_info': _yoy_mes_meta(janelas) if janelas else None,
    }
    _cache_set(key, resp, 'dax_agregado')
    return jsonify(resp)


@app.route('/api/dashboard/serie')
@login_required
def api_dashboard_serie():
    periodo = request.args.get('periodo', '12m')
    sup = _supervisores_filtro()
    key = cache_key_for_user('dashboard:serie', {'periodo': periodo, 'supervisor': _sup_cache_key(sup)})
    cached = _cache_get(key)
    if cached:
        return jsonify(cached)

    # Patch G.2: 3 queries paralelas (vendas + devolução + devolução avulsa)
    # com RBAC nas 3 tabelas, merge por AnoMes em Python. Alinha RCA.
    f_v, f_d, f_da = _construir_filtro_3tabelas(periodo, sup)

    queries = {
        'vendas': f"""EVALUATE
SUMMARIZECOLUMNS(
    CALENDARIO[AnoMes],
    FILTER(FATURAMENTO_VENDAS, {f_v}),
    "Bruta", [VENDA BRUTA],
    "Custo", [CUSTO TOTAL]
)""",
        'devol': f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_DEVOLUCAO[DTENT],
    FILTER(FATURAMENTO_DEVOLUCAO, {f_d}),
    "Devol", [TOTAL DEVOLUCAO],
    "CDevol", [CUSTO TOTAL DEVOLUCAO]
)""",
        'devol_av': f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_DEVOLUCAO_AVULSA[DTENT],
    FILTER(FATURAMENTO_DEVOLUCAO_AVULSA, {f_da}),
    "DevolA", [TOTAL DEVOLUCAO AVULSA],
    "CDevolA", [CUSTO TOTAL DEVOLUCAO AVULSA]
)""",
    }
    resultados = _executar_dax_paralelo_n(queries, max_workers=3)

    def _dtent_para_am(s):
        if not s: return None
        try: s = str(s)[:10]; return int(s[:4]) * 100 + int(s[5:7])
        except (ValueError, IndexError): return None

    # AnoMes → (bruta, custo)
    vendas_por_mes = {}
    for r in clean_rows(_todas_linhas(resultados['vendas'])):
        am = r.get('AnoMes')
        if am is None: continue
        vendas_por_mes[int(am)] = (r.get('Bruta') or 0, r.get('Custo') or 0)

    # AnoMes (via DTENT) → (devol, custo_devol)
    devol_por_mes = {}
    for r in clean_rows(_todas_linhas(resultados['devol'])):
        am = _dtent_para_am(r.get('DTENT'))
        if am is None: continue
        d, cd = devol_por_mes.get(am, (0, 0))
        devol_por_mes[am] = (d + (r.get('Devol') or 0), cd + (r.get('CDevol') or 0))

    devol_av_por_mes = {}
    for r in clean_rows(_todas_linhas(resultados['devol_av'])):
        am = _dtent_para_am(r.get('DTENT'))
        if am is None: continue
        d, cd = devol_av_por_mes.get(am, (0, 0))
        devol_av_por_mes[am] = (d + (r.get('DevolA') or 0), cd + (r.get('CDevolA') or 0))

    # Merge — universo de AnoMes vem das vendas (cada mês com transação real)
    todos_meses = sorted(set(vendas_por_mes.keys()) | set(devol_por_mes.keys()) | set(devol_av_por_mes.keys()))
    rows = []
    for am in todos_meses:
        b, ct = vendas_por_mes.get(am, (0, 0))
        dv, cdv = devol_por_mes.get(am, (0, 0))
        dva, cdva = devol_av_por_mes.get(am, (0, 0))
        vl = round(b - dv - dva, 2)
        lucro = round(vl - (ct - cdv - cdva), 2)
        rows.append({'AnoMes': am, 'VendaLiquida': vl, 'LucroTotal': lucro})

    resp = {'ok': True, 'rows': rows}
    _cache_set(key, resp, 'dax_agregado')
    return jsonify(resp)


@app.route('/api/dashboard/yoy')
@login_required
def api_dashboard_yoy():
    """YoY. Sem filtro: medidas nativas globais. Com supervisor(es): recalcula 12m vs 12m_anterior (RCA)."""
    sup = _supervisores_filtro()
    key = cache_key_for_user('dashboard:yoy', {'supervisor': _sup_cache_key(sup)})
    cached = _cache_get(key)
    if cached:
        return jsonify(cached)

    token = get_token_cached()
    payload = retry_dax(execute_dax)(token, _yoy_query(sup))
    row = _primeira_linha(payload)
    resp = {'ok': True, 'yoy': _yoy_parse(row)}
    _cache_set(key, resp, 'dax_agregado')
    return jsonify(resp)


@app.route('/api/dashboard/pareto')
@login_required
def api_dashboard_pareto():
    """Top N clientes por venda 12m. Frontend desenha cumulative."""
    try:
        top = max(5, min(int(request.args.get('top', 50)), 200))
    except (TypeError, ValueError):
        top = 50
    sup = _supervisores_filtro()
    key = cache_key_for_user('dashboard:pareto', {'top': top, 'supervisor': _sup_cache_key(sup)})
    cached = _cache_get(key)
    if cached:
        return jsonify(cached)

    # Patch G.2: 2 queries paralelas (bruta + devoluções por CODCLI), merge em Python.
    f_v, f_d, f_da = _construir_filtro_3tabelas('12m', sup)

    queries = {
        'vendas': f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODCLI],
    FATURAMENTO_VENDAS[CLIENTE],
    FATURAMENTO_VENDAS[UF],
    FILTER(FATURAMENTO_VENDAS, {f_v}),
    "Bruta", [VENDA BRUTA]
)""",
        'devol': f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_DEVOLUCAO[CODCLI],
    FILTER(FATURAMENTO_DEVOLUCAO, {f_d}),
    "Devol", [TOTAL DEVOLUCAO]
)""",
        'devol_av': f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_DEVOLUCAO_AVULSA[CODCLI],
    FILTER(FATURAMENTO_DEVOLUCAO_AVULSA, {f_da}),
    "DevolA", [TOTAL DEVOLUCAO AVULSA]
)""",
    }
    resultados = _executar_dax_paralelo_n(queries, max_workers=3)

    devol_por_cli = {}
    for r in clean_rows(_todas_linhas(resultados['devol'])):
        cc = r.get('CODCLI')
        if cc is None: continue
        devol_por_cli[int(cc)] = devol_por_cli.get(int(cc), 0) + (r.get('Devol') or 0)
    for r in clean_rows(_todas_linhas(resultados['devol_av'])):
        cc = r.get('CODCLI')
        if cc is None: continue
        devol_por_cli[int(cc)] = devol_por_cli.get(int(cc), 0) + (r.get('DevolA') or 0)

    rows_completos = []
    for r in clean_rows(_todas_linhas(resultados['vendas'])):
        cc = r.get('CODCLI')
        b = r.get('Bruta') or 0
        d = devol_por_cli.get(int(cc), 0) if cc is not None else 0
        vl = round(b - d, 2)
        rows_completos.append({
            'CODCLI':  cc,
            'CLIENTE': r.get('CLIENTE'),
            'UF':      r.get('UF'),
            'Venda12m': vl,
        })

    rows_completos.sort(key=lambda x: x['Venda12m'], reverse=True)
    rows = rows_completos[:top]
    resp = {'ok': True, 'rows': rows, 'top': top}
    _cache_set(key, resp, 'dax_agregado')
    return jsonify(resp)


def _carregar_sazonalidade(role='admin', codusur=None, codsupervisor=None, supervisores=None):
    """Carrega sazonalidade 24m. Aceita role/usur/supv explícitos pra permitir prewarm
    sem request context. `supervisores` (lista) = override de filtro selecionado (admin/viewer).
    Cache key compatível com cache_key_for_user."""
    # RBAC supervisor como lista (multi-área). codsupervisor aceita int único OU lista.
    rbac_sups = _como_lista_supervisores(codsupervisor)

    key = ':'.join([
        'multpel', 'dashboard:sazonalidade',
        f"role={role or 'anon'}",
        f"usur={codusur if codusur is not None else '-'}",
        f"supv={','.join(str(s) for s in rbac_sups) if rbac_sups else '-'}",
        f"supsel={_sup_cache_key(supervisores)}",
    ])
    cached = _cache_get(key)
    if cached:
        return cached

    # Patch G.2: RBAC nas 3 tabelas. Aceita params explícitos (prewarm sem session).
    def _rbac_frag(tabela, col):
        if role == 'admin': return ''
        if codusur is not None:        return f" && {tabela}[{col}] = {int(codusur)}"
        frag = _frag_supervisores(tabela, rbac_sups)
        return f" && {frag}" if frag else ''
    rbac_v   = _rbac_frag('FATURAMENTO_VENDAS', 'CODUSUR')
    rbac_d   = _rbac_frag('FATURAMENTO_DEVOLUCAO', 'CODUSUR')
    rbac_da  = _rbac_frag('FATURAMENTO_DEVOLUCAO_AVULSA', 'CODUSUR')
    sup_v  = _frag_supervisores('FATURAMENTO_VENDAS', supervisores)
    sup_d  = _frag_supervisores('FATURAMENTO_DEVOLUCAO', supervisores)
    sup_da = _frag_supervisores('FATURAMENTO_DEVOLUCAO_AVULSA', supervisores)
    if sup_v:  rbac_v  = f"{rbac_v} && {sup_v}"
    if sup_d:  rbac_d  = f"{rbac_d} && {sup_d}"
    if sup_da: rbac_da = f"{rbac_da} && {sup_da}"
    f_v  = f"FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -24){rbac_v}"
    f_d  = f"FATURAMENTO_DEVOLUCAO[DTENT] >= EDATE(TODAY(), -24){rbac_d}"
    f_da = f"FATURAMENTO_DEVOLUCAO_AVULSA[DTENT] >= EDATE(TODAY(), -24){rbac_da}"

    queries = {
        'vendas': f"""EVALUATE
SUMMARIZECOLUMNS(
    CALENDARIO[Ano],
    CALENDARIO[MES],
    FILTER(FATURAMENTO_VENDAS, {f_v}),
    "Bruta", [VENDA BRUTA]
)""",
        'devol': f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_DEVOLUCAO[DTENT],
    FILTER(FATURAMENTO_DEVOLUCAO, {f_d}),
    "Devol", [TOTAL DEVOLUCAO]
)""",
        'devol_av': f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_DEVOLUCAO_AVULSA[DTENT],
    FILTER(FATURAMENTO_DEVOLUCAO_AVULSA, {f_da}),
    "DevolA", [TOTAL DEVOLUCAO AVULSA]
)""",
    }
    resultados = _executar_dax_paralelo_n(queries, max_workers=3)

    def _dtent_para_ano_mes(s):
        if not s: return (None, None)
        try: s = str(s)[:10]; return (int(s[:4]), int(s[5:7]))
        except (ValueError, IndexError): return (None, None)

    # (Ano, Mes) → bruta
    vendas_por_am = {}
    for r in clean_rows(_todas_linhas(resultados['vendas'])):
        ano = r.get('Ano')
        mes = r.get('MES')
        if ano is None or mes is None: continue
        vendas_por_am[(int(ano), int(mes))] = r.get('Bruta') or 0

    # (Ano, Mes) → devolução
    devol_por_am = {}
    for r in clean_rows(_todas_linhas(resultados['devol'])):
        ano, mes = _dtent_para_ano_mes(r.get('DTENT'))
        if ano is None: continue
        devol_por_am[(ano, mes)] = devol_por_am.get((ano, mes), 0) + (r.get('Devol') or 0)
    for r in clean_rows(_todas_linhas(resultados['devol_av'])):
        ano, mes = _dtent_para_ano_mes(r.get('DTENT'))
        if ano is None: continue
        devol_por_am[(ano, mes)] = devol_por_am.get((ano, mes), 0) + (r.get('DevolA') or 0)

    rows = []
    for (ano, mes) in sorted(set(vendas_por_am.keys()) | set(devol_por_am.keys())):
        b = vendas_por_am.get((ano, mes), 0)
        d = devol_por_am.get((ano, mes), 0)
        rows.append({'Ano': ano, 'MES': mes, 'VendaLiquida': round(b - d, 2)})

    resp = {'ok': True, 'rows': rows}
    _cache_set(key, resp, 'dax_agregado')
    return resp


@app.route('/api/dashboard/sazonalidade')
@login_required
def api_dashboard_sazonalidade():
    """Venda mensal x ano (últimos 24m). Frontend pivota mes/ano."""
    return jsonify(_carregar_sazonalidade(
        role=session.get('role'),
        codusur=session.get('codusur'),
        codsupervisor=_session_supervisores(),  # RBAC multi-área (lista)
        supervisores=_supervisores_filtro(),
    ))


@app.route('/api/dashboard/top-clientes')
@login_required
def api_dashboard_top_clientes():
    """Top N clientes por lucro/venda nos últimos 12m."""
    metrica = request.args.get('metrica', 'lucro')
    try:
        limit = max(3, min(int(request.args.get('limit', 10)), 100))
    except (TypeError, ValueError):
        limit = 10

    if metrica not in ('lucro', 'venda'):
        metrica = 'lucro'

    sup = _supervisores_filtro()
    key = cache_key_for_user('dashboard:top-clientes', {'metrica': metrica, 'limit': limit, 'supervisor': _sup_cache_key(sup)})
    cached = _cache_get(key)
    if cached:
        return jsonify(cached)

    # Patch G.2: 2 queries paralelas (vendas + devoluções por CODCLI), merge em Python.
    f_v, f_d, f_da = _construir_filtro_3tabelas('12m', sup)

    queries = {
        'vendas': f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODCLI],
    FATURAMENTO_VENDAS[CLIENTE],
    FATURAMENTO_VENDAS[UF],
    FATURAMENTO_VENDAS[CODUSUR],
    FILTER(FATURAMENTO_VENDAS, {f_v}),
    "Bruta", [VENDA BRUTA],
    "Custo", [CUSTO TOTAL]
)""",
        'devol': f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_DEVOLUCAO[CODCLI],
    FILTER(FATURAMENTO_DEVOLUCAO, {f_d}),
    "Devol", [TOTAL DEVOLUCAO],
    "CDevol", [CUSTO TOTAL DEVOLUCAO]
)""",
        'devol_av': f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_DEVOLUCAO_AVULSA[CODCLI],
    FILTER(FATURAMENTO_DEVOLUCAO_AVULSA, {f_da}),
    "DevolA", [TOTAL DEVOLUCAO AVULSA],
    "CDevolA", [CUSTO TOTAL DEVOLUCAO AVULSA]
)""",
    }
    resultados = _executar_dax_paralelo_n(queries, max_workers=3)

    # CODCLI → (devol, custo_devol) somando avulsa
    devol_por_cli = {}
    for r in clean_rows(_todas_linhas(resultados['devol'])):
        cc = r.get('CODCLI')
        if cc is None: continue
        d, cd = devol_por_cli.get(int(cc), (0, 0))
        devol_por_cli[int(cc)] = (d + (r.get('Devol') or 0), cd + (r.get('CDevol') or 0))
    for r in clean_rows(_todas_linhas(resultados['devol_av'])):
        cc = r.get('CODCLI')
        if cc is None: continue
        d, cd = devol_por_cli.get(int(cc), (0, 0))
        devol_por_cli[int(cc)] = (d + (r.get('DevolA') or 0), cd + (r.get('CDevolA') or 0))

    rows_completos = []
    for r in clean_rows(_todas_linhas(resultados['vendas'])):
        cc = r.get('CODCLI')
        b = r.get('Bruta') or 0
        ct = r.get('Custo') or 0
        dv, cdv = devol_por_cli.get(int(cc), (0, 0)) if cc is not None else (0, 0)
        vl = round(b - dv, 2)
        lucro = round(vl - (ct - cdv), 2)
        rows_completos.append({
            'CODCLI':  cc,
            'CLIENTE': r.get('CLIENTE'),
            'UF':      r.get('UF'),
            'CODUSUR': r.get('CODUSUR'),
            'Venda12m': vl,
            'Lucro12m': lucro,
        })

    sort_key = 'Lucro12m' if metrica == 'lucro' else 'Venda12m'
    rows_completos.sort(key=lambda x: x[sort_key], reverse=True)
    rows = rows_completos[:limit]
    resp = {'ok': True, 'rows': rows, 'metrica': metrica}
    _cache_set(key, resp, 'dax_agregado')
    return jsonify(resp)


# ──────────────────────────────────────────────────────────────────────
# Carteira RFM — 6 endpoints + helper interno
# Schema PCCLIENT validado em 2026-05-23 (passo 3b plano Onda B):
#   CLIENTE, FANTASIA, MUNICENT (cidade entrega), MUNICCOB (cidade cobr.),
#   ESTENT (UF), TELCOB (preferencial), TELENT (fallback), BLOQUEIO, CODUSUR1
# ──────────────────────────────────────────────────────────────────────

import rfm  # módulo puro de RFM
import cobertura as cob  # módulo puro de Cobertura de Carteira (página Gerencial)

VENDEDORES_TECNICOS = {999, 900, 4, 272}  # excluir das listas de vendedor (técnicos)


@app.route('/carteira')
@login_required
def carteira_page():
    return send_from_directory('.', 'carteira.html')


# ─────────────── DEMO/APRESENTAÇÃO: apelidos fictícios de time (vend/superv) ───────────────
# TEMPORÁRIO, só para apresentação: troca TODO nome de vendedor/supervisor por um nome fictício
# determinístico (mesmo código → sempre o mesmo nome). NÃO afeta cálculo/RBAC (só o rótulo);
# como quase tudo resolve o nome pelos dois mapas + os 2 pontos que leem NOME direto do DAX
# (perfil e metas-por-time) também estão mascarados, a troca é consistente em todas as telas.
# >>> REVERTER: TIME_DEMO = False + redeploy + LIMPAR O REDIS (o cache de payloads sobrevive
#     ao redeploy; sem o flush os nomes reais ficam cacheados por até 24h). <<<
TIME_DEMO = False
_TDEMO_FIRST = ["Carlos", "Beatriz", "Rafael", "Fernanda", "Gustavo", "Patrícia", "André",
                "Juliana", "Marcelo", "Renata", "Thiago", "Camila", "Bruno", "Larissa",
                "Diego", "Vanessa"]
_TDEMO_LAST = ["Andrade", "Lima", "Monteiro", "Rocha", "Teixeira", "Nunes", "Carvalho", "Prado",
               "Fontes", "Barros", "Azevedo", "Duarte", "Siqueira", "Campos", "Ramalho", "Moraes"]


def _demo_nome_time(codigo, sup=False):
    """Nome fictício estável por código (256 combinações). `sup=True` desloca a sequência (+7)
    p/ um supervisor e um vendedor de código igual não caírem no mesmo nome no drill de metas."""
    try:
        m = int(codigo) + (7 if sup else 0)
    except (TypeError, ValueError):
        return "Equipe Comercial"
    return f"{_TDEMO_FIRST[m % len(_TDEMO_FIRST)]} {_TDEMO_LAST[(m // len(_TDEMO_LAST)) % len(_TDEMO_LAST)]}"


def _carregar_supervisores_map():
    """Retorna {codsupervisor_str: {nome, tipo}} via PCSUPERV. Cache 24h.
    37 supervisores reais com nomes (pessoas + canais como DIRETORIA, TELEMARKETING, BALCÃO MULTPEL, etc)."""
    key = 'multpel:supervisores_map:v1'
    cached = _cache_get(key)
    if cached:
        return cached
    query = """EVALUATE
SUMMARIZECOLUMNS(
    PCSUPERV[CODSUPERVISOR],
    PCSUPERV[NOME],
    PCSUPERV[TIPOSUPERVISOR]
)"""
    token = get_token_cached()
    payload = retry_dax(execute_dax)(token, query)
    rows = clean_rows(_todas_linhas(payload))
    mapa = {}
    for r in rows:
        cs = r.get('CODSUPERVISOR')
        if cs is None:
            continue
        mapa[str(cs)] = {
            # DEMO: mascara o nome do time (ver bloco TIME_DEMO acima). Reverter = False.
            'nome': _demo_nome_time(cs, sup=True) if TIME_DEMO else (r.get('NOME') or f'Time {cs}'),
            'tipo': r.get('TIPOSUPERVISOR'),
        }
    _cache_set(key, mapa, 'metadata')
    return mapa


def _carregar_vendedores_map():
    """Retorna {codusur_str: {nome, codsupervisor, tipo, cidade, estado, bloqueio}}.
    Cache Redis 24h. Exclui técnicos (999/900/4/272) automaticamente.
    BLOQUEIO/CIDADE/ESTADO vêm pra permitir filtros downstream (ranking exclui BLOQUEIO='S'
    sempre; tela /vendedores permite filtrar por estado)."""
    key = 'multpel:vendedores_map:v2'  # v2: incluiu BLOQUEIO/CIDADE/ESTADO
    cached = _cache_get(key)
    if cached:
        return cached
    query = """EVALUATE
SUMMARIZECOLUMNS(
    PCUSUARI[CODUSUR],
    PCUSUARI[NOME],
    PCUSUARI[CODSUPERVISOR],
    PCUSUARI[TIPOVEND],
    PCUSUARI[CIDADE],
    PCUSUARI[ESTADO],
    PCUSUARI[BLOQUEIO]
)"""
    token = get_token_cached()
    payload = retry_dax(execute_dax)(token, query)
    rows = clean_rows(_todas_linhas(payload))
    mapa = {}
    for r in rows:
        codusur = r.get('CODUSUR')
        if codusur is None or codusur in VENDEDORES_TECNICOS:
            continue
        mapa[str(codusur)] = {
            # DEMO: mascara o nome do vendedor (ver bloco TIME_DEMO acima). Reverter = False.
            'nome':           _demo_nome_time(codusur) if TIME_DEMO else (r.get('NOME') or f'RCA {codusur}'),
            'codsupervisor':  r.get('CODSUPERVISOR'),
            'tipo':           r.get('TIPOVEND'),
            'cidade':         r.get('CIDADE'),
            'estado':         r.get('ESTADO'),
            'bloqueio':       r.get('BLOQUEIO'),
        }
    _cache_set(key, mapa, 'metadata')
    return mapa


def pode_acessar_vendedor(codusur_alvo):
    """True se user logado pode ver dados do codusur_alvo.
    - admin/viewer: sempre
    - vendedor: só o próprio codusur
    - supervisor: só se codusur_alvo estiver no time (CODSUPERVISOR igual)
    Demais: False.
    """
    role = session.get('role')
    if role in ('admin', 'viewer'):
        return True
    if role == 'vendedor':
        try:
            return int(session.get('codusur') or 0) == int(codusur_alvo)
        except (TypeError, ValueError):
            return False
    if role == 'supervisor':
        meus_sups = _session_supervisores()
        if not meus_sups:
            return False
        vmap = _carregar_vendedores_map()
        v = vmap.get(str(codusur_alvo))
        if not v:
            return False
        try:
            return int(v.get('codsupervisor') or 0) in set(meus_sups)
        except (TypeError, ValueError):
            return False
    return False


@app.route('/api/_internal/vendedores-map')
@login_required
def api_vendedores_map():
    mapa = _carregar_vendedores_map()
    return jsonify({'ok': True, 'vendedores': mapa})


@app.route('/api/_internal/supervisores-map')
@login_required
def api_supervisores_map():
    """{codsupervisor_str: {nome, tipo}}. Cache 24h. Usado em selects de filtro de Time."""
    return jsonify({'ok': True, 'supervisores': _carregar_supervisores_map()})


@app.route('/api/_internal/compradores-map')
@admin_required
def api_compradores_map():
    """{matricula: nome} dos compradores, pro Admin vincular usuário ↔ comprador.

    Fica aqui (e não no blueprint /estoque) de propósito: o Admin é tela do Comercial e o
    administrador pode não ter a área Compras — sob /estoque a guarda o barraria com 403.

    Usa `compradores_reais()`, a MESMA derivação da tela do módulo. Usar o mapa cru da PCEMPR
    trazia a folha inteira (vendedores incluídos) para um campo que pede comprador."""
    try:
        from estoque.routes import compradores_reais
        lista = compradores_reais()
    except Exception as e:
        # Sem Power BI o Admin ainda tem que abrir; o campo só fica sem sugestões.
        print(f"[admin] compradores-map indisponível: {e}")
        return jsonify({'ok': True, 'compradores': {}, 'aviso': 'lista indisponível'})
    return jsonify({'ok': True,
                    'compradores': {str(c['codcomprador']): c['comprador'] for c in lista}})


@app.route('/api/_internal/relatorios-estoque')
@admin_required
def api_relatorios_estoque():
    """Catálogo de relatórios de Compras que podem ir por email (fonte única: estoque/relatorios.py)."""
    from estoque import relatorios as rel_estoque
    return jsonify({'ok': True, 'relatorios': rel_estoque.catalogo()})


@app.route('/api/_internal/supervisores-ativos')
@login_required
def api_supervisores_ativos():
    """Códigos de supervisor que têm CLIENTE na carteira (mesma régua da tela /carteira:
    `times_ativos`). Diferente do supervisores-map (PCSUPERV inteiro): exclui códigos
    'fantasma' sem carteira por trás. Usado no Admin pra listar só áreas atribuíveis."""
    ativos = sorted({c['codsupervisor'] for c in _carteira_no_escopo()
                     if c.get('codsupervisor') is not None})
    return jsonify({'ok': True, 'ativos': ativos})


@app.route('/api/_internal/clientes-busca')
@login_required
def api_clientes_busca():
    """Type-ahead de cliente DENTRO do escopo do usuário (usa _carteira_no_escopo → RBAC de
    graça: cliente fora do cadastro simplesmente não aparece). Casa por trecho do nome/fantasia
    OU código. Ordena por venda 12m desc. Compartilhado por Mix e Radar (busca por cliente)."""
    q = (request.args.get('q') or '').strip().lower()
    so_digitos = q.isdigit()   # número → busca por CÓDIGO; texto → busca por NOME
    try:
        limit = max(1, min(int(request.args.get('limit', 20)), 50))
    except (TypeError, ValueError):
        limit = 20

    clientes = _carteira_no_escopo()
    out = []
    for c in clientes:
        if q:
            cod = str(c.get('codcli') or '')
            if so_digitos:
                # Código: PREFIXO (não "no meio") → "222" acha 222/2225, não 12227.
                if not cod.startswith(q):
                    continue
            else:
                nome = (c.get('cliente') or '').lower()
                fant = (c.get('fantasia') or '').lower()
                if q not in nome and q not in fant:
                    continue
        out.append({
            'codcli':    c.get('codcli'),
            'cliente':   c.get('cliente') or f"Cliente #{c.get('codcli')}",
            'cidade':    c.get('cidade'),
            'uf':        c.get('uf'),
            'vendedor':  c.get('vendedor'),
            'codusur':   c.get('codusur'),
            'time':      c.get('time'),
            'telefone':  c.get('telefone'),
            'venda_12m': c.get('venda_12m') or 0,
        })
    if so_digitos and q:
        # Código exato primeiro, depois prefixos por venda desc.
        out.sort(key=lambda x: (str(x['codcli']) != q, -(x['venda_12m'] or 0)))
    else:
        out.sort(key=lambda x: x['venda_12m'], reverse=True)
    return jsonify({'ok': True, 'total': len(out), 'clientes': out[:limit]})


def _construir_filtro_carteira(extra_dax=None):
    """Concatena RBAC + filtro extra opcional (já com sintaxe DAX)."""
    rbac = aplicar_rbac_dax()
    partes = [p for p in (extra_dax, rbac) if p]
    return ' && '.join(partes) if partes else None


def _normalizar_cidades(clientes):
    """Normaliza nomes de cidade duplicados/truncados do DB.

    PCCLIENT.MUNICENT vem com:
    - Truncamentos a 15 chars: 'CAMPOS DOS GOYT', 'CACHOEIRO DE IT', 'BOM JESUS DO NO'
    - Versões completas: 'CAMPOS DOS GOYTACAZES', 'CACHOEIRO DE ITAPEMIRIM'
    - Typos: 'ITAPEMIRIRM' (R extra) coexistindo com 'ITAPEMIRIM'

    Estratégia em 2 passes:
    A) Prefix collapse: 'A' é prefixo de 'B' (mesmo UF) e B[len(A)] NÃO é espaço
       (= truncamento mid-word, não nome diferente) → 'A' vira 'B'.
    B) Fuzzy match: cidades sobreviventes do pass A com similaridade >= 0.92 no
       mesmo UF → variante mais frequente vence (typos colapsam pra forma canônica).

    Modifica clientes in-place. Retorna mapping pra log/debug."""
    from collections import Counter, defaultdict
    from difflib import SequenceMatcher

    contador = Counter()
    for c in clientes:
        cidade = (c.get('cidade') or '').strip()
        uf = (c.get('uf') or '').strip().upper()
        if cidade and uf:
            contador[(uf, cidade)] += 1

    por_uf = defaultdict(list)
    for (uf, cidade), n in contador.items():
        por_uf[uf].append((cidade, n))

    mapping = {}  # (uf, origem) → canonica

    # PASS 1 — fuzzy match (typos). Roda PRIMEIRO pra collapse não inflar contador
    # do prefix-collapse downstream. Ex: 'ITAPEMIRIM' vs 'ITAPEMIRIRM' (1 char diff) →
    # mais popular absorve o typo. Depois 'IT' achará só 1 candidato canônico.
    for uf, lista in por_uf.items():
        nomes_ord = sorted(lista, key=lambda x: -x[1])  # mais popular primeiro
        for i, (a, _) in enumerate(nomes_ord):
            for b, _ in nomes_ord[i + 1:]:
                if (uf, b) in mapping:
                    continue
                if abs(len(a) - len(b)) <= 3 and min(len(a), len(b)) >= 6:
                    ratio = SequenceMatcher(None, a, b).ratio()
                    if ratio >= 0.92:
                        mapping[(uf, b)] = a  # 'a' (mais popular) vence

    # Recalcula popularidade pós-pass1 (typos já mergeados)
    pop_pos1 = Counter()
    for (uf, cidade), n in contador.items():
        destino = mapping.get((uf, cidade), cidade)
        pop_pos1[(uf, destino)] += n

    # PASS 2 — prefix collapse (truncamentos mid-word).
    # Desempate por MAIOR POPULARIDADE (não maior tamanho) — assim 'CACHOEIRO DE IT'
    # cai pra 'CACHOEIRO DE ITAPEMIRIM' (mais clientes) e não pra typo 'ITAPEMIRIRM'.
    for uf, lista in por_uf.items():
        nomes = [c for c, _ in lista]
        # Só considera "destinos canônicos" (não mergeados pela pass 1) como candidatos longos
        canonicas = [c for c in nomes if (uf, c) not in mapping]
        for curta in nomes:
            if (uf, curta) in mapping:
                continue  # já mergeado pela pass 1
            melhor = None
            melhor_pop = -1
            for longa in canonicas:
                if (len(longa) > len(curta)
                        and longa.startswith(curta)
                        and not longa[len(curta)].isspace()):
                    pop_l = pop_pos1[(uf, longa)]
                    if pop_l > melhor_pop or (pop_l == melhor_pop and len(longa) > (len(melhor) if melhor else 0)):
                        melhor = longa
                        melhor_pop = pop_l
            if melhor:
                mapping[(uf, curta)] = melhor

    # Aplica
    for c in clientes:
        cidade = (c.get('cidade') or '').strip()
        uf = (c.get('uf') or '').strip().upper()
        if (uf, cidade) in mapping:
            c['cidade'] = mapping[(uf, cidade)]

    return mapping


def _carregar_carteira_full():
    """Roda 6 queries DAX em paralelo, processa via rfm.calcular_clientes(),
    retorna a carteira GLOBAL (todos os clientes, métricas TOTAIS — sem filtro de venda).

    O recorte por usuário NÃO é feito aqui: use sempre _carteira_no_escopo(), que filtra
    pelo CADASTRO (CODUSUR1 → vendedor → supervisor). Cache compartilhado entre todos os
    usuários (1 entrada global). NÃO expor esta função direto nos endpoints."""
    key = 'multpel:carteira:full:global:v2'
    cached = _cache_get(key)
    if cached:
        return cached

    # Carteira global: sem RBAC de venda. Isolamento é por cadastro em _carteira_no_escopo().
    rbac_frag = ""

    # 7 queries em paralelo (snapshot quebrado em 2 pra evitar estourar 1.3GB):
    # - snapshot_rec: só recência (universo 24m)
    # - snapshot_freqmon: só freq+monetary (universo 12m, subset)
    # - 4 chunks de datas (12m em 4 trimestres)
    # - meta (PCCLIENT)
    queries = {
        'snapshot_rec': f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODCLI],
    FILTER(FATURAMENTO_VENDAS, FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -24){rbac_frag}),
    "UltimaCompra", MAX(FATURAMENTO_VENDAS[DTSAIDA])
)""",
        'snapshot_freqmon': f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODCLI],
    FILTER(FATURAMENTO_VENDAS, FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12){rbac_frag}),
    "Compras12m", DISTINCTCOUNT(FATURAMENTO_VENDAS[NUMNOTA]),
    "Lucro12m",   [LUCRO TOTAL],
    "Venda12m",   [VENDA LIQUIDA]
)""",
        # ATENÇÃO: NÃO adicionar mais colunas aqui sem cuidado. O executeQueries trunca a
        # resposta por tamanho (~41k clientes × colunas). Com 4 colunas de telefone caía pra
        # ~40.735 linhas → ~260 clientes ativos vinham SEM cadastro (nome/cidade/vendedor em
        # branco). Mantemos só 2 telefones (TELCELENT celular + TELENT ~97% cobertura).
        'meta': """EVALUATE
SUMMARIZECOLUMNS(
    PCCLIENT[CODCLI], PCCLIENT[CLIENTE], PCCLIENT[FANTASIA],
    PCCLIENT[MUNICENT], PCCLIENT[MUNICCOB], PCCLIENT[ESTENT],
    PCCLIENT[TELCELENT], PCCLIENT[TELENT],
    PCCLIENT[CODUSUR1], PCCLIENT[BLOQUEIO]
)""",
    }
    # Chunking datas: 4 trimestres
    chunks = [(-3, 0), (-6, -3), (-9, -6), (-12, -9)]
    for idx, (ini, fim) in enumerate(chunks, start=1):
        fim_clausula = "" if fim == 0 else f" && FATURAMENTO_VENDAS[DTSAIDA] < EDATE(TODAY(), {fim})"
        queries[f'datas_t{idx}'] = f"""EVALUATE
SELECTCOLUMNS(
    FILTER(FATURAMENTO_VENDAS, FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), {ini}){fim_clausula}{rbac_frag}),
    "CodCli", FATURAMENTO_VENDAS[CODCLI],
    "Data",   FATURAMENTO_VENDAS[DTSAIDA]
)"""

    resultados = _executar_dax_paralelo_n(queries, max_workers=7)

    # Processa snapshot: merge rec (24m) + freqmon (12m). Clientes em rec mas
    # não em freqmon = hibernating (compraram 12-24m atrás, nada nos últimos 12m).
    from datetime import date as _date, datetime as _dt
    rec_rows = clean_rows(_todas_linhas(resultados['snapshot_rec']))
    freqmon_rows = clean_rows(_todas_linhas(resultados['snapshot_freqmon']))
    freqmon_idx = {r['CODCLI']: r for r in freqmon_rows if r.get('CODCLI') is not None}

    snapshot_rows = []
    hoje = _date.today()
    for r in rec_rows:
        cc = r.get('CODCLI')
        if cc is None:
            continue
        ultima = r.get('UltimaCompra')
        ultima_str = str(ultima)[:10] if ultima else None
        try:
            dias = (hoje - _date.fromisoformat(ultima_str)).days if ultima_str else None
        except ValueError:
            dias = None
        fm = freqmon_idx.get(cc, {})
        snapshot_rows.append({
            'CODCLI':         cc,
            'UltimaCompra':   ultima_str,
            'DiasSemComprar': dias if dias is not None else 999,
            'Compras12m':     fm.get('Compras12m') or 0,
            'Lucro12m':       fm.get('Lucro12m')   or 0.0,
            'Venda12m':       fm.get('Venda12m')   or 0.0,
        })

    # Processa datas concatenando 4 chunks → dict {codcli: [datas]}
    datas_por_cliente = {}
    for idx in range(1, 5):
        rows = clean_rows(_todas_linhas(resultados[f'datas_t{idx}']))
        for r in rows:
            cc = r.get('CodCli')
            d = r.get('Data')
            if cc is None or d is None:
                continue
            datas_por_cliente.setdefault(cc, []).append(d)

    # Processa meta
    meta_rows = clean_rows(_todas_linhas(resultados['meta']))
    meta_por_cliente = {}
    for r in meta_rows:
        cc = r.get('CODCLI')
        if cc is None:
            continue
        meta_por_cliente[cc] = {
            'cliente':   r.get('CLIENTE'),
            'fantasia':  r.get('FANTASIA'),
            'cidade':    r.get('MUNICENT') or r.get('MUNICCOB'),
            'uf':        r.get('ESTENT'),
            # Prefere celular (TELCELENT) p/ o vendedor ligar/WhatsApp; cai p/ entrega (TELENT,
            # ~97% de cobertura). Só 2 colunas de telefone pra não truncar a resposta (ver acima).
            'telefone':  (r.get('TELCELENT') or r.get('TELENT') or '').strip() or None,
            'codusur1':  r.get('CODUSUR1'),
            'bloqueio':  r.get('BLOQUEIO'),
        }

    # Enriquece via módulo puro rfm.py
    clientes = rfm.calcular_clientes(snapshot_rows, datas_por_cliente, meta_por_cliente)

    # Enriquece com vendedor + time (supervisor) via lookups
    vendedores = _carregar_vendedores_map()
    supervisores = _carregar_supervisores_map()
    for c in clientes:
        cu = c.get('codusur')
        if cu is not None:
            v = vendedores.get(str(cu))
            if v:
                c['vendedor'] = v.get('nome') or f'RCA {cu}'
                cs = v.get('codsupervisor')
                c['codsupervisor'] = cs
                sup = supervisores.get(str(cs)) if cs is not None else None
                c['time'] = (sup.get('nome') if sup else f'Time {cs}') if cs is not None else None
            else:
                c['vendedor'] = f'RCA {cu}'
                c['codsupervisor'] = None
                c['time'] = None
        else:
            c['vendedor'] = None
            c['codsupervisor'] = None
            c['time'] = None

    # Normaliza cidades duplicadas/truncadas (PCCLIENT.MUNICENT vem inconsistente)
    norm_map = _normalizar_cidades(clientes)
    if norm_map:
        print(f"[CARTEIRA] Normalizou {len(norm_map)} cidades: {list(norm_map.items())[:5]}...")

    _cache_set(key, clientes, 'dax_agregado')
    return clientes


def _carteira_no_escopo():
    """Carteira GLOBAL recortada pelo escopo de CADASTRO do usuário logado (CODUSUR1).
    É a ÚNICA porta que os endpoints devem usar — garante o isolamento em Python:
    - admin/viewer  → tudo
    - vendedor      → clientes cujo CODUSUR1 == seu codusur (registrados nele)
    - supervisor    → clientes cujo CODUSUR1 pertence a uma de suas áreas
    - supervisor sem área / role desconhecido → []
    Métricas são as TOTAIS do cliente (a carteira full é global, sem filtro de venda)."""
    clientes = _carregar_carteira_full()
    role = session.get('role')
    if role in ('admin', 'viewer'):
        return clientes
    codusur = session.get('codusur')
    if codusur is not None:
        try:
            cu = int(codusur)
        except (TypeError, ValueError):
            return []
        return [c for c in clientes if c.get('codusur') == cu]
    sups = set(_session_supervisores())
    if sups:
        return [c for c in clientes if c.get('codsupervisor') in sups]
    return []


def _frag_codcli_cadastro():
    """Fragmento DAX que restringe FATURAMENTO_VENDAS aos clientes do escopo de CADASTRO
    do usuário (pra agregados que não dá pra filtrar em memória, ex.: Categorias).
    Retorna (frag, ok):
    - admin/viewer → ('', True): sem restrição (agrega tudo).
    - escopo vazio → (' && FALSE()', True): nada (supervisor sem clientes de cadastro).
    - escopo ≤2500 → ' && CODCLI IN {...}'.
    - escopo >2500 mas complemento ≤2500 → ' && NOT(CODCLI IN {complemento})'.
    - ambos >2500 (raríssimo) → ('', False): caller deve usar fallback (RBAC de venda)."""
    role = session.get('role')
    if role in ('admin', 'viewer'):
        return ('', True)
    escopo = {c['codcli'] for c in _carteira_no_escopo() if c.get('codcli') is not None}
    if not escopo:
        return (' && FALSE()', True)
    if len(escopo) <= 2500:
        lista = ', '.join(str(c) for c in sorted(escopo))
        return (f' && FATURAMENTO_VENDAS[CODCLI] IN {{{lista}}}', True)
    todos = {c['codcli'] for c in _carregar_carteira_full() if c.get('codcli') is not None}
    complemento = todos - escopo
    if len(complemento) <= 2500:
        lista = ', '.join(str(c) for c in sorted(complemento))
        return (f' && NOT(FATURAMENTO_VENDAS[CODCLI] IN {{{lista}}})', True)
    return ('', False)


def _executar_dax_paralelo_n(queries: dict, max_workers: int = 4) -> dict:
    """Versão de executar_dax_paralelo com max_workers configurável."""
    token = get_token_cached()

    @retry_dax
    def _run(q):
        return execute_dax(token, q)

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {nome: ex.submit(_run, q) for nome, q in queries.items()}
        return {nome: f.result() for nome, f in futures.items()}


@app.route('/api/carteira/rfm')
@login_required
def api_carteira_rfm():
    modo = request.args.get('modo', 'personalizada')
    if modo not in ('fixa', 'personalizada'):
        modo = 'personalizada'

    clientes = _carteira_no_escopo()
    agg = rfm.agregar_distribuicoes(clientes, modo=modo)
    matriz = rfm.matriz_rf(clientes)
    histograma = rfm.histograma_recencia(clientes)

    # Listas de vendedores/times/UFs/cidades distintos QUE EFETIVAMENTE aparecem na carteira.
    # Frontend usa pra popular dropdowns só com itens relevantes (evita "0 clientes").
    vendedores_ativos = sorted({c['codusur'] for c in clientes if c.get('codusur') is not None})
    times_ativos      = sorted({c['codsupervisor'] for c in clientes if c.get('codsupervisor') is not None})
    ufs_ativas        = sorted({(c.get('uf') or '').upper() for c in clientes if c.get('uf')})
    # Cidades: {nome_normalizado: uf} → frontend faz cascata UF→Cidade
    cidades_ativas    = sorted({(c.get('cidade') or '', (c.get('uf') or '').upper()) for c in clientes if c.get('cidade')})
    cidades_payload   = [{'cidade': c[0], 'uf': c[1]} for c in cidades_ativas]

    # Tuplas únicas (vendedor, time, UF, cidade) pra cross-filter dos 4 dropdowns no frontend.
    # Ex: usuário escolhe vendedor → outras 3 listas filtram pra mostrar só o que esse vendedor atende.
    # Sort None-safe: clientes sem codusur/codsupervisor de cadastro geram None → não dá
    # pra comparar None < int direto. Chave coage None p/ ordenar sem estourar TypeError.
    cross_tuples = sorted(
        {(
            c.get('codusur'),
            c.get('codsupervisor'),
            (c.get('uf') or '').upper() or None,
            c.get('cidade') or None,
        ) for c in clientes},
        key=lambda t: (t[0] is None, t[0] or 0, t[1] is None, t[1] or 0, t[2] or '', t[3] or ''),
    )
    cross_filter_tuples = [
        {'codusur': t[0], 'codsupervisor': t[1], 'uf': t[2], 'cidade': t[3]}
        for t in cross_tuples
    ]

    return jsonify({
        'ok': True,
        'modo': modo,
        **agg,
        'matriz_rf': matriz,
        'histograma_recencia': histograma,
        'vendedores_ativos':   vendedores_ativos,
        'times_ativos':        times_ativos,
        'ufs_ativas':          ufs_ativas,
        'cidades_ativas':      cidades_payload,
        'cross_filter_tuples': cross_filter_tuples,
    })


def _carregar_venda_mensal_por_cliente(role=None, codusur=None, codsupervisor=None):
    """Cache: {codcli: {anomes: venda_bruta}} dos últimos 24 meses, GLOBAL (todos os clientes,
    sem filtro de venda). O recorte por usuário é feito pelos endpoints via os codclis do
    escopo de cadastro (carteira). Params mantidos por compatibilidade, mas ignorados.

    IMPORTANTE: retorna VENDA BRUTA (por DTSAIDA), NÃO líquida — devolução em função irmã."""
    key = 'multpel:venda_mensal_por_cliente:global:v4'
    cached = _cache_get(key)
    if cached:
        return {int(cc): {int(am): v for am, v in meses.items()}
                for cc, meses in cached.items()}

    rbac_frag = ""  # global: sem filtro de venda

    # Janela 24m (em vez de 12m) cobre comparativo YoY do drill mensal sem nova query.
    # Usa medida [VENDA BRUTA] do PBI (não SUM(VLVENDA) raw) pra alinhar com regras de
    # bonificação/desconto definidas pelo BI deles.
    query = f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODCLI],
    CALENDARIO[AnoMes],
    FILTER(FATURAMENTO_VENDAS, FATURAMENTO_VENDAS[DTSAIDA] >= EOMONTH(TODAY(), -24) + 1{rbac_frag}),
    "Bruta", [VENDA BRUTA]
)"""
    token = get_token_cached()
    payload = retry_dax(execute_dax)(token, query)
    rows = clean_rows(_todas_linhas(payload))
    por_cliente = {}
    for r in rows:
        cc = r.get('CODCLI')
        am = r.get('AnoMes')
        v = r.get('Bruta') or 0
        if cc is None or am is None or v <= 0:
            continue
        por_cliente.setdefault(int(cc), {})[int(am)] = v
    _cache_set(key, por_cliente, 'dax_agregado')
    return por_cliente


def _carregar_devolucao_mensal_por_cliente(role=None, codusur=None, codsupervisor=None):
    """Cache 1h: {codcli: {anomes: devolucao}} dos últimos 12 meses.

    Filtra por FATURAMENTO_DEVOLUCAO[DTENT] (data em que a devolução entrou no sistema)
    pra alinhar com o relatório RCA do ERP. Inclui DEVOLUCAO + DEVOLUCAO_AVULSA.

    Usado junto com _carregar_venda_mensal_por_cliente: líquida = bruta - devolucao.
    GLOBAL (todos os clientes, sem filtro de venda); recorte por usuário é feito nos endpoints."""
    key = 'multpel:devolucao_mensal_por_cliente:global:v2'
    cached = _cache_get(key)
    if cached:
        return {int(cc): {int(am): v for am, v in meses.items()}
                for cc, meses in cached.items()}

    rbac_dev = ""    # global: sem filtro de venda
    rbac_devav = ""

    # 2 queries em paralelo (devolução normal + avulsa). Janela 24m cobre YoY do drill.
    # AnoMes não pode ser expressão dentro de SUMMARIZECOLUMNS → agregamos por (CODCLI, DTENT)
    # e processamos AnoMes em Python (YEAR*100+MONTH).
    queries = {
        'dev': f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_DEVOLUCAO[CODCLI],
    FATURAMENTO_DEVOLUCAO[DTENT],
    FILTER(FATURAMENTO_DEVOLUCAO, FATURAMENTO_DEVOLUCAO[DTENT] >= EOMONTH(TODAY(), -24) + 1{rbac_dev}),
    "Devol", [TOTAL DEVOLUCAO]
)""",
        'devav': f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_DEVOLUCAO_AVULSA[CODCLI],
    FATURAMENTO_DEVOLUCAO_AVULSA[DTENT],
    FILTER(FATURAMENTO_DEVOLUCAO_AVULSA, FATURAMENTO_DEVOLUCAO_AVULSA[DTENT] >= EOMONTH(TODAY(), -24) + 1{rbac_devav}),
    "DevolA", [TOTAL DEVOLUCAO AVULSA]
)""",
    }
    resultados = _executar_dax_paralelo_n(queries, max_workers=2)

    def _dtent_para_anomes(dtent_str):
        """Aceita string ISO ou datetime, retorna int YYYYMM."""
        if not dtent_str:
            return None
        s = str(dtent_str)[:10]  # 'YYYY-MM-DD...'
        try:
            return int(s[:4]) * 100 + int(s[5:7])
        except (ValueError, IndexError):
            return None

    por_cliente = {}
    for r in clean_rows(_todas_linhas(resultados['dev'])):
        cc = r.get('CODCLI')
        am = _dtent_para_anomes(r.get('DTENT'))
        v = r.get('Devol') or 0
        if cc is None or am is None or v <= 0:
            continue
        por_cliente.setdefault(int(cc), {})
        por_cliente[int(cc)][am] = por_cliente[int(cc)].get(am, 0) + v
    for r in clean_rows(_todas_linhas(resultados['devav'])):
        cc = r.get('CODCLI')
        am = _dtent_para_anomes(r.get('DTENT'))
        v = r.get('DevolA') or 0
        if cc is None or am is None or v <= 0:
            continue
        por_cliente.setdefault(int(cc), {})
        por_cliente[int(cc)][am] = por_cliente[int(cc)].get(am, 0) + v
    _cache_set(key, por_cliente, 'dax_agregado')
    return por_cliente


def _meses_ultimos_12():
    """Lista de 12 ints AnoMes terminando no mês corrente (incluso). Ex: hoje em Mai/26
    retorna [202506, 202507, ..., 202605]."""
    from datetime import date
    hoje = date.today()
    meses = []
    y, m = hoje.year, hoje.month
    # Inclui mês corrente + 11 anteriores
    for _ in range(12):
        meses.append(y * 100 + m)
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    meses.reverse()
    return meses


@app.route('/api/carteira/receita-positivacao-12m')
@login_required
def api_carteira_receita_positivacao_12m():
    """Receita líquida + clientes únicos por mês (últimos 12m), filtrado pelos mesmos
    params que /api/carteira/clientes.

    Estratégia HÍBRIDA pra bater EXATO com relatório RCA do cliente:
    - Filtros SIMPLES (time/vendedor/uf — existem em FAT_VENDAS direto): DAX direto.
      Alinha 100% com RCA pois filtra pela transação (não por carteira do cliente atual).
    - Filtros COMPLEXOS (cidade/segmento/busca — vêm de PCCLIENT): in-memory com codclis.
      Aceita ~1% de diferença vs RCA (mas explicada: histórico vs presente).

    LÍQUIDA = BRUTA(DTSAIDA) - DEVOLUÇÃO(DTENT) em ambos os caminhos."""
    # Carteira por CADASTRO: sempre via in-memory (soma os mapas mensais GLOBAIS pelos
    # codclis do escopo de cadastro do usuário → números TOTAIS do cliente). O caminho
    # DAX-direto (por transação/venda) deixou de ser usado pra manter coerência com a
    # nova definição da carteira.
    return _chart_receita_via_in_memory(request.args)


def _chart_receita_via_dax_direto(args):
    """Caminho que alinha 100% com RCA — filtra na DAX por CODSUPERVISOR/CODUSUR/UF
    da própria transação (FAT_VENDAS e FAT_DEVOLUCAO)."""
    rbac = aplicar_rbac_dax()
    rbac_frag = f" && {rbac}" if rbac else ""
    # Patch G.1: RBAC pras devoluções (sem isso vendedor/supervisor logado pega devol da empresa toda)
    rbac_dev = rbac_devol_dax()
    rbac_dev_frag = f" && {rbac_dev}" if rbac_dev else ""
    rbac_devav = rbac_devol_av_dax()
    rbac_devav_frag = f" && {rbac_devav}" if rbac_devav else ""

    # Monta filtros DAX-side a partir de args.
    # FAT_VENDAS e FAT_DEVOLUCAO e FAT_DEVOLUCAO_AVULSA todas tem CODSUPERVISOR/CODUSUR/UF.
    extra_vendas = ""
    extra_devol = ""
    extra_devol_av = ""
    if args.get('time'):
        try:
            cs = int(args['time'])
            extra_vendas   += f" && FATURAMENTO_VENDAS[CODSUPERVISOR] = {cs}"
            extra_devol    += f" && FATURAMENTO_DEVOLUCAO[CODSUPERVISOR] = {cs}"
            extra_devol_av += f" && FATURAMENTO_DEVOLUCAO_AVULSA[CODSUPERVISOR] = {cs}"
        except (TypeError, ValueError):
            pass
    if args.get('vendedor'):
        try:
            cu = int(args['vendedor'])
            extra_vendas   += f" && FATURAMENTO_VENDAS[CODUSUR] = {cu}"
            extra_devol    += f" && FATURAMENTO_DEVOLUCAO[CODUSUR] = {cu}"
            extra_devol_av += f" && FATURAMENTO_DEVOLUCAO_AVULSA[CODUSUR] = {cu}"
        except (TypeError, ValueError):
            pass
    if args.get('uf'):
        ufs = [u.strip().upper() for u in args['uf'].split(',') if u.strip()]
        if len(ufs) == 1:
            extra_vendas   += f' && FATURAMENTO_VENDAS[UF] = "{ufs[0]}"'
            extra_devol_av += f' && FATURAMENTO_DEVOLUCAO_AVULSA[UF] = "{ufs[0]}"'
        elif len(ufs) > 1:
            ufs_dax = ', '.join(f'"{u}"' for u in ufs)
            extra_vendas   += f' && FATURAMENTO_VENDAS[UF] IN {{{ufs_dax}}}'
            extra_devol_av += f' && FATURAMENTO_DEVOLUCAO_AVULSA[UF] IN {{{ufs_dax}}}'
        # FAT_DEVOLUCAO não tem UF — filtra via CODCLI da carteira_full
        if ufs:
            clientes_full = _carregar_carteira_full()
            codclis_uf = {c['codcli'] for c in clientes_full
                          if (c.get('uf') or '').upper() in ufs}
            if codclis_uf and len(codclis_uf) <= 2500:
                lista = ', '.join(str(c) for c in sorted(codclis_uf))
                extra_devol += f" && FATURAMENTO_DEVOLUCAO[CODCLI] IN {{{lista}}}"
            elif codclis_uf:
                all_cc = {c['codcli'] for c in clientes_full if c.get('codcli') is not None}
                complemento = all_cc - codclis_uf
                if len(complemento) <= 2500:
                    lista = ', '.join(str(c) for c in sorted(complemento))
                    extra_devol += f" && NOT(FATURAMENTO_DEVOLUCAO[CODCLI] IN {{{lista}}})"

    queries = {
        'bruta_pos': f"""EVALUATE
SUMMARIZECOLUMNS(
    CALENDARIO[AnoMes],
    FILTER(FATURAMENTO_VENDAS, FATURAMENTO_VENDAS[DTSAIDA] >= EOMONTH(TODAY(), -12) + 1{rbac_frag}{extra_vendas}),
    "Bruta", [VENDA BRUTA],
    "Clientes", DISTINCTCOUNT(FATURAMENTO_VENDAS[CODCLI])
)""",
        'devol': f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_DEVOLUCAO[DTENT],
    FILTER(FATURAMENTO_DEVOLUCAO, FATURAMENTO_DEVOLUCAO[DTENT] >= EOMONTH(TODAY(), -12) + 1{rbac_dev_frag}{extra_devol}),
    "Devol", [TOTAL DEVOLUCAO]
)""",
        'devol_av': f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_DEVOLUCAO_AVULSA[DTENT],
    FILTER(FATURAMENTO_DEVOLUCAO_AVULSA, FATURAMENTO_DEVOLUCAO_AVULSA[DTENT] >= EOMONTH(TODAY(), -12) + 1{rbac_devav_frag}{extra_devol_av}),
    "DevolA", [TOTAL DEVOLUCAO AVULSA]
)""",
    }
    resultados = _executar_dax_paralelo_n(queries, max_workers=3)

    # Agregar venda+positivação por mês
    por_mes_bruta = {}
    por_mes_clientes = {}
    for r in clean_rows(_todas_linhas(resultados['bruta_pos'])):
        am = r.get('AnoMes')
        if am is None: continue
        por_mes_bruta[int(am)] = r.get('Bruta') or 0
        por_mes_clientes[int(am)] = r.get('Clientes') or 0

    # Agregar devoluções por mês (a partir do DTENT)
    def _dtent_para_am(s):
        if not s: return None
        try: s = str(s)[:10]; return int(s[:4]) * 100 + int(s[5:7])
        except (ValueError, IndexError): return None

    por_mes_devol = {}
    for r in clean_rows(_todas_linhas(resultados['devol'])):
        am = _dtent_para_am(r.get('DTENT'))
        if am is None: continue
        por_mes_devol[am] = por_mes_devol.get(am, 0) + (r.get('Devol') or 0)
    for r in clean_rows(_todas_linhas(resultados['devol_av'])):
        am = _dtent_para_am(r.get('DTENT'))
        if am is None: continue
        por_mes_devol[am] = por_mes_devol.get(am, 0) + (r.get('DevolA') or 0)

    meses = _meses_ultimos_12()
    rows = []
    for am in meses:
        rows.append({
            'AnoMes': am,
            'VendaLiquida': round(por_mes_bruta.get(am, 0) - por_mes_devol.get(am, 0), 2),
            'ClientesUnicos': por_mes_clientes.get(am, 0),
        })
    return jsonify({'ok': True, 'rows': rows})


def _chart_receita_via_in_memory(args):
    """Soma os mapas mensais GLOBAIS pelos codclis do escopo de CADASTRO (números totais)."""
    clientes_full = _carteira_no_escopo()
    args_filt = dict(args)
    args_filt['limit'] = 100000
    args_filt['offset'] = 0
    args_filt['_interno'] = True
    resultado = _filtrar_carteira(clientes_full, args_filt)
    codclis_filtrados = {c['codcli'] for c in resultado['rows'] if c.get('codcli') is not None}

    venda_mensal = _carregar_venda_mensal_por_cliente()
    devol_mensal = _carregar_devolucao_mensal_por_cliente()
    meses = _meses_ultimos_12()

    rows = []
    for am in meses:
        total_bruta = 0.0
        total_devol = 0.0
        clientes_unicos = 0
        for cc in codclis_filtrados:
            b = venda_mensal.get(cc, {}).get(am, 0)
            d = devol_mensal.get(cc, {}).get(am, 0)
            if b > 0:
                total_bruta += b
                clientes_unicos += 1
            if d > 0:
                total_devol += d
        rows.append({
            'AnoMes': am,
            'VendaLiquida': round(total_bruta - total_devol, 2),
            'ClientesUnicos': clientes_unicos,
        })

    return jsonify({'ok': True, 'rows': rows})


@app.route('/api/carteira/mes/<int:anomes>')
@login_required
def api_carteira_mes(anomes):
    """Detalhe de 1 mês: resumo + top 10 clientes + top 5 deptos + comparativos
    (vs mês anterior + vs mesmo mês ano anterior). Usado pelo drill do bar chart.
    Aceita filtros geo (UF/cidade/vendedor/time) — drill respeita o contexto da tela."""
    if anomes < 200001 or anomes > 209912:
        return jsonify({'ok': False, 'error': 'anomes inválido (esperado YYYYMM)'}), 400

    # Aplica filtros via in-memory pra obter codclis filtrados.
    # Estratégia dinâmica IN/NOT IN — limite prático DAX IN clause ~2500 items.
    # - Sem filtros (codclis == total) → sem filtro DAX (comportamento natural).
    # - codclis_filtrados <= 2500 → CODCLI IN {lista_filtrados}.
    # - complemento <= 2500 → NOT(CODCLI IN {lista_complemento}). Cobre UF=ES (7132
    #   codclis, complemento 1589) e casos similares onde filtro pega maioria da base.
    # - Ambos > 2500 → DAX sem filtro extra (raríssimo: filtro que pega 'meio' da base).
    # Escopo de CADASTRO do usuário (rbac_frag abaixo segue como rede de segurança).
    clientes_full = _carteira_no_escopo()
    args_filt = dict(request.args)
    args_filt['limit'] = 100000
    args_filt['offset'] = 0
    args_filt['_interno'] = True   # libera cap de 1000
    resultado = _filtrar_carteira(clientes_full, args_filt)
    codclis_filtrados = {c['codcli'] for c in resultado['rows'] if c.get('codcli') is not None}
    total_base_codclis = {c['codcli'] for c in clientes_full if c.get('codcli') is not None}
    total_base = len(total_base_codclis)

    n_filt = len(codclis_filtrados)
    if not codclis_filtrados or n_filt == total_base:
        codcli_frag = ''
        filtros_aplicados = False
    elif n_filt <= 2500:
        lista = ', '.join(str(c) for c in sorted(codclis_filtrados))
        codcli_frag = f" && FATURAMENTO_VENDAS[CODCLI] IN {{{lista}}}"
        filtros_aplicados = True
    elif (total_base - n_filt) <= 2500:
        # Usa complemento — economiza tamanho da query DAX
        complemento = total_base_codclis - codclis_filtrados
        lista = ', '.join(str(c) for c in sorted(complemento))
        codcli_frag = f" && NOT(FATURAMENTO_VENDAS[CODCLI] IN {{{lista}}})"
        filtros_aplicados = True
    else:
        codcli_frag = ''
        filtros_aplicados = False

    # Cache key inclui anomes + tem filtro aplicado (pra não misturar drill filtrado/sem filtro)
    key_params = {'anomes': anomes, 'filtros': sorted(args_filt.items()) if filtros_aplicados else 'none'}
    key = cache_key_for_user('carteira:mes', key_params)
    cached = _cache_get(key)
    if cached:
        return jsonify(cached)

    ano = anomes // 100
    mes = anomes % 100
    rbac = aplicar_rbac_dax()
    rbac_frag = f" && {rbac}" if rbac else ""
    # Patch G.1: RBAC pras devoluções (vendedor/supervisor logado)
    rbac_dev = rbac_devol_dax()
    rbac_dev_frag = f" && {rbac_dev}" if rbac_dev else ""
    rbac_devav = rbac_devol_av_dax()
    rbac_devav_frag = f" && {rbac_devav}" if rbac_devav else ""

    # Filtros usam YEAR/MONTH em DTSAIDA (CALENDARIO[Ano] não funciona dentro de FILTER(FATURAMENTO_VENDAS))
    def _f(a, m):
        return f"YEAR(FATURAMENTO_VENDAS[DTSAIDA])={a} && MONTH(FATURAMENTO_VENDAS[DTSAIDA])={m}{rbac_frag}{codcli_frag}"

    f_atual = _f(ano, mes)
    if mes == 1:
        mes_ant, ano_ant = 12, ano - 1
    else:
        mes_ant, ano_ant = mes - 1, ano
    f_mes_ant = _f(ano_ant, mes_ant)
    f_yoy = _f(ano - 1, mes)

    # Apenas as queries que NÃO foram migradas pra in-memory:
    # - Top clientes / Top deptos: continuam via DAX (uso [VENDA LIQUIDA] do PBI — pode
    #   ter pequena diferença vs RCA mas é ranking ordenado, não soma absoluta)
    # - Lucro total + ticket médio + clientes únicos + vendedores: vem do DAX
    # - VENDA TOTAL: calculada in-memory abaixo (bruta DTSAIDA - devol DTENT) pra alinhar com RCA
    queries = {
        'resumo': f"""EVALUATE {{(
            CALCULATE([LUCRO TOTAL], FILTER(FATURAMENTO_VENDAS, {f_atual})),
            CALCULATE(DISTINCTCOUNT(FATURAMENTO_VENDAS[CODCLI]), FILTER(FATURAMENTO_VENDAS, {f_atual})),
            CALCULATE([TICKET MEDIO], FILTER(FATURAMENTO_VENDAS, {f_atual})),
            CALCULATE(DISTINCTCOUNT(FATURAMENTO_VENDAS[CODUSUR]), FILTER(FATURAMENTO_VENDAS, {f_atual}))
        )}}""",
        'top_clientes': f"""EVALUATE
TOPN(10,
    SUMMARIZECOLUMNS(
        FATURAMENTO_VENDAS[CODCLI],
        FATURAMENTO_VENDAS[CLIENTE],
        FATURAMENTO_VENDAS[UF],
        FATURAMENTO_VENDAS[CODUSUR],
        FILTER(FATURAMENTO_VENDAS, {f_atual}),
        "Venda", [VENDA LIQUIDA],
        "Lucro", [LUCRO TOTAL]
    ),
    [Venda], DESC
)
ORDER BY [Venda] DESC""",
        'top_deptos': f"""EVALUATE
TOPN(5,
    SUMMARIZECOLUMNS(
        FATURAMENTO_VENDAS[CODEPTO],
        FILTER(FATURAMENTO_VENDAS, {f_atual}),
        "Venda", [VENDA LIQUIDA]
    ),
    [Venda], DESC
)
ORDER BY [Venda] DESC""",
        'top_produtos': f"""EVALUATE
TOPN(20,
    SUMMARIZECOLUMNS(
        FATURAMENTO_VENDAS[CODPROD],
        PCPRODUT[DESCRICAO],
        FILTER(FATURAMENTO_VENDAS, {f_atual}),
        "Venda", [VENDA LIQUIDA],
        "QtVenda", SUM(FATURAMENTO_VENDAS[QT])
    ),
    [Venda], DESC
)
ORDER BY [Venda] DESC""",
        'cli_mes_ant': f"""EVALUATE {{(
            CALCULATE(DISTINCTCOUNT(FATURAMENTO_VENDAS[CODCLI]), FILTER(FATURAMENTO_VENDAS, {f_mes_ant}))
        )}}""",
        'cli_yoy': f"""EVALUATE {{(
            CALCULATE(DISTINCTCOUNT(FATURAMENTO_VENDAS[CODCLI]), FILTER(FATURAMENTO_VENDAS, {f_yoy}))
        )}}""",
    }

    resultados = _executar_dax_paralelo_n(queries, max_workers=6)

    # ── VENDA TOTAL + LUCRO TOTAL — estratégia híbrida pra alinhar com RCA ──
    # Detecta tipo de filtros: só time/vendedor/uf = pode rodar DAX direto (= RCA).
    # Cidade/segmento/busca = precisa via in-memory codclis (~1% off mas aceitável).
    filtros_complexos_drill = any(request.args.get(k) for k in ('cidade', 'segmento', 'status', 'busca'))

    am_atual   = ano * 100 + mes
    am_mes_ant = ano_ant * 100 + mes_ant
    am_yoy     = (ano - 1) * 100 + mes

    lucro_alinhado_atual = None  # só preenchido no caminho DAX direto

    if filtros_complexos_drill:
        # Caminho in-memory (cidade/segmento/busca aplicados via codclis_filtrados)
        venda_mensal = _carregar_venda_mensal_por_cliente()
        devol_mensal = _carregar_devolucao_mensal_por_cliente()
        codclis_para_somar = codclis_filtrados if codclis_filtrados else (
            set(venda_mensal.keys()) | set(devol_mensal.keys())
        )
        def _vl_mes(am):
            b = sum(venda_mensal.get(cc, {}).get(am, 0) for cc in codclis_para_somar)
            d = sum(devol_mensal.get(cc, {}).get(am, 0) for cc in codclis_para_somar)
            return round(b - d, 2)
        venda_total_atual = _vl_mes(am_atual)
        venda_ma          = _vl_mes(am_mes_ant)
        venda_ya          = _vl_mes(am_yoy)
    else:
        # Caminho DAX direto — filtros simples aplicados direto na transação. Bate com RCA.
        extra_vendas_drill = ""
        extra_devol_drill = ""
        extra_devol_av_drill = ""
        if request.args.get('time'):
            try:
                cs = int(request.args['time'])
                extra_vendas_drill   += f" && FATURAMENTO_VENDAS[CODSUPERVISOR] = {cs}"
                extra_devol_drill    += f" && FATURAMENTO_DEVOLUCAO[CODSUPERVISOR] = {cs}"
                extra_devol_av_drill += f" && FATURAMENTO_DEVOLUCAO_AVULSA[CODSUPERVISOR] = {cs}"
            except (TypeError, ValueError): pass
        if request.args.get('vendedor'):
            try:
                cuf = int(request.args['vendedor'])
                extra_vendas_drill   += f" && FATURAMENTO_VENDAS[CODUSUR] = {cuf}"
                extra_devol_drill    += f" && FATURAMENTO_DEVOLUCAO[CODUSUR] = {cuf}"
                extra_devol_av_drill += f" && FATURAMENTO_DEVOLUCAO_AVULSA[CODUSUR] = {cuf}"
            except (TypeError, ValueError): pass
        if request.args.get('uf'):
            ufs = [u.strip().upper() for u in request.args['uf'].split(',') if u.strip()]
            if len(ufs) == 1:
                extra_vendas_drill   += f' && FATURAMENTO_VENDAS[UF] = "{ufs[0]}"'
                extra_devol_av_drill += f' && FATURAMENTO_DEVOLUCAO_AVULSA[UF] = "{ufs[0]}"'
            elif len(ufs) > 1:
                ufs_dax = ', '.join(f'"{u}"' for u in ufs)
                extra_vendas_drill   += f' && FATURAMENTO_VENDAS[UF] IN {{{ufs_dax}}}'
                extra_devol_av_drill += f' && FATURAMENTO_DEVOLUCAO_AVULSA[UF] IN {{{ufs_dax}}}'

        def _venda_lucro_mes_dax(a, m):
            """1 query DAX consolidada por mês — devolve (vl, lucro_alinhado).
            VL    = BRUTA(DTSAIDA) - DEVOL(DTENT) - DEVOL_AV(DTENT)
            LUCRO = VL - (CUSTO_TOTAL(DTSAIDA) - CUSTO_DEVOL(DTENT) - CUSTO_DEVOL_AV(DTENT))
            Bate centavo a centavo com RCA (validado com sup 17 Abr/26: R$ 520.326,87)."""
            fv_mes = f"YEAR(FATURAMENTO_VENDAS[DTSAIDA])={a} && MONTH(FATURAMENTO_VENDAS[DTSAIDA])={m}{rbac_frag}{extra_vendas_drill}"
            fd_mes = f"YEAR(FATURAMENTO_DEVOLUCAO[DTENT])={a} && MONTH(FATURAMENTO_DEVOLUCAO[DTENT])={m}{rbac_dev_frag}{extra_devol_drill}"
            fda_mes = f"YEAR(FATURAMENTO_DEVOLUCAO_AVULSA[DTENT])={a} && MONTH(FATURAMENTO_DEVOLUCAO_AVULSA[DTENT])={m}{rbac_devav_frag}{extra_devol_av_drill}"
            q = f"""EVALUATE {{(
                CALCULATE([VENDA BRUTA], FILTER(FATURAMENTO_VENDAS, {fv_mes})),
                CALCULATE([TOTAL DEVOLUCAO], FILTER(FATURAMENTO_DEVOLUCAO, {fd_mes})),
                CALCULATE([TOTAL DEVOLUCAO AVULSA], FILTER(FATURAMENTO_DEVOLUCAO_AVULSA, {fda_mes})),
                CALCULATE([CUSTO TOTAL], FILTER(FATURAMENTO_VENDAS, {fv_mes})),
                CALCULATE([CUSTO TOTAL DEVOLUCAO], FILTER(FATURAMENTO_DEVOLUCAO, {fd_mes})),
                CALCULATE([CUSTO TOTAL DEVOLUCAO AVULSA], FILTER(FATURAMENTO_DEVOLUCAO_AVULSA, {fda_mes}))
            )}}"""
            token = get_token_cached()
            payload = retry_dax(execute_dax)(token, q)
            r = _primeira_linha(payload)
            b   = r.get('[Value1]') or 0
            dv  = r.get('[Value2]') or 0
            dva = r.get('[Value3]') or 0
            ct  = r.get('[Value4]') or 0
            cdv = r.get('[Value5]') or 0
            cda = r.get('[Value6]') or 0
            vl    = round(b - dv - dva, 2)
            lucro = round(vl - (ct - cdv - cda), 2)
            return (vl, lucro)

        # 3 meses em paralelo (cada um é 1 query DAX só)
        qs_meses = {
            'atual':   (ano, mes),
            'mes_ant': (ano_ant, mes_ant),
            'yoy':     (ano - 1, mes),
        }
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=3) as ex:
            futs = {k: ex.submit(_venda_lucro_mes_dax, a, m) for k, (a, m) in qs_meses.items()}
            res_meses = {k: f.result() for k, f in futs.items()}
        venda_total_atual, lucro_alinhado_atual = res_meses['atual']
        venda_ma,          _                    = res_meses['mes_ant']
        venda_ya,          _                    = res_meses['yoy']

    # Resumo — lucro alinhado quando DAX direto disponível (bate RCA centavo a centavo).
    # Senão fallback p/ [LUCRO TOTAL] do DAX (in-memory, ~1% off).
    r = _primeira_linha(resultados['resumo'])
    resumo = {
        'venda_total':       venda_total_atual,
        'lucro_total':       lucro_alinhado_atual if lucro_alinhado_atual is not None else (r.get('[Value1]') or 0),
        'clientes_unicos':   r.get('[Value2]') or 0,
        'ticket_medio':      r.get('[Value3]') or 0,
        'vendedores_ativos': r.get('[Value4]') or 0,
    }

    # Comparativos
    cli_ma = (_primeira_linha(resultados['cli_mes_ant'])).get('[Value1]') or 0
    cli_ya = (_primeira_linha(resultados['cli_yoy'])).get('[Value1]') or 0

    def _pct(atual, anterior):
        if anterior and anterior > 0:
            return (atual - anterior) / anterior
        return None

    comparativo = {
        'venda_vs_mes_anterior_pct':    _pct(resumo['venda_total'], venda_ma),
        'clientes_vs_mes_anterior_pct': _pct(resumo['clientes_unicos'], cli_ma),
        'venda_vs_ano_anterior_pct':    _pct(resumo['venda_total'], venda_ya),
        'clientes_vs_ano_anterior_pct': _pct(resumo['clientes_unicos'], cli_ya),
        'venda_mes_anterior':           venda_ma,
        'venda_ano_anterior':           venda_ya,
    }

    # Top clientes — enriquece com nome vendedor via mapa
    vendedores = _carregar_vendedores_map()
    top_clientes = []
    for r in clean_rows(_todas_linhas(resultados['top_clientes'])):
        cu = r.get('CODUSUR')
        v_meta = vendedores.get(str(cu)) if cu is not None else None
        top_clientes.append({
            'codcli':   r.get('CODCLI'),
            'cliente':  r.get('CLIENTE'),
            'uf':       r.get('UF'),
            'codusur':  cu,
            'vendedor': (v_meta.get('nome') if v_meta else None) or (f'RCA {cu}' if cu else '—'),
            'venda':    r.get('Venda') or 0,
            'lucro':    r.get('Lucro') or 0,
        })

    # Top deptos — enriquece com nome via deptos_map
    deptos_map = _carregar_deptos_map().get('deptos', {})
    total_venda = resumo['venda_total'] or 1
    top_deptos = []
    for r in clean_rows(_todas_linhas(resultados['top_deptos'])):
        cd = r.get('CODEPTO')
        v = r.get('Venda') or 0
        top_deptos.append({
            'codepto':   cd,
            'nome':      deptos_map.get(str(cd), f'Depto {cd}' if cd else '—'),
            'venda':     v,
            'share_pct': v / total_venda if total_venda else 0,
        })

    # Top produtos — descrição vem do relacionamento FATURAMENTO_VENDAS[CODPROD] → PCPRODUT
    top_produtos = []
    for r in clean_rows(_todas_linhas(resultados['top_produtos'])):
        cp = r.get('CODPROD')
        ds = r.get('DESCRICAO') or (f'Produto {cp}' if cp else '—')
        v = r.get('Venda') or 0
        qt = r.get('QtVenda') or 0
        top_produtos.append({
            'codprod':    cp,
            'descricao':  ds,
            'venda':      v,
            'quantidade': qt,
            'share_pct':  v / total_venda if total_venda else 0,
        })

    MESES_PT = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
    resp = {
        'ok':                True,
        'anomes':            anomes,
        'nome_mes':          f"{MESES_PT[mes-1]}/{str(ano)[2:]}",
        'resumo':            resumo,
        'comparativo':       comparativo,
        'top_clientes':      top_clientes,
        'top_deptos':        top_deptos,
        'top_produtos':      top_produtos,
        'filtros_aplicados': filtros_aplicados,  # frontend mostra badge "filtrado"
        'codclis_no_filtro': len(codclis_filtrados) if filtros_aplicados else None,
    }
    _cache_set(key, resp, 'dax_agregado')
    return jsonify(resp)


def _filtrar_carteira(clientes, args, vendedor_forcado=None):
    """Filtra/ordena/pagina lista de clientes. Função pura — sem Flask.

    Args:
        clientes: lista de dicts (já cacheada de _carregar_carteira_full)
        args: dict-like com chaves segmento/status/modo/vendedor/busca/sort/dir/limit/offset
        vendedor_forcado: se passado (int), sobrescreve args['vendedor'] — usado por
                          /api/vendedor/<codusur>/carteira pra travar o filtro.

    Retorna: dict {ok, total, offset, limit, rows}.
    """
    segmento = args.get('segmento')
    status_filtro = args.get('status')
    modo = args.get('modo', 'personalizada')
    vendedor = vendedor_forcado if vendedor_forcado is not None else args.get('vendedor')
    uf = args.get('uf')
    busca = (args.get('busca') or '').strip().lower()
    sort = args.get('sort', 'lucro_perdido')
    direction = args.get('dir', 'desc')
    try:
        # Cap de 1000 protege endpoint exposto ao user (/api/carteira/clientes).
        # Callers internos que precisam de TODOS os filtrados (chart receita+positivação,
        # drill mensal, CSV/PDF export) passam limit=100000 + flag _interno=True.
        limit_max = 100000 if args.get('_interno') else 1000
        limit = max(1, min(int(args.get('limit', 100)), limit_max))
        offset = max(0, int(args.get('offset', 0)))
    except (TypeError, ValueError):
        limit, offset = 100, 0

    status_key = 'status_personalizada' if modo == 'personalizada' else 'status_fixa'
    cidade = args.get('cidade')
    time = args.get('time')
    # Filtro "dias sem comprar" — recencia_dias >= dias_min (mín.) e <= dias_max (máx.).
    # dias_max é usado pelo deep-link de faixa do painel Gerencial (ex.: 31-45 → min=31, max=45).
    dias_min = None
    if args.get('dias_min') not in (None, ''):
        try:
            dias_min = max(0, int(args['dias_min']))
        except (TypeError, ValueError):
            dias_min = None
    dias_max = None
    if args.get('dias_max') not in (None, ''):
        try:
            dias_max = max(0, int(args['dias_max']))
        except (TypeError, ValueError):
            dias_max = None

    # Aplica todos os filtros (geo + drill + busca) num único pass.
    # Cards/donut e tabela compartilham o mesmo resultado (single source of truth).
    filtrados = clientes
    if uf:
        ufs = {u.strip().upper() for u in uf.split(',') if u.strip()}
        filtrados = [c for c in filtrados if (c.get('uf') or '').upper() in ufs]
    if cidade:
        cidades_set = {c.strip().upper() for c in cidade.split(',') if c.strip()}
        filtrados = [c for c in filtrados if (c.get('cidade') or '').upper() in cidades_set]
    if vendedor is not None and vendedor != '':
        try:
            vu = int(vendedor)
            filtrados = [c for c in filtrados if c['codusur'] == vu]
        except (TypeError, ValueError):
            pass
    if time is not None and time != '':
        try:
            tu = int(time)
            filtrados = [c for c in filtrados if c.get('codsupervisor') == tu]
        except (TypeError, ValueError):
            pass
    if segmento:
        segs = {s.strip() for s in segmento.split(',') if s.strip()}
        filtrados = [c for c in filtrados if c['segmento'] in segs]
    if status_filtro:
        sts = {s.strip() for s in status_filtro.split(',') if s.strip()}
        filtrados = [c for c in filtrados if c[status_key] in sts]
    if busca:
        # Estendido: também procura em codcli, codusur, nome do vendedor e nome do time
        filtrados = [c for c in filtrados
                     if (c.get('cliente') or '').lower().find(busca) >= 0
                     or (c.get('cidade') or '').lower().find(busca) >= 0
                     or str(c.get('codcli') or '').find(busca) >= 0
                     or str(c.get('codusur') or '').find(busca) >= 0
                     or (c.get('vendedor') or '').lower().find(busca) >= 0
                     or (c.get('time') or '').lower().find(busca) >= 0]
    if dias_min is not None:
        # Clientes sem compra (recencia_dias None) ficam de fora — só conta quem tem histórico
        filtrados = [c for c in filtrados
                     if c.get('recencia_dias') is not None and c['recencia_dias'] >= dias_min]
    if dias_max is not None:
        filtrados = [c for c in filtrados
                     if c.get('recencia_dias') is not None and c['recencia_dias'] <= dias_max]

    sort_map = {
        'lucro_perdido':   'lucro_perdido_proj',
        'receita_perdida': 'receita_perdida_proj',
        'lucro_12m':       'lucro_12m',
        'venda_12m':       'venda_12m',
        'media_venda':     'venda_12m',   # média = venda_12m/12 → mesma ordenação relativa
        'recencia':        'recencia_dias',
        'frequencia':      'frequencia_12m',
        'cliente':         'cliente',
        'vendedor':        'vendedor',   # nome do vendedor (ordenação alfabética)
        'prioridade':      'prioridade_contato',  # aba Próximo Pedido (valor × atraso)
        'proximo_pedido':  'proximo_pedido_previsto',
        'atraso':          'dias_atraso',
        'ciclo':           'ciclo_pessoal',
        'ultima_compra':   'ultima_compra',
    }
    reverse = (direction == 'desc')

    def _sort_key(v):
        # Texto: normaliza acento + minúscula → ordem alfabética "humana" (ÁLVARO ~ ALVARO).
        if isinstance(v, str):
            import unicodedata
            return unicodedata.normalize('NFKD', v).encode('ascii', 'ignore').decode().casefold()
        return v or 0

    if sort == 'status':
        # Severidade (não alfabético): ok < normal < atencao < urgente
        _rank = {'ok': 0, 'normal': 1, 'atencao': 2, 'urgente': 3}
        filtrados = sorted(filtrados, key=lambda c: _rank.get(c.get(status_key), -1), reverse=reverse)
    else:
        sort_attr = sort_map.get(sort, 'lucro_perdido_proj')
        filtrados = sorted(filtrados, key=lambda c: (c.get(sort_attr) is None, _sort_key(c.get(sort_attr))), reverse=reverse)

    total = len(filtrados)
    # Conta segmentos do conjunto FILTRADO (antes de paginar) — alimenta cards + donut
    segmentos_count = {}
    for c in filtrados:
        s = c.get('segmento') or 'unknown'
        segmentos_count[s] = segmentos_count.get(s, 0) + 1

    rows = filtrados[offset:offset + limit]
    return {
        'ok': True,
        'total': total,
        'offset': offset,
        'limit': limit,
        'rows': rows,
        'segmentos': segmentos_count,
    }


@app.route('/api/carteira/clientes')
@login_required
def api_carteira_clientes():
    """Filtra/ordena/pagina a carteira já enriquecida (em memória sobre cache)."""
    clientes = _carteira_no_escopo()
    return jsonify(_filtrar_carteira(clientes, request.args))


def _clientes_proximo_pedido(clientes, janela='vencidos', dias_janela=3):
    """Filtra a carteira pra lista de Próximo Pedido: só clientes COM ciclo (previsíveis)
    e dentro da janela de contato. Não pagina/ordena (isso fica no _filtrar_carteira).
    - janela='hoje'     → vence exatamente hoje (dias_atraso == 0)
    - janela='atrasados'→ já passou (dias_atraso > 0)
    - janela='vencidos' → no ponto ou atrasado (dias_atraso >= 0) [default]
    - janela='proximos' → vence dentro de `dias_janela` dias OU já vencido (dias_atraso >= -N)
    """
    out = []
    for c in clientes:
        if c.get('ciclo_pessoal') is None or c.get('proximo_pedido_previsto') is None:
            continue  # sem ciclo (1 compra) → não previsível, fica de fora
        da = c.get('dias_atraso')
        if da is None:
            continue
        if janela == 'hoje' and da != 0:
            continue
        if janela == 'atrasados' and da <= 0:
            continue
        if janela == 'vencidos' and da < 0:
            continue
        if janela == 'proximos' and da < -abs(int(dias_janela)):
            continue
        # UI (novas): a ligar nos próximos 7 dias (vence de amanhã até +7, ainda não venceu)
        if janela == 'proximos7' and not (-7 <= da <= -1):
            continue
        # UI: janela acionável = hoje + vencido de 1 a 15 dias (relatório único; 16+ fica de fora)
        if janela == 'vencido15' and not (0 <= da <= 15):
            continue
        out.append(c)
    return out


@app.route('/api/carteira/proximo-pedido')
@login_required
def api_carteira_proximo_pedido():
    """Aba Próximo Pedido: lista diária priorizada de clientes a contatar (previsão pelo
    ciclo de compra). Reusa a carteira cacheada + RBAC; sem DAX novo. Produtos vêm no
    endpoint lazy /api/carteira/cliente/<id>/produtos ao expandir."""
    clientes = _carteira_no_escopo()
    janela = request.args.get('janela', 'vencidos')
    try:
        dias_janela = int(request.args.get('dias', 3))
    except (TypeError, ValueError):
        dias_janela = 3
    elegiveis = _clientes_proximo_pedido(clientes, janela, dias_janela)

    # Reusa filtros (time/vendedor/uf/busca) + paginação; ordena por prioridade desc por padrão.
    args = dict(request.args)
    args.setdefault('sort', 'prioridade')
    args.setdefault('dir', 'desc')
    resp = _filtrar_carteira(elegiveis, args)
    resp['janela'] = janela

    # Cards (acima da tabela): universo de VENCIDOS — independente da janela selecionada,
    # mas respeitando filtros geo (time/vendedor/uf/cidade/busca). Reusa _filtrar_carteira
    # só pros filtros geo (tudo em memória sobre a carteira já cacheada).
    geo = {'limit': 100000, 'offset': 0, '_interno': True}
    for k in ('time', 'vendedor', 'uf', 'cidade', 'busca'):
        v = request.args.get(k)
        if v:
            geo[k] = v
    # 16+ dias vencido o diretor NÃO quer → fora dos cards também (janela acionável = -7 a +15)
    base_all = [c for c in clientes if c.get('ciclo_pessoal') is not None
                and c.get('dias_atraso') is not None and c['dias_atraso'] <= 15]
    base = _filtrar_carteira(base_all, geo)['rows']
    acionaveis = [c for c in base if c['dias_atraso'] >= 0]  # hoje + vencidos 1-15
    top = max(acionaveis, key=lambda c: c.get('prioridade_contato') or 0, default=None)
    resp['cards'] = {
        'hoje':          sum(1 for c in base if c['dias_atraso'] == 0),
        'proximos7':     sum(1 for c in base if -7 <= c['dias_atraso'] <= -1),
        'vencido15':     sum(1 for c in base if 1 <= c['dias_atraso'] <= 15),
        'receita_risco': round(sum(c.get('receita_perdida_proj') or 0 for c in acionaveis), 2),
        'maior_oportunidade': ({
            'codcli':        top['codcli'],
            'cliente':       top.get('cliente'),
            'receita_risco': top.get('receita_perdida_proj') or 0,
        } if top else None),
    }
    return jsonify(resp)


def _top_produtos_cliente(codcli, limit=10):
    """Top N produtos (12m) de um cliente: descrição + qtd + venda. Padrão do drill.
    Cacheado por cliente. NÃO checa escopo — caller deve validar via _carteira_no_escopo."""
    limit = max(1, min(int(limit), 50))
    key = cache_key_for_user(f'carteira:produtos:{codcli}:{limit}')
    cached = _cache_get(key)
    if cached is not None:
        return cached
    q = f"""EVALUATE
TOPN({limit},
    SUMMARIZECOLUMNS(
        FATURAMENTO_VENDAS[CODPROD],
        PCPRODUT[DESCRICAO],
        FILTER(FATURAMENTO_VENDAS,
            FATURAMENTO_VENDAS[CODCLI] = {int(codcli)}
            && FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12)),
        "Venda", [VENDA LIQUIDA],
        "Qt", SUM(FATURAMENTO_VENDAS[QT])
    ),
    [Venda], DESC
)
ORDER BY [Venda] DESC"""
    token = get_token_cached()
    rows = clean_rows(_todas_linhas(retry_dax(execute_dax)(token, q)))
    out = []
    for r in rows:
        cp = r.get('CODPROD')
        if cp is None:
            continue
        out.append({
            'codprod':   cp,
            'descricao': r.get('DESCRICAO') or f'Produto {cp}',
            'qt_12m':    r.get('Qt') or 0,
            'venda_12m': r.get('Venda') or 0,
        })
    _cache_set(key, out, 'dax_lista')
    return out


def _top_produtos_varios(codclis, limit_por_cliente=5):
    """Top N produtos por cliente em UMA query (CODCLI IN). Pra email/lote — escopado por
    vendedor/área é leve (teste: 628 clientes → 7.874 linhas/2s). Cap em 300 clientes pra
    não estourar o teto de 100k linhas do Power BI. Retorna {codcli: [{descricao,venda_12m}]}."""
    ids_int = [int(c) for c in codclis if c is not None][:300]
    if not ids_int:
        return {}
    ids = ", ".join(str(c) for c in ids_int)
    q = f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODCLI],
    FATURAMENTO_VENDAS[CODPROD],
    PCPRODUT[DESCRICAO],
    FILTER(FATURAMENTO_VENDAS,
        FATURAMENTO_VENDAS[CODCLI] IN {{{ids}}}
        && FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12)),
    "Venda", [VENDA LIQUIDA]
)"""
    token = get_token_cached()
    rows = clean_rows(_todas_linhas(retry_dax(execute_dax)(token, q)))
    por_cli = {}
    for r in rows:
        cc = r.get('CODCLI')
        cp = r.get('CODPROD')
        if cc is None or cp is None:
            continue
        por_cli.setdefault(int(cc), []).append({
            'descricao': r.get('DESCRICAO') or f'Produto {cp}',
            'venda_12m': r.get('Venda') or 0,
        })
    out = {}
    for cc, lst in por_cli.items():
        lst.sort(key=lambda x: x['venda_12m'], reverse=True)
        out[cc] = lst[:limit_por_cliente]
    return out


@app.route('/api/carteira/cliente/<int:codcli>/produtos')
@login_required
def api_carteira_cliente_produtos(codcli):
    """Lazy: top N produtos do cliente pra aba Próximo Pedido (oferecer na ligação).
    Guarda de escopo igual ao drill — não revela produto de cliente fora da carteira."""
    try:
        limit = int(request.args.get('limit', 10))
    except (TypeError, ValueError):
        limit = 10
    clientes = _carteira_no_escopo()
    if not any(c['codcli'] == codcli for c in clientes):
        return jsonify({'ok': False, 'error': 'Cliente não encontrado na sua carteira'}), 404
    return jsonify({'ok': True, 'codcli': codcli, 'produtos': _top_produtos_cliente(codcli, limit)})


@app.route('/api/carteira/cliente/<int:codcli>')
@login_required
def api_carteira_cliente(codcli):
    """Drill 360°: dados do cliente + histórico mensal 12m + top 5 categorias."""
    key = cache_key_for_user(f'carteira:cliente:{codcli}')
    cached = _cache_get(key)
    if cached:
        return jsonify(cached)

    # Cliente vem do escopo de CADASTRO do usuário — serve de guarda: se não está no
    # escopo dele, retorna 404 (não revela/abre cliente de fora da carteira dele).
    clientes = _carteira_no_escopo()
    cliente = next((c for c in clientes if c['codcli'] == codcli), None)
    if not cliente:
        return jsonify({'ok': False, 'error': 'Cliente não encontrado na sua carteira'}), 404

    # Histórico do drill = TOTAL do cliente (sem filtro de venda) — coerente com a
    # carteira por cadastro: o cliente é meu, mostro tudo dele.
    rbac_frag = ""
    rbac_d_frag = ""
    rbac_da_frag = ""

    queries = {
        'hist_vendas': f"""EVALUATE
SUMMARIZECOLUMNS(
    CALENDARIO[AnoMes],
    FILTER(FATURAMENTO_VENDAS,
        FATURAMENTO_VENDAS[CODCLI] = {codcli}
        && FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12){rbac_frag}),
    "Bruta", [VENDA BRUTA],
    "Custo", [CUSTO TOTAL]
)""",
        'hist_devol': f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_DEVOLUCAO[DTENT],
    FILTER(FATURAMENTO_DEVOLUCAO,
        FATURAMENTO_DEVOLUCAO[CODCLI] = {codcli}
        && FATURAMENTO_DEVOLUCAO[DTENT] >= EDATE(TODAY(), -12){rbac_d_frag}),
    "Devol", [TOTAL DEVOLUCAO],
    "CDevol", [CUSTO TOTAL DEVOLUCAO]
)""",
        'hist_devol_av': f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_DEVOLUCAO_AVULSA[DTENT],
    FILTER(FATURAMENTO_DEVOLUCAO_AVULSA,
        FATURAMENTO_DEVOLUCAO_AVULSA[CODCLI] = {codcli}
        && FATURAMENTO_DEVOLUCAO_AVULSA[DTENT] >= EDATE(TODAY(), -12){rbac_da_frag}),
    "DevolA", [TOTAL DEVOLUCAO AVULSA],
    "CDevolA", [CUSTO TOTAL DEVOLUCAO AVULSA]
)""",
        # Top deptos — apenas vendas (devoluções costumam ser concentradas e impactam pouco no ranking)
        'deptos': f"""EVALUATE
TOPN(5,
    SUMMARIZECOLUMNS(
        FATURAMENTO_VENDAS[CODEPTO],
        FILTER(FATURAMENTO_VENDAS,
            FATURAMENTO_VENDAS[CODCLI] = {codcli}
            && FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12){rbac_frag}),
        "VendaLiquida", [VENDA LIQUIDA],
        "LucroTotal",   [LUCRO TOTAL]
    ),
    [VendaLiquida], DESC
)""",
    }
    try:
        resultados = _executar_dax_paralelo_n(queries, max_workers=4)
        hist_vendas_rows = clean_rows(_todas_linhas(resultados['hist_vendas']))
        hist_devol_rows = clean_rows(_todas_linhas(resultados['hist_devol']))
        hist_devol_av_rows = clean_rows(_todas_linhas(resultados['hist_devol_av']))
        deptos_rows = clean_rows(_todas_linhas(resultados['deptos']))
    except Exception as e:
        hist_vendas_rows, hist_devol_rows, hist_devol_av_rows, deptos_rows = [], [], [], []

    # Merge histórico mensal: AnoMes → (bruta, custo, devol, c_devol, devol_av, c_devol_av)
    def _dtent_para_am(s):
        if not s: return None
        try: s = str(s)[:10]; return int(s[:4]) * 100 + int(s[5:7])
        except (ValueError, IndexError): return None

    historico_dict = {}  # am → {b, ct, dv, cdv, dva, cdva}
    for r in hist_vendas_rows:
        am = r.get('AnoMes')
        if am is None: continue
        historico_dict.setdefault(int(am), {'b': 0, 'ct': 0, 'dv': 0, 'cdv': 0, 'dva': 0, 'cdva': 0})
        historico_dict[int(am)]['b']  = r.get('Bruta') or 0
        historico_dict[int(am)]['ct'] = r.get('Custo') or 0
    for r in hist_devol_rows:
        am = _dtent_para_am(r.get('DTENT'))
        if am is None: continue
        historico_dict.setdefault(am, {'b': 0, 'ct': 0, 'dv': 0, 'cdv': 0, 'dva': 0, 'cdva': 0})
        historico_dict[am]['dv']  += r.get('Devol') or 0
        historico_dict[am]['cdv'] += r.get('CDevol') or 0
    for r in hist_devol_av_rows:
        am = _dtent_para_am(r.get('DTENT'))
        if am is None: continue
        historico_dict.setdefault(am, {'b': 0, 'ct': 0, 'dv': 0, 'cdv': 0, 'dva': 0, 'cdva': 0})
        historico_dict[am]['dva']  += r.get('DevolA') or 0
        historico_dict[am]['cdva'] += r.get('CDevolA') or 0

    historico = []
    for am in sorted(historico_dict.keys()):
        d = historico_dict[am]
        vl = round(d['b'] - d['dv'] - d['dva'], 2)
        lucro = round(vl - (d['ct'] - d['cdv'] - d['cdva']), 2)
        historico.append({'AnoMes': am, 'VendaLiquida': vl, 'LucroTotal': lucro})

    # Enriquece com nome textual do depto
    mapa = _carregar_deptos_map()
    deptos_nomes = mapa['deptos']
    deptos = []
    for r in deptos_rows:
        cd = r.get('CODEPTO')
        nome = deptos_nomes.get(str(cd)) if cd is not None else None
        deptos.append({
            'codepto':      cd,
            'nome':         nome or (f'Depto {cd}' if cd is not None else '(sem depto)'),
            'tem_nome':     bool(nome),
            'VendaLiquida': r.get('VendaLiquida') or 0,
            'LucroTotal':   r.get('LucroTotal') or 0,
        })

    resp = {
        'ok':         True,
        'cliente':    cliente,
        'historico':  historico,
        'deptos':     deptos,
        # Mantém 'categorias' como alias temporário pra compatibilidade do frontend antigo
        'categorias': deptos,
    }
    _cache_set(key, resp, 'dax_agregado')
    return jsonify(resp)


def _slug_export(texto):
    """Limpa um pedaço de nome de arquivo: remove caracteres inválidos, colapsa espaços, corta em 40."""
    import re
    s = re.sub(r'[\\/:*?"<>|]+', '', str(texto))
    s = re.sub(r'\s+', ' ', s).strip()
    return s[:40]


def _nome_arquivo_export(ext):
    """Monta o nome do arquivo de export a partir dos filtros ativos (vendedor, time, uf, cidade),
    resolvidos pra nome legível + data. Ex: 'JOSE JUNIOR_TIME RJ_BA_ILHEUS_2026-06-09.pdf'.
    Sem nenhum desses 4 filtros → 'carteira_todos_<data>.<ext>'."""
    from datetime import date as _date

    def _nomes_por_codigo(valor, mapa):
        nomes = []
        for cod in str(valor).split(','):
            cod = cod.strip()
            if not cod:
                continue
            item = mapa.get(cod)
            nomes.append(item.get('nome') if item and item.get('nome') else cod)
        return '+'.join(nomes)

    def _valores(valor):
        return '+'.join(v.strip() for v in str(valor).split(',') if v.strip())

    partes = []
    if request.args.get('vendedor'):
        partes.append(_nomes_por_codigo(request.args['vendedor'], _carregar_vendedores_map()))
    if request.args.get('time'):
        partes.append(_nomes_por_codigo(request.args['time'], _carregar_supervisores_map()))
    if request.args.get('uf'):
        partes.append(_valores(request.args['uf']))
    if request.args.get('cidade'):
        partes.append(_valores(request.args['cidade']))

    data = _date.today().isoformat()
    partes_limpas = [_slug_export(p) for p in partes if _slug_export(p)]
    base = '_'.join(partes_limpas) if partes_limpas else 'carteira_todos'
    return f"{base}_{data}.{ext}"


def _content_disposition(nome):
    """Header Content-Disposition com filename ASCII (fallback) + filename* UTF-8 (RFC 5987),
    pra acentos/espaços sobreviverem em qualquer navegador."""
    from urllib.parse import quote
    ascii_fallback = nome.encode('ascii', 'ignore').decode('ascii').strip() or 'carteira.bin'
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(nome)}"


@app.route('/api/carteira/csv')
@login_required
def api_carteira_csv():
    """Export CSV streaming. Reutiliza _filtrar_carteira pra cobrir TODOS os filtros
    (multi-status, multi-segmento, uf, cidade, time, busca codusur/vendedor, etc)."""
    from datetime import date as _date
    modo = request.args.get('modo', 'personalizada')

    clientes = _carteira_no_escopo()
    status_key = 'status_personalizada' if modo == 'personalizada' else 'status_fixa'

    # Usa _filtrar_carteira passando limit alto pra pegar TUDO (sem paginação)
    args_csv = dict(request.args)
    args_csv['limit'] = 100000
    args_csv['offset'] = 0
    args_csv['_interno'] = True   # libera cap de 1000 — exporta TUDO mesmo
    args_csv['sort'] = 'receita_perdida'  # ordena por receita perdida desc
    args_csv['dir'] = 'desc'
    resultado = _filtrar_carteira(clientes, args_csv)
    filtrados = resultado['rows']

    cabecalho = [
        'CodCli', 'Cliente', 'Fantasia', 'Cidade', 'UF',
        'CodUsur', 'Vendedor', 'CodSupervisor', 'Supervisor', 'Telefone',
        'UltimaCompra', 'RecenciaDias', 'Frequencia12m', 'CicloPessoal',
        'Venda12m', 'MediaVenda12m', 'Lucro12m', 'ReceitaPerdidaProj', 'LucroPerdidoProj',
        'Segmento', 'R', 'F', 'M', 'Bloqueio',
    ]

    def gerar():
        yield CSV_PREAMBULO  # BOM UTF-8
        yield _csv_linha(cabecalho)
        for c in filtrados:
            venda = c.get('venda_12m') or 0
            yield _csv_linha([
                c.get('codcli'),
                c.get('cliente'),
                c.get('fantasia'),
                c.get('cidade'),
                c.get('uf'),
                c.get('codusur'),
                c.get('vendedor'),
                c.get('codsupervisor'),
                c.get('time'),
                c.get('telefone'),
                c.get('ultima_compra'),
                c.get('recencia_dias'),
                c.get('frequencia_12m'),
                c.get('ciclo_pessoal'),
                venda,
                round(venda / 12, 2),  # MediaVenda12m
                c.get('lucro_12m'),
                c.get('receita_perdida_proj'),
                c.get('lucro_perdido_proj'),
                c.get('segmento'),
                c.get('r'),
                c.get('f'),
                c.get('m'),
                c.get('bloqueio'),
            ])

    nome = _nome_arquivo_export('csv')
    return Response(
        stream_with_context(gerar()),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': _content_disposition(nome)},
    )


def _gerar_pdf_carteira(filtrados, filtros_resumo=''):
    """Gera PDF da carteira filtrada. Retorna bytes do PDF.
    Colunas: CodCli · Cliente · Cidade/UF · Vendedor · R · Segmento · Telefone · Venda 12m · Média Venda."""
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    from io import BytesIO
    from datetime import date as _date

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=1.2*cm, rightMargin=1.2*cm,
        topMargin=1.2*cm, bottomMargin=1.5*cm,
        title=f"Carteira JOGA {_date.today().isoformat()}",
    )
    styles = getSampleStyleSheet()
    titulo_style = ParagraphStyle('titulo', parent=styles['Heading1'], fontSize=14, alignment=TA_LEFT, textColor=colors.HexColor('#0a0e17'))
    sub_style = ParagraphStyle('sub', parent=styles['Normal'], fontSize=8, textColor=colors.HexColor('#475569'))

    story = []
    story.append(Paragraph('<b>JOGA Analytics</b> — Carteira', titulo_style))
    story.append(Paragraph(f"Gerado em {_date.today().strftime('%d/%m/%Y')} · {len(filtrados)} clientes" + (f" · {filtros_resumo}" if filtros_resumo else ''), sub_style))
    story.append(Spacer(1, 0.3*cm))

    header = ['CodCli', 'Cliente', 'Cidade/UF', 'Vendedor', 'R (dias)', 'Segmento', 'Telefone', 'Venda 12m', 'Média Venda']
    data = [header]
    NOME_SEG_PT = {
        'champions': 'Campeões', 'loyal': 'Fiéis', 'cant_lose': 'Não Perder', 'at_risk': 'Em Risco',
        'potential_loyalist': 'Promissores', 'new': 'Novos', 'hibernating': 'Inativos', 'lost': 'Perdidos',
    }
    for c in filtrados:
        venda = c.get('venda_12m') or 0
        cliente_nome = (c.get('cliente') or '')[:42]
        cidade_uf = f"{(c.get('cidade') or '')[:20]}/{c.get('uf') or ''}"
        vendedor = (c.get('vendedor') or '')[:22]
        seg_pt = NOME_SEG_PT.get(c.get('segmento') or '', c.get('segmento') or '')
        data.append([
            c.get('codcli') or '',
            cliente_nome,
            cidade_uf,
            vendedor,
            c.get('recencia_dias') or '',
            seg_pt,
            c.get('telefone') or '',
            f"R$ {venda:,.0f}".replace(',', '.'),
            f"R$ {(venda/12):,.0f}".replace(',', '.'),
        ])
    tbl = Table(data, repeatRows=1, colWidths=[1.5*cm, 6.5*cm, 4*cm, 4*cm, 1.5*cm, 2.4*cm, 3*cm, 2.4*cm, 2.4*cm])
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1e293b')),
        ('TEXTCOLOR',  (0,0), (-1,0), colors.white),
        ('FONTNAME',   (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE',   (0,0), (-1,-1), 7),
        ('GRID',       (0,0), (-1,-1), 0.3, colors.HexColor('#cbd5e1')),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f8fafc')]),
        ('ALIGN',      (4,0), (4,-1), 'CENTER'),
        ('ALIGN',      (7,0), (8,-1), 'RIGHT'),
        ('VALIGN',     (0,0), (-1,-1), 'MIDDLE'),
        ('LEFTPADDING',(0,0), (-1,-1), 4),
        ('RIGHTPADDING',(0,0), (-1,-1), 4),
    ]))
    story.append(tbl)

    def _rodape(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(colors.HexColor('#94a3b8'))
        canvas.drawRightString(doc.pagesize[0] - 1.2*cm, 0.8*cm, f"Página {doc.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_rodape, onLaterPages=_rodape)
    return buf.getvalue()


def _gerar_pdf_proximo_pedido(filtrados, filtros_resumo=''):
    """PDF da Lista do Dia (Próximo Pedido). Colunas: Cliente · Cidade/UF · Telefone ·
    Últ.compra · Ciclo · Previsão · Atraso · Status · Venda 12m · Receita em risco."""
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    from io import BytesIO
    from datetime import date as _date

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=1.2*cm, rightMargin=1.2*cm, topMargin=1.2*cm, bottomMargin=1.5*cm,
        title=f"Proximo Pedido JOGA {_date.today().isoformat()}",
    )
    styles = getSampleStyleSheet()
    titulo_style = ParagraphStyle('titulo', parent=styles['Heading1'], fontSize=14, alignment=TA_LEFT, textColor=colors.HexColor('#0a0e17'))
    sub_style = ParagraphStyle('sub', parent=styles['Normal'], fontSize=8, textColor=colors.HexColor('#475569'))

    story = []
    story.append(Paragraph('<b>JOGA Analytics</b> — Lista do Dia (Próximo Pedido)', titulo_style))
    story.append(Paragraph(f"Gerado em {_date.today().strftime('%d/%m/%Y')} · {len(filtrados)} clientes a contatar" + (f" · {filtros_resumo}" if filtros_resumo else ''), sub_style))
    story.append(Spacer(1, 0.3*cm))

    STATUS_PT = {'ok': 'No prazo', 'normal': 'Normal', 'atencao': 'Atenção', 'urgente': 'Urgente'}
    header = ['Cliente', 'Cidade/UF', 'Telefone', 'Últ.compra', 'Ciclo', 'Previsão', 'Atraso', 'Status', 'Venda 12m', 'Rec. risco']
    data = [header]
    for c in filtrados:
        venda = c.get('venda_12m') or 0
        risco = c.get('receita_perdida_proj') or 0
        da = c.get('dias_atraso')
        atraso_txt = 'hoje' if da == 0 else (f"{da}d" if da is not None else '—')
        data.append([
            (c.get('cliente') or '')[:38],
            f"{(c.get('cidade') or '')[:16]}/{c.get('uf') or ''}",
            c.get('telefone') or '—',
            c.get('ultima_compra') or '—',
            f"{c.get('ciclo_pessoal') or ''}d",
            c.get('proximo_pedido_previsto') or '—',
            atraso_txt,
            STATUS_PT.get(c.get('status_personalizada') or '', c.get('status_personalizada') or ''),
            f"R$ {venda:,.0f}".replace(',', '.'),
            f"R$ {risco:,.0f}".replace(',', '.'),
        ])
    tbl = Table(data, repeatRows=1, colWidths=[5.6*cm, 3*cm, 2.9*cm, 2*cm, 1.2*cm, 2*cm, 1.5*cm, 1.9*cm, 2.3*cm, 2.4*cm])
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1e293b')),
        ('TEXTCOLOR',  (0,0), (-1,0), colors.white),
        ('FONTNAME',   (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE',   (0,0), (-1,-1), 7),
        ('GRID',       (0,0), (-1,-1), 0.3, colors.HexColor('#cbd5e1')),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f8fafc')]),
        ('ALIGN',      (4,0), (7,-1), 'CENTER'),
        ('ALIGN',      (8,0), (9,-1), 'RIGHT'),
        ('VALIGN',     (0,0), (-1,-1), 'MIDDLE'),
        ('LEFTPADDING',(0,0), (-1,-1), 4),
        ('RIGHTPADDING',(0,0), (-1,-1), 4),
    ]))
    story.append(tbl)

    def _rodape(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(colors.HexColor('#94a3b8'))
        canvas.drawRightString(doc.pagesize[0] - 1.2*cm, 0.8*cm, f"Página {doc.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_rodape, onLaterPages=_rodape)
    return buf.getvalue()


@app.route('/api/carteira/proximo-pedido/pdf')
@login_required
def api_carteira_proximo_pedido_pdf():
    """PDF da Lista do Dia — mesma janela/filtros da tabela, SEMPRE ordenado por data de
    previsão crescente (agenda cronológica de ligação), ignorando o sort da tela."""
    from datetime import date as _date
    clientes = _carteira_no_escopo()
    janela = request.args.get('janela', 'vencidos')
    try:
        dias_janela = int(request.args.get('dias', 3))
    except (TypeError, ValueError):
        dias_janela = 3
    elegiveis = _clientes_proximo_pedido(clientes, janela, dias_janela)
    args = dict(request.args)
    args.update({'limit': 100000, 'offset': 0, '_interno': True,
                 'sort': 'proximo_pedido', 'dir': 'asc'})
    filtrados = _filtrar_carteira(elegiveis, args)['rows']

    parts = [f"Janela: {janela}"]
    for k, lbl in (('time', 'Time'), ('vendedor', 'Vendedor'), ('uf', 'UF'), ('busca', 'Busca')):
        if request.args.get(k):
            parts.append(f"{lbl}: {request.args.get(k)}")
    pdf = _gerar_pdf_proximo_pedido(filtrados, ' · '.join(parts))
    from flask import Response
    return Response(pdf, mimetype='application/pdf', headers={
        'Content-Disposition': f'attachment; filename="{_nome_arquivo_proximo("pdf")}"'
    })


def _nome_arquivo_proximo(ext):
    """Nome do arquivo da Lista do Dia conforme filtros aplicados (códigos → nomes).
    Ex: proximo_hoje_AFONSO_ES_SUL_MANOEL_DE_SOUZA_2026-06-30.csv"""
    from datetime import date as _date
    partes = [_slug_export(request.args.get('janela') or 'vencidos')]
    time_filt = request.args.get('time')
    vendedor = request.args.get('vendedor')
    uf = request.args.get('uf')
    busca = request.args.get('busca')
    if time_filt:
        s = _carregar_supervisores_map().get(str(time_filt))
        partes.append(_slug_export(s.get('nome') if s else f'Sup{time_filt}'))
    if vendedor:
        v = _carregar_vendedores_map().get(str(vendedor))
        partes.append(_slug_export(v.get('nome') if v else f'RCA{vendedor}'))
    if uf:
        partes.append(_slug_export(uf))
    if busca:
        partes.append(_slug_export(f'busca-{busca}'))
    partes_limpas = [p.replace(' ', '_') for p in partes if p]
    base = '_'.join(['proximo'] + partes_limpas)
    return f"{base}_{_date.today().isoformat()}.{ext}"


@app.route('/api/carteira/proximo-pedido/csv')
@login_required
def api_carteira_proximo_pedido_csv():
    """CSV da Lista do Dia — mesma janela/filtros da tabela, nome conforme filtros."""
    clientes = _carteira_no_escopo()
    janela = request.args.get('janela', 'vencidos')
    try:
        dias_janela = int(request.args.get('dias', 3))
    except (TypeError, ValueError):
        dias_janela = 3
    elegiveis = _clientes_proximo_pedido(clientes, janela, dias_janela)
    args = dict(request.args)
    args.update({'limit': 100000, 'offset': 0, '_interno': True})
    args.setdefault('sort', 'prioridade')
    args.setdefault('dir', 'desc')
    rows = _filtrar_carteira(elegiveis, args)['rows']

    head = ['CodCli', 'Cliente', 'Cidade', 'UF', 'Telefone', 'UltimaCompra', 'Ciclo',
            'Previsao', 'DiasAtraso', 'Status', 'Venda12m', 'ReceitaEmRisco']
    linhas = [CSV_PREAMBULO + _csv_linha(head).rstrip('\n')]
    for c in rows:
        linhas.append(_csv_linha([
            c.get('codcli'), c.get('cliente'), c.get('cidade'), c.get('uf'), c.get('telefone'),
            c.get('ultima_compra'), c.get('ciclo_pessoal'), c.get('proximo_pedido_previsto'),
            c.get('dias_atraso'), c.get('status_personalizada'),
            c.get('venda_12m') or 0, c.get('receita_perdida_proj') or 0,
        ]).rstrip('\n'))
    csv_bytes = ('\n'.join(linhas) + '\n').encode('utf-8')
    from flask import Response
    return Response(csv_bytes, mimetype='text/csv; charset=utf-8', headers={
        'Content-Disposition': f'attachment; filename="{_nome_arquivo_proximo("csv")}"'
    })


@app.route('/api/carteira/pdf')
@login_required
def api_carteira_pdf():
    """Export PDF da carteira filtrada — colunas pedidas pelo diretor."""
    from datetime import date as _date

    clientes = _carteira_no_escopo()
    args_pdf = dict(request.args)
    args_pdf['limit'] = 100000
    args_pdf['offset'] = 0
    args_pdf['_interno'] = True   # libera cap de 1000
    args_pdf['sort'] = args_pdf.get('sort', 'receita_perdida')
    args_pdf['dir'] = args_pdf.get('dir', 'desc')
    resultado = _filtrar_carteira(clientes, args_pdf)
    filtrados = resultado['rows']

    # Resumo de filtros pra cabeçalho do PDF
    parts = []
    if request.args.get('time'):     parts.append(f"Time: {request.args.get('time')}")
    if request.args.get('vendedor'): parts.append(f"Vendedor: {request.args.get('vendedor')}")
    if request.args.get('uf'):       parts.append(f"UF: {request.args.get('uf')}")
    if request.args.get('cidade'):   parts.append(f"Cidade: {request.args.get('cidade')}")
    if request.args.get('segmento'): parts.append(f"Segmento: {request.args.get('segmento')}")
    if request.args.get('status'):   parts.append(f"Status: {request.args.get('status')}")
    if request.args.get('busca'):    parts.append(f"Busca: {request.args.get('busca')}")
    filtros_resumo = ' · '.join(parts)

    pdf_bytes = _gerar_pdf_carteira(filtrados, filtros_resumo=filtros_resumo)
    nome = _nome_arquivo_export('pdf')
    return Response(
        pdf_bytes,
        mimetype='application/pdf',
        headers={'Content-Disposition': _content_disposition(nome)},
    )


@app.route('/api/carteira/evolucao')
@login_required
def api_carteira_evolucao():
    """Clientes positivados (únicos) por mês nos últimos N meses."""
    try:
        meses = max(3, min(int(request.args.get('meses', 12)), 24))
    except (TypeError, ValueError):
        meses = 12
    key = cache_key_for_user('carteira:evolucao', {'meses': meses})
    cached = _cache_get(key)
    if cached:
        return jsonify(cached)

    rbac = aplicar_rbac_dax()
    rbac_frag = f" && {rbac}" if rbac else ""

    query = f"""EVALUATE
SUMMARIZECOLUMNS(
    CALENDARIO[AnoMes],
    FILTER(FATURAMENTO_VENDAS, FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -{meses}){rbac_frag}),
    "ClientesUnicos", DISTINCTCOUNT(FATURAMENTO_VENDAS[CODCLI]),
    "Compras",        DISTINCTCOUNT(FATURAMENTO_VENDAS[NUMNOTA])
)
ORDER BY CALENDARIO[AnoMes]"""
    token = get_token_cached()
    payload = retry_dax(execute_dax)(token, query)
    rows = clean_rows(_todas_linhas(payload))
    resp = {'ok': True, 'rows': rows}
    _cache_set(key, resp, 'dax_agregado')
    return jsonify(resp)


# ──────────────────────────────────────────────────────────────────────
# Página Gerencial — Cobertura de Carteira (placar Empresa/Time/RCA)
# Motor puro cobertura.py sobre _carteira_no_escopo() (RBAC por cadastro já aplicado).
# Zero DAX novo — só agrega a carteira já cacheada. Alerta por email reusa cron+Resend.
# ──────────────────────────────────────────────────────────────────────


def _coberto_dias_arg():
    """Lê ?coberto_dias= (toggle 30/45/60), com fallback pro default configurável."""
    try:
        v = int(request.args.get('coberto_dias', _cobertura_coberto_dias()))
        return v if v in (30, 45, 60) else _cobertura_coberto_dias()
    except (TypeError, ValueError):
        return _cobertura_coberto_dias()


@app.route('/gerencial')
@login_required
def gerencial_page():
    return send_from_directory('.', 'gerencial.html')


@app.route('/api/gerencial/cobertura')
@login_required
def api_gerencial_cobertura():
    """Placar de cobertura em 3 níveis (empresa/times/vendedores) sobre a carteira no escopo
    do usuário. O frontend faz o drill in-memory a partir deste payload único."""
    coberto_dias = _coberto_dias_arg()
    limiar_pct = _cobertura_limiar_pct()
    key = cache_key_for_user('gerencial:cobertura', {'coberto_dias': coberto_dias, 'limiar': limiar_pct})
    cached = _cache_get(key)
    if cached:
        return jsonify(cached)

    clientes = _carteira_no_escopo()
    niveis = cob.agregar_niveis(clientes, coberto_dias=coberto_dias)
    baixos = cob.times_rcas_abaixo(niveis, limiar_pct)
    resp = {
        'ok': True,
        'limiar_pct': limiar_pct,
        'abaixo_do_limiar': {
            'times': len(baixos['times']),
            'vendedores': len(baixos['vendedores']),
        },
        **niveis,
    }
    _cache_set(key, resp, 'dax_lista')  # TTL curto (5min): reflete ajuste de limiar rápido
    return jsonify(resp)


def _cobertura_csv_linhas(niveis):
    """Gera as linhas do CSV de cobertura (empresa + cada time + cada vendedor, com faixas)."""
    faixas = [chave for chave, _, _ in cob.FAIXAS]
    cabecalho = (
        ['Nivel', 'Nome', 'TotalClientes', 'ValorTotal', 'Cobertura%Clientes', 'Cobertura%Valor',
         '0-30Clientes', 'DentroDoCiclo%', 'ReceitaEmRisco', 'BaseMorta']
        + [f'Faixa {f} (clientes)' for f in faixas]
        + [f'Faixa {f} (valor)' for f in faixas]
    )
    yield CSV_PREAMBULO + _csv_linha(cabecalho).rstrip('\n')

    def _linha(nivel, g):
        buckets = {b['faixa']: b for b in g['buckets']}
        vals = [
            nivel, g.get('nome', ''), g['total_clientes'], g['valor_total'],
            round(g['cobertura_clientes'] * 100, 1), round(g['cobertura_valor'] * 100, 1),
            g['rollup_0_30']['clientes'], round(g['cobertura_ciclo'] * 100, 1),
            g['receita_em_risco'], g['base_morta'],
        ]
        vals += [buckets[f]['clientes'] for f in faixas]
        vals += [buckets[f]['valor'] for f in faixas]
        return _csv_linha(vals).rstrip('\n')

    yield _linha('EMPRESA', {**niveis['empresa'], 'nome': 'Empresa (escopo)'})
    for t in niveis['times']:
        yield _linha('TIME', t)
    for v in niveis['vendedores']:
        yield _linha('RCA', v)


@app.route('/api/gerencial/cobertura/csv')
@login_required
def api_gerencial_cobertura_csv():
    """Export CSV do placar completo (empresa + times + RCAs + faixas)."""
    from datetime import date as _date
    coberto_dias = _coberto_dias_arg()
    clientes = _carteira_no_escopo()
    niveis = cob.agregar_niveis(clientes, coberto_dias=coberto_dias)

    def gerar():
        for linha in _cobertura_csv_linhas(niveis):
            yield linha + '\n'

    nome = f"cobertura_gerencial_{coberto_dias}d_{_date.today().isoformat()}.csv"
    return Response(
        stream_with_context(gerar()),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': _content_disposition(nome)},
    )


def _gerar_pdf_cobertura(niveis, coberto_dias, limiar_pct):
    """PDF do placar gerencial: resumo da empresa + ranking de times + ranking de RCAs."""
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    from io import BytesIO
    from datetime import date as _date

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=1.2 * cm, rightMargin=1.2 * cm, topMargin=1.2 * cm, bottomMargin=1.5 * cm,
        title=f"Cobertura Gerencial JOGA {_date.today().isoformat()}",
    )
    styles = getSampleStyleSheet()
    titulo_style = ParagraphStyle('titulo', parent=styles['Heading1'], fontSize=14, alignment=TA_LEFT, textColor=colors.HexColor('#0a0e17'))
    sub_style = ParagraphStyle('sub', parent=styles['Normal'], fontSize=8, textColor=colors.HexColor('#475569'))
    limiar_frac = (limiar_pct or 0) / 100.0

    def _brl(v):
        return f"R$ {(v or 0):,.0f}".replace(',', '.')

    def _pct(v):
        return f"{(v or 0) * 100:.1f}%".replace('.', ',')

    emp = niveis['empresa']
    story = []
    story.append(Paragraph('<b>JOGA Analytics</b> — Cobertura de Carteira (Gerencial)', titulo_style))
    story.append(Paragraph(
        f"Gerado em {_date.today().strftime('%d/%m/%Y')} · Coberto = ≤{coberto_dias} dias · "
        f"Limiar baixa performance = {limiar_pct:.0f}% · Empresa: {_pct(emp['cobertura_clientes'])} clientes / "
        f"{_pct(emp['cobertura_valor'])} valor · Receita em risco {_brl(emp['receita_em_risco'])}", sub_style))
    story.append(Spacer(1, 0.3 * cm))

    def _tabela_ranking(titulo, itens):
        story.append(Paragraph(f"<b>{titulo}</b>", sub_style))
        header = ['Nome', 'Clientes', 'Cob.% clientes', 'Cob.% valor', 'Dentro do ciclo', 'Receita em risco', 'Base morta', '⚑']
        data = [header]
        for g in itens:
            abaixo = g['cobertura_clientes'] < limiar_frac
            data.append([
                (g.get('nome') or '')[:32] + (' *' if g.get('amostra_pequena') else ''),
                g['total_clientes'], _pct(g['cobertura_clientes']), _pct(g['cobertura_valor']),
                _pct(g['cobertura_ciclo']), _brl(g['receita_em_risco']), g['base_morta'],
                'BAIXA' if abaixo else '',
            ])
        tbl = Table(data, repeatRows=1, colWidths=[7 * cm, 2 * cm, 3 * cm, 2.8 * cm, 3 * cm, 3.2 * cm, 2 * cm, 1.8 * cm])
        tbl.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e293b')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 7),
            ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#cbd5e1')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
            ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        story.append(tbl)
        story.append(Spacer(1, 0.4 * cm))

    _tabela_ranking('Times (pior → melhor)', niveis['times'])
    _tabela_ranking('RCAs / Vendedores (pior → melhor)', niveis['vendedores'])
    story.append(Paragraph('* amostra pequena (poucos clientes) — % pode não ser representativo.', sub_style))

    def _rodape(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(colors.HexColor('#94a3b8'))
        canvas.drawRightString(doc.pagesize[0] - 1.2 * cm, 0.8 * cm, f"Página {doc.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_rodape, onLaterPages=_rodape)
    return buf.getvalue()


@app.route('/api/gerencial/cobertura/pdf')
@login_required
def api_gerencial_cobertura_pdf():
    from datetime import date as _date
    coberto_dias = _coberto_dias_arg()
    limiar_pct = _cobertura_limiar_pct()
    clientes = _carteira_no_escopo()
    niveis = cob.agregar_niveis(clientes, coberto_dias=coberto_dias)
    pdf = _gerar_pdf_cobertura(niveis, coberto_dias, limiar_pct)
    nome = f"cobertura_gerencial_{coberto_dias}d_{_date.today().isoformat()}.pdf"
    return Response(pdf, mimetype='application/pdf',
                    headers={'Content-Disposition': _content_disposition(nome)})


@app.route('/api/admin/config/cobertura', methods=['GET'])
@admin_required
def api_admin_config_cobertura_get():
    return jsonify({
        'ok': True,
        'limiar_pct': _cobertura_limiar_pct(),
        'coberto_dias': _cobertura_coberto_dias(),
    })


@app.route('/api/admin/config/cobertura', methods=['PUT'])
@admin_required
def api_admin_config_cobertura_set():
    data = request.get_json() or {}
    if 'limiar_pct' in data:
        try:
            lim = float(data['limiar_pct'])
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'error': 'limiar_pct inválido'}), 400
        if not (0 <= lim <= 100):
            return jsonify({'ok': False, 'error': 'limiar_pct deve estar entre 0 e 100'}), 400
        _config_set('cobertura_limiar_pct', lim)
    if 'coberto_dias' in data:
        try:
            dias = int(data['coberto_dias'])
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'error': 'coberto_dias inválido'}), 400
        if dias not in (30, 45, 60):
            return jsonify({'ok': False, 'error': 'coberto_dias deve ser 30, 45 ou 60'}), 400
        _config_set('cobertura_coberto_dias', dias)
    return jsonify({'ok': True, 'limiar_pct': _cobertura_limiar_pct(), 'coberto_dias': _cobertura_coberto_dias()})


# ──────────────────────────────────────────────────────────────────────
# Vendedores + Cockpit individual (Onda C)
# RBAC: aplicar_rbac_dax() nas queries + pode_acessar_vendedor() em endpoints com <codusur>.
# YoY: medida pronta [Crescimento Ano a Ano Receita Liquida] retorna NULL quando dentro
# de SUMMARIZECOLUMNS por CODUSUR (validado no REPL 2026-05-23). Usamos FALLBACK manual:
# 2 queries paralelas (12m atual + 12m anterior) → cálculo Python (atual-ant)/ant.
# ──────────────────────────────────────────────────────────────────────


@app.route('/vendedores')
@login_required
def vendedores_page():
    if session.get('role') == 'vendedor':
        return redirect(f"/vendedor/{session.get('codusur')}")
    return send_from_directory('.', 'vendedores.html')


@app.route('/vendedor/<int:codusur>')
@login_required
def vendedor_page(codusur):
    if not pode_acessar_vendedor(codusur):
        # UX amigável pra vendedor que errou URL: redirect pro próprio
        if session.get('role') == 'vendedor' and session.get('codusur'):
            return redirect(f"/vendedor/{session.get('codusur')}")
        return Response('Sem permissão pra ver este vendedor', status=403, mimetype='text/plain')
    return send_from_directory('.', 'vendedor.html')


def _carregar_ranking_vendedores(role=None, codusur=None, codsupervisor=None):
    """Roda 3 queries DAX em paralelo (vendas 12m atual + vendas 12m anterior + metricas),
    monta lista de vendedores com KPIs+YoY+rank. Cache 1h (chave inclui RBAC do user).
    Filtra técnicos e BLOQUEIO='S'. Não aplica filtros do request — quem faz é o endpoint.

    Args opcionais permitem chamar fora de request context (ex: prewarm). Se omitidos,
    lê de session (compatibilidade com chamadas dos endpoints existentes)."""
    if role is None and codusur is None and codsupervisor is None:
        role = session.get('role')
        codusur = session.get('codusur')
        rbac_sups = _session_supervisores()
    else:
        rbac_sups = _como_lista_supervisores(codsupervisor)

    key = ':'.join([
        'multpel', 'vendedores:ranking:v1',
        f"role={role or 'anon'}",
        f"usur={codusur if codusur is not None else '-'}",
        f"supv={','.join(str(s) for s in rbac_sups) if rbac_sups else '-'}",
    ])
    cached = _cache_get(key)
    if cached:
        return cached

    if role == 'admin':
        rbac = ''
    elif codusur is not None:
        rbac = f"FATURAMENTO_VENDAS[CODUSUR] = {int(codusur)}"
    elif rbac_sups:
        rbac = _frag_supervisores('FATURAMENTO_VENDAS', rbac_sups)
    else:
        rbac = ''
    rbac_frag = f" && {rbac}" if rbac else ""

    # Patch L.2: usar janela de 365 dias EXATA (não EDATE) pra YoY bater com o BI do cliente.
    # Cliente reportou YoY 8,85% pro JOSE JUNIOR; com EDATE(-12) dava 7,67%; com 365 dias dá ~8,72%.
    queries = {
        'vendas_atual': f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODUSUR],
    FILTER(FATURAMENTO_VENDAS, FATURAMENTO_VENDAS[DTSAIDA] >= TODAY() - 365{rbac_frag}),
    "VendaLiq",  [VENDA LIQUIDA],
    "LucroTotal",[LUCRO TOTAL]
)""",
        'vendas_anterior': f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODUSUR],
    FILTER(FATURAMENTO_VENDAS,
        FATURAMENTO_VENDAS[DTSAIDA] >= TODAY() - 730
        && FATURAMENTO_VENDAS[DTSAIDA] < TODAY() - 365{rbac_frag}),
    "VendaLiqAnt", [VENDA LIQUIDA]
)""",
        'metricas': f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODUSUR],
    FILTER(FATURAMENTO_VENDAS, FATURAMENTO_VENDAS[DTSAIDA] >= TODAY() - 365{rbac_frag}),
    "TicketMedio",     [TICKET MEDIO],
    "ClientesUnicos",  DISTINCTCOUNT(FATURAMENTO_VENDAS[CODCLI])
)""",
        # Patch L: carteira OFICIAL via PCCLIENT[CODUSUR1] — denominador da taxa de positivação.
        # A medida nativa [TAXA POSITIVACAO CLIENTE] do PBI está bugada (sempre retorna 0-3%).
        # Esta query agrupa por CODUSUR1 da PCCLIENT pra contar quantos clientes cada vendedor tem oficialmente atribuídos.
        'carteira_oficial': """EVALUATE
SUMMARIZECOLUMNS(
    PCCLIENT[CODUSUR1],
    "CarteiraOficial", DISTINCTCOUNT(PCCLIENT[CODCLI])
)""",
    }
    resultados = _executar_dax_paralelo_n(queries, max_workers=4)

    atual = clean_rows(_todas_linhas(resultados['vendas_atual']))
    anterior_idx = {r['CODUSUR']: r for r in clean_rows(_todas_linhas(resultados['vendas_anterior'])) if r.get('CODUSUR') is not None}
    metricas_idx = {r['CODUSUR']: r for r in clean_rows(_todas_linhas(resultados['metricas'])) if r.get('CODUSUR') is not None}
    carteira_idx = {r['CODUSUR1']: r for r in clean_rows(_todas_linhas(resultados['carteira_oficial'])) if r.get('CODUSUR1') is not None}
    vmap = _carregar_vendedores_map()

    out = []
    for r in atual:
        cu = r.get('CODUSUR')
        if cu is None or cu in VENDEDORES_TECNICOS:
            continue
        v_meta = vmap.get(str(cu))
        if not v_meta:
            continue  # vendedor sem cadastro em PCUSUARI ou bloqueado pelos técnicos
        if v_meta.get('bloqueio') == 'S':
            continue
        venda_atual = r.get('VendaLiq') or 0
        venda_ant = (anterior_idx.get(cu, {}).get('VendaLiqAnt')) or 0
        yoy = None
        if venda_ant > 0:
            yoy = (venda_atual - venda_ant) / venda_ant
        m = metricas_idx.get(cu, {})
        clientes_12m = m.get('ClientesUnicos') or 0
        carteira_oficial = (carteira_idx.get(cu, {}).get('CarteiraOficial')) or 0
        # Patch L: taxa de positivação = clientes que compraram 12m / carteira oficial (PCCLIENT.CODUSUR1)
        # Substitui a medida [TAXA POSITIVACAO CLIENTE] do PBI (que estava bugada retornando 0-3%)
        taxa_positivacao = (clientes_12m / carteira_oficial) if carteira_oficial else 0
        out.append({
            'codusur':          cu,
            'nome':             v_meta.get('nome'),
            'tipo':             v_meta.get('tipo'),
            'codsupervisor':    v_meta.get('codsupervisor'),
            'cidade':           v_meta.get('cidade'),
            'estado':           v_meta.get('estado'),
            'venda_liq':        venda_atual,
            'lucro':            r.get('LucroTotal') or 0,
            'venda_anterior':   venda_ant,
            'ticket_medio':     m.get('TicketMedio') or 0,
            'taxa_positivacao': taxa_positivacao,
            'clientes_unicos':  clientes_12m,
            'carteira_oficial': carteira_oficial,
            'yoy_receita':      yoy,
        })

    out.sort(key=lambda v: v['lucro'] or 0, reverse=True)
    for idx, v in enumerate(out, start=1):
        v['rank'] = idx

    _cache_set(key, out, 'dax_agregado')
    return out


@app.route('/api/vendedores')
@login_required
def api_vendedores():
    if session.get('role') == 'vendedor':
        return jsonify({'ok': False, 'error': 'Sem permissão'}), 403

    tipovend = request.args.get('tipovend', 'R')
    incluir_internos = request.args.get('incluir_internos', 'false').lower() == 'true'
    supervisor = request.args.get('supervisor')
    uf = request.args.get('uf')
    busca = (request.args.get('busca') or '').strip().lower()

    vendedores = _carregar_ranking_vendedores()

    filtrados = vendedores
    if not incluir_internos and tipovend:
        filtrados = [v for v in filtrados if v.get('tipo') == tipovend]
    elif incluir_internos and tipovend:
        # Mostrar tipovend principal + 'I' (internos)
        filtrados = [v for v in filtrados if v.get('tipo') in (tipovend, 'I')]
    if supervisor:
        # Aceita 1 ou múltiplos supervisores (CSV: "18" ou "18,19")
        sup_ids = set()
        for parte in str(supervisor).split(','):
            parte = parte.strip()
            if parte:
                try:
                    sup_ids.add(int(parte))
                except ValueError:
                    pass
        if sup_ids:
            filtrados = [v for v in filtrados if v.get('codsupervisor') in sup_ids]
    if uf:
        filtrados = [v for v in filtrados if (v.get('estado') or '') == uf]
    if busca:
        filtrados = [v for v in filtrados
                     if busca in (v.get('nome') or '').lower()
                     or busca in str(v.get('codusur') or '')]

    return jsonify({'ok': True, 'total': len(filtrados), 'vendedores': filtrados})


def _carregar_perfil_vendedor(codusur):
    """1 query DAX. Retorna 1 dict com os 10 campos principais de PCUSUARI.
    Cache 24h (PCUSUARI muda raramente)."""
    key = f'multpel:vendedor:perfil:{codusur}'
    cached = _cache_get(key)
    if cached:
        return cached
    query = f"""EVALUATE
FILTER(PCUSUARI, PCUSUARI[CODUSUR] = {int(codusur)})"""
    token = get_token_cached()
    payload = retry_dax(execute_dax)(token, query)
    rows = clean_rows(_todas_linhas(payload))
    if not rows:
        return None
    r = rows[0]
    perfil = {
        'codusur':       r.get('CODUSUR'),
        # DEMO: mascara o nome no perfil/cockpit (ver bloco TIME_DEMO). Reverter = False.
        'nome':          _demo_nome_time(r.get('CODUSUR')) if TIME_DEMO else r.get('NOME'),
        'cpf':           r.get('CPF'),
        'email':         r.get('EMAIL'),
        'telefone':      r.get('TELEFONE1') or r.get('CELULAR'),
        'celular':       r.get('CELULAR'),
        'cidade':        r.get('CIDADE'),
        'estado':        r.get('ESTADO'),
        'bairro':        r.get('BAIRRO'),
        'tipo':          r.get('TIPOVEND'),
        'codsupervisor': r.get('CODSUPERVISOR'),
        'codequipe':     r.get('CODEQUIPE'),
        'bloqueio':      r.get('BLOQUEIO'),
        'dt_admissao':   str(r.get('DTADMISSAO') or '')[:10] or None,
        'dt_demissao':   str(r.get('DTDEMISSAO') or '')[:10] or None,
        'percomissao':   r.get('PERCOMISSAO'),
        'permeta':       r.get('PERMETA'),
    }
    _cache_set(key, perfil, 'metadata')
    return perfil


def _comparativo_equipe(codusur, sua_taxa):
    """Calcula média de TAXA POSITIVACAO da equipe (mesmo CODSUPERVISOR).
    Retorna dict com sua_taxa, media_equipe, time_size, label.

    Casos:
    - time_size >= 2: vs time normal
    - time_size == 1: vs média geral TIPOVEND='R'
    - time_size == 0: omite (None retornado pra frontend não mostrar card)
    """
    vmap = _carregar_vendedores_map()
    v = vmap.get(str(codusur))
    if not v:
        return None
    meu_sup = v.get('codsupervisor')
    if not meu_sup:
        return None

    ranking = _carregar_ranking_vendedores()
    colegas = [r for r in ranking if r.get('codsupervisor') == meu_sup and r.get('codusur') != codusur]

    if len(colegas) >= 1:  # time_size >= 2 considerando o vendedor + pelo menos 1 colega
        taxas = [r.get('taxa_positivacao') for r in colegas if r.get('taxa_positivacao') is not None]
        if not taxas:
            return None
        media = sum(taxas) / len(taxas)
        return {
            'sua_taxa':       sua_taxa,
            'media_equipe':   media,
            'time_size':      len(colegas) + 1,
            'fallback_geral': False,
            'label':          f"vs seu time ({len(colegas)+1} vendedores)",
        }
    else:
        # time_size == 1: só ele no supervisor — fallback geral TIPOVEND='R'
        gerais = [r for r in ranking if r.get('tipo') == 'R' and r.get('codusur') != codusur and r.get('taxa_positivacao') is not None]
        if not gerais:
            return None
        media = sum(r['taxa_positivacao'] for r in gerais) / len(gerais)
        return {
            'sua_taxa':       sua_taxa,
            'media_equipe':   media,
            'time_size':      1,
            'fallback_geral': True,
            'label':          "Sua equipe tem só você. Vs média da empresa",
        }


@app.route('/api/vendedor/<int:codusur>')
@login_required
def api_vendedor(codusur):
    if not pode_acessar_vendedor(codusur):
        return jsonify({'ok': False, 'error': 'Sem permissão'}), 403

    key = cache_key_for_user(f'vendedor:full:{codusur}')
    cached = _cache_get(key)
    if cached:
        return jsonify(cached)

    # Perfil + ranking dele (pra KPIs + nome supervisor)
    perfil = _carregar_perfil_vendedor(codusur)
    if not perfil:
        return jsonify({'ok': False, 'error': 'Vendedor não encontrado'}), 404

    ranking = _carregar_ranking_vendedores()
    eu = next((v for v in ranking if v.get('codusur') == codusur), None)
    if not eu:
        eu = {'venda_liq': 0, 'lucro': 0, 'ticket_medio': 0, 'taxa_positivacao': 0,
              'clientes_unicos': 0, 'yoy_receita': None}

    # Nome do supervisor (via vendedores_map)
    vmap = _carregar_vendedores_map()
    sup_nome = None
    if perfil.get('codsupervisor'):
        sup_v = vmap.get(str(perfil['codsupervisor']))
        sup_nome = sup_v.get('nome') if sup_v else None

    # Carteira: contagem cadastrados + positivados + champions + at_risk via carteira_full
    # Carteira_full já filtra por RBAC. Pra cockpit de admin/supervisor, queremos a
    # carteira do <codusur> independente do RBAC do user logado — então fazemos a chamada
    # diretamente e filtramos in-memory por codusur (cache continua válido).
    carteira = _carregar_carteira_full()
    minha_carteira = [c for c in carteira if c['codusur'] == codusur]
    champions = sum(1 for c in minha_carteira if c['segmento'] == 'champions')
    at_risk = sum(1 for c in minha_carteira if c['segmento'] == 'at_risk')

    comparativo = _comparativo_equipe(codusur, eu.get('taxa_positivacao'))

    resp = {
        'ok': True,
        'perfil': {**perfil, 'supervisor_nome': sup_nome},
        'kpis': {
            'venda_liq':        eu.get('venda_liq', 0),
            'lucro':            eu.get('lucro', 0),
            'ticket_medio':     eu.get('ticket_medio', 0),
            'taxa_positivacao': eu.get('taxa_positivacao', 0),
            'yoy_receita':      eu.get('yoy_receita'),
            'rank':             eu.get('rank'),
        },
        'carteira': {
            'cadastrados': len(minha_carteira),
            'positivados': sum(1 for c in minha_carteira if (c.get('frequencia_12m') or 0) > 0),
            'champions':   champions,
            'at_risk':     at_risk,
        },
        'comparativo_equipe': comparativo,
    }
    _cache_set(key, resp, 'dax_agregado')
    return jsonify(resp)


@app.route('/api/vendedor/<int:codusur>/serie')
@login_required
def api_vendedor_serie(codusur):
    if not pode_acessar_vendedor(codusur):
        return jsonify({'ok': False, 'error': 'Sem permissão'}), 403

    periodo = request.args.get('periodo', '12m')
    key = cache_key_for_user(f'vendedor:serie:{codusur}', {'periodo': periodo})
    cached = _cache_get(key)
    if cached:
        return jsonify(cached)

    f_temp = filtro_periodo(periodo)
    rbac = aplicar_rbac_dax()
    filtro = f"FATURAMENTO_VENDAS[CODUSUR] = {int(codusur)} && {f_temp}"
    if rbac:
        filtro += f" && {rbac}"

    query = f"""EVALUATE
SUMMARIZECOLUMNS(
    CALENDARIO[AnoMes],
    FILTER(FATURAMENTO_VENDAS, {filtro}),
    "VendaLiquida", [VENDA LIQUIDA],
    "LucroTotal",   [LUCRO TOTAL]
)
ORDER BY CALENDARIO[AnoMes]"""
    token = get_token_cached()
    payload = retry_dax(execute_dax)(token, query)
    rows = clean_rows(_todas_linhas(payload))
    resp = {'ok': True, 'rows': rows}
    _cache_set(key, resp, 'dax_agregado')
    return jsonify(resp)


@app.route('/api/vendedor/<int:codusur>/carteira')
@login_required
def api_vendedor_carteira(codusur):
    """Wrapper sobre _filtrar_carteira injetando vendedor=<codusur>. Cache via carteira_full."""
    if not pode_acessar_vendedor(codusur):
        return jsonify({'ok': False, 'error': 'Sem permissão'}), 403

    clientes = _carregar_carteira_full()
    return jsonify(_filtrar_carteira(clientes, request.args, vendedor_forcado=codusur))


@app.route('/api/vendedor/<int:codusur>/alertas')
@login_required
def api_vendedor_alertas(codusur):
    """2 alertas: At Risk (valor) + Top 3 Champions. Zero query DAX nova."""
    if not pode_acessar_vendedor(codusur):
        return jsonify({'ok': False, 'error': 'Sem permissão'}), 403

    carteira = _carregar_carteira_full()
    minha = [c for c in carteira if c['codusur'] == codusur]
    at_risk = [c for c in minha if c['segmento'] == 'at_risk']
    champions = sorted(
        [c for c in minha if c['segmento'] == 'champions'],
        key=lambda c: c.get('lucro_12m') or 0,
        reverse=True
    )[:3]

    lucro_perdido_total = round(sum((c.get('lucro_perdido_proj') or 0) for c in at_risk), 2)

    alertas = []
    if at_risk:
        alertas.append({
            'tipo': 'at_risk',
            'count': len(at_risk),
            'lucro_perdido_total': lucro_perdido_total,
            'msg': f"{len(at_risk)} clientes At Risk somam R$ {lucro_perdido_total:,.0f}/ano de lucro em risco — ligue.".replace(',', '.'),
        })
    if champions:
        alertas.append({
            'tipo': 'champions_top3',
            'champions': [
                {'codcli': c['codcli'], 'cliente': c.get('cliente'),
                 'lucro_12m': c.get('lucro_12m'), 'recencia_dias': c.get('recencia_dias')}
                for c in champions
            ],
            'msg': f"Top {len(champions)} Champions da sua carteira — não deixe escapar.",
        })

    return jsonify({'ok': True, 'alertas': alertas})


# ──────────────────────────────────────────────────────────────────────
# Categorias + Mix abandonado + Tendências/Cohort (Onda D)
#
# PIVOT crítico documentado em _PROGRESSO.md "Onda D":
#   CODCATEGORIA tem 92% NULL em FATURAMENTO_VENDAS (R$ 78M de R$ 85M).
#   Usamos CODEPTO como dimensão primária — 33 deptos com 0% NULL.
#   Nomes textuais vêm via FATURAMENTO_DEVOLUCAO[DEPARTAMENTO]/[SECAO] (DIM auxiliar).
#   Marca: CODMARCA 54% NULL — usar apenas como filtro/secundário.
#   Fornecedor: CODFORNECPRINC + FORNECPRINC (textual!) 0% NULL — filtro adicional.
#
# Cohort: módulo puro em cohort.py (9 testes unitários).
# ──────────────────────────────────────────────────────────────────────

import cohort  # módulo puro


@app.route('/categorias')
@login_required
def categorias_page():
    return send_from_directory('.', 'categorias.html')


@app.route('/mix')
@login_required
def mix_page():
    return send_from_directory('.', 'mix.html')


@app.route('/tendencias')
@login_required
def tendencias_page():
    return send_from_directory('.', 'tendencias.html')


def _carregar_deptos_map():
    """Cache 24h. Carrega {CODEPTO: nome} via FATURAMENTO_DEVOLUCAO (única tabela
    que tem o nome textual). CODSEC: {CODSEC: nome} também."""
    key = 'multpel:deptos_map:v1'
    cached = _cache_get(key)
    if cached:
        return cached

    queries = {
        'deptos': """EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_DEVOLUCAO[CODEPTO],
    FATURAMENTO_DEVOLUCAO[DEPARTAMENTO]
)""",
        'secoes': """EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_DEVOLUCAO[CODSEC],
    FATURAMENTO_DEVOLUCAO[SECAO]
)""",
    }
    resultados = _executar_dax_paralelo_n(queries, max_workers=2)

    deptos = {}
    for r in clean_rows(_todas_linhas(resultados['deptos'])):
        cd = r.get('CODEPTO')
        nm = r.get('DEPARTAMENTO')
        if cd is not None and nm:
            deptos[str(cd)] = nm

    secoes = {}
    for r in clean_rows(_todas_linhas(resultados['secoes'])):
        cs = r.get('CODSEC')
        nm = r.get('SECAO')
        if cs is not None and nm:
            secoes[str(cs)] = nm

    mapa = {'deptos': deptos, 'secoes': secoes}
    _cache_set(key, mapa, 'metadata')
    return mapa


@app.route('/api/categorias')
@login_required
def api_categorias():
    """Ranking de DEPARTAMENTOS (CODEPTO) com nome textual.
    PIVOT: usa CODEPTO em vez de CODCATEGORIA (92% NULL).
    """
    sup = _supervisores_filtro()
    key = cache_key_for_user('categorias:ranking:v1', {'supervisor': _sup_cache_key(sup)})
    cached = _cache_get(key)
    if cached:
        return jsonify(cached)

    # Carteira por CADASTRO: admin agrega tudo (+ override ?supervisor= do dashboard);
    # supervisor/vendedor restringem aos codclis do cadastro (sem RBAC de venda).
    if session.get('role') in ('admin', 'viewer'):
        rbac_frag = ''
        sup_frag = _frag_supervisores('FATURAMENTO_VENDAS', sup)
        if sup_frag:
            rbac_frag = f" && {sup_frag}"
    else:
        frag, ok = _frag_codcli_cadastro()
        if ok:
            rbac_frag = frag
        else:  # escopo enorme: fallback seguro mantendo RBAC de venda
            r = aplicar_rbac_dax()
            rbac_frag = f" && {r}" if r else ''

    query = f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODEPTO],
    FILTER(FATURAMENTO_VENDAS, FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12){rbac_frag}),
    "VendaLiquida",  [VENDA LIQUIDA],
    "LucroTotal",    [LUCRO TOTAL],
    "ClientesUnicos", DISTINCTCOUNT(FATURAMENTO_VENDAS[CODCLI]),
    "ProdutosUnicos", DISTINCTCOUNT(FATURAMENTO_VENDAS[CODPROD])
)
ORDER BY [VendaLiquida] DESC"""
    token = get_token_cached()
    payload = retry_dax(execute_dax)(token, query)
    rows = clean_rows(_todas_linhas(payload))

    mapa = _carregar_deptos_map()
    deptos_nomes = mapa['deptos']

    total_venda = sum((r.get('VendaLiquida') or 0) for r in rows)
    out = []
    for r in rows:
        cd = r.get('CODEPTO')
        venda = r.get('VendaLiquida') or 0
        lucro = r.get('LucroTotal') or 0
        nome = deptos_nomes.get(str(cd)) if cd is not None else None
        margem = (lucro / venda) if venda else 0
        out.append({
            'codepto':         cd,
            'nome':            nome or (f'Depto {cd}' if cd is not None else '(sem departamento)'),
            'tem_nome':        bool(nome),
            'venda':           venda,
            'lucro':           lucro,
            'margem':          margem,
            'clientes_unicos': r.get('ClientesUnicos') or 0,
            'produtos_unicos': r.get('ProdutosUnicos') or 0,
            'share':           (venda / total_venda) if total_venda else 0,
        })
    out.sort(key=lambda x: x['venda'], reverse=True)
    resp = {'ok': True, 'total_venda': total_venda, 'categorias': out}
    _cache_set(key, resp, 'dax_agregado')
    return jsonify(resp)


@app.route('/api/categorias/<codepto>/clientes')
@login_required
def api_categoria_clientes(codepto):
    """Drill: top clientes desse departamento.
    Aceita string 'null' pra clientes que compraram produtos SEM departamento."""
    try:
        limit = max(1, min(int(request.args.get('limit', 50)), 500))
    except ValueError:
        limit = 50

    sup = _supervisores_filtro()
    key = cache_key_for_user(f'categoria:clientes:{codepto}', {'limit': limit, 'supervisor': _sup_cache_key(sup)})
    cached = _cache_get(key)
    if cached:
        return jsonify(cached)

    # Cadastro: admin sem restrição (+ override dashboard); supervisor/vendedor por codcli.
    if session.get('role') in ('admin', 'viewer'):
        rbac_frag = ''
        sup_frag = _frag_supervisores('FATURAMENTO_VENDAS', sup)
        if sup_frag:
            rbac_frag = f" && {sup_frag}"
    else:
        frag, ok = _frag_codcli_cadastro()
        if ok:
            rbac_frag = frag
        else:
            r = aplicar_rbac_dax()
            rbac_frag = f" && {r}" if r else ''

    if codepto == 'null':
        filtro_dim = "ISBLANK(FATURAMENTO_VENDAS[CODEPTO])"
    else:
        try:
            cd = int(codepto)
        except ValueError:
            return jsonify({'ok': False, 'error': 'codepto inválido'}), 400
        filtro_dim = f"FATURAMENTO_VENDAS[CODEPTO] = {cd}"

    query = f"""EVALUATE
TOPN({limit},
    SUMMARIZECOLUMNS(
        FATURAMENTO_VENDAS[CODCLI],
        FATURAMENTO_VENDAS[CLIENTE],
        FATURAMENTO_VENDAS[UF],
        FATURAMENTO_VENDAS[CODUSUR],
        FILTER(FATURAMENTO_VENDAS,
            {filtro_dim}
            && FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12){rbac_frag}),
        "VendaCat", [VENDA LIQUIDA],
        "LucroCat", [LUCRO TOTAL]
    ),
    [VendaCat], DESC
)"""
    token = get_token_cached()
    payload = retry_dax(execute_dax)(token, query)
    rows = clean_rows(_todas_linhas(payload))
    resp = {'ok': True, 'codepto': codepto, 'rows': rows}
    _cache_set(key, resp, 'dax_agregado')
    return jsonify(resp)


@app.route('/api/marcas')
@login_required
def api_marcas():
    """Top 10 marcas. CODMARCA tem 54% NULL — incluir aviso na resposta."""
    try:
        top = max(3, min(int(request.args.get('top', 10)), 50))
    except ValueError:
        top = 10
    key = cache_key_for_user('marcas:top', {'top': top})
    cached = _cache_get(key)
    if cached:
        return jsonify(cached)

    rbac = aplicar_rbac_dax()
    rbac_frag = f" && {rbac}" if rbac else ""

    query = f"""EVALUATE
TOPN({top},
    SUMMARIZECOLUMNS(
        FATURAMENTO_VENDAS[CODMARCA],
        FILTER(FATURAMENTO_VENDAS, FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12){rbac_frag}),
        "VendaLiquida", [VENDA LIQUIDA],
        "LucroTotal",   [LUCRO TOTAL]
    ),
    [VendaLiquida], DESC
)"""
    token = get_token_cached()
    payload = retry_dax(execute_dax)(token, query)
    rows = clean_rows(_todas_linhas(payload))

    out = []
    for r in rows:
        cm = r.get('CODMARCA')
        out.append({
            'codmarca':    cm,
            'nome':        f'Marca {cm}' if cm is not None else '(sem marca)',
            'venda':       r.get('VendaLiquida') or 0,
            'lucro':       r.get('LucroTotal') or 0,
        })
    resp = {'ok': True, 'marcas': out, 'aviso': '54% das vendas não têm CODMARCA cadastrada'}
    _cache_set(key, resp, 'dax_agregado')
    return jsonify(resp)


@app.route('/api/fornecedores')
@login_required
def api_fornecedores():
    """Top fornecedores. CODFORNECPRINC + FORNECPRINC textual — 0% NULL!"""
    try:
        top = max(5, min(int(request.args.get('top', 30)), 100))
    except ValueError:
        top = 30
    key = cache_key_for_user('fornecedores:top', {'top': top})
    cached = _cache_get(key)
    if cached:
        return jsonify(cached)

    rbac = aplicar_rbac_dax()
    rbac_frag = f" && {rbac}" if rbac else ""

    query = f"""EVALUATE
TOPN({top},
    SUMMARIZECOLUMNS(
        FATURAMENTO_VENDAS[CODFORNECPRINC],
        FATURAMENTO_VENDAS[FORNECPRINC],
        FILTER(FATURAMENTO_VENDAS, FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12){rbac_frag}),
        "VendaLiquida", [VENDA LIQUIDA],
        "LucroTotal",   [LUCRO TOTAL]
    ),
    [VendaLiquida], DESC
)"""
    token = get_token_cached()
    payload = retry_dax(execute_dax)(token, query)
    rows = clean_rows(_todas_linhas(payload))
    out = [{
        'codfornec': r.get('CODFORNECPRINC'),
        'nome':      r.get('FORNECPRINC') or f'Fornec {r.get("CODFORNECPRINC")}',
        'venda':     r.get('VendaLiquida') or 0,
        'lucro':     r.get('LucroTotal') or 0,
    } for r in rows]
    resp = {'ok': True, 'fornecedores': out}
    _cache_set(key, resp, 'dax_agregado')
    return jsonify(resp)


def _mix_abandonado_rows(dias, codepto=None, fornecedor=None):
    """Lista COMPLETA de pares (cliente × departamento) abandonados há >= dias, no escopo de
    CADASTRO do usuário, ordenada por lucro do depto desc. Números totais do cliente."""
    filtros_extra = []
    if codepto:
        try:
            filtros_extra.append(f"FATURAMENTO_VENDAS[CODEPTO] = {int(codepto)}")
        except ValueError:
            pass
    if fornecedor:
        try:
            filtros_extra.append(f"FATURAMENTO_VENDAS[CODFORNECPRINC] = {int(fornecedor)}")
        except ValueError:
            pass
    filtros_str = (" && " + " && ".join(filtros_extra)) if filtros_extra else ""

    query = f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODCLI],
    FATURAMENTO_VENDAS[CODEPTO],
    FILTER(FATURAMENTO_VENDAS,
        FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12){filtros_str}),
    "UltimaCompra", MAX(FATURAMENTO_VENDAS[DTSAIDA]),
    "VendaCat12m",  [VENDA LIQUIDA],
    "LucroCat12m",  [LUCRO TOTAL]
)"""
    token = get_token_cached()
    payload = retry_dax(execute_dax)(token, query)
    rows = clean_rows(_todas_linhas(payload))

    from datetime import date as _date
    hoje = _date.today()
    deptos_nomes = _carregar_deptos_map()['deptos']
    carteira_idx = {c['codcli']: c for c in _carteira_no_escopo()}

    out = []
    for r in rows:
        cc = r.get('CODCLI')
        cd = r.get('CODEPTO')
        ultima = r.get('UltimaCompra')
        if not ultima or cc is None or cc not in carteira_idx:
            continue
        try:
            d_ultima = _date.fromisoformat(str(ultima)[:10])
        except ValueError:
            continue
        dias_sem = (hoje - d_ultima).days
        if dias_sem < dias:
            continue
        cli_meta = carteira_idx.get(cc, {})
        out.append({
            'codcli':                       cc,
            'cliente':                      cli_meta.get('cliente') or f'Cliente #{cc}',
            'cidade':                       cli_meta.get('cidade'),
            'uf':                           cli_meta.get('uf'),
            'codepto':                      cd,
            'depto_nome':                   deptos_nomes.get(str(cd)) if cd is not None else '(sem depto)',
            'ultima_compra':                str(ultima)[:10],
            'dias_sem_comprar_categoria':   dias_sem,
            'venda_cat_12m':                r.get('VendaCat12m') or 0,
            'lucro_cat_12m':                r.get('LucroCat12m') or 0,
            'lucro_total_12m':              cli_meta.get('lucro_12m') or 0,
            'venda_total_12m':              cli_meta.get('venda_12m') or 0,
            'vendedor':                     cli_meta.get('vendedor'),
            'codusur':                      cli_meta.get('codusur'),
            'time':                         cli_meta.get('time'),
            'codsupervisor':                cli_meta.get('codsupervisor'),
            'telefone':                     cli_meta.get('telefone'),
        })
    out.sort(key=lambda x: x['lucro_cat_12m'], reverse=True)
    return out


@app.route('/api/mix/abandonado')
@login_required
def api_mix_abandonado():
    """Clientes que compraram um DEPARTAMENTO nos últimos 12m mas não nos últimos N dias.
    Filtros: codepto (opcional), fornecedor (opcional), dias (default 60), limit."""
    try:
        dias = max(7, min(int(request.args.get('dias', 60)), 365))
        limit = max(10, min(int(request.args.get('limit', 100)), 1000))
    except ValueError:
        dias, limit = 60, 100
    codepto = request.args.get('codepto')
    fornecedor = request.args.get('fornecedor')
    busca = (request.args.get('busca') or '').strip()
    codcli = request.args.get('codcli')

    key = cache_key_for_user('mix:abandonado', {'dias': dias, 'codepto': codepto or '', 'fornecedor': fornecedor or ''})
    cached = _cache_get(key)
    if cached:
        full = cached['rows']
    else:
        full = _mix_abandonado_rows(dias, codepto, fornecedor)
        _cache_set(key, {'ok': True, 'dias': dias, 'total': len(full), 'rows': full}, 'dax_agregado')

    total = len(full)
    if codcli or busca:
        # Filtra a lista COMPLETA (cacheada) por cliente exato (seleção no autocomplete) OU
        # busca (nome/prefixo de código) → mesmos pares, mesmo formato dos 200, cobrindo TODOS
        # os clientes (não só o top-200). Filtro em memória.
        filtrados = _mix_aplicar_filtros_locais(full, None, None, busca, codcli)
        return jsonify({'ok': True, 'dias': dias, 'total': total, 'filtrado': len(filtrados),
                        'busca': busca, 'codcli': codcli, 'rows': filtrados})
    return jsonify({'ok': True, 'dias': dias, 'total': total, 'rows': full[:limit]})


@app.route('/api/mix/abandonado/<int:codcli>/deptos')
@login_required
def api_mix_cliente_deptos(codcli):
    """Drill: top 5 departamentos que ESTE cliente abandonou (parou há >= dias). Números totais."""
    try:
        dias = max(7, min(int(request.args.get('dias', 60)), 365))
    except ValueError:
        dias = 60

    # Guarda de escopo: cliente precisa estar no cadastro do usuário
    carteira_idx = {c['codcli']: c for c in _carteira_no_escopo()}
    cli_meta = carteira_idx.get(codcli)
    if cli_meta is None:
        return jsonify({'ok': False, 'error': 'Cliente fora da sua carteira'}), 404

    query = f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODEPTO],
    FILTER(FATURAMENTO_VENDAS,
        FATURAMENTO_VENDAS[CODCLI] = {codcli}
        && FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12)),
    "UltimaCompra", MAX(FATURAMENTO_VENDAS[DTSAIDA]),
    "VendaCat12m",  [VENDA LIQUIDA],
    "LucroCat12m",  [LUCRO TOTAL]
)"""
    token = get_token_cached()
    payload = retry_dax(execute_dax)(token, query)
    rows = clean_rows(_todas_linhas(payload))

    from datetime import date as _date
    hoje = _date.today()
    deptos_nomes = _carregar_deptos_map()['deptos']
    out = []
    for r in rows:
        cd = r.get('CODEPTO')
        ultima = r.get('UltimaCompra')
        if not ultima:
            continue
        try:
            d_ultima = _date.fromisoformat(str(ultima)[:10])
        except ValueError:
            continue
        dias_sem = (hoje - d_ultima).days
        if dias_sem < dias:
            continue  # esse depto ainda está ativo
        out.append({
            'codepto':       cd,
            'depto_nome':    deptos_nomes.get(str(cd)) if cd is not None else '(sem depto)',
            'ultima_compra': str(ultima)[:10],
            'dias_parado':   dias_sem,
            'venda_cat_12m': r.get('VendaCat12m') or 0,
            'lucro_cat_12m': r.get('LucroCat12m') or 0,
        })
    out.sort(key=lambda x: x['lucro_cat_12m'], reverse=True)
    return jsonify({
        'ok': True,
        'codcli': codcli,
        'cliente': cli_meta.get('cliente') or f'Cliente #{codcli}',
        'cidade': cli_meta.get('cidade'),
        'uf': cli_meta.get('uf'),
        'vendedor': cli_meta.get('vendedor'),
        'dias': dias,
        'total': len(out),
        'rows': out[:5],   # top 5
    })


def _mix_cliente_fornecedores_rows(codcli, dias):
    """Fornecedores que ESTE cliente parou de comprar (última compra há >= dias), no escopo de
    CADASTRO. Espelha _mix_cliente_deptos, mas agrupa por CODFORNECPRINC. Números totais do
    cliente. Retorna (cli_meta | None, linhas). cli_meta None = fora do escopo (o caller faz 404)."""
    from datetime import date as _date

    carteira_idx = {c['codcli']: c for c in _carteira_no_escopo()}
    cli_meta = carteira_idx.get(codcli)
    if cli_meta is None:
        return None, []

    query = f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODFORNECPRINC],
    FATURAMENTO_VENDAS[FORNECPRINC],
    FILTER(FATURAMENTO_VENDAS,
        FATURAMENTO_VENDAS[CODCLI] = {int(codcli)}
        && FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12)),
    "UltimaCompra", MAX(FATURAMENTO_VENDAS[DTSAIDA]),
    "VendaCat12m",  [VENDA LIQUIDA],
    "LucroCat12m",  [LUCRO TOTAL]
)"""
    token = get_token_cached()
    payload = retry_dax(execute_dax)(token, query)
    rows = clean_rows(_todas_linhas(payload))

    hoje = _date.today()
    out = []
    for r in rows:
        cf = r.get('CODFORNECPRINC')
        ultima = r.get('UltimaCompra')
        if not ultima:
            continue
        try:
            d_ultima = _date.fromisoformat(str(ultima)[:10])
        except ValueError:
            continue
        dias_sem = (hoje - d_ultima).days
        if dias_sem < dias:
            continue  # esse fornecedor ainda está ativo
        out.append({
            'codfornec':     cf,
            'fornec_nome':   r.get('FORNECPRINC') or (f'Fornec {cf}' if cf is not None else '(sem fornecedor)'),
            'ultima_compra': str(ultima)[:10],
            'dias_parado':   dias_sem,
            'venda_cat_12m': r.get('VendaCat12m') or 0,
            'lucro_cat_12m': r.get('LucroCat12m') or 0,
        })
    out.sort(key=lambda x: x['lucro_cat_12m'], reverse=True)
    return cli_meta, out


@app.route('/api/mix/cliente/<int:codcli>/fornecedores')
@login_required
def api_mix_cliente_fornecedores(codcli):
    """Drill por CLIENTE: fornecedores que ELE parou de comprar (>= dias). Números totais.
    Consulta enxuta (CODCLI fixo) → rápida e independente do board de 200."""
    try:
        dias = max(7, min(int(request.args.get('dias', 60)), 365))
    except (TypeError, ValueError):
        dias = 60

    key = cache_key_for_user(f'mix:cliente:fornec:{codcli}', {'dias': dias})
    cached = _cache_get(key)
    if cached:
        return jsonify(cached)

    cli_meta, out = _mix_cliente_fornecedores_rows(codcli, dias)
    if cli_meta is None:
        return jsonify({'ok': False, 'error': 'Cliente fora da sua carteira'}), 404

    resp = {
        'ok': True,
        'codcli': codcli,
        'cliente': cli_meta.get('cliente') or f'Cliente #{codcli}',
        'cidade': cli_meta.get('cidade'),
        'uf': cli_meta.get('uf'),
        'vendedor': cli_meta.get('vendedor'),
        'telefone': cli_meta.get('telefone'),
        'dias': dias,
        'total': len(out),
        'rows': out,
    }
    _cache_set(key, resp, 'dax_agregado')
    return jsonify(resp)


@app.route('/api/mix/cliente/<int:codcli>/fornecedores/csv')
@login_required
def api_mix_cliente_fornecedores_csv(codcli):
    """CSV dos fornecedores que o cliente parou de comprar."""
    from datetime import date as _date
    try:
        dias = max(7, min(int(request.args.get('dias', 60)), 365))
    except (TypeError, ValueError):
        dias = 60

    cli_meta, out = _mix_cliente_fornecedores_rows(codcli, dias)
    if cli_meta is None:
        return jsonify({'ok': False, 'error': 'Cliente fora da sua carteira'}), 404

    cabecalho = ['Fornecedor', 'CodFornec', 'UltimaCompra', 'DiasParado', 'VendaCat12m', 'LucroCat12m']

    def gerar():
        yield CSV_PREAMBULO  # BOM UTF-8
        yield _csv_linha_br(cabecalho)
        for c in out:
            yield _csv_linha_br([
                c.get('fornec_nome'), c.get('codfornec'), c.get('ultima_compra'),
                c.get('dias_parado'), c.get('venda_cat_12m'), c.get('lucro_cat_12m'),
            ])

    nome_cli = _slug_export(cli_meta.get('cliente') or f'cliente-{codcli}')
    nome = f"mix_fornec_{codcli}_{nome_cli}_{dias}d_{_date.today().isoformat()}.csv"
    return Response(
        stream_with_context(gerar()),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': _content_disposition(nome)},
    )


@app.route('/api/mix/cliente/<int:codcli>/fornecedores/pdf')
@login_required
def api_mix_cliente_fornecedores_pdf(codcli):
    """PDF dos fornecedores que o cliente parou de comprar."""
    from datetime import date as _date
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    from io import BytesIO
    try:
        dias = max(7, min(int(request.args.get('dias', 60)), 365))
    except (TypeError, ValueError):
        dias = 60

    cli_meta, out = _mix_cliente_fornecedores_rows(codcli, dias)
    if cli_meta is None:
        return jsonify({'ok': False, 'error': 'Cliente fora da sua carteira'}), 404

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=1.5*cm, rightMargin=1.5*cm, topMargin=1.5*cm, bottomMargin=1.5*cm,
        title=f"Mix Fornecedores Cliente {codcli} {_date.today().isoformat()}",
    )
    styles = getSampleStyleSheet()
    titulo_style = ParagraphStyle('titulo', parent=styles['Heading1'], fontSize=14, alignment=TA_LEFT, textColor=colors.HexColor('#0a0e17'))
    sub_style = ParagraphStyle('sub', parent=styles['Normal'], fontSize=8, textColor=colors.HexColor('#475569'))

    story = []
    cliente = cli_meta.get('cliente') or f'Cliente #{codcli}'
    story.append(Paragraph(f"<b>JOGA Analytics</b> — Fornecedores parados · {cliente}", titulo_style))
    sub = (f"Gerado em {_date.today().strftime('%d/%m/%Y')} · #{codcli} · "
           f"{(cli_meta.get('cidade') or '')}/{cli_meta.get('uf') or ''} · "
           f"{cli_meta.get('vendedor') or '—'} · {len(out)} fornecedor(es) parado(s) há ≥ {dias} dias")
    story.append(Paragraph(sub, sub_style))
    story.append(Spacer(1, 0.3*cm))

    header = ['Fornecedor', 'Última compra', 'Dias parado', 'Venda 12m', 'Lucro 12m']
    data = [header]
    for c in out:
        data.append([
            (c.get('fornec_nome') or '')[:44],
            str(c.get('ultima_compra') or '')[:10],
            c.get('dias_parado') or '',
            f"R$ {(c.get('venda_cat_12m') or 0):,.0f}".replace(',', '.'),
            f"R$ {(c.get('lucro_cat_12m') or 0):,.0f}".replace(',', '.'),
        ])
    tbl = Table(data, repeatRows=1, colWidths=[8*cm, 2.6*cm, 2.2*cm, 2.7*cm, 2.7*cm])
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1e293b')),
        ('TEXTCOLOR',  (0,0), (-1,0), colors.white),
        ('FONTNAME',   (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE',   (0,0), (-1,-1), 8),
        ('GRID',       (0,0), (-1,-1), 0.3, colors.HexColor('#cbd5e1')),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f8fafc')]),
        ('ALIGN',      (2,0), (2,-1), 'CENTER'),
        ('ALIGN',      (3,0), (4,-1), 'RIGHT'),
        ('VALIGN',     (0,0), (-1,-1), 'MIDDLE'),
        ('LEFTPADDING',(0,0), (-1,-1), 4),
        ('RIGHTPADDING',(0,0), (-1,-1), 4),
    ]))
    story.append(tbl)

    def _rodape(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(colors.HexColor('#94a3b8'))
        canvas.drawRightString(doc.pagesize[0] - 1.5*cm, 0.8*cm, f"Página {doc.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_rodape, onLaterPages=_rodape)
    nome_cli = _slug_export(cliente)
    nome = f"mix_fornec_{codcli}_{nome_cli}_{dias}d_{_date.today().isoformat()}.pdf"
    return Response(
        buf.getvalue(),
        mimetype='application/pdf',
        headers={'Content-Disposition': _content_disposition(nome)},
    )


def _carregar_fornecedores_map():
    """Retorna {codfornec_str: nome}. Cache 24h. Usa FORNECPRINC textual da FAT_VENDAS."""
    key = 'multpel:fornecedores_map:v1'
    cached = _cache_get(key)
    if cached:
        return cached
    query = """EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODFORNECPRINC],
    FATURAMENTO_VENDAS[FORNECPRINC]
)"""
    token = get_token_cached()
    payload = retry_dax(execute_dax)(token, query)
    rows = clean_rows(_todas_linhas(payload))
    mapa = {}
    for r in rows:
        cf = r.get('CODFORNECPRINC')
        nome = r.get('FORNECPRINC')
        if cf is None:
            continue
        mapa[str(cf)] = nome or f'Fornec {cf}'
    _cache_set(key, mapa, 'metadata')
    return mapa


def _nome_arquivo_mix(ext, dias):
    """Nome do arquivo Mix Abandonado baseado nos filtros aplicados (resolvidos pra nomes).
    Ex: mix_60d_EMBALAGENS_GALVANOTEK_ARNALDO_AFONSO_ES_SUL_2026-06-24.pdf"""
    from datetime import date as _date
    partes = [f"{dias}d"]
    codepto = request.args.get('codepto')
    fornecedor = request.args.get('fornecedor')
    vendedor = request.args.get('vendedor')
    time_filt = request.args.get('time')
    busca = request.args.get('busca')

    if codepto:
        nome = _carregar_deptos_map().get('deptos', {}).get(str(codepto), f'Depto{codepto}')
        partes.append(_slug_export(nome))
    if fornecedor:
        nome = _carregar_fornecedores_map().get(str(fornecedor), f'Fornec{fornecedor}')
        partes.append(_slug_export(nome))
    if vendedor:
        v = _carregar_vendedores_map().get(str(vendedor))
        nome = v.get('nome') if v else f'RCA{vendedor}'
        partes.append(_slug_export(nome))
    if time_filt:
        s = _carregar_supervisores_map().get(str(time_filt))
        nome = s.get('nome') if s else f'Sup{time_filt}'
        partes.append(_slug_export(nome))
    if busca:
        partes.append(_slug_export(f'busca-{busca}'))

    partes_limpas = [p.replace(' ', '_') for p in partes if p]
    base = '_'.join(['mix'] + partes_limpas) if partes_limpas else 'mix_todos'
    return f"{base}_{_date.today().isoformat()}.{ext}"


def _mix_aplicar_filtros_locais(linhas, vendedor, time_filt, busca, codcli=None):
    """Aplica os filtros locais (vendedor/time/busca/codcli) que o frontend usa.
    Todos opcionais — se vazios, retorna a lista intacta.
    - codcli: casa o cliente EXATO (usado quando o usuário escolhe no autocomplete).
    - busca : número → PREFIXO de código; texto → trecho do nome/cidade. (Evita "222" casar
      no meio de qualquer código.)"""
    if codcli is not None:
        try:
            cc = int(codcli)
            linhas = [r for r in linhas if r.get('codcli') == cc]
        except (TypeError, ValueError):
            pass
    if vendedor:
        try:
            v = int(vendedor)
            linhas = [r for r in linhas if r.get('codusur') == v]
        except (TypeError, ValueError):
            pass
    if time_filt:
        try:
            t = int(time_filt)
            linhas = [r for r in linhas if r.get('codsupervisor') == t]
        except (TypeError, ValueError):
            pass
    if busca:
        b = busca.lower().strip()
        if b.isdigit():
            linhas = [r for r in linhas if str(r.get('codcli') or '').startswith(b)]
        else:
            linhas = [r for r in linhas
                      if b in (r.get('cliente') or '').lower()
                      or b in (r.get('cidade') or '').lower()]
    return linhas


@app.route('/api/mix/abandonado/csv')
@login_required
def api_mix_abandonado_csv():
    """Export CSV da lista COMPLETA de pares cliente×departamento abandonados (escopo de cadastro).
    Respeita filtros locais (vendedor/time/busca) — passados via query string."""
    from datetime import date as _date
    try:
        dias = max(7, min(int(request.args.get('dias', 60)), 365))
    except ValueError:
        dias = 60
    codepto = request.args.get('codepto')
    fornecedor = request.args.get('fornecedor')

    linhas = _mix_abandonado_rows(dias, codepto, fornecedor)
    linhas = _mix_aplicar_filtros_locais(
        linhas, request.args.get('vendedor'),
        request.args.get('time'), request.args.get('busca'),
        request.args.get('codcli'),
    )
    cabecalho = ['CodCli', 'Cliente', 'Cidade', 'UF', 'Departamento', 'UltimaCompra',
                 'DiasParado', 'VendaCat12m', 'LucroCat12m', 'Vendedor', 'Time', 'Telefone']

    def gerar():
        yield CSV_PREAMBULO  # BOM UTF-8
        yield _csv_linha_br(cabecalho)
        for c in linhas:
            yield _csv_linha_br([
                c.get('codcli'), c.get('cliente'), c.get('cidade'), c.get('uf'),
                c.get('depto_nome'), c.get('ultima_compra'),
                c.get('dias_sem_comprar_categoria'),
                c.get('venda_cat_12m'), c.get('lucro_cat_12m'),
                c.get('vendedor'), c.get('time'), c.get('telefone'),
            ])

    nome = _nome_arquivo_mix('csv', dias)
    return Response(
        stream_with_context(gerar()),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': _content_disposition(nome)},
    )


def _gerar_pdf_mix_abandonado(linhas, filtros_resumo='', dias=60):
    """Gera PDF da lista COMPLETA de mix abandonado (par cliente×departamento). Retorna bytes.
    Mesma estética do PDF da carteira (landscape A4, zebra)."""
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    from io import BytesIO
    from datetime import date as _date

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=1.2*cm, rightMargin=1.2*cm,
        topMargin=1.2*cm, bottomMargin=1.5*cm,
        title=f"Mix Abandonado JOGA {_date.today().isoformat()}",
    )
    styles = getSampleStyleSheet()
    titulo_style = ParagraphStyle('titulo', parent=styles['Heading1'], fontSize=14, alignment=TA_LEFT, textColor=colors.HexColor('#0a0e17'))
    sub_style = ParagraphStyle('sub', parent=styles['Normal'], fontSize=8, textColor=colors.HexColor('#475569'))

    story = []
    story.append(Paragraph('<b>JOGA Analytics</b> — Mix Abandonado', titulo_style))
    sub = (f"Gerado em {_date.today().strftime('%d/%m/%Y')} · {len(linhas)} oportunidades · "
           f"parados há ≥ {dias} dias" + (f" · {filtros_resumo}" if filtros_resumo else ''))
    story.append(Paragraph(sub, sub_style))
    story.append(Spacer(1, 0.3*cm))

    header = ['CodCli', 'Cliente', 'Cidade/UF', 'Departamento', 'Última Compra',
              'Dias Parado', 'Venda 12m', 'Lucro 12m', 'Telefone']
    data = [header]
    for c in linhas:
        venda = c.get('venda_cat_12m') or 0
        lucro = c.get('lucro_cat_12m') or 0
        cidade_uf = f"{(c.get('cidade') or '')[:18]}/{c.get('uf') or ''}"
        data.append([
            c.get('codcli') or '',
            (c.get('cliente') or '')[:40],
            cidade_uf,
            (c.get('depto_nome') or '')[:22],
            str(c.get('ultima_compra') or '')[:10],
            c.get('dias_sem_comprar_categoria') or '',
            f"R$ {venda:,.0f}".replace(',', '.'),
            f"R$ {lucro:,.0f}".replace(',', '.'),
            c.get('telefone') or '—',
        ])
    tbl = Table(data, repeatRows=1,
                colWidths=[1.5*cm, 6*cm, 3.4*cm, 3.4*cm, 2.3*cm, 1.8*cm, 2.3*cm, 2.3*cm, 3.4*cm])
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1e293b')),
        ('TEXTCOLOR',  (0,0), (-1,0), colors.white),
        ('FONTNAME',   (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE',   (0,0), (-1,-1), 7),
        ('GRID',       (0,0), (-1,-1), 0.3, colors.HexColor('#cbd5e1')),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f8fafc')]),
        ('ALIGN',      (5,0), (5,-1), 'CENTER'),
        ('ALIGN',      (6,0), (7,-1), 'RIGHT'),
        ('VALIGN',     (0,0), (-1,-1), 'MIDDLE'),
        ('LEFTPADDING',(0,0), (-1,-1), 4),
        ('RIGHTPADDING',(0,0), (-1,-1), 4),
    ]))
    story.append(tbl)

    def _rodape(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(colors.HexColor('#94a3b8'))
        canvas.drawRightString(doc.pagesize[0] - 1.2*cm, 0.8*cm, f"Página {doc.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_rodape, onLaterPages=_rodape)
    return buf.getvalue()


@app.route('/api/mix/abandonado/pdf')
@login_required
def api_mix_abandonado_pdf():
    """Export PDF da lista COMPLETA de mix abandonado (mesmos filtros do CSV, escopo de cadastro).
    Respeita filtros locais (vendedor/time/busca) — passados via query string."""
    from datetime import date as _date
    try:
        dias = max(7, min(int(request.args.get('dias', 60)), 365))
    except ValueError:
        dias = 60
    codepto = request.args.get('codepto')
    fornecedor = request.args.get('fornecedor')
    vendedor = request.args.get('vendedor')
    time_filt = request.args.get('time')
    busca = request.args.get('busca')
    codcli = request.args.get('codcli')

    linhas = _mix_abandonado_rows(dias, codepto, fornecedor)
    linhas = _mix_aplicar_filtros_locais(linhas, vendedor, time_filt, busca, codcli)

    # Resolve códigos → nomes pra mostrar no rodapé do PDF
    deptos_nomes = _carregar_deptos_map().get('deptos', {})
    fornecedores_map = _carregar_fornecedores_map()
    vendedores_map = _carregar_vendedores_map()
    supervisores_map = _carregar_supervisores_map()

    parts = []
    if codepto:
        parts.append(f"Depto: {deptos_nomes.get(str(codepto), codepto)}")
    if fornecedor:
        parts.append(f"Fornecedor: {fornecedores_map.get(str(fornecedor), fornecedor)}")
    if vendedor:
        v = vendedores_map.get(str(vendedor))
        nome_v = v.get('nome') if v else f'RCA {vendedor}'
        parts.append(f"Vendedor: {nome_v}")
    if time_filt:
        s = supervisores_map.get(str(time_filt))
        nome_s = s.get('nome') if s else f'Sup {time_filt}'
        parts.append(f"Time: {nome_s}")
    if busca:
        parts.append(f"Busca: \"{busca}\"")
    filtros_resumo = ' · '.join(parts)

    pdf_bytes = _gerar_pdf_mix_abandonado(linhas, filtros_resumo=filtros_resumo, dias=dias)
    nome = _nome_arquivo_mix('pdf', dias)
    return Response(
        pdf_bytes,
        mimetype='application/pdf',
        headers={'Content-Disposition': _content_disposition(nome)},
    )


# ──────────────────────────────────────────────────────────────────────
# Radar de Produtos — recompra por produto (cliente que parou de comprar X)
# Funil de 2 níveis: board de produtos sangrando → detalhe (clientes que pararam).
# Dados 100% na FATURAMENTO_VENDAS (CODPROD/DESCRICAO/QT/CODEPTO/CODFORNECPRINC).
# RBAC por CADASTRO (igual Mix): admin/viewer veem tudo (+override ?supervisor=),
# vendedor/supervisor restringem aos codclis do cadastro via _frag_codcli_cadastro().
# ──────────────────────────────────────────────────────────────────────

@app.route('/radar')
@login_required
def radar_page():
    return send_from_directory('.', 'radar.html')


def _carregar_produtos_map():
    """Índice GLOBAL {codprod_str: {descricao, codepto, codfornec, venda_12m}} dos produtos
    vendidos nos últimos 12m (~3.7 mil). Cache 24h. Usado pela busca type-ahead e pra
    resolver nome/depto do produto. É catálogo (não tem dado sensível) → global, sem RBAC."""
    key = 'multpel:produtos_map:v2'  # v2: incluiu fornec_nome (FORNECPRINC)
    cached = _cache_get(key)
    if cached:
        return cached

    query = """EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODPROD],
    FATURAMENTO_VENDAS[DESCRICAO],
    FATURAMENTO_VENDAS[CODEPTO],
    FATURAMENTO_VENDAS[CODFORNECPRINC],
    FATURAMENTO_VENDAS[FORNECPRINC],
    FILTER(FATURAMENTO_VENDAS, FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12)),
    "Venda", [VENDA LIQUIDA]
)"""
    token = get_token_cached()
    payload = retry_dax(execute_dax)(token, query)
    rows = clean_rows(_todas_linhas(payload))

    idx = {}
    best = {}  # codprod -> maior venda vista (pra escolher a DESCRICAO representativa)
    for r in rows:
        cp = r.get('CODPROD')
        if cp is None:
            continue
        cps = str(cp)
        venda = r.get('Venda') or 0
        if cps not in idx:
            idx[cps] = {'descricao': r.get('DESCRICAO'), 'codepto': r.get('CODEPTO'),
                        'codfornec': r.get('CODFORNECPRINC'), 'fornec_nome': r.get('FORNECPRINC'),
                        'venda_12m': 0.0}
            best[cps] = -1
        idx[cps]['venda_12m'] += venda
        if venda > best[cps]:  # linha mais relevante define descrição/depto/fornec do produto
            best[cps] = venda
            idx[cps]['descricao'] = r.get('DESCRICAO') or idx[cps]['descricao']
            if r.get('CODEPTO') is not None:        idx[cps]['codepto'] = r.get('CODEPTO')
            if r.get('CODFORNECPRINC') is not None: idx[cps]['codfornec'] = r.get('CODFORNECPRINC')
            if r.get('FORNECPRINC'):                idx[cps]['fornec_nome'] = r.get('FORNECPRINC')

    _cache_set(key, idx, 'metadata')
    return idx


@app.route('/api/radar/produtos/busca')
@login_required
def api_radar_produtos_busca():
    """Busca type-ahead de produto (em memória no índice cacheado). Casa por trecho da
    descrição OU código. Filtros opcionais por depto/fornecedor. Ordena por venda 12m desc."""
    q = (request.args.get('q') or '').strip().lower()
    try:
        limit = max(1, min(int(request.args.get('limit', 30)), 100))
    except (TypeError, ValueError):
        limit = 30

    def _int_or_none(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    f_depto = _int_or_none(request.args.get('codepto'))
    f_fornec = _int_or_none(request.args.get('fornecedor'))

    idx = _carregar_produtos_map()
    deptos_nomes = _carregar_deptos_map()['deptos']

    out = []
    for cps, v in idx.items():
        if f_depto is not None and v.get('codepto') != f_depto:
            continue
        if f_fornec is not None and v.get('codfornec') != f_fornec:
            continue
        if q and q not in (v.get('descricao') or '').lower() and q not in cps:
            continue
        out.append({
            'codprod':     int(cps),
            'descricao':   v.get('descricao') or f'Produto {cps}',
            'codepto':     v.get('codepto'),
            'depto_nome':  deptos_nomes.get(str(v.get('codepto'))) if v.get('codepto') is not None else None,
            'codfornec':   v.get('codfornec'),
            'fornec_nome': v.get('fornec_nome'),
            'venda_12m':   round(v.get('venda_12m') or 0, 2),
        })
    out.sort(key=lambda x: x['venda_12m'], reverse=True)
    return jsonify({'ok': True, 'total': len(out), 'produtos': out[:limit]})


def _radar_vendedor_filtro():
    """Lê ?vendedor=CODUSUR — SÓ admin/viewer (outros roles já travados pela sessão).
    Retorna int ou None."""
    if session.get('role') not in ('admin', 'viewer'):
        return None
    raw = (request.args.get('vendedor') or '').strip()
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _radar_filtrar_carteira_idx(carteira_idx):
    """Aplica o filtro de supervisor/vendedor escolhido (admin/viewer) sobre o índice de
    clientes do cadastro. Vendedor tem precedência. Retorna o idx filtrado (ou o mesmo)."""
    if session.get('role') not in ('admin', 'viewer'):
        return carteira_idx
    vend = _radar_vendedor_filtro()
    if vend is not None:
        return {k: v for k, v in carteira_idx.items() if v.get('codusur') == vend}
    sup = _supervisores_filtro()
    if sup:
        sset = set(sup)
        return {k: v for k, v in carteira_idx.items() if v.get('codsupervisor') in sset}
    return carteira_idx


def _radar_status(dias_parado, venda_rec, venda_ant, dias):
    """Classifica o cliente em relação ao produto:
    perdido (≥2×dias ou nunca) · parou (≥dias) · esfriando (comprou mas volume caiu >50%) · ativo."""
    if dias_parado is None or dias_parado >= 2 * dias:
        return 'perdido'
    if dias_parado >= dias:
        return 'parou'
    if venda_ant > 0 and venda_rec < 0.5 * venda_ant:
        return 'esfriando'
    return 'ativo'


def _radar_detalhe_rows(codprod, dias):
    """Núcleo do detalhe: por cliente (no escopo de CADASTRO), métricas do produto +
    janela recente vs anterior (queda de volume) + flag troca-vs-abandono. Retorna
    (produto_info, linhas). Sem cache (o endpoint cacheia o payload final)."""
    from datetime import date as _date

    info = _carregar_produtos_map().get(str(codprod)) or {}
    codepto = info.get('codepto')
    d2 = 2 * dias
    f_prod = f"FATURAMENTO_VENDAS[CODPROD] = {int(codprod)}"

    queries = {
        'cli': f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODCLI],
    FILTER(FATURAMENTO_VENDAS, {f_prod} && FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12)),
    "Ultima",   MAX(FATURAMENTO_VENDAS[DTSAIDA]),
    "Venda12m", [VENDA LIQUIDA],
    "Qt12m",    SUM(FATURAMENTO_VENDAS[QT])
)""",
        'rec': f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODCLI],
    FILTER(FATURAMENTO_VENDAS, {f_prod} && FATURAMENTO_VENDAS[DTSAIDA] >= TODAY() - {dias}),
    "VendaRec", [VENDA LIQUIDA],
    "QtRec",    SUM(FATURAMENTO_VENDAS[QT])
)""",
        'ant': f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODCLI],
    FILTER(FATURAMENTO_VENDAS, {f_prod}
        && FATURAMENTO_VENDAS[DTSAIDA] >= TODAY() - {d2}
        && FATURAMENTO_VENDAS[DTSAIDA] < TODAY() - {dias}),
    "VendaAnt", [VENDA LIQUIDA],
    "QtAnt",    SUM(FATURAMENTO_VENDAS[QT])
)""",
    }
    # Canibalização: clientes que compraram OUTRO produto do MESMO depto nos últimos `dias`.
    if codepto is not None:
        queries['canib'] = f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODCLI],
    FILTER(FATURAMENTO_VENDAS,
        FATURAMENTO_VENDAS[CODEPTO] = {int(codepto)}
        && FATURAMENTO_VENDAS[CODPROD] <> {int(codprod)}
        && FATURAMENTO_VENDAS[DTSAIDA] >= TODAY() - {dias})
)"""
    resultados = _executar_dax_paralelo_n(queries, max_workers=4)

    rec_idx = {r['CODCLI']: r for r in clean_rows(_todas_linhas(resultados['rec'])) if r.get('CODCLI') is not None}
    ant_idx = {r['CODCLI']: r for r in clean_rows(_todas_linhas(resultados['ant'])) if r.get('CODCLI') is not None}
    canib = set()
    if 'canib' in resultados:
        canib = {r['CODCLI'] for r in clean_rows(_todas_linhas(resultados['canib'])) if r.get('CODCLI') is not None}

    hoje = _date.today()
    carteira_idx = {c['codcli']: c for c in _carteira_no_escopo()}   # recorte por cadastro
    carteira_idx = _radar_filtrar_carteira_idx(carteira_idx)         # +filtro supervisor/vendedor (admin)

    linhas = []
    for r in clean_rows(_todas_linhas(resultados['cli'])):
        cc = r.get('CODCLI')
        if cc is None or cc not in carteira_idx:   # fora do escopo do usuário
            continue
        ultima = r.get('Ultima')
        dias_parado = None
        if ultima:
            try:
                dias_parado = (hoje - _date.fromisoformat(str(ultima)[:10])).days
            except ValueError:
                dias_parado = None
        venda_rec = (rec_idx.get(cc, {}).get('VendaRec')) or 0
        qt_rec    = (rec_idx.get(cc, {}).get('QtRec')) or 0
        venda_ant = (ant_idx.get(cc, {}).get('VendaAnt')) or 0
        qt_ant    = (ant_idx.get(cc, {}).get('QtAnt')) or 0
        status = _radar_status(dias_parado, venda_rec, venda_ant, dias)
        parou = status in ('parou', 'perdido')
        meta = carteira_idx.get(cc, {})
        linhas.append({
            'codcli':        cc,
            'cliente':       meta.get('cliente') or f'Cliente #{cc}',
            'cidade':        meta.get('cidade'),
            'uf':            meta.get('uf'),
            'vendedor':      meta.get('vendedor'),
            'codusur':       meta.get('codusur'),
            'telefone':      meta.get('telefone'),
            'ultima_compra': str(ultima)[:10] if ultima else None,
            'dias_parado':   dias_parado,
            'venda_12m':     round(r.get('Venda12m') or 0, 2),
            'qt_12m':        round(r.get('Qt12m') or 0, 2),
            'venda_rec':     round(venda_rec, 2),
            'qt_rec':        round(qt_rec, 2),
            'venda_ant':     round(venda_ant, 2),
            'qt_ant':        round(qt_ant, 2),
            'status':        status,
            # parou DESTE produto mas comprou OUTRO do mesmo depto → trocou (não é churn puro)
            'trocou':        bool(parou and cc in canib),
        })
    linhas.sort(key=lambda x: x['venda_12m'], reverse=True)   # potencial de recuperação
    return info, linhas


@app.route('/api/radar/produto/<int:codprod>')
@login_required
def api_radar_produto(codprod):
    """Detalhe: clientes que compram/compravam o produto, com status de recência,
    queda de volume (recente vs anterior) e flag troca-vs-abandono. Escopo por cadastro."""
    try:
        dias = max(7, min(int(request.args.get('dias', 60)), 365))
    except (TypeError, ValueError):
        dias = 60

    sup = _supervisores_filtro()
    vend = _radar_vendedor_filtro()
    key = cache_key_for_user(f'radar:produto:{codprod}',
                             {'dias': dias, 'supervisor': _sup_cache_key(sup), 'vendedor': vend if vend is not None else '-'})
    cached = _cache_get(key)
    if cached:
        return jsonify(cached)

    info, linhas = _radar_detalhe_rows(codprod, dias)

    deptos_nomes = _carregar_deptos_map()['deptos']
    codepto = info.get('codepto')
    parados = [c for c in linhas if c['status'] in ('parou', 'perdido')]
    resp = {
        'ok': True,
        'codprod': codprod,
        'dias': dias,
        'produto': {
            'codprod':     codprod,
            'descricao':   info.get('descricao') or f'Produto {codprod}',
            'codepto':     codepto,
            'depto_nome':  deptos_nomes.get(str(codepto)) if codepto is not None else None,
            'fornec_nome': info.get('fornec_nome'),
        },
        'kpis': {
            'clientes':         len(linhas),
            'parados':          len(parados),
            'esfriou_ou_parou': sum(1 for c in linhas if c['status'] in ('esfriando', 'parou', 'perdido')),
            'trocaram':         sum(1 for c in parados if c['trocou']),
            'receita_em_risco': round(sum(c['venda_12m'] for c in parados), 2),
        },
        'rows': linhas,
    }
    _cache_set(key, resp, 'dax_agregado')
    return jsonify(resp)


def _radar_filtrar_fornec(rows, fornecedor):
    """Filtra as linhas do board pelo fornecedor (codfornec do produto). Sem fornecedor → intacto.
    Em memória sobre a lista já cacheada — combina em E com o escopo supervisor/vendedor."""
    if not fornecedor:
        return rows
    try:
        cf = int(fornecedor)
    except (TypeError, ValueError):
        return rows
    return [r for r in rows if r.get('codfornec') == cf]


def _radar_board_full(dias):
    """Lista COMPLETA de produtos sangrando (janela recente vs anterior), ordenada por queda de
    receita desc, no escopo da SESSÃO (supervisor/vendedor/cadastro). Cacheada por (dias, escopo).
    SEM filtro de fornecedor (esse é aplicado depois, em memória). Reusada pelo board e exports."""
    d2 = 2 * dias
    sup = _supervisores_filtro()
    vend = _radar_vendedor_filtro()
    key = cache_key_for_user('radar:board',
                             {'dias': dias, 'supervisor': _sup_cache_key(sup), 'vendedor': vend if vend is not None else '-'})
    cached = _cache_get(key)
    if cached:
        return cached['rows']

    # Escopo. Não-admin: por CADASTRO (mesmo padrão de api_categorias).
    # Admin/viewer: filtro escolhido por venda (vendedor tem precedência sobre supervisor).
    if session.get('role') in ('admin', 'viewer'):
        if vend is not None:
            rbac_frag = f" && FATURAMENTO_VENDAS[CODUSUR] = {vend}"
        else:
            sup_frag = _frag_supervisores('FATURAMENTO_VENDAS', sup)
            rbac_frag = f" && {sup_frag}" if sup_frag else ''
    else:
        frag, ok = _frag_codcli_cadastro()
        rbac_frag = frag if ok else ((f" && {aplicar_rbac_dax()}") if aplicar_rbac_dax() else '')

    queries = {
        'rec': f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODPROD],
    FILTER(FATURAMENTO_VENDAS, FATURAMENTO_VENDAS[DTSAIDA] >= TODAY() - {dias}{rbac_frag}),
    "VendaRec", [VENDA LIQUIDA],
    "CliRec",   DISTINCTCOUNT(FATURAMENTO_VENDAS[CODCLI])
)""",
        'ant': f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODPROD],
    FILTER(FATURAMENTO_VENDAS,
        FATURAMENTO_VENDAS[DTSAIDA] >= TODAY() - {d2}
        && FATURAMENTO_VENDAS[DTSAIDA] < TODAY() - {dias}{rbac_frag}),
    "VendaAnt", [VENDA LIQUIDA],
    "CliAnt",   DISTINCTCOUNT(FATURAMENTO_VENDAS[CODCLI])
)""",
    }
    resultados = _executar_dax_paralelo_n(queries, max_workers=2)

    rec = {r['CODPROD']: r for r in clean_rows(_todas_linhas(resultados['rec'])) if r.get('CODPROD') is not None}
    ant = {r['CODPROD']: r for r in clean_rows(_todas_linhas(resultados['ant'])) if r.get('CODPROD') is not None}

    prod_idx = _carregar_produtos_map()
    deptos_nomes = _carregar_deptos_map()['deptos']

    out = []
    for cp in set(rec) | set(ant):
        v_rec = (rec.get(cp, {}).get('VendaRec')) or 0
        v_ant = (ant.get(cp, {}).get('VendaAnt')) or 0
        c_rec = (rec.get(cp, {}).get('CliRec')) or 0
        c_ant = (ant.get(cp, {}).get('CliAnt')) or 0
        queda = round(v_ant - v_rec, 2)
        if queda <= 0:
            continue   # board só mostra quem está sangrando (perdeu receita)
        info = prod_idx.get(str(cp)) or {}
        codepto = info.get('codepto')
        out.append({
            'codprod':           cp,
            'descricao':         info.get('descricao') or f'Produto {cp}',
            'codepto':           codepto,
            'depto_nome':        deptos_nomes.get(str(codepto)) if codepto is not None else None,
            'codfornec':         info.get('codfornec'),
            'fornec_nome':       info.get('fornec_nome'),
            'venda_rec':         round(v_rec, 2),
            'venda_ant':         round(v_ant, 2),
            'queda_receita':     queda,
            'pct_queda':         round((queda / v_ant), 4) if v_ant else None,
            'clientes_perdidos': max(0, c_ant - c_rec),
        })
    out.sort(key=lambda x: x['queda_receita'], reverse=True)
    _cache_set(key, {'ok': True, 'dias': dias, 'total': len(out), 'rows': out}, 'dax_agregado')
    return out


# Métricas de ordenação do board (mesmas do select "Ordenar board por" do front).
_RADAR_BOARD_METRICAS = {
    'queda_receita':     'Receita em risco',
    'clientes_perdidos': 'Clientes perdidos',
    'pct_queda':         '% de queda',
}


def _radar_board_ordenar(rows, metrica):
    """Ordena as linhas do board pela métrica escolhida (desc). Default = queda de receita."""
    if metrica not in _RADAR_BOARD_METRICAS:
        metrica = 'queda_receita'
    return sorted(rows, key=lambda r: (r.get(metrica) or 0), reverse=True), metrica


def _radar_fornec_nome(codfornec):
    """Nome do fornecedor a partir do catálogo de produtos (codfornec int). None se não achar.
    Usa produtos_map (codfornec já normalizado) — evita o mismatch de chave float do
    fornecedores_map ('113.0' vs '113')."""
    try:
        cf = int(codfornec)
    except (TypeError, ValueError):
        return None
    for v in _carregar_produtos_map().values():
        c = v.get('codfornec')
        if c is not None:
            try:
                if int(c) == cf:
                    return v.get('fornec_nome')
            except (TypeError, ValueError):
                pass
    return None


def _nome_arquivo_radar_board(ext, dias, metrica):
    """Nome do arquivo do board do Radar, baseado nos filtros ativos (resolvidos pra nomes).
    Ex: radar_board_60d_receita-risco_AFONSO_ES-SUL_BOMBRIL_2026-07-06.csv"""
    from datetime import date as _date
    slug_metrica = {'queda_receita': 'receita-risco', 'clientes_perdidos': 'clientes-perdidos',
                    'pct_queda': 'pct-queda'}.get(metrica, 'receita-risco')
    partes = [f"{dias}d", slug_metrica]

    vend = _radar_vendedor_filtro()
    if vend is not None:
        v = _carregar_vendedores_map().get(str(vend))
        partes.append(_slug_export(v.get('nome') if v else f'RCA{vend}'))
    else:
        for cs in (_supervisores_filtro() or []):
            s = _carregar_supervisores_map().get(str(cs))
            partes.append(_slug_export(s.get('nome') if s else f'Sup{cs}'))
    fornecedor = request.args.get('fornecedor')
    if fornecedor:
        nome = _radar_fornec_nome(fornecedor) or f'Fornec{fornecedor}'
        partes.append(_slug_export(nome))

    partes_limpas = [p.replace(' ', '_') for p in partes if p]
    base = '_'.join(['radar_board'] + partes_limpas)
    return f"{base}_{_date.today().isoformat()}.{ext}"


@app.route('/api/radar/board/csv')
@login_required
def api_radar_board_csv():
    """CSV do board (produtos perdendo receita) — respeita janela/escopo/fornecedor + ordenação."""
    try:
        dias = max(7, min(int(request.args.get('dias', 60)), 365))
    except (TypeError, ValueError):
        dias = 60
    fornecedor = request.args.get('fornecedor')
    rows = _radar_filtrar_fornec(_radar_board_full(dias), fornecedor)
    rows, metrica = _radar_board_ordenar(rows, request.args.get('sort'))

    cabecalho = ['#', 'CodProd', 'Produto', 'Departamento', 'Fornecedor', 'ReceitaEmRisco',
                 'PctQueda', 'ClientesPerdidos', 'VendaAnterior', 'VendaRecente']

    def gerar():
        yield CSV_PREAMBULO  # BOM UTF-8
        yield _csv_linha(cabecalho)
        for i, r in enumerate(rows, 1):
            pct = r.get('pct_queda')
            yield _csv_linha([
                i, r.get('codprod'), r.get('descricao'), r.get('depto_nome'), r.get('fornec_nome'),
                r.get('queda_receita'), (round(pct * 100, 1) if pct is not None else ''),
                r.get('clientes_perdidos'), r.get('venda_ant'), r.get('venda_rec'),
            ])

    nome = _nome_arquivo_radar_board('csv', dias, metrica)
    return Response(
        stream_with_context(gerar()),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': _content_disposition(nome)},
    )


def _gerar_pdf_radar_board(rows, dias, metrica, resumo=''):
    """PDF do board do Radar (produtos perdendo receita). Landscape A4, zebra — igual aos outros."""
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    from io import BytesIO
    from datetime import date as _date

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=1.2*cm, rightMargin=1.2*cm, topMargin=1.2*cm, bottomMargin=1.5*cm,
        title=f"Radar Board JOGA {_date.today().isoformat()}",
    )
    styles = getSampleStyleSheet()
    titulo_style = ParagraphStyle('titulo', parent=styles['Heading1'], fontSize=14, alignment=TA_LEFT, textColor=colors.HexColor('#0a0e17'))
    sub_style = ParagraphStyle('sub', parent=styles['Normal'], fontSize=8, textColor=colors.HexColor('#475569'))

    story = []
    story.append(Paragraph('<b>JOGA Analytics</b> — Radar · Produtos perdendo receita', titulo_style))
    sub = (f"Gerado em {_date.today().strftime('%d/%m/%Y')} · janela {dias}d · "
           f"ordenado por {_RADAR_BOARD_METRICAS.get(metrica, '')} · {len(rows)} produtos"
           + (f" · {resumo}" if resumo else ''))
    story.append(Paragraph(sub, sub_style))
    story.append(Spacer(1, 0.3*cm))

    header = ['#', 'Produto', 'Departamento', 'Fornecedor', 'Receita em risco', '% queda', 'Clientes perdidos']
    data = [header]
    for i, r in enumerate(rows, 1):
        pct = r.get('pct_queda')
        data.append([
            i,
            (r.get('descricao') or '')[:40],
            (r.get('depto_nome') or '')[:20],
            (r.get('fornec_nome') or '')[:26],
            f"R$ {(r.get('queda_receita') or 0):,.0f}".replace(',', '.'),
            (f"{pct*100:.0f}%" if pct is not None else '—'),
            r.get('clientes_perdidos') or 0,
        ])
    tbl = Table(data, repeatRows=1,
                colWidths=[1*cm, 7.5*cm, 3.6*cm, 5.2*cm, 3*cm, 1.8*cm, 2.4*cm])
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1e293b')),
        ('TEXTCOLOR',  (0,0), (-1,0), colors.white),
        ('FONTNAME',   (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE',   (0,0), (-1,-1), 7),
        ('GRID',       (0,0), (-1,-1), 0.3, colors.HexColor('#cbd5e1')),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f8fafc')]),
        ('ALIGN',      (4,0), (4,-1), 'RIGHT'),
        ('ALIGN',      (5,0), (6,-1), 'CENTER'),
        ('VALIGN',     (0,0), (-1,-1), 'MIDDLE'),
        ('LEFTPADDING',(0,0), (-1,-1), 3),
        ('RIGHTPADDING',(0,0), (-1,-1), 3),
    ]))
    story.append(tbl)

    def _rodape(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(colors.HexColor('#94a3b8'))
        canvas.drawRightString(doc.pagesize[0] - 1.2*cm, 0.8*cm, f"Página {doc.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_rodape, onLaterPages=_rodape)
    return buf.getvalue()


@app.route('/api/radar/board/pdf')
@login_required
def api_radar_board_pdf():
    """PDF do board (produtos perdendo receita) — respeita janela/escopo/fornecedor + ordenação."""
    try:
        dias = max(7, min(int(request.args.get('dias', 60)), 365))
    except (TypeError, ValueError):
        dias = 60
    fornecedor = request.args.get('fornecedor')
    rows = _radar_filtrar_fornec(_radar_board_full(dias), fornecedor)
    rows, metrica = _radar_board_ordenar(rows, request.args.get('sort'))

    # Resumo (fornecedor/escopo) pro subtítulo do PDF
    parts = []
    vend = _radar_vendedor_filtro()
    if vend is not None:
        v = _carregar_vendedores_map().get(str(vend))
        parts.append(f"Vendedor: {v.get('nome') if v else vend}")
    else:
        for cs in (_supervisores_filtro() or []):
            s = _carregar_supervisores_map().get(str(cs))
            parts.append(f"Time: {s.get('nome') if s else cs}")
    if fornecedor:
        parts.append(f"Fornecedor: {_radar_fornec_nome(fornecedor) or fornecedor}")

    pdf_bytes = _gerar_pdf_radar_board(rows, dias, metrica, resumo=' · '.join(parts))
    nome = _nome_arquivo_radar_board('pdf', dias, metrica)
    return Response(
        pdf_bytes,
        mimetype='application/pdf',
        headers={'Content-Disposition': _content_disposition(nome)},
    )


@app.route('/api/radar/fornecedores')
@login_required
def api_radar_fornecedores():
    """Fornecedores presentes no catálogo do Radar (produtos vendidos nos últimos 12m), pro
    autocomplete do filtro. Derivado do produtos_map cacheado → sem query nova. Lista completa
    (não é top-N), ordenada por nome."""
    idx = _carregar_produtos_map()
    vistos = {}
    for v in idx.values():
        cf = v.get('codfornec')
        if cf is None:
            continue
        try:
            cf = int(cf)   # DAX manda float (113.0) → int limpo pro autocomplete/URL
        except (TypeError, ValueError):
            continue
        vistos.setdefault(cf, v.get('fornec_nome') or f'Fornec {cf}')
    fornecedores = [{'codfornec': cf, 'nome': nome} for cf, nome in vistos.items()]
    fornecedores.sort(key=lambda x: (x['nome'] or '').lower())
    return jsonify({'ok': True, 'total': len(fornecedores), 'fornecedores': fornecedores})


@app.route('/api/radar/board')
@login_required
def api_radar_board():
    """Board (Nível 1): produtos que mais perderam receita — janela recente vs anterior.
    Métrica principal = queda de receita (venda anterior − venda recente). Escopo por cadastro.

    Nota: 'receita em risco' aqui é o proxy barato 'queda de receita no período' (agrupa só por
    produto). A receita em risco PRECISA por cliente sai no detalhe (/api/radar/produto/<x>)."""
    try:
        dias = max(7, min(int(request.args.get('dias', 60)), 365))
        limit = max(10, min(int(request.args.get('limit', 200)), 1000))
    except (TypeError, ValueError):
        dias, limit = 60, 200
    fornecedor = request.args.get('fornecedor')
    full = _radar_filtrar_fornec(_radar_board_full(dias), fornecedor)
    return jsonify({'ok': True, 'dias': dias, 'total': len(full), 'rows': full[:limit]})


_RADAR_STATUS_PT = {'ativo': 'Comprando', 'esfriando': 'Esfriando', 'parou': 'Parou', 'perdido': 'Perdido'}


def _radar_situacao(c):
    """Rótulo troca-vs-abandono pra quem parou; vazio pra quem ainda compra."""
    if c['status'] not in ('parou', 'perdido'):
        return ''
    return 'Trocou (outro do depto)' if c.get('trocou') else 'Abandonou'


@app.route('/api/radar/produto/<int:codprod>/csv')
@login_required
def api_radar_produto_csv(codprod):
    """CSV da lista COMPLETA de clientes do produto (escopo de cadastro)."""
    from datetime import date as _date
    try:
        dias = max(7, min(int(request.args.get('dias', 60)), 365))
    except (TypeError, ValueError):
        dias = 60
    info, linhas = _radar_detalhe_rows(codprod, dias)

    cabecalho = ['CodCli', 'Cliente', 'Cidade', 'UF', 'UltimaCompra', 'DiasParado', 'Situacao',
                 'Situacao_Troca', 'Venda12m', 'Qtd12m', 'VendaAnterior', 'VendaRecente', 'Vendedor', 'Telefone']

    def gerar():
        yield CSV_PREAMBULO  # BOM UTF-8
        yield _csv_linha(cabecalho)
        for c in linhas:
            yield _csv_linha([
                c.get('codcli'), c.get('cliente'), c.get('cidade'), c.get('uf'),
                c.get('ultima_compra'), c.get('dias_parado'),
                _RADAR_STATUS_PT.get(c.get('status'), c.get('status')), _radar_situacao(c),
                c.get('venda_12m'), c.get('qt_12m'), c.get('venda_ant'), c.get('venda_rec'),
                c.get('vendedor'), c.get('telefone'),
            ])

    nome = f"radar_produto_{codprod}_{dias}dias_{_date.today().isoformat()}.csv"
    return Response(
        stream_with_context(gerar()),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': _content_disposition(nome)},
    )


def _gerar_pdf_radar_produto(produto, linhas, dias=60):
    """PDF da lista de clientes do produto. Mesma estética dos outros exports (landscape A4)."""
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    from io import BytesIO
    from datetime import date as _date

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=1.2*cm, rightMargin=1.2*cm, topMargin=1.2*cm, bottomMargin=1.5*cm,
        title=f"Radar Produto {produto.get('codprod')} {_date.today().isoformat()}",
    )
    styles = getSampleStyleSheet()
    titulo_style = ParagraphStyle('titulo', parent=styles['Heading1'], fontSize=14, alignment=TA_LEFT, textColor=colors.HexColor('#0a0e17'))
    sub_style = ParagraphStyle('sub', parent=styles['Normal'], fontSize=8, textColor=colors.HexColor('#475569'))

    story = []
    story.append(Paragraph(f"<b>JOGA Analytics</b> — Radar · {produto.get('descricao') or ''}", titulo_style))
    parados = [c for c in linhas if c['status'] in ('parou', 'perdido')]
    sub = (f"Gerado em {_date.today().strftime('%d/%m/%Y')} · {produto.get('depto_nome') or ''}"
           f" · {produto.get('fornec_nome') or ''} · {len(linhas)} clientes · {len(parados)} pararam (≥ {dias} dias)")
    story.append(Paragraph(sub, sub_style))
    story.append(Spacer(1, 0.3*cm))

    header = ['Cliente', 'Cidade/UF', 'Última compra', 'Dias', 'Situação', 'Troca/Abandono',
              'Comprava (R$)', 'Agora (R$)', 'Venda 12m', 'Vendedor', 'Telefone']
    data = [header]
    for c in linhas:
        cidade_uf = f"{(c.get('cidade') or '')[:16]}/{c.get('uf') or ''}"
        data.append([
            (c.get('cliente') or '')[:34],
            cidade_uf,
            str(c.get('ultima_compra') or '')[:10],
            c.get('dias_parado') if c.get('dias_parado') is not None else '',
            _RADAR_STATUS_PT.get(c.get('status'), ''),
            _radar_situacao(c),
            f"R$ {(c.get('venda_ant') or 0):,.0f}".replace(',', '.'),
            f"R$ {(c.get('venda_rec') or 0):,.0f}".replace(',', '.'),
            f"R$ {(c.get('venda_12m') or 0):,.0f}".replace(',', '.'),
            (c.get('vendedor') or '')[:18],
            c.get('telefone') or '',
        ])
    tbl = Table(data, repeatRows=1,
                colWidths=[5.2*cm, 3*cm, 2*cm, 1.1*cm, 1.8*cm, 3*cm, 2.1*cm, 2.1*cm, 2.1*cm, 3*cm, 2.3*cm])
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1e293b')),
        ('TEXTCOLOR',  (0,0), (-1,0), colors.white),
        ('FONTNAME',   (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE',   (0,0), (-1,-1), 6.5),
        ('GRID',       (0,0), (-1,-1), 0.3, colors.HexColor('#cbd5e1')),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f8fafc')]),
        ('ALIGN',      (3,0), (3,-1), 'CENTER'),
        ('ALIGN',      (6,0), (8,-1), 'RIGHT'),
        ('VALIGN',     (0,0), (-1,-1), 'MIDDLE'),
        ('LEFTPADDING',(0,0), (-1,-1), 3),
        ('RIGHTPADDING',(0,0), (-1,-1), 3),
    ]))
    story.append(tbl)

    def _rodape(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(colors.HexColor('#94a3b8'))
        canvas.drawRightString(doc.pagesize[0] - 1.2*cm, 0.8*cm, f"Página {doc.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_rodape, onLaterPages=_rodape)
    return buf.getvalue()


@app.route('/api/radar/produto/<int:codprod>/pdf')
@login_required
def api_radar_produto_pdf(codprod):
    """PDF da lista COMPLETA de clientes do produto (escopo de cadastro)."""
    from datetime import date as _date
    try:
        dias = max(7, min(int(request.args.get('dias', 60)), 365))
    except (TypeError, ValueError):
        dias = 60
    info, linhas = _radar_detalhe_rows(codprod, dias)
    deptos_nomes = _carregar_deptos_map()['deptos']
    codepto = info.get('codepto')
    produto = {
        'codprod': codprod,
        'descricao': info.get('descricao') or f'Produto {codprod}',
        'depto_nome': deptos_nomes.get(str(codepto)) if codepto is not None else None,
        'fornec_nome': info.get('fornec_nome'),
    }
    pdf_bytes = _gerar_pdf_radar_produto(produto, linhas, dias=dias)
    nome = f"radar_produto_{codprod}_{dias}dias_{_date.today().isoformat()}.pdf"
    return Response(
        pdf_bytes,
        mimetype='application/pdf',
        headers={'Content-Disposition': _content_disposition(nome)},
    )


@app.route('/api/radar/produto/<int:codprod>/cliente/<int:codcli>/serie')
@login_required
def api_radar_produto_cliente_serie(codprod, codcli):
    """Drill: série mensal (12m) das compras DESTE produto por ESTE cliente (sparkline).
    Guarda de escopo: cliente precisa estar no cadastro do usuário (404 fora)."""
    carteira_idx = {c['codcli']: c for c in _carteira_no_escopo()}
    cli_meta = carteira_idx.get(codcli)
    if cli_meta is None:
        return jsonify({'ok': False, 'error': 'Cliente fora da sua carteira'}), 404

    query = f"""EVALUATE
SUMMARIZECOLUMNS(
    CALENDARIO[AnoMes],
    FILTER(FATURAMENTO_VENDAS,
        FATURAMENTO_VENDAS[CODPROD] = {int(codprod)}
        && FATURAMENTO_VENDAS[CODCLI] = {int(codcli)}
        && FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12)),
    "Venda", [VENDA LIQUIDA],
    "Qt",    SUM(FATURAMENTO_VENDAS[QT])
)"""
    token = get_token_cached()
    payload = retry_dax(execute_dax)(token, query)
    rows = []
    for r in clean_rows(_todas_linhas(payload)):
        am = r.get('AnoMes')
        if am is None:
            continue
        rows.append({'anomes': int(am), 'venda': round(r.get('Venda') or 0, 2), 'qt': round(r.get('Qt') or 0, 2)})
    rows.sort(key=lambda x: x['anomes'])
    return jsonify({
        'ok': True,
        'codprod': codprod,
        'codcli': codcli,
        'cliente': cli_meta.get('cliente') or f'Cliente #{codcli}',
        'rows': rows,
    })


def _radar_cliente_rows(codcli, dias):
    """Transposto de _radar_detalhe_rows: fixa CODCLI e agrupa por CODPROD → produtos que ESTE
    cliente parou de comprar. Janela recente vs anterior (queda de volume), status via
    _radar_status. Retorna (cli_meta | None, linhas). cli_meta None = fora do escopo (404).
    Só produtos com status parou/perdido (o que ele 'não está comprando'), ordenados por
    venda 12m desc (potencial de recuperação)."""
    from datetime import date as _date

    carteira_idx = {c['codcli']: c for c in _carteira_no_escopo()}
    cli_meta = carteira_idx.get(codcli)
    if cli_meta is None:
        return None, []

    d2 = 2 * dias
    f_cli = f"FATURAMENTO_VENDAS[CODCLI] = {int(codcli)}"
    queries = {
        'prod': f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODPROD],
    FILTER(FATURAMENTO_VENDAS, {f_cli} && FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12)),
    "Ultima",   MAX(FATURAMENTO_VENDAS[DTSAIDA]),
    "Venda12m", [VENDA LIQUIDA],
    "Qt12m",    SUM(FATURAMENTO_VENDAS[QT])
)""",
        'rec': f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODPROD],
    FILTER(FATURAMENTO_VENDAS, {f_cli} && FATURAMENTO_VENDAS[DTSAIDA] >= TODAY() - {dias}),
    "VendaRec", [VENDA LIQUIDA],
    "QtRec",    SUM(FATURAMENTO_VENDAS[QT])
)""",
        'ant': f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODPROD],
    FILTER(FATURAMENTO_VENDAS, {f_cli}
        && FATURAMENTO_VENDAS[DTSAIDA] >= TODAY() - {d2}
        && FATURAMENTO_VENDAS[DTSAIDA] < TODAY() - {dias}),
    "VendaAnt", [VENDA LIQUIDA],
    "QtAnt",    SUM(FATURAMENTO_VENDAS[QT])
)""",
    }
    resultados = _executar_dax_paralelo_n(queries, max_workers=3)

    rec_idx = {r['CODPROD']: r for r in clean_rows(_todas_linhas(resultados['rec'])) if r.get('CODPROD') is not None}
    ant_idx = {r['CODPROD']: r for r in clean_rows(_todas_linhas(resultados['ant'])) if r.get('CODPROD') is not None}

    prod_map = _carregar_produtos_map()
    deptos_nomes = _carregar_deptos_map()['deptos']
    hoje = _date.today()

    linhas = []
    for r in clean_rows(_todas_linhas(resultados['prod'])):
        cp = r.get('CODPROD')
        if cp is None:
            continue
        ultima = r.get('Ultima')
        dias_parado = None
        if ultima:
            try:
                dias_parado = (hoje - _date.fromisoformat(str(ultima)[:10])).days
            except ValueError:
                dias_parado = None
        venda_rec = (rec_idx.get(cp, {}).get('VendaRec')) or 0
        qt_rec    = (rec_idx.get(cp, {}).get('QtRec')) or 0
        venda_ant = (ant_idx.get(cp, {}).get('VendaAnt')) or 0
        qt_ant    = (ant_idx.get(cp, {}).get('QtAnt')) or 0
        status = _radar_status(dias_parado, venda_rec, venda_ant, dias)
        if status not in ('parou', 'perdido'):
            continue  # só o que o cliente DEIXOU de comprar
        info = prod_map.get(str(cp)) or {}
        codepto = info.get('codepto')
        linhas.append({
            'codprod':       cp,
            'descricao':     info.get('descricao') or f'Produto {cp}',
            'codepto':       codepto,
            'depto_nome':    deptos_nomes.get(str(codepto)) if codepto is not None else None,
            'fornec_nome':   info.get('fornec_nome'),
            'ultima_compra': str(ultima)[:10] if ultima else None,
            'dias_parado':   dias_parado,
            'venda_12m':     round(r.get('Venda12m') or 0, 2),
            'qt_12m':        round(r.get('Qt12m') or 0, 2),
            'venda_rec':     round(venda_rec, 2),
            'qt_rec':        round(qt_rec, 2),
            'venda_ant':     round(venda_ant, 2),
            'qt_ant':        round(qt_ant, 2),
            'status':        status,
        })
    linhas.sort(key=lambda x: x['venda_12m'], reverse=True)
    return cli_meta, linhas


@app.route('/api/radar/cliente/<int:codcli>')
@login_required
def api_radar_cliente(codcli):
    """Radar invertido: produtos que ESTE cliente parou de comprar (status parou/perdido).
    Consulta enxuta (CODCLI fixo), escopo por cadastro (404 fora)."""
    try:
        dias = max(7, min(int(request.args.get('dias', 60)), 365))
    except (TypeError, ValueError):
        dias = 60

    key = cache_key_for_user(f'radar:cliente:{codcli}', {'dias': dias})
    cached = _cache_get(key)
    if cached:
        return jsonify(cached)

    cli_meta, linhas = _radar_cliente_rows(codcli, dias)
    if cli_meta is None:
        return jsonify({'ok': False, 'error': 'Cliente fora da sua carteira'}), 404

    resp = {
        'ok': True,
        'codcli': codcli,
        'dias': dias,
        'cliente': {
            'codcli':   codcli,
            'nome':     cli_meta.get('cliente') or f'Cliente #{codcli}',
            'cidade':   cli_meta.get('cidade'),
            'uf':       cli_meta.get('uf'),
            'vendedor': cli_meta.get('vendedor'),
            'telefone': cli_meta.get('telefone'),
        },
        'kpis': {
            'produtos_parados': len(linhas),
            'receita_em_risco': round(sum(c['venda_12m'] for c in linhas), 2),
        },
        'rows': linhas,
    }
    _cache_set(key, resp, 'dax_agregado')
    return jsonify(resp)


@app.route('/api/radar/cliente/<int:codcli>/csv')
@login_required
def api_radar_cliente_csv(codcli):
    """CSV dos produtos que o cliente parou de comprar."""
    from datetime import date as _date
    try:
        dias = max(7, min(int(request.args.get('dias', 60)), 365))
    except (TypeError, ValueError):
        dias = 60

    cli_meta, linhas = _radar_cliente_rows(codcli, dias)
    if cli_meta is None:
        return jsonify({'ok': False, 'error': 'Cliente fora da sua carteira'}), 404

    cabecalho = ['CodProd', 'Produto', 'Departamento', 'Fornecedor', 'UltimaCompra', 'DiasParado',
                 'Situacao', 'Venda12m', 'Qtd12m', 'VendaAnterior', 'VendaRecente']

    def gerar():
        yield CSV_PREAMBULO  # BOM UTF-8
        yield _csv_linha(cabecalho)
        for c in linhas:
            yield _csv_linha([
                c.get('codprod'), c.get('descricao'), c.get('depto_nome'), c.get('fornec_nome'),
                c.get('ultima_compra'), c.get('dias_parado'),
                _RADAR_STATUS_PT.get(c.get('status'), c.get('status')),
                c.get('venda_12m'), c.get('qt_12m'), c.get('venda_ant'), c.get('venda_rec'),
            ])

    nome_cli = _slug_export(cli_meta.get('cliente') or f'cliente-{codcli}')
    nome = f"radar_cliente_{codcli}_{nome_cli}_{dias}d_{_date.today().isoformat()}.csv"
    return Response(
        stream_with_context(gerar()),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': _content_disposition(nome)},
    )


@app.route('/api/radar/cliente/<int:codcli>/pdf')
@login_required
def api_radar_cliente_pdf(codcli):
    """PDF dos produtos que o cliente parou de comprar."""
    from datetime import date as _date
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    from io import BytesIO
    try:
        dias = max(7, min(int(request.args.get('dias', 60)), 365))
    except (TypeError, ValueError):
        dias = 60

    cli_meta, linhas = _radar_cliente_rows(codcli, dias)
    if cli_meta is None:
        return jsonify({'ok': False, 'error': 'Cliente fora da sua carteira'}), 404

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=1.2*cm, rightMargin=1.2*cm, topMargin=1.2*cm, bottomMargin=1.5*cm,
        title=f"Radar Cliente {codcli} {_date.today().isoformat()}",
    )
    styles = getSampleStyleSheet()
    titulo_style = ParagraphStyle('titulo', parent=styles['Heading1'], fontSize=14, alignment=TA_LEFT, textColor=colors.HexColor('#0a0e17'))
    sub_style = ParagraphStyle('sub', parent=styles['Normal'], fontSize=8, textColor=colors.HexColor('#475569'))

    story = []
    cliente = cli_meta.get('cliente') or f'Cliente #{codcli}'
    story.append(Paragraph(f"<b>JOGA Analytics</b> — Produtos parados · {cliente}", titulo_style))
    sub = (f"Gerado em {_date.today().strftime('%d/%m/%Y')} · #{codcli} · "
           f"{(cli_meta.get('cidade') or '')}/{cli_meta.get('uf') or ''} · "
           f"{cli_meta.get('vendedor') or '—'} · {len(linhas)} produto(s) parado(s) há ≥ {dias} dias")
    story.append(Paragraph(sub, sub_style))
    story.append(Spacer(1, 0.3*cm))

    header = ['Produto', 'Departamento', 'Fornecedor', 'Última compra', 'Dias', 'Situação',
              'Comprava (R$)', 'Agora (R$)', 'Venda 12m']
    data = [header]
    for c in linhas:
        data.append([
            (c.get('descricao') or '')[:34],
            (c.get('depto_nome') or '')[:16],
            (c.get('fornec_nome') or '')[:20],
            str(c.get('ultima_compra') or '')[:10],
            c.get('dias_parado') if c.get('dias_parado') is not None else '',
            _RADAR_STATUS_PT.get(c.get('status'), ''),
            f"R$ {(c.get('venda_ant') or 0):,.0f}".replace(',', '.'),
            f"R$ {(c.get('venda_rec') or 0):,.0f}".replace(',', '.'),
            f"R$ {(c.get('venda_12m') or 0):,.0f}".replace(',', '.'),
        ])
    tbl = Table(data, repeatRows=1,
                colWidths=[6*cm, 3*cm, 3.4*cm, 2.1*cm, 1.1*cm, 1.8*cm, 2.2*cm, 2.2*cm, 2.2*cm])
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1e293b')),
        ('TEXTCOLOR',  (0,0), (-1,0), colors.white),
        ('FONTNAME',   (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE',   (0,0), (-1,-1), 6.5),
        ('GRID',       (0,0), (-1,-1), 0.3, colors.HexColor('#cbd5e1')),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f8fafc')]),
        ('ALIGN',      (4,0), (4,-1), 'CENTER'),
        ('ALIGN',      (6,0), (8,-1), 'RIGHT'),
        ('VALIGN',     (0,0), (-1,-1), 'MIDDLE'),
        ('LEFTPADDING',(0,0), (-1,-1), 3),
        ('RIGHTPADDING',(0,0), (-1,-1), 3),
    ]))
    story.append(tbl)

    def _rodape(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(colors.HexColor('#94a3b8'))
        canvas.drawRightString(doc.pagesize[0] - 1.2*cm, 0.8*cm, f"Página {doc.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_rodape, onLaterPages=_rodape)
    nome_cli = _slug_export(cliente)
    nome = f"radar_cliente_{codcli}_{nome_cli}_{dias}d_{_date.today().isoformat()}.pdf"
    return Response(
        buf.getvalue(),
        mimetype='application/pdf',
        headers={'Content-Disposition': _content_disposition(nome)},
    )


def _carregar_cohort_compras_global(periodo_meses=12):
    """Cache 24h GLOBAL: {codcli: [meses 'YYYY-MM']} das compras no período (todos os clientes,
    sem filtro de venda). +12m de histórico pra identificar quem é 'novo'."""
    key = f'multpel:cohort:compras_global:v2:{periodo_meses}'
    cached = _cache_get(key)
    if cached:
        return {int(cc): meses for cc, meses in cached.items()}

    query = f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODCLI],
    CALENDARIO[AnoMes],
    FILTER(FATURAMENTO_VENDAS,
        FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -{periodo_meses + 12}))
)"""
    token = get_token_cached()
    payload = retry_dax(execute_dax)(token, query)
    rows = clean_rows(_todas_linhas(payload))
    compras = {}
    for r in rows:
        cc = r.get('CODCLI')
        am = r.get('AnoMes')
        if cc is None or am is None:
            continue
        am_str = str(am)
        compras.setdefault(int(cc), []).append(f'{am_str[:4]}-{am_str[4:6]}')
    _cache_set(key, compras, 'metadata')  # 24h
    return compras


def _carregar_cohort_full(periodo_meses=12, scope_codclis=None):
    """Cohort {mes_aquisicao: {mes_relativo: set(codcli)}}, recortado por CADASTRO.
    scope_codclis None = todos (admin sem filtro). Usa as compras globais (cache compartilhado)
    e a retenção é sobre as compras (totais) dos clientes em escopo."""
    compras = _carregar_cohort_compras_global(periodo_meses)
    if scope_codclis is not None:
        compras = {cc: m for cc, m in compras.items() if cc in scope_codclis}

    from datetime import date as _date
    hoje = _date.today()
    corte_y = hoje.year - (periodo_meses // 12)
    corte_m = hoje.month - (periodo_meses % 12)
    if corte_m <= 0:
        corte_y -= 1
        corte_m += 12
    corte_str = f'{corte_y:04d}-{corte_m:02d}'

    compras_no_periodo = {cc: meses for cc, meses in compras.items() if min(meses) >= corte_str}
    return cohort.cohort_de_compras(compras_no_periodo)


def _cohort_scope_codclis():
    """Codclis do escopo de CADASTRO do usuário + filtros vendedor/supervisor da UI.
    Retorna None quando admin/viewer sem nenhum filtro (= todos os clientes)."""
    role = session.get('role')
    vend = request.args.get('vendedor')
    sup = request.args.get('supervisor')
    if role in ('admin', 'viewer') and not vend and not sup:
        return None
    args = {'_interno': True, 'limit': 100000, 'offset': 0}
    if vend:
        args['vendedor'] = vend
    if sup:
        args['time'] = sup   # _filtrar_carteira: 'time' = codsupervisor (cadastro)
    filtrados = _filtrar_carteira(_carteira_no_escopo(), args)['rows']
    return {c['codcli'] for c in filtrados if c.get('codcli') is not None}


@app.route('/api/tendencias/cohort')
@login_required
def api_tendencias_cohort():
    """Cohort retention matriz (por CADASTRO). Filtros: periodo, vendedor, supervisor."""
    try:
        periodo = int(request.args.get('periodo', '12m').rstrip('m'))
        periodo = max(3, min(periodo, 24))
    except (ValueError, TypeError):
        periodo = 12

    cohorts_data = _carregar_cohort_full(periodo_meses=periodo, scope_codclis=_cohort_scope_codclis())
    matriz = cohort.matriz_cohort(cohorts_data, meses_max=periodo)
    return jsonify({
        'ok': True,
        'periodo': f'{periodo}m',
        'cohorts': matriz,
    })


@app.route('/api/tendencias/cohort/<aquisicao>/<int:mes_relativo>/clientes')
@login_required
def api_cohort_drill(aquisicao, mes_relativo):
    """Drill: lista clientes do bucket (aquisicao, M+mes_relativo) — por cadastro."""
    try:
        periodo = int(request.args.get('periodo', '12m').rstrip('m'))
        periodo = max(3, min(periodo, 24))
    except (ValueError, TypeError):
        periodo = 12

    cohorts_data = _carregar_cohort_full(periodo_meses=periodo, scope_codclis=_cohort_scope_codclis())
    codclis = cohort.clientes_no_bucket(cohorts_data, aquisicao, mes_relativo)
    # Enriquece via carteira no escopo de cadastro
    carteira = _carteira_no_escopo()
    idx = {c['codcli']: c for c in carteira}
    rows = []
    for cc in codclis[:200]:  # limita a 200 pra resposta
        cli = idx.get(cc, {})
        rows.append({
            'codcli':       cc,
            'cliente':      cli.get('cliente') or f'Cliente #{cc}',
            'cidade':       cli.get('cidade'),
            'uf':           cli.get('uf'),
            'vendedor':     cli.get('vendedor'),
            'lucro_12m':    cli.get('lucro_12m'),
            'segmento':     cli.get('segmento'),
        })
    return jsonify({
        'ok': True,
        'aquisicao':     aquisicao,
        'mes_relativo':  mes_relativo,
        'total':         len(codclis),
        'rows':          rows,
    })


# ──────────────────────────────────────────────────────────────────────
# Admin — CRUD multpel_users + envio manual de relatório por email
# Tela /admin (admin only) + 4 endpoints REST. Ativa o link Admin que
# já existia no topbar mas estava quebrado (sem rota).
# ──────────────────────────────────────────────────────────────────────

@app.route('/admin')
@admin_required
def admin_page():
    return send_from_directory('.', 'admin.html')


@app.route('/api/admin/users', methods=['GET'])
@admin_required
def api_admin_users_list():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, nome, email, role, codusur, codsupervisor, telefone, ativo, "
        "cron_enabled, cron_horario::text, cron_frequencia, criado_em, email_cc, segmentos_rfm, codsupervisores, "
        "email_proximo_pedido, email_alerta_cobertura, areas, area_padrao, codcomprador, relatorios_estoque, "
        "bloqueado_ate "
        "FROM multpel_users ORDER BY ativo DESC, nome"
    )
    users = []
    for r in cur.fetchall():
        # Multi-área: lista normalizada (legado single → [single])
        sups = _como_lista_supervisores(r[14]) or _como_lista_supervisores(r[5])
        users.append({
            'id': r[0], 'nome': r[1], 'email': r[2], 'role': r[3],
            'codusur': r[4], 'codsupervisor': r[5], 'telefone': r[6], 'ativo': r[7],
            'cron_enabled': r[8], 'cron_horario': r[9], 'cron_frequencia': r[10],
            'criado_em': str(r[11])[:10] if r[11] else None,
            'email_cc': r[12] if r[12] is not None else [],
            'segmentos_rfm': r[13] or '',
            'codsupervisores': sups,
            'email_proximo_pedido': bool(r[15]),
            'email_alerta_cobertura': bool(r[16]),
            'areas': normalizar_areas(r[17]),
            'area_padrao': r[18] or 'portal',
            'codcomprador': r[19],
            'relatorios_estoque': r[20] if r[20] is not None else [],
            'bloqueado': _login_bloqueio_restante(r[21]) > 0,
        })
    cur.close()
    conn.close()
    return jsonify({'ok': True, 'users': users})


@app.route('/api/admin/users', methods=['POST'])
@admin_required
def api_admin_users_create():
    data = request.get_json() or {}
    nome = (data.get('nome') or '').strip()
    email = (data.get('email') or '').strip().lower()
    role = data.get('role') or 'viewer'
    if not nome or not email:
        return jsonify({'ok': False, 'error': 'Nome e email obrigatórios'}), 400
    if role not in ('admin', 'supervisor', 'vendedor', 'viewer'):
        return jsonify({'ok': False, 'error': 'Role inválida'}), 400

    codusur = data.get('codusur') or None
    # Supervisor multi-área: aceita codsupervisores (lista) ou codsupervisor (single legado)
    codsupervisores = _normalizar_codsupervisores(
        data.get('codsupervisores') if data.get('codsupervisores') is not None else data.get('codsupervisor')
    )
    codsupervisor = codsupervisores[0] if codsupervisores else None
    telefone = (data.get('telefone') or '').strip() or None
    cron_enabled = bool(data.get('cron_enabled', False))
    cron_horario = data.get('cron_horario') or '08:00'
    cron_frequencia = data.get('cron_frequencia') or 'diaria'
    email_proximo_pedido = bool(data.get('email_proximo_pedido', False))
    email_alerta_cobertura = bool(data.get('email_alerta_cobertura', False))

    # Patch J — destinatários CC (lista de emails extras)
    try:
        emails_cc = _normalizar_emails_cc(data.get('email_cc') or [], email_principal=email)
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400

    # Patch K — filtro de segmento RFM (vazio = carteira completa)
    segmentos_rfm = _normalizar_segmentos_rfm(data.get('segmentos_rfm'))

    if role == 'vendedor' and not codusur:
        return jsonify({'ok': False, 'error': 'Vendedor exige codusur'}), 400
    if role == 'supervisor' and not codsupervisores:
        return jsonify({'ok': False, 'error': 'Supervisor exige ao menos uma área (codsupervisor)'}), 400

    # ── Fusão: acesso por área + vínculo/relatórios do módulo Compras ──
    areas = normalizar_areas(data.get('areas'))
    codcomprador = data.get('codcomprador') or None
    relatorios_estoque = _normalizar_relatorios_estoque(data.get('relatorios_estoque'))
    erro_area = _validar_area_compras(areas, relatorios_estoque)
    if erro_area:
        return jsonify({'ok': False, 'error': erro_area}), 400

    senha = (data.get('senha') or '').strip() or secrets.token_urlsafe(10)

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO multpel_users
               (nome, email, password_hash, role, codusur, codsupervisor, codsupervisores, telefone,
                cron_enabled, cron_horario, cron_frequencia, email_cc, segmentos_rfm,
                email_proximo_pedido, email_alerta_cobertura, must_change_password,
                areas, codcomprador, relatorios_estoque)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true,
                       %s, %s, %s)
               RETURNING id""",
            (nome, email, generate_password_hash(senha), role,
             int(codusur) if codusur else None,
             codsupervisor, Json(codsupervisores),
             telefone, cron_enabled, cron_horario, cron_frequencia,
             Json(emails_cc), segmentos_rfm, email_proximo_pedido, email_alerta_cobertura,
             Json(areas), int(codcomprador) if codcomprador else None, Json(relatorios_estoque))
        )
        novo_id = cur.fetchone()[0]
        conn.commit()
    except psycopg2.IntegrityError as e:
        conn.rollback()
        return jsonify({'ok': False, 'error': 'Email já cadastrado'}), 400
    finally:
        cur.close()
        conn.close()

    return jsonify({'ok': True, 'id': novo_id, 'senha_gerada': senha})


@app.route('/api/admin/users/<int:user_id>', methods=['PUT'])
@admin_required
def api_admin_users_update(user_id):
    data = request.get_json() or {}
    campos_permitidos = ['nome', 'email', 'role', 'codusur', 'codsupervisor', 'telefone',
                         'ativo', 'cron_enabled', 'cron_horario', 'cron_frequencia',
                         'email_cc', 'segmentos_rfm', 'email_proximo_pedido', 'email_alerta_cobertura',
                         'area_padrao', 'codcomprador']
    # Pra normalizar email_cc precisa do email principal do user
    email_principal_atual = (data.get('email') or '').strip().lower() or None
    if 'email_cc' in data and not email_principal_atual:
        conn0 = get_db(); c0 = conn0.cursor()
        c0.execute("SELECT email FROM multpel_users WHERE id = %s", (user_id,))
        r0 = c0.fetchone()
        c0.close(); conn0.close()
        if r0:
            email_principal_atual = r0[0]
    sets, valores = [], []
    for k in campos_permitidos:
        if k in data:
            # codsupervisor é tratado em bloco separado (vira lista codsupervisores) quando
            # o payload traz codsupervisores — evita atribuir a mesma coluna duas vezes.
            if k == 'codsupervisor' and 'codsupervisores' in data:
                continue
            v = data[k]
            if k == 'email' and v:
                v = str(v).strip().lower()
            elif k in ('codusur', 'codsupervisor') and v:
                v = int(v)
            elif k == 'codusur' or k == 'codsupervisor':
                v = None
            elif k == 'email_cc':
                try:
                    v = Json(_normalizar_emails_cc(v or [], email_principal=email_principal_atual))
                except ValueError as e:
                    return jsonify({'ok': False, 'error': str(e)}), 400
            elif k == 'segmentos_rfm':
                v = _normalizar_segmentos_rfm(v)
            elif k == 'codcomprador':
                v = int(v) if v else None
            elif k == 'area_padrao':
                v = v if v in ('portal',) + AREAS_VALIDAS else 'portal'
            sets.append(f"{k} = %s")
            valores.append(v)
    # Supervisor multi-área: codsupervisores (lista) grava nas 2 colunas (lista + 1º elemento)
    if 'codsupervisores' in data:
        sups = _normalizar_codsupervisores(data.get('codsupervisores'))
        if data.get('role') == 'supervisor' and not sups:
            return jsonify({'ok': False, 'error': 'Supervisor exige ao menos uma área (codsupervisor)'}), 400
        sets.append("codsupervisores = %s"); valores.append(Json(sups))
        sets.append("codsupervisor = %s");   valores.append(sups[0] if sups else None)
    # ── Fusão: áreas e relatórios de Compras (JSONB, como codsupervisores) ──
    # Validação cruzada contra o estado FINAL: quem manda só um dos dois campos poderia deixar
    # o usuário com relatórios de Compras sem ter a área. Lê o que falta do banco antes de decidir.
    if 'areas' in data or 'relatorios_estoque' in data:
        atuais = {}
        if 'areas' not in data or 'relatorios_estoque' not in data:
            conn1 = get_db(); c1 = conn1.cursor()
            c1.execute("SELECT areas, relatorios_estoque FROM multpel_users WHERE id = %s", (user_id,))
            r1 = c1.fetchone()
            c1.close(); conn1.close()
            if r1:
                atuais = {'areas': r1[0], 'relatorios_estoque': r1[1]}
        areas_final = (normalizar_areas(data['areas']) if 'areas' in data
                       else normalizar_areas(atuais.get('areas')))
        rels_final = (_normalizar_relatorios_estoque(data['relatorios_estoque'])
                      if 'relatorios_estoque' in data
                      else _normalizar_relatorios_estoque(atuais.get('relatorios_estoque')))
        erro_area = _validar_area_compras(areas_final, rels_final)
        if erro_area:
            return jsonify({'ok': False, 'error': erro_area}), 400
        if 'areas' in data:
            sets.append("areas = %s"); valores.append(Json(areas_final))
        if 'relatorios_estoque' in data:
            sets.append("relatorios_estoque = %s"); valores.append(Json(rels_final))
    if 'senha' in data and data['senha']:
        sets.append("password_hash = %s")
        valores.append(generate_password_hash(data['senha']))
        sets.append("must_change_password = true")
    if not sets:
        return jsonify({'ok': False, 'error': 'Nada pra atualizar'}), 400

    valores.append(user_id)
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(f"UPDATE multpel_users SET {', '.join(sets)} WHERE id = %s", valores)
        conn.commit()
    except psycopg2.IntegrityError:
        conn.rollback()
        return jsonify({'ok': False, 'error': 'Conflito (email duplicado?)'}), 400
    finally:
        cur.close()
        conn.close()
    return jsonify({'ok': True})


@app.route('/api/admin/users/<int:user_id>', methods=['DELETE'])
@admin_required
def api_admin_users_delete(user_id):
    if user_id == session.get('user_id'):
        return jsonify({'ok': False, 'error': 'Não pode desativar a si próprio'}), 400
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE multpel_users SET ativo = false WHERE id = %s", (user_id,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'ok': True})


@app.route('/api/admin/users/<int:user_id>/desbloquear', methods=['POST'])
@admin_required
def api_admin_desbloquear(user_id):
    """Libera na hora uma conta bloqueada por tentativas. O bloqueio expira sozinho, mas quem
    errou a senha e precisa entrar agora não deve ficar esperando."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE multpel_users SET bloqueado_ate = NULL, tentativas_falhas = 0, "
        "bloqueios_seguidos = 0 WHERE id = %s RETURNING email", (user_id,)
    )
    linha = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    if not linha:
        return jsonify({'ok': False, 'error': 'Usuário não encontrado'}), 404
    # Zera também o contador por IP de quem está pedindo, senão o admin destrava a conta e o
    # usuário continua barrado pela outra camada.
    try:
        _R_LOGIN.delete(f'multpel:login:ip:{_ip_do_request()}')
    except Exception:      # noqa: BLE001
        pass
    _log_login(user_id, linha[0], _ip_do_request(), 'desbloqueado_pelo_admin')
    return jsonify({'ok': True})


@app.route('/api/admin/enviar-relatorio/<int:user_id>', methods=['POST'])
@admin_required
def api_admin_enviar_relatorio(user_id):
    """Disparo manual. `?tipo=compras` envia os relatórios do módulo Compras; sem parâmetro,
    mantém o comportamento histórico (relatório comercial de carteira)."""
    if request.args.get('tipo') == 'compras':
        resultado = enviar_relatorios_estoque_email(user_id)
    else:
        resultado = enviar_relatorio_email(user_id)
    status = 200 if resultado.get('ok') else 500
    return jsonify(resultado), status


# ── Error handler ──
@app.errorhandler(500)
def erro_500(e):
    tb = traceback.format_exc()
    log_request(request.path, parametros=dict(request.args), erro=tb[:2000])
    if '/api/' in request.path:
        return jsonify({'ok': False, 'error': 'Erro interno'}), 500
    return Response('Erro interno do servidor', status=500)


# ══════════════════════════════════════════════════════════════════════
# MÓDULO METAS — replica as 4 telas META do cliente (Venda/Rentab/Clientes/Mix).
# Meta (alvo) é NOSSA (Postgres multpel_metas, por vendedor). Realizado vem do dataset
# META (pedidos PCPEDC/PCPEDI) + calendário EhDiaMeta. Regras calibradas centavo-a-centavo
# contra as medidas oficiais (ver memória multpel-metas-bi). Projeção/falta em metas.py.
# ══════════════════════════════════════════════════════════════════════
import metas  # módulo puro de metas (projeção, necessidade/dia, sugestão)

META_METRICAS = ('venda', 'clientes', 'mix', 'rentabilidade')


def _metas_escopo_codusur(supervisores):
    """Resolve o escopo do usuário logado para um conjunto de CODUSUR (ou None = tudo).
    - vendedor: só o próprio codusur
    - supervisor (multi-área): vendedores das suas áreas (via vendedores_map)
    - admin/viewer: None (tudo) ou, se ?supervisor=, os vendedores desses supervisores
    Uniformizar em CODUSUR garante consistência entre medidas (por vendedor) e distinct."""
    if session.get('codusur'):
        return {int(session['codusur'])}
    sups = _session_supervisores()
    alvo = None
    if sups:
        alvo = set(sups)
    elif supervisores:
        alvo = set(int(s) for s in supervisores)
    if alvo is None:
        return None  # admin/viewer sem filtro → tudo
    vmap = _carregar_vendedores_map()
    return {int(cu) for cu, info in vmap.items() if info.get('codsupervisor') in alvo}


def _metas_rbac_frag(tabela, escopo):
    """Fragmento DAX RBAC por CODUSUR. escopo=None → '' (tudo); escopo vazio → impossível."""
    if escopo is None:
        return ""
    if not escopo:
        return f"{tabela}[CODUSUR] IN {{-1}}"
    ids = ", ".join(str(c) for c in sorted(escopo))
    return f"{tabela}[CODUSUR] IN {{{ids}}}"


def _and_dax(*frags):
    return " && ".join(f for f in frags if f)


def _carregar_dias_uteis_meta(ano, mes):
    """Dias de meta (úteis) do CALENDARIO do dataset META: total/decorridos/restantes.
    EhDiaMeta=1 exclui fins de semana e feriados. Funciona pra qualquer mês."""
    key = f'multpel:metas:dias:{ano}-{mes}'
    cached = _cache_get(key)
    if cached:
        return cached
    base = f"CALENDARIO[Ano]={int(ano)} && CALENDARIO[MES]={int(mes)} && CALENDARIO[EhDiaMeta]=1"
    q = (f'EVALUATE ROW('
         f'"mes", CALCULATE(COUNTROWS(CALENDARIO), FILTER(CALENDARIO, {base})), '
         f'"decorridos", CALCULATE(COUNTROWS(CALENDARIO), FILTER(CALENDARIO, {base} && CALENDARIO[DATA]<=TODAY())), '
         f'"restantes", CALCULATE(COUNTROWS(CALENDARIO), FILTER(CALENDARIO, {base} && CALENDARIO[DATA]>TODAY())))')
    row = _primeira_linha(run_dax_meta(q))
    dias = {
        'mes':        int(row.get('[mes]') or 0),
        'decorridos': int(row.get('[decorridos]') or 0),
        'restantes':  int(row.get('[restantes]') or 0),
    }
    _cache_set(key, dias, 'dax_lista')
    return dias


def _dias_uteis_mes_fechado(ano, mes):
    """Dias úteis (seg-sex) de um mês já encerrado. O CALENDARIO/EhDiaMeta do dataset META
    só existe pro mês corrente; pra mês fechado projeção=realizado, então dias é cosmético.
    decorridos=total, restantes=0."""
    import calendar
    from datetime import date as _d
    n = sum(1 for d in range(1, calendar.monthrange(ano, mes)[1] + 1) if _d(ano, mes, d).weekday() < 5)
    return {'mes': n, 'decorridos': n, 'restantes': 0}


def _realizado_rca_mes(ano, mes, escopo):
    """Realizado de um mês FECHADO via dataset RCA (faturamento, histórico completo) — o
    dataset META só guarda o mês corrente. Aproximação: faturamento ≈ pedidos faturados.
    Mesma estrutura de _carregar_metas_realizado (por supervisor/vendedor/total)."""
    fp = f"MONTH(FATURAMENTO_VENDAS[DTSAIDA])={int(mes)} && YEAR(FATURAMENTO_VENDAS[DTSAIDA])={int(ano)}"
    rb = aplicar_rbac_dax()
    frag = ""
    if escopo is not None:
        ids = ", ".join(str(int(c)) for c in sorted(escopo)) if escopo else "-1"
        frag = f"FATURAMENTO_VENDAS[CODUSUR] IN {{{ids}}}"
    filtro = f"FILTER(FATURAMENTO_VENDAS, {_and_dax(fp, rb, frag)})"
    cols = ('"venda", [VENDA LIQUIDA], "rentab", [LUCRO TOTAL], '
            '"cli", DISTINCTCOUNT(FATURAMENTO_VENDAS[CODCLI]), '
            '"mix", DISTINCTCOUNT(FATURAMENTO_VENDAS[CODPROD])')
    queries = {
        'por_sup':  f'EVALUATE SUMMARIZECOLUMNS(FATURAMENTO_VENDAS[CODSUPERVISOR], {filtro}, {cols})',
        'por_usur': f'EVALUATE SUMMARIZECOLUMNS(FATURAMENTO_VENDAS[CODUSUR], {filtro}, {cols})',
        'total':    (f'EVALUATE ROW("venda", CALCULATE([VENDA LIQUIDA], {filtro}), '
                     f'"rentab", CALCULATE([LUCRO TOTAL], {filtro}), '
                     f'"cli", CALCULATE(DISTINCTCOUNT(FATURAMENTO_VENDAS[CODCLI]), {filtro}), '
                     f'"mix", CALCULATE(DISTINCTCOUNT(FATURAMENTO_VENDAS[CODPROD]), {filtro}))'),
    }
    res = executar_dax_paralelo(queries)  # dataset RCA (default)

    def _mapa(nome, chave):
        out = {}
        for r in clean_rows(_todas_linhas(res[nome])):
            cod = r.get(chave)
            if cod is None:
                continue
            v = r.get('venda') or 0
            out[str(int(cod))] = {'venda': v, 'rentabilidade': r.get('rentab') or 0,
                                  'clientes': r.get('cli') or 0, 'mix': r.get('mix') or 0,
                                  'proj_venda': v}  # mês fechado: projeção = realizado
        return out

    tot = _primeira_linha(res['total'])
    tv = tot.get('[venda]') or 0
    return {
        'dias': _dias_uteis_mes_fechado(int(ano), int(mes)),
        'por_supervisor': _mapa('por_sup', 'CODSUPERVISOR'),
        'por_vendedor':   _mapa('por_usur', 'CODUSUR'),
        'total': {'venda': tv, 'rentabilidade': tot.get('[rentab]') or 0,
                  'clientes': tot.get('[cli]') or 0, 'mix': tot.get('[mix]') or 0, 'proj_venda': tv},
    }


def _carregar_metas_realizado(ano, mes, supervisores):
    """Realizado das 4 métricas, recortado pelo escopo do usuário.
    Mês corrente → medidas oficiais do dataset META (pedidos) + DISTINCTCOUNT, exato.
    Mês FECHADO → dataset RCA (faturamento), pois o META só guarda o mês corrente.
    Retorna dict com mapas por vendedor (aditivo) e distinct por supervisor + total."""
    from datetime import date as _date
    ano, mes = int(ano), int(mes)
    escopo = _metas_escopo_codusur(supervisores)
    hoje = _date.today()
    corrente = (ano == hoje.year and mes == hoje.month)
    # Mês corrente: amarra o cache ao refresh do dataset META (~2h). Quando o BI atualiza, a tag
    # muda → chave nova → realizado/projeção voltam a bater com o BI na hora (sem lag de TTL).
    # Antes, o TTL de 1h desalinhava do ciclo de 2h e servia número de refresh anterior. Mês
    # fechado não muda → tag estática.
    rf = _meta_refresh_tag() if corrente else 'fechado'
    key = cache_key_for_user('metas:realizado', {'ano': ano, 'mes': mes,
                                                  'sup': _sup_cache_key(supervisores), 'rf': rf})
    cached = _cache_get(key)
    if cached:
        return cached

    # Mês FECHADO: dataset META esvazia PCPEDC/CALENDARIO ao virar o mês → realizado vem do RCA.
    if not corrente:
        out = _realizado_rca_mes(ano, mes, escopo)
        _cache_set(key, out, 'dax_agregado')
        return out

    rb_pedc = _metas_rbac_frag('PCPEDC', escopo)   # 'PCPEDC[CODUSUR] IN {...}' ou ''
    rb_pedi = _metas_rbac_frag('PCPEDI', escopo)
    fp_pedc = f"MONTH(PCPEDC[DATA])={mes} && YEAR(PCPEDC[DATA])={ano}"
    fp_pedi = f"MONTH(PCPEDI[DATA])={mes} && YEAR(PCPEDI[DATA])={ano}"
    pos = 'PCPEDC[POSICAO] IN {"F","L","B"}'  # pedidos válidos (clientes)

    f_cli = _and_dax(fp_pedc, pos, rb_pedc)   # clientes distinct
    f_mix = _and_dax(fp_pedi, rb_pedi)        # mix distinct
    # Filtro de escopo p/ as MEDIDAS oficiais (restringe os pedidos do usuário). Vazio = admin.
    filtro_med = f"FILTER(PCPEDC, {rb_pedc}), " if rb_pedc else ""

    # Expressões de venda/rentabilidade. Mês corrente → medidas oficiais (exato).
    # Mês fechado → reconstrução por VLATEND(F,L) (o BI mostra 0 pra passado; é ganho).
    if corrente:
        # REALIZADO de venda = BRUTO (com bônus) = [Tem Pedido] = [Realizado Sem Bonus]+[Venda Bonus].
        # É o que o BI mostra em "($) REALIZADO" (provado: [Projecao] = bruto × DiasMes/Decorridos,
        # então projeção÷realizado do BI = 23/12 exato). O app antes mostrava [Realizado Sem Bonus]
        # → ficava ~R$27k menor que o BI. `venda_sb` (sem bônus) é carregado só p/ o denominador da
        # margem, que foi calibrada como lucro÷receita-sem-bônus — não muda com esta troca.
        venda_expr    = '[Tem Pedido]'
        venda_sb_expr = '[Realizado Sem Bonus]'
        rentab_expr = '[MARGEM META(%)]'
        proj_expr   = '[Projecao]'  # projeção oficial (bruta×dias) — bate com o BI
        venda_tot  = (f'CALCULATE([Tem Pedido], FILTER(PCPEDC, {rb_pedc}))' if rb_pedc
                      else '[Tem Pedido]')
        venda_sb_tot = (f'CALCULATE([Realizado Sem Bonus], FILTER(PCPEDC, {rb_pedc}))' if rb_pedc
                        else '[Realizado Sem Bonus]')
        rentab_tot = (f'CALCULATE([MARGEM META(%)], FILTER(PCPEDC, {rb_pedc}))' if rb_pedc
                      else '[MARGEM META(%)]')
        proj_tot   = (f'CALCULATE([Projecao], FILTER(PCPEDC, {rb_pedc}))' if rb_pedc else '[Projecao]')
    else:
        f_fl = _and_dax(fp_pedc, 'PCPEDC[POSICAO] IN {"F","L"}', rb_pedc)
        venda_expr  = f'CALCULATE(SUM(PCPEDC[VLATEND]), FILTER(PCPEDC, {f_fl}))'
        venda_sb_expr = venda_expr  # mês fechado: sem bônus == bruto (VLATEND não separa bônus)
        rentab_expr = f'CALCULATE(SUM(PCPEDC[VLATEND]) - SUM(PCPEDC[VLCUSTOFIN]), FILTER(PCPEDC, {f_fl}))'
        proj_expr   = 'BLANK()'  # mês fechado → projeção recalculada em Python (run-rate)
        venda_tot, rentab_tot, proj_tot = venda_expr, rentab_expr, 'BLANK()'
        venda_sb_tot = venda_tot

    # cli/mix distinct: medidas dão DISTINCTCOUNT direto; rentab/venda via medidas/recon.
    cli_expr = f'CALCULATE(DISTINCTCOUNT(PCPEDC[CODCLI]), FILTER(PCPEDC, {f_cli}))'
    mix_expr = f'CALCULATE(DISTINCTCOUNT(PCPEDI[CODPROD]), FILTER(PCPEDI, {f_mix}))'

    queries = {
        # tudo por supervisor (transação), num só passe — bate com o BI
        'por_sup': (f'EVALUATE SUMMARIZECOLUMNS(PCSUPERV[CODSUPERVISOR], PCSUPERV[NOME], {filtro_med}'
                    f'"venda", {venda_expr}, "venda_sb", {venda_sb_expr}, "rentab", {rentab_expr}, "proj", {proj_expr}, '
                    f'"cli", {cli_expr}, "mix", {mix_expr})'),
        # totais escalares (distinct verdadeiro + medidas)
        'totais': (f'EVALUATE ROW("venda", {venda_tot}, "venda_sb", {venda_sb_tot}, "rentab", {rentab_tot}, "proj", {proj_tot}, '
                   f'"cli", {cli_expr}, "mix", {mix_expr})'),
        # drill por vendedor
        'vr_usur':  (f'EVALUATE SUMMARIZECOLUMNS(PCUSUARI[CODUSUR], {filtro_med}'
                     f'"venda", {venda_expr}, "venda_sb", {venda_sb_expr}, "rentab", {rentab_expr}, "proj", {proj_expr})') if corrente else
                    (f'EVALUATE SUMMARIZECOLUMNS(PCPEDC[CODUSUR], '
                     f'"venda", {venda_expr}, "venda_sb", {venda_sb_expr}, "rentab", {rentab_expr})'),
        'cli_usur': f'EVALUATE SUMMARIZECOLUMNS(PCPEDC[CODUSUR], "v", {cli_expr})',
        'mix_usur': f'EVALUATE SUMMARIZECOLUMNS(PCPEDI[CODUSUR], "v", {mix_expr})',
    }

    res = run_dax_meta_paralelo(queries)

    def _mapa(nome, chave_grupo):
        out = {}
        for r in clean_rows(_todas_linhas(res[nome])):
            cod = r.get(chave_grupo)
            if cod is None:
                continue
            out[str(int(cod))] = r
        return out

    # por supervisor (realizado das 4 métricas). 'proj_venda' = projeção oficial da venda (mês corrente).
    por_supervisor = {}
    for cod, r in _mapa('por_sup', 'CODSUPERVISOR').items():
        por_supervisor[cod] = {
            # DEMO: não cacheia nome real aqui; None faz o consumidor resolver pelo mapa mascarado.
            'nome': None if TIME_DEMO else (r.get('NOME') or f'Time {cod}'),
            'venda': r.get('venda') or 0, 'venda_sb': r.get('venda_sb') or 0,
            'rentabilidade': r.get('rentab') or 0,
            'clientes': r.get('cli') or 0, 'mix': r.get('mix') or 0,
            'proj_venda': r.get('proj'),
        }

    # por vendedor (drill)
    por_vendedor = {}
    for cod, r in _mapa('vr_usur', 'CODUSUR').items():
        por_vendedor[cod] = {'venda': r.get('venda') or 0, 'venda_sb': r.get('venda_sb') or 0,
                             'rentabilidade': r.get('rentab') or 0,
                             'clientes': 0, 'mix': 0, 'proj_venda': r.get('proj')}
    for cod, r in _mapa('cli_usur', 'CODUSUR').items():
        por_vendedor.setdefault(cod, {'venda': 0, 'rentabilidade': 0, 'clientes': 0, 'mix': 0})
        por_vendedor[cod]['clientes'] = r.get('v') or 0
    for cod, r in _mapa('mix_usur', 'CODUSUR').items():
        por_vendedor.setdefault(cod, {'venda': 0, 'rentabilidade': 0, 'clientes': 0, 'mix': 0})
        por_vendedor[cod]['mix'] = r.get('v') or 0

    tot = _primeira_linha(res['totais'])
    out = {
        'dias': _carregar_dias_uteis_meta(ano, mes),
        'por_supervisor': por_supervisor,
        'por_vendedor': por_vendedor,
        'total': {
            'venda': tot.get('[venda]') or 0, 'venda_sb': tot.get('[venda_sb]') or 0,
            'rentabilidade': tot.get('[rentab]') or 0,
            'clientes': tot.get('[cli]') or 0, 'mix': tot.get('[mix]') or 0,
            'proj_venda': tot.get('[proj]'),
        },
    }
    _cache_set(key, out, 'dax_agregado')
    return out


# ── Persistência da meta (Postgres) ──
def _metas_buscar(ano, mes):
    """Retorna {codusur_str: {valor_meta, clientes_meta, mix_meta, rentabilidade_meta}} do mês."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT codusur, valor_meta, clientes_meta, mix_meta, rentabilidade_meta "
        "FROM multpel_metas WHERE ano = %s AND mes = %s",
        (int(ano), int(mes))
    )
    out = {}
    for r in cur.fetchall():
        out[str(int(r[0]))] = {
            'valor_meta':         float(r[1] or 0),
            'clientes_meta':      int(r[2] or 0),
            'mix_meta':           int(r[3] or 0),
            'rentabilidade_meta': float(r[4] or 0),
        }
    cur.close()
    conn.close()
    return out


def _metas_upsert(ano, mes, codusur, valores, user_id=None):
    """Insere/atualiza a meta de um vendedor no mês (ON CONFLICT)."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO multpel_metas
             (ano, mes, codusur, valor_meta, clientes_meta, mix_meta, rentabilidade_meta, atualizado_em, atualizado_por)
           VALUES (%s,%s,%s,%s,%s,%s,%s, NOW(), %s)
           ON CONFLICT (ano, mes, codusur) DO UPDATE SET
             valor_meta = EXCLUDED.valor_meta,
             clientes_meta = EXCLUDED.clientes_meta,
             mix_meta = EXCLUDED.mix_meta,
             rentabilidade_meta = EXCLUDED.rentabilidade_meta,
             atualizado_em = NOW(), atualizado_por = EXCLUDED.atualizado_por""",
        (int(ano), int(mes), int(codusur),
         float(valores.get('valor_meta') or 0), int(valores.get('clientes_meta') or 0),
         int(valores.get('mix_meta') or 0), float(valores.get('rentabilidade_meta') or 0), user_id)
    )
    conn.commit()
    cur.close()
    conn.close()


def _metas_upsert_lote(ano, mes, itens, user_id=None):
    """Upsert de várias metas numa única transação (botão 'Salvar metas' do editor).
    itens: lista de dicts {codusur, valor_meta, clientes_meta, mix_meta, rentabilidade_meta}.
    Retorna a quantidade gravada. Ou tudo, ou nada (rollback em erro)."""
    n = 0
    conn = get_db()
    cur = conn.cursor()
    try:
        for it in itens:
            cur.execute(
                """INSERT INTO multpel_metas
                     (ano, mes, codusur, valor_meta, clientes_meta, mix_meta, rentabilidade_meta, atualizado_em, atualizado_por)
                   VALUES (%s,%s,%s,%s,%s,%s,%s, NOW(), %s)
                   ON CONFLICT (ano, mes, codusur) DO UPDATE SET
                     valor_meta = EXCLUDED.valor_meta,
                     clientes_meta = EXCLUDED.clientes_meta,
                     mix_meta = EXCLUDED.mix_meta,
                     rentabilidade_meta = EXCLUDED.rentabilidade_meta,
                     atualizado_em = NOW(), atualizado_por = EXCLUDED.atualizado_por""",
                (int(ano), int(mes), int(it['codusur']),
                 float(it.get('valor_meta') or 0), int(it.get('clientes_meta') or 0),
                 int(it.get('mix_meta') or 0), float(it.get('rentabilidade_meta') or 0), user_id)
            )
            n += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()
    return n


def _montar_metas_resposta(ano, mes, supervisores):
    """Costura meta (Postgres) + realizado (dataset) → linhas por supervisor + total, 4 métricas.
    Realizado por supervisor vem medido por transação (DAX, bate com o BI). A meta (por vendedor)
    rola ao supervisor pelo CADASTRO (vendedores_map). Total realizado = escalar distinto."""
    metas_db = _metas_buscar(ano, mes)
    vmap = _carregar_vendedores_map()

    # Universo de meta: supervisores com >=1 vendedor com meta cadastrada. Alinha com o BI —
    # times sem meta (ex.: DIRETORIA, E COMMERCE MARTINS) NÃO entram no painel nem no total.
    # Sem nenhuma meta no mês → fallback mostra tudo (não esconde o realizado).
    sups_com_meta = set()
    for cu, info in vmap.items():
        cs = info.get('codsupervisor')
        if cs is None:
            continue
        m = metas_db.get(cu, {})
        if any((m.get(k) or 0) for k in ('valor_meta', 'clientes_meta', 'mix_meta', 'rentabilidade_meta')):
            sups_com_meta.add(int(cs))

    # Escopa o realizado ao universo de meta (intersecta com ?supervisor do admin/viewer).
    # Escopar via DAX faz per-supervisor, totais E os DISTINCT (clientes/mix) baterem com o BI.
    if supervisores:
        sups_efetivos = sorted(set(int(s) for s in supervisores) & sups_com_meta)
    else:
        sups_efetivos = sorted(sups_com_meta)
    escopo_arg = sups_efetivos if sups_efetivos else supervisores
    realizado = _carregar_metas_realizado(ano, mes, escopo_arg)
    escopo = _metas_escopo_codusur(escopo_arg)
    dias = realizado['dias']
    dm, dd, dr = dias['mes'], dias['decorridos'], dias['restantes']

    def _no_escopo(codusur):
        return escopo is None or int(codusur) in escopo

    # Meta somada por supervisor (cadastro do vendedor), só os no escopo
    meta_sup = {}  # codsup_str → {'valor','rentab','cli','mix'}
    for cu, info in vmap.items():
        if not _no_escopo(cu) or info.get('codsupervisor') is None:
            continue
        cs = str(int(info['codsupervisor']))
        m = metas_db.get(cu, {})
        d = meta_sup.setdefault(cs, {'valor': 0, 'rentab': 0, 'cli': 0, 'mix': 0})
        d['valor']  += m.get('valor_meta') or 0
        d['rentab'] += m.get('rentabilidade_meta') or 0
        d['cli']    += m.get('clientes_meta') or 0
        d['mix']    += m.get('mix_meta') or 0

    rsup = realizado['por_supervisor']
    codsups = set(rsup) | set(meta_sup)
    if sups_com_meta:  # restringe ao universo de meta (descarta times sem meta, alinha BI)
        codsups = {cs for cs in codsups if int(cs) in sups_com_meta}
    supervisores_out = []
    for cs in codsups:
        rz = rsup.get(cs, {'venda': 0, 'rentabilidade': 0, 'clientes': 0, 'mix': 0})
        mt = meta_sup.get(cs, {'valor': 0, 'rentab': 0, 'cli': 0, 'mix': 0})
        # esconde supervisor totalmente vazio (sem meta e sem realizado)
        if (mt['valor'] == 0 and mt['cli'] == 0 and mt['mix'] == 0 and mt['rentab'] == 0
                and rz['venda'] == 0 and rz['clientes'] == 0 and rz['mix'] == 0):
            continue
        nome = rz.get('nome') or (_carregar_supervisores_map().get(cs) or {}).get('nome') or f'Time {cs}'
        supervisores_out.append({
            'codsupervisor': int(cs), 'nome': nome,
            'venda':         metas.linha_metrica(mt['valor'],  rz['venda'],         dm, dd, dr, rz.get('proj_venda')),
            'rentabilidade': metas.linha_metrica(mt['rentab'], rz['rentabilidade'], dm, dd, dr),
            'clientes':      metas.linha_metrica(mt['cli'],    rz['clientes'],      dm, dd, dr),
            'mix':           metas.linha_metrica(mt['mix'],    rz['mix'],           dm, dd, dr),
            # Margem = lucro realizado / receita realizada SEM bônus (calibrada assim vs BI; usa
            # venda_sb, não o realizado bruto exibido, p/ não mudar o % já validado).
            'margem':        metas.pct(rz['rentabilidade'], rz.get('venda_sb') or rz['venda']),
        })
    supervisores_out.sort(key=lambda s: s['venda']['realizado'], reverse=True)

    # Totais: realizado = escalar distinto/medida; meta = soma de todas as metas no escopo
    rt = realizado['total']
    meta_valor_t  = sum(d['valor']  for d in meta_sup.values())
    meta_rentab_t = sum(d['rentab'] for d in meta_sup.values())
    meta_cli_t    = sum(d['cli']    for d in meta_sup.values())
    meta_mix_t    = sum(d['mix']    for d in meta_sup.values())
    total = {
        'venda':         metas.linha_metrica(meta_valor_t,  rt['venda'],         dm, dd, dr, rt.get('proj_venda')),
        'rentabilidade': metas.linha_metrica(meta_rentab_t, rt['rentabilidade'], dm, dd, dr),
        'clientes':      metas.linha_metrica(meta_cli_t,    rt['clientes'],      dm, dd, dr),
        'mix':           metas.linha_metrica(meta_mix_t,    rt['mix'],           dm, dd, dr),
        # Margem do total = lucro realizado / receita realizada SEM bônus (denominador calibrado)
        'margem':        metas.pct(rt['rentabilidade'], rt.get('venda_sb') or rt['venda']),
    }
    return {'ok': True, 'ano': int(ano), 'mes': int(mes), 'dias': dias,
            'supervisores': supervisores_out, 'total': total}


def _ano_mes_req():
    """Lê ?ano=&mes= dos args; default = mês corrente."""
    from datetime import date as _date
    hoje = _date.today()
    try:
        ano = int(request.args.get('ano') or hoje.year)
        mes = int(request.args.get('mes') or hoje.month)
    except (TypeError, ValueError):
        ano, mes = hoje.year, hoje.month
    if not (1 <= mes <= 12):
        mes = hoje.month
    return ano, mes


@app.route('/metas')
@login_required
def metas_page():
    return send_from_directory('.', 'metas.html')


@app.route('/api/metas')
@login_required
def api_metas():
    ano, mes = _ano_mes_req()
    sup = _supervisores_filtro()
    try:
        return jsonify(_montar_metas_resposta(ano, mes, sup))
    except Exception as e:
        traceback.print_exc()
        return jsonify({'ok': False, 'error': f'Falha ao carregar metas: {e}'}), 500


@app.route('/api/metas/vendedores')
@login_required
def api_metas_vendedores():
    """Drill: vendedores de um supervisor, com as 4 métricas (meta+realizado)."""
    ano, mes = _ano_mes_req()
    try:
        codsup = int(request.args.get('codsupervisor'))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'codsupervisor obrigatório'}), 400
    sup = _supervisores_filtro()
    # Segurança: supervisor logado só pode abrir suas áreas
    sess_sups = _session_supervisores()
    if sess_sups and codsup not in sess_sups:
        return jsonify({'ok': False, 'error': 'Fora do escopo'}), 403

    realizado = _carregar_metas_realizado(ano, mes, sup)
    metas_db = _metas_buscar(ano, mes)
    vmap = _carregar_vendedores_map()
    dias = realizado['dias']
    dm, dd, dr = dias['mes'], dias['decorridos'], dias['restantes']
    escopo = _metas_escopo_codusur(sup)

    linhas = []
    for cu, info in vmap.items():
        if info.get('codsupervisor') is None or int(info['codsupervisor']) != codsup:
            continue
        if escopo is not None and int(cu) not in escopo:
            continue
        m = metas_db.get(cu, {})
        rz = realizado['por_vendedor'].get(cu, {})
        # Não trazer vendedor zerado: sem meta em nenhuma métrica E sem realizado em nenhuma.
        tem_meta = any((m.get(k) or 0) for k in ('valor_meta', 'clientes_meta', 'mix_meta', 'rentabilidade_meta'))
        tem_real = any((rz.get(k) or 0) for k in ('venda', 'rentabilidade', 'clientes', 'mix'))
        if not tem_meta and not tem_real:
            continue
        linhas.append({
            'codusur': int(cu), 'nome': info.get('nome') or f'RCA {cu}',
            'venda':         metas.linha_metrica(m.get('valor_meta'),        rz.get('venda'),        dm, dd, dr, rz.get('proj_venda')),
            'rentabilidade': metas.linha_metrica(m.get('rentabilidade_meta'),rz.get('rentabilidade'),dm, dd, dr),
            'clientes':      metas.linha_metrica(m.get('clientes_meta'),     rz.get('clientes'),     dm, dd, dr),
            'mix':           metas.linha_metrica(m.get('mix_meta'),          rz.get('mix'),          dm, dd, dr),
            'margem':        metas.pct(rz.get('rentabilidade'), rz.get('venda_sb') or rz.get('venda')),
        })
    linhas.sort(key=lambda x: x['venda']['realizado'], reverse=True)
    return jsonify({'ok': True, 'codsupervisor': codsup, 'dias': dias, 'vendedores': linhas})


@app.route('/api/metas/serie')
@login_required
def api_metas_serie():
    """Realizado diário (venda) do mês, pra gráfico de série. Só venda por simplicidade."""
    ano, mes = _ano_mes_req()
    sup = _supervisores_filtro()
    escopo = _metas_escopo_codusur(sup)
    rb = _metas_rbac_frag('PCPEDC', escopo)
    f = _and_dax(f"MONTH(PCPEDC[DATA])={mes} && YEAR(PCPEDC[DATA])={ano}",
                 'PCPEDC[POSICAO] IN {"F","L","B"}', rb)
    from datetime import date as _date
    corrente = (ano == _date.today().year and mes == _date.today().month)
    rf = _meta_refresh_tag() if corrente else 'fechado'  # segue o refresh do META (igual ao realizado)
    key = cache_key_for_user('metas:serie', {'ano': ano, 'mes': mes, 'sup': _sup_cache_key(sup), 'rf': rf})
    cached = _cache_get(key)
    if cached:
        return jsonify(cached)
    q = (f'EVALUATE SUMMARIZECOLUMNS(PCPEDC[DATA], '
         f'"venda", CALCULATE(SUM(PCPEDC[VLATEND]), FILTER(PCPEDC, {f})))')
    try:
        rows = clean_rows(_todas_linhas(run_dax_meta(q)))
    except Exception as e:
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500
    serie = []
    for r in rows:
        d = r.get('DATA')
        if d:
            serie.append({'data': str(d)[:10], 'venda': r.get('venda') or 0})
    serie.sort(key=lambda x: x['data'])
    resp = {'ok': True, 'ano': ano, 'mes': mes, 'serie': serie}
    _cache_set(key, resp, 'dax_agregado')
    return jsonify(resp)


@app.route('/api/metas/refresh')
@login_required
def api_metas_refresh():
    """Data/hora da última atualização do dataset META (topo da página de metas). Difere do
    /api/pbi/refresh (dataset RCA) — cada dataset tem refresh próprio; aqui mostramos o do META,
    que é a fonte do realizado/projeção, pra bater com o horário do BI que o diretor vê."""
    return jsonify({'ok': True, 'refresh': _get_meta_refresh()})


# ── Admin: edição de meta + sugestão + importação inicial ──
@app.route('/api/admin/metas')
@admin_required
def api_admin_metas_list():
    """Lista vendedores com meta (Postgres) + realizado do mês, pra tela de edição.
    ?supervisor=CSV recorta pelo(s) time(s) selecionado(s) na tela (herda o chip);
    sem supervisor = empresa toda (decisão do diretor)."""
    ano, mes = _ano_mes_req()
    incluir_todos = request.args.get('todos') == '1'
    sup = _supervisores_filtro()
    sup_set = set(sup) if sup else None
    metas_db = _metas_buscar(ano, mes)
    vmap = _carregar_vendedores_map()
    realizado = _carregar_metas_realizado(ano, mes, sup)
    linhas = []
    for cu, info in vmap.items():
        cs = info.get('codsupervisor')
        # recorte por time: se filtrou, só vendedores do(s) supervisor(es) selecionado(s)
        if sup_set is not None and (cs is None or int(cs) not in sup_set):
            continue
        m = metas_db.get(cu, {})
        rz = realizado['por_vendedor'].get(cu, {})
        tem_meta = any((m.get(k) or 0) for k in ('valor_meta', 'clientes_meta', 'mix_meta', 'rentabilidade_meta'))
        venda_real = rz.get('venda') or 0
        # por padrão só os relevantes (com meta OU com venda no mês); ?todos=1 traz a força toda
        if not incluir_todos and not tem_meta and not venda_real:
            continue
        linhas.append({
            'codusur': int(cu), 'nome': info.get('nome') or f'RCA {cu}',
            'codsupervisor': info.get('codsupervisor'),
            'valor_meta': m.get('valor_meta') or 0, 'clientes_meta': m.get('clientes_meta') or 0,
            'mix_meta': m.get('mix_meta') or 0, 'rentabilidade_meta': m.get('rentabilidade_meta') or 0,
            'venda_real': venda_real,
        })
    linhas.sort(key=lambda x: (-(x['venda_real'] or 0), x['nome']))
    return jsonify({'ok': True, 'ano': ano, 'mes': mes, 'vendedores': linhas})


@app.route('/api/admin/metas', methods=['POST'])
@admin_required
def api_admin_metas_save():
    """Salva (upsert) a meta de 1 vendedor. Body: {ano,mes,codusur,valor_meta,clientes_meta,mix_meta,rentabilidade_meta}."""
    data = request.get_json() or {}
    try:
        ano = int(data['ano']); mes = int(data['mes']); codusur = int(data['codusur'])
    except (KeyError, TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'ano, mes e codusur obrigatórios'}), 400
    if not (1 <= mes <= 12):
        return jsonify({'ok': False, 'error': 'mes inválido'}), 400
    try:
        _metas_upsert(ano, mes, codusur, data, user_id=session.get('user_id'))
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500
    return jsonify({'ok': True})


@app.route('/api/admin/metas/bulk', methods=['POST'])
@admin_required
def api_admin_metas_bulk():
    """Salva várias metas de uma vez (botão único do editor). Body: {ano,mes,metas:[{codusur,...}]}.
    Atômico: ou grava todas, ou nenhuma."""
    data = request.get_json() or {}
    try:
        ano = int(data['ano']); mes = int(data['mes'])
    except (KeyError, TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'ano e mes obrigatórios'}), 400
    if not (1 <= mes <= 12):
        return jsonify({'ok': False, 'error': 'mes inválido'}), 400
    itens = data.get('metas') or []
    if not isinstance(itens, list) or not itens:
        return jsonify({'ok': False, 'error': 'lista de metas vazia'}), 400
    limpos = []
    for it in itens:
        try:
            it = dict(it); it['codusur'] = int(it['codusur'])
        except (KeyError, TypeError, ValueError):
            return jsonify({'ok': False, 'error': 'codusur inválido em um dos itens'}), 400
        limpos.append(it)
    try:
        n = _metas_upsert_lote(ano, mes, limpos, user_id=session.get('user_id'))
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500
    return jsonify({'ok': True, 'salvos': n})


@app.route('/api/admin/metas/sugestao')
@admin_required
def api_admin_metas_sugestao():
    """Sugere meta de um vendedor com base no histórico de pedidos (12 meses).
    ?codusur=&ano=&mes=&metodo=ano_anterior|media_3m&crescimento=0.10"""
    ano, mes = _ano_mes_req()
    try:
        codusur = int(request.args.get('codusur'))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'codusur obrigatório'}), 400
    metodo = request.args.get('metodo', 'media_3m')
    try:
        crescimento = float(request.args.get('crescimento') or 0)
    except (TypeError, ValueError):
        crescimento = 0.0

    # Histórico vem do dataset RCA (faturamento) — o PCPEDC do dataset META só tem o mês corrente.
    # A sugestão é um ponto de partida (faturamento ≈ proxy do alvo); o admin ajusta.
    q = ('EVALUATE SUMMARIZECOLUMNS(CALENDARIO[AnoMes], '
         f'FILTER(FATURAMENTO_VENDAS, FATURAMENTO_VENDAS[CODUSUR]={codusur}), '
         '"venda", [VENDA LIQUIDA], "rentab", [LUCRO TOTAL], '
         '"cli", DISTINCTCOUNT(FATURAMENTO_VENDAS[CODCLI]), '
         '"mix", DISTINCTCOUNT(FATURAMENTO_VENDAS[CODPROD]))')
    try:
        payload = retry_dax(execute_dax)(get_token_cached(), q)  # dataset RCA (default)
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

    h_venda, h_rentab, h_cli, h_mix = {}, {}, {}, {}
    for r in clean_rows(_todas_linhas(payload)):
        am = r.get('AnoMes')
        if am is None:
            continue
        am = int(am)
        h_venda[am] = r.get('venda') or 0
        h_rentab[am] = r.get('rentab') or 0
        h_cli[am] = r.get('cli') or 0
        h_mix[am] = r.get('mix') or 0

    sug = {
        'valor_meta':         metas.sugerir(h_venda,  ano, mes, metodo, crescimento),
        'rentabilidade_meta': metas.sugerir(h_rentab, ano, mes, metodo, crescimento),
        'clientes_meta':      metas.sugerir(h_cli,    ano, mes, metodo, crescimento),
        'mix_meta':           metas.sugerir(h_mix,    ano, mes, metodo, crescimento),
    }
    # arredonda contagens
    for k in ('clientes_meta', 'mix_meta'):
        if sug[k] is not None:
            sug[k] = int(round(sug[k]))
    return jsonify({'ok': True, 'codusur': codusur, 'metodo': metodo, 'sugestao': sug})


@app.route('/api/admin/metas/importar', methods=['POST'])
@admin_required
def api_admin_metas_importar():
    """Semeia multpel_metas lendo a tabela META do dataset (pra o diretor não redigitar).
    Padrão: importa o mês corrente. ?todos=1 importa TODOS os meses presentes na tabela META
    (ideal no 1º deploy — traz tudo que o diretor já cadastrou, de uma vez)."""
    if request.args.get('todos') == '1':
        q = "EVALUATE 'META'"  # tabela inteira (todos os meses)
    else:
        ano, mes = _ano_mes_req()
        q = f"EVALUATE FILTER('META', 'META'[Ano]={ano} && 'META'[MES]={mes})"
    try:
        rows = clean_rows(_todas_linhas(run_dax_meta(q)))
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500
    n = 0
    meses = set()
    for r in rows:
        cod = r.get('CODUSUR')
        r_ano = r.get('ANO') if r.get('ANO') is not None else r.get('Ano')
        r_mes = r.get('MES') if r.get('MES') is not None else r.get('Mes')
        if cod is None or r_ano is None or r_mes is None:
            continue
        try:
            codusur, r_ano, r_mes = int(cod), int(r_ano), int(r_mes)
        except (TypeError, ValueError):
            continue
        _metas_upsert(r_ano, r_mes, codusur, {
            'valor_meta':         r.get('VALOR_META') or 0,
            'clientes_meta':      r.get('CLIENTES_META') or 0,
            'mix_meta':           r.get('MIX_META') or 0,
            'rentabilidade_meta': r.get('RENTABILIDADE') or 0,
        }, user_id=session.get('user_id'))
        n += 1
        meses.add(f'{r_ano}-{r_mes:02d}')
    return jsonify({'ok': True, 'importados': n, 'meses': sorted(meses)})


def _prewarm_metas():
    """Esquenta o realizado de metas do mês corrente (escopo admin). Sem request context →
    simula sessão admin pra session.get() do RBAC/cache_key funcionar."""
    from datetime import date as _date
    hoje = _date.today()
    with app.test_request_context():
        session['role'] = 'admin'
        # Aquece o cache JÁ escopado ao universo de meta (mesmo caminho do /api/metas)
        _montar_metas_resposta(hoje.year, hoje.month, None)


def _prewarm_cache_admin():
    """Roda em thread separada após startup. Esquenta cache de admin pras 2 queries pesadas
    (sazonalidade 24m + ranking vendedores). Aguarda Redis estar UP antes (sem ele cache_set
    é no-op e prewarm seria desperdício). Falhas são silenciosas — app continua subindo."""
    import threading

    def _worker():
        for _ in range(15):
            try:
                _R.ping()
                break
            except redis.RedisError:
                time.sleep(2)
        else:
            print("[PREWARM] Redis indisponivel apos 30s, pulando.")
            return

        print("[PREWARM] Esquentando cache admin (sazonalidade + vendedores ranking)...")
        tarefas = [
            ('_prewarm:sazonalidade',         lambda: _carregar_sazonalidade(role='admin')),
            ('_prewarm:vendedores',           lambda: _carregar_ranking_vendedores(role='admin')),
            ('_prewarm:venda_mensal_cli',     lambda: _carregar_venda_mensal_por_cliente(role='admin')),
            ('_prewarm:devolucao_mensal_cli', lambda: _carregar_devolucao_mensal_por_cliente(role='admin')),
            ('_prewarm:metas',                _prewarm_metas),
        ]
        for rota, fn in tarefas:
            t0 = time.time()
            try:
                fn()
                dur = int((time.time() - t0) * 1000)
                print(f"[PREWARM] {rota} OK ({dur}ms)")
                _log_background(rota, duracao_ms=dur)
            except Exception as e:
                dur = int((time.time() - t0) * 1000)
                print(f"[PREWARM] {rota} falhou (sem impacto): {e}")
                _log_background(rota, duracao_ms=dur, erro=str(e)[:500])

    threading.Thread(target=_worker, daemon=True).start()


# ──────────────────────────────────────────────────────────────────────
# Cron de envio de relatórios por email (APScheduler in-process)
# Roda a cada 5min, verifica quais users têm cron_enabled=true + horário
# na janela últimos 5min + frequência bate com dia da semana atual.
# Só dispara emails se CRON_HABILITADO=true (env var, default false em dev).
# ──────────────────────────────────────────────────────────────────────

_scheduler = None  # singleton, criado em _start_scheduler

_DIAS_SEMANA_MAP = {
    'semanal_seg': 0,  # Monday
    'semanal_sex': 4,  # Friday
}


def _disparar_relatorios_agendados():
    """Job executado pelo APScheduler a cada 5min. Pega users elegíveis e dispara emails."""
    from datetime import datetime, time as dtime, timedelta
    from zoneinfo import ZoneInfo
    if not CRON_HABILITADO:
        return  # safety net: desliga tudo se env var false

    # Container pode rodar em UTC — usa TZ Brasil explícita pra bater com cron_horario cadastrado
    agora = datetime.now(ZoneInfo('America/Sao_Paulo'))
    inicio_janela = (agora - timedelta(minutes=5)).time()
    fim_janela = agora.time()
    dia_semana = agora.weekday()

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """SELECT id, nome, email, cron_horario, cron_frequencia, cron_enabled, email_alerta_cobertura,
                  relatorios_estoque
           FROM multpel_users
           WHERE ativo = true
             AND (cron_enabled = true OR email_alerta_cobertura = true
                  OR COALESCE(jsonb_array_length(relatorios_estoque), 0) > 0)
             AND cron_horario >= %s AND cron_horario < %s""",
        (inicio_janela, fim_janela)
    )
    candidatos = cur.fetchall()
    cur.close()
    conn.close()

    if not candidatos:
        return

    print(f"[CRON] {len(candidatos)} candidato(s) na janela {inicio_janela}–{fim_janela}")
    for row in candidatos:
        uid, nome, email, horario, frequencia, cron_enabled, alerta_cobertura, rels_estoque = row
        # Filtro por frequência (diária roda todo dia; semanal só no dia certo)
        if frequencia in _DIAS_SEMANA_MAP and dia_semana != _DIAS_SEMANA_MAP[frequencia]:
            continue
        # 1) Relatório de carteira (se cron_enabled)
        if cron_enabled:
            try:
                resultado = enviar_relatorio_email(uid)
                print(f"[CRON] user {uid} ({email}): relatório {'OK' if resultado.get('ok') else 'FAIL'} — {resultado.get('error') or resultado.get('message_id')}")
            except Exception as e:
                print(f"[CRON] user {uid} ({email}): erro relatório {e}")
                _log_background(f'cron:erro:user{uid}', erro=str(e)[:500])
        # 2) Alerta de cobertura (se opt-in) — só envia se houver alguém abaixo do limiar
        if alerta_cobertura:
            try:
                res = enviar_alerta_cobertura_email(uid)
                print(f"[CRON] user {uid} ({email}): alerta cobertura {'OK' if res.get('ok') else 'FAIL'} — {res.get('skipped') or res.get('error') or res.get('message_id')}")
            except Exception as e:
                print(f"[CRON] user {uid} ({email}): erro alerta {e}")
                _log_background(f'cron:erro_alerta:user{uid}', erro=str(e)[:500])
        # 3) Relatórios do módulo Compras (reusam o horário/frequência acima, por decisão de
        #    produto: um único agendamento por pessoa, não um por relatório).
        if rels_estoque:
            try:
                res = enviar_relatorios_estoque_email(uid)
                detalhe = res.get('skipped') or res.get('error') or res.get('message_id')
                print(f"[CRON] user {uid} ({email}): compras {'OK' if res.get('ok') else 'FAIL'} — {detalhe}")
            except Exception as e:
                print(f"[CRON] user {uid} ({email}): erro compras {e}")
                _log_background(f'cron:erro_estoque:user{uid}', erro=str(e)[:500])


def _start_scheduler():
    """Inicia APScheduler in-process. Roda a cada 5min. Idempotente."""
    global _scheduler
    if _scheduler is not None:
        return  # já rodando
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        _scheduler = BackgroundScheduler(daemon=True, timezone='America/Sao_Paulo')
        _scheduler.add_job(_disparar_relatorios_agendados, 'interval', minutes=5, id='envio_relatorios')
        _scheduler.start()
        status = 'ATIVO' if CRON_HABILITADO else 'aguardando CRON_HABILITADO=true'
        print(f"[CRON] Scheduler iniciado ({status})")
    except Exception as e:
        print(f"[CRON] Falhou ao iniciar scheduler: {e}")


if __name__ == '__main__':
    print("\n[JOGA Analytics] Backend iniciando...")
    missing = [k for k, v in CONFIG.items() if not v]
    if missing:
        print(f"[AVISO] Vars Power BI faltando: {', '.join(missing)}")
    else:
        print("[OK] Configuracao Power BI carregada")
    try:
        _R.ping()
        print("[OK] Redis conectado")
    except redis.RedisError as e:
        print(f"[AVISO] Redis nao conectou: {e}")
    # Em prod: Waitress (WSGI server). Em dev: Flask dev server com hot-reload.
    IS_PROD = os.getenv('FLASK_ENV') == 'production'
    DEBUG_MODE = not IS_PROD
    # Guard contra double-fire do reloader em dev (Werkzeug spawn pai + filho).
    # Em prod sem reloader → fire sempre.
    if IS_PROD or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        _prewarm_cache_admin()
        _start_scheduler()
    port = int(os.getenv('PORT', 5000))
    if IS_PROD:
        from waitress import serve
        print(f"[INFO] Waitress (production) serving on 0.0.0.0:{port}\n")
        serve(app, host='0.0.0.0', port=port, threads=8)
    else:
        print(f"[INFO] http://localhost:{port}\n")
        app.run(host='0.0.0.0', port=port, debug=DEBUG_MODE)
