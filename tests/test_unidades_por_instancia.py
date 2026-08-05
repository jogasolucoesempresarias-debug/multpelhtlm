"""Gate da configuração de FILIAIS/UNIDADES por instância (achado do diretor 07/2026).

Ele viu na DEMO o seletor mostrando "4 – A&M", "14 – AC", "9 – JID": a nomenclatura interna da
Multpel exposta a qualquer prospect. Não vinha do DAX nem do banco da demo — estava cravada no
código, e demo e produção rodam a mesma imagem.

Pior que o nome: a base sintética só tem as filiais **3 e 5**, então três das cinco unidades
abriam VAZIAS. Tela em branco numa apresentação custa mais que nome estranho.

E um terceiro efeito, achado ao investigar: a filial 5 não aparecia na lista de `venda` de
NENHUMA unidade (correto para a Multpel, onde 5 é depósito) — mas a base sintética tem 299.760
linhas de venda nela. A demo escondia ~26% do próprio faturamento.

Estes testes travam a mesma régua já usada em `EMPRESA_*`: env var por instância, default no
cliente atual, e nunca quebrar o boot por JSON torto (a variável é editada à mão no Portainer).
"""
import importlib
import json

import pytest


def _recarrega(monkeypatch, **env):
    """Reimporta o módulo com as env vars aplicadas — a config é lida no import."""
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    from estoque import routes
    return importlib.reload(routes)


@pytest.fixture(autouse=True)
def _restaura():
    """Devolve o módulo ao estado padrão — outros testes leem UNIDADES no import."""
    yield
    import os
    for k in ("NOMES_FILIAL_JSON", "UNIDADES_JSON", "UNIDADE_PADRAO"):
        os.environ.pop(k, None)
    from estoque import routes
    importlib.reload(routes)


# ─────────────────── produção intocada ───────────────────
def test_sem_env_o_comportamento_e_o_de_hoje(monkeypatch):
    """A instância do cliente não precisa de configuração nenhuma — risco zero na produção."""
    r = _recarrega(monkeypatch, NOMES_FILIAL_JSON=None, UNIDADES_JSON=None, UNIDADE_PADRAO=None)
    assert r.UNIDADE_PADRAO == "atacado"
    assert set(r.UNIDADES) == {"atacado", "am", "ac", "jid", "todas"}
    assert r.NOMES_FILIAL["4"] == "A&M"
    assert r.UNIDADES["atacado"]["venda"] == ["3", "7", "8"]


# ─────────────────── a demo ───────────────────
def test_demo_sobrescreve_nomes_e_unidades(monkeypatch):
    r = _recarrega(
        monkeypatch,
        NOMES_FILIAL_JSON='{"3":"Matriz","5":"Centro de Distribuição"}',
        UNIDADES_JSON=json.dumps({
            "matriz": {"nome": "Matriz", "estoque": ["3"], "venda": ["3"]},
            "cd": {"nome": "Centro de Distribuição", "estoque": ["5"], "venda": ["5"]},
            "todas": {"nome": "Todas", "estoque": ["3", "5"], "venda": ["3", "5"]}}),
        UNIDADE_PADRAO="todas")
    assert r.UNIDADE_PADRAO == "todas"
    # nenhum resquício da estrutura do cliente
    nomes = json.dumps(r.UNIDADES, ensure_ascii=False) + json.dumps(r.NOMES_FILIAL, ensure_ascii=False)
    for vazamento in ("A&M", "JID", "AC", "Telemarketing", "Deposito"):
        assert vazamento not in nomes


def test_toda_unidade_da_demo_tem_filial_que_existe_na_base():
    """A regra que o bug violava: unidade oferecida no seletor TEM de ter dado por trás.
    A base sintética só tem as filiais 3 e 5 — qualquer outra abre a tela em branco."""
    import re
    from pathlib import Path
    compose = Path("docker-compose.demo.yml").read_text(encoding="utf-8")
    bruto = re.search(r"UNIDADES_JSON:\s*'(\{.*?\})'", compose, re.S)
    assert bruto, "a stack da demo tem de fixar UNIDADES_JSON"
    unidades = json.loads(bruto.group(1))
    existentes = {"3", "5"}
    for uid, u in unidades.items():
        assert set(u["estoque"]) <= existentes, f"unidade {uid} tem filial de estoque inexistente"
        assert set(u["venda"]) <= existentes, f"unidade {uid} tem filial de venda inexistente"
        assert u["estoque"] and u["venda"], f"unidade {uid} abriria vazia"


def test_demo_enxerga_a_venda_das_DUAS_filiais():
    """A filial 5 não estava em nenhuma lista de `venda` (certo para a Multpel, onde é depósito),
    mas na base sintética ela tem 299.760 linhas — 26% do faturamento invisível na demo."""
    import re
    from pathlib import Path
    compose = Path("docker-compose.demo.yml").read_text(encoding="utf-8")
    unidades = json.loads(re.search(r"UNIDADES_JSON:\s*'(\{.*?\})'", compose, re.S).group(1))
    vendas = {f for u in unidades.values() for f in u["venda"]}
    assert vendas == {"3", "5"}


# ─────────────────── nunca quebrar o boot ───────────────────
@pytest.mark.parametrize("torto", [
    "{isso nao e json",                     # vírgula/aspas erradas no Portainer
    '{"x":{"nome":"X"}}',                   # falta estoque/venda
    '{"x":{"estoque":["3"],"venda":["3"]}}',  # falta nome
    "[]", "{}", '"texto"',                  # tipo errado / vazio
])
def test_json_torto_cai_no_padrao_sem_derrubar(monkeypatch, torto):
    """Env var é editada à mão; uma vírgula sobrando não pode impedir o app de subir."""
    r = _recarrega(monkeypatch, UNIDADES_JSON=torto)
    assert set(r.UNIDADES) == {"atacado", "am", "ac", "jid", "todas"}


def test_padrao_inexistente_nao_causa_KeyError(monkeypatch):
    """`UNIDADES[_unidade()]` roda em toda tela: um padrão apontando para unidade que não existe
    derrubaria tudo. Cai na primeira unidade configurada."""
    r = _recarrega(monkeypatch,
                   UNIDADES_JSON='{"unica":{"nome":"Única","estoque":["3"],"venda":["3"]}}',
                   UNIDADE_PADRAO="nao_existe")
    assert r.UNIDADE_PADRAO == "unica"
    assert r.UNIDADES[r.UNIDADE_PADRAO]["nome"] == "Única"


def test_unidade_invalida_na_querystring_cai_no_padrao(monkeypatch):
    """Já era o comportamento; o gate garante que continua valendo com config customizada."""
    r = _recarrega(monkeypatch,
                   UNIDADES_JSON='{"unica":{"nome":"Única","estoque":["3"],"venda":["3"]}}',
                   UNIDADE_PADRAO="unica")
    import server  # noqa: F401
    from server import app
    with app.test_request_context("/estoque/api/snapshot?unidade=atacado"):
        assert r._unidade() == "unica"
