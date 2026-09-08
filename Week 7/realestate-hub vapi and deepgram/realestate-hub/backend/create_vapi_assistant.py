from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request

from dotenv import load_dotenv

from config import PROJECT_ROOT
from vapi_voice_server import build_assistant_config, get_active_ngrok_url

API_BASE = "https://api.vapi.ai"


def _sync_env_file(key: str, value: str) -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    text = env_path.read_text(encoding="utf-8")
    pattern = rf"^{key}\s*=.*$"
    new_line = f"{key}={value}"
    if re.search(pattern, text, flags=re.MULTILINE):
        text = re.sub(pattern, new_line, text, flags=re.MULTILINE)
    else:
        text += f"\n{new_line}\n"
    env_path.write_text(text, encoding="utf-8")
    os.environ[key] = value


def _request(method: str, path: str, body: dict, private_key: str) -> dict:
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        data=json.dumps(body).encode("utf-8"),
        method=method,
        headers={
            "Authorization": f"Bearer {private_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "realestate-hub-create-vapi-assistant/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore")
        raise SystemExit(f"Vapi API returned {e.code}: {detail}")


def main():
    load_dotenv(PROJECT_ROOT / ".env", override=True)
    private_key = os.getenv("VAPI_PRIVATE_KEY", "").strip()

    if not private_key:
        raise SystemExit(
            "VAPI_PRIVATE_KEY is not set in .env. Get it from the Vapi dashboard "
            "(Dashboard -> API Keys -> Private Key)."
        )

    # Parse CLI arguments (can pass URL, assistant ID, or both)
    url_arg = None
    existing_id = os.getenv("VAPI_ASSISTANT_ID", "").strip()

    for arg in sys.argv[1:]:
        arg_clean = arg.strip().rstrip("/")
        if arg_clean.startswith("http://") or arg_clean.startswith("https://"):
            url_arg = arg_clean
        elif arg_clean:
            existing_id = arg_clean

    # Auto-detect public URL in priority order:
    # 1. CLI argument URL
    # 2. Local running ngrok tunnel (http://127.0.0.1:4040/api/tunnels)
    # 3. BACKEND_PUBLIC_URL from .env
    detected_ngrok = get_active_ngrok_url()
    public_url = url_arg or detected_ngrok or os.getenv("BACKEND_PUBLIC_URL", "").strip().rstrip("/")

    if not public_url:
        raise SystemExit(
            "Could not determine backend public URL.\n"
            "Either start ngrok (`ngrok http 8000`), set BACKEND_PUBLIC_URL in .env, "
            "or pass the URL directly: `python create_vapi_assistant.py https://...`"
        )

    # Sync with .env if URL changed
    if public_url != os.getenv("BACKEND_PUBLIC_URL", "").strip():
        _sync_env_file("BACKEND_PUBLIC_URL", public_url)
        print(f"Updated BACKEND_PUBLIC_URL in .env -> {public_url}")

    config = build_assistant_config(public_url=public_url)

    if existing_id:
        result = _request("PATCH", f"/assistant/{existing_id}", config, private_key)
        print(f"Updated assistant {existing_id} ({result.get('name')}).")
    else:
        result = _request("POST", "/assistant", config, private_key)
        new_id = str(result.get("id"))
        print(f"Created assistant {new_id} ({result.get('name')}).")
        _sync_env_file("VAPI_ASSISTANT_ID", new_id)
        print(f"Saved VAPI_ASSISTANT_ID in .env -> {new_id}")

    print("\nOpen the Vapi dashboard -> Assistants -> this assistant -> \"Talk to Assistant\" to test it.")
    transcriber = config["transcriber"]
    print(f"Transcriber:  {transcriber['provider']} / {transcriber['model']} / language={transcriber['language']}")
    print(f"LLM URL:      {config['model']['url']}/chat/completions")
    print(f"Voice:        {config['voice']['provider']} / {config['voice']['voiceId']}")


if __name__ == "__main__":
    main()
