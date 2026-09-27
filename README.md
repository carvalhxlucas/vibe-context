# VibeContext

*[English](README.md) · [Português](README.pt-BR.md)*

**The context your code does not contain, searchable from Claude Code.**

The refund rule lives in a product spec. The decision to drop a feature is in last
Tuesday's meeting notes. The partner's API limits are in a PDF someone emailed. Claude
reads your repository and none of that, so it guesses, or asks you, or confidently
implements a policy nobody agreed on.

VibeContext is a [Claude Code](https://claude.com/claude-code) plugin that indexes those
documents on your machine and gives Claude a search tool for them. You attach files to a
single session or to every session; Claude searches them when the task depends on
something the code does not explain.

```shell
/plugin marketplace add carvalhxlucas/vibe-context
/plugin install vibe-context@vibe-context
```

---

## How it works

```
 Claude Code session ──hooks──▶ SQLite (sessions)          Dashboard (localhost)
        │                                                      │ attach files
        │ search_context (MCP)                                 ▼
        ▼                                        ┌──────── FastAPI backend ────────┐
   MCP server ──bearer token──▶ 127.0.0.1:8765 ──┤ parse → chunk → embed → Qdrant  │
                                                 │ hybrid search → rerank          │
                                                 └─────────────────────────────────┘
```

- **Hooks** record each Claude Code session and tell Claude its session id, so a search
  can include the files attached to that session.
- **Ingestion** parses PDF, DOCX, Markdown, text and source code, and chunks prose by
  paragraph and code along its syntax tree (tree-sitter), so a chunk is a function or a
  section rather than an arbitrary window.
- **Indexing** stores two vectors per chunk in Qdrant: a dense embedding (OpenAI or a
  local model) and a BM25 sparse vector, so both meaning and exact terms such as error
  codes or product names match.
- **Search** fuses the two with Reciprocal Rank Fusion inside Qdrant, reranks the
  candidates with a cross-encoder, and returns the best excerpts with their file and
  location.

Claude decides when to search. Nothing is injected into your prompts unless you turn
on automatic injection (see [Configuration](#configuration)).

## What is in the box

### 🔎 `search_context` — the tool Claude calls

Global documents are always searched; the session's own files are added when Claude
passes its session id, which it gets at session start. Files attached to one session
are never visible from another. Each result carries the file, the heading, page or
line range it came from, and a score.

Claude also gets `list_documents`, to see what exists before searching, and
`list_sessions`.

### 🗂️ The dashboard — `/vibe-context:dashboard`

A local web page with three views:

| View | What it is for |
|---|---|
| **Sessions** | Open, stale and recent sessions. Open one to attach files only that session can search. |
| **Global context** | Files every session can search: product specs, business rules, conventions. |
| **Library** | Everything indexed. Preview the exact chunks Claude receives, reindex, delete, and try a search. |

### ⌨️ Commands

| Command | What it does |
|---|---|
| `/vibe-context:start` | Starts Qdrant (Docker) and the backend. The MCP server also starts them on demand. |
| `/vibe-context:add <file> [--global]` | Attaches a file to this session, or globally, and waits until it is indexed. |
| `/vibe-context:dashboard` | Opens the dashboard in your browser with a one-time login link. |
| `/vibe-context:status` | Health, models, document counts, and any file that failed or is waiting. |
| `/vibe-context:stop` | Stops the backend and Qdrant. Indexed data is kept. |

## Requirements

- **Claude Code**, any recent version.
- **[uv](https://docs.astral.sh/uv/)**: runs the backend and downloads Python 3.12 for
  it. `brew install uv`.
- **Docker**, for the local Qdrant. Docker Desktop, OrbStack or
  [colima](https://github.com/abiosoft/colima) all work; with colima installed, VibeContext
  starts it when Docker is not running. You can point `QDRANT_URL` at Qdrant Cloud instead.
- **python3** on your `PATH` for the hooks (the macOS system one is fine).
- **Disk**: about 1 GB for the Python environment (PyTorch), plus about 2 GB for the local
  reranker and 1 GB for the local embedding model if you use them.

Developed and tested on macOS (Apple Silicon). Linux should work; Windows is untested.

## Install

```shell
/plugin marketplace add carvalhxlucas/vibe-context
/plugin install vibe-context@vibe-context
```

Restart Claude Code, then:

```shell
/vibe-context:start
```

The first start builds the Python environment and downloads the Qdrant image, the BM25
model and the tree-sitter grammars: expect a minute or two. It creates
`~/.vibecontext/.env` with a generated Qdrant key.

**Choose the embedding model before adding files.** The default is OpenAI
`text-embedding-3-small`, which needs `OPENAI_API_KEY` in `~/.vibecontext/.env`. To keep
everything on your machine, set `EMBEDDING_PROVIDER=local` instead. Then restart with
`/vibe-context:stop` and `/vibe-context:start`. Without a key, files wait in the queue
with a message saying so; they are not lost.

To try it without installing:

```bash
git clone https://github.com/carvalhxlucas/vibe-context
claude --plugin-dir ./vibe-context/plugins/vibe-context
```

## Use it

Attach files from the dashboard, or from the session:

```shell
/vibe-context:add docs/billing-spec.pdf --global
/vibe-context:add ~/Downloads/meeting-2026-09-12.docx
```

Then work normally. When a request depends on something outside the code, Claude
searches:

| You say | What happens |
|---|---|
| "Implement the refund rule we agreed with finance." | Claude searches, finds the policy in the spec, and implements those numbers. |
| "What did we decide about the partner API rate limits?" | Claude answers from the meeting notes and cites them. |
| "What retention period did we agree on?" (nothing attached says) | Claude says it could not find it instead of inventing one. |

Supported files: `.pdf`, `.docx`, `.md`, `.txt`, `.rst`, and source code (Python,
JavaScript, TypeScript, Go, Rust, Java, Kotlin, Ruby, PHP, C, C++, C#, Swift, Scala,
shell, SQL, YAML, JSON, TOML). Scanned PDFs need OCR, which is not supported.

## Configuration

Everything lives in `~/.vibecontext/.env`. Restart after changing it.

| Setting | Default | Notes |
|---|---|---|
| `EMBEDDING_PROVIDER` | `openai` | `openai` or `local`. Changing the provider or model reindexes every document on the next start. |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | |
| `LOCAL_EMBEDDING_MODEL` | `intfloat/multilingual-e5-base` | Multilingual; about 1 GB, downloaded on first use. |
| `RERANK_PROVIDER` | `local` | `local`, `cohere` or `none`. |
| `LOCAL_RERANK_MODEL` | `BAAI/bge-reranker-v2-m3` | Multilingual; about 2 GB, loads in the background. Until it is ready, search returns the hybrid ranking and says so. |
| `COHERE_API_KEY`, `COHERE_RERANK_MODEL` | | For `RERANK_PROVIDER=cohere`. |
| `BM25_LANGUAGE` | `portuguese` | Stemming and stop words for the keyword side. Use `english` for English documents. |
| `TEXT_CHUNK_TOKENS`, `TEXT_CHUNK_OVERLAP`, `CODE_CHUNK_MAX_TOKENS` | `400`, `60`, `480` | Sized for 512-token local models; raise them with OpenAI embeddings. |
| `QDRANT_URL`, `QDRANT_API_KEY` | local Docker | Point at Qdrant Cloud to skip Docker. |
| `MAX_UPLOAD_MB` | `50` | |
| `VIBECONTEXT_AUTO_INJECT` | `false` | Adds reranked excerpts to Claude's context on every prompt. See the limits below. |
| `VIBECONTEXT_PORT` | `8765` | |

## Security and privacy

Documents, chunks and vectors stay on your machine, unless you choose OpenAI embeddings
or Cohere reranking: then chunk text, and search queries, are sent to that provider.

- The backend listens on `127.0.0.1` only, checks the `Host` header against DNS
  rebinding, and sends no CORS headers.
- Every API call needs a token generated on first start and stored in
  `~/.vibecontext/secrets.json` (mode `600`). Qdrant has its own generated key.
- The dashboard never sees that token. `/vibe-context:dashboard` opens a login link that
  works once, for two minutes, and sets an `HttpOnly`, `SameSite=Strict` cookie. Every
  change from the dashboard also requires an htmx header a foreign page cannot send.
  Pages run under a strict Content Security Policy with no inline script, and htmx is
  vendored, so nothing loads from a CDN.
- Uploads are stored under a generated name; the name you uploaded is only a label.
  Extensions are allowlisted and checked against the file content, sizes are capped
  while streaming, and DOCX archives that would expand past 200 MB are rejected.

To remove everything: `/vibe-context:stop`, uninstall the plugin, then
`docker volume rm vibecontext_qdrant_data` and `rm -rf ~/.vibecontext`. Local models sit
in the Hugging Face cache, `~/.cache/huggingface`.

## What it is not

VibeContext searches what you attach; it does not decide what is true. If two documents
disagree, Claude sees both. It does not read your email, Notion or Drive; you attach
exports.

Known limits:

- **Reranker scores order results; they are not a relevance verdict.** With
  `bge-reranker-v2-m3`, a short relevant note can score 0.08 while a full sentence saying
  the same thing scores 0.99. The tool description tells Claude to read the excerpts
  rather than discard by score.
- **Automatic injection misses long prompts.** It uses the whole prompt as the query, and
  a prompt that asks two things scores lower against each document. In testing, "when is
  the delivery?" found the right note and "when is the delivery? I need to plan the
  sprint" did not. That is why it is off by default and `search_context` is the main path.
- **Questions in one language about code in another** find the right file but not always
  the right function first.

## Development

```bash
git clone https://github.com/carvalhxlucas/vibe-context
cd vibe-context

# Load it into a session without installing
claude --plugin-dir ./plugins/vibe-context

# Check the manifests
claude plugin validate .
```

Keep the development virtualenv outside the plugin directory: `claude plugin eval`
refuses to scan a plugin directory with more than 20,000 entries, and PyTorch alone has
more.

```bash
cd plugins/vibe-context/server
UV_PROJECT_ENVIRONMENT=../../../.venv uv run pytest
```

### Tests

122 tests cover the hooks, the API's authentication and host checks, parsing, chunking,
the ingestion queue, indexing against an in-memory Qdrant, search and reranking with
fakes, automatic injection through a real HTTP server, and the dashboard: login,
CSRF refusal, escaping of file names and chunk text, uploads and fragments.

### Evals

Four cases under `plugins/vibe-context/evals/` check that Claude uses the plugin well,
with the MCP server mocked so no backend or document is needed:

| Case | Passes when |
|---|---|
| `implements-refund-rule-from-spec` | Claude searches, passes its session id, and implements the numbers from the spec. |
| `answers-policy-question` | Claude answers a policy question from the spec, not from general knowledge. |
| `says-when-context-is-missing` | With nothing found, Claude says so instead of inventing a period. |
| `ignores-unrelated-request` | A request unrelated to the documents triggers no search. |

```bash
cd plugins/vibe-context
claude plugin eval . --scaffold --allow-tools Write Edit
```

Last result (one run per arm): every case passes with the plugin; without it, the two
cases that depend on a document fail, a delta of 1.0 on each.

## Roadmap

- [x] Session tracking through hooks
- [x] Ingestion: PDF, DOCX, Markdown, text, code by syntax tree
- [x] Hybrid indexing: dense plus BM25, one collection per embedding model
- [x] `search_context` with reranking and session scoping
- [x] Dashboard
- [ ] Turn the prompt into a search query before automatic injection
- [ ] Watch a folder and reindex changed files
- [ ] OCR for scanned PDFs

## License

MIT, see [LICENSE](LICENSE).
