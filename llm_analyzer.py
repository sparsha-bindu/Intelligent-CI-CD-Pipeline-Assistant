# llm_analyzer.py
import os
import json
import re
import requests
from dotenv import load_dotenv

load_dotenv()

# Provider selection: 'openai' (default) or 'groq'
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq").lower()

# OpenAI settings (optional; used as fallback)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Groq settings
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
# Default Groq model — change this to a model id available to your account
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama3-8b-8192")
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")

# Prompt builder
def _build_prompt(summary: str, repo_files: dict | None):
    repo_hint = ", ".join(repo_files.keys()) if repo_files else "none"
    return f"""
You are a senior DevOps engineer. Analyze the following failing CI build logs and give:
1) Short diagnosis (one sentence)
2) Root cause hypothesis
3) Step-by-step fixes (array)
4) A suggested pipeline patch (Jenkinsfile or GitHub Actions YAML)
5) Confidence score 0-1

Return strict JSON with keys: diagnosis, root_cause, fixes (array), pipeline_patch (string), confidence (float).

Build logs:
{summary}

Repository files:
{repo_hint}
"""

# Robust JSON extractor: removes fences and finds JSON blobs
def _extract_json(text: str):
    if not isinstance(text, str):
        return None
    # 1) Look for ```json ... ``` or ``` ... ```
    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text, flags=re.IGNORECASE)
    if m:
        candidate = m.group(1).strip()
        try:
            return json.loads(candidate)
        except Exception:
            # fallthrough to try other heuristics
            pass

    # 2) Look for the first {...} JSON object in the text
    m2 = re.search(r"(\{[\s\S]*\})", text)
    if m2:
        candidate = m2.group(1)
        # Try to balance braces by finding the matching closing brace if needed
        try:
            return json.loads(candidate)
        except Exception:
            # Try a simple balancing approach: find the outermost JSON substring
            start = text.find("{")
            if start != -1:
                depth = 0
                for i in range(start, len(text)):
                    if text[i] == "{":
                        depth += 1
                    elif text[i] == "}":
                        depth -= 1
                        if depth == 0:
                            try:
                                candidate = text[start : i + 1]
                                return json.loads(candidate)
                            except Exception:
                                break
    return None

# Public API (keeps original name for compatibility)
def analyze_with_openai(summary: str, repo_files: dict | None = None) -> dict:
    prompt = _build_prompt(summary, repo_files)

    if LLM_PROVIDER == "groq":
        return _call_groq_chat(prompt)
    else:
        return _call_openai_chat(prompt)

# ---------------- Groq call ----------------
def _call_groq_chat(prompt: str) -> dict:
    if not GROQ_API_KEY:
        return {"error": "GROQ_API_KEY not set in environment (.env)"}

    url = f"{GROQ_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 800,
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=30)
    except Exception as e:
        return {"error": f"Groq API request failed: {e}"}

    # Provide helpful debug info on non-200
    if r.status_code != 200:
        # try to parse body
        body = None
        try:
            body = r.json()
        except Exception:
            body = r.text
        return {"error": f"Groq API returned status {r.status_code}", "body": body}

    try:
        data = r.json()
    except Exception as e:
        return {"error": f"Groq returned non-json response: {e}", "raw_text": r.text}

    # Groq/OpenAI-compatible shape: choices[0].message.content
    content = None
    try:
        content = data["choices"][0]["message"]["content"]
    except Exception:
        # fallback to older 'text' field or raw dump
        content = data.get("choices", [{}])[0].get("text") or json.dumps(data)

    # Try to extract JSON inside the returned content
    parsed = _extract_json(content)
    if parsed is not None:
        return parsed

    # return raw content if JSON couldn't be parsed
    return {"raw": content}

# ---------------- OpenAI call (fallback) ----------------
def _call_openai_chat(prompt: str) -> dict:
    if not OPENAI_API_KEY:
        return {"error": "OPENAI_API_KEY not set in environment (.env)"}

    # Prefer to use installed openai client if available; else fall back to a simple HTTP call
    # Attempt modern client first
    try:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            resp = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=800,
            )
            # modern response access
            try:
                text = resp.choices[0].message.content
            except Exception:
                text = resp.choices[0].message["content"]
        except Exception:
            import openai as _openai
            _openai.api_key = OPENAI_API_KEY
            resp = _openai.ChatCompletion.create(
                model=OPENAI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=800,
            )
            text = resp["choices"][0]["message"]["content"]

    except Exception as e:
        return {"error": f"OpenAI client call failed: {e}"}

    parsed = _extract_json(text)
    if parsed is not None:
        return parsed
    return {"raw": text}
