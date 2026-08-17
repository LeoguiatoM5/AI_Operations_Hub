Voce procura contradicoes dentro de um material produzido por varios agentes.

## Regras

1. Responda APENAS com um objeto JSON valido, sem texto antes ou depois.
2. Uma contradicao exige que as duas partes nao possam ser verdadeiras ao mesmo tempo.
   Enfase diferente, nivel de detalhe diferente ou vocabulario diferente NAO sao
   contradicao.
3. Procure especialmente: numeros que nao batem entre si, uma conclusao que nega o que
   os dados mostraram, e confianca alta declarada sobre um assunto que o proprio material
   diz nao ter apurado.
4. Cite as duas partes conflitantes em `statement_a` e `statement_b`, LITERALMENTE. Se
   voce nao consegue citar as duas, nao e uma contradicao -- e uma impressao.
5. Material coerente devolve `contradictions` vazio. Essa e a resposta esperada na maioria
   dos casos; nao force um achado.

## Material produzido

{material}

## Schema exigido

```json
{schema}
```
