# ChatGPT Task Scheduler Prototype

## System Requirements

Build a job scheduler with an MCP (Model Context Protocol) interface:

- Users schedule tasks for future execution via MCP tool calls
- A background watcher scans for due jobs and pushes them to a queue
- Workers pull jobs from the queue and execute them
- Support task creation, listing, status checking, and cancellation
- Tool naming follows namespace + action verb pattern (e.g., `task.create`)

### Architecture

```
User → MCP Tool Call → Job Scheduler API → DB
                                            ↓
                              Watcher (scans DB) → Queue → Worker (executes)
```

## Design Questions

Answer these before you start coding:

1. **Watcher vs Cron:** Why separate the watcher from the worker? What problems does a single cron job that both scans and executes have?

- **Independent scaling** — one watcher can feed many workers; scanning and executing have different resource profiles (scanning is cheap I/O, execution can be CPU/memory heavy). Mixing them means scaling the wrong thing.
- **Fault tolerance** — if a single cron dies mid-execution, it restarts and re-runs already-executed jobs (or skips them), and has to redo the full scan. Decoupled, the watcher's job is idempotent and short; the worker's failure only affects one job.
- **Overlapping runs** — if jobs take longer than the cron interval, the next tick fires while the previous is still running, causing race conditions and double-execution.
- **Head-of-line blocking** — in a single cron, a slow job blocks the next scan. Decoupled, the watcher keeps finding new work while old work is still running.

1. **Queue Layer:** Why put a queue between the watcher and worker instead of having the watcher call the worker directly? What are the benefits?

- **Buffering / backpressure** — when 10,000 jobs come due at 9:00 AM, the queue absorbs the spike and workers drain at their own pace. Direct calls would either overwhelm workers or force the watcher to implement its own throttling.
- **At-least-once delivery with retries** — if a worker crashes mid-job, the message becomes visible again (visibility timeout pattern). Without a queue, the watcher would have to track in-flight state itself.
- **Worker discovery is free** — the watcher doesn't need to know how many workers exist or where they live. They just pull from the queue.
- **Durability** — in-flight work survives worker restarts; nothing is held only in a worker's memory.

1. **Time Bucket Partitioning:** Instead of `SELECT * WHERE scheduled_at <= now()`, why partition jobs by time bucket (e.g., hour)? What happens to query performance at 1M+ jobs without partitioning?

- **Bounded scan size as the table grows.** Even with a B-tree index on `scheduled_at`, range scans return more rows over time as old completed/failed jobs accumulate. Bucketing scopes each poll to "current bucket only" — query cost stays flat regardless of total table size.
- **Index bloat & vacuum cost** — Postgres indexes on a hot column degrade; partitioned tables can drop old partitions in O(1) instead of `DELETE` + vacuum.
- **Cache locality** — the current bucket fits in memory; the whole job table doesn't.
- **Operational simplicity** — archive old buckets to cold storage by dropping the partition.

1. **Tool Naming:** Why `task.create` instead of `createTask`? How does naming convention affect LLM tool selection accuracy?

- **Collision avoidance across MCP servers.** When a user connects Slack + GitHub + your scheduler, `create` would clash. The namespace prefix is a soft scoping mechanism.
- **LLM tool selection is text similarity.** LLMs reason about tools via name + description. `task.create`, `task.list`, `task.cancel` form an obvious cluster — the model learns "do something with tasks → look at `task.*`". The noun-first grouping is more discoverable than verb-first.
- **Noun-first matches how users think.** "Cancel task #5" maps to `task.cancel(5)` more directly than `cancelTask(5)` — less cognitive translation for the model. Anthropic's tool-use guidance confirms that consistent, descriptive naming materially improves selection accuracy, especially with 10+ tools.

1. **Registry vs If-Else:** Why use a dictionary registry to route tool calls instead of if-else chains? What happens when you need to add the 20th tool?

- **MCP requires `list_tools()`.** A registry lets you enumerate tools directly; an if-else chain forces you to maintain a separate list (two places to update → drift).
- **Single source of truth.** Adding a tool = one registry entry. With if-else you edit dispatch *and* schema list *and* docs.
- **Introspection** — generate docs, validate schemas, write generic middleware (logging, auth, rate limit) by iterating the registry.
- **Testability** — mock the registry to swap implementations in tests without touching dispatch code.

## Verification

Your prototype is a real MCP server. Test it with the MCP inspector — no Claude needed.

### 1. Start the server (sanity check)

```bash
python -m app.mcp_server
```

The process should hang waiting on stdin (it's a stdio MCP server — that's correct). Ctrl+C to stop. If you see an `ImportError` or other crash, fix that first.

### 2. Run the MCP inspector

Requires Node.js (uses `npx`).

```bash
npx @modelcontextprotocol/inspector python -m app.mcp_server
```

This opens a browser GUI (usually `http://localhost:5173`).

Steps in the GUI:

1. Click **Connect** -> should show 4 tools: `task.create`, `task.list`, `task.status`, `task.cancel`
2. **task.create** -> fill `description="Summarize tech news"`, `scheduled_at="2025-01-01T00:00:00"` (past time so watcher picks it up immediately) -> **Run Tool** -> response should include `{"job_id": 1, "status": "pending", ...}`
3. Wait ~10 seconds, then **task.status** -> `job_id: 1` -> status should now be `"completed"`
4. **task.create** with future time `"2099-12-31T00:00:00"` -> get `job_id: 2`
5. **task.cancel** -> `job_id: 2` -> status `"cancelled"`
6. **task.list** -> see all your jobs

### 3. (Optional) Connect to Claude Desktop / Claude Code

Once the inspector tests pass, the server is ready. To talk to it through Claude:

**Claude Desktop**: edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) and add (use absolute paths):

```json
{
  "mcpServers": {
    "task-scheduler": {
      "command": "/absolute/path/to/scaffold/.venv/bin/python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "/absolute/path/to/scaffold"
    }
  }
}
```

Restart Claude Desktop fully. The 🔨 icon in the chat input should show 4 tools.

**Claude Code**: edit `~/.claude.json` (top-level `mcpServers` for user scope) with the same block, or run `claude mcp add` from inside `scaffold/`.

Then chat:
> "Schedule a task to review PR #123 tomorrow at 9am."
> -> Claude calls `task.create` -> returns job_id
> "What's the status of that task?"
> -> Claude calls `task.status`

## Suggested Tech Stack

Python + the official `mcp` SDK is recommended (already in `requirements.txt` for the Guided Track). Challenge Track may use any language with an MCP SDK.
