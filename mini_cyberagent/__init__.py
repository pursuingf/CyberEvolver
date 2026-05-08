"""Cybersecurity-focused minimal agent.

This package follows the **mini-swe-agent** design philosophy: a small,
linear, message-list-driven agent loop with duck-typed components and
no hidden state. The protocols defined below document the contracts that
``Agent``, ``Environment``, ``Model``, ``Command``, and ``Skill`` already
satisfy — they're optional for runtime, but useful for type-checkers and
for downstream modules (cyber_evolver, run_evolve) that consume these
contracts.

Why protocols and not abstract base classes? Mini-swe-agent's
`minisweagent.__init__` uses the same pattern: duck-typing keeps the
codebase open for new model/environment backends without forcing every
implementer through a single inheritance tree.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Model(Protocol):
    """A language-model client. The historical ``langchain_openai.ChatOpenAI``
    instance and our ``common.llm_dispatch`` LLMClientStub both satisfy this.
    """

    def invoke(self, messages: Any, **kwargs: Any) -> Any: ...


@runtime_checkable
class Environment(Protocol):
    """An execution sandbox. Concrete impls live in
    ``common/agent_runtime/docker_env.py`` (DockerEnvironment) and the
    AutoPenBench wrapper. Method names mirror ``mini-swe-agent``'s
    ``LocalEnvironment`` plus the Docker-specific helpers we need.
    """

    def execute(self, cmd: str, *args: Any, **kwargs: Any) -> Any: ...

    def mkdir(self, path: str) -> Any: ...

    def cp_to_container(self, src: str, dst: str) -> Any: ...


@runtime_checkable
class CommandLike(Protocol):
    """A runnable shell or Python tool the agent can invoke.

    ``mini_cyberagent.command.Command`` is the canonical implementation:
    it loads metadata from a script file (``# Usage:``, ``# Description:``),
    builds prompt-ready documentation, and copies the script into the
    agent sandbox.
    """

    name: str

    def get_prompt_info(self) -> str: ...


@runtime_checkable
class SkillLike(Protocol):
    """A declarative bundle of related tools shipped as a directory.

    ``mini_cyberagent.skill.Skill`` parses a SKILL.md manifest, exposes
    a tool index, and renders prompt-time descriptions. Skills are
    evolution-friendly: the refiner mutates them by editing a single
    directory tree.
    """

    name: str

    def to_index_entry(self) -> dict: ...

    def get_prompt_info(self) -> str: ...


@runtime_checkable
class AgentLike(Protocol):
    """The minimal contract an evolution-discoverable agent must honour.

    ``mini_cyberagent.agent.Agent`` and the seed agent under
    ``cyber_evolver/gen0_root/skill_based/agent.py`` both satisfy it.
    Following the mini-swe-agent shape: ``run(max_steps) -> bool``.
    """

    memory: list
    current_step: int

    def run(self, max_steps: int = 20, **kwargs: Any) -> bool: ...


__all__ = [
    "AgentLike",
    "CommandLike",
    "Environment",
    "Model",
    "SkillLike",
]
