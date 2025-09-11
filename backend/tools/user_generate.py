#!/usr/bin/env python3
# /backend/tools/user_generate.py

from __future__ import annotations
import argparse
import json
import sys
from hashlib import sha256
from pathlib import Path
from typing import Dict, Any, List


def project_root() -> Path:
    """
    Resolve project root independent of current working directory.
    Expected file location: /backend/tools/user_generate.py
    Project root is two levels up from /backend.
    """
    return Path(__file__).resolve().parents[2]


def config_dir() -> Path:
    return project_root() / "config"


def secrets_path() -> Path:
    return config_dir() / "secrets.json"


def users_path() -> Path:
    return config_dir() / "users.json"


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except Exception as e:
        raise RuntimeError(f"Failed to read {path}: {e}")


def dump_json_atomic(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def ensure_users_file(path: Path) -> Dict[str, Any]:
    """
    Ensure users.json exists and has the expected structure.
    If the file doesn't exist, create: {"users": []}
    """
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        dump_json_atomic(path, {"users": []})
        return {"users": []}

    obj = load_json(path)
    if not isinstance(obj, dict) or "users" not in obj or not isinstance(obj["users"], list):
        raise RuntimeError(
            f"{path} is malformed; expected an object with a 'users' array."
        )
    return obj


def get_salt_from_secrets(path: Path) -> str:
    """
    Read /config/secrets.json and return the 'user_password_salt' string.
    (Per your design, this salt should never be sent to the frontend.)
    """
    data = load_json(path)
    if not isinstance(data, dict) or "user_password_salt" not in data:
        raise RuntimeError(
            f"{path} must contain a string field 'user_password_salt'."
        )
    salt = data["user_password_salt"]
    if not isinstance(salt, str) or not salt:
        raise RuntimeError("'user_password_salt' must be a non-empty string.")
    return salt


def password_hash(password: str, salt: str) -> str:
    # sha256(password + salt), hex digest
    return sha256((password + salt).encode("utf-8")).hexdigest()


def upsert_user(users_obj: Dict[str, Any], username: str, hex_hash: str, overwrite: bool) -> bool:
    """
    Insert or update the user in users_obj.
    Returns True if the file should be written (inserted or updated),
    False if nothing changed (e.g., user exists and overwrite=False).
    """
    users_list: List[Dict[str, str]] = users_obj["users"]

    for entry in users_list:
        if not isinstance(entry, dict):
            continue
        if entry.get("username") == username:
            if not overwrite:
                print(f"Error: user '{username}' already exists. Use --overwrite to update.", file=sys.stderr)
                return False
            entry["hash"] = hex_hash
            return True

    users_list.append({"username": username, "hash": hex_hash})
    return True


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate or update a user entry in /config/users.json."
    )
    parser.add_argument("username", help="Username to create or update.")
    parser.add_argument("password", help="Plaintext password (will be hashed with the salt).")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="If set, update the existing user's hash. Otherwise, refuse if user exists."
    )

    args = parser.parse_args(argv)

    try:
        salt = get_salt_from_secrets(secrets_path())
    except FileNotFoundError:
        print(f"Error: {secrets_path()} not found.", file=sys.stderr)
        return 2
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    try:
        users_obj = ensure_users_file(users_path())
    except (FileNotFoundError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    # Compute hash
    hex_hash = password_hash(args.password, salt)

    # Upsert
    changed = upsert_user(users_obj, args.username, hex_hash, args.overwrite)
    if not changed:
        return 1  # nothing written due to existing user and no --overwrite

    try:
        dump_json_atomic(users_path(), users_obj)
    except Exception as e:
        print(f"Error writing {users_path()}: {e}", file=sys.stderr)
        return 2

    if args.overwrite:
        print(f"Updated user '{args.username}'.")
    else:
        print(f"Created user '{args.username}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
