# _seed_demo — Alimentador da base de demonstração (Winthor sintético)

> **Isolado de propósito.** Este diretório NÃO entra na imagem que vai pro cliente
> (mesma lógica do `_estoque_app`). Ele **produz** o banco da demo; não é consumido
> em produção de cliente nenhum. Deve ficar no `.gitignore` do build do app.

## Para que serve

Hoje o app JOGA Analytics consome **3 datasets de Power BI** do cliente (RCA / META /
Estoque). Este pacote recria **as mesmas tabelas Winthor** num Postgres nosso e as
alimenta com **dados sintéticos coerentes** — não aleatórios — para que:

1. **Demo** desacoplada do BI do cliente (roda sozinha, sem expor nada do cliente).
2. **Schema de referência** — o formato Winthor canônico que qualquer cliente do
   mesmo ERP vai ter (a alavanca da produtização).
3. **Bancada de "números-ouro"** — validar que o caminho por BD devolve o mesmo que
   o caminho por BI, contra um dataset fixo.

## Princípio central

**Dado sintético calibrado em distribuições, não registro real scrubado.**
Nenhum registro do cliente viaja. O que a gente copia (se copiar) são as *formas*
das distribuições (nº de clientes, cadência de recompra, curva de margem, sazonalidade,
proporções RFM/ABC), e geramos clientes/produtos/pedidos inventados que batem com elas.

## Estrutura (em construção)

- `schema.sql`        — DDL das tabelas Winthor no formato que o app espera (feito).
- `gerar.py`          — o alimentador: modela uma distribuidora plausível e popula. (a fazer)
- `perfil.py`         — parâmetros do "perfil de cliente" (volumes, sazonalidade, mix). (a fazer)
- `carregar.py`       — COPY em massa pro Postgres. (a fazer)

## Pendência conhecida (não bloqueia a demo)

As 6 medidas nativas do dataset RCA (`[VENDA BRUTA]`, `[CUSTO TOTAL]`, devoluções)
são definidas DENTRO do modelo do cliente — o `server.py` só as invoca. Para a demo,
definimos medidas próprias, coerentes. Para paridade centavo-a-centavo com a Multpel
real (ou clonar o modelo exato), extrair essas 6 definições do `.pbix`.
