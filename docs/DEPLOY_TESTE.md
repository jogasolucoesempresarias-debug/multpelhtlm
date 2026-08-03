# Subir a stack de teste — painel.jogasolucoes.com.br

Ambiente para validar a fusão Comercial + Compras **sem tocar em nada que está no ar**.
Produção segue intacta: `analytics.jogasolucoes.com.br` (Comercial) e
`estoque.jogasolucoes.com.br` (Compras) continuam rodando as imagens atuais.

---

## 1. Publicar a imagem de teste

```bash
git push -u origin feat/fusao-estoque
```

A Action builda e publica **`ghcr.io/jogasolucoesempresarias-debug/multpelhtlm:latest`** (a `main`).

> A tag `:latest` **não é tocada** — só a `main` a move. É o que impede um deploy de branch
> escorregar para a produção da Multpel.

Acompanhe em **Actions** até ficar verde (~2-3 min).

---

## 2. Criar a stack no Portainer

**Stacks → Add stack**, nome `painel-teste`, colar o `docker-compose.teste.yml`.

Variáveis a preencher:

| Variável | Valor |
|---|---|
| `SECRET_KEY` | hex aleatório novo — `python -c "import secrets; print(secrets.token_hex(32))"` |
| `DB_PASSWORD` | senha nova (é um Postgres novo, não reaproveite a de produção) |
| `POWERBI_TENANT_ID` / `CLIENT_ID` / `CLIENT_SECRET` / `GROUP_ID` | as mesmas da produção (leitura) |
| `POWERBI_DATASET_ID` | o mesmo da produção (RCA) |
| `MODULOS` | `comercial,compras` |

> **`SECRET_KEY` é obrigatória**: sem ela o app aborta o boot de propósito. Se a stack subir e
> o serviço ficar reiniciando, é o primeiro lugar para olhar (`docker service logs`).

A imagem é privada — o Portainer precisa da credencial do GHCR (a mesma já usada pelas outras
stacks).

---

## 3. Criar as tabelas

```bash
docker exec $(docker ps -q -f name=painel-teste_painel-app) python -X utf8 init_db.py
```

Cria o schema e o admin padrão (`ADMIN_EMAIL` / `ADMIN_SENHA`; defaults no `init_db.py`).

> ⚠️ O domínio é público. **Troque essa senha no primeiro acesso** ou pule direto para o passo 4,
> que traz os usuários reais e torna o admin padrão desnecessário.

---

## 4. Popular com dados de verdade (é o que torna o teste válido)

Testar com base vazia não valida quase nada: o que interessa é ver os usuários reais, com os
papéis reais, e os dados que os compradores lançaram.

### 4.1 Usuários e config do Comercial

```bash
# dump da produção (só as tabelas do app; NÃO restaure nada de volta lá)
docker exec $(docker ps -q -f name=postgres_postgres) \
  pg_dump -U <DB_USER> -d multpel_db \
  -t multpel_users -t multpel_config -t multpel_metas > /tmp/comercial.sql

docker cp /tmp/comercial.sql $(docker ps -q -f name=painel-teste_painel-postgres):/tmp/
docker exec -i $(docker ps -q -f name=painel-teste_painel-postgres) \
  psql -U painel -d painel_db -f /tmp/comercial.sql
```

### 4.2 Dados do Compras — **este é o ensaio que importa**

Os compradores lançam orçamento, pedidos manuais e planos de ação. Isso não vem do Power BI:
existe **só** no `estoque_db`. Na promoção para produção, essas tabelas precisam viajar do
`estoque_db` para o banco do app fundido — e é melhor errar aqui do que lá.

Antes, veja o volume:

```bash
docker exec $(docker ps -q -f name=multpel-estoque_estoque-postgres) \
  psql -U estoque -d estoque_db -c "
    SELECT 'orcamento' t, count(*) FROM estoque_orcamento
    UNION ALL SELECT 'pedidos',    count(*) FROM estoque_pedidos
    UNION ALL SELECT 'itens',      count(*) FROM estoque_pedido_itens
    UNION ALL SELECT 'planos',     count(*) FROM estoque_planos_acao;"
```

Depois migre:

```bash
docker exec $(docker ps -q -f name=multpel-estoque_estoque-postgres) \
  pg_dump -U estoque -d estoque_db --data-only \
  -t estoque_orcamento -t estoque_pedidos -t estoque_pedido_itens -t estoque_planos_acao \
  > /tmp/compras.sql

docker cp /tmp/compras.sql $(docker ps -q -f name=painel-teste_painel-postgres):/tmp/
docker exec -i $(docker ps -q -f name=painel-teste_painel-postgres) \
  psql -U painel -d painel_db -f /tmp/compras.sql
```

> `--data-only` porque o `init_db.py` já criou as tabelas. Os `id` de `estoque_pedidos` vêm
> junto, então **acerte a sequence** depois, senão o próximo pedido criado colide:
> ```sql
> SELECT setval('estoque_pedidos_id_seq',        COALESCE(MAX(id),1)) FROM estoque_pedidos;
> SELECT setval('estoque_pedido_itens_id_seq',   COALESCE(MAX(id),1)) FROM estoque_pedido_itens;
> ```

### 4.3 Liberar a área Compras

Todo usuário existente vem com `areas = ["comercial"]` (default deliberado: ninguém ganha
acesso por acidente). Libere os compradores pelo Admin, ou de uma vez:

```sql
UPDATE multpel_users SET areas = '["comercial","compras"]'::jsonb
 WHERE email IN ('...');
```

---

## 5. Conferir

```bash
curl -s https://painel.jogasolucoes.com.br/health      # deve listar os módulos ativos
docker service logs -f --tail 100 painel-teste_painel-app
```

Roteiro de validação na tela:

- [ ] Usuário **só comercial** → cai no Dashboard; `/estoque` dá 403
- [ ] Usuário **só compras** → cai direto no Estoque; `/carteira` dá 403
- [ ] Usuário com **as duas áreas** → portal; fixar uma área; relogar vai direto
- [ ] Seletor de área troca e volta em qualquer página, inclusive no Admin
- [ ] Admin: bloco de acesso por área, comprador vinculado (deve listar **8**, não a folha toda)
- [ ] **Compras: orçamento, pedidos e planos apareceram** (prova de que a migração deu certo)
- [ ] Percorrer as 19 abas do Compras com o console aberto — nenhum 404
- [ ] Exportar CSV/XLSX/PDF de alguns relatórios
- [ ] Login: errar 5× bloqueia; admin desbloqueia
- [ ] Botão de envio manual de relatório de Compras **não** dispara (cron e Resend desligados)

---

## 6. Trocar de módulo sem redeploy

A mesma imagem serve as 3 configurações — útil para validar o produto vendável:

Portainer → stack → editar `MODULOS` → `comercial` · `compras` · `comercial,compras` → update.

---

## 7. Derrubar quando terminar

```bash
docker stack rm painel-teste
docker volume rm painel-teste_painel-pgdata     # some com a cópia dos dados
```

> O volume guarda uma cópia da base de usuários (com hash de senha e emails reais). Não deixe
> a stack de pé sem necessidade depois da validação.
