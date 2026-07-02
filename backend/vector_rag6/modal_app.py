"""
modal_app.py

Deploy your FastAPI backend (test_api.py) on Modal with GPU support.

Usage:
    modal deploy modal_app.py
    modal serve modal_app.py
"""

import modal

# ─────────────────────────────────────────────────────────────────────────────
# Persistent storage
# ─────────────────────────────────────────────────────────────────────────────

# Cache HuggingFace models so they aren't downloaded every deployment
model_cache = modal.Volume.from_name(
    "prof-ia-model-cache",
    create_if_missing=True,
)

# Persistent dictionaries for sessions and lessons
_SESSION_STORE = modal.Dict.from_name(
    "prof-ia-sessions",
    create_if_missing=True,
)

_LESSON_STORE = modal.Dict.from_name(
    "prof-ia-lessons",
    create_if_missing=True,
)

# ─────────────────────────────────────────────────────────────────────────────
# Image
# ─────────────────────────────────────────────────────────────────────────────

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        [
            "fastapi",
            "uvicorn",
            "google-genai",
            "langsmith",
            "qdrant-client==1.6.9",
            "sentence-transformers",
            "transformers",
            "torch",
            "httpx",
            "python-dotenv",
            "pydantic",
            "langchain-core",
            "langchain",
            "flashrank",
        ]
    )
    .add_local_dir(".", remote_path="/root", copy=True)
)

# ─────────────────────────────────────────────────────────────────────────────
# Modal App
# ─────────────────────────────────────────────────────────────────────────────

app = modal.App(
    "prof-ia-backend",
    image=image,
)

# ─────────────────────────────────────────────────────────────────────────────
# Secrets
# ─────────────────────────────────────────────────────────────────────────────

# Create once with:
# python3 -m modal secret create prof-ia-secrets --from-dotenv .env
secrets = [modal.Secret.from_name("prof-ia-secrets")]

# ─────────────────────────────────────────────────────────────────────────────
# FastAPI Endpoint
# ─────────────────────────────────────────────────────────────────────────────

@app.function(
    gpu="T4",
    memory=8192,
    cpu=2,
    timeout=300,
    secrets=secrets,
    scaledown_window=600,
    volumes={"/root/.cache/huggingface": model_cache},
)
@modal.asgi_app()
def fastapi_app():
    import sys

    sys.path.insert(0, "/root")

    # Import module first so we can inject persistent stores
    import test_api

    # Inject Modal persistent dictionaries
    test_api._SESSION_STORE = _SESSION_STORE
    test_api._LESSON_STORE = _LESSON_STORE

    from test_api import app as _app

    return _app