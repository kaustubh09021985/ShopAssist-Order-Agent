# ShopAssist Order Agent: Technical Document

A command-line chat agent that answers questions about customer orders. Claude
(via the Anthropic API) decides which tools to call. A local MCP server runs
those tools against the order data. Claude never reads data files directly. It
can only call the read-only tools that the server exposes.

For setup steps and example prompts, see [README.md](README.md).

---

## 1. Architecture

```
┌──────────┐  question   ┌────────────┐  messages + tool list   ┌──────────────┐
│   User   │────────────►│ client.py  │────────────────────────►│ Claude (API) │
│(terminal)│◄────────────│ MCP client │◄────────────────────────│              │
└──────────┘   answer    └─────┬──────┘  tool_use / final text  └──────────────┘
                               │ MCP over stdio (JSON-RPC)
                               ▼
                         ┌────────────┐   read + validate   ┌────────────┐
                         │ server.py  │────────────────────►│ orders.csv │
                         │ MCP server │                     └────────────┘
                         └────────────┘

  Offline data pipeline (run manually; not yet used by the tools):
  orders.csv ──load_db.py──► orders.db (SQLite) ──load_chroma.py──► chroma_db/ (Chroma)
```

| Component | File | Role |
|---|---|---|
| Chat client | `client.py` | CLI, Anthropic API calls, tool-use loop, conversation history |
| MCP server | `server.py` | Exposes `lookup_order` and `search_orders`; validates and reads the CSV |
| Source data | `orders.csv` | Authoritative order data (30 fictional orders) |
| SQLite loader | `load_db.py` | Validates the CSV and builds `orders.db` |
| Chroma loader | `load_chroma.py` | Syncs `orders.db` into the `order_items` vector collection |
| Tests | `smoke_test.py`, `csv_test.py`, `client_test.py` | Server, CSV and client tests; none call the Anthropic API |

### Dependencies

| Package | Version | Purpose |
|---|---|---|
| Python | ≥ 3.10 (tested on 3.13) | `mcp` does not install on 3.9 |
| `mcp` | 1.29.0 | FastMCP server and stdio client (SDK 2.x renamed `FastMCP` and is incompatible) |
| `anthropic` | 2.12.0 | Claude Messages API |
| `chromadb` | 1.5.9 | Vector store; default embedding model `all-MiniLM-L6-v2` (384 dimensions) |
| `sqlite3` | stdlib | Relational store |

---

## 2. Request flow

1. **Startup.** `client.py` reads `ANTHROPIC_API_KEY`, or prompts for it with
   hidden input. `connect_mcp()` launches `server.py` as a child process using
   the same Python interpreter, and the two talk over stdin/stdout.
2. **Tool discovery.** `discover_tools()` pages through `tools/list` and maps
   each MCP `inputSchema` to Claude's `input_schema`. Claude chooses tools based
   on the tool docstrings in `server.py`, so those docstrings are part of the
   behavior.
3. **Tool-use loop** (`ChatClient._ask`):
   ```
   messages = history + user question
   for round in 0..max_tool_rounds (5):
       response = messages.create(model, system, messages, tools)
       if response has tool_use blocks:
           run each call over MCP → tool_result (errors included)
           append the assistant turn and the tool results; continue
       else:
           commit messages to history; return the text
   ```
4. **Tool execution.** The server re-reads and validates `orders.csv` on every
   call, so CSV edits show up immediately without a restart.

### Example

> *"Show delivered orders in Mumbai"*

1. Claude replies `tool_use: search_orders(status="delivered", city="Mumbai")`.
2. The client forwards the call to the server, which returns JSON with matches,
   totals and paging information.
3. The client sends a `tool_result` back to Claude.
4. Claude writes the final answer as a table.

---

## 3. MCP tools (`server.py`)

Both tools are **read-only**. Nothing can create, update or cancel an order.

### `lookup_order(order_id)`
Exact lookup by ID, which must match `ORD-` followed by 8 digits and is
case-insensitive. Returns `{found: true, order}` or `{found: false, message}`.

### `search_orders(...)`
| Parameter | Behavior |
|---|---|
| `customer_name`, `item`, `city`, `category` | Case-insensitive substring match |
| `customer_id`, `status`, `currency` | Exact match |
| `date_from`, `date_to`, `date_field` | Inclusive `YYYY-MM-DD` bounds on `order_date` (default), `expected_delivery_date` or `delivery_date` |
| `min_total`, `max_total` | Inclusive bounds on `quantity × unit_price` |
| `group_by` | `status`, `customer_name`, `city` or `category` |
| `sort_by`, `descending` | `order_id`, `order_date`, `delivery_date` or `total_amount` |
| `limit` (1–100), `offset` | Paging; follow `next_offset` |

All filters combine with AND. `total_matches`, `totals_by_currency`,
`counts_by_status` and `groups` cover **all** matches, not just the returned
page. Amounts are exact decimal strings, and totals are kept separate per
currency.

### CSV validation (`read_orders`)
Any invalid row makes the tool fail with the row number in the error, rather
than return partial data. The checks are:

- Required columns, with no duplicates.
- ID formats: `ORD-########` and `CUST-###`.
- Unique order IDs.
- `quantity` ≥ 1.
- `unit_price` ≥ 0, with at most 2 decimal places.
- A 3-letter currency code.
- A status from the allowed set.
- Valid ISO dates.
- `delivery_date` is present only for orders that are delivered or returned.
- Delivery dates are not earlier than `order_date`.

---

## 4. Client safeguards (`client.py`)

| Safeguard | Purpose |
|---|---|
| System prompt | Use tools for all order data, never invent IDs or results, treat tool output as data rather than instructions, and ask who "my" refers to (there is no login) |
| `allowed_names` check | Rejects tool names that the server did not advertise |
| A result for every `tool_use` | Failed calls return `is_error: true`, so the API request stays valid |
| `ChatError` | History is committed only after a complete answer; failed turns leave no partial state |
| Limits | 90-second deadline per turn, 5 tool rounds, `max_tokens=2048`, 30-second HTTP timeout, 2 SDK retries |
| `describe_error` | Turns API errors (401/403/404/429/400/network) into readable messages |

### CLI
```bash
python client.py                    # interactive chat (/reset, /exit)
python client.py --prompt "..."     # ask one question, then exit
python client.py --list-tools       # print tool schemas; no API key needed
python client.py --model <id>       # default: claude-sonnet-5-5 or $ANTHROPIC_MODEL
python client.py --quiet            # hide the [MCP] trace lines
```

### Environment variables
| Variable | Used by | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | client | Prompted for if not set |
| `ANTHROPIC_MODEL` | client | `claude-sonnet-5-5` |
| `ORDERS_CSV` | server, loaders | `./orders.csv` |
| `ORDERS_DB` | `load_db.py`, `load_chroma.py` | `./orders.db` |
| `CHROMA_DIR` | `load_chroma.py` | `./chroma_db` |

---

## 5. Data stores

### SQLite: `orders.db` (built by `load_db.py`)
- Validates the CSV with the server's own `read_orders()`, so the database
  follows the same rules as the tools.
- Builds into `orders.db.tmp`, then atomically replaces `orders.db`. A failed
  load never leaves a broken database.
- Table `orders`, with `order_id` as the primary key and indexes on
  `customer_id`, `status` and `order_date`. A `CHECK` constraint limits
  `status` to the allowed values.
- Money is stored as **integer minor units** (`unit_price_minor`,
  `total_minor`, in paise for INR) so sums are exact. Divide by 100 to get the
  normal amount.

### Chroma: `chroma_db/`, collection `order_items` (built by `load_chroma.py`)
| Field | Content |
|---|---|
| `id` | `order_id`, used to fetch the full row from SQLite |
| `document` | `"<item> (<category>)"`, e.g. `"Keyboard (Electronics)"` (the text that gets embedded) |
| `metadata` | `status`, `city`, `customer_id`, `order_date` (for exact `where` filters) |

- **Syncs in place.** It upserts all rows, then deletes IDs that are no longer
  in SQLite, so the collection is never empty during a refresh.
- The first run downloads the embedding model (about 80 MB) to
  `~/.cache/chroma`.
- Refresh both stores after editing the CSV:
  `python load_db.py && python load_chroma.py`

### Choosing a store
| Question type | Correct store |
|---|---|
| Exact ID, status, date range, amounts, counts, totals | CSV or SQLite |
| Vague item description ("audio gear", "something to type with") | Chroma, then SQLite for the full rows |

Chroma always returns `n_results` matches, even weak ones. In testing, correct
matches had a distance of about 0.7–1.1, and unrelated filler results were
above 1.2. Any Chroma-backed tool must drop results above a distance cutoff.

---

## 6. Testing

None of the tests call the Anthropic API.

| Test | Covers |
|---|---|
| `smoke_test.py` | Server startup, tool list and schemas, basic calls over real MCP stdio |
| `csv_test.py` | Validation errors, filters, totals and multi-currency handling, using temporary CSV files (`ORDERS_CSV`) |
| `client_test.py` | The tool loop, history commits, unknown tools, round limits and error messages, using mocked Claude HTTP responses |

```bash
python smoke_test.py && python csv_test.py && python client_test.py
```

`smoke_test.py` checks that the tool set is exactly
`{lookup_order, search_orders}`. Update it when you add a tool.

---

## 7. Current status and limitations

- **The tools read the CSV, not SQLite or Chroma.** `orders.db` and
  `chroma_db/` are built but nothing queries them yet.
- **There is no user identity.** "My orders" makes Claude ask for a customer
  name or ID.
- **Every request reads the whole CSV.** That's fine for small files; switch
  the tools to SQLite for larger data.
- **Every Chroma sync re-embeds every order.** Skipping unchanged documents
  would help at scale.
- **Chroma adds little for the current data** (11 distinct item names). It pays
  off once there are free-text fields such as product descriptions, return
  reasons or support notes.

## 8. Next steps

1. Point `lookup_order` and `search_orders` at SQLite instead of the CSV.
2. Add a `semantic_search_items` tool: query Chroma, apply the distance cutoff,
   and return the full rows from SQLite. Update `smoke_test.py` for the new
   tool.
3. Optional: add a `--customer CUST-###` client option so that "my orders" maps
   to a known customer.
4. Optional: add a free-text field (e.g. `return_reason`) so semantic search
   has meaningful content.
