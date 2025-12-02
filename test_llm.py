from llm_analyzer import analyze_with_openai
s = "Traceback (most recent call last):\n  File 'a.py', line 1\nException: boom\n"
print(analyze_with_openai(s))
