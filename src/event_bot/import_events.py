import os
import sys
from dataclasses import dataclass
from time import perf_counter

from event_bot.db import init_db, upsert_source_events
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
    failed = False
    for source in configured_sources():
        started = perf_counter()
        try:
            stats = run_import(source)
        except SourceFetchError as error:
            failed = True
            stats = ImportStats(errors=1, elapsed=perf_counter() - started)
            print(f"{source.source_id}: импорт не выполнен: {error}", file=sys.stderr)
            print(f"{source.source_id}: {stats.format()}", file=sys.stderr)
            continue
        except Exception as error:
            failed = True
            stats = ImportStats(errors=1, elapsed=perf_counter() - started)
            print(f"{source.source_id}: импорт не выполнен: {error}", file=sys.stderr)
            print(f"{source.source_id}: {stats.format()}", file=sys.stderr)
            continue
        print(f"{source.source_id}: {stats.format()}")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
