# Relatorio de avaliacao

**Gerado em:** 2026-08-17 20:30:49 UTC

| Configuracao | Valor |
|---|---|
| LLM | `openai` / `gpt-4o-mini` |
| Embeddings | `fake` / `fake-embedding-1` |
| Corte de relevancia | 0.05 |
| Dimensoes por LLM | sim |

> **Atencao.** Esta rodada usou embeddings falsos, que comparam palavras e nao
> significado. Os numeros medem o encanamento -- que o fluxo roda, que as
> assercoes sao avaliadas -- e **nao** a qualidade da recuperacao. Para medir
> qualidade, rode com `EMBEDDING_PROVIDER=openai`.

## Resumo

- **Casos:** 16
- **Assercoes aprovadas:** 16/16 (100%)
- **Custo da rodada:** US$ 0.008433

### Media por dimensao

| Dimensao | Media |
|---|---|
| `completeness` | 0.900 |
| `consistency` | 1.000 |
| `grounding` | 0.800 |
| `relevance` | 0.900 |

## Casos

| Caso | Assercoes | Nota | Observacao |
|---|---|---|---|
| `reembolso-prazo` | ok | 1.00 |  |
| `reembolso-documentos` | ok | 0.82 |  |
| `reembolso-prazo-e-documentos` | ok | 1.00 |  |
| `ferias-periodo-aquisitivo` | ok | 1.00 |  |
| `ferias-divisao` | ok | 1.00 |  |
| `senha-expiracao` | ok | 1.00 |  |
| `senha-sms` | ok | 0.36 |  |
| `chamado-critico-prazo` | ok | 1.00 |  |
| `chamado-vocabulario-desalinhado` | ok | 1.00 |  |
| `remoto-dias-presenciais` | ok | 1.00 |  |
| `remoto-exterior` | ok | 0.64 |  |
| `ausente-plano-de-saude` | ok | 1.00 |  |
| `ausente-salario` | ok | 1.00 |  |
| `ausente-fora-de-dominio` | ok | 1.00 |  |
| `pedido-vago` | ok | 1.00 |  |
| `armadilha-inferencia` | ok | 1.00 |  |
