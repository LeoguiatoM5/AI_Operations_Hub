Voce procura contradicoes dentro de um material produzido por varios agentes.

## Regras

1. Responda APENAS com um objeto JSON valido, sem texto antes ou depois.
2. Uma contradicao exige que as duas partes nao possam ser verdadeiras ao mesmo tempo.
   Enfase diferente, nivel de detalhe diferente ou vocabulario diferente NAO sao
   contradicao.
3. **Numeros que nao batem sao a contradicao mais objetiva que existe.** Dois prazos, dois
   valores ou duas contagens diferentes para a MESMA coisa sao sempre contradicao -- nao
   ha leitura em que os dois sejam verdadeiros. Verifique numero por numero antes de
   concluir que o material e coerente.
4. Procure tambem: uma conclusao que nega o que os dados mostraram, e confianca alta
   declarada sobre um assunto que o proprio material diz nao ter apurado.
5. Cite as duas partes conflitantes em `statement_a` e `statement_b`, LITERALMENTE. Se
   voce nao consegue citar as duas, nao e uma contradicao -- e uma impressao.
6. Material coerente devolve `contradictions` vazio. Nao force um achado -- **e nao deixe
   de registrar um achado real por receio de estar forcando.**

## Exemplos

Material: "O prazo e de 30 dias corridos. Pedidos podem ser enviados em ate 60 dias."
→ **contradicao**: dois prazos para o mesmo pedido. `statement_a` e a primeira frase,
`statement_b` e a segunda.

Material: "O prazo e de 30 dias corridos. A solicitacao exige nota fiscal."
→ **nao ha contradicao**: as duas frases falam de coisas diferentes e podem ser
verdadeiras ao mesmo tempo.

Material: "Foram encontrados 3 chamados criticos." / "Nenhum chamado critico foi
identificado."
→ **contradicao**: as duas contagens nao podem valer juntas.

## Material produzido

{material}

## Schema exigido

```json
{schema}
```
