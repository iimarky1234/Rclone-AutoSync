"""Configuration and storage manager for rasync."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional

from .models import GlobalConfig, SyncProfile, SyncRunResult

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "rasync"


def get_config_dir() -> Path:
    config_dir = os.environ.get("RASYNC_CONFIG_DIR")
    path = Path(config_dir).expanduser() if config_dir else DEFAULT_CONFIG_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_socket_path() -> Path:
    config = load_global_config()
    path = Path(config.socket_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def get_log_path() -> Path:
    config = load_global_config()
    path = Path(config.log_file).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_global_config() -> GlobalConfig:
    config_file = get_config_dir() / "config.json"
    if not config_file.exists():
        default_cfg = GlobalConfig()
        save_global_config(default_cfg)
        return default_cfg

    try:
        data = json.loads(config_file.read_text(encoding="utf-8"))
        return GlobalConfig.model_validate(data)
    except Exception:
        return GlobalConfig()


def save_global_config(config: GlobalConfig) -> None:
    config_file = get_config_dir() / "config.json"
    config_file.write_text(
        config.model_dump_json(indent=2),
        encoding="utf-8"
    )


def load_profiles() -> List[SyncProfile]:
    profiles_file = get_config_dir() / "profiles.json"
    if not profiles_file.exists():
        return []

    try:
        data = json.loads(profiles_file.read_text(encoding="utf-8"))
        return [SyncProfile.model_validate(p) for p in data]
    except Exception:
        return []


def save_profiles(profiles: List[SyncProfile]) -> None:
    profiles_file = get_config_dir() / "profiles.json"
    data = [p.model_dump() for p in profiles]
    profiles_file.write_text(
        json.dumps(data, indent=2),
        encoding="utf-8"
    )


def get_profile_by_id(profile_id: str) -> Optional[SyncProfile]:
    for p in load_profiles():
        if p.id == profile_id:
            return p
    return None


def get_profile_by_name(name: str) -> Optional[SyncProfile]:
    for p in load_profiles():
        if p.name.lower() == name.lower():
            return p
    return None


def upsert_profile(profile: SyncProfile) -> None:
    profiles = load_profiles()
    index = next((i for i, p in enumerate(profiles) if p.id == profile.id), -1)
    if index >= 0:
        profiles[index] = profile
    else:
        profiles.append(profile)
    save_profiles(profiles)


def delete_profile(profile_id: str) -> bool:
    profiles = load_profiles()
    new_profiles = [p for p in profiles if p.id != profile_id]
    if len(new_profiles) != len(profiles):
        save_profiles(new_profiles)
        return True
    return False


def load_history(limit: int = 50) -> List[SyncRunResult]:
    history_file = get_config_dir() / "history.json"
    if not history_file.exists():
        return []

    try:
        data = json.loads(history_file.read_text(encoding="utf-8"))
        items = [SyncRunResult.model_validate(r) for r in data]
        return items[-limit:]
    except Exception:
        return []


def append_history(result: SyncRunResult, max_entries: int = 200) -> None:
    history = load_history(limit=max_entries)
    history.append(result)
    if len(history) > max_entries:
        history = history[-max_entries:]

    history_file = get_config_dir() / "history.json"
    data = [r.model_dump() for r in history]
    history_file.write_text(
        json.dumps(data, indent=2),
        encoding="utf-8"
    )
