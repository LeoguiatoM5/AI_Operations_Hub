"""Configuracao da aplicacao.

Toda configuracao entra por variavel de ambiente e e validada na inicializacao.
Se um valor for invalido, o processo falha ao subir -- nao no meio de um request.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
LogFormat = Literal["console", "json"]
LLMProviderName = Literal["fake", "openai"]
EmbeddingProviderName = Literal["fake", "openai"]
VectorStoreName = Literal["memory", "chroma"]
NotifierName = Literal["memory", "slack"]


class Settings(BaseSettings):
    """Configuracao tipada, carregada de variaveis de ambiente e do arquivo .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # aplicacao
    app_name: str = "AI Operations Hub"
    app_env: Environment = "local"
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    # logs
    log_level: LogLevel = "INFO"
    log_format: LogFormat = "console"

    # LLM
    # O padrao e "fake" de proposito: quem clona o repositorio consegue subir e usar a
    # aplicacao sem possuir nenhuma chave de API, e o CI roda sem segredos.
    llm_provider: LLMProviderName = "fake"
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    llm_max_output_tokens: int = Field(default=1024, gt=0, le=32_000)
    llm_timeout_seconds: float = Field(default=30.0, gt=0)
    llm_max_attempts: int = Field(default=3, ge=1, le=10)
    llm_retry_base_delay_seconds: float = Field(default=0.5, ge=0)

    # SecretStr impede que a chave apareca em repr(), str() ou em um log de debug
    # que despeje o objeto de configuracao inteiro.
    openai_api_key: SecretStr | None = None

    # RAG: embeddings
    embedding_provider: EmbeddingProviderName = "fake"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = Field(default=1536, gt=0, le=4096)
    #: O provedor falso usa vetores curtos: 256 posicoes bastam para separar assuntos em
    #: uma base pequena, e cada operacao fica seis vezes mais barata em CPU.
    embedding_dimensions_fake: int = Field(default=256, gt=0, le=4096)

    # RAG: banco vetorial
    vector_store: VectorStoreName = "chroma"
    chroma_path: str = "./data/chroma"
    #: O Chroma exige 3 a 512 caracteres de [a-zA-Z0-9._-], comecando e terminando em
    #: alfanumerico. Validar aqui faz a aplicacao recusar subir com um nome invalido, em
    #: vez de quebrar na primeira indexacao com uma mensagem vinda das entranhas da
    #: biblioteca.
    chroma_collection: str = Field(
        default="knowledge_base",
        min_length=3,
        max_length=512,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*[a-zA-Z0-9]$",
    )

    # RAG: recuperacao
    chunk_size: int = Field(default=1000, gt=0, le=8000)
    chunk_overlap: int = Field(default=150, ge=0, le=2000)
    rag_top_k: int = Field(default=5, ge=1, le=50)
    #: Corte de relevancia. `None` (padrao) usa o piso declarado pelo provedor de
    #: embedding -- a escala de similaridade depende do modelo, entao um valor unico aqui
    #: rejeitaria quase tudo com um provedor e nada com outro. Preencha apenas para
    #: sobrescrever conscientemente.
    rag_min_score: float | None = Field(default=None, ge=0.0, le=1.0)

    # upload
    #: Teto de tamanho por arquivo. Limita consumo de memoria, tempo de processamento e
    #: custo de vetorizacao de uma unica requisicao.
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, gt=0)

    # banco de dados
    # O caminho relativo mantem o arquivo dentro do projeto (e fora do Git, via
    # .gitignore). Trocar para PostgreSQL e mudar apenas esta URL.
    database_url: str = "sqlite+aiosqlite:///./data/app.db"
    database_echo: bool = False

    #: Arquivo dos checkpoints do grafo. Separado do banco da aplicacao porque tem outro
    #: dono (a biblioteca), outro ciclo de vida e pode ser descartado sem perder historico.
    checkpoint_path: str = "./data/checkpoints.db"

    # notificacoes
    #: Canal de saida das acoes de escrita. O padrao guarda a mensagem em memoria em vez
    #: de envia-la: quem clona o repositorio consegue exercitar o fluxo de aprovacao
    #: inteiro -- inclusive ver o que "foi enviado" -- sem configurar integracao nenhuma.
    notifier: NotifierName = "memory"
    #: A URL do webhook E a credencial: quem a possui publica no canal. `SecretStr`
    #: impede que ela apareca em log, em repr() ou num dump da configuracao.
    slack_webhook_url: SecretStr | None = None
    #: Rotulo do canal configurado no webhook, usado apenas no comprovante de entrega. O
    #: Slack nao informa qual e o destino: quem sabe e quem criou a integracao.
    slack_destination: str = Field(default="slack", min_length=1, max_length=64)
    notifier_timeout_seconds: float = Field(default=10.0, gt=0)

    # portao de qualidade
    #: Desligado por padrao, por dois motivos independentes -- e cada um bastaria.
    #:
    #: 1. **Custo.** O portao roda quatro chamadas de LLM por execucao. Liga-lo por
    #:    padrao dobraria a conta de quem clona o repositorio sem ter pedido nada.
    #: 2. **Os limites ainda nao foram medidos.** Reprovar respostas reais com base em
    #:    numeros arbitrados e pior que nao avaliar: produz recusa sem fundamento. A
    #:    calibracao sai do conjunto de avaliacao (V5.4).
    #:
    #: Para observar sem reprovar, ligue com `QUALITY_THRESHOLD=0.0`: tudo passa, as notas
    #: sao gravadas, e da para acumular medicao antes de ligar o portao de verdade.
    quality_enabled: bool = False
    #: **0.85, medido** -- o valor anterior (0.7) era arbitrado, e a medicao mostrou que
    #: ele estava ERRADO: duas respostas comprovadamente ruins pontuaram 0.76, acima dele.
    #:
    #: Faixas observadas (`evals/reports/`):
    #:
    #: - respostas boas .. 0.91 a 1.00  (16 casos reais + 1 controle)
    #: - respostas ruins .. 0.39 a 0.76  (7 defeitos escritos a mao)
    #:
    #: 0.85 fica na lacuna, com 0.09 de folga sobre a pior ruim e 0.06 sob a melhor boa.
    #: As folgas sao reais e nao orcamento de ruido: a variacao do juiz entre rodadas
    #: repetidas foi medida em 0.000 (ED-077).
    #:
    #: Continua sendo uma PRIMEIRA calibracao. Os defeitos do conjunto sao deliberadamente
    #: grosseiros; uma resposta ruim mais sutil pode pontuar acima de 0.76 e passar. O
    #: caminho para melhorar e mais casos, e nao outro numero.
    quality_threshold: float = Field(default=0.85, ge=0.0, le=1.0)

    # callback de resultado
    #: Para onde enviar o resultado de uma execucao que foi retomada apos aprovacao
    #: humana. Vazio desliga o callback -- a presenca da URL e o proprio seletor.
    result_callback_url: str | None = None
    result_callback_timeout_seconds: float = Field(default=10.0, gt=0)

    @model_validator(mode="after")
    def overlap_must_be_smaller_than_chunk(self) -> "Settings":
        """Sobreposicao maior ou igual ao pedaco geraria divisao infinita."""
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"CHUNK_OVERLAP ({self.chunk_overlap}) precisa ser menor que "
                f"CHUNK_SIZE ({self.chunk_size})."
            )
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def docs_enabled(self) -> bool:
        """Swagger fica exposto apenas fora de producao."""
        return not self.is_production


@lru_cache
def get_settings() -> Settings:
    """Instancia unica de Settings.

    O cache existe para que ler configuracao nao releia o disco a cada request,
    e para que o mesmo objeto seja compartilhado por toda a aplicacao.
    Em testes, use `get_settings.cache_clear()` apos alterar variaveis de ambiente.
    """
    return Settings()
