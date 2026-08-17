"""Saidas do sistema que NAO sao escolhidas por um agente.

A diferenca para `app/tools/` e quem decide. Uma ferramenta e escolhida pelo agente de
automacao, declara escopo e pode exigir aprovacao humana. O que vive aqui e emitido pela
propria aplicacao ao final de um ciclo -- ninguem aprova um callback, porque ele nao age
sobre o mundo, so conta o que ja aconteceu.
"""
