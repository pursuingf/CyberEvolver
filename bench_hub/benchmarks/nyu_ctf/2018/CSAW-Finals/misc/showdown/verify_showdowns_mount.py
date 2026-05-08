#!/usr/bin/env python3
import sys
from pathlib import Path

import yaml


def main() -> int:
    compose_path = Path(__file__).resolve().parent / "docker-compose.yml"
    data = yaml.safe_load(compose_path.read_text())
    service = data["services"]["showdown"]
    volumes = service.get("volumes", [])
    targets = []
    for volume in volumes:
        if isinstance(volume, str) and ":" in volume:
            targets.append(volume.split(":", 1)[1].split(":", 1)[0])

    missing = []
    for target in ("/showdowns", "/showdown_container"):
        if target not in targets:
            missing.append(f"{target} mount missing")

    env = service.get("environment", {})
    if env.get("DOND_SHIM_MOCK_CONTAINER_ROOT_ON_HOST") != "/mock/root":
        missing.append("DOND_SHIM_MOCK_CONTAINER_ROOT_ON_HOST missing")

    dockerfile = (compose_path.parent / "showdown_server" / "Dockerfile").read_text()
    if 'CMD ["/entrypoint.sh"]' not in dockerfile:
        missing.append("entrypoint wrapper missing")

    if missing:
        print("\n".join(missing))
        return 1
    print("showdown runtime support present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
