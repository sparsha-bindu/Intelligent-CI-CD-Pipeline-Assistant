# process_event.py
import uuid
from log_processor import extract_error_blocks, make_summary
from auto_gen import inspect_repo
from llm_analyzer import analyze_with_openai
from utils.github_utils import create_branch_and_commit, create_pull_request
from utils.notifier import notify_slack
from langsmith_trace import trace_call

def process_event_sync(event: dict):
    logs = event.get('logs', '')
    blocks = extract_error_blocks(logs)
    summary = make_summary(blocks)
    repo_manifest = inspect_repo('.')
    analysis = analyze_with_openai(summary, repo_files=repo_manifest)

    # Trace
    trace_call(summary, analysis, {'event_url': event.get('url')})

    # Notify
    diagnosis = analysis.get('diagnosis') or analysis.get('raw', '')
    notify_slack(f"CI Assistant: {diagnosis}\nSource: {event.get('source')}\nURL: {event.get('url')}")

    # If pipeline patch present, create PR
    patch = analysis.get('pipeline_patch')
    if patch and isinstance(patch, str) and len(patch) > 10:
        branch = f"ci-assistant/fix-{uuid.uuid4().hex[:8]}"
        path = '.github/workflows/ci.yml'  # example; adjust based on intended target
        create_branch_and_commit(path, patch, branch, 'AI: suggested pipeline fix')
        pr = create_pull_request('AI Suggested Pipeline Fix', branch)
        notify_slack(f"Created PR: {pr.get('html_url')}")

async def process_event_async(event: dict):
    # wrapper for server background task
    return process_event_sync(event)
