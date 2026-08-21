"""Gates do CONSUMO DE PRODUÇÃO (rotina 1122) — 08/2026.

CONTEXTO. Investigando por que a filial 9 (JID, a indústria) não aparecia no painel, descobriu-se
que a rotina 1122 (Montar Produtos) dá baixa no componente gravando em `PCMOV` com
`NUMNOTA = 0` — movimento interno. O Winthor atualiza o `DTULTSAIDA` do item mas **não soma em
`QTVENDMES1..3`**, que é de onde o módulo tira o giro. Consequência: demanda que existe e o
painel não vê.

Isso gerou DUAS mudanças, e estes testes separam as duas:

1. **Correção do Atacado** — 50 produtos de revenda das filiais 3+5 têm consumo além da venda.
   Medido: o giro deles estava subestimado e a cobertura inflada ~3x (o 67146 dizia 128 dias e
   eram 49; o 67569 dizia 37 e eram 18). O consumo SOMA à venda, na MESMA janela de 3 meses.

2. **Indústria (filial 9)** — a matéria-prima não vende, só se transforma. Aqui o consumo
   SUBSTITUI a venda como fonte de giro, em janela de 12 meses.

⚠️ O que estes gates protegem, acima de tudo: **as duas mudanças são aditivas e localizadas.**
Medido com fingerprint completo das 5 unidades antes×depois — AM e AC saíram byte a byte iguais,
o Atacado só moveu os 35 itens que têm consumo, e nenhum produto entrou ou saiu de lugar nenhum.
"""
import inspect

import pytest

from estoque import core, queries as Q, routes as R


# ───────────────────────── 1. degradação: sem a tabela, nada muda ─────────────────────────

def test_sem_consumo_o_giro_e_exatamente_o_de_antes():
    """⚠️ O gate mais importante da correção. `CONSUMO_PRODUCAO` é tabela publicada sob demanda
    (como a `TRIB_ENTRADA`): instância que não a tem — ou o modo BD da demo — precisa rodar
    IDÊNTICA. Se este cair, a mudança deixou de ser opt-in e virou regressão para todo cliente."""
    linha = {"giro_m1": 30, "giro_m2": 60, "giro_m3": 90}
    assert core._giro_mensal(linha, "media3") == 60
    assert core._giro_mensal(linha, "media3", None) == 60
    assert core._giro_mensal(linha, "media3", {}) == 60
    assert core._giro_mensal(linha, "m1") == 30
    assert core._giro_mensal(linha, "m1", None) == 30


def test_a_query_padrao_do_cadastro_NAO_menciona_consumo():
    """O universo de sempre não pode passar a depender da tabela nova."""
    padrao = Q.q_cadastro_produto()
    assert "CONSUMO_PRODUCAO" not in padrao
    assert 'PCPRODUT[REVENDA] = "S"' in padrao
    assert 'PCPRODUT[OBS2] <> "FL"' in padrao


# ───────────────────────── 2. correção do Atacado: consumo SOMA ─────────────────────────

def test_consumo_soma_a_venda_mes_a_mes():
    linha = {"giro_m1": 10, "giro_m2": 20, "giro_m3": 30}          # média 20
    consumo = {"m1": 10, "m2": 10, "m3": 10}                        # +10/mês
    assert core._giro_mensal(linha, "media3", consumo) == 30
    assert core._giro_mensal(linha, "m1", consumo) == 20            # só o m1


def test_item_sem_venda_mas_com_consumo_deixa_de_ter_giro_zero():
    """⚠️ O caso que mais dói: item que só é consumido tinha giro 0, logo cobertura 9999,
    logo "sem giro" e sugestão zero. Medido no BI, dois itens do Atacado saíram de 9999 para
    100 e 300 dias de cobertura só com esta linha."""
    linha = {"giro_m1": 0, "giro_m2": 0, "giro_m3": 0}
    assert core._giro_mensal(linha, "media3") == 0
    assert core._giro_mensal(linha, "media3", {"m1": 90, "m2": 90, "m3": 90}) == 90


def test_a_janela_do_consumo_tem_3_slots_e_espelha_QTVENDMES1a3():
    """⚠️ Alinhamento de janela é o erro silencioso desta feature. Somar 4 meses de consumo com
    3 de venda inflaria o giro sem erro nenhum — e foi o que aconteceu na 1ª medição, que deu
    38,7% de consumo sobre venda quando o número alinhado é menor (a janela pegava agosto
    parcial). O mapa só tem m1/m2/m3 justamente para não caber um 4º mês."""
    fonte = inspect.getsource(R._consumo_map)
    assert "_meses_anteriores(hoje, 3)" in fonte, "a janela tem de ser a mesma do QTVENDMES1..3"
    assert 'f"m{i + 1}"' in fonte, "o mês mais recente é m1, espelhando QTVENDMES1"


def test_meses_anteriores_sao_os_3_FECHADOS():
    from datetime import date
    assert core._meses_anteriores(date(2026, 8, 21), 3) == [202607, 202606, 202605]
    assert core._meses_anteriores(date(2026, 1, 5), 3) == [202512, 202511, 202510]


# ───────────────────────── 3. indústria: consumo SUBSTITUI ─────────────────────────

def test_giro_da_industria_SUBSTITUI_a_venda_em_vez_de_somar():
    """⚠️ Matéria-prima não vende, se transforma. Se um dia um item de indústria passar a ter
    venda também, SOMAR contaria a mesma demanda duas vezes. Por isso substitui."""
    fonte = inspect.getsource(core.construir_produtos)
    assert "giro_industria_map" in fonte
    trecho = fonte[fonte.index("giro_ind = "):fonte.index("giro_ind = ") + 400]
    assert "giro_media3 = round(giro_ind)" in trecho, "tem de substituir, não somar"
    assert "else:" in trecho, "sem item de indústria, cai no caminho de sempre"


def test_a_janela_da_industria_e_de_12_meses_e_nao_3():
    """⚠️ Medido no BI: 4 itens não consumiram nos últimos 3 meses mas consumiram 2.465, 2.600,
    2.273 e 1.878 kg no ano — com média de 3 meses teriam giro ZERO e o painel os chamaria de
    estoque morto. E o erro vai para os dois lados: o 58562 projeta 1.749 kg/ano em janela de 3
    contra 8.280 reais (−79%); o 58565 projeta 30.168 contra 25.663 (+18%)."""
    fonte = inspect.getsource(R._industria)
    assert "_meses_anteriores(hoje, 12)" in fonte
    assert "qt / 12.0" in fonte, "o giro é MENSAL — 12 meses divididos por 12"


def test_unidade_sem_filial_industrial_nao_paga_nada():
    """Sem interseção com `FILIAIS_INDUSTRIA` a função sai antes de qualquer I/O: é o que garante
    que o Atacado não pague uma query nem mude um número."""
    from datetime import date
    assert R._industria(["3", "5"], date(2026, 8, 21)) == ({}, {})
    assert R._industria([], date(2026, 8, 21)) == ({}, {})
    assert R._industria(None, date(2026, 8, 21)) == ({}, {})


def test_a_filial_3_NAO_e_industrial_por_default():
    """⚠️ A Matriz (filial 3) TAMBÉM tem movimento de produção — 42.747 lançamentos `SP` no
    Oracle. Se `FILIAIS_INDUSTRIA` fosse derivada do dado ("quem tem SP"), a matéria-prima dela
    entraria no Atacado sem ninguém decidir. Por isso é env explícita, com default "9"."""
    assert R.FILIAIS_INDUSTRIA == {"9"}
    assert "3" not in R.FILIAIS_INDUSTRIA


# ───────────────────────── 4. o universo é o CONSUMO, não REVENDA='N' ─────────────────────────

def test_a_variante_industria_filtra_por_CONSUMO_e_nao_por_revenda():
    """⚠️ A diferença entre feature e regressão. Medido no BI: admitir todo `REVENDA='N'` traria
    +1.612 itens e +R$ 5,9 mi ao Atacado (capital parado 5,9x, sem-giro 45x). Admitir só quem
    CONSOME traz 43 itens, todos da filial 9, e o Atacado ganha R$ 423 — 0,007%."""
    q = Q.q_cadastro_produto(industria_filiais=["9"])
    assert "CONSUMO_PRODUCAO" in q and 'CONSUMO_PRODUCAO[CODOPER] = "SP"' in q
    assert "REVENDA" not in q, "o corte de revenda é justamente o que não se aplica aqui"
    assert 'CONSUMO_PRODUCAO[CODFILIAL] IN {"9"}' in q


def test_as_duas_variantes_do_cadastro_tem_AS_MESMAS_colunas():
    """⚠️ Listas separadas divergiriam no primeiro cadastro novo, e o modo indústria passaria a
    ler `None` numa chave que o `core` espera obrigatória — a tela zeraria em silêncio."""
    import re
    a = re.findall(r'"(\w+)",\s+PCPRODUT', Q.q_cadastro_produto())
    b = re.findall(r'"(\w+)",\s+PCPRODUT', Q.q_cadastro_produto(industria_filiais=["9"]))
    assert a == b and len(a) >= 19


def test_o_consumo_pede_SP_por_default_e_aceita_EP():
    """`SP` = saída de produção (consumo do componente). `EP` = entrada (o produto montado) —
    validado contra o rodapé do relatório da 1122: 20+60+3+40 = 123, exato."""
    assert 'CONSUMO_PRODUCAO[CODOPER] = "SP"' in Q.q_consumo_producao(["9"])
    assert 'CONSUMO_PRODUCAO[CODOPER] = "EP"' in Q.q_consumo_producao(["9"], oper="EP")


def test_a_janela_de_meses_vira_lista_de_DATE_no_dax():
    q = Q.q_consumo_producao(["9"], meses=[202607, 202606])
    assert "DATE(2026, 7, 1)" in q and "DATE(2026, 6, 1)" in q


# ───────────────────────── 5. modo BD (demo) declara o vazio ─────────────────────────

def test_o_provider_declara_o_vazio_em_vez_de_estourar():
    """A base sintética não tem produção. Sem estes stubs o modo BD cairia no `except` por
    `AttributeError` — funciona, mas enche o log da demo de erro a cada 30 min e esconde uma
    falha de verdade no meio."""
    from estoque import provider_sql as PS
    assert PS.consumo_producao(["9"], [202607]) == []
    assert PS.cadastro_produto(industria_filiais=["9"]) == {}
