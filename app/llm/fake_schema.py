"""Geracao de uma instancia valida a partir do JSON Schema embutido no prompt.

**Por que isto existe.** O `FakeLLMProvider` devolvia sempre o mesmo JSON, que nao
satisfazia schema nenhum. Isso bastava para a suite de testes -- que roteiriza cada
resposta -- e escondia um buraco: `python run_evals.py` com `LLM_PROVIDER=fake` reprovava
os 16 casos com `llm_response_format_error`. A promessa de "o projeto roda inteiro sem
chave de API" valia para subir a aplicacao, e nao para exercita-la.

A saida foi ensinar o provider falso a ler o schema que o proprio prompt carrega (todos os
agentes embutem `dump_schema(...)`) e sintetizar uma instancia que passa na validacao.

**O que isto NAO e.** Nao e um gerador de dados de teste de proposito geral, nem tenta
cobrir JSON Schema inteiro. Cobre o que os schemas deste projeto usam. Fora disso,
devolve o valor mais simples do tipo e deixa a validacao do Pydantic reclamar -- que e o
comportamento certo para um substituto de teste: falhar visivelmente em vez de fingir.
"""

import json
import re
from typing import Any

#: Bloco ```json ... ``` do prompt. Os agentes embutem o schema exatamente assim.
_FENCED_JSON = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)

#: Valor de confianca gerado. Baixo de proposito: varios modelos do projeto validam
#: coerencia entre confianca alta e conteudo (ED-028), e uma confianca alta sintetica
#: reprovaria em `AnalysisResult.high_confidence_requires_findings`.
_CONFIDENCE = 0.5

#: Um item por lista. Zero quebraria os validadores que exigem conteudo (evidencia de um
#: achado, limitacoes de um relatorio vazio); mais de um so gastaria espaco.
_ITEMS_PER_ARRAY = 1


def extract_schema(text: str) -> dict[str, Any] | None:
    """Encontra o JSON Schema embutido no prompt, se houver."""
    for bloco in _FENCED_JSON.findall(text):
        try:
            dados = json.loads(bloco)
        except ValueError:
            continue
        if isinstance(dados, dict) and ("properties" in dados or "$defs" in dados):
            return dados
    return None


def instance_for(schema: dict[str, Any]) -> dict[str, Any]:
    """Constroi um objeto que satisfaz o schema."""
    gerado = _build(schema, schema, "")
    # `_build` devolve `Any` porque atende qualquer tipo do schema. No topo, um schema de
    # objeto sempre produz dicionario -- e se nao produzir, e melhor devolver vazio e
    # deixar a validacao do Pydantic reclamar do que propagar algo inesperado.
    return gerado if isinstance(gerado, dict) else {}


def _resolve(node: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    """Segue `$ref` e escolhe um ramo de `anyOf`/`allOf`.

    Em `anyOf`, prefere o ramo que NAO e nulo: um campo opcional preenchido exercita mais
    do que um campo nulo, e o objetivo aqui e produzir uma resposta plausivel.
    """
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        alvo = root.get("$defs", {}).get(ref.removeprefix("#/$defs/"))
        if isinstance(alvo, dict):
            return _resolve(alvo, root)

    for chave in ("allOf", "anyOf", "oneOf"):
        ramos = node.get(chave)
        if isinstance(ramos, list) and ramos:
            candidatos = [r for r in ramos if isinstance(r, dict) and r.get("type") != "null"]
            escolhido = candidatos[0] if candidatos else ramos[0]
            if isinstance(escolhido, dict):
                combinado = {k: v for k, v in node.items() if k not in {"allOf", "anyOf", "oneOf"}}
                return _resolve({**combinado, **escolhido}, root)

    return node


def _build(node: dict[str, Any], root: dict[str, Any], nome: str) -> Any:
    """Valor que satisfaz `node`. `nome` e o campo, usado para heuristicas."""
    node = _resolve(node, root)

    if "const" in node:
        return node["const"]
    if node.get("enum"):
        return node["enum"][0]

    tipo = node.get("type")
    if isinstance(tipo, list):
        tipo = next((t for t in tipo if t != "null"), "string")

    if tipo == "object" or "properties" in node:
        propriedades: dict[str, Any] = node.get("properties", {})
        obrigatorios = set(node.get("required", []))
        # Preenche TODOS os campos conhecidos, e nao so os obrigatorios: campos opcionais
        # com validador de coerencia (ED-028) so sao exercitados se vierem preenchidos.
        return {
            chave: _build(sub, root, chave)
            for chave, sub in propriedades.items()
            if chave in obrigatorios or not _is_nullable(sub, root)
        }

    if tipo == "array":
        itens = node.get("items", {"type": "string"})
        minimo = int(node.get("minItems", 0))
        quantidade = max(minimo, _ITEMS_PER_ARRAY)
        maximo = node.get("maxItems")
        if isinstance(maximo, int):
            quantidade = min(quantidade, maximo)
        return [_build(itens, root, nome) for _ in range(quantidade)]

    if tipo == "boolean":
        return _boolean_for(nome)
    if tipo == "integer":
        return int(node.get("minimum", node.get("exclusiveMinimum", 0) or 0)) or 1
    if tipo == "number":
        return _number_for(nome, node)
    if tipo == "null":
        return None

    return _string_for(nome, node)


def _is_nullable(node: dict[str, Any], root: dict[str, Any]) -> bool:
    ramos = node.get("anyOf") or node.get("oneOf") or []
    return any(isinstance(r, dict) and r.get("type") == "null" for r in ramos)


def _boolean_for(nome: str) -> bool:
    """Escolhe o booleano que produz um objeto internamente coerente.

    `requires_approval` verdadeiro exigiria `automation` no plano
    (`TriageResult.approval_implies_an_automation_step`), e o gerador nao tem como saber
    disso pelo schema -- a regra vive num validador Python. Falso evita a contradicao.
    """
    return nome not in {"requires_approval", "supported", "rejected"}


def _number_for(nome: str, node: dict[str, Any]) -> float:
    if "confidence" in nome or "score" in nome:
        return _CONFIDENCE
    minimo = node.get("minimum", node.get("exclusiveMinimum"))
    return float(minimo) if isinstance(minimo, int | float) else 1.0


def _string_for(nome: str, node: dict[str, Any]) -> str:
    """Texto plausivel, respeitando o comprimento minimo declarado."""
    texto = f"[fake] {nome}" if nome else "[fake]"
    minimo = int(node.get("minLength", 0))
    if len(texto) < minimo:
        texto = texto.ljust(minimo, ".")
    maximo = node.get("maxLength")
    if isinstance(maximo, int) and len(texto) > maximo:
        texto = texto[:maximo]
    return texto
