from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseRewardEvaluator(ABC):
    """
    Minimal base interface for reward evaluators.

    Note: Concrete evaluators may return richer objects than a float; the contract
    here is intentionally small, as requested.
    """

    @abstractmethod
    def get_reward(self, code: str, state: Any) -> float:
        raise NotImplementedError