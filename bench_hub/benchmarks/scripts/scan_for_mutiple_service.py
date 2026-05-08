import os
import yaml
from pathlib import Path

# Configure the benchmark root directory.
BENCHMARK_ROOT = Path("./benchmarks").resolve()

def scan_multi_service_challenges():
    print(f"Scanning {BENCHMARK_ROOT} for multi-service docker-compose files...\n")
    
    count = 0
    for root, dirs, files in os.walk(BENCHMARK_ROOT):
        if "docker-compose.yml" in files:
            file_path = Path(root) / "docker-compose.yml"
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f)
                
                if not config or 'services' not in config:
                    continue

                services = config['services']
                svc_count = len(services)

                # Keep files that define at least two services.
                if svc_count >= 2:
                    # Print a compact relative-looking path.
                    print(f"[{svc_count} Services] {file_path}")
                    print(f"    Services: {list(services.keys())}")
                    
                    # Also report which services define aliases.
                    for svc_name, svc_conf in services.items():
                        networks = svc_conf.get('networks', {})
                        has_alias = False
                        if isinstance(networks, dict):
                            for net_conf in networks.values():
                                if net_conf and 'aliases' in net_conf:
                                    has_alias = True
                                    print(f"    -> {svc_name} has aliases: {net_conf['aliases']}")
                        if not has_alias:
                            print(f"    -> {svc_name} (Internal/No Alias)")
                    print("-" * 40)
                    count += 1

            except Exception as e:
                print(f"Error reading {file_path}: {e}")

    print(f"\nTotal multi-service challenges found: {count}")

if __name__ == "__main__":
    scan_multi_service_challenges()
