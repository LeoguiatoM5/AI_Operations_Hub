# Arquitetura

Os diagramas sao Mermaid dentro do markdown, e nao imagens. O motivo e o mesmo que fez os
prompts virarem arquivo: diagrama que vive em `.png` sai de sincronia com o codigo na
primeira mudanca e ninguem percebe. Este aparece no diff.

## 1. A forma do sistema

Dois transportes sobre uma camada de servico que nunca soube que HTTP existia.

```mermaid
flowchart TB
    subgraph transportes["Transportes — adaptadores, sem regra de negocio"]
        REST["API REST<br/><small>app/api/routes/</small>"]
        MCP["Servidor MCP<br/><small>mcp_server/</small>"]
        EVALS["run_evals.py<br/><small>modo offline</small>"]
    end

    subgraph servicos["Servicos — a regra de negocio vive aqui"]
        RAG["RagService"]
        WF["WorkflowService"]
        APPR["ApprovalService"]
        DOC["DocumentService"]
    end

    subgraph nucleo["Nucleo — tudo atras de um Protocol"]
        LLM["LLMProvider<br/>openai · fake"]
        EMB["EmbeddingProvider<br/>openai · fake"]
        VS["VectorStore<br/>chroma · memory"]
        NOTIF["Notifier<br/>slack · memory"]
        QUAL["QualityEngine<br/>5 dimensoes"]
        REPO["Repositories<br/>SQLAlchemy"]
    end

    REST --> RAG & WF & APPR & DOC
    MCP --> RAG & WF
    EVALS --> RAG & QUAL

    RAG --> LLM & EMB & VS & REPO
    WF --> LLM & QUAL & NOTIF & REPO
    APPR --> WF & REPO
    DOC --> EMB & VS & REPO
```

**O que este desenho garante.** Trocar de provedor de LLM, de banco vetorial ou de canal
de notificacao e trocar uma variavel de ambiente -- cada um tem um Protocol, ao menos duas
implementacoes e um teste de contrato rodando a mesma bateria contra todas.

**O que ele proibe.** Nenhuma seta sobe. Um servico que importasse `fastapi` derrubaria o
servidor MCP junto; foi por isso que o V6 custou menos de 250 linhas.

## 2. O grafo de agentes

O caminho e decidido pelo **plano**, e nao por condicionais no codigo. Cada losango e uma
funcao pura do estado para o nome do proximo no -- testavel em milissegundos, sem provider.

```mermaid
flowchart TD
    START([inicio]) --> ORCH[orchestrator<br/><small>interpreta e monta a fila</small>]

    ORCH --> R1{route_next}
    R1 -->|pesquisa na fila| RES[research]
    R1 -->|analise na fila| ANA[analysis]
    R1 -->|automacao na fila| AP[automation_plan<br/><small>escolhe a ferramenta</small>]
    R1 -->|fila vazia| REP
    R1 -->|sem plano| FIM([fim])

    RES --> R1
    ANA --> R1

    AP -->|aresta fixa| AR[automation_run]
    AR -.->|escrita: pausa| PAUSA{{"interrupt()<br/>waiting_approval"}}
    PAUSA -.->|humano aprova| AR
    AR --> R1

    REP[reporter] --> QUA[quality<br/><small>5 dimensoes</small>]
    QUA --> R2{route_after_quality}
    R2 -->|aprovado| FIM
    R2 -->|reprovado, com tentativa| REP
    R2 -->|tentativas esgotadas| FIM

    style PAUSA fill:#4a3520,stroke:#c98a3a,color:#f0e0c0
    style QUA fill:#1e3a4a,stroke:#4a90b8,color:#d0e8f5
```

Duas decisoes valem o destaque:

**A automacao ocupa dois nos.** A retomada de um `interrupt()` reexecuta o no **inteiro**.
Se decidir e executar vivessem juntos, cada aprovacao pagaria de novo a chamada de LLM --
e o modelo poderia escolher outra coisa, executando algo diferente do que a pessoa
aprovou. O checkpoint entre os dois congela a acao (ED-052).

**O unico ciclo do grafo e `reporter → quality → reporter`.** Corrigir a redacao e barato
perto de reexecutar pesquisa e analise, e a reprovacao quase sempre e da sintese. O limite
mora no roteador, e nao numa configuracao do LangGraph, para ser testavel como funcao pura
(ED-068).

## 3. Aprovacao humana atravessando um restart

O caso que o V4 existe para resolver: a decisao chega horas depois, e o processo que fez o
pedido nao existe mais.

```mermaid
sequenceDiagram
    autonumber
    participant N as n8n
    participant A as API
    participant G as Grafo
    participant D as Disco
    participant H as Pessoa

    N->>A: POST /agents/run
    A->>G: executa
    G->>G: planeja a acao de escrita
    G->>D: checkpoint (acao congelada)
    G-->>A: interrupt()
    A->>D: Approval(status=pending)
    A-->>N: 202 waiting_approval — nada executado

    Note over A,D: a aplicacao pode cair, subir de novo,<br/>fazer deploy. So o disco sobrevive.

    H->>A: POST /approvals/{id}/approve
    A->>D: grava a decisao e CONFIRMA
    Note right of A: commit antes de retomar: se a acao<br/>falhar, quem autorizou nao se perde
    A->>G: Command(resume=...) pelo thread_id
    G->>D: le o checkpoint
    G->>G: executa EXATAMENTE o que foi mostrado
    G-->>A: completed
    A->>N: callback com o resultado
```

O teste `tests/integration/test_approval_across_restart.py` derruba aplicacao, engine,
checkpointer e notificador entre os passos 6 e 8. A mensagem sai por um processo que nunca
viu a solicitacao original.

## 4. O motor de qualidade

Um motor, dois modos. O que os une e o `QualitySubject`: uma descricao pobre do que foi
produzido, sem banco, sem HTTP, sem LangGraph.

```mermaid
flowchart LR
    subgraph online["Modo online — antes de entregar"]
        EXEC[execucao real] --> SUBJ
    end
    subgraph offline["Modo offline — run_evals.py"]
        DATA[16 casos<br/>+ expectativas humanas] --> SUBJ
    end

    SUBJ[QualitySubject] --> ENG{QualityEngine}

    ENG --> G["grounding<br/><small>critica: reprova sozinha</small>"]
    ENG --> R["relevance"]
    ENG --> C["completeness"]
    ENG --> K["consistency"]
    ENG --> AR["api_reliability<br/><small>sem LLM, de graca</small>"]

    G & R & C & K & AR --> AGG[media ponderada<br/><small>inaplicavel sai da conta</small>]
    AGG --> V{passou?}
    V -->|sim| OK([entrega])
    V -->|nao| RETRY([reescrita dirigida])

    style G fill:#4a2020,stroke:#b85450,color:#f5d0d0
    style AR fill:#204a2a,stroke:#4ab86a,color:#d0f5da
```

**As notas nao sao pedidas ao modelo.** O juiz **classifica** -- cada afirmacao como
sustentada ou nao, cada item do pedido como coberto ou nao -- e a aritmetica e nossa. O
ganho e triplo: auditavel, reproduzivel e **testavel** sem rede.

**Dimensao inaplicavel sai da media, nao entra como zero.** Uma recusa correta nao tem
sobre o que ser pertinente; pontuar como zero puniria o comportamento certo.

## 5. Ingestao e recuperacao

```mermaid
flowchart LR
    UP[upload] --> EX[extrai texto<br/><small>txt · md · json · pdf</small>]
    EX --> HASH{hash ja existe?}
    HASH -->|sim| DUP([409 duplicado])
    HASH -->|nao| CH[divide em trechos]
    CH --> VEC[vetoriza]
    VEC --> IDX[(banco vetorial)]
    VEC --> META[(metadados)]

    Q[pergunta] --> QV[vetoriza]
    QV --> BUSCA[busca vizinhos]
    IDX -.-> BUSCA
    BUSCA --> CORTE{acima do corte?}
    CORTE -->|nao| REC([answered: false<br/>sem gastar LLM])
    CORTE -->|sim| AG[agente de pesquisa]
    AG --> RESP([resposta com fontes citadas])
```

**O corte de relevancia nao e o mecanismo de honestidade** -- e um filtro de custo. A
medicao mostrou que perguntas que devem ser recusadas alcancam similaridade **maior**
(0.552) que perguntas que devem ser respondidas (0.477): os grupos se sobrepoem, e nenhum
corte os separa. Quem garante honestidade e o contrato `answered=false` do agente, que le
os trechos e conclui que nao respondem (ED-079).

## 6. Estado da persistencia

```mermaid
erDiagram
    executions ||--o{ agent_executions : "cadeia de passos"
    executions ||--o{ approvals : "acoes pendentes"

    executions {
        str id PK
        str correlation_id
        str request_text
        enum status "pending·running·completed·failed·waiting_approval·needs_human_review"
        json result
        float quality_score
        float total_cost_usd
    }
    agent_executions {
        int sequence
        str agent
        str action
        enum status
        int attempts
        float cost_usd
        str error_code
    }
    approvals {
        str id PK
        str tool
        json arguments "congelados na criacao"
        enum status "pending·approved·rejected"
        str decided_by
        datetime decided_at
    }
    documents {
        str content_hash UK
        enum status
        str embedding_model "detecta base misturada"
    }
```

Duas tabelas para execucao, e nao uma: um pedido gera varias chamadas de LLM, e achatar
tudo em uma linha destruiria justamente o que da valor -- a cadeia de decisoes, com custo,
latencia e tentativas por etapa. E o que responde *por que a IA concluiu isso?*
