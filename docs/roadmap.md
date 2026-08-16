# Roadmap e estado do projeto

Documento de continuidade: o que ja existe, quais invariantes o codigo respeita, e o que
falta construir. Serve para retomar o trabalho sem reconstruir contexto.

Atualizado ao final do **V3**.

---

## 1. Estado atual

| Versao | Escopo | Situacao |
|---|---|---|
| V1 | FastAPI, camada multi-LLM, agente de triagem, persistencia, observabilidade | concluido |
| V2 | RAG com ChromaDB, ingestao de documentos, consulta com fontes citadas | concluido |
| V3 | LangGraph, quatro agentes, roteamento por plano, checkpointer persistente | concluido |
| V4 | Integracoes externas e human-in-the-loop | **proximo** |
| V5 | AI Quality Gateway e AI Evals | planejado |
| V6 | Servidor MCP | planejado |
| V7 | Docker, CI/CD, observabilidade completa, material de portfolio | planejado |

**Numeros:** 309 testes, 96% de cobertura, `ruff` e `mypy` limpos, 47 decisoes
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
  agents/        triage, research, analysis, reporter + prompts/*.md
  workflows/     state (TypedDict + reducers), nodes, graph, checkpointer
  services/      execution, document, rag, workflow   <- regra de negocio, sem FastAPI
  repositories/  acesso a dados (execution, document)
  models/        SQLAlchemy (Execution, AgentExecution, Document)
  api/           rotas, deps (injecao), middleware (correlation ID), errors, responses
  schemas/       Pydantic de entrada e saida
```

**Endpoints:** `/health`, `/chat`, `/executions`, `/executions/{id}`,
`/documents/upload`, `/documents`, `/documents/{id}`, `/rag/query`, `/agents/run`.

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

**Custo e observabilidade**

10. Todo passo grava tokens, custo estimado, latencia, tentativas, provider e modelo.
11. Tentativas de reparo somam custo; reportar so a ultima esconderia metade (ED-023).
12. Todo request tem `correlation_id`, propagado ate os logs das bibliotecas.

**Testes**

13. A suite nunca chama API paga nem toca disco do projeto.
14. Componentes com mais de uma implementacao tem **teste de contrato** rodando a mesma
    bateria contra todas (ver `tests/integration/test_vector_stores.py`).
15. Substituto de teste que nunca encontra o componente real esconde defeito: o caminho
    de producao precisa de ao menos um teste (ED-047).

---

## 4. V4 — Integracoes e human-in-the-loop

**Objetivo:** o sistema executa acoes reais em sistemas externos, e nenhuma acao de
escrita acontece sem aprovacao humana explicita.

### 4.1 Human-in-the-loop

E a peca central, e o que ja esta preparado para ela:

- `TriageResult.requires_approval` ja e decidido pelo orquestrador e validado por
  coerencia (`requires_approval=true` exige `automation` no plano).
- `ExecutionStatus.WAITING_APPROVAL` ja existe no enum.
- O checkpointer persistente ja funciona e ja tem teste provando recuperacao de estado
  por uma segunda instancia (`tests/integration/test_checkpointer.py`).

A construir:

- No `automation` no grafo, precedido de `interrupt()` do LangGraph quando
  `requires_approval` for verdadeiro.
- Modelo `Approval` (execucao, acao pretendida, payload, status, decisor, motivo,
  timestamps).
- `POST /approvals/{id}/approve` e `/reject`, que retomam o grafo pelo `thread_id`.
- `GET /approvals` para listar pendencias.
- Teste que **derruba e recria a aplicacao** entre a pausa e a aprovacao, provando que a
  retomada nao depende do processo original.

Criterio de pronto: uma execucao que pretende enviar e-mail para em `waiting_approval`,
sobrevive a um restart, e so executa apos `POST /approvals/{id}/approve`.

### 4.2 Registro de ferramentas com escopo

- `ToolRegistry` com cada ferramenta declarando escopo `read` ou `write`.
- Somente `write` passa por aprovacao. A regra fica no registro, nao espalhada nos nos.

### 4.3 Integracoes

Prioridade, por relacao valor/esforco:

1. **n8n** (self-hosted, docker). Workflow real: webhook -> `/agents/run` -> decisao ->
   acao -> notificacao. O JSON do workflow entra em `workflows_n8n/`, versionado.
2. **Slack** via incoming webhook -- a integracao mais barata que existe.
3. **Google Sheets** via service account (conta de servico, credencial fora do repo).
4. **Notion** via token de integracao.

O Hub e o cerebro; o n8n sao os bracos. Evitar que o n8n vire decorativo: ele precisa
disparar E receber o resultado.

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
| Agente `automation` ausente | `EXECUTABLE_AGENTS` | Planejado pelo orquestrador, listado em `agents_skipped`. Entra no V4. |
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
