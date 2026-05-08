import itertools
from collections import defaultdict
from typing import Dict, List

import numpy as np
from ttt_discover.rl.types import EnvGroupBuilder, RLDataset, TrajectoryGroup
from ttt_discover.tinker_utils.misc_utils import all_same, dict_mean


def _compute_by_group_metrics(trajectory_groups_P: List[TrajectoryGroup], good_thresh: float = 0.5):
    n_groups = len(trajectory_groups_P)
    n_mixed = n_good = n_bad = 0
    for tg in trajectory_groups_P:
        grp_rewards = tg.get_total_rewards()
        if all_same(grp_rewards):
            if grp_rewards[0] >= good_thresh:
                n_good += 1
            else:
                n_bad += 1
        else:
            n_mixed += 1
    return {
        "by_group/frac_mixed": n_mixed / n_groups,
        "by_group/frac_all_good": n_good / n_groups,
        "by_group/frac_all_bad": n_bad / n_groups,
    }


def get_log_table(trajectory_groups_P: List[TrajectoryGroup]):
    table_row_list = []
    # @xh: loop over all groups
    for i in range(len(trajectory_groups_P)):
        if 'response' not in trajectory_groups_P[i].trajectories_G[0].transitions[0].metrics:
            return None
        # @xh: loop inside a group
        for j in range(len(trajectory_groups_P[i].trajectories_G)):
            traj_metrics = trajectory_groups_P[i].trajectories_G[j].transitions[0].metrics  # @xh: len(transitions) always =1?
            prompt = traj_metrics['prompt']
            response = traj_metrics['response']
            msg = str(traj_metrics['msg'])
            reward = traj_metrics['reward']
            correct = traj_metrics['correctness']
            parsed_code = traj_metrics['parsed_code']
            initial_raw_score = traj_metrics.get('initial_raw_score')
            table_row_list.append(
                (prompt, response, reward, correct, parsed_code, msg, initial_raw_score)
            )
    
    table_row_list = None if len(table_row_list) == 0 else table_row_list

    return table_row_list


def remove_non_numerical_field(trajectory_groups_P: List[TrajectoryGroup]):
    for i in range(len(trajectory_groups_P)):
        for j in range(len(trajectory_groups_P[i].trajectories_G)):
            traj_metrics = trajectory_groups_P[i].trajectories_G[j].transitions[0].metrics  # @xh: len(transitions) always =1?
            for k in ['prompt_hash', 'predicted_grid', 'prompt', 'response', 'ref']:
                if k in traj_metrics:
                    traj_metrics.pop(k)


def compute_trajectory_metrics(
    trajectory_groups_P: List[TrajectoryGroup], taglist_P: List[list[str]]
) -> Dict[str, float]:
    tag2trajgroups = defaultdict(list)
    for taglist, trajectory_group in zip(taglist_P, trajectory_groups_P):
        for tag in taglist:
            tag2trajgroups[tag].append(trajectory_group)
    out = {}
    have_nontrivial_tags = any(
        len(trajgroups) < len(trajectory_groups_P) for trajgroups in tag2trajgroups.values()
    )  # check if any tag gives us a strict subset of the full trajectory groups
    if have_nontrivial_tags:
        for tag, trajectory_groups in tag2trajgroups.items():
            prefixed_metrics = {
                f"env/{tag}/{k}": v
                for k, v in _compute_trajectory_metrics(trajectory_groups).items()
            }
            out.update(prefixed_metrics)
    
    log_table = get_log_table(trajectory_groups_P)
    if log_table is not None:
        out.update({'table': log_table})

    remove_non_numerical_field(trajectory_groups_P)  # @xh: before `_compute_trajectory_metrics`
    
    out.update(
        {f"env/all/{k}": v for k, v in _compute_trajectory_metrics(trajectory_groups_P).items()}
    )
    return out


def _compute_trajectory_metrics(trajectory_groups_P: List[TrajectoryGroup]) -> Dict[str, float]:
    """Compute metrics for the trajectory groups."""
    flat_trajs_PG = [traj for tg in trajectory_groups_P for traj in tg.trajectories_G]
    flat_transitions = [t for traj in flat_trajs_PG for t in traj.transitions]
    ac_tokens_by_turn = [len(t.ac.tokens) for t in flat_transitions]
    ob_tokens_by_turn = [t.ob.length for t in flat_transitions]
    turns_by_trajectory = [len(traj.transitions) for traj in flat_trajs_PG]
    # Timing metrics - compute true mean/max across all transitions
    policy_times = [(t.metrics or {}).get("time/policy", 0.0) for t in flat_transitions]
    env_step_times = [(t.metrics or {}).get("time/env_step", 0.0) for t in flat_transitions]
    # Compute metrics
    metrics = {
        "ac_tokens_per_turn": sum(ac_tokens_by_turn) / sum(turns_by_trajectory),
        "ob_tokens_per_turn": sum(ob_tokens_by_turn) / sum(turns_by_trajectory),
        "turns_per_episode": sum(turns_by_trajectory) / len(flat_trajs_PG),
        "total_episodes": len(flat_trajs_PG),
        "total_turns": sum(turns_by_trajectory),
        "total_ac_tokens": sum(ac_tokens_by_turn),
        "total_ob_tokens": sum(ob_tokens_by_turn),
        "time/sampling_mean": np.mean(policy_times).item() if policy_times else 0.0,
        "time/sampling_max": np.max(policy_times).item() if policy_times else 0.0,
        "time/env_step_mean": np.mean(env_step_times).item() if env_step_times else 0.0,
        "time/env_step_max": np.max(env_step_times).item() if env_step_times else 0.0,
    }
    gr_rewards = [reward for tg in trajectory_groups_P for reward in tg.get_total_rewards()]
    metrics["reward/mean"] = np.mean(gr_rewards).item()
    metrics["reward/max"] = np.max(gr_rewards).item()
    metrics["reward/min"] = np.min(gr_rewards).item()
    # Per-transition metrics
    transition_metrics = [
        transition.metrics
        for tg in trajectory_groups_P
        for traj in tg.trajectories_G
        for transition in traj.transitions
    ]
    traj_metrics = [metrics for tg in trajectory_groups_P for metrics in tg.metrics_G]
    metrics.update(dict_mean(transition_metrics + traj_metrics))
    # combine traj_metrics and transition_metrics in case there's some key
    # (like format error) that appears in the per-step metrics for some envs
    # but the compute_group_rewards metric for other envs.
    metrics.update(_compute_by_group_metrics(trajectory_groups_P))
    return metrics


def dataset_to_env_group_builders(dataset: RLDataset) -> list[EnvGroupBuilder]:
    """
    Get the whole dataset as a list of env group builders.
    """
    return list(itertools.chain(*[dataset.get_batch(i) for i in range(len(dataset))]))
