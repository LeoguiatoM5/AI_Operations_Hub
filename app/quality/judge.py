"""Encanamento comum das dimensoes julgadas por LLM.

**A decisao que define o V5.2: nao se pergunta uma nota ao modelo.**

O caminho obvio seria mandar a resposta ao LLM e pedir "de 0 a 10, quao fundamentada esta
isto?". Funciona, e produz um numero que ninguem consegue auditar: nao da para saber o que
o modelo considerou, nem reproduzir a nota, nem discutir um caso especifico. Pior, notas
holisticas de LLM sao instaveis -- a mesma entrada oscila entre execucoes.

Aqui o modelo faz um trabalho de **classificacao**, nao de pontuacao: rotula cada
afirmacao, aponta cada contradicao, marca cada topico coberto. A nota sai de aritmetica
sobre esses rotulos, do nosso lado. O ganho e triplo:

1. **auditavel** -- a nota vem com a lista do que passou e do que nao passou;
2. **reproduzivel** -- a mesma classificacao sempre produz a mesma nota;
3. **discutivel** -- da para revisar um veredito isolado sem refazer a avaliacao.

**Vies conhecido, nao resolvido.** O juiz e o mesmo modelo que produziu a resposta, e
modelos preferem o proprio texto (*self-preference bias*). Duas coisas atenuam: a
classificacao por item (mais dificil de enviesar que uma nota livre) e, no modo offline,
as expectativas escritas por uma pessoa, que sao verdade externa ao modelo. Eliminar
exigiria um segundo provedor -- possivel, porque `LLMProvider` e um Protocol, e nao feito
porque ainda nao ha medicao mostrando que vale o custo.
"""

from pathlib import Path

from pydantic import BaseModel

from app.llm.base import LLMMessage, LLMProvider, LLMResponse
from app.llm.structured import complete_structured, dump_schema
from app.quality.base import DimensionScore

PROMPTS_DIR = Path(__file__).parent / "prompts"

#: O juiz roda com temperatura zero. Avaliacao que muda de nota a cada execucao nao mede
#: o sistema -- mede o ruido do proprio medidor, e torna impossivel comparar dois
#: relatorios de evals.
JUDGE_TEMPERATURE = 0.0


def load_quality_prompt(name: str) -> str:
    """Le um prompt de avaliacao.

    Mesma razao dos prompts de agente (`app/agents/base.py`): prompt e artefato
    versionavel. Aqui pesa ainda mais -- mudar o prompt do juiz muda a nota de tudo, e
    esse tipo de alteracao precisa aparecer no diff.
    """
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


class JudgedDimension:
    """Base das dimensoes que consultam um LLM.

    Nao e classe abstrata nem impoe heranca ao Protocol `Dimension` -- que e estrutural.
    Existe so para nao repetir em quatro arquivos a chamada estruturada, a temperatura e
    a contabilidade de custo.
    """

    def __init__(self, provider: LLMProvider, *, repair_attempts: int = 1) -> None:
        self._provider = provider
        self._repair_attempts = repair_attempts

    @property
    def uses_llm(self) -> bool:
        return True

    async def _judge[T: BaseModel](
        self, prompt_name: str, schema: type[T], *, task: str, **replacements: str
    ) -> tuple[T, LLMResponse]:
        """Faz a pergunta ao juiz e devolve o veredito validado.

        O `{schema}` do prompt e preenchido aqui para que nenhum prompt precise repetir a
        forma da saida a mao -- e para que ela nunca saia de sincronia com o modelo
        Pydantic que a valida.
        """
        prompt = load_quality_prompt(prompt_name).replace("{schema}", dump_schema(schema))
        for chave, valor in replacements.items():
            prompt = prompt.replace("{" + chave + "}", valor)

        completion = await complete_structured(
            self._provider,
            [LLMMessage.system(prompt), LLMMessage.user(task)],
            schema,
            repair_attempts=self._repair_attempts,
            temperature=JUDGE_TEMPERATURE,
        )
        return completion.value, completion.response


def refusal_is_not_graded(dimension: str) -> DimensionScore:
    """Recusa honesta nao recebe nota de conteudo.

    **Encontrado pelo conjunto de avaliacao, e nao previsto.** Na primeira rodada com
    juizes, todos os cinco casos de recusa correta tiraram entre 0.29 e 0.40: `relevance`
    dizia "a resposta nao aborda a pergunta" e `completeness` dizia "1 de 1 itens nao
    foram cobertos". Ambos estavam literalmente certos e medindo a coisa errada -- uma
    recusa nao tem sobre o que ser pertinente nem o que completar.

    O `grounding` ja tinha esse atalho desde o inicio; as outras duas nao, e o prompt do
    `relevance` chegava a mandar tratar recusa como pertinente. O juiz ignorou. **Atalho
    em codigo vence instrucao em prompt**: um e determinista e gratuito, o outro depende
    de o modelo obedecer.

    O que importa numa recusa e se ela era *correta*, e isso ja e medido -- sem LLM, sem
    ambiguidade -- pela assercao `answered_as_expected` do conjunto de avaliacao. Dar nota
    aqui seria contar a mesma coisa duas vezes, e ao contrario.
    """
    return DimensionScore(
        dimension=dimension,
        score=0.0,
        applicable=False,
        reason=(
            "O sistema recusou por falta de cobertura. Uma recusa nao recebe nota de "
            "conteudo: o que importa e se recusar era o certo."
        ),
    )


def ratio(aprovados: int, total: int) -> float:
    """Proporcao segura, para as notas derivadas de contagem.

    Total zero devolve 1.0, e nao 0.0: nenhum item a verificar significa nenhum item
    reprovado. E a mesma regra do agregado vazio no motor -- nao medir nao e medir mal.
    """
    if total <= 0:
        return 1.0
    return round(aprovados / total, 4)
