import os
import sys
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter

from event_bot.db import init_db, record_source_sync_run, upsert_source_events
from dotenv import load_dotenv

from event_bot.sources import (
    EventSource,
    KudaGoSource,
    SourceFetchError,
    TicketmasterSource,
    TimepadSource,
)


@dataclass
class ImportStats:
    added: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0
    elapsed: float = 0.0
    fetched: int = 0

    def format(self) -> str:
        return (
            f"добавлено: {self.added} / обновлено: {self.updated} / "
            f"пропущено: {self.skipped} / ошибок: {self.errors} / "
            f"время: {self.elapsed:.2f} с"
        )


def run_import(source: EventSource | None = None) -> ImportStats:
    """Полностью получает источник и только затем открывает транзакцию БД."""
    started = perf_counter()
    source = source or KudaGoSource()
    stats = ImportStats()

    raw_events = source.fetch()
    stats.fetched = len(raw_events)
    events = []
    for raw in raw_events:
        if not isinstance(raw, dict):
            stats.errors += 1
            continue
        try:
            event = source.normalize(raw)
        except Exception:
            stats.errors += 1
            continue
        if event is None:
            stats.skipped += 1
            continue
        events.append(event)

    # Пустой результат не должен помечать существующий каталог прошедшим.
    if events:
        init_db()
        added, updated, write_errors = upsert_source_events(events)
        stats.added = added
        stats.updated = updated
        stats.errors += write_errors

    stats.elapsed = perf_counter() - started
    return stats


def configured_sources() -> list[EventSource]:
    """Официальные API; закрытые ключи включают дополнительные каталоги."""
    sources: list[EventSource] = [KudaGoSource()]
    timepad_token = os.getenv("TIMEPAD_API_TOKEN", "").strip()
    if timepad_token:
        sources.append(TimepadSource(timepad_token))
    ticketmaster_key = os.getenv("TICKETMASTER_API_KEY", "").strip()
    if ticketmaster_key:
        sources.append(TicketmasterSource(ticketmaster_key))
    return sources


def main() -> None:
    load_dotenv()
    init_db()
    failed = False
    failures: list[tuple[str, str]] = []
    for source in configured_sources():
        started = perf_counter()
        started_at = datetime.now(timezone.utc)
        try:
            stats = run_import(source)
        except SourceFetchError as error:
            failed = True
            stats = ImportStats(errors=1, elapsed=perf_counter() - started)
            finished_at = datetime.now(timezone.utc)
            record_source_sync_run(
                source.source_id,
                "failed",
                errors=stats.errors,
                error_message=str(error),
                started_at=started_at,
                finished_at=finished_at,
            )
            failures.append((source.source_id, str(error)))
            print(f"{source.source_id}: импорт не выполнен: {error}", file=sys.stderr)
            print(f"{source.source_id}: {stats.format()}", file=sys.stderr)
            continue
        except Exception as error:
            failed = True
            stats = ImportStats(errors=1, elapsed=perf_counter() - started)
            finished_at = datetime.now(timezone.utc)
            record_source_sync_run(
                source.source_id,
                "failed",
                errors=stats.errors,
                error_message=str(error),
                started_at=started_at,
                finished_at=finished_at,
            )
            failures.append((source.source_id, str(error)))
            print(f"{source.source_id}: импорт не выполнен: {error}", file=sys.stderr)
            print(f"{source.source_id}: {stats.format()}", file=sys.stderr)
            continue
        finished_at = datetime.now(timezone.utc)
        record_source_sync_run(
            source.source_id,
            "warning" if stats.errors else "success",
            added=stats.added,
            updated=stats.updated,
            skipped=stats.skipped,
            errors=stats.errors,
            fetched=stats.fetched,
            started_at=started_at,
            finished_at=finished_at,
        )
        print(f"{source.source_id}: {stats.format()}")
    if failures:
        asyncio.run(_notify_import_failures(failures))
    raise SystemExit(1 if failed else 0)


async def _notify_import_failures(failures: list[tuple[str, str]]) -> None:
    """Send one compact operational alert instead of silently losing the catalog."""
    from aiogram import Bot
    from event_bot.analytics import get_admin_ids

    token = os.getenv("BOT_TOKEN", "").strip()
    admin_ids = get_admin_ids()
    if not token or not admin_ids:
        return
    text = "⚠️ Сбой импорта афиши:\n" + "\n".join(
        f"• {source_id}: {message[:300]}" for source_id, message in failures
    )
    bot = Bot(token=token)
    try:
        for admin_id in admin_ids:
            try:
                await bot.send_message(admin_id, text)
            except Exception:
                pass
    finally:
        await bot.session.close()


if __name__ == "__main__":
    main()
