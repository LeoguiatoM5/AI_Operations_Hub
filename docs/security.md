# Postura de seguranca

O que este projeto faz para se defender, o que ele conscientemente nao faz, e as
vulnerabilidades conhecidas que continuam abertas.

## Vulnerabilidades conhecidas e sem correcao

Encontradas por `docker scout` sobre a imagem de producao, em 2026-08-17. As duas
continuam abertas porque **nao existe versao corrigida**, e nao porque ninguem atualizou.

### chromadb 1.5.9 — CVE-2026-45829 (critica, injecao de codigo)

Afeta `>=1.0.0, <=1.5.9`. A 1.5.9 e a ultima versao publicada: **nao ha para onde
atualizar**.

O que reduz a exposicao aqui:

- o Chroma roda **embutido** (`PersistentClient`), lendo e escrevendo um diretorio. Nao ha
  porta, nao ha servidor, nao ha superficie de rede;
- por isso o `docker-compose.yml` **nao** sobe um servico `chromadb`, ainda que o roadmap
  original previsse um. Subir seria acrescentar uma porta de rede a uma dependencia com
  injecao de codigo em aberto.

O que a arquitetura garante caso isso mude de figura:

- `VectorStore` e um Protocol com duas implementacoes e teste de contrato. Trocar o Chroma
  e uma variavel de ambiente (`VECTOR_STORE=memory`) ou uma classe nova -- nao uma
  reescrita. **A abstracao que existia por gosto de projeto virou plano de contingencia.**

Gatilho para reavaliar: publicacao de uma versao corrigida, ou necessidade de expor o
Chroma em rede.

### perl-base 5.40.1-6 — 2 criticas e 2 altas

Vem da imagem base `python:3.12-slim` (Debian trixie). O Debian ainda nao publicou
correcao.

- `perl-base` e pacote **Essential**: remove-lo quebra o `dpkg` e nao e suportado;
- a aplicacao **nunca invoca perl** -- nao ha `subprocess`, `os.system` nem shell-out em
  nenhum caminho de codigo;
- explorar exigiria ja ter execucao de codigo dentro do container, momento em que o perl e
  o menor dos problemas.

Caminho se virar requisito: base distroless, ao custo de perder o gerenciador de pacotes e
o shell -- o que tambem dificulta o diagnostico.

## O que a imagem faz para se defender

| Medida | Por que |
|---|---|
| Multi-estagio | O compilador fica no estagio de build. Ferramenta de compilacao numa imagem de producao e ferramenta a disposicao de quem conseguir executar codigo la dentro. |
| Processo como `aiops` (uid 1000) | Container rodando como root deixa um escape de container a uma unica falha de virar root na maquina. |
| **Codigo pertence ao root** | O processo le o proprio codigo e nao consegue reescreve-lo. Quem obtiver execucao nao consegue persistir alteracao no programa. |
| So `data/` e gravavel | E a unica coisa que a aplicacao precisa escrever. |
| `.env` no `.dockerignore` | Camada de imagem nao se apaga: uma chave copiada para dentro viaja para qualquer registry. Os segredos entram como variavel de ambiente em tempo de execucao. |

## O que o codigo faz

- **Segredos como `SecretStr`** (ED-014): chave da OpenAI e URL do webhook do Slack nao
  aparecem em `repr()`, log ou dump de configuracao.
- **A URL do webhook nunca entra em `details` de erro** (ED-060). `AIHubError.details` sai
  no corpo da resposta da API -- publicar a credencial ali seria entrega-la a quem
  provocou o erro.
- **Argumento gerado por LLM nao chega a sistema externo sem validacao** de schema
  (`ToolRegistry.validate_input`).
- **Nenhuma acao de escrita sem aprovacao humana** (V4), e o servidor MCP nao tem
  ferramenta para aprovar (ED-081): a IA nao autoriza a propria acao.
- **Correlation ID sanitizado**: um identificador com quebra de linha poderia forjar
  entradas falsas no arquivo de log.

## O que este projeto NAO faz, por decisao

**Nao ha autenticacao na API.** E projeto de portfolio, roda em `127.0.0.1`. Expor em rede
exigiria, no minimo, chave de API em header e limite de requisicoes -- e o `decided_by`
das aprovacoes, hoje texto livre, passaria a vir da identidade do request.

**Nao ha criptografia em repouso.** O SQLite e o indice do Chroma ficam em claro no
volume. Para dado corporativo real, o volume precisaria de criptografia de disco.

**A CI nao tem segredo nenhum**, o que e escolha e nao limitacao: o provider `fake`
permite rodar a suite inteira e o conjunto de avaliacao sem chave. Uma CI que depende de
segredo nao roda em pull request externo -- exatamente onde a verificacao mais importa.

## Como verificar

```powershell
docker build -t aiops-api:dev .
docker scout cves --only-severity critical,high aiops-api:dev
```

Na CI, o `trivy` roda a cada build e o `gitleaks` varre o historico completo -- uma chave
vazada e depois removida do arquivo continua nos commits antigos.
