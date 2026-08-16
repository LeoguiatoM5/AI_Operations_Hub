# AI Operations Hub

Plataforma de automacao empresarial que recebe uma solicitacao em linguagem natural,
interpreta a intencao, consulta a base de conhecimento corporativa, decide quais acoes
executar e dispara automacoes -- registrando cada decisao tomada no caminho.

> **Status:** em construcao. Versao atual: `V1.4 - MVP funcional`.

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
| **V1** | FastAPI + camada multi-LLM + 1 agente + persistencia + observabilidade | em andamento |
| V2 | RAG com ChromaDB, upload e ingestao de documentos, respostas com fontes | planejado |
| V3 | LangGraph com multiplos agentes e roteamento por intencao | planejado |
| V4 | Integracoes (n8n, Google Sheets, Notion, Slack) e human-in-the-loop | planejado |
| V5 | AI Quality Gateway e AI Evals | planejado |
| V6 | Servidor MCP | planejado |
| V7 | Docker, CI/CD e observabilidade completa | planejado |

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
| `GET` | `/health` | Estado, versao, ambiente, uptime e provedor de LLM em uso |
| `POST` | `/chat` | Processa uma solicitacao em linguagem natural e devolve a execucao completa |
| `GET` | `/executions` | Lista execucoes com paginacao e filtro por status |
| `GET` | `/executions/{id}` | Detalha uma execucao com toda a cadeia de agentes |

Novos endpoints entram a cada versao do roadmap.

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
escrita visivel para terceiros**. No V4, esse sinal e o que colocara a execucao em
`waiting_approval` antes de qualquer coisa ser enviada.

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

## Persistencia e rastreabilidade

Cada pedido produz uma linha em `executions` e uma linha em `agent_executions` **por
passo de agente**. E essa cadeia que permite responder "por que a IA concluiu isso?" --
com custo, latencia, tentativas e erro de cada etapa.

```
EXECUCAO  9e164013748d4748a5e843b6d9fca3b5
  status: completed   duracao: 18.1 ms   tokens: 4078   custo: US$ 0.00108330
  quality score: 87.5
------------------------------------------------------------------------------
  1. [ok   ] orchestrator  plan                 1420.5 ms   306 tok  tentativas=1
  2. [ok   ] research      rag_query            2890.1 ms  2152 tok  tentativas=2
  3. [FALHA] automation    create_notion_page  30000.0 ms     0 tok  tentativas=3
       erro: integration_timeout — A API do Notion nao respondeu em 30s.
  4. [ok   ] reporter      write_report         4120.7 ms  1620 tok  tentativas=1
```

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
