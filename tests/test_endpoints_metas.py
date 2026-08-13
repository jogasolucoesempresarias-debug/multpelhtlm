"""Smoke + RBAC do módulo de Metas. DAX mockado por substring routing (mock_dax_capture)."""
from datetime import date as _date
import pytest
import server
from tests.conftest import login_as

# Testes exercitam o caminho do MÊS CORRENTE (medidas oficiais do dataset META). Mês fechado
# usa o dataset RCA (outro caminho), então usamos sempre o mês atual dinâmico.
_HOJE = _date.today()
_AM = f'ano={_HOJE.year}&mes={_HOJE.month}'


def _pl(rows):
    return {'results': [{'tables': [{'rows': rows}]}]}


def _rotas_metas():
    """Payloads mockados pras queries do /api/metas (admin, jun/2026). Ordem = específico→genérico."""
    sup_map = _pl([{'PCSUPERV[CODSUPERVISOR]': 17, 'PCSUPERV[NOME]': 'AFONSO ES-SUL',
                    'PCSUPERV[TIPOSUPERVISOR]': 'P'}])
    vend_map = _pl([{'PCUSUARI[CODUSUR]': 879, 'PCUSUARI[NOME]': 'JOSE JUNIOR',
                     'PCUSUARI[CODSUPERVISOR]': 17, 'PCUSUARI[TIPOVEND]': 'R',
                     'PCUSUARI[CIDADE]': 'VITORIA', 'PCUSUARI[ESTADO]': 'ES', 'PCUSUARI[BLOQUEIO]': 'N'},
                    # vendedor zerado (mesmo supervisor, sem meta e sem realizado) → deve ser filtrado do drill
                    {'PCUSUARI[CODUSUR]': 888, 'PCUSUARI[NOME]': 'VENDEDOR ZERADO',
                     'PCUSUARI[CODSUPERVISOR]': 17, 'PCUSUARI[TIPOVEND]': 'R',
                     'PCUSUARI[CIDADE]': 'VITORIA', 'PCUSUARI[ESTADO]': 'ES', 'PCUSUARI[BLOQUEIO]': 'N'}])
    dias = _pl([{'[mes]': 21, '[decorridos]': 19, '[restantes]': 2}])
    # `[venda_sb]` (sem bônus) SEMPRE diferente de `[venda]` (bruto): é o que faz as asserções de
    # margem morderem. Com os dois iguais o teste passa mesmo com a fórmula errada — ver
    # tests/test_margem_bonus.py.
    por_sup = _pl([{'PCSUPERV[CODSUPERVISOR]': 17, 'PCSUPERV[NOME]': 'AFONSO ES-SUL',
                    '[venda]': 2591058.95, '[venda_sb]': 2565148.31, '[rentab]': 466476.24,
                    '[proj]': 2867280.70, '[cli]': 804, '[mix]': 1350}])
    totais = _pl([{'[venda]': 6587356.73, '[venda_sb]': 6521483.16, '[rentab]': 1316297.72,
                   '[proj]': 7354084.45, '[cli]': 2966, '[mix]': 2181}])
    vr_usur = _pl([{'PCUSUARI[CODUSUR]': 879, '[venda]': 400000.0, '[venda_sb]': 396000.0,
                    '[rentab]': 96000.0, '[proj]': 442105.0}])
    cli_usur = _pl([{'PCPEDC[CODUSUR]': 879, '[v]': 55}])
    mix_usur = _pl([{'PCPEDI[CODUSUR]': 879, '[v]': 200}])
    # ordem importa (first match): mais específico primeiro. Quando o realizado é escopado
    # ao universo de meta, o filtro_med injeta 'PCPEDC[CODUSUR]' na query por_sup/totais —
    # por isso PCSUPERV e ROW vêm ANTES das rotas genéricas PCPEDC/PCPEDI[CODUSUR].
    return [
        ('TIPOSUPERVISOR', sup_map),
        ('TIPOVEND', vend_map),
        ('CALENDARIO', dias),
        ('PCSUPERV[CODSUPERVISOR]', por_sup),
        ('ROW("venda"', totais),
        ('PCUSUARI[CODUSUR]', vr_usur),     # vr_usur (vend_map já roteado por TIPOVEND acima)
        ('PCPEDC[CODUSUR]', cli_usur),
        ('PCPEDI[CODUSUR]', mix_usur),
    ]


def test_api_metas_estrutura_e_totais(client, usuario_admin, mock_dax_capture, clean_redis):
    mock_dax_capture.set_routes(_rotas_metas())
    login_as(client, usuario_admin['email'], usuario_admin['senha'])

    r = client.get(f'/api/metas?{_AM}')
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

    # margem = lucro realizado ÷ realizado BRUTO (com bônus), igual à medida [MARGEM(%)] do BI —
    # não pelo `venda_sb` (6.521.483,16), que daria 20,18% em vez de 19,98%
    assert t['margem'] == pytest.approx(1316297.72 / 6587356.73, abs=1e-9)

    # supervisor AFONSO presente com realizado correto
    afonso = next(s for s in d['supervisores'] if s['codsupervisor'] == 17)
    assert round(afonso['venda']['realizado'], 2) == 2591058.95
    assert afonso['mix']['realizado'] == 1350
    assert round(afonso['venda']['projecao'], 2) == 2867280.70  # projeção oficial
    assert afonso['margem'] == pytest.approx(466476.24 / 2591058.95, abs=1e-9)


def test_api_metas_drill_vendedores(client, usuario_admin, mock_dax_capture, clean_redis):
    mock_dax_capture.set_routes(_rotas_metas())
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    r = client.get(f'/api/metas/vendedores?{_AM}&codsupervisor=17')
    assert r.status_code == 200
    d = r.get_json()
    assert d['ok']
    v = next(x for x in d['vendedores'] if x['codusur'] == 879)
    assert round(v['venda']['realizado'], 2) == 400000.0
    assert v['clientes']['realizado'] == 55
    assert v['mix']['realizado'] == 200
    assert v['margem'] == pytest.approx(96000.0 / 400000.0)  # 0.24
    # vendedor zerado (888) não é trazido no drill
    assert all(x['codusur'] != 888 for x in d['vendedores'])


def test_api_metas_escopo_universo_de_meta(client, usuario_admin, mock_dax_capture, clean_redis, monkeypatch):
    """Supervisor SEM meta cadastrada (ex.: DIRETORIA) não entra no painel nem no total (alinha BI)."""
    sup_map = _pl([{'PCSUPERV[CODSUPERVISOR]': 17, 'PCSUPERV[NOME]': 'AFONSO ES-SUL', 'PCSUPERV[TIPOSUPERVISOR]': 'P'},
                   {'PCSUPERV[CODSUPERVISOR]': 99, 'PCSUPERV[NOME]': 'DIRETORIA', 'PCSUPERV[TIPOSUPERVISOR]': 'P'}])
    vend_map = _pl([{'PCUSUARI[CODUSUR]': 879, 'PCUSUARI[NOME]': 'JOSE', 'PCUSUARI[CODSUPERVISOR]': 17,
                     'PCUSUARI[TIPOVEND]': 'R', 'PCUSUARI[CIDADE]': 'V', 'PCUSUARI[ESTADO]': 'ES', 'PCUSUARI[BLOQUEIO]': 'N'},
                    {'PCUSUARI[CODUSUR]': 700, 'PCUSUARI[NOME]': 'DIR VEND', 'PCUSUARI[CODSUPERVISOR]': 99,
                     'PCUSUARI[TIPOVEND]': 'R', 'PCUSUARI[CIDADE]': 'V', 'PCUSUARI[ESTADO]': 'ES', 'PCUSUARI[BLOQUEIO]': 'N'}])
    dias = _pl([{'[mes]': 21, '[decorridos]': 19, '[restantes]': 2}])
    por_sup = _pl([{'PCSUPERV[CODSUPERVISOR]': 17, 'PCSUPERV[NOME]': 'AFONSO ES-SUL',
                    '[venda]': 100.0, '[rentab]': 20.0, '[proj]': 110.0, '[cli]': 5, '[mix]': 9},
                   {'PCSUPERV[CODSUPERVISOR]': 99, 'PCSUPERV[NOME]': 'DIRETORIA',
                    '[venda]': 999.0, '[rentab]': 1.0, '[proj]': 999.0, '[cli]': 3, '[mix]': 2}])
    totais = _pl([{'[venda]': 100.0, '[rentab]': 20.0, '[proj]': 110.0, '[cli]': 5, '[mix]': 9}])
    vr_usur = _pl([{'PCUSUARI[CODUSUR]': 879, '[venda]': 100.0, '[rentab]': 20.0, '[proj]': 110.0}])
    cli_usur = _pl([{'PCPEDC[CODUSUR]': 879, '[v]': 5}])
    mix_usur = _pl([{'PCPEDI[CODUSUR]': 879, '[v]': 9}])
    mock_dax_capture.set_routes([
        ('TIPOSUPERVISOR', sup_map), ('TIPOVEND', vend_map), ('CALENDARIO', dias),
        ('PCSUPERV[CODSUPERVISOR]', por_sup), ('ROW("venda"', totais),
        ('PCUSUARI[CODUSUR]', vr_usur), ('PCPEDC[CODUSUR]', cli_usur), ('PCPEDI[CODUSUR]', mix_usur),
    ])
    # só o vendedor 879 (supervisor 17) tem meta → DIRETORIA (99) deve sumir do painel
    monkeypatch.setattr(server, '_metas_buscar',
                        lambda a, m: {'879': {'valor_meta': 50.0, 'clientes_meta': 3, 'mix_meta': 4, 'rentabilidade_meta': 10.0}})
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    d = client.get(f'/api/metas?{_AM}').get_json()
    assert d['ok']
    cods = [s['codsupervisor'] for s in d['supervisores']]
    assert 17 in cods
    assert 99 not in cods  # DIRETORIA (sem meta) fora do universo de meta


def test_metas_rbac_frag():
    """Fragmento RBAC por CODUSUR: None=tudo, conjunto=IN, vazio=impossível."""
    assert server._metas_rbac_frag('PCPEDC', None) == ''
    assert server._metas_rbac_frag('PCPEDC', {879}) == 'PCPEDC[CODUSUR] IN {879}'
    assert server._metas_rbac_frag('PCPEDC', {879, 17}) == 'PCPEDC[CODUSUR] IN {17, 879}'
    assert server._metas_rbac_frag('PCPEDC', set()) == 'PCPEDC[CODUSUR] IN {-1}'


def test_metas_exige_login(client, clean_redis):
    assert client.get('/api/metas').status_code == 401


def test_meta_refresh_tag_segue_o_refresh_do_bi(monkeypatch, clean_redis):
    """A tag do cache do realizado tem de MUDAR quando o BI atualiza — é o que faz o app parar de
    servir número de um refresh anterior (BO de 16/07/2026). Até 08/2026 nenhum teste cobria esta
    branch: o mock deixava o refresh history estourar 403 e caía sempre no fallback."""
    ref = {'end': '2026-08-13T11:08:00-03:00'}
    monkeypatch.setattr(server, '_get_dataset_refresh', lambda *a, **k: dict(ref))
    tag1 = server._meta_refresh_tag()
    assert tag1 == '2026-08-13T11:08:00-03:00'

    ref['end'] = '2026-08-13T13:08:00-03:00'          # BI atualizou → chave de cache nova
    assert server._meta_refresh_tag() != tag1

    # refresh history indisponível → fallback de bucket de 30min (não explode)
    monkeypatch.setattr(server, '_get_dataset_refresh', lambda *a, **k: None)
    fb = server._meta_refresh_tag()
    assert fb and fb[-1] in ('H', 'L') and len(fb) == 11


def test_meta_refresh_tag_estatica_em_modo_postgres(monkeypatch, clean_redis):
    """Modo BD não tem dataset PBI — a tag não pode tentar rede nenhuma."""
    monkeypatch.setitem(server.CONFIG, 'data_source', 'postgres')
    def _boom(*a, **k):
        raise AssertionError('não deve consultar o Power BI em modo postgres')
    monkeypatch.setattr(server, '_get_dataset_refresh', _boom)
    assert server._meta_refresh_tag() == 'postgres'


def test_admin_metas_save_exige_admin(client, usuario_vendedor, clean_redis):
    login_as(client, usuario_vendedor['email'], usuario_vendedor['senha'])
    r = client.post('/api/admin/metas', json={'ano': 2026, 'mes': 6, 'codusur': 1, 'valor_meta': 1000})
    assert r.status_code == 403


def _rotas_editor_2sup():
    """Dois vendedores em supervisores diferentes (17 e 99), ambos COM realizado — pra provar
    que ?supervisor= recorta o editor por time (pedido do diretor)."""
    sup_map = _pl([{'PCSUPERV[CODSUPERVISOR]': 17, 'PCSUPERV[NOME]': 'AFONSO ES-SUL', 'PCSUPERV[TIPOSUPERVISOR]': 'P'},
                   {'PCSUPERV[CODSUPERVISOR]': 99, 'PCSUPERV[NOME]': 'OUTRO TIME', 'PCSUPERV[TIPOSUPERVISOR]': 'P'}])
    vend_map = _pl([{'PCUSUARI[CODUSUR]': 879, 'PCUSUARI[NOME]': 'JOSE', 'PCUSUARI[CODSUPERVISOR]': 17,
                     'PCUSUARI[TIPOVEND]': 'R', 'PCUSUARI[CIDADE]': 'V', 'PCUSUARI[ESTADO]': 'ES', 'PCUSUARI[BLOQUEIO]': 'N'},
                    {'PCUSUARI[CODUSUR]': 700, 'PCUSUARI[NOME]': 'MARIA', 'PCUSUARI[CODSUPERVISOR]': 99,
                     'PCUSUARI[TIPOVEND]': 'R', 'PCUSUARI[CIDADE]': 'V', 'PCUSUARI[ESTADO]': 'ES', 'PCUSUARI[BLOQUEIO]': 'N'}])
    dias = _pl([{'[mes]': 21, '[decorridos]': 19, '[restantes]': 2}])
    por_sup = _pl([{'PCSUPERV[CODSUPERVISOR]': 17, '[venda]': 100.0, '[rentab]': 20.0, '[proj]': 110.0, '[cli]': 5, '[mix]': 9}])
    totais = _pl([{'[venda]': 100.0, '[rentab]': 20.0, '[proj]': 110.0, '[cli]': 5, '[mix]': 9}])
    vr_usur = _pl([{'PCUSUARI[CODUSUR]': 879, '[venda]': 400000.0, '[rentab]': 96000.0, '[proj]': 442105.0},
                   {'PCUSUARI[CODUSUR]': 700, '[venda]': 250000.0, '[rentab]': 50000.0, '[proj]': 276315.0}])
    cli_usur = _pl([{'PCPEDC[CODUSUR]': 879, '[v]': 55}, {'PCPEDC[CODUSUR]': 700, '[v]': 40}])
    mix_usur = _pl([{'PCPEDI[CODUSUR]': 879, '[v]': 200}, {'PCPEDI[CODUSUR]': 700, '[v]': 150}])
    return [
        ('TIPOSUPERVISOR', sup_map), ('TIPOVEND', vend_map), ('CALENDARIO', dias),
        ('PCSUPERV[CODSUPERVISOR]', por_sup), ('ROW("venda"', totais),
        ('PCUSUARI[CODUSUR]', vr_usur), ('PCPEDC[CODUSUR]', cli_usur), ('PCPEDI[CODUSUR]', mix_usur),
    ]


def test_admin_metas_list_filtra_por_supervisor(client, usuario_admin, mock_dax_capture, clean_redis):
    """Editor herda o chip: ?supervisor=17 traz só o time 17; sem param traz a empresa toda."""
    mock_dax_capture.set_routes(_rotas_editor_2sup())
    login_as(client, usuario_admin['email'], usuario_admin['senha'])

    # sem filtro → os dois vendedores (empresa toda)
    todos = client.get(f'/api/admin/metas?{_AM}').get_json()
    assert todos['ok']
    cods = {v['codusur'] for v in todos['vendedores']}
    assert cods == {879, 700}

    # filtrado por time 17 → só o 879
    so17 = client.get(f'/api/admin/metas?{_AM}&supervisor=17').get_json()
    assert so17['ok']
    assert {v['codusur'] for v in so17['vendedores']} == {879}


def test_admin_metas_bulk_salva(client, usuario_admin, clean_redis, monkeypatch):
    """Botão único: um POST grava todas as linhas de uma vez (lote atômico)."""
    capturado = {}
    def _fake_lote(ano, mes, itens, user_id=None):
        capturado['ano'] = ano; capturado['mes'] = mes; capturado['itens'] = itens
        return len(itens)
    monkeypatch.setattr(server, '_metas_upsert_lote', _fake_lote)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    body = {'ano': 2026, 'mes': 6, 'metas': [
        {'codusur': 879, 'valor_meta': 400000, 'rentabilidade_meta': 96000, 'clientes_meta': 55, 'mix_meta': 200},
        {'codusur': 700, 'valor_meta': 250000, 'rentabilidade_meta': 50000, 'clientes_meta': 40, 'mix_meta': 150},
    ]}
    r = client.post('/api/admin/metas/bulk', json=body)
    assert r.status_code == 200
    j = r.get_json()
    assert j['ok'] and j['salvos'] == 2
    assert capturado['ano'] == 2026 and capturado['mes'] == 6
    assert {it['codusur'] for it in capturado['itens']} == {879, 700}


def test_admin_metas_bulk_valida_lista_vazia(client, usuario_admin, clean_redis):
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    r = client.post('/api/admin/metas/bulk', json={'ano': 2026, 'mes': 6, 'metas': []})
    assert r.status_code == 400


def test_admin_metas_bulk_exige_admin(client, usuario_vendedor, clean_redis):
    login_as(client, usuario_vendedor['email'], usuario_vendedor['senha'])
    r = client.post('/api/admin/metas/bulk', json={'ano': 2026, 'mes': 6,
                    'metas': [{'codusur': 1, 'valor_meta': 1000}]})
    assert r.status_code == 403
