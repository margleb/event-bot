from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError
from aiogram.methods import SendMessage
from aiogram.types import ForceReply, Message

import event_bot.db as db
from event_bot.handlers import pilot_feedback_reply, receive_feedback
from event_bot.pilot_feedback import (
    claim_recipient, dispatch, mark_delivery, question_text, reply_source, select_recipients,
)


CAMPAIGN = 'pilot_club_sep2026'
NOW = datetime(2026, 9, 5, 18, tzinfo=timezone.utc)


@pytest.fixture
def pilot_members(user_factory, event_factory, monkeypatch):
    monkeypatch.setenv('ADMIN_TELEGRAM_IDS', '99')
    for uid in (1, 2, 3, 4, 5, 6, 7, 8, 99):
        user_factory(user_id=uid)
    event_id = event_factory()
    entered = (NOW-timedelta(hours=36)).strftime(db.DB_DATETIME_FORMAT)
    active = (NOW-timedelta(hours=30)).strftime(db.DB_DATETIME_FORMAT)
    with db.get_connection() as conn:
        conn.executemany(
            'INSERT INTO user_acquisition (user_id,campaign,first_seen_at) VALUES (?,?,?)',
            [(u,CAMPAIGN,entered) for u in (1,2,3,4,5,6,7,8,99)],
        )
        conn.executemany(
            "INSERT INTO usage_events (user_id,event_name,source,created_at) VALUES (?,'miniapp.open','miniapp',?)",
            [(u,active) for u in (1,2,3,4,5,6,7,8,99)],
        )
        conn.execute('UPDATE usage_events SET created_at=? WHERE user_id=4', ((NOW-timedelta(hours=2)).strftime(db.DB_DATETIME_FORMAT),))
        conn.execute("INSERT INTO research_participants (campaign,user_id,participant_code) VALUES ('ux_test',5,'UX-5')")
        conn.execute("INSERT INTO user_suspensions (user_id,created_by) VALUES (6,99)")
        conn.execute("INSERT INTO inactivity_feedback_prompts (user_id,prompt_sent_at,delivery_status) VALUES (7,?,'sent')", (active,))
        conn.execute("INSERT INTO feedback_messages (user_id,name,message,source,created_at) VALUES (8,'Tester','Feedback','bot',?)", (active,))
        cursor = conn.execute(
            "INSERT INTO event_groups (event_id,status,target_size,activated_at) VALUES (?,'active',2,?)",
            (event_id,(NOW-timedelta(hours=28)).strftime(db.DB_DATETIME_FORMAT)),
        )
        gid = cursor.lastrowid
        conn.executemany('INSERT INTO event_group_members (group_id,user_id) VALUES (?,?)', [(gid,2),(gid,3)])
        conn.execute("INSERT INTO event_group_messages (group_id,user_id,message) VALUES (?,3,'Hello')", (gid,))
    return gid


def test_selection_excludes_recent_active_test_admin_suspended_and_surveyed_users(pilot_members):
    recipients = select_recipients(CAMPAIGN, now=NOW)
    assert [(r['user_id'],r['segment']) for r in recipients] == [(1,'no_search'),(2,'no_conversation')]


def test_claim_blocks_duplicate_and_reply_is_bound_to_recipient(pilot_members):
    recipient = {'user_id':1,'segment':'no_search'}
    assert claim_recipient(CAMPAIGN,recipient)
    assert not claim_recipient(CAMPAIGN,recipient)
    assert reply_source(1,123) is None
    mark_delivery(CAMPAIGN,1,'sent',123)
    assert reply_source(1,123) == 'pilot:pilot_club_sep2026:no_search'
    assert reply_source(2,123) is None
    assert [r['user_id'] for r in select_recipients(CAMPAIGN,now=NOW)] == [2]
    assert 1 not in db.get_inactive_feedback_user_ids(NOW+timedelta(days=365))
    assert db.delete_user_data(1)
    assert reply_source(1,123) is None


async def test_send_is_silent_once_and_does_not_inflate_usage(pilot_members):
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=123)))
    with db.get_connection() as conn:
        before = conn.execute('SELECT COUNT(*) FROM usage_events').fetchone()[0]
    result = await dispatch(bot,CAMPAIGN,limit=2,now=NOW)
    assert result == {'delivery':{'sent':2},'sent_segments':{'no_search':1,'no_conversation':1}}
    assert bot.send_message.await_count == 2
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs['disable_notification'] is True
    assert isinstance(kwargs['reply_markup'],ForceReply)
    await dispatch(bot,CAMPAIGN,limit=2,now=NOW)
    assert bot.send_message.await_count == 2
    with db.get_connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM usage_events').fetchone()[0] == before


async def test_uncertain_delivery_is_not_retried(pilot_members):
    error = TelegramNetworkError(method=SendMessage(chat_id=1,text='test'),message='timeout')
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=error))
    result = await dispatch(bot,CAMPAIGN,limit=2,now=NOW)
    assert result['delivery'] == {'unknown':1}
    bot.send_message.assert_awaited_once()
    assert [r['user_id'] for r in select_recipients(CAMPAIGN,now=NOW)] == [2]


async def test_blocked_bot_is_reported_as_failed_and_others_can_receive(pilot_members):
    error = TelegramForbiddenError(method=SendMessage(chat_id=1,text='test'),message='blocked')
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=[error,SimpleNamespace(message_id=124)]))
    result = await dispatch(bot,CAMPAIGN,limit=2,now=NOW)
    assert result['delivery'] == {'failed':1,'sent':1}


async def test_limit_and_quiet_hours_are_enforced(pilot_members):
    bot = SimpleNamespace(send_message=AsyncMock())
    with pytest.raises(ValueError):
        await dispatch(bot,CAMPAIGN,limit=1,now=NOW)
    with pytest.raises(ValueError):
        await dispatch(bot,CAMPAIGN,limit=2,now=NOW+timedelta(hours=2))
    bot.send_message.assert_not_awaited()


@pytest.mark.parametrize('answer',['Пока просто смотрел афишу','Да','👍'])
async def test_direct_reply_uses_existing_support_inbox_and_segment(pilot_members,monkeypatch,answer):
    claim_recipient(CAMPAIGN,{'user_id':1,'segment':'no_search'})
    mark_delivery(CAMPAIGN,1,'sent',123)
    message = AsyncMock(spec=Message)
    message.from_user = SimpleNamespace(id=1,first_name='Test',username=None)
    message.reply_to_message = SimpleNamespace(message_id=123)
    message.text = answer
    message.answer = AsyncMock()
    state = SimpleNamespace(clear=AsyncMock())
    notify = AsyncMock()
    monkeypatch.setattr('event_bot.handlers.notify_admins',notify)
    matched = pilot_feedback_reply(message)
    assert matched == {'feedback_source':'pilot:pilot_club_sep2026:no_search'}
    await receive_feedback(message,state,**matched)
    with db.get_connection() as conn:
        row = conn.execute('SELECT source,message FROM feedback_messages WHERE user_id=1').fetchone()
    assert row['source'] == matched['feedback_source']
    assert row['message'] == message.text
    notify.assert_awaited_once()
    message.reply_to_message = None
    assert pilot_feedback_reply(message) is False


def test_event_title_is_escaped_and_question_is_optional():
    text = question_text({'segment':'no_conversation','title':'Concert <tag>'})
    assert '&lt;tag&gt;' in text
    assert 'Ответ необязателен' in text
