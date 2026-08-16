"""Provider deterministico, sem rede.

Nao e um detalhe de teste: e o provider PADRAO do projeto. Quem clona o repositorio
sobe a aplicacao e a ve funcionando sem possuir nenhuma chave de API, e o pipeline de
CI roda a suite completa sem segredos configurados.

Tambem e a unica forma de testar cenarios que a API real nao produz sob encomenda:
timeout, JSON quebrado, limite de cota, resposta vazia.
"""

import asyncio
import hashlib
import json
from collections.abc import Sequence

from app.core.logging import get_logger
from app.llm.base import LLMMessage, LLMResponse, TokenUsage
from app.llm.pricing import estimate_cost_usd

logger = get_logger(__name__)

#: Roteiro de uma chamada: um texto a devolver ou uma excecao a levantar.
ScriptedOutcome = str | Exception


class FakeLLMProvider:
    """Implementa o Protocol LLMProvider sem tocar em rede.

    Sem roteiro, devolve um JSON deterministico derivado do conteudo das mensagens --
    a mesma entrada produz sempre a mesma saida, o que torna os testes reproduziveis.
    """

    def __init__(
        self,
        *,
        script: Sequence[ScriptedOutcome] | None = None,
        latency_seconds: float = 0.0,
        model_name: str = "fake-model-1",
        repeat_last: bool = True,
    ) -> None:
        """
        Args:
            script: sequencia de resultados, consumida em ordem a cada chamada.
            latency_seconds: atraso artificial, para exercitar timeouts.
            model_name: nome reportado como modelo.
            repeat_last: quando o roteiro acaba, repete o ultimo item em vez de falhar.
        """
        self._script: list[ScriptedOutcome] = list(script or [])
        self._latency_seconds = latency_seconds
        self._model_name = model_name
        self._repeat_last = repeat_last
        self._call_count = 0
        self.calls: list[list[LLMMessage]] = []  # historico, util para asserts

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return self._model_name

    @property
    def call_count(self) -> int:
        return self._call_count

    def _next_outcome(self, messages: Sequence[LLMMessage]) -> ScriptedOutcome:
        if not self._script:
            return self._deterministic_answer(messages)

        index = self._call_count - 1
        if index < len(self._script):
            return self._script[index]
        if self._repeat_last:
            return self._script[-1]
        raise AssertionError(
            f"FakeLLMProvider recebeu {self._call_count} chamadas, "
            f"mas o roteiro tem apenas {len(self._script)} itens."
        )

    def _deterministic_answer(self, messages: Sequence[LLMMessage]) -> str:
        """Resposta estavel derivada da entrada, no formato JSON que os agentes usam."""
        joined = "\n".join(f"{message.role}:{message.content}" for message in messages)
        digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]
        last_user = next(
            (message.content for message in reversed(messages) if message.role == "user"),
            "",
        )
        return json.dumps(
            {
                "answer": f"Resposta simulada para: {last_user[:120]}",
                "fingerprint": digest,
                "sources": [],
            },
            ensure_ascii=False,
        )

    async def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        self._call_count += 1
        self.calls.append(list(messages))

        if self._latency_seconds:
            await asyncio.sleep(self._latency_seconds)

        outcome = self._next_outcome(messages)
        if isinstance(outcome, Exception):
            logger.info("fake_llm_raising", error_type=type(outcome).__name__)
            raise outcome

        # Aproximacao grosseira de tokens: suficiente para exercitar o calculo de custo
        # e a gravacao de metricas sem depender de um tokenizador real.
        prompt_tokens = sum(len(message.content) for message in messages) // 4
        usage = TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=len(outcome) // 4,
        )

        return LLMResponse(
            content=outcome,
            provider=self.name,
            model=self.model,
            usage=usage,
            cost_usd=estimate_cost_usd(self.model, usage),
            latency_ms=round(self._latency_seconds * 1000, 3),
            finish_reason="stop",
        )

    async def aclose(self) -> None:
        """Nao ha recurso a liberar."""
        return None
