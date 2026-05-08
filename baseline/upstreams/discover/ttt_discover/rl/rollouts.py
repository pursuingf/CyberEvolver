import asyncio
import time
import json
from typing import Sequence

from ttt_discover.tinker_utils.completers import TokenCompleter
from ttt_discover.tinker_utils.state import to_json_serializable
from ttt_discover.rl.types import (
    Env,
    EnvGroupBuilder,
    Trajectory,
    TrajectoryGroup,
    Transition,
)
from ttt_discover.tinker_utils import logtree


@logtree.scope_header_decorator
async def do_single_rollout(policy: TokenCompleter, env: Env, step_idx: int) -> Trajectory:
    transitions = []
    # TODO: Probably have initial observation return a tree node of the state, pass into env.step
    ob, stop_condition = await env.initial_observation()
    while True:
        t_policy_start = time.time()
        ac_with_logprobs = await policy(ob, stop_condition)
        t_policy = time.time() - t_policy_start

        t_env_start = time.time()
        step_result = await env.step(ac_with_logprobs.tokens, step_idx)
        t_env = time.time() - t_env_start

        step_metrics = dict(step_result.metrics) if step_result.metrics else {}
        step_metrics["time/policy"] = t_policy
        step_metrics["time/env_step"] = t_env
        transition = Transition(
            ob=ob,
            ac=ac_with_logprobs,
            reward=step_result.reward,
            episode_done=step_result.episode_done,
            metrics=step_metrics,
        )
        transitions.append(transition)
        ob = step_result.next_observation
        stop_condition = step_result.next_stop_condition
        if step_result.episode_done:
            break
    return Trajectory(transitions=transitions, final_ob=ob)


@logtree.scope_header_decorator
async def do_group_rollout(
    env_group_builder: EnvGroupBuilder, policy: TokenCompleter, step_idx: int
) -> TrajectoryGroup:
    envs_G: Sequence[Env] = await env_group_builder.make_envs()
    trajectories_G = await asyncio.gather(*[do_single_rollout(policy, env, step_idx) for env in envs_G])

    t_reward_start = time.time()
    rewards_and_metrics_G = await env_group_builder.compute_group_rewards(trajectories_G, envs_G)
    t_reward = time.time() - t_reward_start
    rewards_G, metrics_G = zip(*rewards_and_metrics_G, strict=True)

    per_traj_group_reward = t_reward / max(1, len(trajectories_G))
    for metrics in metrics_G:
        if isinstance(metrics, dict):
            metrics["time/reward_compute"] = per_traj_group_reward

    # Log trajectory tables with final rewards
    with logtree.scope_header("Trajectory Summary"):
        for i, (traj, final_reward, reward_metrics) in enumerate(
            zip(trajectories_G, rewards_G, metrics_G, strict=True)
        ):
            rows = []
            step_reward_sum = 0.0
            for t_idx, t in enumerate(traj.transitions):
                step_reward_sum += t.reward
                rows.append(
                    {
                        "step": t_idx,
                        "ob_len": t.ob.length,
                        "ac_len": len(t.ac.tokens),
                        "reward": f"{t.reward:.3f}",
                    }
                )
            # Add final row with final observation and computed reward
            rows.append(
                {
                    "step": "final",
                    "ob_len": traj.final_ob.length,
                    "ac_len": "-",
                    "reward": f"{final_reward:.3f}",
                }
            )
            # Add total reward row
            rows.append(
                {
                    "step": "total",
                    "ob_len": "-",
                    "ac_len": "-",
                    "reward": f"{step_reward_sum + final_reward:.3f}",
                }
            )
            logtree.table(rows, caption=f"Trajectory {i}")

            metrics_payload = {"final_reward": final_reward, "total_reward": step_reward_sum + final_reward}
            if reward_metrics:
                metrics_payload["reward_metrics"] = reward_metrics
            step_metrics = [
                {"step": idx, **transition.metrics}
                for idx, transition in enumerate(traj.transitions)
                if transition.metrics
            ]
            if step_metrics:
                metrics_payload["step_metrics"] = step_metrics
            logtree.details(json.dumps(to_json_serializable(metrics_payload), indent=2), summary=f"Trajectory {i} metrics")

    return TrajectoryGroup(trajectories_G, list(rewards_G), list(metrics_G))
