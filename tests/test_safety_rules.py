import event_bot.db as db


def _candidate_ids(event_id: int, user_id: int, profile) -> set[int]:
    return {
        candidate.user_id
        for candidate in db.find_companions(event_id, user_id, profile)
    }


def test_contact_is_hidden_until_request_is_accepted(
    user_factory,
    event_factory,
    intent_factory,
):
    alice_id, alice = user_factory(
        user_id=101, name="Alice", username="alice_secret"
    )
    bob_id, _ = user_factory(user_id=202, name="Bob", username="bob_secret")
    event_id = event_factory(title="Закрытый концерт")
    intent_factory(alice_id, event_id)
    intent_factory(bob_id, event_id)

    companion = db.find_companions(event_id, alice_id, alice)[0]
    companion_card = db.format_companion_card(companion, 1)
    assert "bob_secret" not in companion_card
    assert "tg://user?id=" not in companion_card
    assert str(bob_id) not in companion_card

    result, request = db.create_connection_request(event_id, alice_id, bob_id)
    assert result == "created"
    notification = db.format_request_notification(request)
    assert "alice_secret" not in notification
    assert "bob_secret" not in notification
    assert "tg://user?id=" not in notification

    wrong_user_result, _ = db.accept_connection_request(request.id, alice_id)
    assert wrong_user_result == "unavailable"
    accepted, accepted_request = db.accept_connection_request(request.id, bob_id)
    assert accepted == "accepted"
    contact = db.format_contact_message(
        accepted_request.to_name,
        accepted_request.to_user,
        accepted_request.to_username,
        accepted_request.event_title,
    )
    assert "@bob_secret" in contact


def test_blocked_pair_hidden_from_both_candidate_lists(
    user_factory,
    event_factory,
    intent_factory,
):
    alice_id, alice = user_factory(user_id=1)
    bob_id, bob = user_factory(user_id=2)
    event_id = event_factory()
    intent_factory(alice_id, event_id)
    intent_factory(bob_id, event_id)

    assert bob_id in _candidate_ids(event_id, alice_id, alice)
    assert alice_id in _candidate_ids(event_id, bob_id, bob)
    assert db.block_user(alice_id, bob_id)
    assert bob_id not in _candidate_ids(event_id, alice_id, alice)
    assert alice_id not in _candidate_ids(event_id, bob_id, bob)


def test_block_rejects_pending_requests_in_both_directions(
    user_factory,
    event_factory,
    intent_factory,
):
    alice_id, _ = user_factory(user_id=1)
    bob_id, _ = user_factory(user_id=2)
    event_id = event_factory()
    intent_factory(alice_id, event_id)
    intent_factory(bob_id, event_id)
    _, outgoing = db.create_connection_request(event_id, alice_id, bob_id)
    _, incoming = db.create_connection_request(event_id, bob_id, alice_id)

    assert db.block_user(alice_id, bob_id)
    with db.get_connection() as conn:
        statuses = {
            row["id"]: row["status"]
            for row in conn.execute(
                "SELECT id, status FROM requests WHERE id IN (?, ?)",
                (outgoing.id, incoming.id),
            )
        }
    assert statuses == {outgoing.id: "rejected", incoming.id: "rejected"}


def test_invisible_candidate_is_hidden(
    user_factory,
    event_factory,
    intent_factory,
):
    viewer_id, viewer = user_factory(user_id=1)
    hidden_id, _ = user_factory(user_id=2)
    event_id = event_factory()
    intent_factory(viewer_id, event_id)
    intent_factory(hidden_id, event_id, visible=False)

    assert hidden_id not in _candidate_ids(event_id, viewer_id, viewer)


def test_hidden_viewer_cannot_see_candidates(
    user_factory,
    event_factory,
    intent_factory,
):
    viewer_id, viewer = user_factory(user_id=1)
    candidate_id, _ = user_factory(user_id=2)
    event_id = event_factory()
    intent_factory(viewer_id, event_id, visible=False)
    intent_factory(candidate_id, event_id)

    assert db.find_companions(event_id, viewer_id, viewer) == []


def test_candidate_without_confirmed_profile_is_hidden(
    user_factory,
    event_factory,
    intent_factory,
):
    viewer_id, viewer = user_factory(user_id=1)
    unconfirmed_id, _ = user_factory(user_id=2, confirmed=False)
    event_id = event_factory()
    intent_factory(viewer_id, event_id)
    intent_factory(unconfirmed_id, event_id)

    assert unconfirmed_id not in _candidate_ids(event_id, viewer_id, viewer)


def test_sixth_request_in_24_hours_is_not_created(
    user_factory,
    event_factory,
    intent_factory,
):
    sender_id, _ = user_factory(user_id=1)
    event_id = event_factory()
    intent_factory(sender_id, event_id)
    recipients = []
    for user_id in range(2, 8):
        recipient_id, _ = user_factory(user_id=user_id)
        intent_factory(recipient_id, event_id)
        recipients.append(recipient_id)

    for recipient_id in recipients[:5]:
        result, request = db.create_connection_request(
            event_id, sender_id, recipient_id
        )
        assert result == "created" and request is not None

    result, request = db.create_connection_request(
        event_id, sender_id, recipients[5]
    )
    assert result == "limit"
    assert request is None
    with db.get_connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM requests WHERE from_user = ?", (sender_id,)
        ).fetchone()[0]
    assert count == 5


def test_duplicate_request_for_same_pair_and_event_is_not_created(
    user_factory,
    event_factory,
    intent_factory,
):
    sender_id, _ = user_factory(user_id=1)
    recipient_id, _ = user_factory(user_id=2)
    event_id = event_factory()
    intent_factory(sender_id, event_id)
    intent_factory(recipient_id, event_id)

    first_result, first = db.create_connection_request(
        event_id, sender_id, recipient_id
    )
    second_result, second = db.create_connection_request(
        event_id, sender_id, recipient_id
    )

    assert first_result == "created"
    assert second_result == "already"
    assert second.id == first.id
    with db.get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
    assert count == 1
