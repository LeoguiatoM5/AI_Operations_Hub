"""Exercita a camada de LLM pela linha de comando.

Serve para validar configuracao e credencial sem subir a API:

    python scripts/try_llm.py "Explique o que e um webhook em duas frases."

O provedor usado e o de LLM_PROVIDER. Com o padrao (`fake`), roda sem chave e sem rede.
"""

import asyncio
import sys

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.llm.base import LLMMessage
from app.llm.factory import build_llm_provider

SYSTEM_PROMPT = "Voce e um assistente de operacoes. Responda de forma objetiva e em portugues."


async def main() -> int:
    prompt = " ".join(sys.argv[1:]).strip()
    if not prompt:
        print('Uso: python scripts/try_llm.py "sua pergunta"')
        return 2

    settings = get_settings()
    configure_logging(settings)
    provider = build_llm_provider(settings)

    try:
        response = await provider.complete(
            [LLMMessage.system(SYSTEM_PROMPT), LLMMessage.user(prompt)]
        )
    finally:
        await provider.aclose()

    print()
    print("--- resposta -------------------------------------------------------")
    print(response.content)
    print("--- observabilidade ------------------------------------------------")
    print(f"  provider      : {response.provider}")
    print(f"  model         : {response.model}")
    print(
        f"  tokens        : {response.usage.prompt_tokens} entrada / "
        f"{response.usage.completion_tokens} saida / {response.usage.total_tokens} total"
    )
    print(f"  custo estimado: US$ {response.cost_usd:.8f}")
    print(f"  latencia      : {response.latency_ms} ms")
    print(f"  tentativas    : {response.attempts}")
    print(f"  finish_reason : {response.finish_reason}")
    print("--------------------------------------------------------------------")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
