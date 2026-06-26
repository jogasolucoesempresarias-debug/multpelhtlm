"""Smoke + RBAC do módulo de Metas. DAX mockado por substring routing (mock_dax_capture)."""
import server
from tests.conftest import login_as


def _pl(rows):
    return {'results': [{'tables': [{'rows': rows}]}]}


def _rotas_metas():
    """Payloads mockados pras queries do /api/metas (admin, jun/2026). Ordem = específico→genérico."""
    sup_map = _pl([{'PCSUPERV[CODSUPERVISOR]': 17, 'PCSUPERV[NOME]': 'AFONSO ES-SUL',
                    'PCSUPERV[TIPOSUPERVISOR]': 'P'}])
    vend_map = _pl([{'PCUSUARI[CODUSUR]': 879, 'PCUSUARI[NOME]': 'JOSE JUNIOR',
                     'PCUSUARI[CODSUPERVISOR]': 17, 'PCUSUARI[TIPOVEND]': 'R',
                     'PCUSUARI[CIDADE]': 'VITORIA', 'PCUSUARI[ESTADO]': 'ES', 'PCUSUARI[BLOQUEIO]': 'N'}])
    dias = _pl([{'[mes]': 21, '[decorridos]': 19, '[restantes]': 2}])
    por_sup = _pl([{'PCSUPERV[CODSUPERVISOR]': 17, 'PCSUPERV[NOME]': 'AFONSO ES-SUL',
                    '[venda]': 2591058.95, '[rentab]': 466476.24, '[proj]': 2867280.70,
                    '[cli]': 804, '[mix]': 1350}])
    totais = _pl([{'[venda]': 6587356.73, '[rentab]': 1316297.72, '[proj]': 7354084.45,
                   '[cli]': 2966, '[mix]': 2181}])
    vr_usur = _pl([{'PCUSUARI[CODUSUR]': 879, '[venda]': 400000.0, '[rentab]': 96000.0, '[proj]': 442105.0}])
    cli_usur = _pl([{'PCPEDC[CODUSUR]': 879, '[v]': 55}])
    mix_usur = _pl([{'PCPEDI[CODUSUR]': 879, '[v]': 200}])
    # ordem importa (first match): mapas e dias antes das queries de pedido
    return [
        ('TIPOSUPERVISOR', sup_map),
        ('TIPOVEND', vend_map),
        ('CALENDARIO', dias),
        ('ROW("venda"', totais),
        ('PCUSUARI[CODUSUR]', vr_usur),     # vr_usur (vend_map já roteado por TIPOVEND acima)
        ('PCPEDC[CODUSUR]', cli_usur),
        ('PCPEDI[CODUSUR]', mix_usur),
        ('PCSUPERV[CODSUPERVISOR]', por_sup),
    ]


def test_api_metas_estrutura_e_totais(client, usuario_admin, mock_dax_capture, clean_redis):
    mock_dax_capture.set_routes(_rotas_metas())
    login_as(client, usuario_admin['email'], usuario_admin['senha'])

    r = client.get('/api/metas?ano=2026&mes=6')
    assert r.status_code == 200
    d = r.get_json()
    assert d['ok']
    assert d['dias'] == {'mes': 21, 'decorridos': 19, 'restantes': 2}

    # totais batem com o oficial (centavo)
    t = d['total']
    assert round(t['venda']['realizado'], 2) == 6587356.73
    assert round(t['rentabilidade']['realizado'], 2) == 1316297.72
    assert t['clientes']['realizado'] == 2966
    assert t['mix']['realizado'] == 2181
    # projeção oficial passada direto pra venda
    assert round(t['venda']['projecao'], 2) == 7354084.45

    # supervisor AFONSO presente com realizado correto
    afonso = next(s for s in d['supervisores'] if s['codsupervisor'] == 17)
    assert round(afonso['venda']['realizado'], 2) == 2591058.95
    assert afonso['mix']['realizado'] == 1350
    assert round(afonso['venda']['projecao'], 2) == 2867280.70  # projeção oficial


def test_api_metas_drill_vendedores(client, usuario_admin, mock_dax_capture, clean_redis):
    mock_dax_capture.set_routes(_rotas_metas())
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    r = client.get('/api/metas/vendedores?ano=2026&mes=6&codsupervisor=17')
    assert r.status_code == 200
    d = r.get_json()
    assert d['ok']
    v = next(x for x in d['vendedores'] if x['codusur'] == 879)
    assert round(v['venda']['realizado'], 2) == 400000.0
    assert v['clientes']['realizado'] == 55
    assert v['mix']['realizado'] == 200


def test_metas_rbac_frag():
    """Fragmento RBAC por CODUSUR: None=tudo, conjunto=IN, vazio=impossível."""
    assert server._metas_rbac_frag('PCPEDC', None) == ''
    assert server._metas_rbac_frag('PCPEDC', {879}) == 'PCPEDC[CODUSUR] IN {879}'
    assert server._metas_rbac_frag('PCPEDC', {879, 17}) == 'PCPEDC[CODUSUR] IN {17, 879}'
    assert server._metas_rbac_frag('PCPEDC', set()) == 'PCPEDC[CODUSUR] IN {-1}'


def test_metas_exige_login(client, clean_redis):
    assert client.get('/api/metas').status_code == 401


def test_admin_metas_save_exige_admin(client, usuario_vendedor, clean_redis):
    login_as(client, usuario_vendedor['email'], usuario_vendedor['senha'])
    r = client.post('/api/admin/metas', json={'ano': 2026, 'mes': 6, 'codusur': 1, 'valor_meta': 1000})
    assert r.status_code == 403
