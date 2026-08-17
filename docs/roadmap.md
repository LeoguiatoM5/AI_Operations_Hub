# Roadmap e estado do projeto

Documento de continuidade: o que ja existe, quais invariantes o codigo respeita, e o que
falta construir. Serve para retomar o trabalho sem reconstruir contexto.

Atualizado ao final do **V5**.

---

## 1. Estado atual

| Versao | Escopo | Situacao |
|---|---|---|
| V1 | FastAPI, camada multi-LLM, agente de triagem, persistencia, observabilidade | concluido |
| V2 | RAG com ChromaDB, ingestao de documentos, consulta com fontes citadas | concluido |
| V3 | LangGraph, quatro agentes, roteamento por plano, checkpointer persistente | concluido |
| V4 | Ferramentas com escopo, human-in-the-loop, Slack e n8n | concluido |
| V5 | AI Quality Gateway e AI Evals | concluido |
| V6 | Servidor MCP | planejado |
| V7 | Docker, CI/CD, observabilidade completa, material de portfolio | planejado |

**Numeros:** 518 testes, 97% de cobertura, `ruff` e `mypy` limpos, 80 decisoes
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
  integrations/  saidas emitidas pela aplicacao, nao escolhidas por agente (callback)
  workflows/     state (TypedDict + reducers), nodes, graph, checkpointer
  services/      execution, document, rag, workflow   <- regra de negocio, sem FastAPI
  repositories/  acesso a dados (execution, document)
  quality/       motor de qualidade: cinco dimensoes, agregacao, juizes por LLM
  evals/         conjunto de avaliacao: carga, assercoes, runner, relatorio
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

**n8n — concluido.** O Docker foi migrado para o D: antes de subir a stack (ED-067): C:
passou de 14,7 GB para 28,7 GB livres, com as 14 imagens e os 11 volumes preservados.

- `docker-compose.yml` sobe o n8n com os dados em `data/n8n/`, fora do Git. A API continua
  no host enquanto o projeto esta em desenvolvimento -- recarga automatica vale mais que a
  simetria de ter tudo em container. No V7 a `api`, o `chromadb` e o `postgres` entram.
- `app/integrations/callback.py` fecha o circuito: o Hub publica o resultado quando uma
  execucao pausada e retomada. Sem isso o n8n dispararia e nunca saberia como terminou.
- O callback **engole erro de rede** (ED-064) e dispara **apenas na retomada** (ED-065).
- `workflows_n8n/aprovacao-de-acao.json`, versionado, com **dois triggers**: um recebe a
  solicitacao, o outro recebe o resultado que chega horas depois.

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

### 5.2 Conjunto de avaliacao — **concluido**

`evals/evaluation_dataset.json` com 16 casos sobre um corpus de 5 documentos, e
`run_evals.py` como modo offline do mesmo motor.

- Cada caso declara `note`: **por que existe**. Ha teste de contrato exigindo isso -- um
  caso cujo motivo ninguem lembra e apagado no primeiro dia em que der trabalho.
- Tres assercoes deterministicas, sem LLM, sustentam o veredito (ED-074). As notas
  descrevem *quao bem*; as assercoes dizem *se*.
- `python run_evals.py` roda gratis (so assercoes); `--judge` acrescenta as dimensoes.
  Codigo de saida diferente de zero quando ha reprovacao, para a CI poder quebrar.
- O relatorio mais recente fica versionado em `evals/reports/latest.md`.

**O conjunto se pagou na primeira rodada**, encontrando dois defeitos no proprio portao:
recusa correta era punida (ED-075) e vereditos legitimos de grounding eram descartados
(ED-076) -- este ultimo rejeitaria respostas corretas em producao.

**Casos do roadmap, atendidos:** `pedido-vago` (o texto do ED-029),
`ausente-plano-de-saude` (assunto ausente com trecho fraco acima do corte) e
`chamado-vocabulario-desalinhado` (vocabulario que a base nao tem).

### 5.3 Calibracao — **concluida**

Tres rodadas com `EMBEDDING_PROVIDER=openai`, adjudicacao dos casos em disputa lendo os
chunks, e mais tres rodadas de verificacao. Custo total: cerca de US$ 0,05.

**A adjudicacao mudou o trabalho.** Os tres casos em que o juiz discordava das assercoes
estavam **literalmente** sustentados pelos trechos citados: o sistema estava certo e o
juiz errado. Nao era severidade a tolerar -- era defeito a corrigir (ED-078). Nao se
calibra um instrumento quebrado.

| | antes | depois |
|---|---|---|
| amplitude entre rodadas | 0.182 | **0.000** |
| casos instaveis | 2 de 16 | **0 de 16** |
| `senha-sms` | 0.27 | 0.91 |
| `remoto-exterior` | 0.64 | 1.00 |

**O que ficou medido:**

- **Corte de relevancia: 0.35, e ele nao faz o que se pensava** (ED-079). Respostas
  corretas ficaram entre 0.477 e 0.767; recusas corretas com contexto, em 0.526 e 0.552.
  Os grupos se sobrepoem, entao nenhum corte os separa. O corte e filtro de CUSTO; a
  honestidade vem do contrato `answered=false` do agente.
- **Limite de qualidade: 0.7 fica** (ED-080), com 0.21 de margem para a pior resposta boa
  e variacao zero do medidor.
- **Pesos por dimensao: inalterados.** Com todas as dimensoes em 1.00, nenhum peso muda
  resultado -- ajusta-los seria falsa precisao.
- **A variacao do juiz nao era ruido de amostragem** (ED-077), e sim ambiguidade da
  tarefa. A conclusao anterior estava errada e foi reescrita.

**O que o conjunto ainda nao consegue dizer.** Onde esta a fronteira de reprovacao: nao ha
nenhum caso que o sistema responda mal, entao nao ha exemplo negativo entre 0.0 e 0.91.
Falta acrescentar respostas degradadas de proposito -- uma que cita fonte errada, uma que
cobre metade do pedido, uma que se contradiz.

### 5.4 Fora do escopo do conjunto atual

O conjunto exercita o caminho de **RAG** (`RagService`). Duas coisas ficam sem medicao:

- **A confianca da triagem** do ED-029 -- o `confidence: 0.8` para "pede envio de
  e-mail." e uma propriedade do `TriageAgent`, e nao da resposta a uma pergunta. Medir
  exigiria um segundo harness, com casos que declaram faixas esperadas de confianca.
- **O workflow multiagente inteiro.** Avaliar `/agents/run` sobre o conjunto custaria
  varias vezes mais por caso e mediria muitas coisas de uma vez; o caminho de RAG isola
  melhor recuperacao e fundamentacao.

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

**Infraestrutura ja resolvida no V4:** o Docker roda com o disco em
`D:\Docker\DockerDesktopWSL` (ED-067), e o `docker-compose.yml` ja existe com o servico
`n8n`. O V7 acrescenta `api`, `chromadb` e `postgres` ao mesmo arquivo.

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
| Callback sem reentrega | `WebhookPublisher` | Se o n8n estiver fora do ar, o resultado se perde. A execucao continua consultavel em `GET /executions/{id}`; uma fila resolveria, e nao ha fila. |
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




