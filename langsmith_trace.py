# langsmith_trace.py
import os
import requests

LANGSMITH_API_KEY = os.getenv('LANGSMITH_API_KEY')

def trace_call(prompt: str, response: dict, metadata: dict | None = None):
    if not LANGSMITH_API_KEY:
        return
    # Minimal: post events to LangSmith ingestion endpoint (check LangSmith docs for exact API)
    try:
        requests.post('https://api.langsmith.ai/v1/events', json={'prompt': prompt, 'response': response, 'metadata': metadata}, headers={'Authorization': f'Bearer {LANGSMITH_API_KEY}'}, timeout=3)
    except Exception:
        pass
