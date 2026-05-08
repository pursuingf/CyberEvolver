# cpu_scheduler.py
import os, time, socket
from collections import deque
from typing import Dict, List
try:
    import psutil  # optional, but nicer for CPU count
except ImportError:
    psutil = None

import ray

@ray.remote(max_concurrency=1)  # serialization gives atomicity
class CpuScheduler:
    def __init__(self, num_cpus_per_task: int, num_persistent_workers: int = 0):
        self.num_cpus_per_task = max(1, int(num_cpus_per_task))
        self.num_persistent_workers = max(0, int(num_persistent_workers))
        self.host_to_cpu_groups: Dict[str, deque[List[int]]] = {}

    def _initialize_cpu_group_for_host(self, host: str):
        # Determine CPU IDs on that host (best effort)
        try:
            # If the actor is running on the host, affinity is exact; otherwise fall back
            cpu_ids = sorted(os.sched_getaffinity(0))
        except Exception:
            n = psutil.cpu_count(logical=True) if psutil else os.cpu_count() or 1
            cpu_ids = list(range(n))

        # Optionally reserve some for "persistent" workers
        if self.num_persistent_workers > 0:
            cpu_ids = cpu_ids[self.num_persistent_workers:]

        # Partition into fixed-size groups
        groups = [cpu_ids[i:i + self.num_cpus_per_task]
                  for i in range(0, len(cpu_ids), self.num_cpus_per_task)]
        # Drop trailing partial group
        groups = [g for g in groups if len(g) == self.num_cpus_per_task]

        if not groups:
            raise RuntimeError(f"No allocatable CPU groups on host {host}.")

        self.host_to_cpu_groups[host] = deque(groups)

    def get_workers_atomic(self, host_name: str) -> List[int]:
        if host_name not in self.host_to_cpu_groups:
            self._initialize_cpu_group_for_host(host_name)

        q = self.host_to_cpu_groups[host_name]
        if q:
            return q.popleft()
        
        return None

    def release_workers_atomic(self, host_name: str, cpu_group: List[int]) -> None:
        if host_name not in self.host_to_cpu_groups:
            self._initialize_cpu_group_for_host(host_name)
        # Push back to the right host queue
        self.host_to_cpu_groups[host_name].append(cpu_group)

    def stats(self) -> Dict[str, Dict[str, int]]:
        return {
            host: {"available_groups": len(q), "group_size": self.num_cpus_per_task}
            for host, q in self.host_to_cpu_groups.items()
        }


## Helpers ##
def current_host() -> str:
    try:
        # Prefer node IP to avoid container-hostname oddities
        import ray
        return ray.util.get_node_ip_address()
    except Exception:
        return socket.gethostname()

def get_cpu_group(scheduler_actor, timeout_s: float | None = None) -> list[int]:
    host = current_host()

    start = time.time()
    while True:
        if timeout_s is not None and (time.time() - start) >= timeout_s:
            raise TimeoutError(f"No CPU group available on {host} within {timeout_s}s.")
        
        cpu_group = ray.get(scheduler_actor.get_workers_atomic.remote(host))
        
        if cpu_group is not None:
            return cpu_group
        
        time.sleep(1)

def release_cpu_group(scheduler_actor, cpu_group: list[int]) -> None:
    host = current_host()
    ray.get(scheduler_actor.release_workers_atomic.remote(host, cpu_group))
