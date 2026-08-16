"""Agente de pesquisa: responde perguntas a partir da base de conhecimento.

O ponto tecnico central esta em `_answer_schema`: o schema de resposta e construido
DINAMICAMENTE a cada consulta, com as citacoes restritas ao intervalo de trechos que
foram de fato recuperados.

O efeito e que uma citacao inventada -- o modelo dizendo "[7]" quando so existem quatro
trechos -- vira erro de validacao do Pydantic, e o retry dirigido pede a correcao. E o
mesmo mecanismo que ja garante o formato passando a garantir tambem a **ancoragem** da
resposta nas fontes.
"""

from typing import Annotated

from pydantic import BaseModel, Field, create_model, model_validator

from app.agents.base import AgentOutcome, load_prompt
from app.core.logging import get_logger
from app.llm.base import LLMMessage, LLMProvider
from app.llm.structured import complete_structured, dump_schema
from app.rag.base import SearchHit

logger = get_logger(__name__)


class ResearchAnswer(BaseModel):
    """Resposta ancorada nas fontes recuperadas."""

    answered: bool = Field(
        description="False quando o contexto nao permite responder. Isso e uma resposta "
        "correta, nao uma falha."
    )
    answer: str = Field(
        min_length=1,
        max_length=4000,
        description="Resposta baseada exclusivamente nos trechos, ou explicacao do que faltou.",
    )
    citations: list[int] = Field(
        default_factory=list,
        description="Numeros dos trechos que sustentam a resposta.",
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="O quanto os trechos sustentam a resposta."
    )

    @model_validator(mode="after")
    def answer_requires_sources(self) -> "ResearchAnswer":
        """Uma resposta afirmativa sem fonte e, por definicao, nao ancorada.

        Se o modelo afirma ter respondido mas nao aponta de onde tirou a informacao, ou
        ele inventou, ou ele nao sabe dizer a origem. Nos dois casos a resposta nao
        deveria ser entregue como fundamentada.
        """
        if self.answered and not self.citations:
            raise ValueError(
                "answered=true exige ao menos uma citacao: resposta sem fonte nao esta "
                "ancorada no contexto"
            )
        if not self.answered and self.citations:
            raise ValueError("answered=false nao deve citar fontes: se ha fonte, houve resposta")
        return self


def _answer_schema(source_count: int) -> type[ResearchAnswer]:
    """Cria uma variante de `ResearchAnswer` com as citacoes limitadas ao contexto real.

    Sem essa restricao, uma citacao para um trecho inexistente passaria pela validacao e
    so seria descoberta -- se fosse -- por quem lesse a resposta.
    """
    # Apelido de tipo, por isso em CapWords -- a convencao de nomes de variavel nao se
    # aplica aqui.
    Citation = Annotated[int, Field(ge=1, le=source_count)]  # noqa: N806
    schema: type[ResearchAnswer] = create_model(
        f"ResearchAnswerWith{source_count}Sources",
        __base__=ResearchAnswer,
        citations=(list[Citation], Field(default_factory=list)),
    )
    return schema


def format_context(hits: list[SearchHit]) -> str:
    """Numera os trechos para que o modelo possa cita-los."""
    blocos = []
    for position, hit in enumerate(hits, start=1):
        origem = hit.metadata.get("filename", hit.document_id)
        blocos.append(f"[{position}] (fonte: {origem}, similaridade {hit.score:.3f})\n{hit.text}")
    return "\n\n".join(blocos)


class ResearchAgent:
    """Responde perguntas usando exclusivamente os trechos recuperados."""

    name = "research"
    action = "answer_from_knowledge_base"

    def __init__(self, provider: LLMProvider, *, repair_attempts: int = 1) -> None:
        self._provider = provider
        self._repair_attempts = repair_attempts

    async def run(self, question: str, hits: list[SearchHit]) -> AgentOutcome[ResearchAnswer]:
        if not hits:
            raise ValueError("ResearchAgent exige ao menos um trecho de contexto.")

        schema = _answer_schema(len(hits))
        system_prompt = (
            load_prompt("research")
            .replace("{context}", format_context(hits))
            .replace("{schema}", dump_schema(schema))
        )

        completion = await complete_structured(
            self._provider,
            [LLMMessage.system(system_prompt), LLMMessage.user(question)],
            schema,
            repair_attempts=self._repair_attempts,
        )

        logger.info(
            "research_completed",
            answered=completion.value.answered,
            citations=completion.value.citations,
            confidence=completion.value.confidence,
            sources_offered=len(hits),
            repairs=completion.repairs,
        )

        return AgentOutcome(
            agent=self.name,
            action=self.action,
            payload=completion.value,
            response=completion.response,
            repairs=completion.repairs,
        )
