# Skill Design Pattern

This document defines how a **good skill** should be structured and written.
It is not an exploit recipe, but a **template for codifying reusable expertise**.

---

## 1. Core Philosophy (L2)

### Practicality First (Feasibility Gate)

A skill must prioritize techniques that are **technically feasible** under typical CTF conditions (ASLR/PIE, badchars, timeouts, limited interaction, inconsistent layouts).
If a "clever" method is usually dominated by a simpler bypass (change input path, switch primitive, leak+compute, standard ret2libc/SROP, etc.), it must be treated as a **fallback**, not the main theme.

### Degrees of Freedom

A good skill separates **reasoning freedom** from **execution rigidity**:

* **High Freedom (Text / Pseudocode)**
  Use natural language or pseudocode for:

  * hypotheses
  * decision points and branch conditions
  * trade-offs
  * risks and assumptions
    This is required when runtime state is ephemeral (leaks, per-run randomness, per-session layout).

* **Low Freedom (Templates / Tools)**
  Use concrete templates only when:

  * the operation is stable and repeatable
  * parameters can be safely substituted
  * state consistency is preserved
    Avoid runnable fragments that assume state persists across separate steps/runs.

### Progressive Disclosure

Do not dump raw data, long logs, or exhaustive lists.
A skill should teach **what signals matter and how to extract them** using targeted filters (`grep`, narrow queries), not overwhelm with noise.

---

## 2. Skill Content Requirements (What a good SKILL.md must contain)

### Theory (Decision-Relevant Foundations)

Theory must be compact and practical:

* include **invariants / gotchas** that change decisions in this domain
* prioritize what is easy to miss and commonly causes failure
* avoid textbook filler

### Tips (High-Leverage Details)

Include a short list of **high-impact practical details** that improve success rate.
These should capture “things people forget” (precision, granularity, stability assumptions, common pitfalls), not general advice.

Good examples:
- Under ASLR, state which bits/bytes of an address are expected to vary and which are stable due to page alignment (e.g., the page offset / low bytes). If you rely on a “fixed suffix” for a partial overwrite, say exactly how many bytes and add a quick check that the mapping matches the assumption.
- For shellcode-injection, explicitly budget the usable stack/frame space. If the space is too small, consider a stager / short jump trampoline to reach a larger buffer rather than forcing a full payload into a tiny region.
- When the ROP payload must be very short, don’t default to a full chain. Consider stack pivot / stack alignment/offset fixes, or switching to a minimal primitive (e.g., ret2libc with one leak) instead of an overlong chain.

Bad examples:
- Be careful with offsets and memory addresses.
- Always keep notes and stay organized.
- Make sure your payload is correct and test it locally.

### Technique Library (Core of the Skill)

List 2–5 practical approaches/patterns. For each, include:

* **When to use** (conditions)
* **When NOT to use** (failure conditions)
* **Trade-offs**
* **Minimal building block** (small, reusable pattern/snippet)
* **Quick verification** (a short check to confirm it’s working)

Order techniques by practicality; rare techniques go last and are explicitly labeled as fallback.

### Workflow (Decision-Oriented Phases)

Phases exist to structure *decisions*, not to enforce blind execution.
A typical pattern:

* **Phase 1: Orientation**

  * what problem class is this?
  * what assumptions are risky?
  * what cheap checks reduce uncertainty?

* **Phase 2: Technique Selection**

  * enumerate viable approaches (from Technique Library)
  * include explicit switch criteria:
    *if verification fails → switch to approach X; else continue*

* **Phase 3: Controlled Execution**

  * introduce only **small parameterized building blocks**
  * if runtime state matters, describe **value flow** in pseudocode:
    `observe → derive → act → verify`
  * avoid multi-step runnable scripts that break state continuity

* **Phase 4: Consolidation**

  * summarize what values must remain consistent within a single run/session
  * explicitly state when you must restart the process rather than continue

### Common Failure Modes & Recovery (REQUIRED)

A good skill must include 3–7 bullets:

* **symptom → likely cause → next action**
  Keep them technical and actionable.

---

## 3. Templates (Conditional, Not Always Required)

Templates are optional and must be used carefully:

* Use templates only when they **genuinely generalize** for this domain.
* Templates MUST be **parameterized**:

  * **NO hard-coded runtime values** (addresses, leaks, offsets, libc bases, ports, stack indices, etc.)
  * every variable must be a placeholder (e.g., `<OFFSET>`, `<ADDR>`, `<INDEX>`) with a one-line note on where it comes from
* Prefer **small composable templates** over “final scripts”.

If a domain is highly instance-dependent (common for shellcode/fmt/complex pwn constraints), do NOT force a full end-to-end template.
Instead, end with a short **Assembly Guide**:

* 3–6 bullets mapping conditions to techniques:
  *if A → use approach X + which building blocks; else if B → approach Y; else → fallback Z.*

---

## 4. Tool Documentation Standard (Optional)

*Tools are invisible to the LLM unless documented here.*
Tools are optional and should only exist if they clearly add value.

### Tool Documentation Rules

* Explain **what problem the tool solves** (and why CLI alternatives are insufficient).
* State:

  * expected inputs
  * outputs
  * failure behavior
  * how to verify results
* Avoid tools that merely wrap existing CLI commands unless they significantly improve reliability.

#### Example Structure (Illustrative Only)

```
tool_name --arg1 <value> [--arg2 <value>]
```

Include:

* when to use it
* when NOT to use it
* one smoke-test command

---

## Final Note

A good skill teaches **how to decide and adapt within a domain**, not how to follow a checklist.
If following the skill blindly would likely fail due to changing state or assumptions, the skill is written incorrectly.