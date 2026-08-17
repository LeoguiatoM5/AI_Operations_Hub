# AI Operations Hub

Plataforma de automacao empresarial que recebe uma solicitacao em linguagem natural,
interpreta a intencao, consulta a base de conhecimento corporativa, decide quais acoes
executar e dispara automacoes -- registrando cada decisao tomada no caminho.

> **Status:** em construcao. Versao atual: `V3 - Workflow multiagente com LangGraph`.

---

## Problema

Times de operacao recebem pedidos em linguagem natural ("analise os chamados criticos de
hoje e gere um relatorio com recomendacoes") e gastam horas coletando dados, cruzando com
documentacao interna e produzindo relatorios manualmente. Automatizar isso com LLMs e
simples; automatizar de forma **confiavel e auditavel** nao e.

## Proposta

Um hub de agentes especializados, orquestrado por um grafo de estados, com uma camada de
qualidade que avalia cada resposta antes de entrega-la e aprovacao humana obrigatoria para
qualquer acao de escrita em sistemas externos.

---

## Roadmap de versoes

| Versao | Escopo | Status |
|---|---|---|
| **V1** | FastAPI + camada multi-LLM + agente de triagem + persistencia + observabilidade | concluido |
| **V2** | RAG com ChromaDB, ingestao de documentos, respostas com fontes citadas | concluido |
| **V3** | LangGraph: quatro agentes, roteamento por plano, checkpointer persistente | concluido |
| **V4** | Ferramentas com escopo, human-in-the-loop, Slack e n8n | concluido |
| V5 | AI Quality Gateway e AI Evals | planejado |
| V6 | Servidor MCP | planejado |
| V7 | Docker, CI/CD e observabilidade completa | planejado |

Detalhamento de cada versao, invariantes do projeto e pendencias conhecidas em
[`docs/roadmap.md`](docs/roadmap.md). As decisoes arquiteturais, com o contexto que as
motivou, em [`docs/engineering-decisions.md`](docs/engineering-decisions.md).

---

## Como executar

Requer Python 3.12 ou superior.

```powershell
# 1. ambiente virtual
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. dependencias (inclui ferramentas de desenvolvimento)
pip install -e ".[dev]"

# 3. configuracao
Copy-Item .env.example .env

# 4. execucao
uvicorn app.main:app --reload
```

<details>
<summary>Equivalentes em cmd e em Linux/macOS</summary>

```bat
:: Windows - Prompt de Comando
python -m venv .venv
.venv\Scripts\activate.bat
pip install -e ".[dev]"
copy .env.example .env
uvicorn app.main:app --reload
```

```bash
# Linux / macOS
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn app.main:app --reload
```

Sem ativar o ambiente, chamando o interpretador do venv diretamente:

```
.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

</details>

Documentacao interativa: <http://127.0.0.1:8000/docs>

### Verificacoes de qualidade

```powershell
ruff check .          # lint
ruff format --check . # formatacao
mypy app              # verificacao de tipos
pytest                # testes
```

---

## Endpoints

| Metodo | Rota | Descricao |
|---|---|---|
| `GET` | `/health` | Estado, versao, ambiente, uptime, provedor de LLM e canal de saida |
| `POST` | `/chat` | Processa uma solicitacao em linguagem natural e devolve a execucao completa |
| `GET` | `/executions` | Lista execucoes com paginacao e filtro por status |
| `GET` | `/executions/{id}` | Detalha uma execucao com toda a cadeia de agentes |
| `POST` | `/documents/upload` | Ingere `.txt`, `.md`, `.json` ou `.pdf` na base de conhecimento |
| `GET` | `/documents` | Lista documentos, com filtro por status |
| `GET` | `/documents/{id}` | Detalha um documento |
| `DELETE` | `/documents/{id}` | Remove o documento e seus trechos do indice |
| `POST` | `/rag/query` | Responde uma pergunta usando a base, citando as fontes |
| `POST` | `/agents/run` | Executa o workflow multiagente e devolve o relatorio consolidado |
| `GET` | `/tools` | Catalogo de ferramentas, com escopo e schema de entrada de cada uma |
| `GET` | `/approvals` | Fila de acoes de escrita aguardando decisao humana |
| `GET` | `/approvals/{id}` | Detalha exatamente o que sera executado, para conferencia |
| `POST` | `/approvals/{id}/approve` | Autoriza a acao e retoma a execucao de onde ela parou |
| `POST` | `/approvals/{id}/reject` | Recusa a acao e conclui a execucao sem executa-la |

Nao ha rota para executar uma ferramenta diretamente. Seria um atalho para disparar acao
de escrita sem passar pela aprovacao -- e ha um teste afirmando que ela nao existe.

Novos endpoints entram a cada versao do roadmap.

### Automacao com n8n

```powershell
docker compose up -d      # n8n em http://localhost:5678
```

`workflows_n8n/aprovacao-de-acao.json` tem **dois triggers**, e a razao e o coracao do V4:
uma execucao que pausa para aprovacao termina horas depois, quando ninguem esta mais
segurando a resposta HTTP. O primeiro webhook dispara o Hub; o segundo recebe o resultado
quando a pessoa decide. Instrucoes em [`workflows_n8n/README.md`](workflows_n8n/README.md).

### Exemplo

```http
POST /chat
{"message": "Envie um e-mail para a diretoria avisando da indisponibilidade de amanha."}
```

```json
{
  "execution_id": "3026d482e69a4e97ac181e5f54a7eb3b",
  "status": "completed",
  "usage": { "prompt_tokens": 875, "completion_tokens": 94, "cost_usd": 0.00018765 },
  "duration_ms": 1882.188,
  "result": {
    "intent": "automacao",
    "summary": "Enviar um e-mail para toda a diretoria informando sobre a indisponibilidade do sistema de pagamentos.",
    "entities": ["sistema de pagamentos", "diretoria"],
    "urgency": "alta",
    "requires_approval": true,
    "suggested_agents": ["automation"],
    "confidence": 0.9
  },
  "steps": [
    {
      "sequence": 1, "agent": "triage", "action": "classify_request", "status": "completed",
      "provider": "openai", "model": "gpt-4o-mini-2024-07-18",
      "prompt_tokens": 875, "completion_tokens": 94, "latency_ms": 1866.4, "attempts": 1
    }
  ]
}
```

O campo `requires_approval` foi decidido pelo modelo: a solicitacao implica **acao de
escrita visivel para terceiros**. Desde o V4, quem decide de fato e o escopo declarado
pela ferramenta escolhida -- o sinal do modelo e uma expectativa, nao a regra.

### Saida do LLM como dado nao confiavel

O modelo devolve texto; `app/llm/structured.py` e a fronteira onde texto vira objeto
tipado -- ou vira erro registrado. Quatro modos de falha cobertos por teste:

| Falha | Tratamento |
|---|---|
| JSON malformado | Retry dirigido: o erro volta ao modelo, que corrige |
| JSON valido violando o schema | Idem, com o campo problematico citado |
| JSON embrulhado em cercas markdown | Cercas removidas antes do parse |
| Modelo nunca acerta | `502 llm_response_format_error`, execucao gravada como `failed` |

Cada tentativa de reparo e uma chamada paga: os custos sao **somados**, nunca apenas o
da ultima.

### Falha nao vira sucesso

| Situacao | HTTP | Registro |
|---|---|---|
| Provedor sem resposta | `504` | execucao `failed`, com `execution_id` em `error.details` |
| Cota estourada | `429` | idem |
| Formato irrecuperavel | `502` | idem |
| Payload invalido | `422` | nenhuma chamada de LLM e feita |

O registro da falha e confirmado em transacao propria, antes de a excecao subir --
caso contrario o rollback apagaria a auditoria junto com a operacao.

---

## Camada de LLM

O projeto nao depende de um unico fornecedor. Todo o codigo de negocio conversa com o
Protocol `LLMProvider` (`app/llm/base.py`); trocar de provedor e mudar uma variavel de
ambiente.

```
LLM_PROVIDER=fake     # padrao: deterministico, sem rede, sem chave
LLM_PROVIDER=openai   # exige OPENAI_API_KEY
```

**O provedor padrao e `fake`** por decisao de projeto: quem clona o repositorio consegue
subir e usar a aplicacao sem possuir credencial alguma, e o pipeline de CI roda a suite
completa sem segredos configurados. O provedor falso tambem aceita um roteiro de
respostas e de excecoes, o que permite testar cenarios que a API real nao produz sob
encomenda -- timeout, cota estourada, JSON quebrado, resposta vazia.

Toda resposta carrega os dados de observabilidade junto com o conteudo:

| Campo | Uso |
|---|---|
| `usage` | tokens de entrada e de saida |
| `cost_usd` | custo estimado, calculado com tarifas separadas por entrada/saida |
| `latency_ms` | latencia medida por nos, nao pelo SDK |
| `attempts` | tentativas ate obter a resposta |
| `provider` / `model` | qual modelo respondeu de fato |

### Retry e timeout

`RetryingLLMProvider` (`app/llm/retrying.py`) decora qualquer provedor com backoff
exponencial e *full jitter*. Repete apenas falhas transitorias -- timeout e limite de
cota; credencial invalida falha na primeira tentativa, porque insistir so gastaria
tempo e cota.

O retry interno do SDK da OpenAI e desligado (`max_retries=0`) de proposito: uma
repeticao silenciosa dentro da biblioteca corromperia a medicao de latencia e a
contagem de tentativas.

---

## Workflow multiagente

```
START -> orchestrator -+-> research --+
                       |              |
                       +-> analysis --+--> (roteador) -> reporter -> END
                       |
                       +-> END   (plano nao pode ser produzido)
```

Cinco agentes, cada um com prompt versionado em arquivo e saida validada por schema:

| Agente | Responsabilidade | Guarda de coerencia |
|---|---|---|
| `orchestrator` | Classifica a solicitacao e monta a fila de agentes | `requires_approval` exige `automation` no plano |
| `research` | Consulta a base de conhecimento | Citacao fora do intervalo recuperado e rejeitada |
| `analysis` | Encontra padroes nos dados | Todo achado exige evidencia literal; confianca alta exige achado |
| `automation` | Escolhe a ferramenta e monta os argumentos | A ferramenta precisa existir e os argumentos precisam satisfazer o schema DELA |
| `reporter` | Consolida tudo, inclusive o que falhou | Relatorio vazio precisa declarar limitacoes |

### O caminho e decidido pelo plano, nao pelo codigo

Execucao real, com `gpt-4o-mini`:

```
plano do orquestrador : ['analysis', 'research', 'reporter']
caminho percorrido    : orchestrator -> analysis -> research -> reporter
```

O roteamento e uma `conditional_edge` que consulta uma **funcao pura** do estado
(`route_next`). Isso permite testar todos os caminhos do grafo -- inclusive falha fatal,
agente desconhecido e fila vazia -- em milissegundos, sem executar agente nenhum.

### Falha de um agente nao derruba a execucao

```json
{"status": "completed",
 "agents_executed": ["orchestrator", "reporter"],
 "analysis": null,
 "errors": [{"agent": "analysis", "code": "llm_timeout", "message": "..."}],
 "report": {"limitations": ["A analise falhou por timeout do provedor."]}}
```

Um relatorio que declara o que falhou vale mais que `502` com nada aproveitado -- o custo
dos agentes anteriores ja foi pago. A unica falha fatal e a do orquestrador: sem plano,
nao ha o que executar.

### Estado persistido a cada no

```
thread_id  : d7d21c68d94f4bd8a95f9818fa450d83
checkpoints: 6
canais     : plan, pending_agents, analysis, research, report, completed, errors,
             branch:to:orchestrator, branch:to:analysis, branch:to:research, ...
```

O checkpointer grava o estado apos cada superstep. E o que permite, desde o V4, pausar em
`WAITING_APPROVAL` e retomar do ponto exato -- sem reexecutar os agentes anteriores nem
pagar os tokens de novo. Ha um teste que **derruba e recria a aplicacao inteira** entre a
pausa e a aprovacao para provar que a retomada nao depende do processo original.

### Custo de uma execucao completa

Quatro agentes, cinco chamados analisados, base de conhecimento consultada:
**4.413 tokens, US$ 0,000908, 8,6 segundos.**

---

## Busca semantica

Duas abstracoes independentes, pelo mesmo motivo de sempre: sao eixos de evolucao
diferentes. O modelo de embedding muda por qualidade e custo; o banco vetorial muda por
escala e operacao.

```
Documento -> normalizacao -> chunking -> embedding -> VectorStore
                                                          |
Pergunta  --------------------------> embedding -----> busca -> trechos + score
```

| Componente | Implementacoes | Troca por |
|---|---|---|
| `EmbeddingProvider` | `fake` (hashing lexical), `openai` | `EMBEDDING_PROVIDER` |
| `VectorStore` | `memory` (referencia), `chroma` (persistente) | `VECTOR_STORE` |

### O provedor falso nao e um placeholder

`FakeEmbeddingProvider` e um vetorizador por hashing: cada palavra de conteudo ocupa
sempre a mesma posicao do vetor. Isso produz **similaridade lexical real** -- os testes
verificam comportamento de recuperacao, e nao apenas encanamento, sem rede e sem custo.

O limite dele e conhecido e medido. Mesma base, mesmas perguntas:

| Pergunta | `fake` | `openai` |
|---|---|---|
| "prazo para solicitar **reembolso** de despesas" | acerta (0.250) | acerta (0.773) |
| "quanto tempo para **pedir de volta um valor que gastei**" | **erra** | acerta (0.587) |
| "trocar minha **credencial de acesso**" | acerta por 0.002 (ruido) | acerta (0.641) |

Na segunda pergunta nao ha uma palavra em comum com o documento correto. E exatamente
para isso que servem embeddings de verdade -- e ter os dois lado a lado torna a diferenca
demonstravel, em vez de afirmada.

### Detalhes que costumam quebrar RAG

| Armadilha | Como e tratada |
|---|---|
| `hash()` de string e aleatorizado por processo | Hash estavel via `blake2b`: indice de hoje continua valido amanha |
| Banco vetorial devolve **distancia**, nao similaridade | Convertido na fronteira de cada implementacao; `score` e sempre "maior e melhor" |
| Chroma baixa um modelo ONNX de dezenas de MB | Fornecemos os vetores; ele e so o indice |
| Cliente do Chroma e sincrono | Envolvido em `asyncio.to_thread`, para nao travar o event loop |
| `chunk_overlap >= chunk_size` gera divisao infinita | Rejeitado na validacao da configuracao, no startup |

### Teste de contrato

A mesma bateria de 13 testes roda contra `InMemoryVectorStore` e `ChromaVectorStore`.
E o que sustenta a afirmacao de que trocar de banco vetorial e mudar uma variavel: se o
Chroma divergir do comportamento de referencia, a suite quebra.

O mesmo vale para os notificadores (`MemoryNotifier` e `SlackNotifier`), e ali o teste ja
se pagou: ele reprovou a primeira versao do `SlackNotifier`, cuja referencia de entrega
era derivada so do instante e colidia entre dois envios no mesmo microssegundo. Um
identificador que colide nao identifica nada -- e o defeito nao apareceria em nenhum
teste do Slack isolado, porque a exigencia de unicidade e do **contrato**, nao da
implementacao.

---

## Ingestao de documentos

```
POST /documents/upload   (multipart, campo `file`)
```

```json
{
  "document_id": "f8402ca6b1a549dcaae59cad561531a6",
  "filename": "runbook.json",
  "status": "indexed",
  "chunk_count": 1,
  "char_count": 103,
  "content_hash": "e520362acc7d80d222a27054851018dd...",
  "embedding_provider": "openai",
  "embedding_model": "text-embedding-3-small",
  "metadata": {"json_fields": 3},
  "indexed_at": "2026-08-16T19:23:50.874906Z"
}
```

Formatos aceitos: `.txt`, `.md`, `.markdown`, `.json`, `.pdf`.

### Consistencia entre dois sistemas

A ingestao escreve no banco relacional (metadados) e no banco vetorial (trechos). **Nao
existe transacao comum entre os dois.** Um processo interrompido no meio deixaria um
documento registrado sem nenhum vetor indexado -- invisivel na busca, sem erro algum.

A resposta e estado explicito e ordem deliberada:

```
pending -> processing -> grava vetores -> indexed
```

Um documento parado em `processing` e evidencia de processo interrompido, nao misterio.
Na falha, os vetores parciais sao removidos antes de o documento virar `failed`. Na
remocao a ordem se inverte -- vetores primeiro, metadados depois -- para nunca existir
trecho indexado sem origem identificavel.

### Cada formato tem sua armadilha

| Formato | Tratamento |
|---|---|
| `.txt` / `.md` | Cadeia de codificacoes com `utf-8-sig` **antes** de `utf-8` (ver ED-037) |
| `.json` | Achatado em linhas `caminho: valor` -- indexar JSON cru gastaria o trecho com chaves e colchetes |
| `.pdf` | Assinatura verificada; PDF sem texto extraivel e rejeitado, nao indexado vazio |
| qualquer | SHA-256 do conteudo impede reindexacao duplicada (`409`) |

### Respostas de erro

| Situacao | HTTP | Codigo |
|---|---|---|
| Conteudo identico ja ingerido | `409` | `duplicate_document` |
| Acima do limite de tamanho | `413` / `422` | `document_too_large` |
| Extensao nao suportada | `415` | `unsupported_document` |
| Arquivo corrompido | `422` | `document_extraction_failed` |
| Sem texto extraivel (PDF escaneado) | `422` | `empty_document` |

---

## Consulta com fontes rastreaveis

```http
POST /rag/query
{"question": "Qual o prazo para solicitar reembolso de despesas?"}
```

```json
{
  "answered": true,
  "answer": "Colaboradores podem solicitar reembolso de despesas em até 30 dias corridos após o gasto.",
  "confidence": 1.0,
  "sources": [
    {"number": 1, "cited": true, "score": 0.767, "filename": "politica-reembolso.md",
     "document_id": "03c4e62f...", "excerpt": "# Politica de reembolso..."}
  ],
  "retrieval": {"chunks_retrieved": 1, "chunks_cited": 1, "min_score": 0.35, "best_score": 0.7666},
  "usage": {"total_tokens": 419, "cost_usd": 0.00012242},
  "repairs": 0
}
```

### Nao responder e uma resposta

Pergunta cujo assunto nao esta na base, medida em execucao real:

```
PERGUNTA: Quantos dias de ferias eu tenho direito por ano?
  respondeu : false
  resposta  : Os trechos fornecidos nao contem informacoes sobre o numero de dias de
              ferias a que um colaborador tem direito por ano.
  busca     : 1 trecho recuperado, 0 citados | corte=0.35 melhor=0.3525
    [1]        0.352  politica-reembolso.md
```

**Duas camadas de defesa, e a segunda cobriu a falha da primeira.** A recuperacao errou:
devolveu um trecho de 0.3525, logo acima do corte, do documento errado. O agente, ainda
assim, recusou-se a responder. Um sistema que so confia no corte de similaridade teria
respondido sobre reembolso a uma pergunta sobre ferias.

### Anti-alucinacao pela validacao

O schema de resposta e construido **a cada consulta**, com as citacoes restritas ao
intervalo de trechos realmente recuperados:

```python
Citation = Annotated[int, Field(ge=1, le=source_count)]
```

Se o modelo citar `[7]` quando existem quatro trechos, o Pydantic rejeita e o retry
dirigido pede a correcao -- o mesmo mecanismo que garante formato passa a garantir
**ancoragem**. Um validador complementar recusa `answered: true` sem nenhuma citacao:
afirmar que respondeu sem dizer de onde e, por definicao, resposta nao ancorada.

Quando nenhum trecho passa do corte, **o LLM nao e chamado**. Consultar o modelo sem
contexto e convidar a alucinacao: ele responderia com conhecimento proprio, e a resposta
pareceria tao fundamentada quanto uma real.

### O corte de relevancia pertence ao modelo

```python
provider.min_relevant_score  # fake: 0.05   |   text-embedding-3-small: 0.35
```

A escala de similaridade depende do modelo de embedding -- um valor unico na configuracao
rejeitaria quase tudo com um provedor e nada com outro. `RAG_MIN_SCORE` existe apenas
como sobrescrita consciente.

### Base misturada e recusada

```json
{"error": {"code": "embedding_model_mismatch",
  "details": {"current_model": "text-embedding-3-small",
              "models_in_index": ["fake-embedding-1"],
              "hint": "Remova e reenvie os documentos, ou volte ao modelo anterior."}}}
```

Vetores de modelos diferentes ocupam espacos diferentes: compara-los produz
similaridades sem significado. E o sistema **nao teria como perceber** -- os numeros
continuam entre 0 e 1 e resultados continuam aparecendo. Eles e que seriam aleatorios.

---

## Persistencia e rastreabilidade

Cada pedido produz uma linha em `executions` e uma linha em `agent_executions` **por
passo de agente**. E essa cadeia que permite responder "por que a IA concluiu isso?" --
com custo, latencia, tentativas e erro de cada etapa.

O formato do rastro, com uma acao de escrita que passou por aprovacao humana:

```
EXECUCAO  9e164013748d4748a5e843b6d9fca3b5
  status: completed   duracao: 41320.8 ms   tokens: 3874   custo: US$ 0.00092
------------------------------------------------------------------------------
  1. [ok   ] orchestrator  plan                       1420.5 ms   306 tok  tent.=1
  2. [ok   ] research      answer_from_knowledge_base 2890.1 ms  2152 tok  tent.=2
  3. [ok   ] automation    choose_tool                1932.4 ms   412 tok  tent.=1
        --- pausado em waiting_approval; aprovado por leonardo ---
  4. [ok   ] automation    execute_tool                284.0 ms     0 tok  tent.=1
  5. [ok   ] reporter      write_report               4120.7 ms  1004 tok  tent.=1
```

A duracao total inclui o tempo em que a execucao ficou parada esperando uma pessoa --
que e a verdade, e nao um defeito de medicao. `execute_tool` nao consome tokens: e um
POST, nao uma chamada de LLM. O passo 2 registra `tent.=2` porque a primeira resposta do
modelo foi rejeitada na validacao e o reparo dirigido corrigiu; o custo das duas
tentativas esta somado.

| Decisao | Motivo |
|---|---|
| SQLAlchemy assincrono (`aiosqlite`) | Driver bloqueante travaria o event loop a cada consulta, anulando a concorrencia da camada de LLM |
| Duas tabelas, nao uma | Achatar tudo em uma linha destruiria a cadeia de decisoes |
| `UtcDateTime` proprio | SQLite nao guarda fuso; sem isso, duracoes calculadas depois mentem |
| Agregados na escrita | Listar cem execucoes nao deve varrer todos os passos de cada uma |
| `create_all` agora, Alembic no V7 | Migracao versionada so importa quando ha dados que nao podem ser perdidos |

Trocar para PostgreSQL e mudar uma variavel:

```
DATABASE_URL=postgresql+asyncpg://usuario:senha@localhost:5432/aiops
```

### Experimentar pela linha de comando

```powershell
python scripts/try_llm.py "Quais chamados criticos exigem escalonamento imediato?"
```

```
--- observabilidade ------------------------------------------------
  provider      : fake
  model         : fake-model-1
  tokens        : 32 entrada / 34 saida / 66 total
  custo estimado: US$ 0.00000000
  latencia      : 0.0 ms
  tentativas    : 1
--------------------------------------------------------------------
```

---

## Convencoes

**Correlation ID.** Todo request recebe (ou reaproveita, via header `X-Correlation-ID`)
um identificador que acompanha cada log gerado no caminho. E o que permite reconstruir
uma execucao inteira -- inclusive as chamadas de LLM e integracoes que ela disparou.

**Envelope de erro.** Toda falha retorna o mesmo formato:

```json
{
  "error": {
    "code": "not_found",
    "message": "Execucao nao encontrada.",
    "details": {"execution_id": "abc"},
    "correlation_id": "0f2c1f4e-..."
  }
}
```

Detalhes internos ficam no log; a resposta nunca expoe stack trace.

**Segredos.** Nenhuma chave de API no codigo ou no repositorio. Toda configuracao passa
por `app/core/config.py`, validada na inicializacao.

---

## Licenca

MIT
