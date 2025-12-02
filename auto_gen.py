# auto_gen.py
import os
from llm_analyzer import analyze_with_openai

def inspect_repo(path='.') -> dict:
    manifest = {}
    for root, dirs, files in os.walk(path):
        for f in files:
            if f in ("package.json", "requirements.txt", "pyproject.toml", "Dockerfile", "pom.xml", "build.gradle"):
                p = os.path.join(root, f)
                try:
                    with open(p, 'r', encoding='utf-8') as fh:
                        manifest[os.path.relpath(p, path)] = fh.read()[:4000]
                except Exception:
                    manifest[os.path.relpath(p, path)] = '<unreadable>'
    return manifest

def generate_pipeline(manifest: dict, target='github') -> str:
    desc = 'Detected files:\n' + '\n'.join(manifest.keys())
    result = analyze_with_openai(f"Please generate a {target} CI pipeline for this project.\n{desc}", repo_files=manifest)
    return result.get('pipeline_patch') or result.get('raw')
