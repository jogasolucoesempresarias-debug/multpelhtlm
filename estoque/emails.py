"""Geração dos anexos dos relatórios de Compras para envio por email.

Este módulo NÃO envia email: só produz os anexos. O envio (Resend, CC, assunto) fica no
server.py, que é quem já tem essa infraestrutura. Assim o módulo continua sem depender do
servidor — mesma razão pela qual a guarda de acesso também vive do lado do app.

⚠️ Diferença importante em relação ao relatório comercial: lá o recorte vem da `session`
(RBAC por vendedor/supervisor). Aqui os filtros das telas são lidos de `request.args`
(`_aplicar_filtros_cliente`), então o recorte por comprador precisa ir na QUERY STRING do
contexto simulado. Injetar na sessão não teria efeito e o relatório sairia sem filtro,
silenciosamente — exatamente o tipo de bug que não aparece em teste de fumaça.
"""

import io

from . import relatorios as rel


def _xlsx_bytes(view, cols, linhas):
    """Mesma planilha do botão ⬇ Excel da tela (api_export_xlsx), em bytes."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = view[:31]
    ws.append([c.upper() for c in cols])
    for r in linhas:
        ws.append([r.get(c, "") for c in cols])
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def gerar_anexos(app, views, codcomprador=None, data_iso=""):
    """Gera [(filename, bytes), ...] para as views pedidas.

    `app`      — a aplicação Flask (para simular o contexto de request fora do HTTP).
    `views`    — lista de views do catálogo; é sanitizada aqui de novo, por segurança.
    `codcomprador` — recorte inicial do usuário; None = empresa toda.

    Uma view que falhar não derruba as outras: o relatório segue com o que deu certo e o
    problema é devolvido em `erros` para aparecer no log do cron.
    """
    from . import routes as R

    qs = {}
    if codcomprador not in (None, "", 0):
        # ⚠️ O parâmetro é `comprador_cod` — é esse que `_aplicar_filtros_cliente` (e os filtros
        # próprios de vencidos/leadtime/verbas) leem. Até 07/2026 isto ia como `comprador`, que
        # NINGUÉM lê no export: o comprador recebia um relatório rotulado com o nome dele e com
        # os dados da EMPRESA TODA. Gate: tests/test_email_comprador.py.
        qs["comprador_cod"] = str(codcomprador)

    anexos, erros = [], []
    for view in rel.normalizar(views):
        try:
            # O path importa: algumas telas leem request.args, e o contexto precisa existir
            # antes de qualquer acesso a `request`.
            with app.test_request_context(f"/estoque/api/export/{view}.xlsx", query_string=qs):
                cols, linhas = R._export_data(view)
                xlsx = _xlsx_bytes(view, cols, linhas)
                pdf = R._gerar_pdf(view, linhas,
                                   group_by="fornecedor" if view == "produtos" else None)
            sufixo = f"_{data_iso}" if data_iso else ""
            anexos.append((f"estoque_{view}{sufixo}.pdf", pdf))
            anexos.append((f"estoque_{view}{sufixo}.xlsx", xlsx))
        except Exception as e:                                    # noqa: BLE001
            erros.append(f"{view}: {e}")
    return anexos, erros
