# Overview

TTT-Discover operates on discovery problems: given a scientific problem at test time, find a state s such that R(s) exceeds the state-of-the-art. The problem description d defines an environment — a reward function R(s) and a transition function (s, a) → s'.

A state s is a candidate solution (e.g., a kernel implementation, a mathematical construction). An action a is the LLM's output, typically code with optional reasoning. The policy π_θ generates actions conditioned on d and s. The buffer H stores previous (state, action, reward) tuples for reuse.

The training loop:
1. Sample initial state s from buffer H using PUCT
2. Generate action a ~ π_θ(· | d, s)
3. Transition to s' = T(a), evaluate r = R(s')
4. Update buffer H and model weights θ
