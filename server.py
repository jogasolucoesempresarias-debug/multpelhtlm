"""
Multpel Analytics — Backend
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
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-change-me')
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
    parts = [
        'multpel', endpoint,
        f"role={session.get('role', 'anon')}",
        f"usur={session.get('codusur', '-')}",
        f"supv={session.get('codsupervisor', '-')}",
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


# ── Decorators de autenticação ──
def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            if request.path.startswith('/api/'):
                return jsonify({'ok': False, 'error': 'Não autenticado'}), 401
            return redirect('/login')
        if session.get('must_change_password') and request.path not in ('/trocar-senha', '/api/trocar-senha'):
            if request.path.startswith('/api/'):
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


# ── RBAC ──
def aplicar_rbac_dax():
    """Fragmento DAX a concatenar via && em FILTER, conforme RBAC."""
    if session.get('role') == 'admin':
        return ""
    if session.get('codusur'):
        return f"FATURAMENTO_VENDAS[CODUSUR] = {int(session['codusur'])}"
    if session.get('codsupervisor'):
        return f"FATURAMENTO_VENDAS[CODSUPERVISOR] = {int(session['codsupervisor'])}"
    return ""


def rbac_devol_dax():
    """Fragmento RBAC pra colar em FILTER(FATURAMENTO_DEVOLUCAO, ...).
    Sem isso, devoluções vão da empresa toda → valores negativos absurdos pro vendedor."""
    if session.get('role') == 'admin':
        return ""
    if session.get('codusur'):
        return f"FATURAMENTO_DEVOLUCAO[CODUSUR] = {int(session['codusur'])}"
    if session.get('codsupervisor'):
        return f"FATURAMENTO_DEVOLUCAO[CODSUPERVISOR] = {int(session['codsupervisor'])}"
    return ""


def rbac_devol_av_dax():
    """Fragmento RBAC pra colar em FILTER(FATURAMENTO_DEVOLUCAO_AVULSA, ...)."""
    if session.get('role') == 'admin':
        return ""
    if session.get('codusur'):
        return f"FATURAMENTO_DEVOLUCAO_AVULSA[CODUSUR] = {int(session['codusur'])}"
    if session.get('codsupervisor'):
        return f"FATURAMENTO_DEVOLUCAO_AVULSA[CODSUPERVISOR] = {int(session['codsupervisor'])}"
    return ""


# ── Helper temporal ──
def filtro_periodo(tipo: str) -> str:
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
    if tipo == '24m':
        return "FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -24)"
    ano, mes = tipo.split('-')
    return f"YEAR(FATURAMENTO_VENDAS[DTSAIDA])={int(ano)} && MONTH(FATURAMENTO_VENDAS[DTSAIDA])={int(mes)}"


def filtro_periodo_devol(tipo: str) -> str:
    """Mesmo que filtro_periodo, mas pra FATURAMENTO_DEVOLUCAO via DTENT (alinha RCA)."""
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
    if tipo == '24m':
        return "FATURAMENTO_DEVOLUCAO[DTENT] >= EDATE(TODAY(), -24)"
    ano, mes = tipo.split('-')
    return f"YEAR(FATURAMENTO_DEVOLUCAO[DTENT])={int(ano)} && MONTH(FATURAMENTO_DEVOLUCAO[DTENT])={int(mes)}"


def filtro_periodo_devol_av(tipo: str) -> str:
    """Mesmo que filtro_periodo, mas pra FATURAMENTO_DEVOLUCAO_AVULSA via DTENT."""
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
    if tipo == '24m':
        return "FATURAMENTO_DEVOLUCAO_AVULSA[DTENT] >= EDATE(TODAY(), -24)"
    ano, mes = tipo.split('-')
    return f"YEAR(FATURAMENTO_DEVOLUCAO_AVULSA[DTENT])={int(ano)} && MONTH(FATURAMENTO_DEVOLUCAO_AVULSA[DTENT])={int(mes)}"


def _construir_filtro_3tabelas(periodo='mes_atual'):
    """Retorna (f_vendas, f_devol, f_devol_av) com filtro temporal + RBAC pra cada tabela.
    Pra usar em CALCULATE/FILTER quando a métrica envolve devoluções (VL/Lucro alinhado RCA)."""
    base_v = filtro_periodo(periodo)
    base_d = filtro_periodo_devol(periodo)
    base_da = filtro_periodo_devol_av(periodo)
    rbac_v = aplicar_rbac_dax()
    rbac_d = rbac_devol_dax()
    rbac_da = rbac_devol_av_dax()
    f_v = f"{base_v} && {rbac_v}" if rbac_v else base_v
    f_d = f"{base_d} && {rbac_d}" if rbac_d else base_d
    f_da = f"{base_da} && {rbac_da}" if rbac_da else base_da
    return (f_v, f_d, f_da)


def expr_venda_liquida_rca(periodo):
    """Expressão DAX escalar [VENDA LIQUIDA] alinhada com RCA (F.3) + RBAC nas 3 tabelas.
    VL = BRUTA(DTSAIDA) - DEVOL(DTENT) - DEVOL_AV(DTENT)."""
    f_v, f_d, f_da = _construir_filtro_3tabelas(periodo)
    return (
        f"(CALCULATE([VENDA BRUTA], FILTER(FATURAMENTO_VENDAS, {f_v}))"
        f" - CALCULATE([TOTAL DEVOLUCAO], FILTER(FATURAMENTO_DEVOLUCAO, {f_d}))"
        f" - CALCULATE([TOTAL DEVOLUCAO AVULSA], FILTER(FATURAMENTO_DEVOLUCAO_AVULSA, {f_da})))"
    )


def expr_lucro_rca(periodo):
    """Expressão DAX escalar [LUCRO TOTAL] alinhada com RCA (F.4) + RBAC nas 3 tabelas.
    LUCRO = VL - (CUSTO - CUSTO_DEVOL_DTENT - CUSTO_DEVOL_AV_DTENT)."""
    f_v, f_d, f_da = _construir_filtro_3tabelas(periodo)
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


def _ler_usuario(usuario_id):
    """Carrega 1 user do multpel_users por id. Retorna dict ou None."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, nome, email, role, codusur, codsupervisor, telefone, ativo, "
        "cron_enabled, cron_horario, cron_frequencia, email_cc, segmentos_rfm "
        "FROM multpel_users WHERE id = %s",
        (usuario_id,)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return None
    return {
        'id': row[0], 'nome': row[1], 'email': row[2], 'role': row[3],
        'codusur': row[4], 'codsupervisor': row[5], 'telefone': row[6], 'ativo': row[7],
        'cron_enabled': row[8], 'cron_horario': row[9], 'cron_frequencia': row[10],
        'email_cc': row[11] if row[11] is not None else [],
        'segmentos_rfm': row[12] or '',
    }


def _gerar_relatorio_para_usuario(usuario):
    """Gera (csv_bytes, pdf_bytes) já filtrados pelo escopo do usuário.
    - role='vendedor' → só clientes com codusur=usuario.codusur
    - role='supervisor' → só clientes com codsupervisor=usuario.codsupervisor
    - role='admin'/'viewer' → carteira inteira (raro mandar por email)."""
    clientes = _carregar_carteira_full()
    args = {'modo': 'personalizada', 'limit': 100000, 'offset': 0,
            'sort': 'receita_perdida', 'dir': 'desc'}
    if usuario.get('role') == 'vendedor' and usuario.get('codusur') is not None:
        args['vendedor'] = str(usuario['codusur'])
    elif usuario.get('role') == 'supervisor' and usuario.get('codsupervisor') is not None:
        args['time'] = str(usuario['codsupervisor'])
    # Patch K: filtro de segmento RFM (vazio = carteira completa)
    if usuario.get('segmentos_rfm'):
        args['segmento'] = usuario['segmentos_rfm']
    resultado = _filtrar_carteira(clientes, args)
    filtrados = resultado['rows']

    # PDF — anexa segmentos selecionados no rodapé pra ficar visível
    filtros_seg = ''
    if usuario.get('segmentos_rfm'):
        # NOME_SEG_PT é definido em _gerar_pdf_carteira / outras telas; usa mapa local pra não acoplar
        _NOMES_SEG = {
            'champions': 'Campeões', 'loyal': 'Fiéis', 'cant_lose': 'Não Perder',
            'at_risk': 'Em Risco', 'potential_loyalist': 'Promissores',
            'new': 'Novos', 'hibernating': 'Inativos', 'lost': 'Perdidos',
        }
        nomes = [_NOMES_SEG.get(s, s) for s in usuario['segmentos_rfm'].split(',') if s]
        filtros_seg = f" · Segmentos: {', '.join(nomes)}"
    pdf_bytes = _gerar_pdf_carteira(filtrados, filtros_resumo=f"Relatório de {usuario.get('nome')}{filtros_seg}")

    # CSV
    cabecalho = ['CodCli', 'Cliente', 'Cidade', 'UF', 'Vendedor', 'Telefone',
                 'R(dias)', 'Segmento', 'Venda12m', 'MediaVenda12m', 'ReceitaPerdidaProj']
    csv_lines = ['﻿' + _csv_linha(cabecalho).rstrip('\n')]
    for c in filtrados:
        v = c.get('venda_12m') or 0
        csv_lines.append(_csv_linha([
            c.get('codcli'), c.get('cliente'), c.get('cidade'), c.get('uf'),
            c.get('vendedor'), c.get('telefone'),
            c.get('recencia_dias'), c.get('segmento'),
            v, round(v / 12, 2), c.get('receita_perdida_proj'),
        ]).rstrip('\n'))
    csv_bytes = ('\n'.join(csv_lines) + '\n').encode('utf-8')

    return csv_bytes, pdf_bytes, len(filtrados)


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
        session['codsupervisor'] = user.get('codsupervisor')
        try:
            csv_bytes, pdf_bytes, count = _gerar_relatorio_para_usuario(user)
        except Exception as e:
            return {'ok': False, 'error': f'Falha ao gerar relatório: {e}'}

    from datetime import date as _date
    data_iso = _date.today().isoformat()
    base_nome = f"carteira_{user.get('nome','user').replace(' ','_').lower()}_{data_iso}"

    html = f"""<html><body style="font-family:Arial,sans-serif;color:#0a0e17;">
<h2 style="color:#38bdf8;">Multpel Analytics</h2>
<p>Olá <strong>{user.get('nome')}</strong>,</p>
<p>Segue seu relatório de carteira atualizado em {_date.today().strftime('%d/%m/%Y')}.</p>
<ul>
  <li><strong>{count}</strong> clientes na sua carteira</li>
  <li>Modo de classificação: PERSONALIZADA (ciclo individual)</li>
</ul>
<p>Anexos: 1 PDF (visual) + 1 CSV (Excel-compatível).</p>
<p style="color:#94a3b8;font-size:12px;">Email automatizado — Multpel Analytics</p>
</body></html>"""

    # Patch J — sanitiza lista de CC (remove principal duplicado, valida formato, limita)
    try:
        emails_cc = _normalizar_emails_cc(user.get('email_cc') or [], user['email'])
    except ValueError:
        emails_cc = []  # CC inválido no banco não bloqueia o envio principal

    payload = {
        'from': RESEND_FROM,
        'to': [user['email']],
        'subject': f"Multpel — Carteira {_date.today().strftime('%d/%m/%Y')}",
        'html': html,
        'attachments': [
            {'filename': base_nome + '.pdf', 'content': base64.b64encode(pdf_bytes).decode()},
            {'filename': base_nome + '.csv', 'content': base64.b64encode(csv_bytes).decode()},
        ],
    }
    if emails_cc:
        payload['cc'] = emails_cc

    try:
        resp = resend.Emails.send(payload)
        message_id = resp.get('id') if isinstance(resp, dict) else getattr(resp, 'id', None)
        _log_background(f'email:enviado:user{usuario_id}', duracao_ms=None)
        return {'ok': True, 'message_id': message_id, 'clientes_no_anexo': count,
                'anexos_kb': round((len(pdf_bytes) + len(csv_bytes)) / 1024, 1),
                'destinatarios_total': 1 + len(emails_cc)}
    except Exception as e:
        erro_str = str(e)[:500]
        _log_background(f'email:erro:user{usuario_id}', erro=erro_str)
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
        return redirect('/')
    return send_from_directory('.', 'login.html')


@app.route('/health')
def health():
    """Liveness check pro Docker/Traefik. Sem auth, retorna 200 quando o processo está up."""
    return jsonify({'ok': True, 'service': 'multpel-analytics'}), 200


@app.route('/trocar-senha', methods=['GET'])
def trocar_senha_page():
    if 'user_id' not in session:
        return redirect('/login')
    return send_from_directory('.', 'trocar-senha.html')


@app.route('/')
@login_required
def index_page():
    return send_from_directory('.', 'index.html')


# ── Auth API ──
@app.route('/api/login', methods=['POST'])
def login_post():
    data = request.get_json() or {}
    email = data.get('email', '').strip().lower()
    senha = data.get('senha', '')
    if not email or not senha:
        return jsonify({'ok': False, 'error': 'Preencha e-mail e senha'}), 400
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, nome, password_hash, role, ativo, codusur, codsupervisor, must_change_password "
        "FROM multpel_users WHERE email = %s", (email,)
    )
    user = cur.fetchone()
    cur.close()
    conn.close()
    if not user:
        return jsonify({'ok': False, 'error': 'E-mail ou senha inválidos'}), 401
    uid, nome, pw_hash, role, ativo, codusur, codsupervisor, mcp = user
    if not ativo:
        return jsonify({'ok': False, 'error': 'Conta desativada'}), 403
    if not check_password_hash(pw_hash, senha):
        return jsonify({'ok': False, 'error': 'E-mail ou senha inválidos'}), 401
    session['user_id']              = uid
    session['nome']                 = nome
    session['role']                 = role
    session['codusur']              = codusur
    session['codsupervisor']        = codsupervisor
    session['must_change_password'] = bool(mcp)
    if mcp:
        return jsonify({'ok': True, 'redirect': '/trocar-senha'})
    return jsonify({'ok': True, 'redirect': '/'})


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
    return jsonify({'ok': True, 'redirect': '/'})


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


# ──────────────────────────────────────────────────────────────────────
# Dashboard Executivo — 6 endpoints
# Todos: @login_required + cache Redis 1h + RBAC aplicado (quando faz sentido)
# ──────────────────────────────────────────────────────────────────────


def _construir_filtro(periodo='mes_atual'):
    """Combina filtro temporal + RBAC pra usar em CALCULATE/FILTER."""
    base = filtro_periodo(periodo)
    rbac = aplicar_rbac_dax()
    return f"{base} && {rbac}" if rbac else base


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


@app.route('/api/dashboard/kpis')
@login_required
def api_dashboard_kpis():
    key = cache_key_for_user('dashboard:kpis')
    cached = _cache_get(key)
    if cached:
        return jsonify(cached)

    f_atual = _construir_filtro('mes_atual')
    # Patch G.2: VL e LUCRO alinhados RCA c/ RBAC nas 3 tabelas (vendedor/supervisor logado).
    # MARGEM e TICKET MEDIO recalculados em Python a partir do VL alinhado.
    vl_rca   = expr_venda_liquida_rca('mes_atual')
    lucro_rca = expr_lucro_rca('mes_atual')

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
        'yoy': """EVALUATE {(
            [Crescimento Ano a Ano Receita Liquida],
            [Crescimento Ano a Ano Lucro Bruto],
            [Crescimento Ano a Ano Positivacao Cliente],
            [Crescimento Ano a Ano Positivacao Mix]
        )}"""
    }

    resultados = executar_dax_paralelo(queries)

    p = _primeira_linha(resultados['primarios'])
    s = _primeira_linha(resultados['secundarios'])
    y = _primeira_linha(resultados['yoy'])

    def _vals(row, n=4):
        return [row.get(f'[Value{i}]') for i in range(1, n+1)]

    vp = _vals(p, 3)
    vs = _vals(s)
    vy = _vals(y)

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
        'yoy': {
            'receita_liquida':    vy[0],
            'lucro_bruto':        vy[1],
            'positivacao_cliente': vy[2],
            'positivacao_mix':    vy[3],
        }
    }
    _cache_set(key, resp, 'dax_agregado')
    return jsonify(resp)


@app.route('/api/dashboard/serie')
@login_required
def api_dashboard_serie():
    periodo = request.args.get('periodo', '12m')
    key = cache_key_for_user('dashboard:serie', {'periodo': periodo})
    cached = _cache_get(key)
    if cached:
        return jsonify(cached)

    # Patch G.2: 3 queries paralelas (vendas + devolução + devolução avulsa)
    # com RBAC nas 3 tabelas, merge por AnoMes em Python. Alinha RCA.
    f_v, f_d, f_da = _construir_filtro_3tabelas(periodo)

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
    """YoY global (medidas pré-calculadas usam contexto temporal próprio)."""
    key = cache_key_for_user('dashboard:yoy')
    cached = _cache_get(key)
    if cached:
        return jsonify(cached)

    query = """EVALUATE {(
        [Crescimento Ano a Ano Receita Liquida],
        [Crescimento Ano a Ano Lucro Bruto],
        [Crescimento Ano a Ano Positivacao Cliente],
        [Crescimento Ano a Ano Positivacao Mix]
    )}"""
    token = get_token_cached()
    payload = retry_dax(execute_dax)(token, query)
    row = _primeira_linha(payload)
    resp = {
        'ok': True,
        'yoy': {
            'receita_liquida':     row.get('[Value1]'),
            'lucro_bruto':         row.get('[Value2]'),
            'positivacao_cliente': row.get('[Value3]'),
            'positivacao_mix':     row.get('[Value4]'),
        }
    }
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
    key = cache_key_for_user('dashboard:pareto', {'top': top})
    cached = _cache_get(key)
    if cached:
        return jsonify(cached)

    # Patch G.2: 2 queries paralelas (bruta + devoluções por CODCLI), merge em Python.
    f_v, f_d, f_da = _construir_filtro_3tabelas('12m')

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


def _carregar_sazonalidade(role='admin', codusur=None, codsupervisor=None):
    """Carrega sazonalidade 24m. Aceita role/usur/supv explícitos pra permitir prewarm
    sem request context. Cache key compatível com cache_key_for_user."""
    key = ':'.join([
        'multpel', 'dashboard:sazonalidade',
        f"role={role or 'anon'}",
        f"usur={codusur if codusur is not None else '-'}",
        f"supv={codsupervisor if codsupervisor is not None else '-'}",
    ])
    cached = _cache_get(key)
    if cached:
        return cached

    # Patch G.2: RBAC nas 3 tabelas. Aceita params explícitos (prewarm sem session).
    def _rbac_frag(tabela, col):
        if role == 'admin': return ''
        if codusur is not None:        return f" && {tabela}[{col}] = {int(codusur)}"
        if codsupervisor is not None:
            col_sup = 'CODSUPERVISOR'
            return f" && {tabela}[{col_sup}] = {int(codsupervisor)}"
        return ''
    rbac_v   = _rbac_frag('FATURAMENTO_VENDAS', 'CODUSUR')
    rbac_d   = _rbac_frag('FATURAMENTO_DEVOLUCAO', 'CODUSUR')
    rbac_da  = _rbac_frag('FATURAMENTO_DEVOLUCAO_AVULSA', 'CODUSUR')
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
        codsupervisor=session.get('codsupervisor'),
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

    key = cache_key_for_user('dashboard:top-clientes', {'metrica': metrica, 'limit': limit})
    cached = _cache_get(key)
    if cached:
        return jsonify(cached)

    # Patch G.2: 2 queries paralelas (vendas + devoluções por CODCLI), merge em Python.
    f_v, f_d, f_da = _construir_filtro_3tabelas('12m')

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

VENDEDORES_TECNICOS = {999, 900, 4, 272}  # excluir das listas de vendedor (técnicos)


@app.route('/carteira')
@login_required
def carteira_page():
    return send_from_directory('.', 'carteira.html')


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
            'nome': r.get('NOME') or f'Time {cs}',
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
            'nome':           r.get('NOME') or f'RCA {codusur}',
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
        meu_sup = session.get('codsupervisor')
        if not meu_sup:
            return False
        vmap = _carregar_vendedores_map()
        v = vmap.get(str(codusur_alvo))
        if not v:
            return False
        try:
            return int(v.get('codsupervisor') or 0) == int(meu_sup)
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
    retorna lista completa de clientes com TODOS os campos calculados.
    Cache Redis 1h (chave inclui RBAC do user)."""
    key = cache_key_for_user('carteira:full:v1')
    cached = _cache_get(key)
    if cached:
        return cached

    rbac = aplicar_rbac_dax()
    rbac_frag = f" && {rbac}" if rbac else ""

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
        'meta': """EVALUATE
SUMMARIZECOLUMNS(
    PCCLIENT[CODCLI], PCCLIENT[CLIENTE], PCCLIENT[FANTASIA],
    PCCLIENT[MUNICENT], PCCLIENT[MUNICCOB], PCCLIENT[ESTENT],
    PCCLIENT[TELCOB], PCCLIENT[TELENT],
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
            'telefone':  r.get('TELCOB') or r.get('TELENT'),
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

    clientes = _carregar_carteira_full()
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
    cross_tuples = sorted({(
        c.get('codusur'),
        c.get('codsupervisor'),
        (c.get('uf') or '').upper() or None,
        c.get('cidade') or None,
    ) for c in clientes})
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
    """Cache 1h: {codcli: {anomes: venda_bruta}} dos últimos 12 meses.

    IMPORTANTE: retorna VENDA BRUTA (SUM(VLVENDA) por DTSAIDA), NÃO líquida.
    A devolução é calculada separadamente em _carregar_devolucao_mensal_por_cliente
    usando DTENT (data da devolução). Líquida final = bruta(DTSAIDA) - devolução(DTENT)
    pra alinhar com o relatório RCA do ERP do cliente (visão caixa)."""
    if role is None and codusur is None and codsupervisor is None:
        role = session.get('role')
        codusur = session.get('codusur')
        codsupervisor = session.get('codsupervisor')

    key = ':'.join([
        'multpel', 'venda_mensal_por_cliente:v3',   # v3 = [VENDA BRUTA] em 24m
        f"role={role or 'anon'}",
        f"usur={codusur if codusur is not None else '-'}",
        f"supv={codsupervisor if codsupervisor is not None else '-'}",
    ])
    cached = _cache_get(key)
    if cached:
        return {int(cc): {int(am): v for am, v in meses.items()}
                for cc, meses in cached.items()}

    if role == 'admin':
        rbac = ''
    elif codusur is not None:
        rbac = f"FATURAMENTO_VENDAS[CODUSUR] = {int(codusur)}"
    elif codsupervisor is not None:
        rbac = f"FATURAMENTO_VENDAS[CODSUPERVISOR] = {int(codsupervisor)}"
    else:
        rbac = ''
    rbac_frag = f" && {rbac}" if rbac else ""

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

    Usado junto com _carregar_venda_mensal_por_cliente: líquida = bruta - devolucao."""
    if role is None and codusur is None and codsupervisor is None:
        role = session.get('role')
        codusur = session.get('codusur')
        codsupervisor = session.get('codsupervisor')

    key = ':'.join([
        'multpel', 'devolucao_mensal_por_cliente:v1',
        f"role={role or 'anon'}",
        f"usur={codusur if codusur is not None else '-'}",
        f"supv={codsupervisor if codsupervisor is not None else '-'}",
    ])
    cached = _cache_get(key)
    if cached:
        return {int(cc): {int(am): v for am, v in meses.items()}
                for cc, meses in cached.items()}

    if role == 'admin':
        rbac_dev = ''
        rbac_devav = ''
    elif codusur is not None:
        rbac_dev = f" && FATURAMENTO_DEVOLUCAO[CODUSUR] = {int(codusur)}"
        rbac_devav = f" && FATURAMENTO_DEVOLUCAO_AVULSA[CODUSUR] = {int(codusur)}"
    elif codsupervisor is not None:
        rbac_dev = f" && FATURAMENTO_DEVOLUCAO[CODSUPERVISOR] = {int(codsupervisor)}"
        rbac_devav = f" && FATURAMENTO_DEVOLUCAO_AVULSA[CODSUPERVISOR] = {int(codsupervisor)}"
    else:
        rbac_dev = ''
        rbac_devav = ''

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
    args = request.args
    # Detecta tipo de filtros
    filtros_complexos = any(args.get(k) for k in ('cidade', 'segmento', 'status', 'busca'))

    if filtros_complexos:
        return _chart_receita_via_in_memory(args)
    else:
        return _chart_receita_via_dax_direto(args)


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
    """Caminho in-memory pra filtros que envolvem cidade/segmento/busca.
    Aceita ~1% de diferença vs RCA por usar carteira_full atual (histórico vs presente)."""
    clientes_full = _carregar_carteira_full()
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
    clientes_full = _carregar_carteira_full()
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
    # Filtro "dias sem comprar (mín.)" — recencia_dias >= dias_min
    dias_min = None
    if args.get('dias_min') not in (None, ''):
        try:
            dias_min = max(0, int(args['dias_min']))
        except (TypeError, ValueError):
            dias_min = None

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

    sort_map = {
        'lucro_perdido':   'lucro_perdido_proj',
        'receita_perdida': 'receita_perdida_proj',
        'lucro_12m':       'lucro_12m',
        'venda_12m':       'venda_12m',
        'media_venda':     'venda_12m',   # média = venda_12m/12 → mesma ordenação relativa
        'recencia':        'recencia_dias',
        'frequencia':      'frequencia_12m',
        'cliente':         'cliente',
    }
    sort_attr = sort_map.get(sort, 'lucro_perdido_proj')
    reverse = (direction == 'desc')
    filtrados = sorted(filtrados, key=lambda c: (c.get(sort_attr) is None, c.get(sort_attr) or 0), reverse=reverse)

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
    clientes = _carregar_carteira_full()
    return jsonify(_filtrar_carteira(clientes, request.args))


@app.route('/api/carteira/cliente/<int:codcli>')
@login_required
def api_carteira_cliente(codcli):
    """Drill 360°: dados do cliente + histórico mensal 12m + top 5 categorias."""
    key = cache_key_for_user(f'carteira:cliente:{codcli}')
    cached = _cache_get(key)
    if cached:
        return jsonify(cached)

    # Cliente vem do cache da carteira full
    clientes = _carregar_carteira_full()
    cliente = next((c for c in clientes if c['codcli'] == codcli), None)
    if not cliente:
        return jsonify({'ok': False, 'error': 'Cliente não encontrado na carteira'}), 404

    rbac = aplicar_rbac_dax()
    rbac_frag = f" && {rbac}" if rbac else ""
    # Patch G.2: RBAC nas 3 tabelas + cálculo VL/Lucro alinhado RCA
    rbac_d = rbac_devol_dax()
    rbac_d_frag = f" && {rbac_d}" if rbac_d else ""
    rbac_da = rbac_devol_av_dax()
    rbac_da_frag = f" && {rbac_da}" if rbac_da else ""

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


@app.route('/api/carteira/csv')
@login_required
def api_carteira_csv():
    """Export CSV streaming. Reutiliza _filtrar_carteira pra cobrir TODOS os filtros
    (multi-status, multi-segmento, uf, cidade, time, busca codusur/vendedor, etc)."""
    from datetime import date as _date
    modo = request.args.get('modo', 'personalizada')

    clientes = _carregar_carteira_full()
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
        yield '﻿'  # BOM UTF-8
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

    seg_arg = request.args.get('segmento', '').replace(',', '+') or 'todos'
    nome = f"carteira_{seg_arg}_{_date.today().isoformat()}.csv"
    return Response(
        stream_with_context(gerar()),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{nome}"'},
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
        title=f"Carteira Multpel {_date.today().isoformat()}",
    )
    styles = getSampleStyleSheet()
    titulo_style = ParagraphStyle('titulo', parent=styles['Heading1'], fontSize=14, alignment=TA_LEFT, textColor=colors.HexColor('#0a0e17'))
    sub_style = ParagraphStyle('sub', parent=styles['Normal'], fontSize=8, textColor=colors.HexColor('#475569'))

    story = []
    story.append(Paragraph('<b>Multpel Analytics</b> — Carteira', titulo_style))
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


@app.route('/api/carteira/pdf')
@login_required
def api_carteira_pdf():
    """Export PDF da carteira filtrada — colunas pedidas pelo diretor."""
    from datetime import date as _date

    clientes = _carregar_carteira_full()
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
    seg_arg = request.args.get('segmento', '').replace(',', '+') or 'todos'
    nome = f"carteira_{seg_arg}_{_date.today().isoformat()}.pdf"
    return Response(
        pdf_bytes,
        mimetype='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="{nome}"'},
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
        codsupervisor = session.get('codsupervisor')

    key = ':'.join([
        'multpel', 'vendedores:ranking:v1',
        f"role={role or 'anon'}",
        f"usur={codusur if codusur is not None else '-'}",
        f"supv={codsupervisor if codsupervisor is not None else '-'}",
    ])
    cached = _cache_get(key)
    if cached:
        return cached

    if role == 'admin':
        rbac = ''
    elif codusur is not None:
        rbac = f"FATURAMENTO_VENDAS[CODUSUR] = {int(codusur)}"
    elif codsupervisor is not None:
        rbac = f"FATURAMENTO_VENDAS[CODSUPERVISOR] = {int(codsupervisor)}"
    else:
        rbac = ''
    rbac_frag = f" && {rbac}" if rbac else ""

    queries = {
        'vendas_atual': f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODUSUR],
    FILTER(FATURAMENTO_VENDAS, FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12){rbac_frag}),
    "VendaLiq",  [VENDA LIQUIDA],
    "LucroTotal",[LUCRO TOTAL]
)""",
        'vendas_anterior': f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODUSUR],
    FILTER(FATURAMENTO_VENDAS,
        FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -24)
        && FATURAMENTO_VENDAS[DTSAIDA] < EDATE(TODAY(), -12){rbac_frag}),
    "VendaLiqAnt", [VENDA LIQUIDA]
)""",
        'metricas': f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODUSUR],
    FILTER(FATURAMENTO_VENDAS, FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12){rbac_frag}),
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
        try:
            sup_id = int(supervisor)
            filtrados = [v for v in filtrados if v.get('codsupervisor') == sup_id]
        except ValueError:
            pass
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
        'nome':          r.get('NOME'),
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
    key = cache_key_for_user('categorias:ranking:v1')
    cached = _cache_get(key)
    if cached:
        return jsonify(cached)

    rbac = aplicar_rbac_dax()
    rbac_frag = f" && {rbac}" if rbac else ""

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

    key = cache_key_for_user(f'categoria:clientes:{codepto}', {'limit': limit})
    cached = _cache_get(key)
    if cached:
        return jsonify(cached)

    rbac = aplicar_rbac_dax()
    rbac_frag = f" && {rbac}" if rbac else ""

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
    fornecedor = request.args.get('fornecedor')  # CODFORNECPRINC

    key = cache_key_for_user('mix:abandonado', {'dias': dias, 'codepto': codepto or '', 'fornecedor': fornecedor or ''})
    cached = _cache_get(key)
    if cached:
        return jsonify(cached)

    rbac = aplicar_rbac_dax()
    rbac_frag = f" && {rbac}" if rbac else ""
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
        FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -12){rbac_frag}{filtros_str}),
    "UltimaCompra", MAX(FATURAMENTO_VENDAS[DTSAIDA]),
    "VendaCat12m",  [VENDA LIQUIDA],
    "LucroCat12m",  [LUCRO TOTAL]
)"""
    token = get_token_cached()
    payload = retry_dax(execute_dax)(token, query)
    rows = clean_rows(_todas_linhas(payload))

    from datetime import date as _date
    hoje = _date.today()
    mapa = _carregar_deptos_map()
    deptos_nomes = mapa['deptos']
    carteira = _carregar_carteira_full()
    carteira_idx = {c['codcli']: c for c in carteira}

    out = []
    for r in rows:
        cc = r.get('CODCLI')
        cd = r.get('CODEPTO')
        ultima = r.get('UltimaCompra')
        if not ultima or cc is None:
            continue
        try:
            d_ultima = _date.fromisoformat(str(ultima)[:10])
        except ValueError:
            continue
        dias_sem = (hoje - d_ultima).days
        if dias_sem < dias:
            continue  # ainda dentro da janela — não é abandonado
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
        })
    out.sort(key=lambda x: x['lucro_cat_12m'], reverse=True)
    resp = {
        'ok': True,
        'dias': dias,
        'total': len(out),
        'rows': out[:limit],
    }
    _cache_set(key, resp, 'dax_agregado')
    return jsonify(resp)


def _carregar_cohort_full(periodo_meses=12, codusur=None):
    """Carrega cohort data completo. Cache 24h por chave (RBAC + periodo + codusur).

    Retorna dict {mes_aquisicao: {mes_relativo: set(codcli)}} pronto pra matriz_cohort.
    """
    key = cache_key_for_user('cohort:full', {'periodo': periodo_meses, 'codusur': codusur or ''})
    cached = _cache_get(key)
    if cached:
        # Reidratar sets (JSON não tem set)
        return {aq: {int(rel): set(ids) for rel, ids in buckets.items()}
                for aq, buckets in cached.items()}

    rbac = aplicar_rbac_dax()
    rbac_frag = f" && {rbac}" if rbac else ""
    filtro_vend = f" && FATURAMENTO_VENDAS[CODUSUR] = {int(codusur)}" if codusur else ""

    # 1 query: todas as compras (codcli, anomes) no período
    query = f"""EVALUATE
SUMMARIZECOLUMNS(
    FATURAMENTO_VENDAS[CODCLI],
    CALENDARIO[AnoMes],
    FILTER(FATURAMENTO_VENDAS,
        FATURAMENTO_VENDAS[DTSAIDA] >= EDATE(TODAY(), -{periodo_meses + 12}){rbac_frag}{filtro_vend})
)"""
    # Pegamos +12m a mais pra ter histórico antes do cohort começar (necessário pra
    # identificar quem é "novo" no período vs quem já comprava antes).

    token = get_token_cached()
    payload = retry_dax(execute_dax)(token, query)
    rows = clean_rows(_todas_linhas(payload))

    # Monta compras_por_cliente = {codcli: [meses 'YYYY-MM']}
    compras_por_cliente = {}
    for r in rows:
        cc = r.get('CODCLI')
        am = r.get('AnoMes')  # ex: 202504
        if cc is None or am is None:
            continue
        am_str = str(am)
        mes_iso = f'{am_str[:4]}-{am_str[4:6]}'
        compras_por_cliente.setdefault(cc, []).append(mes_iso)

    # Filtra clientes cujo cohort cai DENTRO do periodo (ignora cohorts > periodo_meses atrás)
    from datetime import date as _date
    hoje = _date.today()
    corte_y = hoje.year - (periodo_meses // 12)
    corte_m = hoje.month - (periodo_meses % 12)
    if corte_m <= 0:
        corte_y -= 1
        corte_m += 12
    corte_str = f'{corte_y:04d}-{corte_m:02d}'

    compras_no_periodo = {}
    for cc, meses in compras_por_cliente.items():
        primeiro = min(meses)
        if primeiro >= corte_str:
            compras_no_periodo[cc] = meses

    cohorts_data = cohort.cohort_de_compras(compras_no_periodo)

    # Serializa pro cache (set → list)
    serializable = {aq: {str(rel): sorted(ids) for rel, ids in buckets.items()}
                    for aq, buckets in cohorts_data.items()}
    _cache_set(key, serializable, 'metadata')  # 24h
    return cohorts_data


@app.route('/api/tendencias/cohort')
@login_required
def api_tendencias_cohort():
    """Cohort retention matriz. Filtros: periodo (12m/24m), vendedor.
    Segmento RFM: TODO (Fase 2)."""
    try:
        periodo = int(request.args.get('periodo', '12m').rstrip('m'))
        periodo = max(3, min(periodo, 24))
    except (ValueError, TypeError):
        periodo = 12
    codusur = request.args.get('vendedor')

    cohorts_data = _carregar_cohort_full(periodo_meses=periodo, codusur=codusur)
    matriz = cohort.matriz_cohort(cohorts_data, meses_max=periodo)
    return jsonify({
        'ok': True,
        'periodo': f'{periodo}m',
        'cohorts': matriz,
    })


@app.route('/api/tendencias/cohort/<aquisicao>/<int:mes_relativo>/clientes')
@login_required
def api_cohort_drill(aquisicao, mes_relativo):
    """Drill: lista clientes do bucket (aquisicao, M+mes_relativo)."""
    try:
        periodo = int(request.args.get('periodo', '12m').rstrip('m'))
        periodo = max(3, min(periodo, 24))
    except (ValueError, TypeError):
        periodo = 12
    codusur = request.args.get('vendedor')

    cohorts_data = _carregar_cohort_full(periodo_meses=periodo, codusur=codusur)
    codclis = cohort.clientes_no_bucket(cohorts_data, aquisicao, mes_relativo)
    # Enriquece via carteira_full
    carteira = _carregar_carteira_full()
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
        "cron_enabled, cron_horario::text, cron_frequencia, criado_em, email_cc, segmentos_rfm "
        "FROM multpel_users ORDER BY ativo DESC, nome"
    )
    users = []
    for r in cur.fetchall():
        users.append({
            'id': r[0], 'nome': r[1], 'email': r[2], 'role': r[3],
            'codusur': r[4], 'codsupervisor': r[5], 'telefone': r[6], 'ativo': r[7],
            'cron_enabled': r[8], 'cron_horario': r[9], 'cron_frequencia': r[10],
            'criado_em': str(r[11])[:10] if r[11] else None,
            'email_cc': r[12] if r[12] is not None else [],
            'segmentos_rfm': r[13] or '',
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
    codsupervisor = data.get('codsupervisor') or None
    telefone = (data.get('telefone') or '').strip() or None
    cron_enabled = bool(data.get('cron_enabled', False))
    cron_horario = data.get('cron_horario') or '08:00'
    cron_frequencia = data.get('cron_frequencia') or 'diaria'

    # Patch J — destinatários CC (lista de emails extras)
    try:
        emails_cc = _normalizar_emails_cc(data.get('email_cc') or [], email_principal=email)
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400

    # Patch K — filtro de segmento RFM (vazio = carteira completa)
    segmentos_rfm = _normalizar_segmentos_rfm(data.get('segmentos_rfm'))

    if role == 'vendedor' and not codusur:
        return jsonify({'ok': False, 'error': 'Vendedor exige codusur'}), 400
    if role == 'supervisor' and not codsupervisor:
        return jsonify({'ok': False, 'error': 'Supervisor exige codsupervisor'}), 400

    senha = (data.get('senha') or '').strip() or secrets.token_urlsafe(10)

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO multpel_users
               (nome, email, password_hash, role, codusur, codsupervisor, telefone,
                cron_enabled, cron_horario, cron_frequencia, email_cc, segmentos_rfm,
                must_change_password)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true)
               RETURNING id""",
            (nome, email, generate_password_hash(senha), role,
             int(codusur) if codusur else None,
             int(codsupervisor) if codsupervisor else None,
             telefone, cron_enabled, cron_horario, cron_frequencia,
             Json(emails_cc), segmentos_rfm)
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
                         'email_cc', 'segmentos_rfm']
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
            sets.append(f"{k} = %s")
            valores.append(v)
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


@app.route('/api/admin/enviar-relatorio/<int:user_id>', methods=['POST'])
@admin_required
def api_admin_enviar_relatorio(user_id):
    resultado = enviar_relatorio_email(user_id)
    status = 200 if resultado.get('ok') else 500
    return jsonify(resultado), status


# ── Error handler ──
@app.errorhandler(500)
def erro_500(e):
    tb = traceback.format_exc()
    log_request(request.path, parametros=dict(request.args), erro=tb[:2000])
    if request.path.startswith('/api/'):
        return jsonify({'ok': False, 'error': 'Erro interno'}), 500
    return Response('Erro interno do servidor', status=500)


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
        """SELECT id, nome, email, cron_horario, cron_frequencia
           FROM multpel_users
           WHERE ativo = true AND cron_enabled = true
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
        uid, nome, email, horario, frequencia = row
        # Filtro por frequência (diária roda todo dia; semanal só no dia certo)
        if frequencia in _DIAS_SEMANA_MAP and dia_semana != _DIAS_SEMANA_MAP[frequencia]:
            continue
        try:
            resultado = enviar_relatorio_email(uid)
            print(f"[CRON] user {uid} ({email}): {'OK' if resultado.get('ok') else 'FAIL'} — {resultado.get('error') or resultado.get('message_id')}")
        except Exception as e:
            print(f"[CRON] user {uid} ({email}): erro {e}")
            _log_background(f'cron:erro:user{uid}', erro=str(e)[:500])


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
    print("\n[Multpel Analytics] Backend iniciando...")
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
