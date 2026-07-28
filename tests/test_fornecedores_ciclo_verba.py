"""Gate das colunas novas da aba Fornecedores (pedido do consultor/diretor 07/2026):
ciclo de compras + nº de compras, e lucro bruto / lucro com verba.

O que estes testes travam:
1. `n_pedidos` respeita a janela do seletor "Venda"; o CICLO não (12m fixo) — foram desenhados
   em janelas diferentes de propósito e é fácil alguém "consertar" isso sem entender;
2. ciclo conta DATAS distintas, não pedidos (mesmo dia = 1 compra, senão a média desaba);
3. transferência entre filiais (mesma raiz de CNPJ) não é compra nem verba;
4. verba é somada na MESMA janela do lucro — somar lucro de 1 mês com verba de 12 seria o erro
   mais fácil e mais caro aqui;
5. `lucro` é o agregado que já existia, NUNCA recalculado como venda × margem (arredondamento).
"""
from datetime import date

from estoque import core

HOJE = date(2026, 7, 28)
CNPJ_EMPRESA = "12345678000199"


def _ped(numped, cod, dia, filial="3", cgc_forn=None):
    return {"NUMPED": numped, "CODFORNEC": cod, "CODFILIAL": filial,
            "DTEMISSAO": dia.isoformat(), "VLTOTAL": 1000.0}


def _forn_map():
    return {
        10: {"FORNECEDOR": "BOMBRIL SA", "CGC": "61024006000133"},
        99: {"FORNECEDOR": "MULTPEL FILIAL", "CGC": "12345678000288"},   # mesma raiz da empresa
    }


# ───────────────────────── ciclo de compras ─────────────────────────

def test_n_pedidos_respeita_a_janela_e_ciclo_usa_12m():
    """A contagem segue o filtro (foi o pedido literal); o ciclo é sempre 12m."""
    cab = [
        _ped(1, 10, date(2025, 9, 10)),    # fora da janela curta, dentro dos 12m
        _ped(2, 10, date(2026, 1, 10)),    # idem
        _ped(3, 10, date(2026, 7, 10)),    # dentro das duas
    ]
    r = core.ciclo_compras(cab, ini=date(2026, 7, 1), fim=HOJE, filiais=["3"],
                           forn_map=_forn_map(), cnpj_empresa=CNPJ_EMPRESA,
                           ciclo_desde=HOJE.replace(year=2025))
    assert r[10]["n_pedidos"] == 1                    # só o de julho entra na janela do filtro
    assert r[10]["n_ciclo"] == 3                      # mas o ciclo enxerga as 3 datas dos 12m
    # (2026-07-10 − 2025-09-10) = 303 dias em 2 intervalos
    assert r[10]["ciclo_dias"] == 151.5
    assert r[10]["ultima_compra"] == "2026-07-10"


def test_ciclo_conta_datas_distintas_nao_pedidos():
    """3 pedidos no mesmo dia = 1 compra. Contar pedidos criaria intervalos de 0 dia e a média
    de ciclo despencaria — o fornecedor pareceria abastecer diariamente."""
    cab = [_ped(1, 10, date(2026, 1, 5)), _ped(2, 10, date(2026, 1, 5)),
           _ped(3, 10, date(2026, 1, 5)), _ped(4, 10, date(2026, 3, 6))]
    r = core.ciclo_compras(cab, ini=date(2025, 1, 1), fim=HOJE, filiais=["3"],
                           forn_map=_forn_map(), cnpj_empresa=CNPJ_EMPRESA)
    assert r[10]["n_pedidos"] == 4                    # 4 pedidos foram emitidos
    assert r[10]["n_ciclo"] == 2                      # mas em 2 datas
    assert r[10]["ciclo_dias"] == 60.0                # 05/01 → 06/03


def test_uma_compra_so_nao_tem_ciclo():
    """Com 1 data não existe intervalo: ciclo None, não 0 — zero mentiria 'compra todo dia'."""
    r = core.ciclo_compras([_ped(1, 10, date(2026, 5, 5))], ini=date(2025, 1, 1), fim=HOJE,
                           filiais=["3"], forn_map=_forn_map(), cnpj_empresa=CNPJ_EMPRESA)
    assert r[10]["n_pedidos"] == 1
    assert r[10]["ciclo_dias"] is None


def test_transferencia_entre_filiais_nao_e_compra():
    cab = [_ped(1, 10, date(2026, 5, 5)), _ped(2, 99, date(2026, 5, 6))]
    r = core.ciclo_compras(cab, ini=date(2025, 1, 1), fim=HOJE, filiais=["3"],
                           forn_map=_forn_map(), cnpj_empresa=CNPJ_EMPRESA)
    assert 10 in r
    assert 99 not in r        # mesma raiz de CNPJ da empresa → não é fornecedor


def test_filtra_por_filial_da_unidade():
    cab = [_ped(1, 10, date(2026, 5, 5), filial="3"), _ped(2, 10, date(2026, 5, 6), filial="7")]
    r = core.ciclo_compras(cab, ini=date(2025, 1, 1), fim=HOJE, filiais=["3"],
                           forn_map=_forn_map(), cnpj_empresa=CNPJ_EMPRESA)
    assert r[10]["n_pedidos"] == 1
    r_all = core.ciclo_compras(cab, ini=date(2025, 1, 1), fim=HOJE, filiais=None,
                               forn_map=_forn_map(), cnpj_empresa=CNPJ_EMPRESA)
    assert r_all[10]["n_pedidos"] == 2


# ───────────────────────── verba por fornecedor ─────────────────────────

def _verba(num, cod, dia, valor, conta=250009, cancel=None):
    return {"NUMVERBA": num, "CODFORNEC": cod, "DTEMISSAO": dia.isoformat(),
            "VALOR": valor, "CODCONTA": conta, "DTCANCEL": cancel, "DTVENC": None}


def test_verba_soma_so_a_janela_pedida():
    """A janela é a MESMA do lucro. Verba de fora do período não pode entrar — senão o
    'lucro com verba' de 1 mês viria somado com 12 meses de verba."""
    verbas = [_verba(1, 10, date(2026, 7, 5), 10_000.0),
              _verba(2, 10, date(2026, 1, 5), 90_000.0)]     # fora da janela
    r = core.verba_por_fornecedor(verbas, [], ini=date(2026, 7, 1), fim=HOJE,
                                  forn_map=_forn_map(), cnpj_empresa=CNPJ_EMPRESA)
    assert r[10]["verba"] == 10_000.0


def test_verba_cancelada_fica_fora():
    verbas = [_verba(1, 10, date(2026, 7, 5), 10_000.0),
              _verba(2, 10, date(2026, 7, 6), 5_000.0, cancel="2026-07-07")]
    r = core.verba_por_fornecedor(verbas, [], ini=date(2026, 7, 1), fim=HOJE,
                                  forn_map=_forn_map(), cnpj_empresa=CNPJ_EMPRESA)
    assert r[10]["verba"] == 10_000.0


def test_campanha_entra_no_total_mas_sai_identificada():
    """Decisão do diretor: por ora TODA verba entra. Mas a parcela de campanha viaja à parte
    para a tela avisar o tamanho do que ainda falta refinar (e o opt-out ser trivial depois)."""
    verbas = [_verba(1, 10, date(2026, 7, 5), 10_000.0, conta=250009),   # rebaixa de custo
              _verba(2, 10, date(2026, 7, 6), 4_000.0, conta=200013)]    # premiações e campanhas
    r = core.verba_por_fornecedor(verbas, [], ini=date(2026, 7, 1), fim=HOJE,
                                  forn_map=_forn_map(), cnpj_empresa=CNPJ_EMPRESA)
    assert r[10]["verba"] == 14_000.0
    assert r[10]["verba_campanha"] == 4_000.0
    assert 200013 in core.CONTAS_VERBA_CAMPANHA


def test_verba_de_transferencia_entre_filiais_fica_fora():
    verbas = [_verba(1, 99, date(2026, 7, 5), 50_000.0)]
    r = core.verba_por_fornecedor(verbas, [], ini=date(2026, 7, 1), fim=HOJE,
                                  forn_map=_forn_map(), cnpj_empresa=CNPJ_EMPRESA)
    assert r == {}


# ───────────────────────── integração com fornecedores() ─────────────────────────

def _prod(cod, codfornec, venda, lucro, valor=1000.0):
    return {"codprod": cod, "codfornec": codfornec, "fornecedor": "BOMBRIL SA",
            "comprador": "Carlos", "valor": valor, "giro_mes": 10.0, "venda": venda,
            "lucro": lucro, "qtdisp": 100.0, "giro_dia": 1.0, "venda_ano_ant": 0.0}


def test_lucro_com_verba_soma_e_margem_sobe():
    extra = {10: {"n_pedidos": 4, "ciclo_dias": 45.0, "ultima_compra": "2026-07-10",
                  "verba": 20_000.0, "verba_campanha": 5_000.0}}
    linhas = core.fornecedores([_prod(1, 10, venda=100_000.0, lucro=20_000.0)], extra=extra)
    f = linhas[0]
    assert f["lucro"] == 20_000.0
    assert f["margem"] == 20.0
    assert f["verba"] == 20_000.0
    assert f["lucro_verba"] == 40_000.0
    assert f["margem_verba"] == 40.0          # a verba DOBRA o retorno deste fornecedor
    assert f["n_pedidos"] == 4
    assert f["ciclo_dias"] == 45.0


def test_fornecedor_sem_verba_tem_lucro_com_verba_igual_ao_lucro():
    linhas = core.fornecedores([_prod(1, 10, venda=100_000.0, lucro=20_000.0)], extra={})
    f = linhas[0]
    assert f["verba"] == 0
    assert f["lucro_verba"] == f["lucro"] == 20_000.0
    assert f["n_pedidos"] == 0
    assert f["ciclo_dias"] is None


def test_lucro_nao_e_recalculado_por_venda_vezes_margem():
    """Prova que a coluna exibe o lucro AGREGADO e não o caminho de volta venda × margem —
    esse round-trip passa por um % arredondado a 1 casa e faria a aba divergir do Comercial."""
    # margem arredondada = 33.3%; venda × 33.3% = 33.300,00, mas o lucro real é 33.333,33
    linhas = core.fornecedores([_prod(1, 10, venda=100_000.0, lucro=33_333.33)], extra={})
    f = linhas[0]
    assert f["margem"] == 33.3
    assert f["lucro"] == 33_333.33
    assert f["lucro"] != round(f["venda"] * f["margem"] / 100, 2)


def test_sem_extra_a_aba_continua_funcionando():
    """Se o lead time / verbas caírem, o mapa vem vazio e a aba não pode quebrar."""
    linhas = core.fornecedores([_prod(1, 10, venda=1000.0, lucro=100.0)], extra=None)
    assert linhas[0]["lucro_verba"] == 100.0
