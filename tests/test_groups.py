import event_bot.db as db


def _join(user_id: int):
    assert db.set_group_matching_enabled(user_id, True)
    assignment = db.assign_user_to_interest_group(user_id)
    assert assignment is not None
    return assignment


def test_group_activates_for_three_users_with_common_interest(
    temp_db,
    user_factory,
):
    first, _ = user_factory(
        interests=["Концерты", "Выставки"],
        group_size_min=3,
        group_size_max=5,
    )
    second, _ = user_factory(
        interests=["концерты", "Кино"],
        group_size_min=3,
        group_size_max=5,
    )
    third, _ = user_factory(
        interests=["Концерты", "Театр"],
        group_size_min=3,
        group_size_max=5,
    )

    first_assignment = _join(first)
    second_assignment = _join(second)
    third_assignment = _join(third)

    assert first_assignment.group.status == "forming"
    assert second_assignment.group.id == first_assignment.group.id
    assert second_assignment.group.member_count == 2
    assert third_assignment.group.id == first_assignment.group.id
    assert third_assignment.group.status == "active"
    assert third_assignment.newly_activated is True
    assert third_assignment.notify_user_ids == [first, second, third]

    view = db.get_user_interest_group(first)
    assert view is not None
    assert view.group.member_count == 3
    assert view.group.topics == ["Концерты"]
    assert [member.user_id for member in view.members] == [first, second, third]


def test_incompatible_or_blocked_users_get_separate_groups(
    temp_db,
    user_factory,
):
    music_user, _ = user_factory(interests=["Музыка"])
    theatre_user, _ = user_factory(interests=["Театр"])
    blocked_music_user, _ = user_factory(interests=["музыка"])
    assert db.block_user(music_user, blocked_music_user)

    music = _join(music_user)
    theatre = _join(theatre_user)
    blocked_music = _join(blocked_music_user)

    assert len({music.group.id, theatre.group.id, blocked_music.group.id}) == 3


def test_disabling_matching_removes_membership_and_reopens_small_group(
    temp_db,
    user_factory,
):
    users = [user_factory(interests=["Кино"])[0] for _ in range(3)]
    assignments = [_join(user_id) for user_id in users]
    assert assignments[-1].group.status == "active"

    assert db.set_group_matching_enabled(users[-1], False)
    assert db.get_group_matching_enabled(users[-1]) is False
    assert db.get_user_interest_group(users[-1]) is None

    remaining = db.get_user_interest_group(users[0])
    assert remaining is not None
    assert remaining.group.member_count == 2
    assert remaining.group.status == "forming"


def test_existing_membership_is_idempotent(temp_db, user_factory):
    user_id, _ = user_factory(interests=["Лекции"])
    first = _join(user_id)
    second = db.assign_user_to_interest_group(user_id)

    assert second is not None
    assert second.group.id == first.group.id
    assert second.joined is False
    assert second.notify_user_ids == []


def test_group_connection_requires_consent_before_contact_is_available(
    temp_db,
    user_factory,
):
    users = [
        user_factory(interests=["Кино"], username=f"member{index}")[0]
        for index in range(3)
    ]
    assignments = [_join(user_id) for user_id in users]
    group_id = assignments[-1].group.id

    status, request = db.create_group_connection_request(
        group_id,
        users[0],
        users[1],
    )

    assert status == "created"
    assert request is not None
    assert db.get_group_connection_state(group_id, users[0], users[1])[0] == "pending_sent"
    assert db.get_group_connection_state(group_id, users[1], users[0])[0] == "pending_received"

    accepted, contact = db.accept_group_connection_request(request.id, users[1])
    assert accepted == "accepted"
    assert contact is not None
    assert contact.from_username == "member0"
    assert contact.to_username == "member1"
    assert db.get_group_connection_state(group_id, users[0], users[1])[0] == "connected"


def test_group_chat_and_invites_are_visible_only_to_members(
    temp_db,
    user_factory,
    event_factory,
):
    users = [user_factory(interests=["Театр"])[0] for _ in range(3)]
    assignments = [_join(user_id) for user_id in users]
    group_id = assignments[-1].group.id
    outsider, _ = user_factory(interests=["Спорт"])
    event_id = event_factory(title="Премьера")

    message_status, message = db.create_group_message(users[0], "Встречаемся у входа?")
    invite_status, invite = db.create_group_event_invite(users[0], event_id)

    assert message_status == "created"
    assert message is not None and message.group_id == group_id
    assert [item.text for item in db.get_group_messages(users[1])] == ["Встречаемся у входа?"]
    assert db.get_group_messages(outsider) == []
    assert invite_status == "created"
    assert invite is not None and invite.my_response == "going"
    assert db.get_group_event_invites(outsider) == []

    response_status, updated = db.respond_group_event_invite(
        users[1],
        invite.id,
        "going",
    )
    assert response_status == "updated"
    assert updated is not None
    assert len(updated.going_names) == 2


def test_group_chat_has_short_anti_spam_limit(temp_db, user_factory):
    users = [user_factory(interests=["Лекции"])[0] for _ in range(3)]
    [_join(user_id) for user_id in users]

    for index in range(6):
        assert db.create_group_message(users[0], f"Сообщение {index}")[0] == "created"

    assert db.create_group_message(users[0], "Седьмое сообщение")[0] == "limit"
