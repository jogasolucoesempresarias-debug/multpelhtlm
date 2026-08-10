"""Gate das medidas logísticas (peso e cubagem) — core.medidas_unitarias.

Contexto (08/2026, achado pelo comprador comparando documento com documento): o PDF da JOGA
dizia 6.758 kg num pedido em que o Winthor dizia 14.497,64 — 53% a menos. Não era erro de
fórmula, era FONTE: o peso saía do `PCEMBALAGEM[PESOBRUTO]`, vazio em 75,6% dos produtos de
revenda (o `VOLUME` da mesma tabela, em 100%). O item de maior peso do pedido pesava zero.

Decisões que estes testes travam:
  · a fonte é o PCPRODUT, e a conta é sobre a quantidade em UNIDADES — é assim que o
    Winthor chega aos três totais do rodapé do 211;
  · cadastro cuja CAIXA implicada é fisicamente impossível não vira número na tela: alguém
    gravou o dado do máster no registro da unidade e o "× fator" infla pelo fator inteiro;
  · o rótulo da unidade sai do texto da embalagem (FD), o FATOR não.
"""
from estoque import core

# ── pedido 565848 · fornecedor 9406 (ALIMENTOS WILSON) · 22 itens ──
# qtd em CAIXAS, fator, peso líq/bruto unitário e volume unitário, todos do cadastro real.
# Rodapé do 211: Peso Líquido 14.482,02 · Peso Bruto 14.497,64 · Volume 23,50
PEDIDO_565848 = [
    # (cod, qtd_cx, fator, peso_liq_un, peso_bruto_un, volume_un) — cadastro real, precisão cheia
    (41896, 80, 4, 3.528, 3.528, 0.00588), (49449, 20, 4, 3.65, 3.65, 0.00588),
    (49395, 40, 24, 0.212, 0.217917, 0.0003825), (41901, 200, 8, 1.6, 1.6, 0.00341775),
    (49447, 350, 6, 3.164, 3.164, 0.0040095), (46661, 70, 24, 0.212, 0.217917, 0.0003825),
    (56981, 2, 12, 0.55, 0.55, 0.00087012), (42908, 80, 8, 1.6, 1.6, 0.00341775),
    (53908, 10, 8, 1.6, 1.6, 0.00341775), (51439, 6, 12, 1.044, 1.044, 0.00195075),
    (59353, 2, 12, 0.18, 0.18, 0.0004125), (57456, 15, 4, 3.7, 3.7, 0.0059192),
    (52083, 15, 12, 1.03, 1.03, 0.00198688), (51440, 8, 12, 0.19, 0.19, 0.00033413),
    (51441, 4, 24, 0.188, 0.188, 0.0004125), (54846, 4, 12, 0.198, 0.198, 0.0004125),
    (58554, 45, 6, 2.04, 2.04, 0.00336), (49463, 60, 4, 3.32, 3.32, 0.00588),
    (47115, 5, 8, 1.6, 1.6, 0.00341775), (67610, 10, 20, 0.375, 0.375, 0.00071533),
    (67548, 5, 20, 0.375, 0.375, 0.00071533), (67549, 10, 20, 0.375, 0.375, 0.0007254),
]
WINTHOR = {"liq": 14482.02, "bru": 14497.64, "m3": 23.50}


def _cad(pliq, pbru, vol):
    return {"PESOLIQ": pliq, "PESOBRUTO": pbru, "VOLUME": vol}


def test_reproduz_os_tres_totais_do_winthor():
    """Caso REAL travado: os três números do rodapé do 211, do pedido que o comprador
    comparou lado a lado. Se algum divergir, o documento voltou a discordar do ERP."""
    liq = bru = m3 = 0.0
    for _cod, qcx, fator, pl, pb, vo in PEDIDO_565848:
        med = core.medidas_unitarias(_cad(pl, pb, vo), fator)
        assert med["confiavel"], f"{_cod} reprovado pela guarda sem motivo"
        un = qcx * fator                      # o pedido é gravado em UNIDADES
        liq += un * med["liq"]
        bru += un * med["bruto"]
        m3 += un * med["vol"]
    assert round(liq, 2) == WINTHOR["liq"]
    assert round(bru, 2) == WINTHOR["bru"]
    assert round(m3, 2) == WINTHOR["m3"]


def test_a_conta_e_sobre_unidades_nao_sobre_caixas():
    """O erro de −53% tinha DOIS componentes; este é o segundo. Multiplicar o peso unitário
    pelo número de CAIXAS (em vez de unidades) erraria pelo fator inteiro."""
    med = core.medidas_unitarias(_cad(3.164, 3.164, 0.00401), 6)
    assert round(350 * 6 * med["bruto"], 2) == 6644.40      # 350 caixas de 6 = 2.100 un
    assert round(350 * med["bruto"], 2) != 6644.40


# ── guarda: cadastro com o dado do MÁSTER no registro da unidade ──
def test_guarda_barra_caixa_impossivel_de_peso():
    """66919 (EMB.GALV.G31CT P/BOLO PRETA): o registro da unidade carrega 5,3 kg e 0,09879 m³,
    que são os números da CAIXA de 100 — uma embalagem de bolo não pesa 5,3 kg. Multiplicar
    por 100 daria 530 kg/caixa. Célula vazia é melhor que 530 kg apresentado como fato."""
    med = core.medidas_unitarias(_cad(4.6, 5.3, 0.09879), 100)
    assert med["confiavel"] is False


def test_guarda_nao_barra_cadastro_legitimo():
    """Contraprova: sem ela, "reprova tudo" passaria como correção. Caixa de 4 galões de
    catchup = 14,1 kg e 0,024 m³ — grande, mas real."""
    med = core.medidas_unitarias(_cad(3.528, 3.528, 0.00588), 4)
    assert med["confiavel"] is True


def test_guarda_pega_volume_impossivel_mesmo_com_peso_sao():
    """Os dois sinais são independentes: o 52000 (papel A4) tem cm³ digitado no campo de m³,
    e o peso dele é normal. Só o volume denuncia."""
    med = core.medidas_unitarias(_cad(2.97, 2.97, 3465.0), 10)
    assert med["confiavel"] is False


def test_sem_volume_deriva_das_dimensoes():
    """Fallback herdado da antiga `vol_unitario`, que foi absorvida aqui: A×L×C em cm → m³."""
    med = core.medidas_unitarias({"ALTURAM3": 10, "LARGURAM3": 20, "COMPRIMENTOM3": 30}, 1)
    assert round(med["vol"], 6) == 0.006


def test_cadastro_vazio_nao_explode_nem_inventa():
    assert core.medidas_unitarias(None, 12) == {"vol": 0.0, "bruto": 0.0, "liq": 0.0,
                                                "confiavel": True}


# ── rótulo da unidade ──
def test_rotulo_master_vem_do_texto_da_embalagem():
    """O comprador confere o PDF contra o 211 linha a linha. O Winthor imprime FD no
    fardo; a JOGA imprimia CX em tudo que tivesse fator."""
    assert core.item_master(1600, 8, 1.0, "FD/8X192/UN")[2] == "FD"
    assert core.item_master(320, 4, 1.0, "CX/0004/UN")[2] == "CX"
    assert core.item_master(100, 20, 1.0, "PC/0020/UN")[2] == "PC"


def test_rotulo_cai_em_CX_quando_a_embalagem_nao_ajuda():
    for emb in (None, "", "0004", "EMBALAGEMLONGA/1/UN"):
        assert core.item_master(320, 4, 1.0, emb)[2] == "CX"


def test_sem_fator_continua_UN_independente_do_texto():
    """Sem fator a unidade do Winthor já É a master — o texto não muda isso."""
    assert core.item_master(10, 1, 1.0, "FD/8X192/UN")[2] == "UN"


def test_fator_nao_vem_do_texto_da_embalagem():
    """Só o RÓTULO sai do texto. O número segue o QTUNITCX porque os dois divergem em
    cadastros reais (cód. 57474: texto diz CX/0100/UN, fator real 10)."""
    qtd, preco, un = core.item_master(100, 10, 2.0, "CX/0100/UN")
    assert (qtd, un) == (10, "CX") and preco == 20.0


# ── contrato loader→core (armadilha nº 16 do README) ──
def test_provider_postgres_devolve_as_chaves_que_medidas_unitarias_le():
    """O modo BD tem de entregar as MESMAS chaves do caminho DAX. Chave faltando não quebra:
    o core lê 0 e a tela zera EM SILÊNCIO — foi assim que o peso ficou meses errado."""
    from pathlib import Path
    src = Path('estoque/provider_sql.py').read_text(encoding='utf-8')
    bloco = src[src.index('def cadastro_produto'):]
    bloco = bloco[:bloco.index('def cadastro_fornecedor')]
    for chave in ('"VOLUME"', '"PESOBRUTO"', '"PESOLIQ"', '"ALTURAM3"', '"QTUNITCX"'):
        assert chave in bloco, f'provider_sql.cadastro_produto parou de devolver {chave}'


def test_provider_le_pesoliq_de_forma_tolerante():
    """As bases sintéticas já criadas não têm a coluna `pesoliq`. Referenciá-la direto
    derrubaria o módulo inteiro em modo BD com "column does not exist"."""
    from pathlib import Path
    src = Path('estoque/provider_sql.py').read_text(encoding='utf-8')
    bloco = src[src.index('def cadastro_produto'):]
    bloco = bloco[:bloco.index('def cadastro_fornecedor')]
    assert "to_jsonb" in bloco and "'pesoliq'" in bloco, \
        'leitura de pesoliq deixou de ser tolerante a base antiga'


def test_seed_da_demo_cria_a_coluna():
    """Base nova já nasce completa — senão a demo fica degradada para sempre."""
    from pathlib import Path
    assert 'pesoliq' in Path('_seed_demo/schema.sql').read_text(encoding='utf-8')
    assert '"pesoliq"' in Path('_seed_demo/gerar.py').read_text(encoding='utf-8')
