from event_bot.db import delete_legacy_events, init_db


def main() -> None:
    init_db()
    deleted = delete_legacy_events()
    print(f"Удалено старых событий без источника: {deleted}")


if __name__ == "__main__":
    main()
