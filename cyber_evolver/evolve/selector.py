from typing import List, Dict, Tuple
from abc import ABC, abstractmethod
from .node import EvolutionNode
from dataclasses import dataclass

@dataclass
class SelectionResult:
    selected: List[EvolutionNode]
    metrics: Dict[str, float]  # e.g., {'success_mean': 0.65, 'diversity': 0.32}

class BaseSelector(ABC):
    @abstractmethod
    def select(self, nodes: List[EvolutionNode], k: int) -> SelectionResult:
        pass

class TopKSelector(BaseSelector):
    def __init__(
        self,
        primary_key: str = "success_rate",
        secondary_key: str = "-avg_steps",  # '-' means descending; smaller raw values are better.
    ):
        self.primary_key = primary_key
        self.secondary_key = secondary_key

    def select(self, nodes: List[EvolutionNode], k: int) -> SelectionResult:
        for node in nodes:
            if node.total_runs > 0:
                node.avg_steps = node.avg_steps / node.total_runs
                node.avg_duration = node.avg_duration / node.total_runs
                node.avg_token_num = node.avg_token_num / node.total_runs
                node.success_rate = node.success_count / node.total_runs

        def _get_key(node: EvolutionNode) -> Tuple:
            pri = getattr(node, self.primary_key, 0.0)
            sec = getattr(node, self.secondary_key.lstrip("-"), 0.0)
            if self.secondary_key.startswith("-"):
                sec = -sec
            return (pri, sec)

        sorted_nodes = sorted(nodes, key=_get_key, reverse=True)
        selected = sorted_nodes[:k]

        # Metrics for logging
        success_rates = [n.success_rate for n in selected if n.total_runs > 0]
        metrics = {
            "success_mean": sum(success_rates) / len(success_rates) if success_rates else 0.0,
            "selected_count": len(selected),
        }

        return SelectionResult(selected=selected, metrics=metrics)


# --- Extension points ---
# class TournamentSelector(BaseSelector): ...
# class NSGA2Selector(BaseSelector): ...
