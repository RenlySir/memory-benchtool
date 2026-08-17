from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import redis


@dataclass(frozen=True)
class Target:
    name: str
    product: str
    host: str
    port: int
    db: int = 0
    tls: bool = False
    password_env: Optional[str] = None

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Target":
        required = {"name", "product", "host", "port"}
        missing = sorted(required - raw.keys())
        if missing:
            raise ValueError(f"target is missing fields: {', '.join(missing)}")
        product = str(raw["product"]).lower()
        if product not in {"redis", "tidis"}:
            raise ValueError("target product must be redis or tidis")
        port = int(raw["port"])
        if not 1 <= port <= 65535:
            raise ValueError("target port must be between 1 and 65535")
        return cls(
            name=str(raw["name"]),
            product=product,
            host=str(raw["host"]),
            port=port,
            db=int(raw.get("db", 0)),
            tls=bool(raw.get("tls", False)),
            password_env=raw.get("password_env"),
        )

    @property
    def endpoint(self) -> str:
        return f"{self.host}:{self.port}"

    def client(self) -> redis.Redis:
        password = os.getenv(self.password_env) if self.password_env else None
        return redis.Redis(
            host=self.host,
            port=self.port,
            db=self.db,
            password=password,
            ssl=self.tls,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=5,
        )

    def public_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "product": self.product,
            "endpoint": self.endpoint,
            "db": self.db,
            "tls": self.tls,
            "password_env": self.password_env,
        }


def load_targets(path: Path) -> List[Target]:
    with path.open(encoding="utf-8") as config_file:
        raw = json.load(config_file)
    if not isinstance(raw, dict) or not isinstance(raw.get("targets"), list):
        raise ValueError("config must contain a targets array")
    targets = [Target.from_dict(item) for item in raw["targets"]]
    if not targets:
        raise ValueError("config must contain at least one target")
    names = [target.name for target in targets]
    if len(names) != len(set(names)):
        raise ValueError("target names must be unique")
    return targets
