# Coding Style

> Source of truth for Python conventions in this project. Applies to `api/`, `workers/`, `ai_engine/`, `tests/`.

## Language & Tooling

- **Python 3.11+** — use `match` statements where appropriate, `Self` type, etc.
- Every module starts with `from __future__ import annotations`
- Format: `ruff format` (Black-compatible), 100-char line limit
- Lint: `ruff check`
- Type check: `mypy` (strict on `ai_engine/` and `api/schemas/`, lenient elsewhere)

## Imports

Order (enforced by ruff):
1. `__future__`
2. stdlib
3. third-party
4. local (`api.*`, `workers.*`, `ai_engine.*`)

Use absolute imports. Never `from .foo import bar`.

```python
# Good
from api.schemas.training import TrainingRequest
from ai_engine.training.unsloth_trainer import UnslothTrainer

# Bad
from .schemas.training import TrainingRequest
```

## Type Hints

**Type hints everywhere.** Public functions must declare return types.

```python
async def get_dataset(dataset_id: int, session: AsyncSession) -> Dataset | None:
    ...
```

Use:
- `X | None` instead of `Optional[X]` (PEP 604)
- `list[X]`, `dict[K, V]` (PEP 585) instead of `List`, `Dict`
- `TypedDict` for structured dicts crossing boundaries
- `Protocol` for duck-typed interfaces

## Pydantic v2

Use the v2 API exclusively:

```python
from pydantic import BaseModel, Field, field_validator, model_validator

class TrainingRequest(BaseModel):
    mode: TrainingMode
    manual_config: ManualConfig | None = None
    hpo_config: HPOConfig | None = None

    @model_validator(mode="after")
    def validate_mode_config(self) -> Self:
        if self.mode == TrainingMode.MANUAL and self.manual_config is None:
            raise ValueError("manual_config required when mode=manual")
        if self.mode == TrainingMode.HPO and self.hpo_config is None:
            raise ValueError("hpo_config required when mode=hpo")
        return self
```

- Use `model_dump()`, `model_validate()` — **never** `.dict()` or `.parse_obj()`
- Use `Field(..., description="...")` for OpenAPI docs
- Discriminated unions: use `Field(discriminator="...")` when the model has a tagged variant

## SQLAlchemy 2.0

Use the **declarative + async** style:

```python
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class Dataset(Base):
    __tablename__ = "datasets"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(default=func.now())
```

- Use `select(...)` not `query(...)` (1.x style)
- Async session: `AsyncSession` + `async with session.begin():`
- Never expose ORM objects directly through the API — convert to a Pydantic schema first

## Async / Await

- All FastAPI route handlers that touch DB or network are `async def`
- All Celery tasks are **synchronous** (Celery doesn't natively run coroutines) — use `asyncio.run(...)` only at the boundary if you must call async code
- Use `httpx.AsyncClient` not `requests` in async code

## Error Handling

- HTTP errors: raise `HTTPException(status_code=..., detail=...)` — let FastAPI render
- Validation errors: let Pydantic raise; FastAPI returns 422 automatically
- Internal errors: log full stack, raise generic `HTTPException(500)` with a safe message
- Domain exceptions in `ai_engine/`: define module-local `class XxxError(Exception)` subclasses; let workers translate to user-facing messages

## Logging

```python
import logging
log = logging.getLogger(__name__)

log.info("training started", extra={"job_id": job_id, "model": base_model})
```

- **Never `print`** outside `examples/`
- Use the module-level logger, not the root
- INFO for major events, DEBUG for fine-grained
- Structured logging: pass values via `extra={}`, not f-strings, when grep-ability matters

## Configuration

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str
    redis_url: str
    openrouter_api_key: str
    mlflow_tracking_uri: str = "http://localhost:5000"
```

- All config from env, loaded into `Settings`
- **No hardcoded URLs, ports, or credentials** anywhere in code
- Singleton via `@lru_cache`:
  ```python
  @lru_cache
  def get_settings() -> Settings:
      return Settings()
  ```

## Docstrings

Google style on public functions:

```python
def deduplicate(samples: list[dict], threshold: float = 0.9) -> list[dict]:
    """Remove near-duplicate samples by embedding cosine similarity.

    Args:
        samples: Input samples; each must have a "text" field.
        threshold: Cosine similarity above which samples are considered duplicates.

    Returns:
        Deduplicated samples in original order.

    Raises:
        ValueError: If samples is empty or any sample lacks a "text" field.
    """
```

Skip docstrings on trivial private helpers — use a clear name instead.

## Comments

Default: **no comments**. Only add a comment when the **why** is non-obvious — a workaround, a hidden invariant, a surprising behavior. Never explain the **what** (the code already says that).

## File Layout

- One public class or one cohesive function group per file
- Test file mirrors source path: `ai_engine/training/foo.py` → `tests/ai_engine/training/test_foo.py`
- Avoid `utils.py` dumping grounds — name files for what they do (`text_normalization.py`, not `utils.py`)
