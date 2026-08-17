"""Saida estruturada e validada a partir de um LLM.

O modelo devolve texto livre. Este modulo e a fronteira onde texto vira objeto tipado
-- ou vira erro. Nada passa adiante sem validacao.

Quando o parse falha, a mensagem de erro volta para o modelo em uma nova tentativa
("retry dirigido"): dizer o que exatamente quebrou tem chance muito maior de corrigir
do que simplesmente repetir o mesmo pedido.
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from app.core.logging import get_logger
from app.llm.base import LLMMessage, LLMProvider, LLMResponse, TokenUsage
from app.llm.exceptions import LLMResponseFormatError

logger = get_logger(__name__)

_FENCE = "```"


@dataclass(frozen=True)
class StructuredCompletion[T: BaseModel]:
    """Resultado validado, com o custo real de obte-lo."""

    value: T
    response: LLMResponse
    #: Quantas tentativas de reparo foram necessarias (0 = acertou de primeira).
    repairs: int


def strip_code_fences(content: str) -> str:
    """Remove cercas de markdown que o modelo insiste em adicionar mesmo em modo JSON."""
    text = content.strip()
    if not text.startswith(_FENCE):
        return text

    lines = text.splitlines()
    if lines and lines[0].startswith(_FENCE):
        lines = lines[1:]
    if lines and lines[-1].strip() == _FENCE:
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _merge_usage(responses: Sequence[LLMResponse]) -> LLMResponse:
    """Consolida varias chamadas em uma resposta, somando tokens, custo e latencia.

    Uma tentativa de reparo e uma segunda chamada paga. Reportar apenas a ultima
    esconderia metade do custo real da execucao.
    """
    last = responses[-1]
    return last.model_copy(
        update={
            "usage": TokenUsage(
                prompt_tokens=sum(r.usage.prompt_tokens for r in responses),
                completion_tokens=sum(r.usage.completion_tokens for r in responses),
            ),
            "cost_usd": round(sum(r.cost_usd for r in responses), 8),
            "latency_ms": round(sum(r.latency_ms for r in responses), 3),
            "attempts": sum(r.attempts for r in responses),
        }
    )


async def complete_structured[T: BaseModel](
    provider: LLMProvider,
    messages: Sequence[LLMMessage],
    schema: type[T],
    *,
    repair_attempts: int = 1,
    temperature: float | None = None,
    validate: Callable[[T], None] | None = None,
) -> StructuredCompletion[T]:
    """Chama o modelo exigindo JSON e devolve uma instancia validada de `schema`.

    Args:
        validate: verificacao extra, executada sobre o objeto ja tipado. Deve levantar
            `ValueError` com uma mensagem acionavel quando reprovar.

            Existe porque nem toda regra cabe no schema. "A ferramenta escolhida precisa
            existir no catalogo" depende de dados de RUNTIME -- o registro montado para
            esta execucao -- e um JSON Schema estatico nao tem como express-la. Deixar
            essa checagem para depois da funcao funcionaria, mas perderia o reparo: o
            modelo receberia a rejeicao apenas como uma falha, sem chance de corrigir.
            Injetada aqui, ela usa o MESMO retry dirigido que a validacao de tipos.

    Raises:
        LLMResponseFormatError: quando nem a tentativa original nem os reparos
            produziram um objeto valido.
    """
    conversation = list(messages)
    responses: list[LLMResponse] = []
    last_error = ""

    for attempt in range(repair_attempts + 1):
        response = await provider.complete(conversation, temperature=temperature, json_mode=True)
        responses.append(response)

        content = strip_code_fences(response.content)
        try:
            value = schema.model_validate_json(content)
        except ValidationError as error:
            last_error = _describe_validation_error(error)
        except ValueError as error:  # JSON malformado
            last_error = f"O conteudo nao e um JSON valido: {error}"
        else:
            try:
                if validate is not None:
                    validate(value)
            except ValueError as error:
                # Reprovado pela regra de runtime. Cai no mesmo caminho de reparo: a
                # mensagem volta para o modelo na proxima tentativa.
                last_error = str(error)
            else:
                return StructuredCompletion(
                    value=value,
                    response=_merge_usage(responses),
                    repairs=attempt,
                )

        logger.warning(
            "structured_output_invalid",
            schema=schema.__name__,
            attempt=attempt + 1,
            error=last_error,
            content_preview=content[:200],
        )

        if attempt < repair_attempts:
            conversation = [
                *conversation,
                LLMMessage.assistant(response.content),
                LLMMessage.user(
                    "A resposta anterior foi rejeitada pela validacao:\n"
                    f"{last_error}\n\n"
                    "Responda novamente APENAS com um objeto JSON valido que atenda ao "
                    "schema exigido. Nao inclua explicacoes nem blocos de codigo."
                ),
            ]

    raise LLMResponseFormatError(
        f"O modelo nao produziu um {schema.__name__} valido apos {repair_attempts + 1} tentativas.",
        details={"schema": schema.__name__, "last_error": last_error},
    )


def _describe_validation_error(error: ValidationError) -> str:
    """Transforma o erro do Pydantic em texto acionavel para o proprio modelo."""
    problems = []
    for item in error.errors():
        # Erro de coerencia entre campos nao tem `loc`: ele pertence ao objeto inteiro.
        location = ".".join(str(part) for part in item["loc"]) or "objeto"
        problems.append(f"- {location}: {item['msg']}")
    return "\n".join(problems)


def dump_schema(schema: type[BaseModel]) -> str:
    """Serializa o JSON Schema para embutir no prompt.

    Descrever o formato esperado em prosa produz resultado pior que entregar o schema:
    o modelo passa a ter os nomes de campo e os valores permitidos exatos.
    """
    return json.dumps(schema.model_json_schema(), ensure_ascii=False, indent=2)
