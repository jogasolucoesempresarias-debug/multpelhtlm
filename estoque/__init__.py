"""Módulo Compras (Estoque) do JOGA Analytics.

O blueprint é definido em routes.py; aqui só reexportamos para o server.py fazer
`from estoque import bp`. Definir o bp no routes.py (em vez de aqui) evita o ciclo
de import do padrão "bp no __init__ + import routes no fim".
"""

from .routes import bp

__all__ = ["bp"]
