import os, time
from groq import Groq

client = Groq(api_key=os.environ['GROQ_API_KEY'])
for i in range(5):
    t0 = time.perf_counter()
    try:
        r = client.chat.completions.create(
            model='openai/gpt-oss-20b',
            max_completion_tokens=50,
            temperature=0.0,
            response_format={'type': 'json_object'},
            messages=[
                {'role': 'system', 'content': 'Return JSON: {\"ok\": true}'},
                {'role': 'user', 'content': 'go'},
            ],
        )
        dt = time.perf_counter() - t0
        print(f'{i}: {dt:.2f}s OK')
    except Exception as e:
        dt = time.perf_counter() - t0
        print(f'{i}: {dt:.2f}s FAILED: {e}')
