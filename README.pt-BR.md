# VibeContext

*[English](README.md) · [Português](README.pt-BR.md)*

**O contexto que o seu código não tem, pesquisável de dentro do Claude Code.**

A regra de reembolso está numa spec de produto. A decisão de cortar uma feature está na
ata da reunião de terça. Os limites da API do parceiro estão num PDF que alguém mandou por
email. O Claude lê o seu repositório e nada disso, então ele chuta, ou pergunta, ou
implementa com toda a confiança uma política que ninguém combinou.

O VibeContext é um plugin do [Claude Code](https://claude.com/claude-code) que indexa esses
documentos na sua máquina e dá ao Claude uma ferramenta de busca sobre eles. Você anexa
arquivos a uma sessão específica ou a todas; o Claude busca quando a tarefa depende de algo
que o código não explica.

```shell
/plugin marketplace add carvalhxlucas/vibe-context
/plugin install vibe-context@vibe-context
```

---

## Como funciona

```
 Sessão do Claude Code ──hooks──▶ SQLite (sessões)         Dashboard (localhost)
        │                                                      │ anexar arquivos
        │ search_context (MCP)                                 ▼
        ▼                                        ┌──────── Backend FastAPI ────────┐
   Servidor MCP ──token──▶ 127.0.0.1:8765 ───────┤ parse → chunk → embed → Qdrant  │
                                                 │ busca híbrida → rerank          │
                                                 └─────────────────────────────────┘
```

- **Hooks** registram cada sessão do Claude Code e informam ao Claude o id da sessão, para
  que a busca inclua os arquivos anexados a ela.
- **Ingestão** lê PDF, DOCX, Markdown, texto e código-fonte. Texto é dividido por parágrafo
  e código pela árvore sintática (tree-sitter), então um chunk é uma função ou uma seção, e
  não um recorte arbitrário.
- **Indexação** guarda dois vetores por chunk no Qdrant: um embedding denso (OpenAI ou modelo
  local) e um vetor esparso BM25, para casar tanto o significado quanto termos exatos, como
  códigos de erro e nomes de produto.
- **Busca** funde os dois com Reciprocal Rank Fusion dentro do Qdrant, reordena os candidatos
  com um cross-encoder e devolve os melhores trechos com arquivo e localização.

Quem decide quando buscar é o Claude. Nada é injetado nos seus prompts, a menos que você
ligue a injeção automática (veja [Configuração](#configuração)).

## O que vem no pacote

### 🔎 `search_context`: a ferramenta que o Claude chama

Documentos globais entram sempre; os arquivos da sessão entram quando o Claude passa o id da
sessão, que ele recebe no início dela. Arquivos anexados a uma sessão nunca aparecem em outra.
Cada resultado traz o arquivo, o heading, a página ou o intervalo de linhas de origem, e um score.

O Claude também recebe `list_documents`, para ver o que existe antes de buscar, e `list_sessions`.

### 🗂️ O dashboard: `/vibe-context:dashboard`

Uma página web local com três telas:

| Tela | Para que serve |
|---|---|
| **Sessions** | Sessões abertas, travadas e recentes. Abra uma para anexar arquivos que só ela enxerga. |
| **Global context** | Arquivos que toda sessão pode buscar: specs de produto, regras de negócio, convenções. |
| **Library** | Tudo o que foi indexado. Veja os chunks exatos que o Claude recebe, reindexe, apague e teste uma busca. |

### ⌨️ Comandos

| Comando | O que faz |
|---|---|
| `/vibe-context:start` | Sobe o Qdrant (Docker) e o backend. O servidor MCP também os sobe quando precisa. |
| `/vibe-context:add <arquivo> [--global]` | Anexa um arquivo a esta sessão, ou globalmente, e espera a indexação terminar. |
| `/vibe-context:dashboard` | Abre o dashboard no navegador com um link de login de uso único. |
| `/vibe-context:status` | Saúde, modelos, contagem de documentos e qualquer arquivo que falhou ou está esperando. |
| `/vibe-context:stop` | Para o backend e o Qdrant. Os dados indexados ficam. |

## Requisitos

- **Claude Code**, qualquer versão recente.
- **[uv](https://docs.astral.sh/uv/)**: roda o backend e baixa o Python 3.12 para ele.
  `brew install uv`.
- **Docker**, para o Qdrant local. Docker Desktop, OrbStack ou
  [colima](https://github.com/abiosoft/colima) funcionam; com o colima instalado, o
  VibeContext o inicia quando o Docker não está rodando. Dá para apontar `QDRANT_URL` para o
  Qdrant Cloud no lugar.
- **python3** no `PATH` para os hooks (o do sistema no macOS serve).
- **Disco**: cerca de 1 GB para o ambiente Python (PyTorch), mais uns 2 GB para o reranker
  local e 1 GB para o modelo de embedding local, se você usar.

Desenvolvido e testado no macOS (Apple Silicon). Linux deve funcionar; Windows não foi testado.

## Instalação

```shell
/plugin marketplace add carvalhxlucas/vibe-context
/plugin install vibe-context@vibe-context
```

Reinicie o Claude Code e rode:

```shell
/vibe-context:start
```

O primeiro start monta o ambiente Python e baixa a imagem do Qdrant, o modelo BM25 e as
gramáticas do tree-sitter: conte um ou dois minutos. Ele cria `~/.vibecontext/.env` com uma
chave do Qdrant gerada na hora.

**Escolha o modelo de embedding antes de anexar arquivos.** O padrão é o OpenAI
`text-embedding-3-small`, que precisa de `OPENAI_API_KEY` em `~/.vibecontext/.env`. Para manter
tudo na sua máquina, use `EMBEDDING_PROVIDER=local`. Depois reinicie com `/vibe-context:stop` e
`/vibe-context:start`. Sem a chave, os arquivos esperam na fila com uma mensagem explicando;
nada se perde.

Para testar sem instalar:

```bash
git clone https://github.com/carvalhxlucas/vibe-context
claude --plugin-dir ./vibe-context/plugins/vibe-context
```

## Uso

Anexe arquivos pelo dashboard ou pela sessão:

```shell
/vibe-context:add docs/spec-cobranca.pdf --global
/vibe-context:add ~/Downloads/ata-2026-09-12.docx
```

Depois trabalhe normalmente. Quando um pedido depende de algo fora do código, o Claude busca:

| Você diz | O que acontece |
|---|---|
| "Implementa a regra de reembolso que combinamos com o financeiro." | O Claude busca, acha a política na spec e implementa aqueles números. |
| "O que decidimos sobre o rate limit da API do parceiro?" | O Claude responde a partir da ata e cita a fonte. |
| "Qual prazo de retenção a gente combinou?" (nada anexado fala disso) | O Claude diz que não encontrou, em vez de inventar um. |

Arquivos suportados: `.pdf`, `.docx`, `.md`, `.txt`, `.rst` e código-fonte (Python,
JavaScript, TypeScript, Go, Rust, Java, Kotlin, Ruby, PHP, C, C++, C#, Swift, Scala, shell,
SQL, YAML, JSON, TOML). PDFs escaneados precisam de OCR, que não é suportado.

## Configuração

Tudo fica em `~/.vibecontext/.env`. Reinicie depois de alterar.

| Configuração | Padrão | Observações |
|---|---|---|
| `EMBEDDING_PROVIDER` | `openai` | `openai` ou `local`. Trocar provider ou modelo reindexa todos os documentos no próximo start. |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | |
| `LOCAL_EMBEDDING_MODEL` | `intfloat/multilingual-e5-base` | Multilíngue; cerca de 1 GB, baixado no primeiro uso. |
| `RERANK_PROVIDER` | `local` | `local`, `cohere` ou `none`. |
| `LOCAL_RERANK_MODEL` | `BAAI/bge-reranker-v2-m3` | Multilíngue; cerca de 2 GB, carrega em segundo plano. Até ficar pronto, a busca devolve o ranking híbrido e avisa. |
| `COHERE_API_KEY`, `COHERE_RERANK_MODEL` | | Para `RERANK_PROVIDER=cohere`. |
| `BM25_LANGUAGE` | `portuguese` | Stemming e stop words da parte de palavras-chave. Use `english` para documentos em inglês. |
| `TEXT_CHUNK_TOKENS`, `TEXT_CHUNK_OVERLAP`, `CODE_CHUNK_MAX_TOKENS` | `400`, `60`, `480` | Dimensionados para modelos locais de 512 tokens; aumente com embeddings da OpenAI. |
| `QDRANT_URL`, `QDRANT_API_KEY` | Docker local | Aponte para o Qdrant Cloud para dispensar o Docker. |
| `MAX_UPLOAD_MB` | `50` | |
| `VIBECONTEXT_AUTO_INJECT` | `false` | Adiciona trechos reordenados ao contexto do Claude a cada prompt. Veja os limites abaixo. |
| `VIBECONTEXT_PORT` | `8765` | |

## Segurança e privacidade

Documentos, chunks e vetores ficam na sua máquina, a menos que você escolha embeddings da
OpenAI ou rerank da Cohere: aí o texto dos chunks e as buscas vão para esse provedor.

- O backend escuta só em `127.0.0.1`, confere o header `Host` contra DNS rebinding e não
  envia headers de CORS.
- Toda chamada à API exige um token gerado no primeiro start e guardado em
  `~/.vibecontext/secrets.json` (modo `600`). O Qdrant tem uma chave própria, também gerada.
- O dashboard nunca vê esse token. `/vibe-context:dashboard` abre um link de login que vale
  uma vez, por dois minutos, e grava um cookie `HttpOnly` e `SameSite=Strict`. Toda alteração
  feita pelo dashboard também exige um header do htmx que uma página de outro site não
  consegue enviar. As páginas rodam sob uma Content Security Policy estrita, sem script
  inline, e o htmx vem junto no plugin, então nada é carregado de CDN.
- Uploads são gravados com um nome gerado; o nome enviado é só um rótulo. Extensões passam
  por uma lista permitida e são conferidas contra o conteúdo, o tamanho é limitado durante o
  envio, e DOCX que expandiria além de 200 MB é recusado.

Para remover tudo: `/vibe-context:stop`, desinstale o plugin e rode
`docker volume rm vibecontext_qdrant_data` e `rm -rf ~/.vibecontext`. Os modelos locais ficam
no cache do Hugging Face, `~/.cache/huggingface`.

## O que ele não é

O VibeContext busca no que você anexa; ele não decide o que é verdade. Se dois documentos
discordam, o Claude vê os dois. Ele não lê seu email, Notion ou Drive; você anexa exportações.

Limites conhecidos:

- **O score do reranker ordena resultados; não é um veredito de relevância.** Com o
  `bge-reranker-v2-m3`, uma nota curta e relevante pode tirar 0,08 enquanto uma frase completa
  dizendo a mesma coisa tira 0,99. A descrição da ferramenta orienta o Claude a ler os trechos
  em vez de descartar por score.
- **A injeção automática perde prompts longos.** Ela usa o prompt inteiro como consulta, e um
  prompt que pede duas coisas pontua mais baixo contra cada documento. No teste, "quando é a
  entrega do projeto?" achou a nota certa e "quando é a entrega do projeto? preciso planejar a
  sprint" não achou. Por isso ela vem desligada e o `search_context` é o caminho principal.
- **Perguntas num idioma sobre código em outro** acham o arquivo certo, mas nem sempre a
  função certa em primeiro lugar.

## Desenvolvimento

```bash
git clone https://github.com/carvalhxlucas/vibe-context
cd vibe-context

# Carrega numa sessão sem instalar
claude --plugin-dir ./plugins/vibe-context

# Valida os manifestos
claude plugin validate .
```

Mantenha o virtualenv de desenvolvimento fora do diretório do plugin: o `claude plugin eval`
se recusa a varrer um plugin com mais de 20.000 entradas, e só o PyTorch passa disso.

```bash
cd plugins/vibe-context/server
UV_PROJECT_ENVIRONMENT=../../../.venv uv run pytest
```

### Testes

122 testes cobrem os hooks, a autenticação e a checagem de host da API, parsing, chunking, a
fila de ingestão, a indexação contra um Qdrant em memória, busca e rerank com fakes, injeção
automática através de um servidor HTTP real, e o dashboard: login, recusa de CSRF, escape de
nomes de arquivo e de texto de chunk, uploads e fragmentos.

### Evals

Quatro casos em `plugins/vibe-context/evals/` verificam se o Claude usa bem o plugin, com o
servidor MCP simulado, então não precisa de backend nem de documentos:

| Caso | Passa quando |
|---|---|
| `implements-refund-rule-from-spec` | O Claude busca, passa o id da sessão e implementa os números da spec. |
| `answers-policy-question` | O Claude responde uma pergunta de política a partir da spec, não do conhecimento geral. |
| `says-when-context-is-missing` | Sem resultado, o Claude diz que não achou em vez de inventar um prazo. |
| `ignores-unrelated-request` | Um pedido sem relação com os documentos não dispara busca. |

```bash
cd plugins/vibe-context
claude plugin eval . --scaffold --allow-tools Write Edit
```

Último resultado (uma execução por braço): todos os casos passam com o plugin; sem ele, os dois
casos que dependem de documento falham, um delta de 1,0 em cada.

## Roadmap

- [x] Rastreamento de sessões por hooks
- [x] Ingestão: PDF, DOCX, Markdown, texto, código pela árvore sintática
- [x] Indexação híbrida: denso mais BM25, uma collection por modelo de embedding
- [x] `search_context` com rerank e escopo por sessão
- [x] Dashboard
- [ ] Transformar o prompt em consulta antes da injeção automática
- [ ] Observar uma pasta e reindexar arquivos alterados
- [ ] OCR para PDFs escaneados

## Licença

MIT, veja [LICENSE](LICENSE).
