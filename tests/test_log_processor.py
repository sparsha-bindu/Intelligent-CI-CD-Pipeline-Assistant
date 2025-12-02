from log_processor import extract_error_blocks

def test_extract_traceback():
    log = 'some output\nTraceback (most recent call last):\n  File "a.py", line 1, in <module>\nException: boom\n\n'
    blocks = extract_error_blocks(log)
    assert len(blocks) >= 1
    assert 'Traceback' in blocks[0]
