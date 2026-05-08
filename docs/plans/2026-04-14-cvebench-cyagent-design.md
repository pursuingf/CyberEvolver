# CVEBench CyAgent Design

Goal: connect upstream CyBench `SimpleAgent` to the current CVEBench target runtime with minimal intrusion.

Design:
- Keep `baseline/upstreams/cy_agent/cybench/` read-only.
- Use upstream `SimpleAgent`, `AgentConfig`, `SubtaskConfig`, prompt, command parsing, answer parsing, memory truncation, and iteration loop.
- Replace only runtime integration in `baseline/agents/cy_agent.py`: build synthetic CyBench task metadata from CVEBench challenge data, patch model routing at runtime, and patch command execution to call `DockerEnvironment.agent_execute(...)`.
- CVEBench target lifecycle remains owned by the current `ChallengeClient`/Challenge server. The baseline worker must request `parallel_mode=network` for `cy_agent` on CVEBench and must connect the agent container to the runtime network returned by the Challenge server.
- Do not call upstream `TaskRunner.setup_environment()` because it runs destructive host-level Docker cleanup. The equivalence boundary is upstream `SimpleAgent`, not the full CyBench runner.

Verification:
- Unit-test runtime policy for `cy_agent+cvebench`.
- Unit-test that CyAgent commands run through `docker_env.agent_execute`.
- Run a small CVEBench smoke after tests pass.
