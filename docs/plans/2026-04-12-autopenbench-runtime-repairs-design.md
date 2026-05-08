# AutoPenBench Runtime Repairs Design

**Goal:** Repair the known AutoPenBench launch failures without changing vulnerable software versions or challenge semantics.

**Architecture:** Split the work into two buckets. Keep host-exposed targets in `host_ports` mode and fix only their incorrect metadata or readiness behavior. Move the two UDP network-scanning targets to compose-local network mode so the agent reaches them on the intended internal subnet instead of through an unsuitable host TCP port mapping.

**Scope:**
- Fix `network_security/vm1` to expose the real SSH port `52693`.
- Move `network_security/vm2` and `network_security/vm3` to compose-local network mode so they remain reachable on `192.168.3.2:161/udp` and `192.168.3.3:65421/udp`.
- Fix `real-world/cve/vm9` to expose the meaningful SMB service port `445`.
- Add database readiness gating for `web_security/vm3`.
- Teach the launch pipeline to honor explicit `challenge.json` network settings instead of forcing every AutoPenBench target into `host_ports`.

**Non-Goals:**
- Do not change base images, package versions, or vulnerable service versions.
- Do not solve slow network downloads for `cve-vm0`, `cve-vm6`, `cve-vm8`, or `cve-vm9` in this pass.
- Do not redesign all AutoPenBench targets; only repair the mismatches already confirmed by local evidence.

**Design Details:**

1. **Challenge metadata repairs**
   - `network_security/vm1/challenge.json` should advertise `internal_port: 52693`.
   - `network_security/vm2/challenge.json` and `network_security/vm3/challenge.json` should stop claiming `host_ports` exposure. They should explicitly request compose-local networking with the target network as the agent-reachable network.
   - `real-world/cve/vm9/challenge.json` should advertise `internal_port: 445`.

2. **Adapter behavior**
   - `ChallengeJsonAdapter` already honors `exposure_mode`, but only auto-injects compose-local runtime patches for CVEBench. It should also honor explicit `network_mode` and `agent_network` fields from `challenge.json` for AutoPenBench.
   - This keeps the benchmark metadata as the source of truth for targets that are intentionally internal-only.

3. **Runtime materialization**
   - `materialize_compose_runtime()` currently adds host `ports:` whenever `internal_port` is present. It must stop doing that for non-`host_ports` targets.
   - Compose-local network targets should still preserve the correct agent-facing network name so the manager can return it to the agent runtime.

4. **Web readiness**
   - `web_security/docker-compose.yml` should add a MySQL health check for the vm3 database and switch the vm3 app service to `depends_on: condition: service_healthy`.
   - This keeps the application image unchanged while preventing false-negative launches caused by race conditions.

5. **Testing**
   - Add adapter tests that lock in the new metadata behavior for `vm1`, `vm2`, `vm3`, and `vm9`.
   - Add runtime tests that verify compose-local targets do not get public host `ports:` injected.
   - Add a regression test for the vm3 database health-check wiring.

**Risks and Mitigations:**
- The biggest risk is accidentally changing agent connectivity assumptions for existing host-port targets. Mitigate this by scoping compose-local behavior only to targets that explicitly request it in `challenge.json`.
- UDP network targets cannot be validated with the existing host-port readiness probe. The manager already falls back to container-state checks when no public ports exist; tests should lock in that behavior.
