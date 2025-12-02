# utils/notifier.py
import os
import requests

SLACK_WEBHOOK = os.getenv('SLACK_WEBHOOK')

def notify_slack(text: str):
    if not SLACK_WEBHOOK:
        return
    try:
        requests.post(SLACK_WEBHOOK, json={'text': text}, timeout=5)
    except Exception:
        pass
