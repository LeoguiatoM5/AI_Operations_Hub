"""Ponto de entrada do servidor MCP.

    python -m mcp_server

Fala por **stdio**: o cliente (Claude Desktop, um IDE, outro agente) sobe este processo e
conversa por entrada e saida padrao. Nao ha porta, nao ha URL, nao ha autenticacao -- o
processo pertence a quem o iniciou, e o isolamento e o do sistema operacional.

**Consequencia pratica:** nada pode ser escrito em `stdout` alem do protocolo. Um `print`
perdido corrompe a conversa. Por isso o logging do projeto e redirecionado para `stderr`
antes de o servidor subir -- e nao apenas silenciado, que perderia o diagnostico.
"""

import asyncio
import logging
import sys

from app.core.config import get_settings
from mcp_server.container import build_container
from mcp_server.server import build_server


def _logs_to_stderr() -> None:
    """Manda todo log para stderr.

    O `configure_logging` do projeto escreve em stdout, que aqui e o canal do protocolo.
    Trocar o destino preserva o diagnostico -- o cliente MCP costuma mostrar o stderr do
    servidor -- sem corromper a conversa.
    """
    raiz = logging.getLogger()
    # A copia com `list()` nao e supérflua: `removeHandler` muta a mesma lista que esta
    # sendo percorrida, e iterar sobre ela direto pularia handlers.
    for handler in list(raiz.handlers):
        raiz.removeHandler(handler)
    raiz.addHandler(logging.StreamHandler(sys.stderr))


async def main() -> None:
    settings = get_settings()
    async with build_container(settings) as container:
        _logs_to_stderr()
        await build_server(container).run_stdio_async()


if __name__ == "__main__":
    asyncio.run(main())
