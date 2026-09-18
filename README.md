# GridWise — FastAPI + Groq Starter

Starter architecture for the BUP CSE Fest 2026 Smart Campus Energy Optimization Challenge.

## Architecture

```text
Client / Judge
      |
      v
 FastAPI app
   |      |
   |      +---- GET /health
   |
   +---- POST /optimize-energy
              |
              v
       Groq LLM interpreter
              |
              v
       deterministic guardrails
              |
              v
       24-hour optimizer
              |
              v
       validated JSON response
```

Current status:

- `GET /health` is implemented.
- `POST /optimize-energy` validates the 24 hourly entries and calls Groq for operator-note interpretation.
- Deterministic directive guardrails are not implemented yet.
- The energy/battery optimizer is not implemented yet.

## Project structure

```text
app/
├── main.py
├── schemas.py
├── routes/
│   ├── __init__.py
│   ├── health.py
│   └── optimize.py
└── services/
    ├── __init__.py
    ├── groq_client.py
    └── llm_interpreter.py
Dockerfile
.dockerignore
.gitignore
requirements.txt
README.md
```

## Environment variables

Create your environment later as planned. The app expects:

```text
GROQ_API_KEY=your_key_here
GROQ_MODEL=llama-3.3-70b-versatile
```

Do not commit the API key to Git.

## Run locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

Expected:

```json
{"status":"ok"}
```

## Run with Docker

```bash
docker build -t gridwise-groq .
docker run --rm -p 8000:8000 --env-file .env gridwise-groq
```

Then:

```bash
curl http://127.0.0.1:8000/health
```

## Next implementation stages

1. Add deterministic validation for the six allowed directive types.
2. Convert validated directives into optimizer constraints.
3. Implement the 24-hour battery/grid/solar optimization.
4. Recalculate totals from the final hourly plan.
5. Add public-sample regression tests.
6. Harden error handling and latency.
