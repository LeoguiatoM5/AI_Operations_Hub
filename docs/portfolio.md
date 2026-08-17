# Material de apresentacao

Como falar deste projeto sem recitar a lista de tecnologias.

## Em uma frase

> Uma plataforma de agentes de IA em que **nenhuma acao irreversivel acontece sem uma
> pessoa autorizar** e **nenhuma resposta e entregue sem ser medida** -- com o mecanismo de
> medicao tendo sido corrigido pela propria medicao.

## Para o LinkedIn

**Versao curta:**

> **AI Operations Hub** — plataforma de automacao orientada a agentes, em Python.
> Workflow multiagente com LangGraph, RAG com fontes citadas, aprovacao humana obrigatoria
> para acoes de escrita (sobrevive a restart do servidor) e um motor de qualidade que
> avalia cada resposta em cinco dimensoes antes de entrega-la.
> 555 testes, 97% de cobertura, e 88 decisoes de engenharia documentadas com o contexto
> que as motivou.

**O paragrafo que costuma gerar pergunta:**

> O conjunto de avaliacao encontrou dois defeitos no proprio portao de qualidade na
> primeira rodada: recusas corretas eram punidas, e vereditos legitimos eram descartados
> por diferenca de texto. O segundo rejeitaria respostas corretas em producao. Nao se
> calibra um instrumento quebrado.

## Roteiro de apresentacao (15 minutos)

### 1. O problema (2 min)

Nao comece pelo stack. Comece pelo que da errado com agentes de IA em producao:

- eles **afirmam com confianca o que nao sabem**;
- eles **executam acoes irreversiveis** quando ninguem esperava;
- e nao ha como saber **por que** concluiram o que concluiram.

Este projeto e uma resposta a esses tres, e cada um tem um mecanismo proprio.

### 2. Nao inventar (3 min)

Mostre `POST /rag/query` com uma pergunta que a base **nao** cobre. `answered: false`.

O ponto que vale: **a chamada de LLM nem acontece**. Sem contexto acima do corte, chamar o
modelo seria convidar a alucinacao -- e a resposta pareceria tao boa quanto uma
fundamentada.

Se perguntarem "e se o corte deixar passar algo fraco?": aconteceu, esta medido. Perguntas
que devem ser recusadas alcancam similaridade **maior** (0.552) que perguntas que devem ser
respondidas (0.477). Os grupos se sobrepoem -- **nenhum corte os separa**. O corte e filtro
de custo; quem garante honestidade e o contrato do agente, que le os trechos e conclui que
nao respondem.

### 3. Nao agir sozinho (4 min) — **o melhor momento da demo**

`POST /agents/run` com "avise o time no canal X". A execucao para em `waiting_approval`.
Mostre que **nada foi enviado**.

Entao **reinicie o servidor**. Mostre a pendencia ainda la. Aprove. A mensagem sai.

O detalhe tecnico que impressiona quem conhece LangGraph: a automacao ocupa **dois nos**,
porque a retomada de um `interrupt()` reexecuta o no inteiro. Se decidir e executar
vivessem juntos, cada aprovacao pagaria a chamada de LLM de novo -- e o modelo poderia
escolher outra coisa, executando algo diferente do que a pessoa aprovou.

E o fecho: no servidor MCP **nao existe ferramenta de aprovar**. Um cliente MCP e um
modelo de linguagem; dar a ele esse poder seria a IA autorizando a propria acao.

### 4. Medir o que foi produzido (4 min)

`run_evals.py --judge`. Cinco dimensoes, notas por dimensao, custo de medir separado do
custo de produzir.

O que diferencia: **nao se pergunta uma nota ao modelo**. Ele classifica cada afirmacao
como sustentada ou nao; a aritmetica e nossa. Isso torna a nota auditavel, reproduzivel e
-- o que mais importa na pratica -- **testavel sem rede**.

E a historia que vale o tempo: na primeira rodada, os cinco casos de recusa correta tiraram
0.29. As assercoes diziam "certo" e as notas diziam "ruim". Investigando, o juiz recebia a
pergunta do usuario e deslizava de *"o trecho diz isto?"* para *"isto responde bem?"*.
Corrigido, a variacao entre rodadas caiu de 0.182 para **0.000**.

### 5. Fechar (2 min)

`docker compose up -d --wait`. E o `docs/engineering-decisions.md`: 88 decisoes com o
contexto que as motivou, incluindo as que se provaram **erradas** e foram reescritas.

## As cinco perguntas mais provaveis

**"Por que LangGraph e nao uma sequencia de chamadas?"**
Porque o caminho e decidido pelo plano, e nao por condicionais. O roteador e funcao pura do
estado para o proximo no -- da para percorrer todos os caminhos em milissegundos sem
provider nenhum. E o checkpointer e o que torna a aprovacao humana possivel: sem ele,
"aguardando aprovacao" seria so uma coluna no banco.

**"Como voce testa isso sem gastar com API?"**
Todo ponto de extensao e um Protocol com uma implementacao de teste. O provider falso tem
roteiro programavel -- da para escrever exatamente o que cada juiz responde e observar o
efeito no desfecho. A suite inteira roda sem rede e sem chave.

**"Qual foi a parte mais dificil?"**
Descobrir que o instrumento de medicao estava errado. O conjunto de avaliacao existia para
medir o sistema, e o que ele mediu primeiro foi um defeito no proprio avaliador. Corrigir
exigiu aceitar que a conclusao anterior -- "o juiz varia mesmo com temperatura zero" --
estava errada: a variacao era ambiguidade da tarefa, nao ruido.

**"O que voce faria diferente?"**
O conjunto de avaliacao nao tem exemplo negativo: 15 casos em 1.00 e um em 0.91. Sem
respostas degradadas de proposito, o limite de reprovacao continua sem justificativa
empirica. Esta declarado no roadmap, e nao escondido.

**"Isso esta pronto para producao?"**
Nao, e sei o que falta: nao ha autenticacao, o `decided_by` das aprovacoes e texto livre, o
SQLite precisa virar PostgreSQL com Alembic, e ha duas CVEs criticas sem correcao publicada
em dependencias -- documentadas em `docs/security.md`, com o que reduz a exposicao de cada
uma.

## Os numeros

| | |
|---|---|
| Testes | 555, 97% de cobertura |
| Decisoes documentadas | 88 |
| Endpoints REST | 14 |
| Ferramentas MCP | 5 |
| Custo de uma execucao completa | ~US$ 0,0009 |
| Custo do conjunto de avaliacao | ~US$ 0,008 |
| Agentes | 5 |
| Dimensoes de qualidade | 5 |

## O que evitar ao apresentar

**Nao liste tecnologias.** "FastAPI, LangGraph, ChromaDB, Pydantic" nao diz nada sobre
julgamento. Toda vaga tem candidatos com a mesma lista.

**Nao esconda o que falta.** Dizer "nao tem autenticacao, e aqui esta o motivo" gera mais
confianca que deixar o entrevistador descobrir.

**Nao apresente as decisoes como obvias.** O valor esta na alternativa descartada. "Usei
volume nomeado" e trivial; "usei volume nomeado porque bind mount do Windows quebra o
SQLite com um erro que nao acusa a causa" mostra que voce esteve la.
