"""CRM leve — plano de ação por cliente (pedido do João Victor, 29/09/2026 — PLANO_MELHORIAS §12).

Por que existe: a lista do dia (Próximo Pedido) diz QUEM contatar, mas não guardava O QUE foi feito.
Para o gestor, é saber quais clientes receberam tratativa; para o vendedor, o histórico de ações.
A loja da Matriz já usa o painel no dia a dia — o registro acontece onde eles já trabalham.

Regras (decididas por ele):
- 6 status de um toque; descrição opcional;
- só o RETORNO AGENDADO aceita data futura (e exige data); o retorno de hoje (ou atrasado) aparece
  EM DESTAQUE na lista do dia;
- quem tem acesso à lista registra (a porta de escopo é a das telas — `_carteira_no_escopo`);
- o acompanhamento ZERA quando o cliente compra: registro feito até o dia da última compra vira
  "acompanhamento anterior" (nada é apagado — é o histórico do que levou à recuperação).

Funções PURAS — sem Flask nem banco. Testes em tests/test_plano_cliente.py.
"""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

FUSO = ZoneInfo('America/Sao_Paulo')
JANELA_EXCLUSAO = timedelta(hours=24)   # autor exclui o próprio registro até aqui; admin, sempre

STATUS = [
    ('ligacao_feita', 'Ligação feita'),
    ('sem_contato', 'Sem contato'),
    ('retorno_agendado', 'Retorno agendado'),
    ('pedido_prometido', 'Pedido prometido'),
    ('nao_compra_mais', 'Não compra mais'),
    ('transferir', 'Transferir'),
]
ROTULO = dict(STATUS)
RETORNO = 'retorno_agendado'
MAX_DESCRICAO = 500
MAX_DIAS_ATRAS = 60          # registrar ação de até 2 meses atrás (esqueceu de anotar); além disso é erro


def _data(v):
    if v is None or v == '':
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return False


def hora_local(dt):
    """criado_em do banco (TIMESTAMPTZ) → hora de Brasília, sem fuso. O banco é outra stack e roda
    em UTC: sem converter, a tela mostrava 3 h adiantado e a DATA virava às 21 h (05/10/2026).
    Naive fica como está (não há como saber o fuso)."""
    if isinstance(dt, datetime) and dt.tzinfo is not None:
        return dt.astimezone(FUSO).replace(tzinfo=None)
    return dt


def pode_excluir(reg, user_id, admin, agora):
    """Admin exclui qualquer registro; o autor, o PRÓPRIO até 24 h depois de registrar (erro de
    digitação aparece na hora — um "não compra mais" antigo não some porque mudaram de ideia)."""
    if admin:
        return True
    if user_id is None or reg.get('autor_id') != user_id:
        return False
    cr = reg.get('criado_em')
    if not isinstance(cr, datetime):
        return False
    if cr.tzinfo is None:
        cr = cr.replace(tzinfo=timezone.utc) if agora.tzinfo else cr
    return agora - cr <= JANELA_EXCLUSAO


def validar(dados, hoje):
    """(registro_limpo, erro). `dados` = {status, data_acao?, descricao?}."""
    status = (dados or {}).get('status')
    if status not in ROTULO:
        return None, 'status inválido'
    d = _data((dados or {}).get('data_acao'))
    if d is False:
        return None, 'data inválida (AAAA-MM-DD)'
    desc = str((dados or {}).get('descricao') or '').strip()
    if len(desc) > MAX_DESCRICAO:
        return None, f'descrição com mais de {MAX_DESCRICAO} caracteres'
    if status == RETORNO:
        if d is None:
            return None, 'retorno agendado precisa da data do retorno'
        if d < hoje:
            return None, 'retorno agendado precisa ser de hoje em diante'
    else:
        d = d or hoje
        if d > hoje:
            return None, 'só o retorno agendado pode ter data futura'
        if d < hoje - timedelta(days=MAX_DIAS_ATRAS):
            return None, f'data de mais de {MAX_DIAS_ATRAS} dias atrás'
    return {'status': status, 'data_acao': d, 'descricao': desc}, None


def separar(registros, ultima_compra):
    """(atual, anteriores), cada um do mais recente para o mais antigo. Registro feito ATÉ o dia da
    última compra pertence ao acompanhamento que a compra encerrou."""
    ult = _data(ultima_compra) or None
    ordem = sorted(registros or [], key=lambda r: (r['criado_em'], r.get('id') or 0), reverse=True)
    if not ult:
        return ordem, []
    atual = [r for r in ordem if _data(hora_local(r['criado_em'])) > ult]
    anteriores = [r for r in ordem if _data(hora_local(r['criado_em'])) <= ult]
    return atual, anteriores


def resumo(atual, hoje):
    """O selo da linha: {n, ultimo, retorno, retorno_situacao, destaque}. `atual` do mais recente
    para o mais antigo. O retorno só vale se o ÚLTIMO registro é o agendamento — qualquer registro
    depois dele (ligou, não atendeu…) resolve o retorno."""
    if not atual:
        return {'n': 0, 'ultimo': None, 'retorno': None, 'retorno_situacao': None, 'destaque': False}
    u = atual[0]
    ultimo = {'status': u['status'], 'rotulo': ROTULO.get(u['status'], u['status']),
              'data_acao': _data(u['data_acao']).isoformat(), 'autor_nome': u.get('autor_nome')}
    retorno = situacao = None
    if u['status'] == RETORNO:
        d = _data(u['data_acao'])
        retorno = d.isoformat()
        situacao = 'hoje' if d == hoje else ('atrasado' if d < hoje else 'futuro')
    return {'n': len(atual), 'ultimo': ultimo, 'retorno': retorno, 'retorno_situacao': situacao,
            'destaque': situacao in ('hoje', 'atrasado')}


def serializar(r):
    """Registro do banco → JSON da tela."""
    return {'id': r.get('id'), 'status': r['status'], 'rotulo': ROTULO.get(r['status'], r['status']),
            'data_acao': _data(r['data_acao']).isoformat(), 'descricao': r.get('descricao') or '',
            'autor_nome': r.get('autor_nome') or '—',
            'criado_em': hora_local(r['criado_em']).isoformat(timespec='minutes')
            if isinstance(r['criado_em'], datetime) else str(r['criado_em']),
            'pode_excluir': bool(r.get('pode_excluir'))}
