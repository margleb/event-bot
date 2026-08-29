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
