"""Endpoints da base de conhecimento."""

from typing import Annotated

from fastapi import APIRouter, File, Path, Query, UploadFile, status

from app.api.deps import DocumentRepositoryDep, DocumentServiceDep, SettingsDep
from app.api.responses import with_errors
from app.core.exceptions import NotFoundError, ValidationError
from app.models.enums import DocumentStatus
from app.schemas.common import ErrorResponse
from app.schemas.document import (
    DocumentDeletedResponse,
    DocumentListResponse,
    DocumentResponse,
)

router = APIRouter(prefix="/documents", tags=["documents"])


def _error(code: int, description: str) -> dict[str, dict[str, object]]:
    return {str(code): {"model": ErrorResponse, "description": description}}


@router.post(
    "/upload",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ingere um arquivo na base de conhecimento",
    responses=with_errors(
        **_error(409, "Conteudo identico ja ingerido"),
        **_error(413, "Arquivo acima do limite"),
        **_error(415, "Formato nao suportado"),
    ),
)
async def upload_document(
    service: DocumentServiceDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File(description="Arquivo .txt, .md, .json ou .pdf")],
) -> DocumentResponse:
    """Extrai o texto, divide em trechos, vetoriza e indexa.

    A resposta so sai com `status: indexed` quando o documento ja participa da busca.
    Falha em qualquer etapa remove os trechos parciais e devolve o erro correspondente.
    """
    if not file.filename:
        raise ValidationError("O arquivo enviado nao tem nome.")

    # Le o arquivo inteiro apenas depois de o FastAPI ter aplicado o limite de corpo.
    # Arquivos grandes vao para disco temporario pelo proprio UploadFile, entao esta
    # leitura nao carrega gigabytes em memoria de uma vez sem controle.
    content = await file.read()
    if len(content) > settings.max_upload_bytes:
        raise ValidationError(
            f"Arquivo de {len(content)} bytes excede o limite de {settings.max_upload_bytes} bytes."
        )

    document = await service.ingest(content, filename=file.filename)
    return DocumentResponse.from_model(document)


@router.get(
    "",
    response_model=DocumentListResponse,
    summary="Lista documentos da base de conhecimento",
    responses=with_errors(),
)
async def list_documents(
    repository: DocumentRepositoryDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    status_filter: Annotated[DocumentStatus | None, Query(alias="status")] = None,
) -> DocumentListResponse:
    documents = await repository.list(limit=limit, offset=offset, status=status_filter)
    return DocumentListResponse(
        total=await repository.count(status=status_filter),
        limit=limit,
        offset=offset,
        indexed_chunks=await repository.total_chunks(),
        items=[DocumentResponse.from_model(item) for item in documents],
    )


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
    summary="Detalha um documento",
    responses=with_errors(**_error(404, "Documento nao encontrado")),
)
async def get_document(
    repository: DocumentRepositoryDep,
    document_id: Annotated[str, Path(min_length=1, max_length=64)],
) -> DocumentResponse:
    document = await repository.get(document_id)
    if document is None:
        raise NotFoundError("Documento nao encontrado.", details={"document_id": document_id})
    return DocumentResponse.from_model(document)


@router.delete(
    "/{document_id}",
    response_model=DocumentDeletedResponse,
    summary="Remove um documento e seus trechos do indice",
    responses=with_errors(**_error(404, "Documento nao encontrado")),
)
async def delete_document(
    service: DocumentServiceDep,
    document_id: Annotated[str, Path(min_length=1, max_length=64)],
) -> DocumentDeletedResponse:
    document = await service.delete(document_id)
    return DocumentDeletedResponse(
        document_id=document.id,
        filename=document.filename,
        chunks_removed=document.chunk_count,
    )
