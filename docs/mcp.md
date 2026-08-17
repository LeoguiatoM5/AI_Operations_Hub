# Servidor MCP

## O que e MCP

O **Model Context Protocol** e um protocolo aberto para um cliente de LLM -- Claude
Desktop, uma extensao de IDE, um agente de terceiros -- **descobrir e chamar** ferramentas
de um sistema externo.

A palavra que carrega a diferenca e *descobrir*. Uma API REST publica recursos para um
programa que ja sabe o que quer: quem escreve o cliente leu a documentacao e decidiu, em
tempo de desenvolvimento, chamar `GET /documents`. O MCP publica **capacidades** para um
modelo que precisa descobrir, em tempo de execucao, o que existe e se serve para a tarefa
que ele recebeu.

Isso muda o que importa no projeto da interface.

## REST e MCP, lado a lado

| | REST | MCP |
|---|---|---|
| Quem consome | um programa escrito por alguem | um modelo de linguagem |
| Quando descobre a interface | ao escrever o codigo | a cada sessao |
| O que a descricao precisa fazer | documentar | **convencer e delimitar** |
| Transporte tipico | HTTP, uma porta | stdio, um processo filho |
| Erro | codigo de status | texto que o modelo vai interpretar |
| Autenticacao | do proprio protocolo | do sistema operacional (quem subiu o processo) |

**A descricao de uma ferramenta MCP e prompt, nao documentacao.** E por ela que o modelo
decide se a ferramenta serve, e o que ela nao faz. Uma frase mal escrita nao gera um erro
de compilacao -- gera uma chamada errada em producao. Por isso ha teste exigindo
descricao substancial em todas elas.

## Quando usar cada um

Use **REST** quando o cliente e um programa, quando ha varios consumidores independentes,
quando e preciso autenticar por usuario, ou quando a operacao precisa de garantias de
transporte (idempotencia, cache, versionamento de contrato).

Use **MCP** quando o cliente e um modelo, quando o conjunto de capacidades e mais util que
o conjunto de recursos, e quando o processo pode viver ao lado de quem o usa.

Este projeto oferece os dois **sobre a mesma camada de servico**, e nao um por cima do
outro. O servidor MCP nao chama a API REST -- os dois chamam `RagService`,
`WorkflowService` e os repositorios diretamente.

## O que este servidor expoe

| Ferramenta | Servico que ela adapta |
|---|---|
| `search_knowledge_base` | `RagService.query` |
| `list_documents` | `DocumentRepository` |
| `get_execution` | `ExecutionRepository` |
| `list_pending_approvals` | `ApprovalRepository` |
| `run_workflow` | `WorkflowService.run` |

## A ferramenta que este servidor nao tem

**Nao existe `approve_action` nem `reject_action`.** E a decisao de projeto mais
importante do V6.

O V4 estabeleceu que nenhuma acao de escrita acontece sem uma pessoa autorizar. Um cliente
MCP e um modelo de linguagem. Oferecer a ele a ferramenta de aprovar significaria a IA
autorizando a propria acao -- e o mecanismo inteiro viraria teatro: o grafo pausaria, o
modelo chamaria `approve`, e nenhum humano teria participado.

O servidor faz o que pode fazer com seguranca: **mostra** o que esta pendente
(`list_pending_approvals`), com os argumentos exatos, para que o modelo **relate** a
pendencia a quem puder decidir. A decisao acontece em `POST /approvals/{id}/approve`, pela
interface humana.

Ha um teste afirmando que a ferramenta nao existe
(`test_there_is_no_tool_to_approve_an_action`). Testar a ausencia de algo parece
excentrico, ate lembrar que "adicionar uma ferramenta que faltava" e a mudanca mais
natural do mundo -- e que aqui ela desmontaria a garantia central do sistema.

## Como rodar

```powershell
python -m mcp_server
```

O servidor fala por **stdio**: quem o usa sobe o processo e conversa por entrada e saida
padrao. Nao ha porta nem URL.

Consequencia pratica: **nada pode ser escrito em `stdout` alem do protocolo**. Um `print`
perdido corrompe a conversa. O logging do projeto e redirecionado para `stderr` antes de o
servidor subir -- redirecionado, e nao silenciado, porque o cliente MCP costuma mostrar o
stderr do servidor e e la que o diagnostico aparece.

### Ligando ao Claude Desktop

No arquivo de configuracao do cliente:

```json
{
  "mcpServers": {
    "ai-operations-hub": {
      "command": "D:\\Ai_Operations_HUB\\.venv\\Scripts\\python.exe",
      "args": ["-m", "mcp_server"],
      "cwd": "D:\\Ai_Operations_HUB"
    }
  }
}
```

O `cwd` importa: a configuracao vem do `.env` do projeto, e os caminhos de banco e indice
sao relativos a ele.

## O que o V6 provou

A camada `services/` foi escrita sem conhecer FastAPI desde o V1 -- e o invariante numero
1 do roadmap. Isso e barato de afirmar e caro de sustentar; a cobranca so chega quando
aparece um segundo transporte.

O servidor MCP inteiro tem menos de 250 linhas e **nenhuma regra de negocio**. Se a
camada de servico tivesse recebido um `Request` do FastAPI, um `HTTPException` ou uma
dependencia de sessao amarrada ao ciclo de vida de um request, este arquivo seria uma
reimplementacao -- e as duas versoes divergiriam na primeira correcao feita em so uma
delas.

O que precisou ser construido foi apenas o que o transporte exige: um container de
dependencias de vida longa (o MCP nao tem request para pendurar `Depends`) e uma sessao de
banco por chamada de ferramenta.
