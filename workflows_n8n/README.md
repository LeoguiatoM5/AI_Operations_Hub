# Workflows do n8n

O Hub e o cerebro; o n8n sao os bracos. Para o n8n nao ser decorativo, ele precisa
**disparar** o Hub e **receber** o resultado -- e a segunda metade e a dificil, porque uma
execucao que pausa para aprovacao humana termina horas depois, quando ninguem esta mais
segurando a resposta HTTP.

Por isso `aprovacao-de-acao.json` tem **dois triggers**:

```
POST /webhook/hub/solicitacao
   -> POST host.docker.internal:8000/agents/run
      -> status == waiting_approval ?  responde "aguardando pessoa"  (nada executado)
      -> senao                         responde o relatorio

   ... a pessoa decide em POST /approvals/{id}/approve ...

POST /webhook/hub/resultado   <- o Hub publica aqui (RESULT_CALLBACK_URL)
   -> extrai desfecho, custo e relatorio
```

## Subir

```powershell
docker compose up -d          # n8n em http://localhost:5678
docker compose logs -f n8n
docker compose down           # derruba preservando os dados
```

Os dados ficam em `data/n8n/` -- dentro do projeto e fora do Git.

## Importar o workflow

1. `http://localhost:5678` -> crie a conta local (fica so na sua maquina).
2. **Workflows** -> **Import from File** -> `workflows_n8n/aprovacao-de-acao.json`.
3. Clique em **Publish** (botao laranja, canto superior direito) e confirme. Nas versoes
   recentes do n8n e ele que ativa o workflow -- nao existe mais o toggle *Active*.

   **Enquanto nao publicar, os webhooks respondem 404.** O n8n so registra as URLs de
   producao na ativacao: antes disso o workflow existe no banco, aparece na lista e nao
   atende a nada. Para conferir, o `POST` do passo seguinte tem de devolver 200.
4. Copie a URL de producao do webhook **Resultado do Hub** e ponha no `.env` da API:

   ```
   RESULT_CALLBACK_URL=http://localhost:5678/webhook/hub/resultado
   ```

5. Reinicie o `uvicorn` -- a configuracao e lida no startup, de proposito (ED-002).

## Exercitar

Com a API rodando no host (`uvicorn app.main:app --reload`):

```powershell
$body = @{ task = "Avise o time no canal operacoes que ha tres chamados criticos." } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://localhost:5678/webhook/hub/solicitacao" `
  -ContentType "application/json" -Body $body
```

A resposta traz `approval_id` e **nenhuma mensagem foi enviada**. Confira em
`GET /approvals?status=pending`, depois aprove:

```powershell
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/approvals/<id>/approve" `
  -ContentType "application/json" -Body '{"decided_by":"leonardo"}'
```

Em **Executions**, no n8n, aparece uma segunda execucao -- disparada pelo callback, com o
desfecho e o custo. Ela e a prova de que o circuito fecha.

## Por que a API nao esta no compose

Enquanto o projeto esta em desenvolvimento, recarga automatica a cada edicao vale mais que
a simetria de ter tudo em container. No V7 a `api`, o `chromadb` e o `postgres` entram no
`docker-compose.yml` e o arranjo passa a ser o mesmo em dev e em producao.

## Versionar alteracoes

Editou o workflow na interface? Exporte por cima do arquivo (**...** -> **Download**) e
commite o JSON. Sem isso, a automacao existe apenas dentro do container -- e some no
primeiro `docker compose down -v`.
