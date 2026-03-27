import json
import os

DB_FILE = "User_Data.json"


def _ensure_db_file():
    """Гарантирует наличие файла БД и возвращает словарь всех пользователей."""
    if not os.path.exists(DB_FILE):
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)
        return {}
    with open(DB_FILE, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except Exception:
            data = {}
    if not isinstance(data, dict):
        data = {}
    return data


def load_user(user_id: int):
    data = _ensure_db_file()
    default_user = {
        "city": None,
        "lat": None,
        "lon": None,
        "notifications": {"enabled": False, "interval_h": 2, "last_ts": 0},
    }
    user = data.get(str(user_id), {})
    # Аккуратно дополняем профиль пользователя значениями по умолчанию,
    # чтобы не ломать уже сохранённые данные
    if "notifications" not in user or not isinstance(user.get("notifications"), dict):
        user["notifications"] = default_user["notifications"].copy()
    else:
        user["notifications"].setdefault("enabled", False)
        user["notifications"].setdefault("interval_h", 2)
        user["notifications"].setdefault("last_ts", 0)
    user.setdefault("city", None)
    user.setdefault("lat", None)
    user.setdefault("lon", None)
    return user


def save_user(user_id: int, user_data: dict):
    all_data = _ensure_db_file()
    all_data[str(user_id)] = user_data
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(all_data, f, indent=4, ensure_ascii=False)


def load_all_users():
    """
    Возвращает словарь всех пользователей из файла.
    Ключи — строки user_id, значения — словари с данными пользователя.
    """
    return _ensure_db_file()