"""Gerador de PDFs minimos para teste.

Escrito a mao em vez de adicionar uma biblioteca de geracao de PDF so para os testes.
Sao cerca de quarenta linhas, e o formato e simples o bastante: cabecalho, objetos
numerados, tabela `xref` com o deslocamento em bytes de cada objeto, e o `trailer`.

O detalhe que costuma quebrar implementacoes caseiras e o `xref`: cada entrada precisa
ter exatamente vinte bytes, e os deslocamentos precisam ser calculados sobre o arquivo
ja montado -- por isso o conteudo e construido antes e as posicoes sao anotadas durante
a escrita.
"""

from collections.abc import Sequence


def _escape(text: str) -> str:
    """Escapa os caracteres com significado dentro de uma string PDF."""
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf(pages: Sequence[str]) -> bytes:
    """Monta um PDF valido com uma linha de texto por pagina.

    Passar uma string vazia produz uma pagina sem texto extraivel -- util para simular
    um documento escaneado, que exigiria OCR.
    """
    page_count = len(pages)
    page_ids = [3 + 2 * index for index in range(page_count)]
    content_ids = [4 + 2 * index for index in range(page_count)]
    font_id = 3 + 2 * page_count

    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: (
            "<< /Type /Pages /Kids ["
            + " ".join(f"{page_id} 0 R" for page_id in page_ids)
            + f"] /Count {page_count} >>"
        ).encode("ascii"),
        font_id: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }

    for index, text in enumerate(pages):
        objects[page_ids[index]] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Contents {content_ids[index]} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>"
        ).encode("ascii")

        stream = f"BT /F1 12 Tf 72 720 Td ({_escape(text)}) Tj ET".encode("latin-1")
        objects[content_ids[index]] = (
            b"<< /Length "
            + str(len(stream)).encode("ascii")
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        )

    output = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for object_id in sorted(objects):
        offsets[object_id] = len(output)
        output += f"{object_id} 0 obj\n".encode("ascii") + objects[object_id] + b"\nendobj\n"

    xref_offset = len(output)
    last_id = max(objects)

    output += f"xref\n0 {last_id + 1}\n".encode("ascii")
    output += b"0000000000 65535 f \n"  # entrada obrigatoria do objeto zero
    for object_id in range(1, last_id + 1):
        output += f"{offsets[object_id]:010d} 00000 n \n".encode("ascii")

    output += (
        f"trailer\n<< /Size {last_id + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
    ).encode("ascii")

    return bytes(output)
