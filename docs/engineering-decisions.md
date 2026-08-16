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
