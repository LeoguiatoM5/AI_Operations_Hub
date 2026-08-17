"""Montagem do grafo de agentes.

    START -> orchestrator -+-> research ---------+
                           |                     |
                           +-> analysis ---------+--> (roteador) -> reporter -> END
                           |                     |
                           +-> automation_plan --+
                           |        |
                           |        v
                           |   automation_run  (pausa aqui, se exigir aprovacao)
                           |
                           +-> END   (plano nao pode ser produzido)

O roteador nao e um `if` dentro de um no: e uma `conditional_edge`, uma funcao pura do
estado para o nome do proximo no. A diferenca pratica e que o caminho fica **inspecionavel
e testavel isoladamente**, sem executar agente nenhum -- e o grafo, desenhavel a partir do
codigo.

**Por que a automacao sao DOIS nos.** Quando o grafo pausa em `interrupt()`, a retomada
reexecuta o no inteiro desde a primeira linha. Se decidir a acao e executa-la vivessem no
mesmo no, cada aprovacao pagaria de novo a chamada de LLM que escolheu a ferramenta -- e,
pior, o modelo poderia escolher OUTRA coisa, executando algo diferente do que o humano
aprovou. Separando, a decisao fica gravada no checkpoint antes da pausa: a retomada
executa exatamente o que estava na tela de aprovacao.
"""

from collections.abc import Hashable

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.core.logging import get_logger
from app.workflows.nodes import WorkflowNodes
from app.workflows.state import WorkflowState

logger = get_logger(__name__)

ORCHESTRATOR = "orchestrator"
RESEARCH = "research"
ANALYSIS = "analysis"
AUTOMATION = "automation"
AUTOMATION_PLAN = "automation_plan"
AUTOMATION_RUN = "automation_run"
REPORTER = "reporter"

#: Agente na fila -> no que o executa. Quase sempre o mesmo nome; a automacao e a
#: excecao, porque um agente pode ocupar mais de um no.
ENTRY_NODE = {
    RESEARCH: RESEARCH,
    ANALYSIS: ANALYSIS,
    AUTOMATION: AUTOMATION_PLAN,
}


def route_next(state: WorkflowState) -> str:
    """Decide o proximo no a partir do estado.

    Funcao pura: mesmo estado, mesmo destino. Nao chama LLM, nao toca banco, nao tem
    efeito colateral -- por isso da para testar todos os caminhos do grafo em
    milissegundos, sem provider nenhum.
    """
    if state.get("fatal"):
        return END

    for agente in state.get("pending_agents", []):
        destino = ENTRY_NODE.get(agente)
        if destino is not None:
            return destino

    return REPORTER


def build_graph(
    nodes: WorkflowNodes, checkpointer: BaseCheckpointSaver[str] | None = None
) -> CompiledStateGraph[WorkflowState, None, WorkflowState, WorkflowState]:
    """Compila o grafo com os nos de uma execucao.

    Compilado por requisicao porque os nos carregam a sessao de banco e a execucao em
    curso. A compilacao e barata -- monta o grafo, nao executa nada -- e o custo se paga
    em clareza: nenhum estado de requisicao vive em objeto compartilhado entre threads.
    """
    builder: StateGraph[WorkflowState, None, WorkflowState, WorkflowState] = StateGraph(
        WorkflowState
    )

    builder.add_node(ORCHESTRATOR, nodes.orchestrator)
    builder.add_node(RESEARCH, nodes.research)
    builder.add_node(ANALYSIS, nodes.analysis)
    builder.add_node(AUTOMATION_PLAN, nodes.automation_plan)
    builder.add_node(AUTOMATION_RUN, nodes.automation_run)
    builder.add_node(REPORTER, nodes.reporter)

    builder.add_edge(START, ORCHESTRATOR)

    # O mesmo roteador liga os pontos de decisao. O `path_map` explicito e o que permite
    # ao LangGraph desenhar o grafo e validar que todo destino existe.
    # `dict[Hashable, str]` explicito: dict e invariante em Python, entao um
    # `dict[str, str]` nao satisfaz a assinatura da biblioteca.
    destinos: dict[Hashable, str] = {
        RESEARCH: RESEARCH,
        ANALYSIS: ANALYSIS,
        AUTOMATION_PLAN: AUTOMATION_PLAN,
        REPORTER: REPORTER,
        END: END,
    }
    builder.add_conditional_edges(ORCHESTRATOR, route_next, destinos)
    builder.add_conditional_edges(RESEARCH, route_next, destinos)
    builder.add_conditional_edges(ANALYSIS, route_next, destinos)
    builder.add_conditional_edges(AUTOMATION_RUN, route_next, destinos)

    # Aresta fixa, e nao condicional: decidir a acao e executa-la sao duas metades de um
    # passo so. Nao existe estado em que valha a pena planejar e nao seguir para a
    # execucao -- e e entre os dois que o checkpoint da pausa e gravado.
    builder.add_edge(AUTOMATION_PLAN, AUTOMATION_RUN)

    builder.add_edge(REPORTER, END)

    return builder.compile(checkpointer=checkpointer)
