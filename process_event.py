# process_event.py
import os
import time
import json
import base64
import hashlib
import requests
import urllib.parse
import html
from datetime import datetime

from typing import Dict, Any

# Import your LLM wrapper (the file we patched earlier)
from llm_analyzer import analyze_with_openai

# ENV/config
GITHUB_REPO = os.getenv("GITHUB_REPO")            # e.g. owner/repo
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_BASE_BRANCH = os.getenv("GITHUB_BASE_BRANCH", "main")
JENKINS_USER = os.getenv("JENKINS_USER")
JENKINS_API_TOKEN = os.getenv("JENKINS_API_TOKEN")
JENKINS_URL = os.getenv("JENKINS_URL")            # optional
SLACK_WEBHOOK = os.getenv("SLACK_WEBHOOK")
ASSISTANT_OWNER = os.getenv("ASSISTANT_OWNER", "ci-assistant")  # tag used in PRs/messages

# Minor helper for logging (prints to uvicorn console)
def log(*args, **kwargs):
    ts = datetime.utcnow().isoformat()
    print(f"[process_event {ts}]", *args, **kwargs)

# -------------------------
# Normalization & extraction
# -------------------------
def normalize_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize incoming payload to a common shape.
    Supports Jenkins-style payloads with `build` and GitHub `workflow_run`.
    """
    if not isinstance(payload, dict):
        return {"source": "unknown", "status": None, "url": None, "logs": str(payload), "metadata": payload}

    if "build" in payload:
        build = payload.get("build", {})
        return {
            "source": "jenkins",
            "status": build.get("status"),
            "url": build.get("full_url") or build.get("url"),
            "logs": build.get("logs") or "",
            "metadata": payload,
        }

    # GitHub Actions workflow_run
    if "workflow_run" in payload:
        run = payload.get("workflow_run", {})
        return {
            "source": "github",
            "status": payload.get("action"),
            "url": run.get("html_url"),
            "logs": "",  # GitHub usually sends no consolidated logs in webhook
            "metadata": payload,
        }

    # fallback
    return {"source": "unknown", "status": None, "url": None, "logs": json.dumps(payload), "metadata": payload}

def extract_error_blocks(log_text: str) -> str:
    """
    Simple log extractor: keep last ~2000 chars and try to find a stacktrace-like block.
    You can replace this with a more advanced parser that extracts exceptions, failing steps, etc.
    """
    if not log_text:
        return ""

    # prefer the last meaningful portion of the log
    tail = log_text[-16000:]  # last 16k chars
    # Try to find "Traceback" or "Exception" markers
    markers = ["Traceback", "Exception", "ERROR", "error:", "fatal:"]
    for m in markers:
        idx = tail.rfind(m)
        if idx != -1:
            # return from marker to end
            return tail[idx:]
    # fallback to the last 2000 characters
    return tail[-2000:]

# -------------------------
# Jenkins post-back
# -------------------------
def _post_back_to_jenkins(build_url: str, analysis: Dict[str, Any]) -> bool:
    """
    Set Jenkins build description with a small HTML summary from `analysis`.
    Requires JENKINS_USER and JENKINS_API_TOKEN in env and an accessible build_url.
    Returns True on success.
    """
    if not (JENKINS_USER and JENKINS_API_TOKEN and build_url):
        log("Jenkins post-back skipped: missing credentials or build_url")
        return False

    # Ensure trailing slash
    if not build_url.endswith("/"):
        build_url = build_url + "/"

    # Try to fetch crumb (if Jenkins requires CSRF crumb)
    headers = {}
    try:
        crumb_url = urllib.parse.urljoin(build_url, "../crumbIssuer/api/json")
        r = requests.get(crumb_url, auth=(JENKINS_USER, JENKINS_API_TOKEN), timeout=5)
        if r.status_code == 200:
            cr = r.json()
            headers[cr["crumbRequestField"]] = cr["crumb"]
    except Exception as e:
        # ignore crumb errors - Jenkins may not require CSRF for API token usage
        log("Jenkins crumb fetch failed (continuing):", e)

    diag = analysis.get("diagnosis", "No diagnosis")
    confidence = analysis.get("confidence")
    fixes = analysis.get("fixes", []) or []

    fixes_html = "<ul>" + "".join(f"<li>{html.escape(str(x))}</li>" for x in fixes[:5]) + "</ul>" if fixes else ""
    desc = f"AI diagnosis: {html.escape(str(diag))}<br/>Confidence: {html.escape(str(confidence))}<br/>{fixes_html}"

    submit_url = urllib.parse.urljoin(build_url, "submitDescription")
    try:
        resp = requests.post(submit_url, data={"description": desc}, auth=(JENKINS_USER, JENKINS_API_TOKEN), headers=headers, timeout=6)
        resp.raise_for_status()
        log("Posted analysis back to Jenkins build description")
        return True
    except Exception as e:
        log("Failed to post back to Jenkins:", e)
        return False

# -------------------------
# Slack notifier (optional)
# -------------------------
def _notify_slack(analysis: Dict[str, Any], build_url: str = None) -> bool:
    if not SLACK_WEBHOOK:
        return False
    text = f"*AI diagnosis*: {analysis.get('diagnosis')}\n*Confidence*: {analysis.get('confidence')}\n"
    if build_url:
        text += f"<{build_url}|Open build>\n"
    if analysis.get("pipeline_patch"):
        text += "Suggested pipeline patch available.\n"
    payload = {"text": text}
    try:
        r = requests.post(SLACK_WEBHOOK, json=payload, timeout=4)
        r.raise_for_status()
        log("Slack notification sent")
        return True
    except Exception as e:
        log("Slack notify failed:", e)
        return False

# -------------------------
# GitHub PR creation (minimal)
# -------------------------
def _create_pull_request_with_patch(repo: str, base_branch: str, patch_content: str, path: str = ".github/workflows/ai-suggested.yml") -> Dict[str, Any]:
    """
    Create a branch and a PR that adds `path` with content `patch_content`.
    Uses GitHub REST API. Requires GITHUB_TOKEN and GITHUB_REPO environment variables.
    Returns a dict with 'pr_url' on success or {'error':...}.
    """
    if not (GITHUB_TOKEN and repo):
        return {"error": "Missing GITHUB_TOKEN or repo"}

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }
    api = "https://api.github.com"

    owner, repo_name = repo.split("/", 1)

    # 1) get base branch commit SHA
    ref_url = f"{api}/repos/{owner}/{repo_name}/git/ref/heads/{base_branch}"
    r = requests.get(ref_url, headers=headers, timeout=8)
    if r.status_code != 200:
        return {"error": f"Failed to fetch base ref: {r.status_code} {r.text}"}
    base_sha = r.json()["object"]["sha"]

    # 2) create new branch ref
    new_branch = f"ai-suggest-{int(time.time())}"
    create_ref_url = f"{api}/repos/{owner}/{repo_name}/git/refs"
    payload_ref = {"ref": f"refs/heads/{new_branch}", "sha": base_sha}
    r = requests.post(create_ref_url, headers=headers, json=payload_ref, timeout=8)
    if r.status_code not in (200, 201):
        # If branch exists with same name, try another suffix
        if r.status_code == 422:
            new_branch = f"{new_branch}-{int(time.time()%10000)}"
            payload_ref["ref"] = f"refs/heads/{new_branch}"
            r = requests.post(create_ref_url, headers=headers, json=payload_ref, timeout=8)
            if r.status_code not in (200, 201):
                return {"error": f"Failed to create branch: {r.status_code} {r.text}"}
        else:
            return {"error": f"Failed to create branch: {r.status_code} {r.text}"}

    # 3) create file on that branch using contents API
    create_file_url = f"{api}/repos/{owner}/{repo_name}/contents/{urllib.parse.quote(path, safe='')}"
    encoded = base64.b64encode(patch_content.encode("utf-8")).decode("utf-8")
    commit_msg = "chore(ci): AI suggested pipeline"
    file_payload = {"message": commit_msg, "content": encoded, "branch": new_branch}
    r = requests.put(create_file_url, headers=headers, json=file_payload, timeout=8)
    if r.status_code not in (200, 201):
        # If file already exists, create a unique path or return error
        return {"error": f"Failed to create file: {r.status_code} {r.text}"}

    # 4) create a PR
    pr_url = f"{api}/repos/{owner}/{repo_name}/pulls"
    pr_title = "AI suggested pipeline improvements"
    pr_body = "Automated suggestion from CI Assistant: suggested pipeline changes."
    pr_payload = {"title": pr_title, "head": new_branch, "base": base_branch, "body": pr_body}
    r = requests.post(pr_url, headers=headers, json=pr_payload, timeout=8)
    if r.status_code not in (200, 201):
        return {"error": f"Failed to create PR: {r.status_code} {r.text}"}
    pr = r.json()
    return {"pr_url": pr.get("html_url")}

# -------------------------
# Main event processing
# -------------------------
def process_event_sync(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Synchronous processing for an incoming event.
    Returns the analysis dict (or an error dict).
    """
    # Normalize incoming
    e = normalize_event(event)
    build_url = e.get("url")
    logs = e.get("logs", "")

    log("PROCESS_EVENT: got event", e.get("source"), "url=", build_url, "len_logs=", len(logs))
    if not logs:
        log("No logs found in payload (empty). Aborting analysis.")
        return {"error": "no logs"}

    # Extract the part of logs most likely to contain the error
    snippet = extract_error_blocks(logs)
    log("Log snippet length:", len(snippet))

    # Call the LLM analyzer
    try:
        analysis = analyze_with_openai(snippet, repo_files=None)
    except Exception as exc:
        log("LLM call raised exception:", exc)
        return {"error": str(exc)}

    # If analyzer returned an 'error' wrapper, bubble it up
    if isinstance(analysis, dict) and analysis.get("error"):
        log("LLM returned error:", analysis.get("error"))
        # still return the raw value to caller
        return analysis

    # If analyzer returned 'raw' (string), try to wrap into a minimal structure
    if isinstance(analysis, dict) and "raw" in analysis and not any(k in analysis for k in ("diagnosis", "fixes", "pipeline_patch")):
        # keep raw text
        log("LLM returned raw text (not JSON).")
        analysis = {"raw": analysis.get("raw")}

    # Pretty print result to assistant logs
    try:
        pretty = json.dumps(analysis, indent=2, ensure_ascii=False)
    except Exception:
        pretty = str(analysis)
    log("LLM RESULT:", pretty)

    # Post back to Jenkins build description if possible (non-fatal)
    try:
        posted = _post_back_to_jenkins(build_url, analysis)
        log("Jenkins post-back:", "ok" if posted else "skipped/failed")
    except Exception as e:
        log("Jenkins post-back failed with exception:", e)

    # Notify Slack (optional, non-fatal)
    try:
        notified = _notify_slack(analysis, build_url)
        log("Slack notify:", "ok" if notified else "skipped")
    except Exception as e:
        log("Slack notify failed:", e)

    # If analyzer suggested a pipeline_patch and we have GitHub creds, create a PR
    pipeline_patch = None
    if isinstance(analysis, dict):
        pipeline_patch = analysis.get("pipeline_patch") or analysis.get("pipeline", None)

    if pipeline_patch and GITHUB_TOKEN and GITHUB_REPO:
        try:
            # choose a canonical path to create the file
            path = ".github/workflows/ai-suggested.yml"
            result = _create_pull_request_with_patch(GITHUB_REPO, GITHUB_BASE_BRANCH, pipeline_patch, path=path)
            if result.get("pr_url"):
                log("Created PR:", result["pr_url"])
                # Optionally: notify Slack with PR link
                if SLACK_WEBHOOK:
                    _notify_slack({"diagnosis": analysis.get("diagnosis"), "confidence": analysis.get("confidence")}, build_url)
            else:
                log("PR creation result:", result)
        except Exception as e:
            log("PR creation failed:", e)

    return analysis

# -------------------------
# Async wrapper used by server
# -------------------------
import asyncio

async def process_event_async(event: Dict[str, Any]):
    """
    Async wrapper that runs the synchronous processing in a thread pool.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, process_event_sync, event)


# -------------------------
# Small CLI-style test helper
# -------------------------
if __name__ == "__main__":
    # Quick test: run with a sample payload file path or with example
    sample = {
        "build": {
            "status": "FAILURE",
            "full_url": "http://localhost/job/test/1/",
            "url": "http://localhost/job/test/1/",
            "logs": "Traceback (most recent call last):\\n  File 'a.py', line 1\\nException: boom\\n"
        }
    }
    print("Running local test...")
    print(process_event_sync(sample))
