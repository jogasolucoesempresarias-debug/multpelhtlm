"""Gate do acesso SÓ-COMPRAS no Admin (bug de produção, 08/2026).

Sintoma: ao criar o PRIMEIRO usuário só de Compras, a tela devolvia
"Erro: Vendedor exige codusur" — falando de um papel que ninguém escolheu, num campo que não
estava na tela.

Causa, nos dois lados:
  · FRONT — o select de papel tem "Vendedor" como 1ª opção, então é o valor inicial de todo
    formulário novo. Ao desmarcar COMERCIAL, `atualizarVisibilidadeRoleFields` ESCONDIA o bloco
    (correto: papel e codusur são RBAC do Comercial) mas não LIMPAVA o valor. O envio mandava
    role=vendedor com codusur vazio.
  · SERVIDOR — exigia `codusur` de vendedor sem olhar as áreas. Vendedor sem Comercial é uma
    combinação sem sentido, e a exigência não deveria nem se aplicar.

Os dois foram corrigidos: só o front consertaria a tela, mas a API também é chamada por script.
"""
import pytest

from tests.conftest import _criar_usuario, _remover_usuario, login_as


@pytest.fixture
def admin_logado(client):
    email = "adm-socompras@teste.local"
    uid = _criar_usuario(email, "senha123", role="admin", must_change=False)
    import server
    conn = server.get_db()
    cur = conn.cursor()
    cur.execute("""UPDATE multpel_users SET areas = '["comercial","compras"]'::jsonb, ativo = true
                   WHERE email = %s""", (email,))
    conn.commit()
    cur.close()
    conn.close()
    login_as(client, email, "senha123")
    yield uid
    _remover_usuario(email)


def _novo(client, **kw):
    corpo = {"nome": "BRUNO", "email": "bruno-so-compras@teste.local", "role": "viewer",
             "codusur": "", "areas": ["compras"], "senha": "joga123", "ativo": True}
    corpo.update(kw)
    r = client.post("/api/admin/users", json=corpo)
    return r, r.get_json() or {}


def test_o_payload_que_producao_recusou_agora_passa(client, admin_logado):
    """Reprodução exata: role=vendedor (1ª opção do select, invisível na tela) + só Compras."""
    try:
        r, j = _novo(client, role="vendedor")
        assert r.status_code == 200, j
        assert j.get("ok") is True
    finally:
        _remover_usuario("bruno-so-compras@teste.local")


def test_vendedor_COM_comercial_continua_exigindo_codusur(client, admin_logado):
    """Contraprova. Sem ela, "aceita tudo" passaria como correção — e o RBAC do Comercial
    depende do codusur para recortar a venda."""
    r, j = _novo(client, role="vendedor", areas=["comercial"])
    assert r.status_code == 400 and "codusur" in j.get("error", "")


def test_supervisor_sem_comercial_tambem_passa(client, admin_logado):
    """O mesmo defeito valia para supervisor, que exige `codsupervisor`."""
    try:
        r, j = _novo(client, role="supervisor")
        assert r.status_code == 200, j
    finally:
        _remover_usuario("bruno-so-compras@teste.local")


def test_supervisor_COM_comercial_continua_exigindo_area(client, admin_logado):
    r, j = _novo(client, role="supervisor", areas=["comercial"])
    assert r.status_code == 400 and "codsupervisor" in j.get("error", "")


# ── front ──
def _admin_html():
    from pathlib import Path
    s = Path('admin.html').read_text(encoding='utf-8')
    ini = s.index('function atualizarVisibilidadeRoleFields')
    return s[ini:s.index('ff_relatorios_estoque', ini)]


def test_front_neutraliza_o_papel_ao_desmarcar_comercial():
    """Esconder não é limpar: o campo somia da tela e continuava mandando "vendedor"."""
    bloco = _admin_html()
    assert "selRole.value = 'viewer'" in bloco
    assert "!temComercial" in bloco


def test_front_NAO_rebaixa_admin_nem_viewer():
    """`admin` com acesso só a Compras é legítimo — a Administração é área neutra (README).
    Neutralizar tudo rebaixaria silenciosamente um admin ao editá-lo."""
    bloco = _admin_html()
    assert "'vendedor' || selRole.value === 'supervisor'" in bloco.replace('\n', ' '), \
        'a neutralização deixou de se limitar aos papéis que exigem campo do Comercial'


def test_front_nao_usa_papel_obsoleto_para_decidir_visibilidade():
    """A neutralização acontece no meio da função; usar o valor lido antes dela deixaria a
    decisão de mostrar/esconder baseada num papel que já mudou."""
    bloco = _admin_html()
    assert "const roleEfetivo = selRole.value" in bloco
    assert "role === 'vendedor'" not in bloco, 'voltou a usar a variável lida antes da troca'
