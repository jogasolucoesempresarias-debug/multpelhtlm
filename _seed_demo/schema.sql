-- =====================================================================
-- schema.sql — Tabelas Winthor (sintéticas) no formato que o app consome.
-- Derivado do inventário real: server.py (Comercial/RCA) + estoque/queries.py (Compras).
-- Identificadores em minúsculo (convenção Postgres); a camada de acesso/import
-- mapeia para os nomes Winthor MAIÚSCULOS usados no DAX.
--
-- Organizado por dataset de origem (RCA / META / Estoque). Tabelas compartilhadas
-- entre datasets (pcprodut, pcempr, pcfornec) existem UMA vez aqui.
-- =====================================================================

-- ---------------------------------------------------------------------
-- DIMENSÕES COMPARTILHADAS
-- ---------------------------------------------------------------------

-- Produtos (revenda) — universo: revenda='S' e obs2<>'FL'
CREATE TABLE IF NOT EXISTS pcprodut (
    codprod                 INTEGER PRIMARY KEY,
    descricao               TEXT,
    codfab                  TEXT,
    percipi                 NUMERIC(6,2),
    codfornec               INTEGER,
    codepto                 INTEGER,
    codsec                  INTEGER,
    embalagem               TEXT,
    qtunitcx                NUMERIC(12,3),
    classificfiscal         TEXT,          -- NCM
    marca                   TEXT,
    codmarca                INTEGER,
    prazoval                INTEGER,
    controlavalidadedolote  CHAR(1),       -- 'S'/'N'
    volume                  NUMERIC(12,4),
    alturam3                NUMERIC(12,4),
    larguram3               NUMERIC(12,4),
    comprimentom3           NUMERIC(12,4),
    pesobruto               NUMERIC(12,4),
    pesoliq                 NUMERIC(12,4),   -- peso líquido do rodapé do 211 (08/2026)
    revenda                 CHAR(1) DEFAULT 'S',
    obs2                    TEXT
);

-- Fornecedores
CREATE TABLE IF NOT EXISTS pcfornec (
    codfornec       INTEGER PRIMARY KEY,
    fornecedor      TEXT,
    fantasia        TEXT,
    codcomprador    INTEGER,       -- -> pcempr.matricula
    prazoentrega    INTEGER,
    vlminpedcompra  NUMERIC(14,2),
    cgc             TEXT,          -- CNPJ (raiz de 8 díg. define transferência entre filiais)
    ie              TEXT,
    numeroend       TEXT,
    bairro          TEXT,
    cep             TEXT,
    cidade          TEXT,
    estado          CHAR(2),
    email           TEXT
);

-- Funcionários (compradores; nome do comprador) — presente em RCA e Estoque
CREATE TABLE IF NOT EXISTS pcempr (
    matricula   INTEGER PRIMARY KEY,
    nome        TEXT
);

-- ---------------------------------------------------------------------
-- DATASET RCA (Comercial)
-- ---------------------------------------------------------------------

-- Vendedores / RCAs
CREATE TABLE IF NOT EXISTS pcusuari (
    codusur         INTEGER PRIMARY KEY,
    nome            TEXT,
    codsupervisor   INTEGER,
    tipovend        CHAR(1),
    cidade          TEXT,
    estado          CHAR(2),
    bloqueio        CHAR(1) DEFAULT 'N'
);

-- Supervisores
CREATE TABLE IF NOT EXISTS pcsuperv (
    codsupervisor   INTEGER PRIMARY KEY,
    nome            TEXT,
    tiposupervisor  TEXT
);

-- Clientes
CREATE TABLE IF NOT EXISTS pcclient (
    codcli      INTEGER PRIMARY KEY,
    cliente     TEXT,
    fantasia    TEXT,
    municent    TEXT,
    municcob    TEXT,
    estent      CHAR(2),
    telcelent   TEXT,
    telent      TEXT,
    codusur1    INTEGER,        -- RCA dono do cadastro (RBAC por CADASTRO)
    bloqueio    CHAR(1) DEFAULT 'N'
);

-- Calendário
CREATE TABLE IF NOT EXISTS calendario (
    data        DATE PRIMARY KEY,
    anomes      INTEGER,        -- YYYYMM
    ano         INTEGER,
    mes         INTEGER,
    ehdiameta   CHAR(1) DEFAULT 'S'   -- dia útil que conta p/ meta
);

-- Fato: VENDAS (grão = item da nota). Medidas [VENDA BRUTA]/[CUSTO TOTAL] agregam
-- vlvenda/vlcusto (medidas próprias na demo; def. exata do cliente é pendência).
CREATE TABLE IF NOT EXISTS faturamento_vendas (
    id              BIGSERIAL PRIMARY KEY,
    numtransvenda   BIGINT,         -- 1:1 com a nota (join correto; NUNCA numnota)
    numnota         BIGINT,
    dtsaida         DATE,
    codcli          INTEGER,
    cliente         TEXT,
    uf              CHAR(2),
    codusur         INTEGER,
    codsupervisor   INTEGER,
    codprod         INTEGER,
    descricao       TEXT,
    codepto         INTEGER,
    codsec          INTEGER,
    codmarca        INTEGER,
    codfornecprinc  INTEGER,
    fornecprinc     TEXT,
    codfornec       INTEGER,
    codcomprador    INTEGER,
    codfilial       CHAR(1),        -- '3'/'5'
    codoper         CHAR(2),        -- 'S'=venda, 'SB'=bonificação, 'ST'/'SR'=excluídas das medidas
    bonific         CHAR(1),        -- 'S'/'N'
    dtcancel        DATE,           -- preenchida = cancelada
    qt              NUMERIC(14,3),
    vlvenda         NUMERIC(14,2),      -- [VENDA BRUTA] = SUM(vlvenda - icmsretido - vlfecp) WHERE codoper='S'
    icmsretido      NUMERIC(14,2),      -- ICMS-ST retido (descontado da venda bruta; 0 em filial sem ST)
    vlfecp          NUMERIC(14,2),      -- Fundo Combate à Pobreza (descontado da venda bruta)
    vlcustofin      NUMERIC(14,2),      -- custo financeiro
    vlcustofinbonif NUMERIC(14,2)       -- [CUSTO TOTAL] = SUM(vlcustofin+vlcustofinbonif) WHERE codoper IN ('S','SB')
);

-- Fato: DEVOLUÇÃO (por DTENT)
CREATE TABLE IF NOT EXISTS faturamento_devolucao (
    id              BIGSERIAL PRIMARY KEY,
    dtent           DATE,
    codcli          INTEGER,
    codusur         INTEGER,
    codsupervisor   INTEGER,
    codepto         INTEGER,
    departamento    TEXT,
    codsec          INTEGER,
    secao           TEXT,
    codprod         INTEGER,
    codcomprador    INTEGER,
    codativ         INTEGER,        -- 37 = atividade interna (filial como 'cliente')
    coddevol        INTEGER,        -- tipo devol.: 65=solicitação filial, 33=acerto NF (internos), 9=cliente
    codfilial       CHAR(1),
    qt              NUMERIC(14,3),
    vldevolucao     NUMERIC(14,2),  -- [TOTAL DEVOLUCAO] = SUM(vldevolucao) excl. (codativ=37 AND coddevol<>9)
    vlcustofin      NUMERIC(14,2)   -- [CUSTO DEVOLUCAO] = SUM(vlcustofin)  mesma exclusão
);

-- Fato: DEVOLUÇÃO AVULSA (por DTENT)
CREATE TABLE IF NOT EXISTS faturamento_devolucao_avulsa (
    id              BIGSERIAL PRIMARY KEY,
    dtent           DATE,
    codcli          INTEGER,
    uf              CHAR(2),
    codusur         INTEGER,
    codsupervisor   INTEGER,
    codprod         INTEGER,
    codfilial       CHAR(1),
    qt              NUMERIC(14,3),
    vldevolucao     NUMERIC(14,2),  -- base de [TOTAL DEVOLUCAO AVULSA]
    vlcusto         NUMERIC(14,2)   -- base de [CUSTO TOTAL DEVOLUCAO AVULSA]
);

-- ---------------------------------------------------------------------
-- DATASET META (metas por vendedor = PEDIDOS, não faturamento)
-- ---------------------------------------------------------------------

-- Cabeçalho de pedido de venda (grão = pedido)
CREATE TABLE IF NOT EXISTS pcpedc (
    numped      BIGINT PRIMARY KEY,
    data        DATE,
    codusur     INTEGER,
    codcli      INTEGER,
    posicao     CHAR(1),        -- posição do pedido (F=faturado etc.)
    vlatend     NUMERIC(14,2),  -- valor atendido (base da meta realizada)
    vlcustofin  NUMERIC(14,2)
);

-- Itens do pedido de venda
CREATE TABLE IF NOT EXISTS pcpedi (
    numped      BIGINT,
    codprod     INTEGER,
    data        DATE,
    codusur     INTEGER,
    qt          NUMERIC(14,3),
    PRIMARY KEY (numped, codprod)
);

-- ---------------------------------------------------------------------
-- DATASET ESTOQUE (Compras)
-- ---------------------------------------------------------------------

-- Estoque gerencial (tabela-ilha; merge por codprod no app). Filiais '3','5'.
CREATE TABLE IF NOT EXISTS pcest (
    codprod     INTEGER,
    codfilial   CHAR(1),
    qtestger    NUMERIC(14,3),
    qtreserv    NUMERIC(14,3),
    qtbloqueada NUMERIC(14,3),
    qtpendente  NUMERIC(14,3),
    qttransito  NUMERIC(14,3),
    qtvendmes1  NUMERIC(14,3),   -- giro = média (m1+m2+m3)/3
    qtvendmes2  NUMERIC(14,3),
    qtvendmes3  NUMERIC(14,3),
    custofin    NUMERIC(14,4),
    dtultsaida  DATE,
    dtultent    DATE,
    -- quantidade da última entrada: é o TETO de quanto do bloqueio pode ser pré-entrada
    -- (o resto é avaria). Ver core.qt_em_transicao. Nulo = sem teto (comportamento antigo).
    qtultent    NUMERIC(14,3),
    PRIMARY KEY (codprod, codfilial)
);

-- Endereços WMS (posições)
CREATE TABLE IF NOT EXISTS pcendereco (
    codendereco INTEGER PRIMARY KEY,
    codfilial   CHAR(1),
    rua         INTEGER,        -- <>99 = físico endereçado; 99 = pulmão/virtual
    predio      TEXT,
    nivel       TEXT,
    apto        TEXT,
    tipoender   CHAR(2),        -- AP=picking / AE=pulmão
    ativo       CHAR(1) DEFAULT 'S',
    bloqueio    CHAR(1) DEFAULT 'N',
    situacao    CHAR(1)         -- 'O'=ocupada
);

-- Estoque endereçado (QTDISP oficial via soma de qt, rua<>99)
CREATE TABLE IF NOT EXISTS pcestendereco (
    id          BIGSERIAL PRIMARY KEY,
    codprod     INTEGER,
    codendereco INTEGER,
    numlote     TEXT,
    dtval       DATE,           -- validade (FEFO)
    qt          NUMERIC(14,3)
);

-- Embalagem / cubagem
CREATE TABLE IF NOT EXISTS pcembalagem (
    id          BIGSERIAL PRIMARY KEY,
    codprod     INTEGER,
    embalagem   TEXT,
    qtunit      NUMERIC(12,3),  -- fator de caixa
    volume      NUMERIC(12,4),
    altura      NUMERIC(12,4),
    largura     NUMERIC(12,4),
    comprimento NUMERIC(12,4),
    pesobruto   NUMERIC(12,4)
);

-- Pedido de compra — cabeçalho (grão = numped)
CREATE TABLE IF NOT EXISTS pcpedido (
    numped              BIGINT PRIMARY KEY,
    dtemissao           DATE,
    codfilial           CHAR(1),
    codfornec           INTEGER,
    codcomprador        INTEGER,
    vltotal             NUMERIC(14,2),
    vlentregue          NUMERIC(14,2),
    dtvenc              DATE,
    dtentradaestoque    DATE,       -- nulo = ainda aberto (não recebido)
    dtprevent           DATE
);

-- Pedido de compra — itens
CREATE TABLE IF NOT EXISTS pcitem (
    numped      BIGINT,
    codprod     INTEGER,
    qtpedida    NUMERIC(14,3),
    qtentregue  NUMERIC(14,3),
    -- tributação PRATICADA na linha (espelha PCITEM do Winthor). vlipi/vlst são UNITÁRIOS —
    -- é assim no ERP, e é o que `core.montar_tributacao` espera p/ derivar o ST efetivo.
    periipi     NUMERIC(6,2),
    vlipi       NUMERIC(14,6),
    percst      NUMERIC(6,2),
    vlst        NUMERIC(14,6),
    PRIMARY KEY (numped, codprod)
);
-- base semeada antes da v4 não tem as colunas de tributação (o provider faz probe, mas quem
-- rodar o schema por cima ganha as colunas sem precisar regerar 1,17M linhas)
-- ⚠️ MIGRATIONS: coluna nova PRECISA de um ALTER aqui, não basta entrar no CREATE.
-- O `CREATE TABLE IF NOT EXISTS` não toca tabela que já existe, então uma base de demo já
-- criada (a que está no ar) nunca recebe a coluna — e o gerador quebra no COPY com
-- "coluna X da relação Y não existe". Foi o que aconteceu com `pcprodut.pesoliq`: a coluna
-- entrou no CREATE em 08/2026 e a demo publicada seguiu sem ela.
ALTER TABLE pcprodut ADD COLUMN IF NOT EXISTS pesoliq NUMERIC(12,4);
ALTER TABLE pcitem ADD COLUMN IF NOT EXISTS periipi NUMERIC(6,2);
ALTER TABLE pcitem ADD COLUMN IF NOT EXISTS vlipi   NUMERIC(14,6);
ALTER TABLE pcitem ADD COLUMN IF NOT EXISTS percst  NUMERIC(6,2);
ALTER TABLE pcitem ADD COLUMN IF NOT EXISTS vlst    NUMERIC(14,6);

-- Verbas de fornecedor (rotina 1801)
CREATE TABLE IF NOT EXISTS pcverba (
    numverba        BIGINT PRIMARY KEY,
    codfilial       CHAR(1),
    codfornec       INTEGER,
    codcomprador    INTEGER,
    valor           NUMERIC(14,2),
    tipo            TEXT,
    formapgto       TEXT,
    referencia      TEXT,
    codconta        INTEGER,     -- 250009 rebaixa / 250008 conta corrente / 200013 premiação
    dtemissao       DATE,
    dtvenc          DATE,
    dtcancel        DATE         -- preenchida = cancelada
);

-- Aplicações de verba
CREATE TABLE IF NOT EXISTS pcaplicverba (
    id          BIGSERIAL PRIMARY KEY,
    numverba    BIGINT,
    vlaplic     NUMERIC(14,2),
    dtaplic     DATE,
    dtestorno   DATE            -- preenchida = estornada
);

-- Ponte NUMPED -> data 1ª entrada da NF (publicada do Oracle no modelo real)
CREATE TABLE IF NOT EXISTS pedido_entrada (
    numped      BIGINT PRIMARY KEY,
    dtentrada   DATE
);

-- Movimento (vencidos: conta 200042). Join p/ nota por NUMTRANSVENDA.
CREATE TABLE IF NOT EXISTS pcmov (
    id              BIGSERIAL PRIMARY KEY,
    numtransvenda   BIGINT,
    numnota         BIGINT,
    codprod         INTEGER,
    codfilial       CHAR(1),
    qt              NUMERIC(14,3),
    punit           NUMERIC(14,4)
);

-- Nota de saída (data da baixa por validade)
CREATE TABLE IF NOT EXISTS pcnfsaid (
    numtransvenda   BIGINT PRIMARY KEY,
    dtsaida         DATE
);

-- ---------------------------------------------------------------------
-- ÍNDICES (as varreduras quentes: fatos por data e por chave de RBAC)
-- ---------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS ix_fv_dtsaida   ON faturamento_vendas (dtsaida);
CREATE INDEX IF NOT EXISTS ix_fv_codusur   ON faturamento_vendas (codusur);
CREATE INDEX IF NOT EXISTS ix_fv_codcli    ON faturamento_vendas (codcli);
CREATE INDEX IF NOT EXISTS ix_fv_codprod   ON faturamento_vendas (codprod);
CREATE INDEX IF NOT EXISTS ix_fd_dtent     ON faturamento_devolucao (dtent);
CREATE INDEX IF NOT EXISTS ix_fda_dtent    ON faturamento_devolucao_avulsa (dtent);
CREATE INDEX IF NOT EXISTS ix_pcpedc_data  ON pcpedc (data, codusur);
CREATE INDEX IF NOT EXISTS ix_pcest_prod   ON pcest (codprod);
CREATE INDEX IF NOT EXISTS ix_pcee_prod    ON pcestendereco (codprod);
