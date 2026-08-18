# Decisoes de engenharia

Registro das decisoes arquiteturais do projeto, com o contexto que as motivou e as
alternativas descartadas. Escrito durante o desenvolvimento, nao reconstruido depois.

---

## ED-001 — Factory de aplicacao em vez de objeto global

**Contexto.** O padrao mais comum em tutoriais de FastAPI e criar `app = FastAPI()` no
topo do modulo, executado na importacao.

**Decisao.** A aplicacao e criada por `create_app(settings)`.

**Motivo.** Cada teste constroi uma instancia limpa, com configuracao propria, sem
estado vazando entre casos. Um objeto montado na importacao obriga a manipular
variaveis de ambiente antes do `import`, o que torna a suite fragil e dependente de
ordem de execucao.

---

## ED-002 — Nenhuma rota alcanca um objeto global

**Contexto.** Na primeira versao, `create_app(settings)` recebia a configuracao, mas o
endpoint `/health` lia o singleton `get_settings()`. O parametro era decorativo.

**Como foi descoberto.** Um teste injetou `app_env="test"` e a API respondeu `"local"`.
Em producao isso apareceria como "mudei a variavel de ambiente e a aplicacao ignorou".

**Decisao.** A configuracao vive em `app.state` e chega as rotas por `Depends`
(`app/api/deps.py`). O mesmo mecanismo passara a servir a sessao de banco, o provider
de LLM e o cliente do banco vetorial.

**Licao.** Uma assinatura que aceita uma dependencia mas nao a usa e pior que nao
aceitar: ela promete um controle que nao existe.

---

## ED-003 — structlog integrado ao logging padrao

**Contexto.** Uvicorn e as bibliotecas de terceiros logam pelo `logging` da stdlib.
Configurar o structlog isoladamente produziria duas metades de saida em formatos
diferentes, quebrando qualquer agregador.

**Decisao.** `ProcessorFormatter` com `foreign_pre_chain` compartilhada, e handlers do
uvicorn removidos em favor do handler do root logger.

**Efeito colateral desejado.** Eventos de bibliotecas herdam o contexto do request. O
log de acesso do uvicorn e o log HTTP do SDK da OpenAI aparecem com `correlation_id`
sem que nenhuma delas tenha sido instrumentada.

---

## ED-004 — Cores condicionadas ao terminal

**Contexto.** `ConsoleRenderer(colors=True)` era incondicional.

**Como foi descoberto.** Executando com a saida redirecionada para arquivo. No Windows
o colorama removeu os codigos por acaso; em Linux ou dentro de um container, sequencias
ANSI iriam parar no meio do log.

**Decisao.** `colors=sys.stdout.isatty()`.

**Licao.** Teste unitario nao pegaria isso. So aparece executando de verdade, em um
ambiente diferente do de desenvolvimento.

---

## ED-005 — Configuracao validada na inicializacao

**Decisao.** `pydantic-settings` com tipos `Literal` e restricoes de faixa.

**Motivo.** `LOG_LEVEL=INFOO` derruba o processo no segundo zero, com mensagem
apontando o campo e os valores aceitos. Em container, a instancia mal configurada nunca
entra no balanceador. A alternativa (`os.getenv`) subiria o servidor e falharia em um
request aleatorio, horas depois, longe da causa.

---

## ED-006 — Protocol em vez de classe base abstrata

**Decisao.** `LLMProvider` e um `typing.Protocol`.

**Motivo.** Structural typing: um provider satisfaz o contrato pela forma, sem herdar de
nada. Isso evita acoplar implementacoes a uma hierarquia e permite que um objeto de
teste sirva sem importar o modulo base. O mypy verifica a conformidade estaticamente.

**Alternativa descartada.** `abc.ABC` — funciona, mas obriga cada provider a conhecer e
herdar da nossa classe.

---

## ED-007 — Provider `fake` como padrao da aplicacao

**Decisao.** `LLM_PROVIDER=fake` e o valor padrao, nao `openai`.

**Motivo.** Tres beneficios concretos:

1. Quem clona o repositorio roda o projeto sem possuir credencial alguma.
2. O pipeline de CI executa a suite completa sem segredos configurados.
3. Cenarios que a API real nao produz sob encomenda -- timeout, 429, JSON quebrado,
   resposta vazia -- viram um roteiro de tres linhas.

O provider falso e deterministico: a mesma entrada gera sempre a mesma saida, o que
torna testes reproduziveis e avaliacoes comparaveis entre execucoes.

---

## ED-008 — Retry proprio, com o retry do SDK desligado

**Contexto.** O SDK da OpenAI repete requisicoes automaticamente e em silencio.

**Decisao.** `max_retries=0` no cliente do SDK; a repeticao e feita por
`RetryingLLMProvider`.

**Motivo.** Com o retry do SDK ligado, `latency_ms` incluiria tentativas invisiveis e o
campo `attempts` mentiria. Observabilidade so vale se medir o que realmente aconteceu.

**Alternativa descartada.** `tenacity` — e a escolha certa em producao. Aqui, quarenta
linhas proprias mantem o predicado de retry e a estrategia de jitter explicitos e
testaveis, sem uma dependencia a mais.

---

## ED-009 — Retry como decorador, nao como responsabilidade do provider

**Decisao.** `RetryingLLMProvider` implementa o mesmo Protocol que decora.

**Motivo.** Escrever a politica dentro de cada provider a duplicaria em OpenAI,
Anthropic e Gemini, com tres chances de divergir. Como decorador, ela e testada uma vez,
isoladamente, contra o provider falso.

---

## ED-010 — Nem toda falha merece nova tentativa

**Decisao.** Cada erro de LLM carrega `retryable`. Timeout e limite de cota repetem;
credencial invalida falha na primeira tentativa.

**Motivo.** Repetir um 401 tres vezes so atrasa o erro, gasta cota e polui o log. A
distincao entre falha transitoria e permanente e o que separa uma politica de retry util
de um laco teimoso.

---

## ED-011 — Full jitter no backoff exponencial

**Decisao.** O intervalo e sorteado em `[0, delay]`, e nao fixado em `delay`.

**Motivo.** Se dez execucoes falharem no mesmo instante por um 429, um backoff
deterministico faria as dez tentarem de novo simultaneamente, reproduzindo o pico que
causou a falha. O jitter espalha as tentativas no tempo.

---

## ED-012 — Custo resolvido por prefixo do nome do modelo

**Contexto.** A tabela de precos indexava pelo alias (`gpt-4o-mini`).

**Como foi descoberto.** Na primeira chamada real a API. O provedor responde com a
versao fixada -- `gpt-4o-mini-2024-07-18` -- que nao batia com nenhuma chave. Todo custo
saia como `US$ 0.00000000`.

**Decisao.** `resolve_pricing` tenta a chave exata e, na falta dela, o prefixo conhecido
mais longo. O comprimento importa: `gpt-4o-mini-2024-07-18` comeca tanto com `gpt-4o`
quanto com `gpt-4o-mini`, e escolher o primeiro superestimaria o custo em ate 16 vezes.

**Licao.** O aviso `pricing_unknown_model` foi o que tornou o bug visivel. Retornar zero
em silencio teria produzido meses de relatorios com custo zerado.

---

## ED-013 — Teto de major version nas dependencias

**Contexto.** O `pyproject.toml` declarava `openai>=1.55`. O pip resolveu para `3.1.0`
-- dois saltos de major. Funcionou por coincidencia de compatibilidade.

**Decisao.** Todas as dependencias diretas passaram a ter piso e teto
(`openai>=3.1,<4`).

**Motivo.** Sem teto, `pip install` daqui a alguns meses pode trazer uma versao com
mudancas incompatives e quebrar o projeto sem que uma linha de codigo tenha mudado. Em
um repositorio de portfolio, isso significa um recrutador clonando e encontrando erro.

---

## ED-034 — Consistencia entre dois sistemas sem transacao comum

**Contexto.** A ingestao escreve no banco relacional (metadados) e no banco vetorial
(trechos). Nao existe `ROLLBACK` que desfaca os dois juntos.

**Risco concreto.** Um processo interrompido entre as duas escritas deixaria um
documento registrado como existente e sem nenhum vetor indexado. A busca simplesmente
nao o encontraria -- para sempre, sem erro algum. Falha silenciosa e a pior categoria.

**Decisao.** Estado explicito e ordem deliberada:

```
pending -> processing -> grava vetores -> indexed
```

`processing` marca o ponto a partir do qual ha intencao declarada de indexar. Um
documento parado nesse estado e **evidencia de processo interrompido**, nao misterio.

Na falha, os vetores parciais sao removidos antes de o documento ser marcado como
`failed` -- sem isso, sobrariam trechos orfaos aparecendo em buscas e apontando para um
documento que o sistema considera invalido.

Na remocao a ordem se inverte: vetores primeiro, metadados depois. Um trecho indexado
sem documento correspondente apareceria na busca sem origem identificavel.

---

## ED-035 — Deduplicacao por hash do conteudo

**Decisao.** `content_hash` (SHA-256) e coluna unica. Reenviar o mesmo arquivo devolve
`409`, com o id do documento original.

**Motivo.** Reindexar o mesmo conteudo duplicaria trechos no indice. O efeito nao seria
um erro visivel, e sim uma degradacao gradual: as mesmas passagens ocupando varias
posicoes do top-k, expulsando resultados diversos. A busca "continua funcionando",
piorando.

**Detalhe.** A comparacao e por conteudo, nao por nome: dois envios do mesmo arquivo com
nomes diferentes sao o mesmo documento, e dois arquivos de mesmo nome com conteudo
diferente nao sao.

---

## ED-036 — Documento sem texto e erro, nao sucesso

**Decisao.** Um arquivo que nao produz texto extraivel e rejeitado com `422`, em vez de
ser indexado vazio.

**Motivo.** O caso tipico e o PDF escaneado -- imagem de pagina, sem camada de texto.
Indexado em silencio, ele constaria da base, nunca apareceria em busca alguma, e o
usuario nao teria como descobrir por que. A mensagem de erro diz explicitamente que OCR
seria necessario e que o sistema nao faz OCR.

---

## ED-037 — Ordem das codificacoes de texto

**Contexto.** `TEXT_ENCODINGS` tentava `utf-8` antes de `utf-8-sig`.

**Como foi descoberto.** Na demonstracao de ingestao: um `.json` gravado por
`Set-Content -Encoding utf8` do PowerShell foi rejeitado com "Unexpected UTF-8 BOM".

**Causa.** Decodificar um arquivo com BOM usando `utf-8` puro **nao levanta excecao**:
devolve a string com um U+FEFF invisivel no inicio. Sem excecao, nenhuma codificacao
seguinte era tentada, e o BOM seguia adiante -- quebrando o parse de JSON e sujando o
primeiro trecho indexado de qualquer arquivo de texto.

**Decisao.** `utf-8-sig` vem primeiro (remove o BOM quando existe, comporta-se como
`utf-8` quando nao existe), e o resultado ainda passa por um `lstrip` defensivo.

**Por que importa.** Bloco de Notas, Excel e PowerShell gravam BOM por padrao. Isso
atinge boa parte dos arquivos que um usuario corporativo envia -- exatamente o publico
deste sistema.

**Licao.** Uma cadeia de fallbacks so funciona se cada etapa falhar de forma detectavel.
`utf-8` "funcionava" com BOM, e por isso o fallback correto nunca era alcancado.

---

## ED-038 — O corte de relevancia pertence ao modelo de embedding

**Contexto.** `RAG_MIN_SCORE` era um valor unico na configuracao.

**Por que estava errado.** A escala de similaridade depende do modelo. Medido nesta
base: o melhor acerto do vetorizador lexical fica em 0.25; o do `text-embedding-3-small`
passa de 0.76. Um corte de 0.15 rejeitaria quase tudo no primeiro e nada no segundo.

**Decisao.** O provedor declara `min_relevant_score`. A configuracao vira sobrescrita
opcional (`None` por padrao).

**Motivo.** O conhecimento fica junto de quem o tem. Trocar de modelo passa a trazer o
corte adequado junto, em vez de exigir que alguem lembre de ajustar outra variavel.

**Reconhecimento honesto.** Os valores atuais (0.05 e 0.35) sao pontos de partida
derivados de poucas medicoes. O valor correto sai do conjunto de avaliacao (V5).

---

## ED-039 — Citacao inventada e erro de validacao

**Decisao.** O schema de resposta do agente de pesquisa e construido **dinamicamente a
cada consulta**, com as citacoes restritas a `[1, n]`, onde `n` e o numero de trechos
efetivamente recuperados.

**Motivo.** Se o modelo cita `[7]` quando existem quatro trechos, ele esta inventando a
origem da informacao. Com o intervalo declarado no schema, isso vira um erro do Pydantic
e o retry dirigido pede a correcao -- **o mesmo mecanismo que ja garantia formato passa
a garantir ancoragem**.

A alternativa seria filtrar citacoes invalidas depois. Isso esconderia o problema: a
resposta continuaria sendo entregue, sem a fonte que a sustentava.

**Complemento.** Um validador de coerencia recusa `answered=true` sem nenhuma citacao --
afirmar que respondeu sem dizer de onde e, por definicao, resposta nao ancorada.

---

## ED-040 — Sem contexto, o LLM nao e chamado

**Decisao.** Quando nenhum trecho passa do corte de relevancia, a resposta e montada sem
consultar o modelo.

**Motivo.** Chamar o LLM sem contexto e convidar a alucinacao: ele responde com
conhecimento proprio, e a resposta parece tao fundamentada quanto uma real. Nao chamar
economiza a chamada **e** elimina a categoria de erro.

**Verificado em execucao real.** Pergunta sobre ferias numa base sem esse assunto: o
retriever devolveu um trecho fraco (0.3525, logo acima do corte de 0.35) do documento
errado, e o agente respondeu `answered: false` com zero citacoes. Duas camadas de defesa,
a segunda cobrindo a imprecisao da primeira.

---

## ED-041 — Base com modelos de embedding misturados e recusada

**Decisao.** `/rag/query` devolve `409 embedding_model_mismatch` quando ha documentos
indexados com um modelo diferente do atual.

**Motivo.** Vetores de modelos diferentes ocupam espacos diferentes. Compara-los produz
similaridades sem significado -- e **o sistema nao teria como perceber**: os numeros
continuam entre 0 e 1, a busca continua devolvendo resultados, e eles sao aleatorios.

O erro lista os modelos presentes no indice e o que fazer a respeito. E o uso concreto
da coluna `embedding_model` gravada em cada documento (ED-015).

---

## ED-042 — Nome de colecao validado na configuracao

**Contexto.** O Chroma exige nomes de colecao com 3 a 512 caracteres de `[a-zA-Z0-9._-]`,
comecando e terminando em alfanumerico.

**Como foi descoberto.** Uma colecao chamada `p1` numa demonstracao produziu um erro
vindo das entranhas da biblioteca, na primeira indexacao.

**Decisao.** O padrao entra como `pattern` no campo de configuracao.

**Motivo.** Mesmo principio de ED-005: restricao conhecida vira validacao de startup. A
aplicacao recusa subir com um nome invalido, em vez de aceitar a configuracao e quebrar
quando alguem enviar o primeiro documento.

---

## ED-043 — LangGraph, e os tres recursos que o justificam

**Decisao.** O workflow multiagente roda sobre `StateGraph`, nao sobre um laco com
condicionais.

**Motivo.** A dependencia so se paga se os recursos exclusivos forem usados. Sao tres, e
os tres estao em uso:

1. **Reducers no estado.** `Annotated[list, operator.add]` declara a acumulacao no tipo.
   Com o reducer padrao (substituicao), o segundo no apagaria o registro do primeiro.
2. **`conditional_edges`.** O caminho e decidido por uma funcao pura do estado, nao por
   `if` dentro de um no (ED-044).
3. **Checkpointer.** O estado sobrevive a execucao e permite retomada (ED-047).

Se fosse executor linear, um `for` sobre uma lista faria o mesmo com menos dependencia --
e a critica seria justa.

---

## ED-044 — O caminho e dado, nao codigo

**Decisao.** `route_next(state) -> str` e funcao pura: sem LLM, sem banco, sem efeito
colateral. O plano do orquestrador preenche uma fila; o roteador consome dela.

**Beneficio medido.** Todos os caminhos do grafo -- inclusive falha fatal, agente
desconhecido e fila vazia -- sao testados em milissegundos, sem provider nenhum. Com o
roteamento dentro dos nos, cada caminho exigiria executar agentes.

**Verificado em execucao real:** o plano devolveu `['analysis', 'research', 'reporter']`
e o grafo percorreu `orchestrator -> analysis -> research -> reporter`. A ordem veio do
modelo, nao do codigo.

---

## ED-045 — Falha de agente degrada, nao aborta

**Decisao.** Um agente que falha registra o erro em `errors` e o grafo continua. Somente
o orquestrador e fatal -- sem plano nao ha o que executar.

**Motivo.** Um relatorio que declara "a analise falhou por timeout" vale mais que um
`502` com nada aproveitado: o custo dos agentes que ja rodaram foi pago de qualquer
forma, e a informacao parcial ainda serve para decidir.

**Consequencia no contrato.** `/agents/run` termina com `201` e `status: completed`
mesmo com falhas parciais -- diferente de `/chat` (ED-024), onde a falha significa que
nada foi produzido. A diferenca e deliberada e esta documentada na rota.

**Guarda contra relatorio vazio e confiante.** O `Report` recusa validacao se nao houver
pontos-chave, recomendacoes **nem** limitacoes declaradas. Se tudo falhou, quem le
precisa saber o que falhou.

---

## ED-046 — Grafo compilado por requisicao

**Decisao.** `build_graph` roda a cada execucao, com nos que carregam a sessao de banco
e a execucao em curso.

**Motivo.** Compilar e barato -- monta a estrutura, nao executa nada. O custo se paga em
seguranca: nenhum estado de requisicao vive em objeto compartilhado entre threads, que e
a origem classica de vazamento de dados entre usuarios em servidores async.

---

## ED-047 — Substituto em teste que nunca encontra o real esconde defeito

**Contexto.** Toda a suite injeta `MemorySaver`, o que e correto: testes nao devem tocar
disco. A consequencia era que o caminho de producao -- `AsyncSqliteSaver` criado no
lifespan -- nunca era exercitado.

**O que estava quebrado.** Duas incompatibilidades encadeadas, ambas silenciosas na
instalacao:

1. `langgraph-checkpoint-sqlite` 2.x chama `is_alive()` no `aiosqlite`, metodo removido
   na versao 0.22 junto com a heranca de `threading.Thread`.
2. A mesma 2.x chama `dumps()` no serializador, metodo que o `langgraph-checkpoint` 4.x
   nao expoe mais.

**A aplicacao nao subia.** Nenhum teste falhava.

**Causa raiz do meu lado.** O teto `langgraph-checkpoint-sqlite>=2,<3` foi escrito
seguindo o ED-013, mas com o valor errado: a versao compativel com o ecossistema atual e
a 3.1. Teto de major protege de quebra futura; nao protege de escolher o major errado
hoje.

**Decisao.** Piso em `>=3.1`, e um arquivo de teste dedicado que constroi o checkpointer
**real**, roda um grafo por ele e recupera o estado com uma segunda instancia -- provando
persistencia entre processos, que e a base do human-in-the-loop do V4.

---

## ED-014 — Segredos como `SecretStr`

**Decisao.** `openai_api_key: SecretStr | None`.

**Motivo.** Vazamento de credencial raramente e deliberado. Acontece em um log de debug
que despeja a configuracao inteira, em um traceback enviado a uma ferramenta de
monitoramento, ou em uma mensagem colada num ticket. `SecretStr` fecha os tres caminhos:
`repr()`, `str()` e `model_dump()` mostram `**********`. Ha testes garantindo que
continue assim.

---

## ED-015 — Duas tabelas: `Execution` e `AgentExecution`

**Decisao.** O pedido do usuario e uma linha; cada passo de agente dentro dele e outra.

**Motivo.** Um pedido gera varias chamadas de LLM. Achatar tudo em uma linha destruiria
exatamente o que da valor ao projeto: a cadeia de decisoes, com custo, latencia,
tentativas e erro por etapa. E essa cadeia que responde "por que a IA concluiu isso?".

---

## ED-016 — SQLAlchemy assincrono desde o inicio

**Decisao.** `create_async_engine` com `aiosqlite`, nao o engine sincrono.

**Motivo.** A API e assincrona. Um driver bloqueante travaria o event loop a cada
consulta, anulando a concorrencia conquistada na camada de LLM. Trocar para PostgreSQL
significa trocar `aiosqlite` por `asyncpg` na URL -- o codigo nao muda.

---

## ED-017 — Tipo proprio para data e hora em UTC

**Decisao.** `UtcDateTime`, um `TypeDecorator` que converte para UTC na escrita e
reanexa o fuso na leitura.

**Motivo.** O SQLite nao armazena fuso horario. Sem tratamento, grava-se um `datetime`
com timezone e le-se um ingenuo -- e a subtracao entre os dois levanta `TypeError`, ou
pior, produz uma duracao silenciosamente errada. Gravar sem fuso e recusado com erro
explicito: um instante ambiguo no banco custa mais caro que uma excecao no teste.

---

## ED-018 — Nenhum carregamento implicito de relacao

**Contexto.** A primeira versao numerava os passos com
`len(execution.agent_executions) + 1`.

**Como foi descoberto.** Cinco testes falharam com `MissingGreenlet`. Ler uma relacao
nao carregada dispara uma consulta ao banco dentro de um acesso a atributo -- I/O
escondido em um `len()`, sem `await` a vista. O SQLAlchemy assincrono recusa isso por
principio, e faz bem.

**Decisao.** A numeracao vem de uma consulta explicita (`MAX(sequence) + 1`), e o passo
e inserido com `session.add`, nunca por `append` na colecao. Leitura da cadeia acontece
por `list_steps()` ou por `get()`, que carrega os passos com `lazy="selectin"`.

**Licao.** Em codigo assincrono, toda ida ao banco precisa ser visivel no ponto de
chamada. Conveniencia de ORM que esconde I/O e uma armadilha de performance em codigo
sincrono e um erro em tempo de execucao em codigo assincrono.

---

## ED-019 — Agregados calculados na escrita

**Decisao.** `total_tokens` e `total_cost_usd` sao atualizados quando um passo e
gravado, em vez de somados a cada leitura.

**Motivo.** Listar cem execucoes nao deve exigir varrer todos os passos de cada uma.
Sao dados derivados e recalculaveis -- a duplicacao e deliberada, em troca de leitura
barata.

---

## ED-020 — `create_all` agora, Alembic junto com o PostgreSQL

**Decisao.** O schema e criado por `Base.metadata.create_all` no startup.

**Motivo.** Migracao versionada resolve o problema de evoluir um banco com dados que
nao podem ser perdidos. Enquanto o banco e um SQLite descartavel, Alembic e cerimonia
sem beneficio. Ele entra no V7, junto com o PostgreSQL, que e quando o problema passa
a existir de verdade.

---

## ED-021 — Convencao de nomes para constraints

**Decisao.** `MetaData(naming_convention=...)` definida na classe base.

**Motivo.** Sem ela, o banco gera nomes automaticos e inconsistentes entre dialetos, e
o Alembic nao consegue identificar uma constraint existente para altera-la ou remove-la.
Custa cinco linhas agora; descobrir a falta depois de ter migrations rodando custa muito
mais.

---

## ED-022 — Pydantic como fronteira da saida do LLM

**Decisao.** Nenhum dado produzido por um modelo circula como dicionario solto. O texto
devolvido pelo LLM ou vira uma instancia validada, ou vira erro registrado.

**Motivo.** O LLM e uma fonte nao confiavel. Ele responde JSON quebrado, JSON valido com
campo faltando, JSON valido com valor fora do enum, e JSON correto embrulhado em cercas
de markdown -- todos observados em teste. Tratar a saida como dado confiavel transfere a
falha para tres camadas adiante, onde ela aparece como `KeyError` sem contexto.

---

## ED-023 — Retry dirigido no parse

**Decisao.** Quando a validacao falha, o erro do Pydantic e reenviado ao modelo em uma
nova tentativa, junto com a instrucao de corrigir.

**Motivo.** Repetir o mesmo pedido tem chance baixa de mudar o resultado; dizer
exatamente qual campo quebrou e por que tem chance alta. E o mesmo mecanismo que o AI
Quality Gateway usara no V5, em escala maior.

**Contrapartida.** Cada reparo e uma chamada paga a mais. Por isso o custo das tentativas
e somado em `_merge_usage`: reportar apenas a ultima escondeira metade do gasto real.

---

## ED-024 — Falha de LLM devolve status HTTP honesto

**Decisao.** Timeout vira `504`, cota estourada vira `429`, formato invalido vira `502`.
Nao existe `200` com `status: "failed"` no corpo.

**Motivo.** Monitoramento, balanceador e cliente de API decidem por codigo HTTP. Esconder
falha atras de `200` faz um sistema quebrado parecer saudavel em qualquer painel.

**Contrapartida resolvida.** Para nao perder rastreabilidade, o `execution_id` acompanha
o erro em `error.details` -- a execucao fica gravada como `failed` e pode ser inspecionada
em `GET /executions/{id}`.

---

## ED-025 — Auditoria de falha em transacao propria

**Contexto.** O servico gravava a execucao como `failed` e propagava a excecao.

**Como foi descoberto.** O teste `test_failure_is_recorded_and_traceable` recebeu `404`
ao buscar a execucao. A excecao disparava o rollback da sessao do request, que apagava
justamente o registro de auditoria recem-criado.

**Decisao.** O registro da falha e confirmado (`commit`) antes de a excecao subir.

**Licao.** Auditoria de uma falha nao pode compartilhar transacao com a operacao que
falhou. Se compartilhar, o unico rastro do problema desaparece exatamente quando o
problema acontece.

---

## ED-026 — Coverage com `concurrency = greenlet`

**Contexto.** `execution_service.py` reportava 50% de cobertura, embora os testes de API
exercitassem o arquivo inteiro.

**Como foi descoberto.** Estranhamento com o numero: 16 de 32 linhas nao cobertas em um
arquivo com teste de ponta a ponta.

**Causa.** O SQLAlchemy assincrono executa o ORM dentro de greenlets. Sem
`concurrency = ["greenlet", "thread"]`, o `coverage` perde o rastreamento nesses trechos.

**Resultado.** O arquivo passou a reportar 100% sem que uma linha de teste fosse escrita.

**Licao.** Metrica errada leva a trabalho errado. Sem investigar, o caminho natural seria
escrever testes redundantes para "cobrir" codigo que ja estava coberto.

---

## ED-027 — O OpenAPI precisa concordar com o runtime

**Contexto.** Os tratadores em `app/api/errors.py` garantem que toda falha saia no
envelope `ErrorResponse`. Isso e comportamento de runtime.

**Como foi descoberto.** Inspecionando o Swagger: `/chat` documentava o `422` no nosso
envelope, mas `/executions` documentava `{"detail": [...]}` -- o formato padrao do
FastAPI. O runtime devolvia o envelope; a especificacao mentia.

**Por que importa.** Quem gera um cliente a partir do OpenAPI produz codigo que trata o
erro no formato errado. A falha nao aparece em teste nenhum do lado do servidor: ela
aparece no consumidor, em producao.

**Decisao.** `app/api/responses.py` centraliza as respostas de erro documentadas, e um
teste de contrato varre a especificacao inteira verificando que todo status de erro
aponta para `ErrorResponse`.

---

## ED-028 — Pydantic valida coerencia, nao apenas tipos

**Contexto.** Um teste manual com a frase vaga "pede envio de e-mail." produziu
`requires_approval: true` junto com `suggested_agents: []`.

**Por que e um defeito.** A classificacao e internamente contraditoria: se a solicitacao
exige aprovacao, ela envolve escrita em sistema externo -- e escrita, nesta arquitetura,
so passa pelo agente `automation`. Um plano sem esse agente jamais executaria o que foi
pedido. O JSON era valido; o **significado** nao era.

**Decisao.** `TriageResult` ganhou um `model_validator` que rejeita a combinacao. O
retry dirigido entao corrige semantica, e nao apenas sintaxe.

**Confirmado em execucao real:** a OpenAI reproduziu o mesmo erro, recebeu a explicacao
e corrigiu na segunda tentativa (`repairs=1`).

**Contrapartida.** O reparo dobrou o consumo (913 para 1977 tokens). Regra de coerencia
mal calibrada vira imposto fixo sobre toda execucao -- razao a mais para o custo por
tentativa ser medido.

---

## ED-029 — Prompt nao se ajusta sem medicao

**Contexto.** No mesmo teste, o modelo respondeu `confidence: 0.8` para uma solicitacao
de quatro palavras, embora o prompt peca explicitamente confianca baixa para pedidos
vagos.

**Decisao.** Nao corrigir o prompt agora.

**Motivo.** Sem um conjunto de avaliacao, "melhorar o prompt" e chute: nao ha como saber
se a mudanca corrigiu o caso observado sem estragar dez casos que funcionavam. Prompt e
codigo cujo comportamento so e verificavel estatisticamente.

O caso fica registrado como entrada do `evaluation_dataset.json` (V5). Depois de existir
a medicao, o ajuste vira experimento com antes e depois -- nao opiniao.

---

## ED-030 — Embedding e banco vetorial sao abstracoes separadas

**Decisao.** `EmbeddingProvider` e `VectorStore` sao Protocols independentes.

**Motivo.** Bancos vetoriais oferecem embedding embutido -- e conveniente e amarra as
duas decisoes numa so. Separando, troca-se o modelo de embedding sem trocar o banco, e o
banco sem trocar o modelo. Sao eixos de evolucao diferentes: o modelo muda por qualidade
e custo; o banco muda por escala e operacao.

**Consequencia pratica.** O Chroma e configurado SEM funcao de embedding propria. Por
padrao ele baixaria um modelo ONNX de dezenas de megabytes na primeira execucao; passando
os vetores prontos, ele vira apenas o indice.

---

## ED-031 — Embeddings falsos com similaridade lexical real

**Decisao.** `FakeEmbeddingProvider` nao gera vetores aleatorios: e um vetorizador por
hashing, em que cada palavra de conteudo ocupa sempre a mesma posicao.

**Motivo.** Vetores aleatorios testariam encanamento -- "chamou o banco, recebeu uma
lista". Com similaridade lexical de verdade, buscar "politica de reembolso" recupera o
trecho sobre reembolso, e os testes passam a verificar **comportamento de recuperacao**
sem rede e sem custo.

**Detalhes que importam:**

- Hash via `blake2b`, nunca `hash()`. O hash de string em Python e aleatorizado por
  processo: usa-lo tornaria um indice gravado hoje incompativel com a busca de amanha.
- Presenca binaria de palavras, nao contagem. Descoberto por teste que falhou: com
  contagem bruta, um documento com tres ocorrencias de "de" ficou mais proximo da
  pergunta que o documento que tratava do assunto perguntado.
- Palavras com menos de tres letras sao descartadas -- em portugues sao majoritariamente
  conectivos. E a versao mais simples do que o IDF faz num vetorizador real.

**Limite reconhecido.** Nao captura sinonimia. Medido: para "quanto tempo tenho para
pedir de volta um valor que gastei?", o provedor falso erra o documento e o
`text-embedding-3-small` acerta com folga (0.587 contra 0.381 do segundo colocado).

---

## ED-032 — Distancia vira similaridade na fronteira

**Decisao.** `SearchHit.score` e sempre similaridade: maior e melhor, faixa [0, 1].

**Motivo.** O Chroma devolve **distancia** -- menor e melhor, faixa [0, 2]. Deixar essa
convencao vazar para cima e o caminho mais curto para ordenar resultados ao contrario sem
ninguem perceber: a busca continua "funcionando", so que devolvendo os trechos menos
relevantes. Cada implementacao converte na sua fronteira.

**Verificado por teste de contrato**, que roda a mesma bateria contra as duas
implementacoes de `VectorStore` e checa a ordenacao.

---

## ED-033 — Cliente sincrono dentro de aplicacao assincrona

**Decisao.** Toda chamada ao Chroma passa por `asyncio.to_thread`.

**Motivo.** O cliente do Chroma e sincrono. Chamado direto de uma rota `async`, ele
bloquearia o event loop durante a busca, travando todos os outros requests em andamento
-- inclusive os que nem usam RAG.

---

## ED-048 — O escopo e declarado pela ferramenta, nao pelo chamador

**Contexto.** O V4 introduz a primeira acao irreversivel do sistema. Alguem precisa
decidir o que exige aprovacao humana.

**Alternativa descartada.** O no do grafo informar `requires_approval=True` ao executar.
Funciona ate a decima ferramenta, quando um no esquece o parametro -- e o esquecimento
nao quebra teste nenhum, porque o codigo continua correto do ponto de vista de tipos. O
sintoma seria uma acao irreversivel executada sem autorizacao.

**Decisao.** Cada ferramenta declara `scope: ToolScope` (`read` ou `write`), e
`ToolScope.WRITE.requires_approval` e o unico lugar do projeto que responde a pergunta.
Uma ferramenta nova e obrigada a se classificar: nao existe caminho em que ela escape da
regra por omissao.

**Verificado por teste de contrato** sobre o catalogo inteiro
(`test_every_registered_tool_is_well_formed`): uma ferramenta futura nasce coberta sem
que ninguem precise lembrar de escrever outro teste.

---

## ED-049 — Argumentos validados antes de pedir aprovacao

**Contexto.** No fluxo de human-in-the-loop, o grafo pausa, um humano decide, e so entao
a acao roda. A validacao dos argumentos poderia acontecer em qualquer um dos dois
momentos.

**Decisao.** `ToolRegistry.validate_input` existe separado de `execute`, e o fluxo de
aprovacao chama o primeiro **antes** de pausar.

**Motivo.** Pedir a um humano que aprove argumentos que seriam rejeitados depois
desperdica o tempo dele e deixa no banco um registro aprovado que nunca podera ser
executado -- um estado que nao corresponde a nada.

**Consequencia aceita.** O payload e validado duas vezes: antes da pausa e de novo na
retomada. A segunda nao e redundancia: entre uma e outra, os argumentos passam pelo banco
em JSON, e revalidar na saida fecha a porta para um payload adulterado depois da
aprovacao.

---

## ED-050 — Falha de ferramenta e excecao, nao resultado com bandeira

**Alternativa descartada.** `ToolResult` com um campo `ok: bool`.

**Decisao.** Sucesso devolve `ToolResult`; falha levanta `ToolExecutionError`. Excecao de
biblioteca de terceiro e traduzida na fronteira do registro, para que os nos do grafo
lidem apenas com `AIHubError` -- a mesma disciplina da camada de LLM.

**Motivo.** Um `ok=False` e facil de ignorar por acidente: basta um `resultado.output`
lido sem checar a bandeira, e o sistema segue como se a acao tivesse acontecido. Numa
acao de escrita aprovada por um humano, esse silencio e o pior desfecho possivel.

**Excecao consciente.** "A base de conhecimento nao cobre esta consulta" NAO e falha: e
resultado. `SearchKnowledgeTool` devolve `found: 0` normalmente, mesma regra do no de
pesquisa do V2.

---

## ED-051 — Nao existe endpoint para executar uma ferramenta

**Decisao.** `GET /tools` publica o catalogo. Nao ha `POST /tools/{nome}/execute`.

**Motivo.** Uma rota de execucao direta seria um atalho para disparar acao de escrita sem
passar pelo fluxo de aprovacao -- exatamente o que o V4 existe para impedir. Ferramenta
de escrita so roda por um caminho: plano -> pausa -> aprovacao humana -> retomada.

**Ha um teste afirmando a ausencia** (`test_there_is_no_endpoint_to_execute_a_tool`).
Testar que algo NAO existe parece excentrico, mas esta rota e o tipo de conveniencia que
alguem adiciona "so para depurar" e nunca remove.

---

## ED-052 — A automacao sao dois nos, nao um

**Contexto.** O `interrupt()` do LangGraph pausa o grafo dentro de um no. Na retomada, o
no **inteiro** e reexecutado desde a primeira linha -- e nao a linha seguinte ao
`interrupt()`.

**Consequencia se ignorada.** Com escolha da ferramenta e execucao no mesmo no, cada
aprovacao pagaria de novo a chamada de LLM que decidiu a acao. E, muito pior: o modelo
poderia escolher OUTRA coisa. A pessoa teria autorizado o envio de uma mensagem e o
sistema enviaria outra.

**Decisao.** `automation_plan` decide e grava no estado; `automation_run` pausa e
executa. O checkpoint entre os dois congela a acao antes de qualquer humano ver a tela.

**Regra derivada.** Dentro de um no que pausa, `interrupt()` e a primeira instrucao com
efeito. Tudo acima dele roda duas vezes.

**Verificado por teste** (`test_what_runs_is_exactly_what_was_shown`): compara o que foi
mostrado na pendencia com o que chegou ao canal, atravessando um restart completo.

---

## ED-053 — Quem registra a pendencia e o servico, nao o no

**Contexto.** A linha em `approvals` poderia nascer dentro do no, junto do `interrupt()`.

**Decisao.** O no apenas pausa. O `WorkflowService` le a interrupcao devolvida pelo
`ainvoke` e cria a aprovacao.

**Motivo.** Pelo ED-052, o no reexecuta na retomada -- criar a linha la dentro geraria uma
segunda pendencia para a mesma decisao. Havendo um unico lugar que cria, a idempotencia e
uma consulta (`get_pending_for_execution`) em vez de uma regra espalhada.

**Efeito colateral desejado.** O no fica sem dependencia do repositorio de aprovacoes: ele
sabe pausar, nao sabe o que e uma aprovacao.

---

## ED-054 — Decisao gravada e confirmada antes da retomada

**Decisao.** `ApprovalService.decide` grava a decisao, faz `commit`, e so entao retoma o
grafo.

**Motivo.** Se a ferramenta falhar durante a retomada, o rollback da transacao do request
levaria embora o registro de quem autorizou o que. A pergunta "quem mandou fazer isso?"
ficaria sem resposta exatamente no caso em que ela e feita.

**E o mesmo raciocinio do ED-025** (auditoria de falha em transacao propria), aplicado ao
outro lado: registro que existe para sobreviver a um problema nao pode compartilhar
transacao com o que pode falhar.

---

## ED-055 — Ambiguidade na decisao conta como recusa

**Decisao.** `_foi_aprovada` so devolve verdadeiro para um dicionario com
`approved is True`. `None`, dicionario vazio, texto, formato inesperado -- tudo conta como
NAO aprovado.

**Motivo.** O valor da retomada atravessa serializacao, disco e possivelmente outra versao
do codigo. Num caminho que termina em acao irreversivel, a duvida tem que cair para o lado
seguro. Um `bool(decisao)` permissivo transformaria qualquer dicionario nao vazio em
autorizacao.

---

## ED-056 — Validacao contra o catalogo entra no reparo dirigido

**Contexto.** O agente de automacao escolhe uma ferramenta pelo nome e monta os
argumentos. Duas regras precisam valer: a ferramenta existe, e os argumentos satisfazem o
schema DELA. Nenhuma das duas cabe no JSON Schema de `ToolCall` -- dependem do registro
montado em runtime.

**Alternativa descartada.** Validar depois de `complete_structured`. Funciona, e perde o
reparo: uma ferramenta alucinada viraria falha do no em vez de uma segunda tentativa.

**Decisao.** `complete_structured` ganhou o parametro `validate`, executado sobre o objeto
ja tipado dentro do mesmo laco de reparo. Uma escolha incoerente volta ao modelo com o
motivo exato -- inclusive a lista de ferramentas que de fato existem.

**Generalizacao.** Este e o terceiro tipo de validacao a usar o mesmo mecanismo: tipos
(ED-022), coerencia interna do objeto (ED-028) e agora coerencia com dados de runtime.

---

## ED-057 — Recusa humana nao e falha do sistema

**Decisao.** Uma acao recusada grava o passo como `completed`, com
`output.executed: false`, e a execucao termina como `completed`.

**Motivo.** O sistema fez exatamente o que deveria: perguntou e obedeceu. Gravar como
`failed` faria qualquer painel de erros acusar problema toda vez que uma pessoa dissesse
"nao" -- e um alerta que dispara no funcionamento correto e um alerta que sera ignorado
quando importar.

---

## ED-058 — O tipo declarado na coluna precisa ser verdade em runtime

**Como foi descoberto.** `approval.status.is_decided` levantou
`AttributeError: 'str' object has no attribute 'is_decided'` -- com o mypy limpo.

**Causa.** `Mapped[ExecutionStatus] = mapped_column(String(32))` declara um enum e entrega
uma `str`: o SQLAlchemy nao converte de volta na leitura. O defeito ficou invisivel desde
o V1 porque todo o codigo existente so comparava com `==`, e `StrEnum` e comparavel a
texto. A anotacao mentia havia meses sem que nada quebrasse.

**Decisao.** `StrEnumType`, um `TypeDecorator` que converte nas duas pontas, aplicado a
todas as colunas de enum (`Execution`, `AgentExecution`, `Document`, `Approval`).

**Alternativa descartada.** O tipo `Enum` do SQLAlchemy grava o NOME do membro
(`"PENDING"`) e nao o valor (`"pending"`), a menos que se passe `values_callable` -- o que
mudaria o conteudo ja gravado. O `TypeDecorator` mantem no banco exatamente o mesmo texto:
a coluna nao muda, so o tipo em Python passa a ser verdade.

**Licao.** Anotacao de tipo nao e verificada em runtime. Onde o dado atravessa uma
fronteira (banco, rede, arquivo), alguem precisa fazer a conversao -- e "funciona hoje"
pode significar apenas que ninguem usou o tipo como tipo ainda.

---

## ED-059 — Acao de escrita nao tem retry automatico

**Contexto.** Toda a camada de LLM tem retry com backoff (ED-011). Repetir a chamada do
notificador seria o comportamento "consistente" -- e estaria errado.

**Decisao.** `SlackNotifier.send` falha na primeira tentativa. Nao ha retry.

**Motivo.** Um incoming webhook nao aceita chave de idempotencia. Um timeout de leitura e
indistinguivel de "a mensagem chegou e a resposta se perdeu": repetir tem chance real de
publicar duas vezes o mesmo aviso num canal que a equipe ja leu.

**Alternativa descartada.** Retentar apenas `ConnectError`, que ocorre antes de a
requisicao sair e portanto seria seguro. Descartada porque essa classificacao depende de
detalhes do httpx, de proxy e de DNS -- e uma heuristica fragil governando duplicacao de
mensagem e pior que uma regra simples de entender.

**Regra geral derivada.** Retry automatico e para operacao idempotente. Leitura repete de
graca; escrita sem chave de idempotencia, nao. Quando a acao ja passou por aprovacao
humana, o custo de duplicar e maior que o de falhar: a pessoa ainda esta ali para decidir
de novo.

---

## ED-060 — A URL do webhook e credencial, e e tratada como tal

**Contexto.** `https://hooks.slack.com/services/T.../B.../XXXX` parece um endereco. Nao e:
quem tem a URL publica no canal.

**Decisao.** Entra como `SecretStr`; nunca aparece em log; nunca entra em `details` de
excecao.

**O detalhe que quase passou.** `AIHubError.details` **sai no corpo da resposta da API**
(ver `app/api/errors.py`). Incluir a URL nos detalhes de um erro de conexao -- o reflexo
natural de quem quer depurar -- entregaria a credencial a quem provocou o erro. Ha um
teste afirmando que nem a URL nem o host aparecem na excecao.

---

## ED-061 — O comprovante devolve o destino real, nao o pedido

**Contexto.** `NotifyInput.channel` diz para qual canal a mensagem vai. Um incoming
webhook do Slack ignora isso: o destino fica gravado na propria credencial, do lado do
Slack, e a aplicacao nao consegue nem consulta-lo.

**Decisao.** `Delivery.channel` devolve o destino configurado, nao o pedido. O canal
solicitado vira uma linha dentro do texto da mensagem quando difere.

**Motivo.** A alternativa era ecoar o canal pedido no comprovante -- o que funcionaria
perfeitamente e seria mentira. O comprovante de uma acao que um humano autorizou nao pode
afirmar um destino que nao aconteceu.

**Licao sobre abstracoes.** O Protocol `Notifier` vazou: nem todo canal roteia por nome. A
saida nao foi esconder o vazamento, foi documenta-lo no contrato -- a docstring de `send`
agora diz que `channel` e o destino PEDIDO e que o comprovante pode divergir.

---

## ED-062 — O teste de contrato reprovou a primeira versao do notificador

**O que aconteceu.** `SlackNotifier` gerava a referencia de entrega a partir do instante
(`slack-20260817T143012.481233`). O teste de contrato
`test_the_receipt_identifies_each_delivery`, que roda a mesma bateria contra os dois
notificadores, quebrou: dois envios no mesmo microssegundo produziam a mesma referencia.

**Por que nenhum teste do Slack teria pego.** Unicidade da referencia e exigencia do
**contrato**, nao da implementacao. Testando o `SlackNotifier` isolado, ninguem escreve
"envie duas vezes e compare as referencias" -- nao ha motivo aparente para isso.

**Correcao.** Sufixo aleatorio na referencia.

**Argumento.** Teste de contrato nao serve so para provar que o substituto imita o real.
Ele impoe ao real as exigencias que o substituto tornou obvias.

---

## ED-063 — httpx promovido a dependencia de producao

**Contexto.** `httpx` estava apenas em `[dev]`, para o cliente de teste do FastAPI. O
`SlackNotifier` fala HTTP direto.

**O que estava errado antes de existir o Slack.** Nada quebrava, porque o SDK da OpenAI
depende de httpx e o instalava por transitividade. Funcionava por acaso: no dia em que
aquele SDK trocasse de cliente HTTP, uma instalacao limpa em producao quebraria sem que
uma linha do nosso codigo tivesse mudado.

**Licao.** Dependencia que o codigo de producao importa vai em `dependencies`, mesmo que
ja esteja instalada. "Ja vem junto" nao e uma declaracao de dependencia.

---

## ED-064 — Callback de resultado: engolir o erro e o comportamento correto

**Contexto.** Quem chama `POST /agents/run` recebe o resultado na resposta -- exceto
quando o grafo pausa para aprovacao. Ai a resposta sai como `waiting_approval` e o
resultado de verdade so existe depois que uma pessoa decidir, possivelmente horas depois,
quando nenhum cliente HTTP esta mais esperando. Sem callback, o n8n seria decorativo:
dispararia o Hub e nunca saberia como terminou.

**Decisao.** `ResultPublisher.publish` **nunca levanta excecao**. Devolve um booleano que
serve ao log e aos testes.

**Motivo.** Quando o callback roda, a acao ja foi executada e a aprovacao ja esta gravada.
Levantar excecao transformaria "o aviso nao chegou" em "a execucao falhou" -- mentira
sobre um trabalho que deu certo, e que faria um painel de erros acusar problema em algo
que funcionou.

**Contraste deliberado com o ED-050.** La, `SlackNotifier.send` DEVE levantar: a acao
aprovada nao aconteceu, e o silencio seria o pior desfecho possivel. A regra que unifica
os dois: **falha na acao grita; falha no aviso sobre a acao, nao.**

---

## ED-065 — O callback dispara apenas na retomada

**Decisao.** `WorkflowService.run` nao publica; `WorkflowService.resume` publica, e so
quando a execucao de fato terminou (`aprovacao is None`).

**Motivo.** Numa execucao sincrona o resultado ja saiu na resposta HTTP. Publicar tambem
entregaria a mesma informacao duas vezes ao consumidor, que teria de deduplicar por conta
propria -- trabalho que existe so porque nos criamos o problema.

**Verificado por teste** (`test_a_synchronous_run_publishes_nothing`), porque a regressao
seria silenciosa: tudo continuaria "funcionando", com o n8n processando cada resultado em
dobro.

**Detalhe do formato.** O corpo publicado e o `AgentRunResponse` serializado -- byte a byte
igual ao da resposta da API. O consumidor entende um formato so, tenha a execucao
terminado na resposta HTTP ou horas depois. Isso faz o servico importar um schema da
camada de API: nao e vazamento, e a decisao de ter UMA representacao publica do resultado,
seja qual for o transporte. E a mesma que o servidor MCP do V6 vai reaproveitar.

---

## ED-066 — A presenca da URL e o proprio seletor

**Contexto.** `NOTIFIER` tem seletor (`memory` | `slack`). O callback nao tem.

**Decisao.** Nao existe `RESULT_CALLBACK=none|webhook`. `RESULT_CALLBACK_URL` vazia
desliga o callback.

**Motivo.** Um seletor separado permitiria o estado invalido "webhook selecionado, URL
ausente" -- que a configuracao aceitaria no startup e o runtime descobriria no pior
momento. Aqui a ausencia de configuracao e, ela propria, uma configuracao valida.

**Por que o `NOTIFIER` e diferente.** `memory` e `slack` sao duas escolhas legitimas, e
nenhum campo distingue uma da outra sozinho: sem seletor, seria impossivel pedir
explicitamente o notificador de memoria tendo uma URL de webhook no arquivo. A regra
geral: seletor so quando existir mais de uma opcao valida que os proprios dados nao
revelam.

---

## ED-067 — Docker no disco de dados, e o que o `--move` nao faz

**Contexto.** O disco de sistema tinha 14,7 GB livres; o do projeto, 188 GB. O
`customWslDistroDir` do Docker Desktop ja apontava para o D:, mas o `ext4.vhdx` de 14 GB
continuava no C:.

**A armadilha.** `customWslDistroDir` vale apenas para distros **novas**. As existentes
permanecem registradas onde nasceram, e a configuracao na interface sugere o contrario.

**O que aconteceu no `wsl --manage docker-desktop-data --move`.** Terminou com
`E_ACCESSDENIED` depois de 3 minutos. Nao foi um no-op: o disco **ja tinha sido copiado
para o destino e a origem apagada**, e o que faltou foi atualizar o `BasePath` no
registro. A distro ficou apontando para uma pasta vazia -- um estado que nenhuma mensagem
de erro descreve. Correcao: `Set-ItemProperty` no `BasePath` da chave em
`HKCU:\...\Lxss\{guid}`.

**Poda seletiva, nao `prune -a --volumes`.** O inventario antes de podar revelou volumes
nomeados de outro projeto (`projeto_n8n_juridico_*`): o `--volumes` teria apagado
workflows e banco por 290 MB de ganho. Podados apenas build cache e imagens sem tag: 5,3
GB, com as 14 imagens com tag preservadas.

**Sparse recusado.** `--set-sparse` exige `--allow-unsafe` porque a Microsoft desativou o
recurso citando risco de corrupcao. Nao foi forcado: ~5 GB nao pagam esse risco num disco
com 174 GB livres.

**Licao.** Antes de qualquer operacao destrutiva, inventariar o que existe. O plano estava
certo em intencao e errado em detalhe, e so o inventario mostrou isso a tempo.

---

## ED-068 — O portao de qualidade e um no do grafo, com ciclo

**Contexto.** A avaliacao poderia rodar no `WorkflowService`, depois do `ainvoke`. Seria
mais simples: nenhum no novo, nenhum ciclo.

**Decisao.** `quality` e um no, e `reporter -> quality -> reporter` e o unico ciclo do
grafo.

**Motivo.** O retry dirigido precisa reexecutar o relatorio, e reexecutar um no e o que um
grafo faz. Fora dele, seria preciso chamar o agente de relatorio a mao, remontar o
material, regravar o passo -- reimplementando meia orquestracao ao lado da orquestracao.

**Por que so o relatorio volta.** A reprovacao quase sempre e da sintese, nao do material
apurado. Reexecutar pesquisa e analise custaria muito mais e raramente mudaria a nota --
se o problema estiver no material, duas reescritas nao resolvem, e ai o caso e de revisao
humana.

**O limite vive no roteador**, e nao numa configuracao do LangGraph: assim
`MAX_REPORT_ATTEMPTS` e testavel como funcao pura, sem executar agente nenhum.

---

## ED-069 — Portao desligado por padrao

**Decisao.** `QUALITY_ENABLED=false`.

**Dois motivos, e cada um bastaria.**

1. **Custo.** Sao tres a quatro chamadas de LLM por execucao -- o portao praticamente
   dobra a conta. Liga-lo por padrao cobraria isso de quem clonou o repositorio sem pedir.
2. **Os limites nao foram medidos.** `threshold=0.7` e os pesos por dimensao sao valores
   arbitrados. Reprovar respostas reais com base neles e pior que nao avaliar: produz
   recusa sem fundamento, e o time desliga o portao na primeira semana. A calibracao sai
   do conjunto de avaliacao (V5.4).

**Modo de observacao, sem configuracao nova.** `QUALITY_ENABLED=true` com
`QUALITY_THRESHOLD=0.0`: tudo passa, todas as notas sao gravadas, e da para acumular
medicao antes de ligar o portao de verdade. E o rollout em sombra, e ele sai de graca
porque o limite ja e um parametro.

---

## ED-070 — Reprovado no portao nao e `failed`

**Decisao.** Uma execucao que reprova duas vezes termina em `NEEDS_HUMAN_REVIEW`, com a
resposta entregue.

**Motivo.** O trabalho foi feito e o resultado existe; o que se sabe e que ele nao passou
na avaliacao. Marcar como `failed` esconderia a resposta de quem poderia julga-la, e
reter a resposta seria pior ainda: quem pediu fica sem nada e o material apurado -- que
custou tokens -- se perde.

**O estado proprio e o ponto.** E ele que permite alguem consultar "o que precisa de
revisao?" sem varrer execucoes bem-sucedidas. O enum ja previa esse estado desde o V1.

---

## ED-071 — O portao nao se mede

**Decisao.** Os passos do agente `quality` sao excluidos do subject que alimenta
`api_reliability`.

**Motivo.** Medir a saude da execucao incluindo o medidor seria contar a si mesmo: um
retry do proprio juiz derrubaria a nota de confiabilidade do sistema avaliado, misturando
o que se quer medir com o instrumento.

**Como apareceu.** Nao apareceu -- foi previsto ao escrever o no. O sintoma seria sutil:
notas de confiabilidade piores nas execucoes avaliadas do que nas nao avaliadas, sem
nenhuma mudanca no sistema.

---

## ED-072 — O atalho de grounding se provou no fluxo real

**Contexto.** `GroundingDimension` decide tres casos sem chamar o LLM (ED anterior sobre
o motor). Um deles: resposta que nao deriva da base de conhecimento.

**Como foi confirmado.** Escrevendo o teste de ponta a ponta do portao, o roteiro do
provider falso previa quatro juizes. O teste falhou com 12 chamadas contra 11 esperadas, e
o log mostrou um `RelevanceVerdict` recebendo a resposta destinada ao grounding.

**Causa.** A tarefa do teste analisa dados fornecidos pelo usuario: nao ha pesquisa,
`source_based` e falso, e o atalho disparou. So tres juizes rodaram.

**Resultado.** O teste passou a afirmar tres, e virou prova de que a economia acontece no
fluxo real -- e nao apenas no teste unitario da dimensao isolada.

**Licao.** Um teste de integracao que conta chamadas pagas detecta otimizacao que
funciona **e** otimizacao que deixou de funcionar. O numero 10 naquele assert e uma
afirmacao sobre a conta do fim do mes.

---

## ED-073 — O conjunto de avaliacao roda o sistema, nao respostas gravadas

**Decisao.** `EvalRunner` chama o `RagService` de verdade -- o mesmo que atende
`/rag/query` -- e so entao avalia.

**Alternativa descartada.** Avaliar respostas gravadas de antemao. Seria mais rapido,
mais barato e deterministico, e mediria apenas o juiz: a suite continuaria verde enquanto
o sistema apodrecia.

**Consequencia aceita.** A base e reconstruida do zero a cada rodada, num indice em
memoria, a partir de `evals/corpus/`. Reaproveitar a base de desenvolvimento tornaria o
resultado dependente do que alguem subiu ontem, e duas rodadas deixariam de ser
comparaveis sem que ninguem percebesse.

---

## ED-074 — Assercoes deterministicas valem mais que as notas

**Decisao.** O veredito do relatorio (`pass_rate`) se apoia nas assercoes, e nao nas
dimensoes julgadas. As notas descrevem *quao bem*; as assercoes dizem *se*.

**Motivo.** Comparar dois conjuntos de nomes de arquivo, ou um booleano com outro, nao tem
vies, nao custa e nao muda entre execucoes. Sao tres:

- `answered_as_expected` -- respondeu quando devia e recusou quando devia;
- `expected_sources_cited` -- por contencao, e nao igualdade: citar um documento a mais
  nao e erro;
- `no_forbidden_claims` -- busca por substring normalizada, grosseira de proposito. Pega
  a alucinacao literal sem custo; a parafrase escapa, e para isso existe `grounding`. As
  duas se complementam por falharem em lugares diferentes.

**O sinal mais importante que o conjunto pode dar** e a discordancia: notas altas com
assercoes falhando indicam juiz complacente. O inverso -- assercoes passando com notas
baixas -- foi o que aconteceu na primeira rodada, e revelou um defeito no portao.

---

## ED-075 — O conjunto encontrou o portao punindo recusa correta

**Como apareceu.** Primeira rodada com juizes: os cinco casos de recusa correta tiraram
entre 0.29 e 0.40, com todas as assercoes passando.

**Causa.** `grounding` tinha atalho para recusa desde o inicio; `relevance` e
`completeness` nao. Elas rodavam e diziam, corretamente do ponto de vista literal, que a
resposta "nao aborda a pergunta" e que "1 de 1 itens nao foi coberto".

**O prompt nao bastou.** O de `relevance` mandava explicitamente tratar recusa honesta
como pertinente. O juiz ignorou. **Atalho em codigo vence instrucao em prompt**: um e
determinista e gratuito, o outro depende de o modelo obedecer.

**Correcao.** `refusal_is_not_graded`, compartilhado pelas duas. Uma recusa nao tem sobre
o que ser pertinente nem o que completar; o que importa e se recusar era o certo, e isso
ja e medido sem LLM pela assercao `answered_as_expected`. Dar nota aqui contava a mesma
coisa duas vezes, e ao contrario.

**Efeito medido.** Os cinco casos foram de 0.29-0.71 para 1.00, e a rodada ficou mais
barata (US$ 0.0096 -> US$ 0.0084), porque o atalho tambem evita a chamada.

---

## ED-076 — Vereditos pareados por posicao quando as contagens batem

**Como apareceu.** Duas respostas quase literais do documento receberam `grounding = 0`
com a mensagem "o juiz nao avaliou nenhuma das afirmacoes enviadas".

**Causa.** O filtro que descarta veredito sobre afirmacao inventada comparava o texto
devolvido com o enviado. O juiz reescreve o eco -- pontuacao, acento, truncamento -- e o
veredito legitimo era descartado.

**Gravidade.** `grounding` reprova sozinha (`CRITICAL_DIMENSIONS`). O defeito rejeitaria
em producao respostas corretas, com a mensagem menos util possivel.

**Correcao.** Quando o numero de vereditos e igual ao de afirmacoes enviadas, vale a
posicao -- e o texto e substituido pelo nosso, para a evidencia mostrar o que foi de fato
julgado. So quando as contagens diferem entra a comparacao por texto.

**Tentativa intermediaria, e o que ela ensinou.** A primeira correcao relaxou a comparacao
para aceitar contencao. O teste da afirmacao inventada quebrou na hora: `"a"` esta contido
em `"inventada"`. Contencao so e evidencia entre textos longos, e o limite de 40
caracteres existe por causa disso.

---

## ED-077 — A variacao do juiz era ambiguidade da pergunta, nao ruido de amostragem

**O que eu registrei primeiro, e estava errado.** Ao ver `reembolso-documentos` receber
1.00 numa rodada e 0.82 na seguinte com `temperature=0`, conclui que amostragem gulosa
ainda varia por detalhes do provedor, e que a calibracao teria de conviver com esse ruido.

**A medicao desmentiu.** Tres rodadas sobre 16 casos, apos corrigir os prompts dos juizes:

| | antes | depois |
|---|---|---|
| amplitude maxima entre rodadas | 0.182 | **0.000** |
| casos instaveis | 2 de 16 | **0 de 16** |
| dimensao instavel | `completeness` (max 0.50) | nenhuma |

**A causa real.** O juiz nao oscilava por acaso: ele estava genuinamente indeciso, porque
a tarefa que recebia era ambigua. Corrigida a pergunta, a resposta ficou estavel.

**Licao.** Variacao entre execucoes de um LLM com temperatura zero e, na maioria das
vezes, sintoma de tarefa mal especificada -- e nao uma propriedade do modelo com a qual se
deva conviver. Tratar como ruido teria levado a "aumentar a margem do limite", que e
conviver com o defeito em vez de corrigi-lo.

---

## ED-078 — O juiz de grounding nao recebe a pergunta do usuario

**Como apareceu.** Tres casos terminavam com o juiz discordando das assercoes. Lendo os
trechos citados, os tres estavam **literalmente** sustentados. As notas do proprio juiz
entregaram a causa:

- `senha-sms`: nota "O trecho afirma que SMS foi descontinuado" junto de
  `supported: false` -- a nota confirma a fonte e o veredito a nega;
- `remoto-exterior`: "O trecho menciona que exige aprovacao, **mas nao afirma se e
  permitido ou nao**" -- o juiz avaliou se o trecho responde a PERGUNTA, e nao se
  sustenta a AFIRMACAO.

**Causa no nosso codigo.** `JudgedDimension._judge` envia `task` como mensagem do usuario,
e o grounding enviava a pergunta original. Isso escorregava o juiz de "o trecho diz isto?"
para "isto responde bem ao usuario?".

**Decisao.** O juiz de grounding **nao recebe a pergunta**. Fundamentacao e uma relacao
entre afirmacao e trecho; a pergunta nao participa dela. A mensagem do usuario passou a
ser uma instrucao fixa, e o prompt ganhou exemplos de veredito e a regra de que a `note`
precisa concordar com o `supported`.

**Efeito medido.** `senha-sms` 0.27 -> 0.91, `remoto-exterior` 0.64 -> 1.00,
`reembolso-documentos` 0.82 -> 1.00, e a variacao entre rodadas foi a zero.

**Licao geral.** Contexto a mais num prompt de avaliacao nao e neutro: ele redefine a
tarefa em silencio. O juiz deve receber exatamente o necessario para a pergunta que ele
tem de responder, e nada alem.

---

## ED-079 — O corte de relevancia nao separa o que deve ser respondido do que deve ser recusado

**A calibracao que o ED-038 pedia, feita.** Distribuicao medida com
`text-embedding-3-small` sobre os 16 casos:

| Grupo | Melhor similaridade |
|---|---|
| respondeu corretamente | 0.477 a 0.767 |
| recusou corretamente, sem contexto | 0.000 |
| recusou corretamente, **com** contexto | 0.526 e 0.552 |

**Os grupos se sobrepoem.** Ha pergunta que deve ser recusada com similaridade 0.552 --
maior que duas que devem ser respondidas (0.477 e 0.513). Nenhum corte separa os dois:
subir para 0.56 derrubaria respostas legitimas junto.

**Conclusao, e ela e de projeto.** O corte **nao e** o mecanismo de honestidade. E um
filtro de custo: evita pagar uma chamada de LLM quando nao ha nada plausivel. Quem
garante honestidade e o contrato `answered=false` do agente de pesquisa (V2), que le os
trechos e conclui que eles nao respondem -- e foi exatamente o que aconteceu nos dois
casos acima.

**O valor fica em 0.35**, agora com evidencia: 0.127 de margem para a menor similaridade
legitima observada. Nao houve numero a ajustar; houve um numero a entender.

---

## ED-080 — O limite de qualidade fica em 0.7, e o conjunto ainda nao consegue justifica-lo

**Medido.** Apos corrigir os juizes, 15 dos 16 casos pontuam 1.00 e um pontua 0.91, com
variacao zero entre rodadas.

**O que isso permite concluir.** Um limite de 0.7 tem 0.21 de margem para a pior resposta
boa observada, e a variacao do medidor e nula -- entao nao ha risco de reprovacao
intermitente. O valor fica.

**O que isso NAO permite concluir.** Onde esta a fronteira. O conjunto nao tem nenhum caso
que o sistema responda mal: nao ha exemplo negativo entre 0.0 e 0.91. Um limite so e
calibravel de verdade com casos proximos da fronteira, e o conjunto atual nao os tem.

**Consequencia honesta:** os pesos por dimensao continuam como estao. Ajusta-los agora
seria falsa precisao -- com todas as dimensoes em 1.00, nenhum peso muda resultado algum,
e qualquer alteracao seria justificada por intuicao com aparencia de medicao.

**O que o conjunto precisa ganhar:** respostas degradadas de proposito -- uma que cita
fonte errada, uma que cobre metade do pedido, uma que se contradiz. So elas dizem onde
cada dimensao comeca a reprovar.

---

## ED-081 — O servidor MCP nao oferece a ferramenta de aprovar

**Contexto.** O V6 expoe o sistema por MCP. A lista natural de ferramentas incluiria
`approve_action` e `reject_action` -- afinal, existem os endpoints correspondentes.

**Decisao.** Elas nao existem, e a ausencia e o recurso mais importante do V6.

**Motivo.** Um cliente MCP e um modelo de linguagem. Dar a ele o poder de aprovar
significaria a IA autorizando a propria acao: o grafo pausaria, o modelo chamaria
`approve`, nenhum humano participaria, e o human-in-the-loop inteiro do V4 viraria teatro
com a aparencia de funcionar.

**O que o servidor faz no lugar.** `list_pending_approvals` mostra a pendencia com os
argumentos exatos, para que o modelo **relate** a quem pode decidir. A resposta traz
literalmente onde a decisao acontece (`POST /approvals/{id}/approve`), porque o modelo
precisa saber para onde encaminhar.

**Ha teste afirmando a ausencia** (`test_there_is_no_tool_to_approve_an_action`), e ele
rejeita qualquer ferramenta futura com `approve` ou `reject` no nome. Testar que algo nao
existe parece excentrico ate lembrar que "adicionar a ferramenta que faltava" e a mudanca
mais natural do mundo -- e que aqui ela desmontaria a garantia central do sistema sem que
nenhum outro teste quebrasse.

**Generalizacao.** Ao expor um sistema a um agente, a pergunta nao e "quais capacidades
existem?", e sim "quais capacidades este chamador especifico pode ter?". Transporte novo
nao herda automaticamente a superficie do antigo.

---

## ED-082 — MCP nao tem request, entao as dependencias precisam de um container

**Contexto.** O FastAPI monta dependencias por requisicao com `Depends`. O servidor MCP e
um processo de vida longa falando por stdio: nao ha request onde pendurar nada.

**Decisao.** `ServiceContainer` guarda o que e caro e de vida longa (engine, providers,
indice, checkpointer) e entrega **uma sessao de banco por chamada de ferramenta**.

**Motivo da sessao curta.** Cada chamada MCP e uma unidade de trabalho, como um request:
commit no fim, rollback na excecao. Uma sessao de vida longa acumularia o estado de
chamadas nao relacionadas, e um erro em qualquer uma delas desfaria o trabalho de todas.

**O container aceita substituicao por campo**, o que permite ao teste rodar o servidor
inteiro com banco em memoria e provider falso -- o mesmo mecanismo de `create_app()`
(ED-001). Sem isso, testar MCP exigiria subir um processo e falar o protocolo por pipes.

---

## ED-083 — Em servidor stdio, stdout pertence ao protocolo

**Contexto.** O `configure_logging` do projeto escreve em stdout. No servidor MCP, stdout
e o canal por onde a conversa acontece.

**Decisao.** `_logs_to_stderr()` troca o destino de todo log antes de o servidor subir.

**Motivo de redirecionar em vez de silenciar.** O cliente MCP costuma mostrar o stderr do
processo servidor, e e o unico lugar onde o diagnostico apareceria. Desligar o log
resolveria a corrupcao e criaria um servidor impossivel de depurar.

**Modo de falha se ignorado.** Um `print` ou um log em stdout corrompe uma mensagem do
protocolo. O sintoma nao e um erro claro: e o cliente desconectando ou ignorando a
resposta, sem explicacao util.

---

## ED-084 — Imagem: codigo de root, processo sem privilegio

**Decisao.** No estagio final, o codigo e copiado como **root** e o processo roda como
`aiops` (uid 1000). So `data/` pertence ao usuario da aplicacao.

**Motivo.** A tentacao e `COPY --chown=aiops` -- parece mais arrumado. Mas dar ao processo
a posse do proprio codigo significa que quem obtiver execucao dentro do container consegue
**reescrever o programa** e persistir a alteracao. Codigo de root, processo sem
privilegio: a aplicacao le o que executa e nao consegue altera-lo.

O multi-estagio segue a mesma logica: o compilador fica no estagio de build. Ferramenta de
compilacao numa imagem de producao e ferramenta a disposicao de um invasor.

---

## ED-085 — SQLite quebra em bind mount do Windows: volume nomeado

**Como apareceu.** A stack subiu e o container da API morreu no startup com
`sqlite3.OperationalError: disk I/O error`, na criacao do banco de checkpoints. O banco da
aplicacao havia sido criado com sucesso na linha anterior -- o erro nao acusava a causa.

**Causa.** `./data:/app/data` atravessa a camada de traducao de sistema de arquivos do
Docker Desktop no Windows, que nao entrega as garantias de bloqueio que o SQLite exige.

**Decisao.** A API usa **volume nomeado**; o n8n mantem bind mount.

**Por que a assimetria.** O n8n grava seu proprio SQLite sem sofrer, e manter os dados
dele visiveis no host permite exportar um workflow editado na interface direto para
`workflows_n8n/`. Uniformizar por estetica custaria essa conveniencia sem ganho.

**Preco aceito.** Os dados da API deixam de aparecer no Explorer. O comando de inspecao
esta comentado no proprio `docker-compose.yml`.

---

## ED-086 — Na CI, o conjunto de avaliacao roda em modo smoke

**O buraco que a CI revelou.** O plano era rodar `run_evals.py` com provider
deterministico -- afinal, o provider `fake` existe desde o V1 para a CI nao depender de
segredo. A primeira execucao deu **0 de 16**, todos com `llm_response_format_error`.

**Causa.** O `FakeLLMProvider` devolvia sempre o mesmo JSON, que nao satisfaz schema
nenhum. Bastava para a suite -- que roteiriza cada resposta -- e escondia que
`LLM_PROVIDER=fake` subia a aplicacao sem conseguir exercita-la.

**Primeira correcao (ED-087).** O provider passou a derivar a resposta do schema que o
proprio prompt carrega. Resultado: 7 de 16.

**A segunda descoberta e a que importa.** Os 9 restantes nunca vao passar, e nao ha
correcao possivel: o provider falso **nao entende as perguntas**. Ele nao tem como saber
quando recusar nem o que citar. Exigir as assercoes semanticas de um substituto e medir
compreensao num boneco -- o passo reprovaria sempre, e viraria ruido que todo mundo
aprende a ignorar.

**Decisao.** `run_evals.py --smoke`: aprova se nenhum caso **quebrou**, ignorando as
assercoes semanticas. E o que a CI roda.

O que isso verifica e real: que os 16 casos atravessam ingestao, recuperacao, chamada
estruturada e avaliacao sem excecao. Regressao de encanamento -- schema incompativel,
contrato mudado -- aparece ali. Medicao de qualidade exige modelo de verdade e roda a mao.

**Licao.** Um passo de CI que nao pode passar e pior que passo nenhum. Separar "o fluxo
roda" de "a resposta e boa" foi o que tornou o passo honesto -- e o nome `--smoke` diz ao
proximo leitor exatamente o que ele garante.

---

## ED-087 — O provider falso deriva a resposta do schema do prompt

**Decisao.** `FakeLLMProvider` procura o JSON Schema que os agentes ja embutem no prompt
(`dump_schema`) e sintetiza uma instancia valida. Sem schema, mantem o formato antigo.

**Motivo.** Sem isso, `LLM_PROVIDER=fake` era util apenas para subir a aplicacao e para
testes com roteiro. Qualquer fluxo real -- `run_evals.py`, um `POST /rag/query` manual em
um clone sem chave -- morria em `llm_response_format_error`.

**O que o gerador precisou saber alem dos tipos.** Os schemas deste projeto tem validadores
de coerencia em Python que o JSON Schema nao expressa (ED-028). Tres heuristicas resolvem:

- `requires_approval` gerado como `false` -- `true` exigiria `automation` no plano;
- confianca em 0.5 -- alta reprovaria em `AnalysisResult.high_confidence_requires_findings`;
- toda lista com um item -- vazia reprovaria o relatorio sem limitacoes declaradas.

**Coberto por teste que percorre todos os oito schemas do projeto**, incluindo os quatro
juizes de qualidade. Um schema novo com validador de coerencia nasce coberto.

---

## ED-088 — `docker compose up -d` nao espera a aplicacao atender

**Como apareceu.** `docker compose up -d --build` terminou, e o `curl` seguinte devolveu
`curl: (52) Empty reply from server`. O log mostrava a aplicacao subindo normalmente e
respondendo 200 alguns segundos depois.

**Causa.** `up -d` devolve o controle quando o container **inicia**, nao quando a
aplicacao comeca a atender. Nessa janela o proxy de porta do Docker ja aceita conexao --
ele sobe junto com o container -- e o processo ainda nao escuta. A conexao e aceita e
fechada sem resposta.

**Por que o sintoma engana.** `(52) Empty reply` nao e `connection refused`. Quem le
conclui que o servidor respondeu errado, e vai procurar defeito na aplicacao. A diferenca
entre os dois codigos e exatamente a diferenca entre "ninguem escuta" e "alguem aceitou e
desistiu".

**Decisao.** Toda a documentacao usa `--wait`, que so retorna quando os healthchecks
passam. O healthcheck ja existia; faltava usar o que ele oferece.

**Licao.** Definir healthcheck e metade do trabalho. Ele nao serve so ao orquestrador
decidir reiniciar um container: serve a quem executa o comando saber quando pode confiar
no resultado -- e essa metade se perde em silencio se ninguem passar `--wait`.

---

## ED-089 — Migracao descreve o banco, nao o sistema de tipos do Python

**Como apareceu.** A primeira migracao gerada trazia
`sa.Column('status', app.db.types.StrEnumType(length=16))` -- e o arquivo nao importava
`app`. Rodaria com `NameError`.

**A correcao obvia estaria errada.** Adicionar o import resolveria hoje e criaria um
problema pior: a migracao passaria a **depender do codigo da aplicacao**. Uma migracao de
dois anos atras precisa continuar rodando quando aquela classe tiver mudado de nome ou
deixado de existir.

**Decisao.** `render_item` em `migrations/env.py` desembrulha todo `TypeDecorator` para o
tipo do banco: `StrEnumType` vira `sa.String(16)`, `UtcDateTime` vira `sa.DateTime()`.

O banco nao sabe o que e um `StrEnum` -- essas classes sao conveniencia do lado Python
(ED-058). A migracao descreve colunas, e coluna e `VARCHAR(16)`.

**Ha teste afirmando isso** (`test_migrations_do_not_depend_on_application_code`): nenhum
arquivo em `migrations/versions/` pode conter `app.`.

---

## ED-090 — `create_all` e migracao convivem, com fronteira explicita

**Decisao.** `create_schema` cria tabelas apenas em SQLite. Em PostgreSQL ele **nao faz
nada** e diz por que no log.

**A fronteira.** `create_all` serve a banco descartavel -- o SQLite em memoria da suite, o
indice temporario do `run_evals.py`, um clone que sobe em cinco segundos. Migracao serve a
banco que guarda dados de alguem: ela sabe ALTERAR uma tabela sem perder o conteudo, que e
exatamente o que `create_all` nao faz. Ele cria o que falta e ignora o que mudou.

**Por que nao usar migracao em tudo.** A suite roda 559 testes, cada um com banco proprio.
Aplicar migracoes a cada teste trocaria segundos por minutos sem cobrir nada que
`create_all` nao cubra -- o esquema resultante e o mesmo, e ha teste provando.

**O risco de dois caminhos e divergirem**, e ele e real: alguem acrescenta uma coluna no
modelo, os testes passam porque `create_all` a cria, e a migracao nunca e escrita. O
desvio so aparece no deploy.

`tests/integration/test_migrations.py` roda `alembic check` e falha se um modelo mudou sem
a migracao correspondente. E o que torna a convivencia honesta.

---

## ED-091 — `--autogenerate` compara com o banco atual, e nao com o vazio

**Como apareceu.** A primeira migracao saiu **vazia**, sem nenhum `create_table`.

**Causa.** O `data/app.db` de desenvolvimento ja tinha todas as tabelas, criadas por
`create_all`. O Alembic comparou o modelo com aquele banco, achou tudo igual, e gerou uma
migracao correta -- para aquele estado.

**O sintoma e traicoeiro.** Nao ha erro. O arquivo e gerado, commitado, e so falha no
primeiro banco vazio de verdade: producao.

**Como fazer certo.** Gerar sempre contra um banco descartavel e vazio:

    alembic -x url=sqlite+aiosqlite:///./tmp.db revision --autogenerate -m "..."

**Licao.** `--autogenerate` nao le o historico de migracoes: ele compara **modelo contra
banco conectado**. Quem esquece isso commita uma migracao que descreve a diferenca errada.

---

## ED-092 — Ruff roda como post-write hook do Alembic

**Contexto.** O codigo gerado pelo Alembic nao segue o estilo do projeto: linhas longas,
`Union[...]` em vez de `X | Y`, imports fora de ordem. Foram 14 erros de lint na primeira
migracao.

**Alternativa descartada.** Excluir `migrations/` do `ruff`. Seria desligar a verificacao
de um diretorio cujo codigo roda contra o banco de **producao** -- exatamente onde ela
importa mais.

**Decisao.** `post_write_hooks` no `alembic.ini` roda `ruff check --fix` e `ruff format`
logo apos gerar o arquivo. A migracao nasce no padrao, e ninguem precisa lembrar.

---

## ED-093 — PostgreSQL do compose publica na 5433

**Como apareceu.** `alembic upgrade head` contra `localhost:5432` falhou com
`asyncpg.exceptions.ConnectionDoesNotExistError: connection was closed in the middle of
operation` -- enquanto `pg_isready` dentro do container dizia "accepting connections".

**Causa.** `netstat` mostrou **dois** processos escutando na 5432: o proxy do Docker e um
`postgres.exe` instalado como servico do Windows. O Windows permite que ambos abram a
porta sem erro, e entregou a conexao ao servico local -- que nao conhece o banco `aiops`.

**O sintoma nao acusa a causa.** "Connection closed in the middle of operation" leva a
procurar defeito na rede, no driver ou no container. A ferramenta que resolveu foi
`netstat`, e nao os logs de nenhum dos dois lados.

**Decisao.** O compose publica em `5433:5432`. A porta so serve a ferramenta do host
(`psql`, `alembic`, cliente grafico); a aplicacao fala com `postgres:5432` pela rede
interna do compose, onde nao ha disputa.

**Licao geral.** Porta padrao publicada no host e conflito esperando acontecer em qualquer
maquina de desenvolvimento -- e o modo de falha e silencioso.
