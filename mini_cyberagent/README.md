# mini_cyberagent

A **cybersecurity-focused minimal agent**, modelled after
[`mini-swe-agent`](https://github.com/SWE-agent/mini-swe-agent).

The mini-swe-agent thesis is *"radical simplicity"*: a 100-line agent class
that holds a linear message list, executes one bash action per step, and has
no hidden state. We share that thesis. The differences below come from the
fact that **CTF tasks are not software-engineering tasks**.

---

## Conceptual mapping

| mini-swe-agent | mini_cyberagent | Notes |
|---|---|---|
| `DefaultAgent` (`agents/default.py`) | `Agent` (`agent.py`) | Same shape: `run()` loop, `step() = query() + execute_actions()`, message list as state. |
| `AgentConfig` (pydantic) | `AgentConfig` (`agent.py`) | Tunables: `max_steps`, `max_time`, `max_token_budget`. |
| `LocalEnvironment` (`environments/local.py`) | `DockerEnvironment` (`common/agent_runtime/docker_env.py`) | We always sandbox in Docker; CTF challenges interact with binaries we don't trust. |
| `LitellmModel` (`models/litellm_model.py`) | `LLMClientStub` (`common/llm_dispatch/dispatcher.py`) | Both expose `.invoke(messages)`. Ours adds cross-process scheduling and a circuit breaker because CTF runs are 10× longer than typical SWE-agent runs. |
| Single `bash` tool | `Command` + `Skill` system | A CTF agent needs a *toolbox*: disassembly (capstone), exploit dev (pwntools), web fuzzing, flag submission. We let the agent declare them as files (commands/) and bundles (skills/). |
| `Submitted` exception → exit | `submit_flag` command + `benchmark_scorer_registry` | Same idea: a sentinel action ends the run. We support multiple scorers (NYU CTF flag string, AutoPenBench HTTP report, network-CVE health check). |
| `serialize() → json trajectory` | `Agent.memory` + run logs | Ours writes JSONL to `logs/.../<chal_id>_run<i>.log` with the full thought/action/observation trace. |
| Single agent | Population of agents | The evolution engine in `cyber_evolver/` mutates an entire `mini_cyberagent` directory tree (commands + skills + prompts) and evaluates the variants in parallel. |

---

## Module layout

```
mini_cyberagent/
├── __init__.py            # Protocols: Model, Environment, AgentLike, CommandLike, SkillLike
├── agent.py               # Agent class, AgentConfig, PromptTemplates
├── command.py             # Command — a single executable script with metadata
├── skill.py               # Skill — a directory of related tools with a manifest
├── benchmark_scorers.py   # Per-benchmark scoring callbacks (flag, network-cve, etc.)
├── commands/              # Built-in commands shipped with every agent variant
│   ├── disassemble.py     # capstone-backed binary disassembler
│   ├── editor.py          # file editor with safe partial replace
│   ├── submit_flag.py     # canonical submission action
│   ├── load_skill.py      # dynamic skill loader (skill_based seed only)
│   └── …
├── skills/                # Declarative skill bundles (each = a directory)
│   ├── binary-analysis/
│   └── skill_example/
└── configs/               # Run configs (max_steps, observation truncation, etc.)
```

The split between `command.py` (single tool) and `skill.py` (bundle) is the
main thing we add over mini-swe-agent. It exists because the evolution engine
needs **two granularities of mutation**: tweak a single tool, or invent a
whole new skill bundle.

---

## Protocols

`mini_cyberagent/__init__.py` declares duck-typed contracts that any
implementation must satisfy:

```python
@runtime_checkable
class Environment(Protocol):
    def execute(self, cmd: str, *args, **kwargs) -> Any: ...
    def mkdir(self, path: str) -> Any: ...
    def cp_to_container(self, src: str, dst: str) -> Any: ...

@runtime_checkable
class Model(Protocol):
    def invoke(self, messages, **kwargs) -> Any: ...

@runtime_checkable
class AgentLike(Protocol):
    memory: list
    current_step: int
    def run(self, max_steps: int = 20, **kwargs) -> bool: ...
```

These are *protocols*, not abstract base classes — same choice
mini-swe-agent makes — so any class with the right method set works
without inheritance ceremony. The evolution engine relies on this: the
seed agent at `cyber_evolver/gen0_root/skill_based/agent.py` is loaded
by file path into a Docker sandbox, never imports `mini_cyberagent`,
and still satisfies `AgentLike` because it implements `run()`.

---

## When to use which

| If you want… | Use |
|---|---|
| A simple agent on a SWE bug | `mini-swe-agent` (upstream) |
| An agent on a CTF challenge with shell + binary tools | `mini_cyberagent` |
| An evolving population of CTF agents | `cyber_evolver/` driving `mini_cyberagent` seeds |
| To benchmark an existing third-party agent on our infra | `baseline/` runners |

---

## Differences worth knowing

- **Memory is a flat `list[dict]`** (system, user, assistant, observation,
  …). We do not collapse turns; the LogAnalyzer (`cyber_evolver/`) reads
  the raw trace.
- **One LLM call per step**, gated by tenacity retries on connection
  errors. The cross-process dispatcher (`common/llm_dispatch/`) handles
  rate-limit/outage policy above this layer.
- **All actions are bash**. Commands and skills compile down to executable
  files in the sandbox; the LLM produces shell snippets that invoke them.
  This is intentional (matches mini-swe-agent's "shell as universal
  interface" stance) — and crucial for evolution, because the LLM can
  freely synthesise new tools by writing new files.
- **Scoring is pluggable** via `benchmark_scorers.benchmark_scorer_registry`.
  Each entry maps a benchmark family (`nyu_ctf`, `autopenbench`,
  `cvebench_network`, …) to a function that decides whether the agent's
  most recent observation/submission means "solved".
