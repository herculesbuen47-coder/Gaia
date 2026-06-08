import json
import os
from pathlib import Path
from typing import Any, Dict

STORAGE_FILE = Path(__file__).parent / "player_data.json"

DEFAULT_ATTRIBUTES = {
    "razao": 0,
    "emocao": 0,
    "vazio": 0,
    "investigacao": 0,
    "acao": 0,
}


def load_data() -> Dict[str, Any]:
    if not STORAGE_FILE.exists():
        return {}
    try:
        with STORAGE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}


def save_data(data: Dict[str, Any]) -> None:
    with STORAGE_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_player(user_id: int) -> Dict[str, Any]:
    data = load_data()
    return data.get(str(user_id), {})


def persist_player(user_id: int, player_data: Dict[str, Any]) -> None:
    data = load_data()
    data[str(user_id)] = player_data
    save_data(data)


def create_new_player() -> Dict[str, Any]:
    return {
        "chapter": 1,
        "block": 1,
        "awaiting_choice": False,
        "finished": False,
        "attributes": DEFAULT_ATTRIBUTES.copy(),
        "choices": [],
    }


# Vale Sereno persistence helpers
VALE_FILE = Path(__file__).parent / "vale_data.json"


def load_vale_data() -> Dict[str, Any]:
    if not VALE_FILE.exists():
        return {}
    try:
        with VALE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}


def save_vale_data(data: Dict[str, Any]) -> None:
    with VALE_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_vale(user_id: int) -> Dict[str, Any]:
    data = load_vale_data()
    return data.get(str(user_id), {})


def persist_vale(user_id: int, state: Dict[str, Any]) -> None:
    data = load_vale_data()
    data[str(user_id)] = state
    save_vale_data(data)


def delete_vale(user_id: int) -> None:
    data = load_vale_data()
    if str(user_id) in data:
        del data[str(user_id)]
        save_vale_data(data)
