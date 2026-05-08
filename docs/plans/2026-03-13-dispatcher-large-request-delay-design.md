# Dispatcher Large Request Delay Design

**Goal:** Delay high-token LLM requests inside the centralized dispatcher to reduce burst pressure on the upstream server.

**Architecture:** Keep the existing single dispatcher and lane scheduler. Classify requests as "large" using an estimated `input_tokens + max_tokens` threshold. For large requests only, sleep for a fixed delay immediately before the HTTP call, then report large-request activity in dispatcher metrics and summary logs.

**Scope:** Minimal change set. No separate large/small queues, no global token bucket, no fairness redesign.
