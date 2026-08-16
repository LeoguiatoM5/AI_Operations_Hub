"""Testes da extracao de texto por formato."""

import json

import pytest

from app.rag.loaders import (
    DocumentExtractionError,
    EmptyDocumentError,
    UnsupportedDocumentError,
    extract_text,
)
from tests.pdf_builder import build_pdf

TEXTO = "Politica de reembolso.\nPrazo de 30 dias corridos."


# ---------------------------------------------------------------- texto e markdown


def test_reads_utf8_text() -> None:
    resultado = extract_text("Política de reembolso — 30 dias.".encode(), filename="a.txt")

    assert "Política" in resultado.text
    assert resultado.extension == ".txt"


def test_falls_back_to_other_encodings() -> None:
    """Arquivos legados do Windows costumam vir em cp1252, sem declarar codificacao."""
    resultado = extract_text("Reembolso em até 30 dias".encode("cp1252"), filename="a.txt")

    assert "at" in resultado.text


def test_strips_the_utf8_bom() -> None:
    """Bug real: ferramentas do Windows gravam BOM, e `utf-8` puro nao reclama dele.

    Decodificar com `utf-8` devolve a string com um U+FEFF invisivel no inicio. Sem
    excecao, `utf-8-sig` nunca seria alcancado e o BOM seguiria para o indice.
    """
    resultado = extract_text("Politica de reembolso.".encode("utf-8-sig"), filename="a.txt")

    assert not resultado.text.startswith("﻿")
    assert resultado.text.startswith("Politica")


def test_json_with_bom_is_parsed() -> None:
    """Foi assim que o defeito apareceu: `Set-Content -Encoding utf8` grava BOM."""
    conteudo = json.dumps({"prazo_dias": 30}).encode("utf-8-sig")

    resultado = extract_text(conteudo, filename="a.json")

    assert "prazo_dias: 30" in resultado.text


def test_reads_markdown() -> None:
    resultado = extract_text(b"# Titulo\n\nConteudo do documento.", filename="a.md")

    assert resultado.extension == ".md"
    assert "Conteudo" in resultado.text


def test_empty_text_file_is_rejected() -> None:
    with pytest.raises(EmptyDocumentError):
        extract_text(b"   \n\n  ", filename="a.txt")


# ---------------------------------------------------------------- json


def test_json_is_flattened_into_path_value_lines() -> None:
    """Indexar JSON cru gastaria o pedaco com chaves e colchetes em vez de conteudo."""
    conteudo = json.dumps(
        {"politicas": {"reembolso": {"prazo_dias": 30, "aprovador": "diretoria"}}}
    ).encode()

    resultado = extract_text(conteudo, filename="a.json")

    assert "politicas.reembolso.prazo_dias: 30" in resultado.text
    assert "politicas.reembolso.aprovador: diretoria" in resultado.text


def test_json_arrays_keep_their_position() -> None:
    conteudo = json.dumps({"passos": ["abrir chamado", "classificar"]}).encode()

    resultado = extract_text(conteudo, filename="a.json")

    assert "passos[0]: abrir chamado" in resultado.text
    assert "passos[1]: classificar" in resultado.text


def test_json_reports_how_many_fields_were_found() -> None:
    conteudo = json.dumps({"a": 1, "b": 2, "c": {"d": 3}}).encode()

    resultado = extract_text(conteudo, filename="a.json")

    assert resultado.metadata["json_fields"] == 3


def test_invalid_json_is_rejected_with_the_position() -> None:
    with pytest.raises(DocumentExtractionError, match="JSON invalido"):
        extract_text(b'{"aberto": ', filename="a.json")


def test_json_with_only_nulls_produces_no_text() -> None:
    with pytest.raises(EmptyDocumentError):
        extract_text(json.dumps({"a": None, "b": None}).encode(), filename="a.json")


# ---------------------------------------------------------------- pdf


def test_reads_every_page_of_a_pdf() -> None:
    pdf = build_pdf(["Primeira pagina sobre reembolso.", "Segunda pagina sobre ferias."])

    resultado = extract_text(pdf, filename="a.pdf")

    assert "reembolso" in resultado.text
    assert "ferias" in resultado.text
    assert resultado.metadata["pages"] == 2
    assert resultado.metadata["pages_with_text"] == 2


def test_counts_pages_without_extractable_text() -> None:
    pdf = build_pdf(["Pagina com texto.", ""])

    resultado = extract_text(pdf, filename="a.pdf")

    assert resultado.metadata["pages"] == 2
    assert resultado.metadata["pages_with_text"] == 1


def test_scanned_pdf_is_rejected_instead_of_indexed_empty() -> None:
    """Um PDF sem texto indexado em silencio nunca apareceria em busca alguma."""
    with pytest.raises(EmptyDocumentError, match="OCR"):
        extract_text(build_pdf(["", ""]), filename="escaneado.pdf")


def test_file_pretending_to_be_a_pdf_is_rejected() -> None:
    """A extensao e o que o usuario digitou; a assinatura e o que o arquivo e."""
    with pytest.raises(DocumentExtractionError, match="assinatura"):
        extract_text(b"isto e texto puro, nao um pdf", filename="falso.pdf")


def test_corrupted_pdf_is_rejected() -> None:
    pdf = bytearray(build_pdf(["Conteudo valido."]))
    del pdf[40:200]  # destroi a estrutura interna, preservando a assinatura

    with pytest.raises(DocumentExtractionError):
        extract_text(bytes(pdf), filename="corrompido.pdf")


# ---------------------------------------------------------------- extensao


@pytest.mark.parametrize("filename", ["planilha.xlsx", "imagem.png", "script.exe", "semextensao"])
def test_unsupported_extensions_are_rejected(filename: str) -> None:
    with pytest.raises(UnsupportedDocumentError) as exc_info:
        extract_text(TEXTO.encode(), filename=filename)

    assert ".pdf" in exc_info.value.details["supported"]


def test_extension_matching_is_case_insensitive() -> None:
    resultado = extract_text(TEXTO.encode(), filename="DOCUMENTO.TXT")

    assert resultado.extension == ".txt"
