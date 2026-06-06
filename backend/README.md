# Backend

Python dependencies are managed with uv from `pyproject.toml` and `uv.lock`.

```bash
mise exec -- uv sync --locked
mise exec -- uv run uvicorn app.main:app --reload
```

To add dependencies:

```bash
mise exec -- uv add <package>
```
