"""Gate do cache em memória do Compras (estoque/pbi.TTLCache): item vencido não pode morar para sempre.

O get só apagava o vencido quando a MESMA chave era pedida de novo. Várias chaves levam a data do dia
(`leadtime:raw:2026-10-15`, `topvend:<produto>:…:<data>`): a de ontem nunca mais é pedida, então
ficava na memória até o container reiniciar — produção crescia ~120 MB/dia (10/2026).
"""
from estoque import pbi


class _Relogio:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def _cache(monkeypatch, t0=1_000_000.0):
    relogio = _Relogio(t0)
    monkeypatch.setattr(pbi.time, 'time', relogio)
    return pbi.TTLCache(), relogio


def test_chave_de_ontem_some_sem_ninguem_pedir_de_novo(monkeypatch):
    c, relogio = _cache(monkeypatch)
    c.set('leadtime:raw:2026-10-14', ['base inteira'], 1800)
    relogio.t += 86400  # dia seguinte: ninguém mais pede a chave de ontem
    c.set('leadtime:raw:2026-10-15', ['base nova'], 1800)
    assert 'leadtime:raw:2026-10-14' not in c._store
    assert c.get('leadtime:raw:2026-10-15') == ['base nova']


def test_item_dentro_do_prazo_continua_no_cache(monkeypatch):
    c, relogio = _cache(monkeypatch)
    c.set('cad:prod', 'cadastro', 86400)   # 24 h
    c.set('snap:3-5', 'snapshot', 1800)    # 30 min
    relogio.t += 3600                      # 1 h depois: snapshot venceu, cadastro não
    c.set('outra', 1, 1800)
    assert c.get('cad:prod') == 'cadastro'
    assert 'snap:3-5' not in c._store


def test_varredura_roda_no_maximo_uma_vez_por_minuto(monkeypatch):
    c, relogio = _cache(monkeypatch)
    relogio.t += pbi.TTLCache._VARRER_A_CADA
    c.set('a', 1, 10)                      # varre aqui (passou 1 min) e marca o horário
    relogio.t += 30                        # 'a' venceu, mas a última varredura foi há 30 s
    c.set('b', 2, 10)
    assert 'a' in c._store                 # ainda não varreu: custo não cai em toda gravação
    relogio.t += 30                        # agora fecha 1 min
    c.set('c', 3, 10)
    assert 'a' not in c._store and 'b' not in c._store
    assert c.get('c') == 3


def test_get_continua_igual(monkeypatch):
    """Contrato antigo intacto: vencido devolve None; False ('consultado, sem dado') volta como False."""
    c, relogio = _cache(monkeypatch)
    c.set('sem-dado', False, 300)
    assert c.get('sem-dado') is False
    relogio.t += 301
    assert c.get('sem-dado') is None
