"""Bateria de QUALIDADE do Agente de IA do Comercial — contra o modelo e os dados REAIS.

NÃO é pytest: consome a API paga da OpenAI (~US$ 0,002 por pergunta). Roda DENTRO do processo
(chama a rota /api/ia/chat com um contexto de requisição e a sessão do perfil), então não precisa
de instância no ar nem esbarra no cookie `Secure` — a armadilha do smoke do Compras.

    python -X utf8 tests/smoke_ia_comercial_real.py --fonte demo   # base sintética (joga_demo)
    python -X utf8 tests/smoke_ia_comercial_real.py --fonte bi     # Power BI da Multpel (.env) — só leitura

Cada pergunta tem ASSERÇÕES sobre as regressões que já aconteceram ou que o glossário existe para
impedir (docs/comercial/IA_COMERCIAL_CONTEUDO.md §6). A saída lista resposta, consultas feitas,
a conferência de números e o que falhou. Precisa de OPENAI_API_KEY no .env.
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.chdir(RAIZ)
os.environ['MODULOS'] = 'comercial,compras,ia'

import fakeredis  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(RAIZ / '.env')


def _pede(server, sess, pergunta, tela='/'):
    from flask import session
    logs = []
    orig = server._iacom_log
    server._iacom_log = lambda *a, **k: logs.append(k)
    t0 = time.time()
    try:
        with server.app.test_request_context('/api/ia/chat', method='POST',
                                             json={'pergunta': pergunta, 'tela': tela}):
            session.update(sess)
            r = server.api_iacom_chat()
            if isinstance(r, tuple):
                return {'erro': f'HTTP {r[1]}', 'texto': ''}
            tokens = []
            for ch in r.response:
                ch = ch.decode() if isinstance(ch, bytes) else ch
                for linha in ch.split('\n\n'):
                    if linha.startswith('data: ') and linha[6:] != '[DONE]':
                        o = json.loads(linha[6:])
                        tokens.append(o.get('token', ''))
    finally:
        server._iacom_log = orig
    lg = logs[-1] if logs else {}
    return {'texto': ''.join(tokens), 's': round(time.time() - t0, 1),
            'consultas': lg.get('consultas'), 'conferencia': lg.get('conferencia')}


def _nao(txt, padrao):
    return re.search(padrao, txt, re.I) is None


def bateria(fonte):
    import server
    server._R = fakeredis.FakeRedis(decode_responses=True)
    server._R_LOGIN = server._R
    if fonte == 'demo':
        server.CONFIG['data_source'] = 'postgres'
    import ia_comercial as iacom

    diretor = {'user_id': -101, 'role': 'viewer', 'nome': 'Diretoria (bateria)', 'areas': ['comercial']}
    ctx = server._iacom_panorama(diretor)
    if fonte == 'bi':
        # §6 item 1 — a pergunta real do João (25/09/2026)
        caso_cod, caso_nome, sup_cod = 29, 'JULIANO', 17
    else:
        # demo: o 1º do topo do campo (o `explique` automático saiu com as sugestões por grupo)
        topo = ((ctx.get('performance') or {}).get('universos') or {}).get('campo', {}).get('topo') or [{}]
        caso_cod, caso_nome, sup_cod = topo[0].get('codusur'), (topo[0].get('nome') or '').split()[0], None
    with server._ia_sessao(diretor):
        vmap = server._carregar_vendedores_map()
        if sup_cod is None:
            sup_cod = int((vmap.get(str(caso_cod)) or {}).get('codsupervisor') or 0)
        fora = next(int(k) for k, v in vmap.items() if str(k).isdigit() and v.get('bloqueio') != 'S'
                    and v.get('codsupervisor') not in (sup_cod, None))
    supervisor = {'user_id': -102, 'role': 'supervisor', 'codsupervisor': sup_cod, 'codsupervisores': [sup_cod],
                  'nome': 'Supervisor (bateria)', 'areas': ['comercial']}
    fundo = []
    for u in ((ctx.get('performance') or {}).get('universos') or {}).values():
        fundo += [str(x['codusur']) for x in u['fundo']]
    metas_estavel = (ctx.get('metas') or {}).get('estavel')

    casos = [
        (diretor, '/performance',
         f'Por que a cobertura do {caso_cod} {caso_nome} é diferente no Gerencial e na Performance?',
         [('mesma régua em datas diferentes (hoje × fim do mês)',
           lambda t: re.search(r'hoje', t, re.I) and re.search(r'fim do m[êe]s|m[êe]s fechado', t, re.I)),
          ('cita o código', lambda t: str(caso_cod) in t),
          ('fala da nota nas faixas / meta 100% / informativa', lambda t: re.search(r'85%|100%|faixa|informativ', t, re.I)),
          ('não fala de índice ABC', lambda t: _nao(t, r'índice|esperados'))]),
        (diretor, '/recuperacao', 'Quanto dinheiro estamos deixando na mesa?',
         [('usa o dinheiro na mesa', lambda t: re.search(r'dinheiro na mesa', t, re.I)),
          ('não soma com a receita perdida acumulada', lambda t: _nao(t, r'(somad|total).{0,40}receita perdida'))]),
        (diretor, '/performance', 'Quem está no fim da Performance e em quê?',
         [('cita alguém do fundo com código — ou diz que ninguém tem nota',
           lambda t: any(c in t for c in fundo) if fundo else re.search(r'nenhum|ninguém|sem nota', t, re.I)),
          ('não culpa indicador sem meta', lambda t: _nao(t, r'(derruba|tira|puxa).{0,40}(rentabilidade|receita|mix).{0,30}sem meta'))]),
        (diretor, '/gerencial', 'Quantos vendedores estão abaixo do limiar de cobertura? Isso é grave?',
         [('declara o limiar de 85% (piso da nota)', lambda t: re.search(r'85', t)),
          ('aponta QUEM está mais longe (código)', lambda t: re.search(r'\b\d{2,4} [A-ZÁÉÍÓÚÂÊÔÃÕÇ]{3,}', t))]),
        (diretor, '/metas', 'Vamos bater a meta do mês?',
         [('respeita a projeção instável' if not metas_estavel else 'responde pela projeção',
           (lambda t: _nao(t, r'n[ãa]o (vai|v[ãa]o) bater')) if not metas_estavel
           else (lambda t: re.search(r'proje', t, re.I)))]),
        (supervisor, '/vendedores', f'Como está o vendedor {fora}?',
         [('recusa por escopo', lambda t: re.search(r'escopo|acesso|permiss', t, re.I)),
          ('não traz as réguas do vendedor de fora', lambda t: _nao(t, r'Gerencial.*\d+,\d%.*Vendedores'))]),
        (supervisor, '/recuperacao', 'Onde meu time perde dinheiro?',
         [('fala do time dele', lambda t: str(sup_cod) in t or 'time' in t.lower()),
          ('cita vendedores com código', lambda t: re.search(r'\b\d{2,4} [A-ZÁÉÍÓÚÂÊÔÃÕÇ]{3,}', t))]),
    ]
    falhas = 0
    for sess, tela, q, checks in casos:
        r = _pede(server, sess, q, tela)
        print(f"\n{'=' * 100}\n[{sess['role']}] {q}\n({r.get('s')} s · consultas={r.get('consultas')} · "
              f"conferência={r.get('conferencia') or 'ok'})\n{'-' * 100}\n{r['texto'] or r.get('erro')}")
        for nome, fn in checks:
            ok = bool(fn(r['texto']))
            falhas += 0 if ok else 1
            print(f"  {'✅' if ok else '❌'} {nome}")
    print(f"\n{'=' * 100}\nFALHAS: {falhas}")
    return falhas


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--fonte', choices=('demo', 'bi'), default='demo')
    sys.exit(1 if bateria(ap.parse_args().fonte) else 0)
