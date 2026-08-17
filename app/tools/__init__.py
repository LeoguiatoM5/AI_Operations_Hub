"""Ferramentas: a camada por onde o sistema AGE sobre o mundo externo.

Tudo antes daqui produz texto. Uma ferramenta manda mensagem, escreve em sistema de
terceiro, dispara webhook -- e algumas dessas acoes nao tem desfazer. Por isso toda
ferramenta declara um escopo (`read` ou `write`), e o escopo e o unico lugar do projeto
que decide o que exige aprovacao humana.
"""

from app.tools.base import Tool, ToolResult, ToolScope, ToolSpec
from app.tools.exceptions import ToolExecutionError, ToolInputError, ToolNotFoundError
from app.tools.registry import ToolRegistry

__all__ = [
    "Tool",
    "ToolExecutionError",
    "ToolInputError",
    "ToolNotFoundError",
    "ToolRegistry",
    "ToolResult",
    "ToolScope",
    "ToolSpec",
]
