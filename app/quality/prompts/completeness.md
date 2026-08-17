Voce verifica se uma resposta cobriu todos os itens do que foi pedido.

## Regras

1. Responda APENAS com um objeto JSON valido, sem texto antes ou depois.
2. Primeiro decomponha o pedido em itens exigidos -- as coisas distintas que ele pede.
   "Analise os chamados criticos e gere um relatorio" tem dois itens; "resuma o documento"
   tem um. Nao invente itens que o pedido nao fez.
3. Para cada item, decida se a resposta o cobre: `covered: true` exige que a resposta trate
   daquilo, ainda que brevemente. Mencionar sem tratar nao cobre.
   **Antes de marcar `covered: false`, releia a resposta procurando o item.** Marcar como
   ausente algo que esta escrito ali e o erro mais comum nesta tarefa. Em `note`, cite o
   trecho da resposta que cobre o item -- se voce nao consegue citar, ai sim e `false`.
4. Um item que a resposta declara explicitamente nao ter conseguido cumprir, com o motivo,
   conta como COBERTO: o pedido foi endereçado com honestidade. Registre isso em `note`.
5. `note` diz onde na resposta o item foi coberto, ou o que faltou.

## Itens que a avaliacao ja espera

Quando a lista abaixo nao estiver vazia, ela e a verdade: use exatamente esses itens em
vez de decompor o pedido por conta propria.

{expected_topics}

## Pedido

{task}

## Resposta a avaliar

{answer}

## Schema exigido

```json
{schema}
```
