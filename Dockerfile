# Imagem da API. Multi-estagio: o que compila fica no primeiro estagio e nao viaja.
#
# O ganho nao e so tamanho. Compilador dentro de uma imagem de producao e ferramenta a
# disposicao de quem conseguir executar codigo la dentro -- e a superficie de ataque some
# junto com os megabytes.

# ---------------------------------------------------------------- estagio de build
FROM python:3.12-slim AS builder

# Alguns pacotes (pypdf, dependencias nativas do chromadb) trazem extensoes em C sem
# roda pronta para todas as plataformas. Estas ferramentas existem so aqui.
RUN apt-get update && apt-get install --no-install-recommends -y \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# O ambiente virtual e copiado inteiro para o estagio final. E mais simples e mais
# previsivel que reinstalar la: o que foi testado aqui e exatamente o que roda.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copiado sozinho, ANTES do codigo: a camada de dependencias so e refeita quando o
# pyproject muda. Editar um arquivo de `app/` nao dispara uma reinstalacao inteira.
COPY pyproject.toml README.md ./
COPY app/__init__.py ./app/
RUN pip install --no-cache-dir .

# ---------------------------------------------------------------- estagio final
FROM python:3.12-slim AS runtime

# Usuario sem privilegio. Container que roda como root deixa um escape de container a uma
# unica falha de distancia de ser root na maquina.
RUN useradd --create-home --uid 1000 aiops

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# O codigo fica de ROOT, e o processo roda como `aiops`. Assim a aplicacao le o proprio
# codigo e nao consegue reescreve-lo: quem obtiver execucao dentro do container nao
# consegue persistir uma alteracao no proprio programa. Copiar com `--chown=aiops` seria
# entregar a chave junto com a fechadura.
COPY app ./app
COPY mcp_server ./mcp_server
COPY evals ./evals
COPY run_evals.py ./

# `data/` e a UNICA coisa que a aplicacao escreve, e por isso a unica que lhe pertence.
# No compose ela e volume; criada aqui para o caso de a imagem rodar sem volume montado.
RUN mkdir -p /app/data && chown aiops:aiops /app/data

USER aiops

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Dentro do container o servidor precisa escutar em todas as interfaces; 127.0.0.1
    # (o padrao, correto no host) o tornaria inalcancavel de fora.
    API_HOST=0.0.0.0

EXPOSE 8000

# Usa o proprio /health, que ja existe e ja diz mais que "o processo esta vivo": responde
# ambiente, versao e qual canal de saida esta ativo.
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=4).status == 200 else 1)"

# Sem --reload: recarga automatica e ferramenta de desenvolvimento, e observar arquivos
# dentro de um container custa CPU sem entregar nada.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
