# AI Operations Hub

Plataforma de automacao empresarial que recebe uma solicitacao em linguagem natural,
interpreta a intencao, consulta a base de conhecimento corporativa, decide quais acoes
executar e dispara automacoes -- registrando cada decisao tomada no caminho.

> **Status:** em construcao. Versao atual: `V1.1 - Fundacao`.

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
| `GET` | `/health` | Estado, versao, ambiente e uptime da aplicacao |

Novos endpoints entram a cada versao do roadmap.

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
