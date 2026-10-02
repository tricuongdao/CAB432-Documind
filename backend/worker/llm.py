"""Bedrock adapter: embeddings + text generation with provider dispatch.

Kept dependency-light (boto3 only) so the same logic runs identically in the
ECS worker container and in the API Lambda. The model is selected by ID at
deploy time, so the pipeline can switch between Titan, Nova, Claude and
Gemma families without code changes.
"""

import json
import random
import time

import boto3
from botocore.config import Config

from config import AWS_REGION, EMBED_MODEL_ID, EMBED_DIM

_client = None

_RETRYABLE = {
    "ThrottlingException",
    "ServiceUnavailableException",
    "ModelTimeoutException",
    "InternalServerException",
    "ModelNotReadyException",
    "TooManyRequestsException",
}


def client():
    """Lazily created, long-lived bedrock-runtime client."""
    global _client
    if _client is None:
        _client = boto3.client(
            "bedrock-runtime",
            region_name=AWS_REGION,
            config=Config(
                retries={"max_attempts": 5, "mode": "adaptive"},
                connect_timeout=10,
                read_timeout=120,
            ),
        )
    return _client


def _invoke(model_id: str, body: dict, attempts: int = 5) -> dict:
    """Invoke a Bedrock model with jittered exponential backoff on throttling."""
    last = None
    for attempt in range(attempts):
        try:
            resp = client().invoke_model(
                modelId=model_id,
                body=json.dumps(body).encode("utf-8"),
                contentType="application/json",
                accept="application/json",
            )
            return json.loads(resp["body"].read())
        except Exception as exc:  # botocore ClientError / read timeouts
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__)
            if code in _RETRYABLE and attempt < attempts - 1:
                last = exc
                time.sleep(min(8.0, 0.5 * (2 ** attempt)) + random.random())
                continue
            raise
    if last:
        raise last
    raise RuntimeError("unreachable")


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------

def embed_text(text: str, model_id: str = None) -> list:
    """Return the embedding vector for a piece of text."""
    model_id = model_id or EMBED_MODEL_ID
    body = {"inputText": text[: 8000 * 4]}
    if "titan-embed-text-v2" in model_id:
        body["dimensions"] = EMBED_DIM
        body["normalize"] = True
    out = _invoke(model_id, body)
    return out["embedding"]


# ---------------------------------------------------------------------------
# Text generation (model-family adapter)
# ---------------------------------------------------------------------------

def _gen_body(model_id: str, prompt: str, max_tokens: int, temperature: float) -> dict:
    """Build the request body for the configured model family."""
    if model_id.startswith("anthropic."):
        return {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        }
    if model_id.startswith("amazon.nova"):
        return {
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
        }
    if model_id.startswith("amazon.titan-text"):
        return {
            "inputText": prompt,
            "textGenerationConfig": {"maxTokenCount": max_tokens, "temperature": temperature},
        }
    if model_id.startswith("google.gemma"):
        # Gemma 3 on Bedrock exposes an OpenAI-compatible chat API.
        return {
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
    if model_id.startswith("meta.llama"):
        return {"prompt": prompt, "max_gen_len": max_tokens, "temperature": temperature}
    raise ValueError(f"unsupported model family: {model_id}")


def _parse_gen(model_id: str, out: dict) -> str:
    """Extract the generated text from the family-specific response."""
    if model_id.startswith("anthropic."):
        return out["content"][0]["text"]
    if model_id.startswith("amazon.nova"):
        return out["output"]["message"]["content"][0]["text"]
    if model_id.startswith("amazon.titan-text"):
        return out["results"][0]["outputText"]
    if "choices" in out:  # OpenAI-compatible surface (e.g. gemma-3 on Bedrock)
        return out["choices"][0]["message"]["content"]
    if model_id.startswith("google.gemma") or model_id.startswith("meta.llama"):
        gen = out.get("generations") or out.get("outputs") or []
        return gen[0].get("text", "") if gen else ""
    raise ValueError(f"unsupported model family: {model_id}")


def generate_text(model_id: str, prompt: str, max_tokens: int = 600,
                  temperature: float = 0.2) -> str:
    """Generate text with the configured model, normalised across families."""
    body = _gen_body(model_id, prompt, max_tokens, temperature)
    out = _invoke(model_id, body)
    return _parse_gen(model_id, out).strip()
