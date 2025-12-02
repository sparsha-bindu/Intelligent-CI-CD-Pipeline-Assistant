# log_processor.py
import re
from typing import List

ERROR_PATTERNS = [
    re.compile(r"(Traceback \(most recent call last\):[\s\S]+?)(?:\n\n|\Z)", re.MULTILINE),
    re.compile(r"(?s)(ERROR:.*?)(?:\n\n|\Z)"),
    re.compile(r"(?s)(Exception:.*?)(?:\n\n|\Z)"),
]

def extract_error_blocks(log: str, max_blocks=5) -> List[str]:
    blocks = []
    for pat in ERROR_PATTERNS:
        for m in pat.finditer(log):
            blocks.append(m.group(1).strip())
            if len(blocks) >= max_blocks:
                return blocks
    lines = log.splitlines()
    return ["\n".join(lines[-500:])]

def make_summary(blocks: List[str]) -> str:
    out = []
    for i, b in enumerate(blocks, 1):
        header = f"--- Error block {i} (first 2000 chars) ---"
        out.append(header)
        out.append(b[:2000])
    return "\n\n".join(out)
