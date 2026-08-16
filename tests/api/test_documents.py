"""Testes dos endpoints da base de conhecimento."""

import json

from httpx import AsyncClient

from tests.pdf_builder import build_pdf

POLITICA = (
    b"Politica de reembolso. Solicitacoes em ate 30 dias corridos apos o gasto. "
    b"A analise leva 5 dias uteis."
)


def arquivo(nome: str, conteudo: bytes, tipo: str = "text/plain") -> dict[str, object]:
    return {"file": (nome, conteudo, tipo)}


# ---------------------------------------------------------------- upload


async def test_uploads_a_text_file(client: AsyncClient) -> None:
    response = await client.post("/documents/upload", files=arquivo("politica.txt", POLITICA))

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "indexed"
    assert body["filename"] == "politica.txt"
    assert body["extension"] == ".txt"
    assert body["chunk_count"] > 0
    assert len(body["content_hash"]) == 64
    assert body["indexed_at"] is not None


async def test_uploads_a_pdf(client: AsyncClient) -> None:
    pdf = build_pdf(["Politica de reembolso em ate 30 dias.", "Segunda pagina."])

    response = await client.post(
        "/documents/upload", files=arquivo("politica.pdf", pdf, "application/pdf")
    )

    assert response.status_code == 201
    body = response.json()
    assert body["extension"] == ".pdf"
    assert body["metadata"]["pages"] == 2


async def test_uploads_json(client: AsyncClient) -> None:
    conteudo = json.dumps({"reembolso": {"prazo_dias": 30}}).encode()

    response = await client.post(
        "/documents/upload", files=arquivo("dados.json", conteudo, "application/json")
    )

    assert response.status_code == 201
    assert response.json()["metadata"]["json_fields"] == 1


async def test_identical_content_returns_conflict(client: AsyncClient) -> None:
    await client.post("/documents/upload", files=arquivo("a.txt", POLITICA))

    response = await client.post("/documents/upload", files=arquivo("copia.txt", POLITICA))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "duplicate_document"
    assert "document_id" in response.json()["error"]["details"]


async def test_unsupported_format_returns_415(client: AsyncClient) -> None:
    response = await client.post("/documents/upload", files=arquivo("planilha.xlsx", b"dados"))

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_document"


async def test_oversized_file_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/documents/upload", files=arquivo("grande.txt", b"x" * (64 * 1024 + 1))
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_empty_file_is_rejected(client: AsyncClient) -> None:
    response = await client.post("/documents/upload", files=arquivo("vazio.txt", b""))

    assert response.status_code == 422


async def test_scanned_pdf_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/documents/upload",
        files=arquivo("escaneado.pdf", build_pdf(["", ""]), "application/pdf"),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "empty_document"


async def test_missing_file_returns_422(client: AsyncClient) -> None:
    response = await client.post("/documents/upload")

    assert response.status_code == 422


# ---------------------------------------------------------------- listagem


async def test_lists_documents_with_chunk_totals(client: AsyncClient) -> None:
    await client.post("/documents/upload", files=arquivo("a.txt", POLITICA))
    await client.post("/documents/upload", files=arquivo("b.txt", b"Politica de ferias."))

    body = (await client.get("/documents")).json()

    assert body["total"] == 2
    assert body["indexed_chunks"] >= 2
    assert {item["filename"] for item in body["items"]} == {"a.txt", "b.txt"}


async def test_filters_by_status(client: AsyncClient) -> None:
    await client.post("/documents/upload", files=arquivo("a.txt", POLITICA))

    indexados = (await client.get("/documents", params={"status": "indexed"})).json()
    falhos = (await client.get("/documents", params={"status": "failed"})).json()

    assert indexados["total"] == 1
    assert falhos["total"] == 0


async def test_rejects_invalid_status_filter(client: AsyncClient) -> None:
    response = await client.get("/documents", params={"status": "inventado"})

    assert response.status_code == 422


# ---------------------------------------------------------------- detalhe e remocao


async def test_detail_returns_the_document(client: AsyncClient) -> None:
    document_id = (await client.post("/documents/upload", files=arquivo("a.txt", POLITICA))).json()[
        "document_id"
    ]

    body = (await client.get(f"/documents/{document_id}")).json()

    assert body["document_id"] == document_id
    assert body["status"] == "indexed"


async def test_unknown_document_returns_404(client: AsyncClient) -> None:
    response = await client.get("/documents/naoexiste")

    assert response.status_code == 404
    assert response.json()["error"]["details"] == {"document_id": "naoexiste"}


async def test_deletes_a_document(client: AsyncClient) -> None:
    upload = (await client.post("/documents/upload", files=arquivo("a.txt", POLITICA))).json()

    response = await client.delete(f"/documents/{upload['document_id']}")

    assert response.status_code == 200
    assert response.json()["chunks_removed"] == upload["chunk_count"]
    assert (await client.get("/documents")).json()["total"] == 0


async def test_deleting_an_unknown_document_returns_404(client: AsyncClient) -> None:
    response = await client.delete("/documents/naoexiste")

    assert response.status_code == 404


async def test_content_can_be_uploaded_again_after_deletion(client: AsyncClient) -> None:
    upload = (await client.post("/documents/upload", files=arquivo("a.txt", POLITICA))).json()
    await client.delete(f"/documents/{upload['document_id']}")

    response = await client.post("/documents/upload", files=arquivo("a.txt", POLITICA))

    assert response.status_code == 201
