# Roadmap e estado do projeto

Documento de continuidade: o que ja existe, quais invariantes o codigo respeita, e o que
falta construir. Serve para retomar o trabalho sem reconstruir contexto.

Atualizado durante o **V4** (4.1 a 4.3 concluidas; 4.4 com o Slack pronto e o n8n
bloqueado em infraestrutura).

---

## 1. Estado atual

| Versao | Escopo | Situacao |
|---|---|---|
| V1 | FastAPI, camada multi-LLM, agente de triagem, persistencia, observabilidade | concluido |
| V2 | RAG com ChromaDB, ingestao de documentos, consulta com fontes citadas | concluido |
| V3 | LangGraph, quatro agentes, roteamento por plano, checkpointer persistente | concluido |
| V4 | Integracoes externas e human-in-the-loop | **em curso** |
| V5 | AI Quality Gateway e AI Evals | planejado |
| V6 | Servidor MCP | planejado |
| V7 | Docker, CI/CD, observabilidade completa, material de portfolio | planejado |

**Numeros:** 415 testes, 97% de cobertura, `ruff` e `mypy` limpos, 63 decisoes
registradas em `engineering-decisions.md`.

**Custo medido:** execucao completa do workflow (4 agentes, com RAG) custa cerca de
US$ 0,0009 com `gpt-4o-mini` e `text-embedding-3-small`.

---

## 2. Mapa do codigo

```
app/
  core/          config (pydantic-settings), logging (structlog), excecoes, retry
  llm/           Protocol LLMProvider + OpenAI + fake + retry + pricing + structured
  rag/           embeddings, vector stores (memory/chroma), chunking, loaders, retriever
  agents/        triage, research, analysis, automation, reporter + prompts/*.md
  tools/         Protocol Tool + escopo read/write + registro + notify + slack + knowledge
  workflows/     state (TypedDict + reducers), nodes, graph, checkpointer
  services/      execution, document, rag, workflow   <- regra de negocio, sem FastAPI
  repositories/  acesso a dados (execution, document)
  models/        SQLAlchemy (Execution, AgentExecution, Document, Approval)
  api/           rotas, deps (injecao), middleware (correlation ID), errors, responses
  schemas/       Pydantic de entrada e saida
```

**Endpoints:** `/health`, `/chat`, `/executions`, `/executions/{id}`,
`/documents/upload`, `/documents`, `/documents/{id}`, `/rag/query`, `/agents/run`,
`/tools`, `/approvals`, `/approvals/{id}`, `/approvals/{id}/approve`,
`/approvals/{id}/reject`.

---

## 3. Invariantes do projeto

Regras que o codigo respeita hoje. Quebra-las exige decisao consciente e registro em
`engineering-decisions.md`.

**Arquitetura**

1. Camada `services/` nao importa FastAPI. E ela que o servidor MCP (V6) vai reaproveitar.
2. Nenhuma rota alcanca objeto global: tudo chega por `Depends` (ED-002).
3. Toda dependencia externa tem uma implementacao de teste (`fake`, `memory`) e um
   Protocol que as une.
4. `create_app()` aceita injecao de banco, LLM, embeddings, vector store e checkpointer.

**Confiabilidade**

5. Saida de LLM nunca circula como dicionario solto: ou vira modelo Pydantic validado, ou
   vira erro (ED-022).
6. Validacao inclui **coerencia semantica**, nao apenas tipos -- e o retry dirigido
   corrige as duas (ED-028, ED-039).
7. Falha de LLM devolve status HTTP honesto em `/chat`; em `/agents/run` degrada e
   continua (ED-024, ED-045).
8. Registro de falha e confirmado em transacao propria, antes de a excecao subir (ED-025).
9. Em codigo assincrono, nenhuma ida ao banco e implicita (ED-018).
10. Ferramenta declara o proprio escopo, e `write` sempre exige aprovacao humana. A regra
    existe em um lugar so: `ToolScope.requires_approval` (ED-048).
11. Dentro de um no que pausa, `interrupt()` e a primeira instrucao com efeito -- tudo
    acima dele roda duas vezes na retomada (ED-052).
12. O que e executado apos uma aprovacao e exatamente o que foi mostrado a quem aprovou.
    Nada e reinterpretado entre a pausa e a retomada.

**Custo e observabilidade**

13. Todo passo grava tokens, custo estimado, latencia, tentativas, provider e modelo.
14. Tentativas de reparo somam custo; reportar so a ultima esconderia metade (ED-023).
15. Todo request tem `correlation_id`, propagado ate os logs das bibliotecas.
16. Toda autorizacao tem autor e motivo gravados, confirmados antes de a acao rodar
    (ED-054).

**Testes**

17. A suite nunca chama API paga nem toca disco do projeto -- exceto
    `tests/integration/`, onde tocar disco de verdade e o proposito.
18. Componentes com mais de uma implementacao tem **teste de contrato** rodando a mesma
    bateria contra todas (ver `tests/integration/test_vector_stores.py`).
19. Substituto de teste que nunca encontra o componente real esconde defeito: o caminho
    de producao precisa de ao menos um teste (ED-047).

---

## 4. V4 — Integracoes e human-in-the-loop

**Objetivo:** o sistema executa acoes reais em sistemas externos, e nenhuma acao de
escrita acontece sem aprovacao humana explicita.

### 4.1 Registro de ferramentas com escopo — **concluido**

Veio antes do human-in-the-loop por ordem de dependencia: nao da para pausar para
aprovar uma acao que ainda nao existe.

- `app/tools/`: Protocol `Tool`, `ToolScope` (`read`/`write`), `ToolRegistry`.
- A regra de aprovacao mora em `ToolScope.WRITE.requires_approval` e em nenhum outro
  lugar (ED-048).
- Duas ferramentas: `search_knowledge_base` (leitura, embrulha o `Retriever`) e
  `send_notification` (escrita, fala com o Protocol `Notifier`; `MemoryNotifier` e o
  padrao, o Slack entra em 4.4).
- `GET /tools` publica o catalogo com `input_schema` -- o mesmo dado que o prompt do
  agente de automacao e o servidor MCP (V6) vao consumir.
- Nao existe endpoint de execucao direta, de proposito (ED-051).

### 4.2 Human-in-the-loop — **concluido**

Criterio de pronto, atendido: uma execucao que pretende enviar mensagem para em
`waiting_approval`, **sobrevive a um restart completo da aplicacao**, e so executa apos
`POST /approvals/{id}/approve`.

Como ficou:

- Dois nos, e nao um: `automation_plan` decide a acao, `automation_run` pausa e executa.
  A separacao existe porque a retomada reexecuta o no inteiro (ED-052).
- Modelo `Approval` com a acao congelada em JSON: o que foi autorizado fica imutavel no
  registro, independente do estado do grafo.
- `GET /approvals`, `GET /approvals/{id}`, `POST /approvals/{id}/approve` e `/reject`.
  Duas rotas de decisao em vez de um booleano no corpo.
- A pendencia e criada pelo servico, nao pelo no (ED-053); a decisao e confirmada antes
  da retomada (ED-054); ambiguidade conta como recusa (ED-055).
- `tests/integration/test_approval_across_restart.py` derruba app, engine, checkpointer e
  notificador entre a pausa e a decisao. A mensagem sai por um processo que nunca viu a
  solicitacao original.

### 4.3 Agente de automacao — **concluido**

- `AutomationAgent` recebe `ToolRegistry.specs()` no prompt e devolve um `ToolCall`
  validado.
- A checagem contra o catalogo (a ferramenta existe? os argumentos batem com o schema
  dela?) entra no reparo dirigido do `complete_structured`, e nao depois dele (ED-056).
  Uma ferramenta alucinada vira uma segunda tentativa com a lista do que existe.
- `automation` entrou em `EXECUTABLE_AGENTS`. Com isso `agents_skipped` fica vazio para
  todo plano que a triagem consegue produzir hoje -- a lista continua existindo para o
  dia em que um agente novo for adicionado ao schema e esquecido no grafo.

### 4.4 Integracoes

**Slack — concluido.** Segunda implementacao do Protocol `Notifier`, e o momento em que
`Settings` ganhou o seletor `notifier` -- porque so entao passou a existir escolha.

- `SlackNotifier` fala HTTP direto com o incoming webhook. Sem SDK, sem OAuth.
- **Sem retry** (ED-059): webhook nao aceita chave de idempotencia, e repetir pode
  publicar duas vezes um aviso que a equipe ja leu.
- A URL do webhook e credencial: `SecretStr`, fora do log e fora de `details` (ED-060).
- O comprovante devolve o destino REAL, nao o pedido (ED-061). O Protocol vazou, e o
  vazamento foi documentado no contrato em vez de escondido.
- Teste de contrato roda a mesma bateria contra os dois notificadores -- e reprovou a
  primeira versao do `SlackNotifier` (ED-062).
- `GET /health` passou a dizer qual canal esta ativo: uma aprovacao significa coisas
  diferentes conforme a mensagem saia de verdade ou fique em memoria.

**n8n — bloqueado em infraestrutura.** Precisa do Docker Desktop no ar e, antes disso, do
data root movido para outro disco: o C: desta maquina tem ~15 GB livres contra ~188 GB
no D:. Enquanto isso nao acontecer, escrever o `docker-compose.yml` e o JSON do workflow
seria produzir artefato que ninguem consegue executar nem validar.

Quando desbloquear: webhook -> `/agents/run` -> decisao -> acao -> notificacao, com o JSON
do workflow versionado em `workflows_n8n/`. O Hub e o cerebro; o n8n sao os bracos. Evitar
que o n8n vire decorativo: ele precisa disparar E receber o resultado -- o que exige um
callback de saida, hoje inexistente.

**Google Sheets e Notion — adiados por avaliacao de valor.** Cada um seria mais uma
`Tool`, e o mecanismo que elas exercitariam (escopo, aprovacao, execucao, auditoria) ja
esta provado pelo Slack. Sem credencial configurada, o codigo delas seria nao verificavel:
tres integracoes sem teste real valem menos que uma com teste de contrato. Entram quando
houver caso de uso concreto.

Cortado por decisao: Airtable, Make, Zapier (ver README, secao de decisoes).

---

## 5. V5 — AI Quality Gateway e AI Evals

**O diferencial do projeto.** Deve ser construido como **um motor em dois modos**, nao
como dois sistemas -- se forem separados, divergem em uma semana.

### 5.1 Motor de qualidade

`app/quality/` com cinco dimensoes, cada uma em seu modulo:

| Dimensao | Pergunta que responde |
|---|---|
| `grounding` | Toda afirmacao tem fonte entre os trechos citados? |
| `relevance` | A resposta trata da pergunta feita? |
| `completeness` | Cobriu todos os itens do pedido? |
| `consistency` | Ha contradicao interna ou entre agentes? |
| `api_reliability` | Houve erro, timeout ou retry no caminho? |

`api_reliability` sai direto dos dados ja gravados em `agent_executions` -- nao precisa de
LLM. As outras quatro provavelmente precisam (LLM-as-judge), e isso tem custo: medir.

**Modo online:** roda antes de entregar a resposta, grava `quality_score` (a coluna ja
existe em `Execution`). Score abaixo do limite dispara retry dirigido com o motivo da
reprovacao; falhando de novo, `NEEDS_HUMAN_REVIEW` (o estado ja existe no enum).

**Modo offline:** `python run_evals.py` roda o mesmo motor sobre
`evals/evaluation_dataset.json` e emite relatorio em `evals/reports/`.

### 5.2 Conjunto de avaliacao

Formato por entrada: `question`, `expected_topics`, `expected_sources`,
`forbidden_claims`. Cerca de 15 a 20 entradas -- suficiente para o relatorio, barato em
tokens.

**Casos ja identificados que devem entrar no conjunto:**

- Pergunta vaga ("pede envio de e-mail.") produziu `confidence: 0.8`, alta demais para
  quatro palavras. Registrado em ED-029, deliberadamente nao corrigido por prompt ate
  existir medicao.
- Pergunta sobre assunto ausente da base: verificar que `answered` continua `false`
  mesmo quando um trecho fraco passa do corte (aconteceu com score 0.3525 contra corte
  0.35).
- Vocabulario desalinhado: a tarefa pediu "chamados criticos" e os dados usavam
  severidade "alta"; a analise, corretamente, nao encontrou nada. Serve para medir se o
  sistema sinaliza o desalinhamento em vez de entregar relatorio vazio.

### 5.3 Calibrar os cortes de relevancia

`min_relevant_score` hoje e 0.05 (fake) e 0.35 (`text-embedding-3-small`), derivados de
poucas medicoes. Com o conjunto de avaliacao, viram numero medido (ED-038).

---

## 6. V6 — Servidor MCP

Baixo esforco, alto diferencial: a camada `services/` ja e transporte-agnostica.

- `mcp_server/` expondo `search_documents`, `get_execution`, `run_analysis`,
  `create_report`.
- Reaproveitar `RagService`, `ExecutionRepository`, `WorkflowService` sem duplicar regra.
- `docs/mcp.md` explicando: o que e MCP, como funciona, diferenca para uma API REST
  tradicional, e quando usar cada arquitetura.

O argumento forte para a entrevista ja esta pronto: REST e MCP sao adaptadores sobre a
mesma camada de servico.

---

## 7. V7 — Docker, CI/CD e portfolio

### 7.1 Docker

`docker-compose.yml` com `api`, `chromadb`, `n8n` e `postgres`. Migrar de SQLite exige
trocar a URL e introduzir Alembic (ED-020) -- a convencao de nomes de constraint ja esta
pronta para isso (ED-021).

**Atencao de infraestrutura:** o disco de sistema desta maquina tem pouco espaco livre.
Mover o data root do Docker Desktop para outro disco antes de subir a stack completa.

### 7.2 CI (GitHub Actions)

`push` e `pull_request`: `ruff check` -> `ruff format --check` -> `mypy` -> `pytest` ->
evals com provider deterministico -> build da imagem.

A CI nao pode depender de segredo de LLM: o provider padrao `fake` existe exatamente
para isso. Adicionar `gitleaks` para varredura de segredos.

### 7.3 Material de portfolio

- Diagrama de arquitetura (o grafo e o fluxo principal).
- Screenshots do Swagger e GIF do fluxo n8n.
- Relatorio de AI Evals commitado em `evals/reports/`.
- README com `Engineering Decisions` (ja em `docs/engineering-decisions.md`) e
  `What I learned`.
- Descricao curta para LinkedIn e roteiro de apresentacao em entrevista.

---

## 8. Pendencias conhecidas

| Item | Onde | Observacao |
|---|---|---|
| Ingestao sincrona | `POST /documents/upload` | Um PDF grande estoura o timeout do cliente. Exige processamento em segundo plano e mudanca no contrato (status `pending` + consulta posterior). |
| Cortes de relevancia por intuicao | `min_relevant_score` | Vira numero medido no V5. |
| Sem tempo limite na pendencia | `Approval` | Uma acao pode ficar `pending` para sempre. Faltam expiracao e uma varredura que a aplique. |
| Uma pendencia por execucao | `automation_run` | O grafo pausa no maximo uma vez por execucao hoje. Duas acoes de escrita no mesmo plano exigiriam repensar `get_pending_for_execution`. |
| `decided_by` e texto livre | `POST /approvals/{id}/*` | Consequencia de nao haver autenticacao. Com ela, o campo passa a vir da identidade do request. |
| Falha do Slack e definitiva | `SlackNotifier.send` | Sem retry por decisao (ED-059). Uma fila com chave de idempotencia resolveria; nao ha fila. |
| Destino do Slack nao e verificavel | `SLACK_DESTINATION` | Rotulo declarado a mao. O Slack nao expoe o canal do webhook, entao um valor errado passa despercebido. |
| Sem autenticacao | API inteira | Fora do escopo por decisao. Se entrar, API key estatica em header basta. |
| Metrica de custo e estimativa | `app/llm/pricing.py` | Tabela mantida a mao; a fatura do provedor e a fonte de verdade. |

---

## 9. Comandos

```powershell
# qualidade
.venv\Scripts\ruff.exe check .
.venv\Scripts\ruff.exe format --check .
.venv\Scripts\mypy.exe app
.venv\Scripts\pytest.exe --cov

# aplicacao
.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Sem chave de API, o projeto roda por completo com `LLM_PROVIDER=fake` e
`EMBEDDING_PROVIDER=fake`. Com chave, troque no `.env`.

**Cuidado ao trocar de modelo de embedding:** documentos ja indexados com outro modelo
fazem `/rag/query` devolver `409 embedding_model_mismatch`. Para migrar, apague
`data/app.db` e `data/chroma` e reenvie os documentos (ED-041).
