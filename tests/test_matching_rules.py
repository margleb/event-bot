from datetime import datetime, timedelta

import numpy as np

import event_bot.db as db
from event_bot.embedding_provider import vector_to_blob
from event_bot.models import Profile


def test_budget_filter_wins_over_maximum_embedding_similarity(event_factory):
    profile_vector = np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
    event_factory(
        title="Слишком дорого",
        external_id="expensive",
        price_min=5_000,
        price_max=5_000,
        embedding=profile_vector,
        embedding_model="semantic-test",
    )
    event_factory(
        title="В бюджете",
        external_id="affordable",
        price_min=900,
        price_max=900,
        embedding=np.asarray([0.0, 1.0, 0.0], dtype=np.float32),
        embedding_model="semantic-test",
    )

    found = db.find_events(
        Profile(interests=["музыка"], budget_rub=1_000),
        profile_embedding=vector_to_blob(profile_vector),
        profile_embedding_model="semantic-test",
    )

    assert [event.title for event in found] == ["В бюджете"]


def test_past_and_non_active_events_never_appear(event_factory):
    matching = np.asarray([1.0, 0.0], dtype=np.float32)
    event_factory(
        title="Прошедшее",
        external_id="past",
        date=datetime(2020, 1, 1),
        embedding=matching,
        embedding_model="semantic-test",
    )
    event_factory(
        title="Отменённое",
        external_id="cancelled",
        status="cancelled",
        embedding=matching,
        embedding_model="semantic-test",
    )
    event_factory(
        title="Актуальное",
        external_id="active",
        embedding=np.asarray([0.0, 1.0], dtype=np.float32),
        embedding_model="semantic-test",
    )

    found = db.find_events(
        Profile(interests=["что угодно"]),
        profile_embedding=vector_to_blob(matching),
        profile_embedding_model="semantic-test",
    )

    assert [event.title for event in found] == ["Актуальное"]


def test_disabled_embeddings_fall_back_to_tags_without_error(event_factory):
    event_factory(title="Спектакль", tags=["театр"], external_id="theater")
    event_factory(title="Матч", tags=["спорт"], external_id="sport")
    # Повреждённый вектор имитирует недоступный/непригодный semantic-слой:
    # наружу не должна выйти ошибка, а выдача остаётся полезной.
    with db.get_connection() as conn:
        conn.execute(
            """
            UPDATE events
            SET embedding = x'010203', embedding_model = 'broken-provider'
            WHERE external_id = 'theater'
            """
        )

    found = db.find_events(
        Profile(interests=["театр"]),
        profile_embedding=vector_to_blob(
            np.asarray([1.0, 0.0], dtype=np.float32)
        ),
        profile_embedding_model="broken-provider",
    )

    assert found[0].title == "Спектакль"


def test_profile_days_filter_events_before_ranking(event_factory):
    start = datetime(2099, 1, 1, 19, 0)
    monday = start + timedelta(days=(7 - start.weekday()) % 7)
    tuesday = monday + timedelta(days=1)
    event_factory(title="В понедельник", date=monday, external_id="monday")
    event_factory(title="Во вторник", date=tuesday, external_id="tuesday")

    found = db.find_events(Profile(interests=["музыка"], days=["mon"]))

    assert [event.title for event in found] == ["В понедельник"]
