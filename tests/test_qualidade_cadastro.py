"""Gate do bloco "Cadastro logístico" da aba Qualidade da base (08/2026).

Pedido do diretor: "a lista dos itens com erro de cadastro que vc me enviou, não conseguimos
deixar ela em uma aba na gestão de estoque, para ficar mais fácil consultar tudo que está
errado no sistema?".

Decisão central que estes testes travam: o bloco roda sobre a **BASE INTEIRA**, não sobre o
snapshot. As checagens antigas da aba dependem de saldo ("sem giro c/ estoque", "estoque
negativo") e por isso seguem o snapshot, que é recortado por FILIAL. Estas são do CADASTRO —
o erro está no produto, exista ele em qual filial for. Medido no BI: **72** produtos na base
contra **21** dentro do snapshot do Atacado. Ligar a checagem no snapshot faria a tela dizer
21 enquanto a planilha enviada ao cliente dizia 70 — e alguém perguntaria cadê o resto.
"""
from estoque import core

# 66919 é o caso real: 5,3 kg e 0,09879 m³ gravados no registro da UNIDADE, sendo os números
# da CAIXA de 100 → 530 kg/caixa. Os demais são cadastros sadios ou incompletos.
PROD = {
    66919: {"CODPROD": 66919, "DESCRICAO": "EMB.GALV.G31CT P/BOLO PRETA", "CODFORNEC": 7,
            "QTUNITCX": 100, "PESOBRUTO": 5.3, "PESOLIQ": 4.6, "VOLUME": 0.09879},
    52000: {"CODPROD": 52000, "DESCRICAO": "PAPEL OFICIO A4", "CODFORNEC": 7,
            "QTUNITCX": 10, "PESOBRUTO": 2.97, "PESOLIQ": 2.9, "VOLUME": 3465.0},
    41896: {"CODPROD": 41896, "DESCRICAO": "CATCHUP GL 3,4KG", "CODFORNEC": 9,
            "QTUNITCX": 4, "PESOBRUTO": 3.528, "PESOLIQ": 3.5, "VOLUME": 0.00588},
    99001: {"CODPROD": 99001, "DESCRICAO": "ITEM SEM CUBAGEM", "CODFORNEC": 9,
            "QTUNITCX": 12, "PESOBRUTO": 0.4, "PESOLIQ": 0.38, "VOLUME": 0},
}
EMB = {66919: {"qtunit": 100}, 52000: {"qtunit": 10}, 41896: {"qtunit": 4}}
FORN = {7: {"FORNECEDOR": "GALVANOTEK", "CODCOMPRADOR": 47},
        9: {"FORNECEDOR": "ALIMENTOS WILSON", "CODCOMPRADOR": 12}}
COMP = {47: "JOÃO VICTOR", 12: "RONILSON"}


def _run(**kw):
    return core.qualidade_cadastro(PROD, EMB, FORN, COMP, **kw)


def test_pega_o_cadastro_impossivel_e_ignora_o_sadio():
    r = _run()
    cods = {p["codprod"] for p in r["produtos"]}
    assert 66919 in cods and 52000 in cods       # máster na unidade · cm³ no campo de m³
    assert 41896 not in cods                     # caixa de 4 galões: grande, mas real


def test_a_caixa_implicada_e_o_que_denuncia():
    """5,3 kg por unidade parece plausível; 530 kg por caixa, não. É a coluna que explica
    ao TI por que aquele cadastro está errado."""
    p = next(x for x in _run()["produtos"] if x["codprod"] == 66919)
    assert p["caixa_kg"] == 530.0 and p["un_por_cx"] == 100
    assert "Cadastro impossível" in p["problemas"]


def test_sem_cubagem_e_categoria_separada():
    p = next(x for x in _run()["produtos"] if x["codprod"] == 99001)
    assert p["categorias"] == ["sem_cubagem"] and p["volume_un_m3"] is None
    assert _run()["resumo"]["contagem"]["sem_cubagem"] >= 1


def test_um_produto_pode_ter_dois_problemas():
    """52000 tem cm³ no campo de m³: reprova a guarda E não tem cubagem utilizável? Não —
    aqui ele tem volume (absurdo), então só a 1ª categoria. O teste trava a distinção."""
    p = next(x for x in _run()["produtos"] if x["codprod"] == 52000)
    assert p["categorias"] == ["cadastro_impossivel"]


def test_universo_e_a_base_inteira_nao_o_snapshot():
    """O ponto do pedido: `base` conta TODOS os produtos recebidos, não os que têm estoque."""
    r = _run()
    assert r["resumo"]["base"] == len(PROD) == 4
    assert r["resumo"]["total"] == len(r["produtos"])


def test_filtro_de_comprador_recorta():
    """Permite mandar a lista já separada por quem cadastra."""
    assert {p["codprod"] for p in _run(comprador=47)["produtos"]} == {66919, 52000}
    assert {p["codprod"] for p in _run(comprador=12)["produtos"]} == {99001}


def test_ordena_do_pior_para_o_menos_pior():
    """O TI ataca de cima para baixo: a caixa mais absurda primeiro."""
    cods = [p["codprod"] for p in _run()["produtos"]]
    assert cods[0] == 52000                       # 34.650 m³/caixa
    assert cods.index(66919) < cods.index(99001)


def test_cadastro_vazio_nao_explode():
    r = core.qualidade_cadastro({}, {}, {}, {})
    assert r["produtos"] == [] and r["resumo"]["total"] == 0


def test_limiares_viajam_para_a_tela():
    """A tela explica o critério ("caixa acima de X m³ ou Y kg"); se o número ficasse
    hardcoded no front, mudar a guarda no core faria a explicação mentir."""
    r = _run()["resumo"]
    assert r["max_m3_caixa"] == core.MAX_M3_CAIXA
    assert r["max_kg_caixa"] == core.MAX_KG_CAIXA


def test_a_tela_declara_os_dois_universos():
    """Gate de código. Os dois blocos vivem na MESMA aba com escopos diferentes — se um
    deles parar de dizer qual é o seu, volta o "cadê o resto?"."""
    from pathlib import Path
    js = Path('static/estoque/estoque.js').read_text(encoding='utf-8')
    bloco1 = js[js.index('function renderQualidade'):js.index('async function renderQualCadastro')]
    bloco2 = js[js.index('async function renderQualCadastro'):]
    bloco2 = bloco2[:bloco2.index('function renderReposicao')]
    assert 'snapshot' in bloco1, 'o bloco de saldo parou de declarar que segue o snapshot'
    assert 'Base inteira' in bloco2, 'o bloco de cadastro parou de declarar a base inteira'
    assert 'qualidade_cadastro.xlsx' in bloco2, 'o bloco de cadastro perdeu o export'


# ── barra de rolagem da página (reclamação do diretor 08/2026: "some no tema branco") ──
def test_barra_da_pagina_tem_token_proprio_e_contraste():
    """Com `--surface3` o polegar dava 1,15:1 no claro e 1,40:1 no escuro — invisível. A WCAG
    pede 3:1 para componente de UI. O token vive no tema.css porque cor cravada sumiria em um
    dos dois temas (mesma lição do bloco `.tbl-wrap`, que já usava variável)."""
    from pathlib import Path
    css = Path('static/estoque/estoque.css').read_text(encoding='utf-8')
    bloco = css[css.index('::-webkit-scrollbar {'):css.index('@keyframes fadeUp')]
    assert 'var(--scroll-thumb)' in bloco
    assert 'var(--surface3)' not in bloco, 'a barra da página voltou à cor que sumia'

    tema = Path('static/tema.css').read_text(encoding='utf-8')
    assert tema.count('--scroll-thumb:') == 2, 'o token tem de existir nos DOIS temas'

    def _lum(h):
        h = h.lstrip('#')
        c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        f = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
        return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2]

    def _ct(a, b):
        la, lb = _lum(a), _lum(b)
        return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)

    import re
    thumbs = re.findall(r'--scroll-thumb:\s*(#[0-9a-fA-F]{6})', tema)
    bgs = re.findall(r'--bg:\s*(#[0-9a-fA-F]{6})', tema)
    assert len(thumbs) == 2 and len(bgs) >= 2
    for thumb, bg in zip(thumbs, bgs[:2]):
        assert _ct(bg, thumb) >= 3.0, f'{thumb} sobre {bg} = {_ct(bg, thumb):.2f}:1 (mínimo 3:1)'


def test_color_scheme_declarado_nos_dois_temas():
    """O que o NAVEGADOR desenha — barra de rolagem, a lista que abre num <select>, checkbox,
    seletor de data — ignora as variáveis do tema.css e sai no modo claro por padrão. Sem esta
    declaração, o tema escuro (default do app) mostrava nas 17 telas do Comercial gatilho de
    select escuro abrindo popup BRANCO, e barra clara sobre fundo quase preto. Verificado no
    navegador em /, /carteira, /vendedores, /metas e /estoque/: computa `dark` e `light`.

    ⚠️ Tem de existir nos DOIS: declarar só o escuro deixaria o claro no default implícito, e
    a troca de tema pararia de ser simétrica."""
    from pathlib import Path
    css = Path('static/tema.css').read_text(encoding='utf-8')
    # ancora na REGRA (com a chave), não no seletor solto: o comentário do cabeçalho do
    # arquivo cita `:root[data-tema="claro"]` em prosa, antes de qualquer regra
    corte = css.index(':root[data-tema="claro"] {')
    escuro = css[css.index(':root {'):corte]
    claro = css[corte:]
    assert 'color-scheme: dark' in escuro, 'tema escuro sem color-scheme'
    assert 'color-scheme: light' in claro, 'tema claro sem color-scheme'
