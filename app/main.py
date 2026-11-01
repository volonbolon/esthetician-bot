import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.engine import Engine, build_app
from app.jobs import (
    REMINDER_POLL_INTERVAL_S,
    SYNC_POLL_INTERVAL_S,
    reminder_loop,
    sync_loop,
)


def create_app(
    engine: Engine | None = None,
    *,
    enable_jobs: bool | None = None,
    reminder_interval_s: float = REMINDER_POLL_INTERVAL_S,
    sync_interval_s: float = SYNC_POLL_INTERVAL_S,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        eng = engine if engine is not None else build_app()
        app.state.engine = eng
        run_loops = (
            enable_jobs
            if enable_jobs is not None
            else eng.config.messenger != "console"
        )
        tasks: list[asyncio.Task[None]] = []
        if run_loops:
            tasks = [
                asyncio.create_task(
                    reminder_loop(eng, interval_s=reminder_interval_s),
                    name="reminder_loop",
                ),
                asyncio.create_task(
                    sync_loop(eng, interval_s=sync_interval_s),
                    name="sync_loop",
                ),
            ]
        app.state.job_tasks = tasks
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
