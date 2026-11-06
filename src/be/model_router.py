import re 
import time 

from google.genai import errors

try:
    import httpx
    NETWORK_ERRORS = (httpx.TransportError,)
except ImportError:
    NETWORK_ERRORS = ()

# Best first 
PREFERRED_MODELS = [
    "gemini-3.6-flash",
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
]

SKIP_WORDS = ("image", "tts", "live", "audio", "embed", "robotics", "computer-use")
TEMPORARY = {500, 503, 504}

# Memory shared by every assessment while the app is running
_BLOCKED = {}
_LISTING = {"at": 0.0, "names": None}

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

def _candidates(client):
    "Models to try, in order: the preferred ones that exist, with blocked ones last."
    names = _available(client)
    models = [m for m in PREFERRED_MODELS if names is None or m in names]

    if not models and names: 
        found = [n for n in names if "flash" in n and not any(w in n for w in SKIP_WORDS)]
        models = (sorted((n for n in found if "lite" not in n), reverse=True)
                  + sorted((n for n in found if "lite" in n), reverse=True))
    
    if not models: 
        raise RuntimeError("No usable Gemini model was found for this API key.")
    
    now  = time.time()
    ready = [m  for m in models if _BLOCKED.get(m, 0) <= now]
    waiting = sorted ((m for m in models if _BLOCKED.get(m, 0) > now), key=lambda m: _BLOCKED[m])
    return ready + waiting

def _block_seconds(error):
    """How long to leave a model alone after 429."""
    text = str(error)
    if "PerDay" in text:
        return 2*3600
    m = (re.search(r"retry in ([\d.]+)s", text)
         or re.search(r"retryDelay['\"]?\s*:\s*['\"]([\d.]+)s", text))
    if m:
        return min(float(m.group(1)) + 2, 3600)
    return 60

def generate_with_fallback(client, contents, make_config):
    """Tries the models in order until one answers.
    make_config(use_thinking) returns the GenerateContentConfig for one attempt.
    Returns (response, model name); response.parsed is never None."""
    last_error = None

    for model in _candidates(client):
        use_thinking = True
        for attempt in range(2):
            try:
                response = client.models.generate_content(
                    model=model, contents=contents, config=make_config(use_thinking),
                )
                if response.parsed is None: 
                    raise ValueError(f"Couls not parse the output of {model}:\n{response.text}")
                print(f"model used: {model}", flush=True)
                return response, model
            
            except errors.APIError as e:
                last_error = e
                if e.code == 400 and use_thinking and "thinking" in str(e).lower():
                    use_thinking = False
                    continue
                if e.code == 429:
                    wait = _block_seconds(e)
                    _BLOCKED[model] = time.time() + wait
                    print(f"{model} limit reached, skipping it for {wait:.0f}s", flush=True)
                    break
                if e.code == 404:
                    _BLOCKED[model] = time.time() + 3600
                    break
                if e.code in TEMPORARY:
                    time.sleep(5 * (attempt + 1))
                    if attempt == 1:
                        _BLOCKED[model] = time.time() + 30
                    continue
                raise

            except NETWORK_ERRORS as e:
                last_error = e
                time.sleep(5 * (attempt + 1))
                if attempt == 1:
                    _BLOCKED[model] = time.time() + 30 

            except ValueError as e:
                last_error = e
                break
    
    raise last_error or RuntimeError("No Gemini model answered.")