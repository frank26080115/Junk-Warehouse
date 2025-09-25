import json
import random
import string
from pathlib import Path


LOWER_HEX = "abcdef"
UPPER_HEX = "ABCDEF"


def transform_string(value: str) -> str:
    characters = []
    for ch in value:
        if "a" <= ch <= "f":
            characters.append(random.choice(LOWER_HEX))
        elif "A" <= ch <= "F":
            characters.append(random.choice(UPPER_HEX))
        elif ch.islower() and ch >= "e":
            characters.append(random.choice(string.ascii_lowercase))
        elif ch.isupper() and ch >= "E":
            characters.append(random.choice(string.ascii_uppercase))
        elif ch.isdigit():
            characters.append(random.choice(string.digits))
        else:
            characters.append(ch)
    return "".join(characters)


def transform_value(value):
    if isinstance(value, str):
        return transform_string(value)
    if isinstance(value, list):
        return [transform_value(item) for item in value]
    if isinstance(value, dict):
        return {key: transform_value(val) for key, val in value.items()}
    return value


def process_file(json_path: Path) -> None:
    with json_path.open("r", encoding="utf-8") as source:
        data = json.load(source)

    transformed = transform_value(data)
    example_path = json_path.with_suffix(json_path.suffix + ".example")
    with example_path.open("w", encoding="utf-8") as target:
        json.dump(transformed, target, ensure_ascii=False, indent=2)
        target.write("\n")


def main() -> None:
    config_dir = Path("config")
    if not config_dir.is_dir():
        raise SystemExit("config directory not found")

    for json_path in sorted(config_dir.rglob("*.json")):
        process_file(json_path)


if __name__ == "__main__":
    main()
