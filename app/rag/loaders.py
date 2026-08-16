"""Extracao de texto a partir de arquivos enviados.

Cada formato tem uma armadilha propria:

- texto e markdown: codificacao nao declarada;
- JSON: estrutura que, serializada crua, gasta tokens com chaves e colchetes em vez de
  conteudo;
- PDF: paginas sem texto extraivel (documento escaneado), que produziriam um documento
  indexado e vazio.
"""

import json
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import PurePath
from typing import Any

from pypdf import PdfReader

from app.core.exceptions import AIHubError
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Extensoes aceitas na ingestao.
SUPPORTED_EXTENSIONS = frozenset({".txt", ".md", ".markdown", ".json", ".pdf"})

#: Assinatura inicial de um PDF valido.
PDF_MAGIC = b"%PDF-"

#: Codificacoes tentadas em ordem para arquivos de texto.
#:
#: `utf-8-sig` vem ANTES de `utf-8`, e a ordem nao e detalhe. Decodificar um arquivo com
#: BOM usando `utf-8` puro NAO levanta erro: devolve a string com um caractere invisivel
#: U+FEFF grudado no inicio. Sem excecao, nenhuma codificacao seguinte seria tentada, e o
#: BOM seguiria adiante -- quebrando o parse de JSON e sujando o primeiro trecho indexado.
#: `utf-8-sig` remove o BOM quando existe e se comporta como `utf-8` quando nao existe.
#:
#: Ferramentas do Windows (Bloco de Notas, Excel, `Set-Content`) gravam BOM por padrao,
#: entao isto atinge boa parte dos arquivos que um usuario corporativo envia.
TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")

#: Marca de ordem de bytes, como caractere ja decodificado.
BOM_CHARACTER = "﻿"


class UnsupportedDocumentError(AIHubError):
    """Formato de arquivo nao suportado."""

    code = "unsupported_document"
    http_status = 415
    default_message = "Formato de arquivo nao suportado."


class DocumentExtractionError(AIHubError):
    """O arquivo tem o formato certo mas nao foi possivel ler seu conteudo."""

    code = "document_extraction_failed"
    http_status = 422
    default_message = "Nao foi possivel extrair texto do arquivo."


class EmptyDocumentError(AIHubError):
    """O arquivo foi lido, mas nao produziu texto algum."""

    code = "empty_document"
    http_status = 422
    default_message = (
        "O arquivo nao produziu texto extraivel. PDFs escaneados exigem OCR, "
        "que este sistema nao faz."
    )


@dataclass(frozen=True)
class ExtractedDocument:
    """Texto extraido e o que se aprendeu sobre o arquivo no caminho."""

    text: str
    extension: str
    metadata: dict[str, Any] = field(default_factory=dict)


def normalize_extension(filename: str) -> str:
    return PurePath(filename).suffix.lower()


def _decode_text(content: bytes) -> str:
    """Decodifica tentando as codificacoes mais comuns, em ordem.

    Nao ha como saber a codificacao de um arquivo de texto pelo conteudo com certeza --
    `latin-1` fica por ultimo porque decodifica qualquer sequencia de bytes sem erro, e
    portanto sempre "funciona", ainda que produzindo caracteres errados.
    """
    for encoding in TEXT_ENCODINGS:
        try:
            decoded = content.decode(encoding)
        except UnicodeDecodeError:
            continue
        # Rede de seguranca: BOM em UTF-16 convertido, ou BOM no meio de um arquivo
        # concatenado, continuaria passando pelas codificacoes acima.
        return decoded.lstrip(BOM_CHARACTER)
    raise DocumentExtractionError("Nao foi possivel determinar a codificacao do arquivo.")


def _flatten_json(value: Any, prefix: str = "") -> list[str]:
    """Transforma JSON em linhas "caminho: valor".

    Indexar o JSON cru desperdicaria boa parte do pedaco com chaves, aspas e colchetes,
    e o embedding acabaria representando a estrutura em vez do conteudo. O caminho e
    mantido porque ele carrega significado: `politicas.reembolso.prazo_dias: 30` diz
    muito mais que `30`.
    """
    if isinstance(value, dict):
        linhas: list[str] = []
        for key, item in value.items():
            caminho = f"{prefix}.{key}" if prefix else str(key)
            linhas.extend(_flatten_json(item, caminho))
        return linhas
    if isinstance(value, list):
        linhas = []
        for position, item in enumerate(value):
            linhas.extend(_flatten_json(item, f"{prefix}[{position}]"))
        return linhas
    if value is None:
        return []
    return [f"{prefix}: {value}" if prefix else str(value)]


def _extract_json(content: bytes) -> ExtractedDocument:
    raw = _decode_text(content)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise DocumentExtractionError(f"JSON invalido: {error.msg} (linha {error.lineno}).") from (
            error
        )

    linhas = _flatten_json(parsed)
    return ExtractedDocument(
        text="\n".join(linhas),
        extension=".json",
        metadata={"json_fields": len(linhas)},
    )


def _extract_pdf(content: bytes) -> ExtractedDocument:
    if not content.startswith(PDF_MAGIC):
        raise DocumentExtractionError(
            "O arquivo tem extensao .pdf mas nao comeca com a assinatura de um PDF."
        )

    try:
        reader = PdfReader(BytesIO(content))
        paginas = [page.extract_text() or "" for page in reader.pages]
    except Exception as error:
        # Captura ampla deliberada. Estamos numa fronteira de confianca: arquivo
        # arbitrario enviado por terceiro, processado por biblioteca de terceiro. Um PDF
        # corrompido faz o pypdf levantar de PdfReadError a AttributeError, dependendo de
        # qual estrutura interna quebrou -- e todas significam a mesma coisa para o
        # usuario. Sem isto, um arquivo malformado viraria 500 em vez de 422.
        raise DocumentExtractionError(f"PDF ilegivel: {type(error).__name__}: {error}") from error

    com_texto = sum(1 for pagina in paginas if pagina.strip())
    return ExtractedDocument(
        text="\n\n".join(pagina for pagina in paginas if pagina.strip()),
        extension=".pdf",
        metadata={"pages": len(paginas), "pages_with_text": com_texto},
    )


def extract_text(content: bytes, *, filename: str) -> ExtractedDocument:
    """Extrai o texto de um arquivo enviado.

    Raises:
        UnsupportedDocumentError: extensao fora da lista suportada.
        DocumentExtractionError: arquivo corrompido ou ilegivel.
        EmptyDocumentError: leitura bem-sucedida, mas sem texto algum.
    """
    extension = normalize_extension(filename)
    if extension not in SUPPORTED_EXTENSIONS:
        raise UnsupportedDocumentError(
            f"Extensao {extension or '(ausente)'} nao suportada.",
            details={"supported": sorted(SUPPORTED_EXTENSIONS)},
        )

    if extension == ".json":
        extracted = _extract_json(content)
    elif extension == ".pdf":
        extracted = _extract_pdf(content)
    else:
        extracted = ExtractedDocument(text=_decode_text(content), extension=extension)

    if not extracted.text.strip():
        # Falhar aqui e essencial: um documento indexado sem texto nunca aparece em
        # busca alguma, e o usuario nao teria como saber por que.
        raise EmptyDocumentError(details={"filename": filename, **extracted.metadata})

    logger.info(
        "document_extracted",
        filename=filename,
        extension=extension,
        characters=len(extracted.text),
        **extracted.metadata,
    )
    return extracted
