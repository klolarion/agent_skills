# Profile

The profile decides which packs and items apply and the target level. Infer it from the repository first; ask only about what remains uncertain.

## Fields

| Field | Values | Meaning |
| --- | --- | --- |
| `traits` | `ai`, `agent`, `pii`, `payment` (any) | Enables trait packs |
| `forms` | `web`, `api`, `worker` (any) | Filters items that only make sense for some forms |
| `stage` | `prototype`, `beta`, `production` (one) | Sets the target level |
| `region` | `KR` (one; more regions later) | Enables the region pack |
| `agent_side_effects` | `none`, `read_only`, `write`, `irreversible` | Only with `agent`; `irreversible` can raise the target to L3 |

## Target level

| Profile | Target |
| --- | --- |
| `prototype` | L1 |
| `beta` or `production` | L2 |
| `production` with any of: `payment`, `pii`, or `agent` + `irreversible` | L3 |

Levels are cumulative: L2 includes L1 items, L3 includes L1 and L2 items. Only `production` reaches L3: a `beta` service with `pii` targets L2.

## Repository signals

Use these as evidence for inference. Quote the signal you used in the report. A signal counts only when production entry points reach it: a dependency nothing imports, or tool definitions that only tests or examples call, do not set a trait or `agent_side_effects`. Mention such unwired code in the report instead.

| Field | Signal in the repository | Infer |
| --- | --- | --- |
| traits: ai | `openai`, `anthropic`, `@anthropic-ai/sdk`, `google-genai`, `litellm`, `langchain`, `llamaindex` in dependencies or imports | `ai` |
| traits: agent | The model chooses or proposes actions that the code then carries out: a loop that feeds tool results back to the model, MCP server or client config, agent frameworks (LangGraph, CrewAI, OpenAI Agents SDK, Claude Agent SDK), or a single tool call whose chosen action writes, sends, or deletes, with or without a human confirming it. See "Routing is not an agent" below | `agent` |
| agent_side_effects | Tools that send email/messages, pay, delete, deploy, or change permissions → `irreversible`; tools that write data → `write`; only search/read → `read_only` | as listed |
| traits: pii | User or account models with email, phone, name, address, birth date; signup flows; auth providers | `pii` |
| traits: payment | `stripe`, `toss`, `portone`/`iamport`, `paddle`, `lemonsqueezy`, payment webhooks | `payment` |
| forms: web | Next.js, React, Vue, Svelte apps; HTML templates; SSR frameworks | `web` |
| forms: api | FastAPI, Express, NestJS, Django, Spring, Go HTTP servers; route definitions | `api` |
| forms: worker | Celery, RQ, BullMQ, Sidekiq, cron definitions, queue consumers. A worker the deploy config runs keeps this form even when nothing in the repository queues its jobs (the producer may live elsewhere); list those jobs under "Unwired code" | `worker` |
| region: KR | Korean UI strings, `ko` locale default, Korean payment providers, Korean privacy policy text | `KR` |
| stage | Not reliably visible in code | ask |

### Routing is not an agent

Function calling used only to classify a request does not make a service an agent. Example: a forced function call (`tool_choice` set to one function) picks one intent, the code runs a fixed read-only path for it, and the model never sees a tool result. Leave `agent` off and report the routing call under the `ai` trait; its timeouts and limits are judged by the AI items.

It is an agent, even with a single call, when the chosen intent leads to a write, a message, a payment, a deletion, or a permission change. That holds **whether or not a human confirms it first**: a confirmation step is not a reason to turn `agent` off, it is exactly what `AGENT-HITL-01` and `AGENT-HITL-02` judge (does the confirmation exist, and does it show the real action and arguments rather than the model's description of them). Set `agent_side_effects` from those effects.

### Disabled agent code

When agent code with side-effecting tools exists but production does not reach it (commented-out import, a feature flag that is off), leave `agent` off. In the report, name the code and the blocker items that would apply the day it is enabled (for example `AGENT-HITL-01`), so the risk is visible before someone turns it on.

## When to ask

- Always ask for `stage` unless the user already said it. `select_items.py` refuses to run without it rather than assume one.
- Ask for `region` when signals are mixed or absent.
- Ask for `agent_side_effects` when tools exist but their effects are unclear from names and code.
- When `scripts/repo_stats.py` proposes `workers`, ask whether to use them in the same message (see "Running mode" in SKILL.md).
- Ask all questions in one message, offering your inferred values as defaults.
