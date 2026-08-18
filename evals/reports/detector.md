# Sensibilidade do detector

Respostas com defeito **conhecido**, escritas a mao. Mede o motor de qualidade,
e nao o sistema: nenhuma passa pelo `RagService`.

- **Defeitos detectados:** 7/7
- **Controles aprovados:** 1/1
- **Total:** 8/8
- **Custo:** US$ 0.004283

| Caso | Defeito | Nota da dimensao | Agregado | Detectou? |
|---|---|---|---|---|
| `controle-resposta-boa` | controle | -- | 1.00 | sim |
| `grounding-inventado` | grounding | 0.00 | 0.64 | sim |
| `grounding-numero-trocado` | grounding | 0.00 | 0.64 | sim |
| `grounding-metade-sustentada` | grounding | 0.50 | 0.64 | sim |
| `relevance-fora-do-assunto` | relevance | 0.00 | 0.55 | sim |
| `completeness-metade-do-pedido` | completeness | 0.50 | 0.64 | sim |
| `consistency-contradiz-a-si-mesma` | consistency | 0.66 | 0.76 | sim |
| `grounding-sem-fonte-alguma` | grounding | 0.00 | 0.45 | sim |

## Onde o limite pode ficar

- pior resposta **boa**: 1.00
- melhor resposta **ruim**: 0.76

As faixas **nao se sobrepoem**. Qualquer limite entre 0.76 e 1.00 separa as duas; o meio da faixa e 0.88.
