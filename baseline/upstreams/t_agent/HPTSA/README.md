# HPTSA: Teams of LLM Agents for Zero-Day Vulnerability Exploitation

This repository contains the official implementation for the paper **"Teams of LLM Agents can Exploit Zero-Day Vulnerabilities"** ([arXiv:2406.01637](https://arxiv.org/abs/2406.01637)), accepted to **EACL 2026**.

## Overview

HPTSA is a multi-agent system for automated penetration testing on web applications. A **planning (supervisor) agent** explores the target and orchestrates **specialized subagents**, each focused on a particular vulnerability class. This design addresses long-horizon planning and exploration across many vulnerability types—enabling the team to exploit real-world, previously unknown (zero-day) vulnerabilities.

The system is built on the [OpenAI Agents SDK](https://github.com/openai/openai-agents-python).

## Architecture

- **Supervisor agent**: Decides which subagent to invoke and in what order, and passes context (e.g., what other agents have tried) to avoid redundant work and improve coverage.
- **Specialized subagents** (each with tailored prompts and tools):
  - **General** — general-purpose reconnaissance and exploitation
  - **SQL** — SQL injection
  - **CSRF** — Cross-Site Request Forgery
  - **SSTI** — Server-Side Template Injection
  - **XSS** — Cross-Site Scripting
  - **ZAP** — OWASP ZAP baseline scanning and related checks

Subagent runs are summarized so the supervisor can reason over outcomes and plan subsequent steps.

## Benchmark

The paper evaluates on a benchmark of **14 real-world vulnerabilities**. Runs can be configured for **zero-day** (no vulnerability description given to the agent) or **one-day** (description provided) settings via the `--one_day` flag in the run script.

## Requirements

- **Python** ≥ 3.10  
- **Docker** (for running target environments)  
- **OpenAI API** key (for GPT-4)

## Installation

1. Clone the repository:

   ```bash
   git clone https://github.com/uiuc-kang-lab/HPTSA.git
   cd HPTSA
   ```

2. Create and edit the environment file:

   ```bash
   cp .env.example .env
   ```

   In `.env`, set:

   - `OPENAI_API_KEY` — your OpenAI API key  
   - `OPENAI_VECTOR_STORE_ID` — (optional) vector store ID for file search tools  

3. Install the package and dependencies:

   ```bash
   pip install -e .
   ```

## Usage

### Providing the task prompt

The task prompt (e.g. which URL to attack) is passed via the CLI, not environment variables. You must provide exactly one of:

- **`--prompt "..."`** — prompt as a string (convenient for short prompts and scripting).
- **`--prompt-file PATH`** — path to a file whose contents are the prompt (better for long or version-controlled prompts).

Examples:

```bash
# Inline prompt
python -m tagent.main --prompt "Hack the website at http://localhost:8080"

# From a file
python -m tagent.main --prompt-file ./prompts/my_task.txt
```

Optional **`--model`** selects the OpenAI model for the supervisor and all subagents (default: `gpt-4.1-2025-04-14`):

```bash
python -m tagent.main --prompt "..." --model gpt-4o
```

### Running with Docker

Build and run with a prompt passed as an argument:

```bash
docker build -t tagent .
docker run --env-file .env tagent --prompt "Hack the website at http://target:8080"
```

To use a prompt file, mount it and pass `--prompt-file`:

```bash
docker run --env-file .env -v $(pwd)/prompts:/prompts tagent --prompt-file /prompts/my_task.txt
```

`docker run tagent` with no arguments prints usage (including `--prompt`, `--prompt-file`, and `--model`).

## Citation

If you use this code or the benchmark in your research, please cite:

```bibtex
@inproceedings{zhu2026teams,
  title     = {Teams of {LLM} Agents can Exploit Zero-Day Vulnerabilities},
  author    = {Zhu, Yuxuan and Kellermann, Antony and Gupta, Akul and Li, Philip and Fang, Richard and Bindu, Rohan and Kang, Daniel},
  booktitle = {Proceedings of the 2026 Conference of the European Chapter of the Association for Computational Linguistics (EACL)},
  year      = {2026}
}
```

## License

See the repository for license terms.
