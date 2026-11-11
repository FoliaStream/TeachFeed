import re
import time

from google.genai import errors

try:
    import httpx
    NETWORK_ERRORS = (httpx.TransportError,)       # timeouts and dropped connections
except ImportError:
    NETWORK_ERRORS = ()

# Best first. Names that your key cannot use are skipped automatically.
# Tip: to save the small daily quota of the Flash models while testing, put a Flash-Lite model first.
PREFERRED_MODELS = [
    "gemini-3.6-flash",
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
]
SKIP_WORDS = ("image", "tts", "live", "audio", "embed", "robotics", "computer-use")

MAX_SLOW_FAILURES = 2      # timeouts or server errors allowed in one assessment before giving up
TIME_BUDGET = 300          # seconds in total for one assessment

# Memory shared by every assessment while the app is running
_BLOCKED = {}              # model name -> time until which it is skipped
_NO_THINKING = set()       # models that reject the thinking setting
_LISTING = {"at": 0.0, "names": None}


class AssessmentError(Exception):
    """Raised when no model could deliver a usable answer. The message lists every attempt."""


def _available(client):
    """Names of the models this key can see, cached for an hour. None if the list is unavailable."""
    now = time.time()
    if _LISTING["names"] is not None and now - _LISTING["at"] < 3600:
        return _LISTING["names"]
    try:
        names = {m.name.replace("models/", "") for m in client.models.list()}
    except Exception:
        return _LISTING["names"]
    _LISTING.update(at=now, names=names)
    return names


def _candidates(client, preferred=None):
    """Models to try, in order: the preferred ones that exist, with blocked ones last."""
    names = _available(client)
    models = [m for m in (preferred or PREFERRED_MODELS) if names is None or m in names]

    if not models and names:               # none of the preferred names exist: use any flash model
        found = [n for n in names if "flash" in n and not any(w in n for w in SKIP_WORDS)]
        models = (sorted((n for n in found if "lite" not in n), reverse=True)
                  + sorted((n for n in found if "lite" in n), reverse=True))
    if not models:
        raise AssessmentError("No usable Gemini model was found for this API key.")

    now = time.time()
    ready = [m for m in models if _BLOCKED.get(m, 0) <= now]
    waiting = sorted((m for m in models if _BLOCKED.get(m, 0) > now), key=lambda m: _BLOCKED[m])
    return ready + waiting


def _block_seconds(error):
    """How long to leave a model alone after a 429."""
    text = str(error)
    if "PerDay" in text:
        return 2 * 3600                    # daily quota: check again in a couple of hours
    m = (re.search(r"retry in ([\d.]+)s", text)
         or re.search(r"retryDelay['\"]?\s*:\s*['\"]([\d.]+)s", text))
    if m:
        return min(float(m.group(1)) + 2, 3600)
    return 60


def generate_with_fallback(client, contents, make_config, schema, preferred=None):
    """Asks one model at a time until one gives a usable answer.

    make_config(use_thinking) returns the GenerateContentConfig for an attempt.
    schema is the pydantic class the answer must match.
    preferred optionally replaces the default model order (for example cheap models first).
    Returns (parsed answer, response, model name).

    Switches model only when a model is out of quota, unavailable, overloaded or too slow.
    An answer that cannot be read is NOT sent to another model: that would repeat the same
    cost, so it stops and reports exactly what was wrong with the answer.
    """
    started = time.time()
    trail = []
    slow_failures = 0

    def note(model, text):
        line = f"{model}: {text}"
        trail.append(line)
        print(line, flush=True)

    def fail(reason):
        return AssessmentError(reason + "\n\nAttempts:\n" + "\n".join(trail))

    for model in _candidates(client, preferred):
        if slow_failures >= MAX_SLOW_FAILURES:
            break
        if time.time() - started > TIME_BUDGET:
            note("-", "time budget used up")
            break

        use_thinking = model not in _NO_THINKING
        for _ in range(2):                                        # second pass only without the thinking setting
            try:
                response = client.models.generate_content(
                    model=model, contents=contents, config=make_config(use_thinking),
                )
                parsed = response.parsed
                if parsed is None:                                # the SDK could not read it: try ourselves
                    try:
                        parsed = schema.model_validate_json(response.text or "")
                    except Exception as e:
                        note(model, "answered, but the answer did not match the expected format")
                        raise fail(f"{model} answered, but the answer could not be read: {str(e)[:600]}")
                note(model, "answered")
                return parsed, response, model

            except errors.APIError as e:
                if e.code == 400 and use_thinking and "thinking" in str(e).lower():
                    _NO_THINKING.add(model)                       # remembered, so it is only tried once
                    use_thinking = False
                    note(model, "does not accept the thinking setting, trying without it")
                    continue
                if e.code == 429:                                 # out of quota: next model, no waiting
                    wait = _block_seconds(e)
                    _BLOCKED[model] = time.time() + wait
                    note(model, f"limit reached, skipped for {wait:.0f} s")
                elif e.code == 404:                               # this key cannot use that model
                    _BLOCKED[model] = time.time() + 3600
                    note(model, "not available for this key")
                elif e.code in (500, 503, 504):                   # overloaded: switch, do not hammer it
                    _BLOCKED[model] = time.time() + 60
                    slow_failures += 1
                    note(model, f"error {e.code} (overloaded or timed out)")
                else:                                             # bad key, request too big...: another model will not help
                    note(model, f"error {e.code}")
                    raise fail(f"{model}: {e}")
                break

            except NETWORK_ERRORS as e:                           # timeout or connection problem
                _BLOCKED[model] = time.time() + 30
                slow_failures += 1
                note(model, f"connection problem or timeout ({type(e).__name__})")
                break

    raise fail("No model could answer right now.")