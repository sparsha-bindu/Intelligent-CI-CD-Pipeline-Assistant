# utils/github_utils.py
import os
import requests
import base64

GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')
REPO = os.getenv('GITHUB_REPO')
BASE_BRANCH = os.getenv('GITHUB_BASE_BRANCH', 'main')
API = 'https://api.github.com'
HEADERS = { 'Authorization': f'token {GITHUB_TOKEN}', 'Accept': 'application/vnd.github+json' }

def create_branch_and_commit(path, content, branch_name, commit_msg):
    # 1) Get base branch sha
    r = requests.get(f"{API}/repos/{REPO}/git/ref/heads/{BASE_BRANCH}", headers=HEADERS)
    r.raise_for_status()
    base_sha = r.json()['object']['sha']

    # 2) Create new branch ref
    r = requests.post(f"{API}/repos/{REPO}/git/refs", headers=HEADERS, json={
        'ref': f'refs/heads/{branch_name}', 'sha': base_sha
    })
    r.raise_for_status()

    # 3) Create blob
    blob = requests.post(f"{API}/repos/{REPO}/git/blobs", headers=HEADERS, json={
        'content': content, 'encoding': 'utf-8'
    })
    blob_sha = blob.json()['sha']

    # 4) Get base tree
    tree = requests.get(f"{API}/repos/{REPO}/git/commits/{base_sha}", headers=HEADERS).json()['tree']['sha']
    # 5) Create new tree with our file at given path
    new_tree = requests.post(f"{API}/repos/{REPO}/git/trees", headers=HEADERS, json={
        'base_tree': tree,
        'tree': [{'path': path, 'mode': '100644', 'type': 'blob', 'sha': blob_sha}]
    }).json()

    # 6) Create commit
    commit = requests.post(f"{API}/repos/{REPO}/git/commits", headers=HEADERS, json={
        'message': commit_msg, 'tree': new_tree['sha'], 'parents': [base_sha]
    }).json()

    # 7) Update ref to point to new commit
    requests.patch(f"{API}/repos/{REPO}/git/refs/heads/{branch_name}", headers=HEADERS, json={'sha': commit['sha']})

def create_pull_request(title, head_branch, body='CI assistant suggested change'):
    r = requests.post(f"{API}/repos/{REPO}/pulls", headers=HEADERS, json={
        'title': title, 'head': head_branch, 'base': BASE_BRANCH, 'body': body
    })
    r.raise_for_status()
    return r.json()
