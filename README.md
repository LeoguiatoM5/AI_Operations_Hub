# AI Operations Hub

Plataforma de automacao empresarial que recebe uma solicitacao em linguagem natural,
interpreta a intencao, consulta a base de conhecimento corporativa, decide quais acoes
executar e dispara automacoes -- registrando cada decisao tomada no caminho.

> **Status:** V1 a V7 concluidas. 571 testes, 97% de cobertura, 100 decisoes de engenharia
> documentadas com o contexto que as motivou -- inclusive as que se provaram erradas e
> foram reescritas.

**Tres garantias, cada uma com um mecanismo proprio:**

| Garantia | Como |
|---|---|
| Nao inventa | Sem contexto relevante, a chamada de LLM **nem acontece** -- a resposta e "a base nao cobre isto" |
| Nao age sozinho | Acao de escrita para em `waiting_approval` e **sobrevive a um restart** ate uma pessoa decidir |
| Nao entrega sem medir | Cinco dimensoes avaliam a resposta; reprovada, ela e reescrita com o motivo da reprovacao |

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
| **V5** | Portao de qualidade em cinco dimensoes + conjuntos de avaliacao | concluido |
| **V6** | Servidor MCP sobre a mesma camada de servico | concluido |
| **V7** | Docker, CI sem segredos, PostgreSQL com Alembic, material de portfolio | concluido |

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

![Swagger: visao geral da API](docs/images/swagger-visao-geral.jpg)

As rotas que distinguem o projeto -- catalogo de ferramentas com escopo, e a fila de acoes
esperando decisao humana:

![Swagger: ferramentas e aprovacoes](docs/images/swagger-ferramentas-e-aprovacoes.jpg)

### Rodar com Docker

A stack inteira -- API e n8n -- sobe com um comando:

```powershell
docker compose up -d --build --wait
curl http://localhost:8000/health
```

**O `--wait` nao e opcional na pratica.** Sem ele, `up -d` devolve o controle quando o
container INICIA, e nao quando a aplicacao comeca a atender. Nessa janela a porta ja
aceita conexao (o proxy do Docker sobe junto com o container) e o Python ainda nao
escuta -- o `curl` responde `(52) Empty reply from server`, que parece defeito e e apenas
pressa. Com `--wait`, o comando so retorna quando os healthchecks passam.

Sem nenhuma configuracao, a stack roda com provider deterministico. Com um `.env`
preenchido, o compose le as variaveis **do host** e as injeta como ambiente -- o arquivo
nunca entra na imagem (`.dockerignore`), porque camada de imagem nao se apaga.

A imagem roda como usuario sem privilegio, e o codigo dentro dela pertence ao root: a
aplicacao le o que executa e nao consegue reescrever. Detalhes, e as CVEs conhecidas sem
correcao, em [`docs/security.md`](docs/security.md).

### PostgreSQL e migracoes

O padrao e SQLite -- um clone recem-baixado deve subir sem esperar banco inicializar. Para
exercitar o caminho que a producao usaria:

```powershell
docker compose --profile postgres up -d --wait
alembic -x url=postgresql+asyncpg://aiops:aiops@localhost:5433/aiops upgrade head
$env:DATABASE_URL="postgresql+asyncpg://aiops:aiops@postgres:5432/aiops"
docker compose --profile postgres up -d --wait api
```

O mesmo arquivo de migracao produz o esquema em SQLite e em PostgreSQL -- e e assim que a
afirmacao "trocar de banco e trocar a URL" deixa de ser folclore de README e vira coisa
verificada.

**Por que a 5433 e nao a 5432.** Um PostgreSQL instalado na maquina disputa a porta padrao
com o container, e o Windows deixa os dois abrirem sem erro: a conexao cai no servico local
e falha com uma mensagem que nao acusa a causa (ED-093).

**`create_all` e migracao convivem.** O primeiro cria tabelas em banco descartavel (a
suite, um clone novo); o segundo altera banco que guarda dados de alguem. Em PostgreSQL o
`create_all` nao faz nada, de proposito. Um teste roda `alembic check` e falha se um modelo
mudar sem a migracao correspondente -- e o que impede os dois caminhos de divergirem.

### Automacao com n8n

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

| Operacao | Custo medido |
|---|---|
| Consulta a base que **responde** | US$ 0,0002 |
| Consulta a base que **recusa** | US$ 0,0000003 -- o LLM nem e chamado |
| Workflow completo (4 agentes, com RAG) | US$ 0,0009 |
| Portao de qualidade ligado | ~2x o custo da execucao |
| Conjunto de avaliacao (16 casos, com juizes) | US$ 0,009 |
| Conjunto do detector (13 casos) | US$ 0,007 |

A segunda linha e a mais interessante: uma pergunta que a base nao cobre custa **tres
decimos de milionesimo de dolar**, porque so o embedding da pergunta e pago. Recusar sai
seiscentas vezes mais barato que responder.

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

A mesma bateria roda contra `InMemoryVectorStore` e `ChromaVectorStore` -- 13 casos, 26
execucoes.
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

## Aprovacao humana

Nenhuma acao que escreve em sistema externo acontece sem uma pessoa autorizar. O que torna
isso real, e nao um campo no banco, e o checkpointer: a execucao **pausa**, o estado vai
para o disco, e a decisao pode chegar horas depois -- de outro processo.

```
POST /agents/run  "avise o time sobre os chamados criticos"
  -> status: waiting_approval    nada foi executado
  -> pending_approval: { tool, arguments, reason }

   ... a aplicacao pode cair, subir de novo, fazer deploy ...

POST /approvals/{id}/approve  { "decided_by": "leonardo" }
  -> executa EXATAMENTE o que foi mostrado  -> completed
```

**A automacao ocupa dois nos do grafo, e a razao e sutil.** A retomada de um `interrupt()`
reexecuta o no **inteiro**, e nao a linha seguinte. Se decidir a acao e executa-la
vivessem juntos, cada aprovacao pagaria de novo a chamada de LLM -- e o modelo poderia
escolher **outra coisa**, executando algo diferente do que a pessoa aprovou. O checkpoint
entre os dois congela a acao antes de qualquer humano ver a tela.

`tests/integration/test_approval_across_restart.py` derruba aplicacao, engine, checkpointer
e notificador entre a pausa e a decisao. A mensagem sai por um processo que nunca viu a
solicitacao original.

**Quem decide nao pode ser a IA.** O escopo (`read`/`write`) e declarado pela propria
ferramenta, e `GET /tools` publica isso. Nao existe rota para executar ferramenta direto,
e o servidor MCP nao tem ferramenta de aprovar -- ha testes afirmando as duas ausencias.

---

## Portao de qualidade

Cinco dimensoes avaliam a resposta antes de ela ser entregue. Desligado por padrao: custa
tres a quatro chamadas de LLM por execucao.

| Dimensao | Pergunta | Custo |
|---|---|---|
| `grounding` | Toda afirmacao tem fonte entre os trechos citados? | LLM |
| `relevance` | A resposta trata do que foi pedido? | LLM |
| `completeness` | Cobriu todos os itens do pedido? | LLM |
| `consistency` | Ha contradicao interna? | LLM |
| `api_reliability` | Houve erro, timeout ou repeticao no caminho? | **gratis** |

**Nao se pergunta uma nota ao modelo.** O juiz **classifica** -- cada afirmacao como
sustentada ou nao, cada item do pedido como coberto ou nao -- e a aritmetica e nossa. A
nota fica auditavel (vem com a lista), reproduzivel (mesma classificacao, mesma nota) e
**testavel sem rede**.

Reprovada, a resposta volta para uma reescrita que recebe o **motivo exato** da reprovacao.
Reprovando de novo, a execucao termina em `needs_human_review` -- com a resposta entregue
assim mesmo, marcada. Reter o resultado perderia o material que custou tokens.

```powershell
# no .env: QUALITY_ENABLED=true
# QUALITY_THRESHOLD=0.0 mede tudo sem reprovar nada (modo sombra)
```

---

## Avaliacao: dois conjuntos, duas perguntas

```powershell
python run_evals.py            # so assercoes deterministicas: gratis
python run_evals.py --judge    # + as dimensoes por LLM: ~US$ 0,009
python run_evals.py --detector # mede o MEDIDOR: ~US$ 0,007
```

**`evaluation_dataset.json` pergunta "a resposta esta boa?"** -- 16 casos que rodam o
sistema de verdade sobre um corpus de politicas. Tres assercoes **sem LLM** sustentam o
veredito: respondeu quando devia, citou as fontes esperadas, nao disse o proibido. Sem
vies, sem custo, sem variacao entre execucoes.

**`detector_cases.json` pergunta "o medidor percebe quando ela esta ruim?"** -- 13
respostas com defeito **conhecido**, escritas a mao, que nao passam pelo sistema. Existe
porque nao da para calibrar um limite sem saber que nota uma resposta ruim tira, e o
sistema acertava 15 de 16 casos com nota 1.00.

Foi assim que `QUALITY_THRESHOLD` deixou de ser chute:

```
respostas BOAS  ...  0.91 ─────────── 1.00
respostas RUINS ...  0.39 ──── 0.76
                                   ↑
                    limite: 0.85 na lacuna
```

O valor anterior, 0.7, **nao era so impreciso -- era errado**: duas respostas
comprovadamente ruins pontuavam 0.76 e teriam passado pelo portao.

O conjunto do detector se pagou na estreia, achando dois pontos cegos no proprio motor.
Relatorios versionados em [`evals/reports/`](evals/reports/).

---

## Servidor MCP

O mesmo sistema por outro transporte. `python -m mcp_server` publica cinco ferramentas
para um cliente de LLM (Claude Desktop, um IDE, outro agente) descobrir e chamar.

| Ferramenta | Servico que ela adapta |
|---|---|
| `search_knowledge_base` | `RagService.query` |
| `list_documents` | `DocumentRepository` |
| `get_execution` | `ExecutionRepository` |
| `list_pending_approvals` | `ApprovalRepository` |
| `run_workflow` | `WorkflowService.run` |

**O servidor inteiro tem menos de 250 linhas e nenhuma regra de negocio** -- ele chama
exatamente o que `app/api/routes/` chama. REST e MCP sao adaptadores sobre uma camada de
servico que nunca soube que HTTP existia, e o V6 foi a cobranca dessa promessa, feita no
V1 e barata de afirmar ate aparecer o segundo transporte.

**A ferramenta que ele NAO tem e a decisao mais importante daqui.** Nao existe
`approve_action`: um cliente MCP e um modelo de linguagem, e dar a ele o poder de aprovar
seria a IA autorizando a propria acao. O servidor **mostra** a pendencia para o modelo
relatar a quem decide. Detalhes em [`docs/mcp.md`](docs/mcp.md).

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
| `create_all` e Alembic convivem | O primeiro cria banco descartavel; o segundo altera banco com dados de alguem. Um teste roda `alembic check` e falha se um modelo mudar sem a migracao |

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

## O que eu aprendi

As licoes que custaram mais caro, e o que cada uma mudou no codigo. Todas tem o contexto
completo em [`docs/engineering-decisions.md`](docs/engineering-decisions.md).

### O medidor tambem precisa ser medido

O conjunto de avaliacao foi construido para medir o sistema. O que ele mediu primeiro foi
um defeito no **proprio avaliador**: os cinco casos de recusa correta tiraram 0.29 enquanto
as assercoes deterministicas diziam "certo".

Investigando as notas do juiz, a causa apareceu: ele recebia a pergunta do usuario e
deslizava de *"o trecho diz isto?"* para *"isto responde bem ao usuario?"*. Uma nota chegou
a dizer "o trecho afirma que SMS foi descontinuado" junto de `supported: false` -- a
justificativa negando o veredito.

Corrigido, a variacao entre rodadas repetidas caiu de **0.182 para 0.000**. E isso derrubou
uma conclusao que eu ja havia registrado: o juiz nao "varia mesmo com temperatura zero". A
variacao era **ambiguidade da tarefa**. Tratar como ruido teria levado a aumentar a margem
do limite -- conviver com o defeito em vez de corrigi-lo.

### Um numero arbitrado precisa se declarar arbitrado

O corte de relevancia era 0.35 "por intuicao" desde o V2, e ficou marcado como pendencia
por tres versoes. Quando finalmente foi medido, a resposta nao foi um numero melhor:

```
respondeu corretamente ............. 0.477 a 0.767
recusou corretamente, com contexto . 0.526 e 0.552   ← dentro da faixa acima
```

Os grupos **se sobrepoem**. Nenhum corte os separa. O corte nunca foi o mecanismo de
honestidade -- e um filtro de custo, e quem garante honestidade e o contrato do agente. O
numero nao precisava de ajuste; precisava de entendimento.

### Instrucao em prompt e mais fraca que atalho em codigo

O prompt de `relevance` mandava, com todas as letras, tratar recusa honesta como
pertinente. O juiz ignorou. A correcao foi um atalho em Python que nem chama o modelo.

Vale a generalizacao: quando o comportamento **precisa** acontecer, ele nao pode depender
de o modelo obedecer.

### Testar que algo NAO existe

Tres testes deste projeto afirmam ausencias:

- nao ha rota para executar uma ferramenta direto (seria atalho para escrita sem
  aprovacao);
- nao ha ferramenta MCP para aprovar (seria a IA autorizando a propria acao);
- uma execucao sincrona nao dispara o callback (entregaria o resultado duas vezes).

Parece excentrico ate lembrar que "adicionar a funcionalidade que faltava" e a mudanca mais
natural do mundo -- e que nenhum outro teste quebraria.

### Anotacao de tipo nao e verificada em runtime

`Mapped[ExecutionStatus] = mapped_column(String(32))` declarava um enum e devolvia `str`
desde o V1. O `mypy` ficava limpo. O defeito so apareceu quando alguem chamou um **metodo**
do enum, meses depois: todo o codigo anterior so comparava com `==`, e `StrEnum` e
comparavel a texto.

Onde o dado atravessa uma fronteira -- banco, rede, arquivo -- alguem precisa fazer a
conversao. "Funciona hoje" pode significar apenas que ninguem usou o tipo como tipo ainda.

### Um passo de CI que nao pode passar e pior que passo nenhum

O plano era rodar o conjunto de avaliacao na CI com provider deterministico. Deu 0 de 16.
Corrigido o provider falso, foram 7 de 16 -- e os 9 restantes **jamais passariam**: um
substituto nao entende as perguntas, nao sabe quando recusar nem o que citar.

A saida foi separar "o fluxo roda" de "a resposta e boa". `run_evals.py --smoke` verifica
o encanamento, que e real e regride de verdade. A medicao de qualidade exige modelo de
verdade e roda a mao.

### A abstracao que existia por gosto virou plano de contingencia

`VectorStore` e um Protocol com duas implementacoes e teste de contrato -- escrito assim
por disciplina de projeto, sem necessidade concreta.

Ate o `docker scout` encontrar uma CVE critica de injecao de codigo no ChromaDB, sem versao
corrigida disponivel. Trocar o banco vetorial passou a ser uma variavel de ambiente, e nao
uma reescrita. A disciplina de ontem pagou o incidente de hoje.

### Retry automatico e para operacao idempotente

A camada de LLM tem retry com backoff. O notificador do Slack **nao tem**, e a
inconsistencia e deliberada: um webhook nao aceita chave de idempotencia, e um timeout de
leitura e indistinguivel de "chegou e a resposta se perdeu". Repetir publicaria duas vezes
o mesmo aviso num canal que a equipe ja leu.

Leitura repete de graca. Escrita ja aprovada por uma pessoa, nao.

---

## Documentacao

| Documento | O que traz |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | Seis diagramas: a forma do sistema, o grafo de agentes, a aprovacao atravessando um restart, o motor de qualidade |
| [`docs/engineering-decisions.md`](docs/engineering-decisions.md) | 100 decisoes com o contexto que as motivou e as alternativas descartadas |
| [`docs/roadmap.md`](docs/roadmap.md) | Estado, invariantes do projeto e pendencias conhecidas |
| [`docs/mcp.md`](docs/mcp.md) | O que e MCP, comparacao com REST, e a ferramenta que o servidor deliberadamente nao tem |
| [`docs/security.md`](docs/security.md) | Endurecimento da imagem e as CVEs conhecidas sem correcao |
| [`docs/portfolio.md`](docs/portfolio.md) | Roteiro de apresentacao e as perguntas mais provaveis |
| [`evals/reports/latest.md`](evals/reports/latest.md) | Ultima rodada do conjunto de avaliacao |

---

## Licenca

MIT
