"""Google Gemini: writes product descriptions and search-engine text from the product's
details. The admin pastes an API key (from aistudio.google.com) in Settings → Online shop;
nothing is sent to Google until someone clicks "Write with AI"."""

import json
import urllib.error
import urllib.request

API = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-3.8-flash"

FIELDS = ("seo_title", "seo_description", "description", "slug")


class AIError(Exception):
    pass


def _request(url, key, body=None, timeout=45):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 method="POST" if body is not None else "GET",
                                 headers={"x-goog-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            message = json.loads(e.read())["error"]["message"]
        except Exception:  # not Google's JSON error
            message = f"HTTP {e.code}"
        raise AIError(f"Gemini said: {message}")
    except (urllib.error.URLError, TimeoutError) as e:
        raise AIError(f"Can't reach Google Gemini ({getattr(e, 'reason', e)}). Check the internet connection.")


def list_models(key):
    """Model names this key can use for writing text, newest first."""
    data = _request(f"{API}/models?pageSize=1000", key)
    names = [m["name"].split("/", 1)[-1] for m in data.get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", [])]
    return sorted((n for n in names if n.startswith("gemini")), reverse=True)


def product_prompt(store_name, fields):
    facts = "\n".join(f"- {label}: {value}" for label, value in fields.items() if value)
    return f"""You write product pages for "{store_name}", a grocery and general store in India that takes orders online.
Write for the product below. Use simple, friendly Indian English.

Rules:
- Only state facts you can be sure of from the product name and details. Do not invent ingredients, weights,
  health claims, offers or delivery promises. If unsure, keep it general (what it is, how people use it).
- seo_title: at most 60 characters; product name first, then the store name if it fits.
- seo_description: 120 to 155 characters; say what it is and that it can be ordered online from {store_name}.
- description: 2 short paragraphs, 60 to 110 words in total, plain text (no markdown, no emojis, no bullet points).
- slug: lowercase words separated by hyphens, only a-z, 0-9 and hyphens.

Reply with only a JSON object with the keys "seo_title", "seo_description", "description" and "slug" (string
values; use \\n between the two description paragraphs), and nothing else.

Product:
{facts}"""


def write_product(key, model, store_name, fields):
    """Returns a dict with seo_title, seo_description, description and slug."""
    if not key:
        raise AIError("Add a Gemini API key in Settings → Online shop first.")
    body = {
        "contents": [{"role": "user", "parts": [{"text": product_prompt(store_name, fields)}]}],
        # The JSON is asked for in the prompt (not with an API option) so this works across model versions.
        "generationConfig": {"temperature": 0.6},
    }
    data = _request(f"{API}/models/{model or DEFAULT_MODEL}:generateContent", key, body)
    try:
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))  # skip any "thinking" parts
        result = json.loads(text[text.index("{"):text.rindex("}") + 1])
    except (KeyError, IndexError, ValueError):
        reason = (data.get("promptFeedback") or {}).get("blockReason") or "no usable answer"
        raise AIError(f"Gemini returned {reason}. Try again.")
    if not isinstance(result, dict) or not result.get("description"):
        raise AIError("Gemini's answer was incomplete. Try again.")
    return {k: str(result.get(k, "")).strip() for k in FIELDS}
