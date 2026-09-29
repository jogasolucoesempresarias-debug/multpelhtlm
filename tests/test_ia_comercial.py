"""Gate do Agente de IA do COMERCIAL (Fase 1 — gestão, na demo).

Duas metades:
- motor PURO (`ia_comercial.py`): regras de leitura que o modelo não pode errar — pontos que cada
  indicador tira da nota, "sem meta" nunca como vilão, metas instáveis no começo do mês, limiar
  do Gerencial × mediana, índice tirado do texto renderizado, resolução de busca no escopo;
- integração (`server.py`, modo postgres = base sintética da demo): 3 estados, guarda de área,
  escopo das consultas, `_ia_rota` herdando a sessão sem vazar, SSE com consultas (OpenAI falso).

O modelo de verdade NÃO roda aqui (API paga): a bateria real é `tests/smoke_ia_comercial_real.py`.
"""
import json
from pathlib import Path

import pytest

import ia_comercial as iacom
from tests.conftest import _criar_usuario, _remover_usuario, login_as


# ══════════════════════════════════ motor puro ══════════════════════════════════
PESOS = {'rentabilidade': 35, 'cobertura': 25, 'mix': 20, 'receita': 10, 'frequencia': 10}


def _linha_perf(cod=1, nota=5.0, notas=None, valores=None, posicao=1, universo='campo', sup=10, **kw):
    notas = notas or {'cobertura': 4.0, 'frequencia': 8.0, 'rentabilidade': None, 'mix': None, 'receita': None}
    return {'codusur': cod, 'nome': f'VEND {cod}', 'time': f'TIME {sup}', 'codsupervisor': sup,
            'universo': universo, 'posicao': posicao, 'nota': nota, 'parcial': True, 'peso_medido': 0.35,
            'notas': notas, 'valores': valores or {'cobertura': 1.0, 'frequencia': 3.0}, **kw}


def test_pontos_por_indicador_somam_10_menos_a_nota():
    """A mesma renormalização de `performance_comercial.nota`: a soma dos pontos perdidos é
    exatamente 10 − nota — o gestor consegue conferir a conta na tela."""
    import performance_comercial as pc
    valores = {'cobertura': 1.0, 'frequencia': 2.2, 'rentabilidade': 0.9, 'mix': None, 'receita': 1.05}
    n = pc.nota(valores, pc.CAMPO, PESOS)
    tira, sem = iacom.pontos_por_indicador({'notas': n['notas'], 'valores': valores}, PESOS)
    assert sum(t[1] for t in tira) == pytest.approx(10 - n['nota'], abs=0.05)
    assert sem == ['mix']


def test_indicador_SEM_META_nunca_e_o_que_mais_tira_nota():
    """Ajuste (d) do Gabriel: indicador sem meta/sem medida aparece como 'sem meta', nunca como o
    que derruba a nota — seria acusar alguém de uma meta que ninguém cadastrou."""
    l = _linha_perf(notas={'cobertura': 9.0, 'frequencia': 9.5, 'rentabilidade': None, 'mix': None, 'receita': None})
    tira, sem = iacom.pontos_por_indicador(l, PESOS)
    assert {t[0] for t in tira} == {'cobertura', 'frequencia'}
    assert set(sem) == {'rentabilidade', 'mix', 'receita'}
    txt = iacom._r_perf_linha(iacom._linha_perf(l, PESOS))
    assert 'o que mais tira nota: cobertura' in txt
    assert 'sem meta/sem medida (não tiram ponto): rentabilidade, mix, receita' in txt
    # nota cheia: nada tira ponto → não aponta vilão nenhum
    l10 = _linha_perf(nota=10, notas={'cobertura': 10.0, 'frequencia': 10.0, 'rentabilidade': None,
                                      'mix': None, 'receita': None})
    assert 'o que mais tira nota' not in iacom._r_perf_linha(iacom._linha_perf(l10, PESOS))


def _metas(decorridos, mes=21, pct_venda=0.8, meta=100000.0):
    def lm(meta_v, pp):
        return {'meta': meta_v, 'realizado': 50000.0, 'falta': 50000.0, 'necessidade_dia': 1000.0,
                'projecao': 80000.0, 'pct_realizado': 0.5, 'pct_projecao': pp}
    sup = {'codsupervisor': 17, 'nome': 'AFONSO', 'venda': lm(meta, pct_venda), 'rentabilidade': lm(0, None),
           'clientes': lm(0, None), 'mix': lm(0, None), 'margem': 0.18}
    return {'ok': True, 'ano': 2026, 'mes': 9, 'dias': {'mes': mes, 'decorridos': decorridos, 'restantes': mes - decorridos},
            'supervisores': [sup], 'total': {'venda': lm(meta, pct_venda)}}


def test_metas_no_comeco_do_mes_sao_instaveis_e_nao_apontam_quem_nao_bate():
    """Ajuste (b): com poucos dias úteis a projeção é instável. Antes de ~1/3 do mês, declarar e
    não apontar quem não vai bater."""
    vend = {17: {'vendedores': [{'codusur': 29, 'nome': 'J', 'venda': {'pct_projecao': 0.5, 'falta': 1}}]}}
    b = iacom.bloco_metas(_metas(decorridos=5), vend)
    assert b['estavel'] is False and b['times_nao_batem'] == [] and b['vendedores_nao_batem'] == []
    txt = iacom._r_metas(b)
    assert 'PROJEÇÃO INSTÁVEL' in txt and 'dia útil 5 de 21' in txt
    assert 'NÃO vão bater' not in txt
    b = iacom.bloco_metas(_metas(decorridos=8), vend)            # 8 ≥ 21/3
    assert b['estavel'] is True
    assert [t['cod'] for t in b['times_nao_batem']] == [17]
    assert [v['cod'] for v in b['vendedores_nao_batem']] == [29]
    assert 'NÃO vão bater' in iacom._r_metas(b)


def test_sem_meta_cadastrada_e_sem_meta_nao_zero_por_cento():
    b = iacom.bloco_metas(_metas(decorridos=15, meta=0.0, pct_venda=None))
    assert b['tem_meta'] is False and b['times_nao_batem'] == []
    txt = iacom._r_metas(b)
    assert "'sem meta' (não é 0%)" in txt and 'sem meta' in txt


def _ger():
    v = lambda i, c, sup=10, **k: {'id': i, 'nome': f'V{i}', 'time': 'T', 'codsupervisor': sup,
                                   'cobertura_clientes': c, 'cobertura_valor': 0.8, 'total_clientes': 40,
                                   'amostra_pequena': False, **k}
    return {'ok': True, 'coberto_dias': 30, 'limiar_pct': 60.0, 'abaixo_do_limiar': {'times': 9, 'vendedores': 74},
            'empresa': {'total_clientes': 8000, 'cobertura_clientes': 0.352, 'cobertura_valor': 0.819,
                        'cobertura_ciclo': 0.46, 'base_morta': 4000, 'receita_em_risco': 2645803.44},
            'times': [{'id': None, 'nome': 'Sem time', 'cobertura_clientes': 0.05, 'amostra_pequena': False},
                      {'id': 10, 'nome': 'T', 'cobertura_clientes': 0.3, 'cobertura_valor': 0.7,
                       'total_clientes': 500, 'amostra_pequena': False}],
            'vendedores': [v(1, 0.30), v(2, 0.40), v(3, 0.50), v(4, 0.0), v(999, 0.08), v(5, 0.9, amostra_pequena=True),
                           v(6, 0.0, sup=34)]}


def test_gerencial_declara_a_regua_unica_e_o_limiar_de_85():
    """Régua única (João, 29/09/2026): carteira ATIVA de 12 meses, janela de 60 d, qualquer
    vendedor; limiar 85% = piso das faixas (abaixo = nota 0 na cobertura). O bloco traz a MEDIANA
    entre pessoas como referência e não põe 'Sem time' nem RCA bloqueado entre os piores."""
    b = iacom.bloco_gerencial(_ger(), nao_pessoas={999, 34}, bloqueados={4})
    assert b['mediana_pessoas'] == pytest.approx(0.35)          # 0,0 · 0,30 · 0,40 · 0,50 (sem 999, 5, 6)
    assert [t['cod'] for t in b['times_piores']] == [10]
    assert 4 not in [v['cod'] for v in b['vendedores_piores']]
    assert b['bloqueados_com_carteira']['vendedores'] == 1
    txt = iacom._r_gerencial(b)
    assert 'carteira ATIVA de 12 meses' in txt and 'qualquer vendedor' in txt
    assert 'abaixo dele a nota da cobertura é 0' in txt and 'EM REVISÃO' not in txt
    assert 'MEDIANA ENTRE VENDEDORES' in txt and 'OUTRO número' in txt
    assert 'carteira a TRANSFERIR' in txt


def test_o_indice_sai_do_texto_RENDERIZADO_e_bloco_vazio_nao_e_anunciado():
    """Lição do Compras: índice derivado da estrutura anunciava pilar vazio e o agente dizia que o
    painel não tinha algo que tinha (ou prometia o que não tinha)."""
    ctx = iacom.montar_panorama({'role': 'admin'}, {'gerencial': _ger(), 'vendedores': {'ok': True, 'vendedores': []}},
                                erros={'recuperacao': 'HTTP 500'})
    idx = iacom.indice(ctx)
    assert idx == ['cobertura da carteira (Gerencial) com a mediana']      # cadastro vazio não entra
    sp = iacom.system_prompt(ctx)
    assert 'Fora do ar agora (não prometa): recuperacao' in sp


def test_o_glossario_e_as_regras_cobrem_as_reguas_que_o_modelo_confunde():
    sp = iacom.REGRAS + iacom.GLOSSARIO
    for trecho in ('QUATRO sentidos', 'RÉGUA ÚNICA', '"RECEITA EM RISCO" tem TRÊS sentidos', 'NUNCA some',
                   'DINHEIRO NA MESA', 'CADASTRO (de quem o cliente é)', 'mais de 60 dias sem comprar E além do próprio ciclo',
                   'CRÉDITO DA RECUPERAÇÃO é de QUEM VENDEU', 'PARCIAL', 'BRUTO', 'DTENT',
                   'SEMPRE o CÓDIGO', 'RÉGUA e o RECORTE', 'LIMIAR do Gerencial = 85%', 'Não presuma o gênero',
                   'o Gerencial é HOJE', 'fim do mês fechado', 'INFORMATIVA', 'meta de 100%'):
        assert trecho in sp, trecho
    # o que saiu com a régua única: índice ABC, três réguas, limiar "em revisão", escalas provisórias
    for velho in ('ÍNDICE relativo', 'TRÊS réguas', 'São três réguas', 'em discussão', 'EM REVISÃO', 'PROVISÓRIA'):
        assert velho not in sp, velho


def test_regras_da_2a_rodada_com_o_bi_real():
    """Achados do teste do Gabriel (26/09): somou times ("juntos somam R$ 550 mil"), subtraiu
    ("279 não compraram"), leu o realizado de setembro como "mês fechado", comparou a cobertura da
    empresa com a mediana, chamou -0,9 p.p. de queda, recusou ticket médio como "fora do Comercial"
    e copiou o código de cliente do exemplo do prompt."""
    sp = iacom.REGRAS + iacom.GLOSSARIO
    for trecho in ('NUNCA subtraia', 'juntos somam', 'mês CORRENTE até hoje', 'ESTÁVEL',
                   'nunca a cobertura da empresa/time com a mediana', 'NÃO é "fora"', 'Dashboard',
                   'nunca de exemplo', '"PLANO DE AÇÃO"'):
        assert trecho in sp, trecho
    assert '121154' not in sp and 'JULIANO' not in iacom.REGRAS.split('"EXPLIQUE')[0]


def _ctx_sug(role, **blocos):
    return {'perfil': {'role': role}, **blocos}


def test_sugestoes_falam_do_GRUPO_por_perfil_e_nunca_de_uma_pessoa():
    """Feedback do Gabriel: a sugestão com nome de pessoa (1449 VALDELI) repetia sempre e o
    "Explique este número:" era genérico. Agora: a da TELA (régua dela) + as do PERFIL, até 6."""
    cheio = dict(recuperacao={'x': 1}, performance={'x': 1}, gerencial={'empresa': {'cobertura_valor': 0.819,
                 'cobertura_clientes': 0.352}}, metas={'tem_meta': True}, cadastro={'vendedores': [1]},
                 yoy={'quedas': [1]})
    d = iacom.sugestoes(_ctx_sug('viewer', **cheio), '/gerencial')
    assert d[0] == 'Por que a cobertura por valor é 81,9% e por cliente 35,2%?'
    assert 'Qual plano de ação você recomenda para meus supervisores?' in d and len(d) == iacom.MAX_SUGESTOES
    s = iacom.sugestoes(_ctx_sug('supervisor', **cheio), '/recuperacao')
    assert s[0] == 'O que é o dinheiro na mesa e de onde ele sai?'
    assert 'Quais vendedores meus precisam de atenção esta semana?' in s and 'Onde meu time perde dinheiro?' in s
    for lista in (d, s, iacom.sugestoes(_ctx_sug('vendedor', **cheio), '/performance')):
        assert not any(c.isdigit() for q in lista if not q.startswith('Por que a cobertura por valor') for c in q)
        assert 'Explique este número: ' not in lista


def test_sugestao_que_depende_de_bloco_vazio_nao_aparece():
    ctx = {'perfil': {'role': 'admin'}, 'metas': {'tem_meta': False}}
    sug = iacom.sugestoes(ctx, '/metas')
    assert 'Vamos bater o mês? Onde falta?' not in sug
    assert 'Onde a empresa perde dinheiro?' not in sug                  # sem recuperação
    assert 'Quais vendedores mais caíram contra o ano passado?' not in sug
    assert sug == ['Qual plano de ação você recomenda para meus supervisores?']


def test_bloco_vendedores_x_ano_passado():
    """Mesma coluna da tela Vendedores (yoy_receita, 12m × 12m anteriores). Fora: fictício,
    bloqueado, canal e base anterior pequena (RCA novo daria "+∞%")."""
    v = lambda c, yoy, ant=100000.0, nome=None: {'codusur': c, 'nome': nome or f'V{c}', 'yoy_receita': yoy,
                                                 'venda_anterior': ant, 'venda_liq': ant * (1 + yoy)}
    vend = {'ok': True, 'vendedores': [v(1, -0.40), v(2, -0.10), v(3, 0.30), v(4, 0.05), v(999, -0.90),
                                       v(5, -0.80, ant=1000.0), v(6, -0.70), v(7, -0.60, nome='MARTINS E-COMMERCE')]}
    b = iacom.bloco_yoy(vend, nao_pessoas={999}, bloqueados={6})
    assert [x['cod'] for x in b['quedas']] == [1, 2] and [x['cod'] for x in b['altas']] == [3, 4]
    txt = iacom._r_yoy(b)
    assert '== VENDEDORES × ANO PASSADO' in txt and '1 V1: -40,0%' in txt and '+30,0%' in txt
    ctx = iacom.montar_panorama({'role': 'viewer'}, {'vendedores': vend}, nao_pessoas={999}, bloqueados={6})
    assert 'vendedores × ano passado (quedas e altas)' in iacom.indice(ctx)


def test_time_que_nao_e_time_sai_dos_piores():
    """No BI real o plano de ação mandou "trabalhar com os supervisores" do 35 PROSPECÇAO e do 33
    GLEICIANE - EXTERNO (18 clientes)."""
    ger = _ger()
    ger['times'] += [{'id': 35, 'nome': 'PROSPECÇAO', 'cobertura_clientes': 0.09, 'total_clientes': 333,
                      'amostra_pequena': False},
                     {'id': 33, 'nome': 'GLEICIANE - EXTERNO', 'cobertura_clientes': 0.05, 'total_clientes': 18,
                      'amostra_pequena': False}]
    b = iacom.bloco_gerencial(ger)
    assert [t['cod'] for t in b['times_piores']] == [10]
    txt = iacom._r_gerencial(iacom.bloco_gerencial(_ger(), nao_pessoas={999, 34}))
    assert '4 V4 (T): 0,0% dos clientes' in txt and 'confirmar se é RCA novo ou carteira parada' in txt


def test_metas_rotulam_o_periodo_em_andamento():
    """O modelo leu o realizado de setembro (até o dia 26) como "mês fechado 08/2026"."""
    txt = iacom._r_metas(iacom.bloco_metas(_metas(decorridos=18)))
    assert txt.startswith('== METAS DO MÊS CORRENTE 09/2026 — EM ANDAMENTO')
    assert 'ATÉ HOJE (mês corrente, incompleto) — NÃO é o mês fechado' in txt
    assert 'metas do mês' in iacom.indice({'metas': iacom.bloco_metas(_metas(decorridos=18))})


def test_resolver_busca_no_escopo():
    it = [{'cod': 29, 'nome': 'JULIANO PASTORE'}, {'cod': 1507, 'nome': 'JULIANO JOSE', 'inativo': True},
          {'cod': 826, 'nome': 'JULIANO RAMIRO', 'inativo': True}, {'cod': 290, 'nome': 'ANA'}]
    assert iacom.resolver('29', it)[0]['cod'] == 29                        # código EXATO antes do prefixo
    assert iacom.resolver('29 JULIANO PASTORE', it)[0]['cod'] == 29        # o modelo busca "código nome"
    assert iacom.resolver('#29', it)[0]['cod'] == 29
    assert iacom.resolver('juliano', it)[0]['cod'] == 29                   # único ATIVO entre vários
    alvo, cands = iacom.resolver('2', it)
    assert alvo is None and {c['cod'] for c in cands} == {29, 290}
    # prefixo com UM candidato também não escolhe sozinho ("1" não pode virar o time 12)
    assert iacom.resolver('1', [{'cod': 12, 'nome': 'T'}]) == (None, [{'cod': 12, 'nome': 'T'}])
    assert iacom.resolver('xyz', it) == (None, [])
    assert 'NÃO diga que está fora do escopo' in iacom.texto_candidatos('vendedor', 'xyz', [])
    assert 'FORA DO ESCOPO' in iacom.texto_fora_escopo('vendedor', '1')


def test_consulta_vendedor_traz_a_regua_unica_hoje_e_no_fim_do_mes():
    """JULIANO no BI real (29/09/2026): 35 de 45 = 77,8% nos 60 d até 31/08 (Performance/Vendedores)
    e 29 de 46 = 63,0% hoje (Gerencial). É a MESMA régua em duas datas — a consulta diz isso."""
    perf = _linha_perf(cod=29, nota=None, notas={'cobertura': 0.0, 'frequencia': 10.0, 'rentabilidade': None,
                                                 'mix': None, 'receita': None},
                       valores={'cobertura': 35 / 45, 'frequencia': 3.73},
                       detalhe={'base_ativa': 45, 'cobertos': 35, 'clientes_atendidos': 30},
                       informativos=['cobertura'])
    meta = {'mes': 202608, 'pesos': PESOS, 'cobertura_informativa': True, 'cobertura_na_nota_desde': 202611}
    ger = {'cobertura_clientes': 29 / 46, 'clientes_cobertos': 29, 'total_clientes': 46, 'cobertura_valor': 0.9,
           'cobertura_ciclo': 0.787, 'base_morta': 9, 'receita_em_risco': 24164.67, '_coberto_dias': 60}
    vend = {'kpis': {'positivacao_mes': 202608, 'taxa_positivacao': 35 / 45, 'base_coberta': 35, 'base_ativa': 45,
                     'alcance': 30 / 45, 'fora_base': 0, 'fora_base_tipo': {}}}
    txt = iacom.consulta_vendedor(29, 'JULIANO PASTORE', vend=vend, perf=perf, perf_meta=meta, ger=ger,
                                  time=(17, 'AFONSO ES-SUL'))
    for trecho in ('Time (cadastro): 17 AFONSO ES-SUL', 'COBERTURA DA CARTEIRA (régua única',
                   'fim do mês fechado 08/2026: 77,8% (35 de 45) → nota 0',
                   'hoje (Gerencial, 60 d): 63,0% (29 de 46)', 'INFORMATIVA até 11/2026',
                   'sem meta/sem medida (não tiram ponto): rentabilidade, mix, receita'):
        assert trecho in txt, trecho
    for velho in ('TRÊS RÉGUAS', 'índice', 'esperados', 'satura'):
        assert velho not in txt, velho


def test_bloco_performance_declara_faixas_e_mes_de_limpeza():
    perf = {'ok': True, 'mes': 202608, 'pesos': PESOS, 'total_universo': {'campo': 1}, 'escalas': {'campo': {}},
            'cobertura_informativa': True, 'cobertura_na_nota_desde': 202611,
            'linhas': [_linha_perf(cod=1, nota=5.0, valores={'cobertura': 0.8, 'frequencia': 3.0})]}
    txt = iacom._r_performance(iacom.bloco_performance(perf))
    assert 'PROVISÓRIAS' not in txt and 'índice' not in txt
    assert 'cobertura 80,0% da carteira' in txt
    assert 'COBERTURA INFORMATIVA até 11/2026' in txt


# ══════════════════════════════════ integração ══════════════════════════════════
@pytest.fixture
def ia_ligado(monkeypatch):
    monkeypatch.setenv('MODULOS', 'comercial,compras,ia')
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-teste')


@pytest.fixture
def modo_pg(monkeypatch):
    import server
    monkeypatch.setitem(server.CONFIG, 'data_source', 'postgres')
    # a cobertura já VALENDO na nota: no banco de teste não há metas, e com ela informativa (mês de
    # limpeza) a nota de todos ficaria abaixo do peso mínimo — aqui o assunto é o agente, não a limpeza
    monkeypatch.setattr(server, '_cobertura_na_nota_desde', lambda: 200001)
    server._IACOM_CTX.clear()
    yield
    server._IACOM_CTX.clear()


def test_tres_estados_das_rotas(client, usuario_admin, monkeypatch):
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    monkeypatch.setenv('MODULOS', 'comercial,compras')               # Multpel
    j = client.get('/api/ia/status').get_json()
    assert j['ok'] and j['modulo'] is False and j['estado'] == 'off'
    assert client.get('/api/ia/contexto').status_code == 404
    assert client.post('/api/ia/chat', json={'pergunta': 'x'}).status_code == 404
    monkeypatch.setenv('MODULOS', 'comercial,compras,ia')           # oferta: módulo sem chave
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    j = client.get('/api/ia/status').get_json()
    assert j['modulo'] is True and j['disponivel'] is False and j['upsell']
    assert client.get('/api/ia/contexto').status_code == 402
    assert client.post('/api/ia/chat', json={'pergunta': 'x'}).status_code == 402


def test_import_quebrado_nao_derruba_e_rotas_somem(client, usuario_admin, monkeypatch, ia_ligado):
    import server
    monkeypatch.setattr(server, 'iacom', None)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    assert client.get('/api/ia/status').get_json()['modulo'] is False
    assert client.post('/api/ia/chat', json={'pergunta': 'x'}).status_code == 404
    fonte = Path('server.py').read_text(encoding='utf-8')
    ini = fonte.index('    import ia_comercial as iacom\n')
    assert 'except Exception' in fonte[ini:ini + 300] and 'iacom = _iacom_conf = None' in fonte[ini:ini + 300]


def test_usuario_SEM_a_area_comercial_leva_403_no_chat(client, ia_ligado):
    """As rotas internas (`_ia_rota`) pulam o `_guard_comercial`. A proteção é a porta: o
    /api/ia/chat (e o /contexto) passam pela guarda — quem só tem Compras não entra."""
    import server
    email = 'so-compras-ia@teste.local'
    _criar_usuario(email, 'senha123', role='viewer')
    conn = server.get_db()
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE multpel_users SET areas='[\"compras\"]'::jsonb WHERE email=%s", (email,))
    conn.close()
    try:
        login_as(client, email, 'senha123')
        assert client.post('/api/ia/chat', json={'pergunta': 'quanto vendemos?'}).status_code == 403
        assert client.get('/api/ia/contexto').status_code == 403
    finally:
        _remover_usuario(email)


def test_na_multpel_o_widget_nao_carrega_nem_chama_api_ia(client, usuario_admin, monkeypatch):
    """Estado off: o /api/me (que o cabeçalho JÁ chama) não traz `ia`, e o joga-header.js só baixa
    o comercial-ia.js com `ia` no `modulos`, área Comercial e tela do Comercial. Zero requisição."""
    import server
    monkeypatch.setattr(server, 'MODULOS', ['comercial', 'compras'])
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    assert 'ia' not in client.get('/api/me').get_json()['modulos']
    js = Path('static/joga-header.js').read_text(encoding='utf-8')
    corpo = js[js.index('function carregarAgenteIA(me)'):]
    corpo = corpo[:corpo.index('\n  }\n')]
    assert "(me.modulos || []).indexOf('ia') === -1) return;" in corpo
    assert "(me.areas || []).indexOf('comercial') === -1) return;" in corpo
    assert "localAtual() !== 'comercial'" in corpo
    assert "'/static/comercial-ia.js'" in corpo
    assert 'carregarAgenteIA(me);' in js
    # nenhuma página do Comercial carrega o widget direto (só pelo cabeçalho, condicionado)
    for html in Path('.').glob('*.html'):
        assert 'comercial-ia.js' not in html.read_text(encoding='utf-8'), html.name


def test_ia_rota_herda_a_sessao_nao_amplia_escopo_e_nao_vaza(app, modo_pg):
    import server
    from flask import session
    sess_vend = {'user_id': -7, 'role': 'vendedor', 'codusur': 213, 'nome': 't', 'areas': ['comercial']}
    st, _j = server._ia_rota('/api/vendedor/198', sess_vend)
    assert st == 403                                          # outro vendedor: a rota recusa
    st, j = server._ia_rota('/api/vendedor/213', sess_vend)
    assert st == 200 and j['ok']
    with app.test_request_context('/'):
        session.update({'user_id': 1, 'role': 'admin'})
        server._ia_rota('/api/vendedor/213', sess_vend)
        assert session.get('role') == 'admin' and 'codusur' not in session   # a sessão de fora intacta


def test_panorama_e_consultas_no_modo_postgres_por_perfil(app, modo_pg, ia_ligado):
    """Diretor = empresa + times; supervisor = só os RCAs do(s) time(s) dele. As consultas nunca
    ampliam o escopo: supervisor perguntando de vendedor de outro time recebe FORA DO ESCOPO."""
    import server
    adm = {'user_id': -1, 'role': 'viewer', 'nome': 'dir', 'areas': ['comercial']}
    ctx = server._iacom_panorama(adm)
    assert ctx['recuperacao'] and ctx['recuperacao']['nivel_top'] == 'times'
    assert ctx['performance'] and ctx['gerencial'] and ctx['positivacao']
    txt = iacom.renderizar(ctx)
    assert 'EMPRESA inteira' in txt and '== RECUPERAÇÃO' in txt and 'MEDIANA ENTRE VENDEDORES' in txt

    sup = {'user_id': -2, 'role': 'supervisor', 'codsupervisor': 12, 'codsupervisores': [12], 'nome': 'sup',
           'areas': ['comercial']}
    ctx_s = server._iacom_panorama(sup)
    assert ctx_s['recuperacao']['nivel_top'] == 'vendedores'
    with server._ia_sessao(sup):
        do_time = {int(k) for k, v in server._carregar_vendedores_map().items()
                   if str(k).isdigit() and v.get('codsupervisor') == 12}
    assert {r['cod'] for r in ctx_s['recuperacao']['top']} <= do_time
    for un in ctx_s['performance']['universos'].values():
        assert all(x['codsupervisor'] == 12 for x in un['topo'] + un['fundo'])

    # consulta de vendedor do próprio time × de outro time
    meu = sorted(do_time)[0]
    txt = server._iacom_executar('vendedor', str(meu), sup)
    assert 'COBERTURA DA CARTEIRA (régua única' in txt and 'fim do mês fechado' in txt and 'hoje (Gerencial' in txt
    with server._ia_sessao(sup):
        outro = next(int(k) for k, v in server._carregar_vendedores_map().items()
                     if str(k).isdigit() and v.get('codsupervisor') not in (12, None))
    txt = server._iacom_executar('vendedor', str(outro), sup)
    assert 'FORA DO ESCOPO' in txt and 'COBERTURA DA CARTEIRA' not in txt
    with server._ia_sessao(sup):
        outro_time = next(int(k) for k in server._carregar_supervisores_map() if str(k).isdigit() and int(k) != 12)
    txt = server._iacom_executar('time', str(outro_time), sup)
    assert 'FORA DO ESCOPO' in txt and 'COBERTURA DA CARTEIRA' not in txt
    txt = server._iacom_executar('time', '12', sup)
    assert 'CONSULTA time 12' in txt and 'COBERTURA DA CARTEIRA hoje (Gerencial' in txt

    # cliente: um do time dele sim; o busca de fora não aparece (escopo de cadastro)
    with server._ia_sessao(sup):
        cc = server._carteira_no_escopo()[0]['codcli']
    with server._ia_sessao(sup):
        nome_cli = next(c for c in server._carteira_no_escopo() if c['codcli'] == cc)['cliente']
    # o modelo busca "código nome" — a busca da tela casa um OU outro (recusou cliente real em 26/09)
    assert f'CONSULTA cliente {cc}' in server._iacom_executar('cliente', f'{cc} {nome_cli}', sup)
    txt = server._iacom_executar('cliente', str(cc), sup)
    assert f'CONSULTA cliente {cc}' in txt and 'RECUPERAÇÃO' in txt


def test_cache_do_panorama_e_por_usuario():
    import server
    a = server._iacom_chave({'user_id': 1, 'role': 'viewer'})
    b = server._iacom_chave({'user_id': 2, 'role': 'supervisor', 'codsupervisores': [12]})
    assert a != b and 'supervisor' in b and '12' in b


# ── chat com OpenAI FALSO: tool call → status no SSE → resposta; teto de 3 consultas ──
class _Delta:
    def __init__(self, content=None, tool_calls=None):
        self.content, self.tool_calls = content, tool_calls


class _Fn:
    def __init__(self, name, arguments):
        self.name, self.arguments = name, arguments


class _TC:
    def __init__(self, i, id_, name, args):
        self.index, self.id, self.function = i, id_, _Fn(name, args)


class _Chunk:
    def __init__(self, delta):
        self.choices = [type('C', (), {'delta': delta})()]


def _fake_openai(roteiro, chamadas):
    class _Completions:
        def create(self, **kw):
            chamadas.append(kw)
            return iter(roteiro(len(chamadas), kw))

    class _Fake:
        def __init__(self, **kw):
            self.chat = type('X', (), {'completions': _Completions()})()
    return _Fake


def _sse(resp):
    evts = []
    for linha in resp.get_data(as_text=True).split('\n\n'):
        if linha.startswith('data: ') and linha[6:] != '[DONE]':
            evts.append(json.loads(linha[6:]))
    return evts


def test_chat_com_consulta_e_teto_de_3(client, usuario_admin, monkeypatch, ia_ligado):
    import openai
    import server
    monkeypatch.setattr(server, '_iacom_panorama', lambda sess, forcar=False: iacom.montar_panorama(
        {'role': 'admin'}, {'gerencial': _ger()}))
    executadas = []
    monkeypatch.setattr(server, '_iacom_executar', lambda n, b, s: executadas.append((n, b)) or f'CONSULTA {n} {b}: R$ 1.234,00')
    monkeypatch.setattr(server, '_iacom_log', lambda *a, **k: logs.append(k))
    monkeypatch.setattr(server, '_iacom_salvar_contexto', lambda *a, **k: None)
    logs, chamadas = [], []

    def roteiro(n, kw):
        if kw.get('tool_choice') == 'none':
            return [_Chunk(_Delta(content='Resposta final R$ 1.234,00'))]
        # pede sempre uma consulta nova + uma repetida (a repetida não gasta o teto)
        return [_Chunk(_Delta(tool_calls=[_TC(0, f'c{n}', 'vendedor', json.dumps({'busca': str(n)})),
                                          _TC(1, f'd{n}', 'vendedor', json.dumps({'busca': str(n)}))]))]
    monkeypatch.setattr(openai, 'OpenAI', _fake_openai(roteiro, chamadas))
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    r = client.post('/api/ia/chat', json={'pergunta': 'como está o vendedor 1?', 'tela': '/performance',
                                          'filtros': {'time': '12'}})
    assert r.status_code == 200
    evts = _sse(r)
    assert [e['status'] for e in evts if 'status' in e] == ['consultando vendedor 1…', 'consultando vendedor 2…',
                                                            'consultando vendedor 3…']
    assert ''.join(e.get('token', '') for e in evts) == 'Resposta final R$ 1.234,00'
    assert executadas == [('vendedor', '1'), ('vendedor', '2'), ('vendedor', '3')]   # teto 3, sem repetir
    assert chamadas[-1]['tool_choice'] == 'none' and len(chamadas) == 4
    sp = chamadas[0]['messages'][0]['content']
    assert 'tela `/performance`' in sp and 'time=12' in sp and 'ESCOPO INTEIRO' in sp
    # a conferência olha o que as consultas devolveram: o R$ 1.234,00 veio de uma consulta
    assert logs and logs[-1]['conferencia'] is None and len(logs[-1]['consultas']) == 3


def test_tela_e_filtros_viajam_no_corpo_e_nao_na_querystring():
    """As rotas internas leem `request.args`: filtro na URL do chat estreitaria o panorama calado."""
    js = Path('static/comercial-ia.js').read_text(encoding='utf-8')
    assert "fetch('/api/ia/chat', {" in js
    assert 'tela: tela(), filtros: filtros()' in js


def test_ia_comercial_modo_postgres_rota_contexto(client, usuario_admin, modo_pg, ia_ligado):
    """Gate de produto: o /api/ia/contexto responde na DEMO (DATA_SOURCE=postgres) sem tocar o BI
    — a rede de segurança (`execute_dax` levanta em postgres) derrubaria qualquer fonte que vazasse."""
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    r = client.get('/api/ia/contexto?tela=/performance&texto=1')
    assert r.status_code == 200
    j = r.get_json()
    assert j['ok'] and not j['indisponivel'], j.get('indisponivel')
    assert 'carteira em risco × recuperada e dinheiro na mesa' in j['indice']
    assert j['sugestoes'] and j['sugestoes'][0] == 'Como a cobertura da carteira entra na nota?'
    assert '== PERFORMANCE' in j['texto']


def test_seed_de_usuarios_da_demo_tem_as_tres_travas():
    """Escreve no banco de AUTH: só com DEMO_SEED=1, nunca no multpel_db, só em banco 'demo'."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('seed_usuarios_demo', '_seed_demo/seed_usuarios_demo.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert 'DEMO_SEED=1' in m.guardas('joga_demo', {})
    assert 'PRODUÇÃO da Multpel' in m.guardas('multpel_db', {'DEMO_SEED': '1'})
    assert "não tem 'demo'" in m.guardas('outro_db', {'DEMO_SEED': '1'})
    assert m.guardas('joga_demo', {'DEMO_SEED': '1'}) is None
    fatores = [m.fator_meta(c) for c in range(100, 300)]
    assert min(fatores) >= 0.90 and max(fatores) <= 1.30 and len(set(fatores)) > 20   # tem quem bate e quem não
    assert m.fator_meta(213) == m.fator_meta(213)                                        # reprodutível
    src = Path('_seed_demo/seed_usuarios_demo.py').read_text(encoding='utf-8')
    assert 'DEMO_USUARIOS_SENHA' in src and "'viewer'" in src.replace('"', "'")
    assert 'ON CONFLICT (ano, mes, codusur) DO NOTHING' in src                           # nunca sobrescreve meta
    boot = Path('_seed_demo/bootstrap_demo.sh').read_text(encoding='utf-8')
    assert boot.count('seed_usuarios_demo.py') == 2      # 1º boot E redeploy de demo já populada


# ══════════════ 3ª rodada — pedidos do João Victor (26/09/2026) ══════════════
# "Além da performance do vendedor, colocar a performance do time também" · "Vendedores em
# queda, clientes em queda, produtos em queda" — tudo no AGENTE (decisão do Gabriel).

def _perf_time():
    def l(cod, sup, nota, notas, un='campo', pos=1):
        return {'codusur': cod, 'nome': f'V{cod}', 'codsupervisor': sup, 'time': f'TIME {sup}', 'universo': un,
                'posicao': pos, 'nota': nota, 'parcial': True, 'peso_medido': 0.35, 'notas': notas, 'valores': {}}

    def N(cob, freq):
        return {'cobertura': cob, 'frequencia': freq, 'rentabilidade': None, 'mix': None, 'receita': None}
    return {'ok': True, 'mes': 202608, 'pesos': PESOS, 'total_universo': {'campo': 4, 'lojas': 1},
            'linhas': [l(1, 17, 10.0, N(10.0, 10.0), pos=1), l(2, 17, 4.0, N(2.0, 9.0), pos=3),
                       l(3, 19, 6.0, N(6.0, 6.0), pos=2), l(4, 19, None, N(None, None), pos=None),
                       l(5, 19, 2.0, N(1.0, 4.0), pos=4), l(6, 4, 5.0, N(5.0, 5.0), un='lojas', pos=1)]}


def test_performance_por_time_e_a_media_das_notas_com_melhor_pior_e_o_que_pesa():
    """Decisão (26/09): performance do time = MÉDIA das notas dos vendedores do time, por universo
    (universos não se comparam), rotulada como NÃO oficial — não existe nota de time na tela."""
    b = iacom.bloco_performance_times(_perf_time())
    t = {(x['cod'], x['universo']): x for x in b['times']}
    t17, t19 = t[(17, 'campo')], t[(19, 'campo')]
    assert t17['media'] == 7.0 and t17['n'] == 2 and t17['melhor']['cod'] == 1 and t17['pior']['cod'] == 2
    assert t19['media'] == 4.0 and t19['n'] == 2 and t19['sem_nota'] == 1          # vendedor 4 sem nota
    assert t19['pesa'] == 'cobertura'          # média de pontos tirados: cobertura pesa mais que frequência
    assert [x['cod'] for x in b['times'] if x['universo'] == 'campo'] == [19, 17]   # pior média primeiro
    txt = iacom._r_performance_times(b)
    assert '== PERFORMANCE POR TIME' in txt and 'NÃO é nota oficial' in txt
    assert '19 TIME 19 (campo): média 4,00 de 2 vendedores' in txt and 'melhor 3 V3 (6)' in txt
    assert 'pior 5 V5 (2)' in txt and '1 sem nota' in txt and 'o que mais tira pontos no time: cobertura' in txt
    ctx = iacom.montar_panorama({'role': 'viewer'}, {'performance': _perf_time()})
    assert 'performance por time (média das notas)' in iacom.indice(ctx)


def test_produtos_em_queda_vem_do_radar():
    radar = {'ok': True, 'dias': 60, 'total': 1500, 'rows': [
        {'codprod': 41384, 'descricao': 'CEREAL X 400G', 'depto_nome': 'MATINAIS', 'fornec_nome': 'F1',
         'clientes_ant': 62, 'clientes_rec': 43, 'clientes_perdidos': 62, 'pct_queda': 0.4362,
         'queda_receita': 5588.97, 'venda_ant': 12813.23, 'venda_rec': 7224.26}] * 12}
    b = iacom.bloco_produtos_queda(radar)
    assert len(b['produtos']) == 10 and b['total'] == 1500 and b['dias'] == 60
    txt = iacom._r_produtos_queda(b)
    assert '== PRODUTOS EM QUEDA' in txt and '60 dias recentes × 60 dias anteriores' in txt
    assert '41384 CEREAL X 400G (MATINAIS): queda de R$ 5.588,97 (-43,6%)' in txt
    assert '62 clientes pararam' in txt and 'NÃO somar' in txt
    assert iacom.bloco_produtos_queda({'ok': True, 'rows': []}) is None


def test_clientes_em_queda_sao_os_parados_e_os_que_largaram_departamento():
    """Não existe tela de "cliente comprando menos" (régua pendente com o João). O que existe entra:
    quem PAROU (Recuperação, em risco) e quem largou um DEPARTAMENTO e segue comprando (Mix)."""
    risco = {'ok': True, 'total': 2532, 'rows': [
        {'codcli': 2584, 'cliente': 'HORIZONTE ARMAZEM', 'dono': 139, 'dono_nome': 'BEATRIZ', 'time': 'RJ WAGNER',
         'dias': 71, 'ciclo': 11, 'valor_mensal': 631.47, 'chance_volta': 0.325}]}
    mix = {'ok': True, 'total': 27124, 'rows': [
        {'codcli': 120367, 'cliente': 'UNIMED SUL', 'codepto': 8, 'depto_nome': 'BOBINA PICOTADA',
         'dias_sem_comprar_categoria': 84, 'venda_cat_12m': 108000.0, 'lucro_cat_12m': 20000.0,
         'codusur': 879, 'vendedor': 'JOSE JUNIOR', 'time': 'FABIANE BA'}]}
    b = iacom.bloco_clientes_queda(risco, mix)
    txt = iacom._r_clientes_queda(b)
    assert '== CLIENTES EM QUEDA' in txt
    assert 'PARARAM de comprar' in txt and '2584 HORIZONTE ARMAZEM' in txt and 'dono 139 BEATRIZ' in txt
    assert 'R$ 631,47/mês' in txt and 'chance de voltar 32,5%' in txt and '2.532 no total' in txt
    assert 'LARGARAM UM DEPARTAMENTO' in txt and '120367 UNIMED SUL — 8 BOBINA PICOTADA há 84 d' in txt
    assert '27.124 pares' in txt
    assert iacom.bloco_clientes_queda(None, None) is None
    assert '"CLIENTE EM QUEDA"' in iacom.GLOSSARIO and 'régua ainda não existe' in iacom.GLOSSARIO


def test_vendedores_em_queda_mostram_o_time():
    def v(c, sup, yoy):
        return {'codusur': c, 'nome': f'V{c}', 'codsupervisor': sup, 'yoy_receita': yoy,
                'venda_anterior': 100000.0, 'venda_liq': 100000.0 * (1 + yoy)}
    vend = {'ok': True, 'vendedores': [v(1, 17, -0.4), v(2, 19, -0.1), v(3, 17, 0.3)]}
    ctx = iacom.montar_panorama({'role': 'viewer'}, {'vendedores': vend, 'performance': _perf_time()})
    txt = iacom.renderizar(ctx)
    assert '1 V1 (time 17 TIME 17): -40,0%' in txt and 'carteira transferida' not in txt
    vend['vendedores'].append(v(9, 19, -0.95))
    txt = iacom.renderizar(iacom.montar_panorama({'role': 'viewer'}, {'vendedores': vend, 'performance': _perf_time()}))
    assert '9 V9 (time 19 TIME 19): -95,0%' in txt and 'confirmar se o RCA saiu ou teve a carteira transferida' in txt
    sp = iacom.REGRAS
    assert 'PRODUTO e' in sp and '60 dias recentes × 60 dias anteriores' in sp and 'PERFORMANCE POR TIME' in sp


def test_sugestoes_trazem_time_e_quedas():
    cheio = dict(recuperacao={'x': 1}, performance={'x': 1}, gerencial={'x': 1}, yoy={'quedas': [1]},
                 performance_times={'times': [1]}, produtos_queda={'produtos': [1]}, clientes_queda={'parados': [1]})
    d = iacom.sugestoes({'perfil': {'role': 'viewer'}, **cheio}, '/')
    for q in ('Como está a performance de cada time?', 'Quais produtos estão em queda?',
              'Quais clientes estão em queda?'):
        assert q in d, q
    s = iacom.sugestoes({'perfil': {'role': 'supervisor'}, **cheio}, '/')
    assert 'Como está a performance do meu time?' in s and 'Quais clientes do meu time estão em queda?' in s
    assert len(d) <= iacom.MAX_SUGESTOES and iacom.MAX_SUGESTOES == 8


def test_panorama_de_quedas_e_times_no_modo_postgres(app, modo_pg, ia_ligado):
    import server
    adm = {'user_id': -11, 'role': 'viewer', 'nome': 'dir', 'areas': ['comercial']}
    ctx = server._iacom_panorama(adm)
    assert ctx['performance_times']['times'] and ctx['produtos_queda']['produtos']
    assert ctx['clientes_queda']['parados'] and ctx['clientes_queda']['departamentos']
    assert not ctx['indisponivel'], ctx['indisponivel']
    sup = {'user_id': -12, 'role': 'supervisor', 'codsupervisor': 12, 'codsupervisores': [12], 'nome': 's',
           'areas': ['comercial']}
    ctx_s = server._iacom_panorama(sup)
    assert {t['cod'] for t in ctx_s['performance_times']['times']} == {12}
    with server._ia_sessao(sup):
        meus = {c['codcli'] for c in server._carteira_no_escopo()}
    assert {c['codcli'] for c in ctx_s['clientes_queda']['parados']} <= meus       # escopo de cadastro


def test_performance_sem_nenhuma_nota_e_declarada_e_nao_some():
    """Sem metas cadastradas e com a cobertura INFORMATIVA (mês de limpeza), só a frequência é
    medida (13% do peso < 35%): NINGUÉM tem nota. O bloco some e o agente inventava "piores médias"
    de time sem número (BI real, 29/09/2026). Agora o panorama DIZ por que não há nota."""
    linhas = [_linha_perf(cod=c, nota=None, notas={'cobertura': 0.0, 'frequencia': 5.0, 'rentabilidade': None,
                                                     'mix': None, 'receita': None}) for c in (1, 2, 3)]
    perf = {'ok': True, 'mes': 202608, 'pesos': PESOS, 'total_universo': {}, 'escalas': {},
            'cobertura_informativa': True, 'cobertura_na_nota_desde': 202611, 'linhas': linhas}
    b = iacom.bloco_performance(perf)
    assert b and b['sem_notas'] is True
    txt = iacom._r_performance(b)
    assert 'NENHUM vendedor tem nota em 08/2026' in txt and 'metas' in txt and 'INFORMATIVA até 11/2026' in txt
    assert any('nota da Performance' in i for i in iacom.indice({'performance': b}))


def test_gerencial_manda_citar_quem_esta_mais_longe():
    txt = iacom._r_gerencial(iacom.bloco_gerencial(_ger(), nao_pessoas={999, 34}))
    assert 'cite pelo código os que estão mais longe' in txt
    assert 'Toda CONTAGEM de pessoas ou times' in iacom.REGRAS
