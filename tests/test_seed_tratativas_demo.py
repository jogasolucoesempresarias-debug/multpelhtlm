"""Tratativas na DEMO (26/11/2026): o seeder e o alimentador diário.

A demo não tem vendedor registrando nada, então a página Tratativas abriria vazia na apresentação.
O `seed_tratativas_demo.py` gera um histórico coerente com as vendas sintéticas, e o
`avancar_demo.py` passa a deslocar `cliente_plano` junto com o resto — senão as tratativas
envelheceriam contra as vendas e a página esvaziaria em semanas.
"""
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_seed_demo"))

import avancar_demo as av            # noqa: E402
import seed_tratativas_demo as sd    # noqa: E402

HOJE = date(2026, 8, 21)


def test_trava_recusa_tudo_que_nao_e_demo():
    assert sd.guardas("joga_demo", {}) is not None                                  # sem DEMO_SEED
    assert sd.guardas("multpel_db", {"DEMO_SEED": "1"}) is not None                  # produção
    assert sd.guardas("painel_db", {"DEMO_SEED": "1"}) is not None                   # produção
    assert sd.guardas("gestao", {"DEMO_SEED": "1"}) is not None                      # sem "demo"
    assert sd.guardas("joga_demo", {"DEMO_SEED": "1"}) is None


def _cenario():
    vend = [(10, "ANA", 1, 50), (20, "BIA", 1, 50), (30, "CAIO", 1, 50), (40, "DUDA", 1, 50), (50, "EDU", 1, 50)]
    carteira = {u: [u * 100 + i for i in range(30)] for u, *_ in vend}
    compras = {}
    for u, *_ in vend:
        for i, cc in enumerate(carteira[u]):
            if i % 3 == 0:                       # um terço compra toda semana
                compras[cc] = [HOJE - timedelta(days=7 * k) for k in range(30, -1, -1)]
            elif i % 3 == 1:                     # um terço parou há 4 meses
                compras[cc] = [HOJE - timedelta(days=120)]
    return vend, carteira, compras


def test_status_coerentes_com_as_vendas_e_perfis():
    vend, carteira, compras = _cenario()
    regs = sd.gerar(HOJE, 1, 10, vend, carteira, compras, {}, random.Random(42))
    assert regs, "gerou nada"
    for cc, acao, st, _d, _aid, _nome, quando, *_ in regs:
        if st == "pedido_feito":                 # pedido feito = houve compra logo depois
            _sem, prox = sd._situacao(compras, cc, quando.date())
            assert prox is not None and prox <= 3
        if st == "retorno_agendado":
            assert acao > quando.date()          # retorno é sempre para frente
        if st != "retorno_agendado":
            assert acao <= HOJE
    # o perfil "parou" (5º vendedor) não registra depois da 2ª semana
    inicio = HOJE - timedelta(weeks=sd.SEMANAS)
    seguimentos = ("Assumi o cliente transferido", "Retorno feito como combinado")
    parou = [r for r in regs if r[4] == 900050 and r[3] not in seguimentos]
    assert parou and max(r[6].date() for r in parou) < inicio + timedelta(weeks=2)
    assert regs == sd.gerar(HOJE, 1, 10, vend, carteira, compras, {}, random.Random(42))   # reprodutível


def test_usuario_vendedor_da_demo_vira_autor_da_propria_carteira():
    vend, carteira, compras = _cenario()
    regs = sd.gerar(HOJE, 1, 10, vend, carteira, compras, {("vendedor", 10): (7, "Ana (demo)")}, random.Random(1))
    meus = {r[0] for r in regs if r[4] == 7}
    assert meus and meus <= set(carteira[10]) | {cc for u in carteira for cc in carteira[u]}
    assert all(r[5] == "Ana (demo)" for r in regs if r[4] == 7)


def test_avancar_desloca_as_tratativas_no_tipo_certo():
    assert av.TABELAS["cliente_plano"] == ["data_acao", "criado_em", "excluido_em"]
    assert av._soma("data_acao", "date") == "data_acao = data_acao + %s"
    assert av._soma("criado_em", "timestamp with time zone") == "criado_em = criado_em + make_interval(days => %s)"


def test_bootstrap_semeia_as_tratativas_depois_dos_usuarios():
    sh = (Path(__file__).resolve().parent.parent / "_seed_demo" / "bootstrap_demo.sh").read_text(encoding="utf-8")
    assert sh.index("seed_usuarios_demo.py") < sh.index("seed_tratativas_demo.py")
