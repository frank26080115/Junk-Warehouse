from __future__ import annotations

import os
import sys
from pathlib import Path
import json
import requests
import math
from typing import List, Union

# Guarantee that the repository root is discoverable on sys.path when this
# module is invoked directly. This keeps imports such as app.config_loader
# working during local scripting without requiring a package-style launch.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from openai import OpenAI
try:
    from ..app.config_loader import CONFIG_DIR, CONFIG_PATH, load_app_config
except:
    CONFIG_DIR = REPO_ROOT / "config"
    CONFIG_PATH = CONFIG_DIR / "appconfig.json"
    def load_app_config():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

from sentence_transformers import SentenceTransformer
from huggingface_hub import HfApi

import logging
log = logging.getLogger(__name__)

OLLAMA_HOST_URL = "http://127.0.0.1:11434"

def ensure_ollama_up(host=OLLAMA_HOST_URL, wait_sec=8):
    import requests, time, subprocess, platform

    # 1) Probe
    try:
        r = requests.get(f"{host}/api/version", timeout=1)
        if r.ok:
            return True
    except Exception:
        pass

    # 2) Try to start (requires Ollama installed and on PATH)
    try:
        if platform.system() == "Windows":
            creationflags = 0
            if hasattr(subprocess, "DETACHED_PROCESS"):
                creationflags |= subprocess.DETACHED_PROCESS
            if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
                creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
            subprocess.Popen(
                ["ollama", "serve"],
                creationflags=creationflags,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT
            )
        else:
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT
            )
    except FileNotFoundError:
        raise RuntimeError("Ollama not found on PATH. Install it and try again.")

    # 3) Wait for it to come up
    deadline = time.time() + wait_sec
    while time.time() < deadline:
        try:
            r = requests.get(f"{host}/api/version", timeout=1)
            if r.ok:
                return True
        except Exception:
            time.sleep(0.25)

    raise RuntimeError("Ollama did not start in time.")

def list_ollama_models(host: str = OLLAMA_HOST_URL) -> list[str]:
    """
    Return a list of available Ollama model names.
    """
    ensure_ollama_up(host)
    url = f"{host}/api/tags"
    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        return [m["name"] for m in data.get("models", [])]
    except Exception as e:
        print(f"Error fetching models: {e}")
        return []

def is_model_online(model: str) -> bool:
    """
    Heuristic:
      - If model exists in local Ollama tags -> offline (False).
      - If it's a known OpenAI model name -> online (True).
      - Otherwise raise (better to fail loud than silently route wrong).
    """
    local = set(list_ollama_models())
    if model in local:
        return False

    try:
        # Fast metadata check
        HfApi().model_info(model)
        return True
    except Exception:
        pass

    # expand as needed
    known_online = {
        "gpt-5-mini", "gpt-5-nano", "gpt-4o-mini", "gpt-4o", "gpt-4.1-mini",
        "o4-mini", "o3-mini"
    }
    if model in known_online or model.startswith("gpt-"):
        return True
    raise ValueError(f"Unknown AI model specified: {model}")

def get_openai_client():
    secrets_path = CONFIG_DIR / "secrets.json"
    api_key: str | None = None
    if secrets_path.exists():
        try:
            data = json.loads(secrets_path.read_text(encoding="utf-8"))
            api_key = data.get("openai_api_key")
        except Exception:
            pass
    # Fallback to environment variable
    api_key = api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OpenAI API key not found. Set OPENAI_API_KEY or provide secrets.json with 'openai_api_key'."
        )
    return OpenAI(api_key=api_key)

class LlmAi(object):
    def __init__(self, model: str):
        self.model = model
        self.appconfig = load_app_config()

        # Map convenience aliases
        if self.model == "online":
            self.model = self.appconfig["llm_model_online"]
        elif self.model == "offline":
            self.model = self.appconfig["llm_model_offline"]

        # IMPORTANT: decide online/offline based on the resolved model
        self.is_online = is_model_online(self.model)

        if not self.is_online:
            ensure_ollama_up()
            self.client = OpenAI(
                base_url=OLLAMA_HOST_URL + "/v1",  # Ollama's OpenAI-compatible endpoint
                api_key="ollama"  # any non-empty string
            )
        else:
            self.client = get_openai_client()

        self.tool = None
        self.system_msg = None

    def remove_tool(self):
        self.tool = None

    def make_tool(self, desc: str, param_props, system_msg:str = None, required="all", name: str = "return_tool"):
        """
        example of param_props:
        {
            "related": {"type": "string", "enum": ["YES","NO"]},
            "answer":  {"type": "string"},
            "evidence":{"type": "array", "items": {"type":"string"}, "maxItems": 5}
        }
        """

        # it is unlikely a user is going to define a tool without knowing what to say as a system message
        if system_msg:
            self.system_msg = system_msg

        self.tool = {
            "type": "function",
            "function": {
                "name": name,
                "description": desc,
                "parameters": {
                    "type": "object",
                    "properties": param_props,
                    "additionalProperties": False
                }
            }
        }
        if required:
            params = self.tool["function"]["parameters"]
            if required == "all":
                # copy all keys from properties into required
                props = params.get("properties", {}) or {}
                params["required"] = list(props.keys())
            elif isinstance(required, list):
                params["required"] = required
            else:
                raise TypeError(f"param 'required' must be a list of strings, but it is '{type(required)}'")

    def query(self, user_msg: Union[List[str], str], system_msg: str = None):
        if not system_msg:
            system_msg = self.system_msg
        self.msg = [{"role": "system", "content": system_msg}] if system_msg else []

        if isinstance(user_msg, str):
            msg_lst = [user_msg]
        elif isinstance(user_msg, list):
            msg_lst = user_msg[:]
        else:
            raise TypeError(f"param 'user_msg' must be str or list[str], but it is '{type(user_msg)}'")

        for m in msg_lst:
            self.msg.append({"role": "user", "content": m})

        if self.tool:
            res = self.client.chat.completions.create(
                model=self.model,
                messages=self.msg,
                tools=[self.tool],
                tool_choice={"type": "function", "function": {"name": self.tool["function"]["name"]}}
            )
            msg = res.choices[0].message
            # Guard for tool calls presence
            if not getattr(msg, "tool_calls", None):
                # Model ignored the tool; return normal content
                return getattr(msg, "content", "")
            call = msg.tool_calls[0]
            # OpenAI SDK returns objects with .function.arguments (JSON string)
            args_json = call.function.arguments
            return json.loads(args_json) if isinstance(args_json, str) else args_json
        else:
            res = self.client.chat.completions.create(
                model=self.model,
                messages=self.msg
            )
            return res.choices[0].message.content

def _emb_normalize(v: List[float]) -> List[float]:
    n = math.sqrt(sum(x*x for x in v)) or 1.0
    return [x / n for x in v]

class EmbeddingAi(object):
    def __init__(self, model: str = "offline"):
        self.model = model
        self.appconfig = load_app_config()

        # Map convenience aliases
        if self.model == "online":
            self.is_online = True
            self.model = self.appconfig["emb_model_online"]
            self.client = get_openai_client()
        elif self.model == "offline":
            self.is_online = False
            self.model = self.appconfig["emb_model_offline"]
            self.st = SentenceTransformer(self.model)
        else:
            # When a concrete model name is supplied, infer whether it is an online or offline option
            # and initialize the appropriate client lazily so downstream calls operate consistently.
            self.is_online = is_model_online(self.model)
            if self.is_online:
                self.client = get_openai_client()
            else:
                self.st = SentenceTransformer(self.model)

    def build_embedding_vector(self, text: str, *, dimensions: int = 3072) -> List[float]:
        if not text:
            return [[0.0] * dimensions]
        if self.is_online:
            return self.build_embedding_vector_openai(text, dimensions=dimensions)
        else:
            return self.build_embedding_vector_st(text, dimensions=dimensions)

    def build_embedding_vector_openai(self, texts: str, *, dimensions: int = 3072) -> List[float]:
        resp = self.client.embeddings.create(model=self.model, input=texts)  # data[i].embedding
        out = [row.embedding for row in resp.data]
        # Some models have fixed dim; enforce/trim/pad to DB dim just in case
        fixed = []
        for v in out:
            if len(v) > dimensions:
                v = v[:dimensions]
            elif len(v) < dimensions:
                v = v + [0.0]*(dimensions - len(v))
            fixed.append(_emb_normalize(v))
        return fixed

    def build_embedding_vector_st(self, texts: str, *, dimensions: int = 3072) -> List[float]:
        vecs = self.st.encode(texts, batch_size=64, normalize_embeddings=True, convert_to_numpy=True)
        out = vecs.tolist()
        fixed = []
        for v in out:
            if len(v) > dimensions:
                v = v[:dimensions]
            elif len(v) < dimensions:
                v = v + [0.0]*(dimensions - len(v))
            fixed.append(_emb_normalize(v))
        return fixed

    def _deterministic_embedding(self, text: str, *, dimensions: int = 3072) -> List[float]:
        if not text:
            return [0.0] * dimensions
        import hashlib, random
        digest = hashlib.sha512(text.encode("utf-8")).digest()
        seed = int.from_bytes(digest, "big")
        rng = random.Random(seed)
        return _emb_normalize([rng.uniform(-1.0, 1.0) for _ in range(dimensions)])

def main() -> None:
    import argparse
    import time

    """Parse arguments, run the AI query, and report the timing."""
    parser = argparse.ArgumentParser(
        description=(
            "Send a single user message to the configured AI provider while "
            "optionally supplying a custom system prompt."
        )
    )
    parser.add_argument(
        "--model-name",
        dest="model_name",
        default="",
        help=(
            "Name of the model to use. Defaults to a blank string when not "
            "supplied."
        ),
    )
    parser.add_argument(
        "--system-message",
        dest="system_message",
        default="",
        help="System prompt content sent before the user message.",
    )
    parser.add_argument(
        "--user-message",
        dest="user_message",
        default="",
        help="User prompt content to send to the model.",
    )

    args = parser.parse_args()

    # Instantiate LlmAi with the requested model name; this handles
    # connecting to either Ollama or OpenAI based on configuration.
    ai_instance = LlmAi(args.model_name)

    # Measure the time it takes for the AI service to respond.
    start_time = time.perf_counter()
    response_text = ai_instance.query(
        user_msg=args.user_message,
        system_msg=args.system_message,
    )
    elapsed_seconds = time.perf_counter() - start_time

    # Provide detailed output so that a user can see the response and timing.
    print("AI response:\r\n")
    print(response_text)
    print(
        "\r\nTotal request duration: {:.3f} seconds".format(elapsed_seconds)
    )


if __name__ == "__main__":
    # Entry point for command-line execution.
    main()
