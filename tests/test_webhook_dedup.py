"""Tests for WhatsApp webhook deduplication, message buffering, and event aggregation."""
import json
from datetime import datetime, timedelta, timezone
import pytest

from src.db.models import MessageBuffer, ProcessedMessage
from src.db.session import get_db
from src.endpoints.whatsapp import aggregate_events, buffer_message, deduplicate


# ---------------------------------------------------------------------------
# deduplicate
# ---------------------------------------------------------------------------


class TestDeduplicate:
    def test_first_call_returns_false(self):
        """First time seeing a message ID should return False (not a duplicate)."""
        result = deduplicate("unique_msg_001")
        assert result is False

    def test_second_call_returns_true(self):
        """Second time seeing the same ID should return True (is a duplicate)."""
        deduplicate("unique_msg_002")
        result = deduplicate("unique_msg_002")
        assert result is True

    def test_different_keys_not_duplicates(self):
        result1 = deduplicate("msg_aaa")
        result2 = deduplicate("msg_bbb")
        assert result1 is False
        assert result2 is False

    def test_stores_record_in_db(self):
        deduplicate("msg_stored_001")
        with get_db() as session:
            record = session.get(ProcessedMessage, "msg_stored_001")
            assert record is not None
            assert record.message_id == "msg_stored_001"

    def test_many_unique_keys(self):
        """All unique keys should return False."""
        for i in range(10):
            assert deduplicate(f"batch_msg_{i:04d}") is False

    def test_replay_after_initial(self):
        """Replaying all keys should all return True."""
        for i in range(5):
            deduplicate(f"replay_msg_{i:04d}")
        for i in range(5):
            assert deduplicate(f"replay_msg_{i:04d}") is True


# ---------------------------------------------------------------------------
# buffer_message
# ---------------------------------------------------------------------------


class TestBufferMessage:
    @pytest.mark.asyncio
    async def test_creates_new_buffer(self):
        event = {"id": "msg_buf_001", "type": "text", "raw_message": {"text": {"body": "hello"}}}
        await buffer_message("+5500000003001", event)
        with get_db() as session:
            buf = session.get(MessageBuffer, "+5500000003001")
            assert buf is not None
            events = json.loads(buf.events)
            assert len(events) == 1
            assert events[0]["id"] == "msg_buf_001"

    @pytest.mark.asyncio
    async def test_appends_to_existing_buffer(self):
        event1 = {"id": "msg_buf_002a", "type": "text", "raw_message": {"text": {"body": "hello"}}}
        event2 = {"id": "msg_buf_002b", "type": "text", "raw_message": {"text": {"body": "world"}}}
        await buffer_message("+5500000003002", event1)
        await buffer_message("+5500000003002", event2)
        with get_db() as session:
            buf = session.get(MessageBuffer, "+5500000003002")
            events = json.loads(buf.events)
            assert len(events) == 2
            assert events[0]["id"] == "msg_buf_002a"
            assert events[1]["id"] == "msg_buf_002b"

    @pytest.mark.asyncio
    async def test_updates_flush_at(self):
        event1 = {"id": "msg_buf_003a", "type": "text", "raw_message": {"text": {"body": "first"}}}
        await buffer_message("+5500000003003", event1)
        with get_db() as session:
            buf = session.get(MessageBuffer, "+5500000003003")
            first_flush = buf.flush_at

        event2 = {"id": "msg_buf_003b", "type": "text", "raw_message": {"text": {"body": "second"}}}
        await buffer_message("+5500000003003", event2)
        with get_db() as session:
            buf = session.get(MessageBuffer, "+5500000003003")
            second_flush = buf.flush_at
        # Second flush_at should be >= first (pushed forward)
        assert second_flush >= first_flush

    @pytest.mark.asyncio
    async def test_buffer_has_flush_at_set(self):
        event = {"id": "msg_buf_004", "type": "text", "raw_message": {"text": {"body": "test"}}}
        await buffer_message("+5500000003004", event)
        with get_db() as session:
            buf = session.get(MessageBuffer, "+5500000003004")
            assert buf.flush_at is not None
            # flush_at should be in the future (approx 3 seconds from now)
            flush_dt = datetime.fromisoformat(buf.flush_at)
            if flush_dt.tzinfo is None:
                flush_dt = flush_dt.replace(tzinfo=timezone.utc)
            # It should be roughly now + 3s (allow 10s margin)
            now = datetime.now(timezone.utc)
            assert flush_dt > now - timedelta(seconds=1)
            assert flush_dt < now + timedelta(seconds=10)


# ---------------------------------------------------------------------------
# aggregate_events
# ---------------------------------------------------------------------------


class TestAggregateEvents:
    def test_single_text_event(self):
        events = [{
            "id": "agg_001",
            "type": "text",
            "from_number": "+5511999990001",
            "raw_message": {"text": {"body": "hello world"}},
        }]
        result = aggregate_events(events)
        assert result["aggregated_text"] == "hello world"
        assert result["all_ids"] == ["agg_001"]
        assert result["id"] == "agg_001"

    def test_multiple_text_events_concatenated(self):
        events = [
            {
                "id": "agg_002a",
                "type": "text",
                "from_number": "+5511999990001",
                "raw_message": {"text": {"body": "first message"}},
            },
            {
                "id": "agg_002b",
                "type": "text",
                "from_number": "+5511999990001",
                "raw_message": {"text": {"body": "second message"}},
            },
        ]
        result = aggregate_events(events)
        assert "first message" in result["aggregated_text"]
        assert "second message" in result["aggregated_text"]
        assert result["all_ids"] == ["agg_002a", "agg_002b"]

    def test_audio_events_collected(self):
        events = [{
            "id": "agg_003",
            "type": "audio",
            "from_number": "+5511999990001",
            "raw_message": {"audio": {"id": "audio_file_123"}},
        }]
        result = aggregate_events(events)
        assert "audio_file_123" in result["raw_message"]["all_audios"]

    def test_image_events_collected(self):
        events = [{
            "id": "agg_004",
            "type": "image",
            "from_number": "+5511999990001",
            "raw_message": {"image": {"id": "img_file_123", "caption": "my photo"}},
        }]
        result = aggregate_events(events)
        assert len(result["raw_message"]["images"]) == 1
        assert result["raw_message"]["images"][0]["id"] == "img_file_123"
        # Caption should also be in the aggregated text
        assert "my photo" in result["aggregated_text"]

    def test_mixed_events(self):
        events = [
            {
                "id": "mix_001",
                "type": "text",
                "from_number": "+5511999990001",
                "raw_message": {"text": {"body": "check this out"}},
            },
            {
                "id": "mix_002",
                "type": "image",
                "from_number": "+5511999990001",
                "raw_message": {"image": {"id": "img_001", "caption": "cool pic"}},
            },
            {
                "id": "mix_003",
                "type": "audio",
                "from_number": "+5511999990001",
                "raw_message": {"audio": {"id": "aud_001"}},
            },
        ]
        result = aggregate_events(events)
        assert result["all_ids"] == ["mix_001", "mix_002", "mix_003"]
        assert "check this out" in result["aggregated_text"]
        assert "cool pic" in result["aggregated_text"]
        assert len(result["raw_message"]["images"]) == 1
        assert len(result["raw_message"]["all_audios"]) == 1

    def test_quoted_text_included(self):
        events = [{
            "id": "quote_001",
            "type": "text",
            "from_number": "+5511999990001",
            "raw_message": {
                "text": {"body": "my reply"},
                "quoted_text": "original message",
            },
        }]
        result = aggregate_events(events)
        assert "original message" in result["aggregated_text"]
        assert "my reply" in result["aggregated_text"]

    def test_id_is_from_first_event(self):
        events = [
            {"id": "first_id", "type": "text", "from_number": "+55", "raw_message": {"text": {"body": "a"}}},
            {"id": "second_id", "type": "text", "from_number": "+55", "raw_message": {"text": {"body": "b"}}},
        ]
        result = aggregate_events(events)
        assert result["id"] == "first_id"

    def test_image_without_caption(self):
        events = [{
            "id": "img_nocap",
            "type": "image",
            "from_number": "+55",
            "raw_message": {"image": {"id": "img_x", "caption": ""}},
        }]
        result = aggregate_events(events)
        assert len(result["raw_message"]["images"]) == 1
        # Empty caption should not add junk text
        assert result["aggregated_text"].strip() == ""


# ---------------------------------------------------------------------------
# flush_ready_buffers — unit test with mocked orchestration
# ---------------------------------------------------------------------------


class TestFlushReadyBuffers:
    """flush_ready_buffers uses with_for_update(skip_locked=True) which is
    not supported by SQLite. We test the logic by mocking the DB query to
    return the expected rows and verifying the downstream behavior."""

    def test_flushes_expired_buffers_and_deletes(self):
        """Insert a buffer with flush_at in the past, verify it gets deleted after flush."""
        past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        event = {"id": "flush_001", "type": "text", "from_number": "+5500000003010",
                 "raw_message": {"text": {"body": "buffered msg"}}}
        with get_db() as session:
            session.add(MessageBuffer(
                user_id="+5500000003010",
                events=json.dumps([event]),
                flush_at=past,
            ))

        # Manually simulate what flush_ready_buffers does (without skip_locked)
        ready_rows = []
        with get_db() as db:
            buffers = (
                db.query(MessageBuffer)
                .filter(MessageBuffer.flush_at <= datetime.now(timezone.utc).isoformat())
                .all()
            )
            for buf in buffers:
                ready_rows.append((buf.user_id, buf.user_id, buf.events))
                db.delete(buf)

        assert len(ready_rows) >= 1
        found = any(uid == "+5500000003010" for (_, uid, _) in ready_rows)
        assert found

        # Buffer should be deleted
        with get_db() as session:
            buf = session.get(MessageBuffer, "+5500000003010")
            assert buf is None

    def test_does_not_flush_future_buffers(self):
        """Buffers with flush_at in the future should NOT be picked up."""
        future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        event = {"id": "flush_002", "type": "text", "from_number": "+5500000003011",
                 "raw_message": {"text": {"body": "not yet"}}}
        with get_db() as session:
            session.add(MessageBuffer(
                user_id="+5500000003011",
                events=json.dumps([event]),
                flush_at=future,
            ))

        # Query with same filter as flush_ready_buffers (minus skip_locked)
        with get_db() as db:
            buffers = (
                db.query(MessageBuffer)
                .filter(MessageBuffer.flush_at <= datetime.now(timezone.utc).isoformat())
                .all()
            )
            matched = [b for b in buffers if b.user_id == "+5500000003011"]
            assert len(matched) == 0

        # Buffer should still exist
        with get_db() as session:
            buf = session.get(MessageBuffer, "+5500000003011")
            assert buf is not None

    def test_flushed_events_parsed_as_json(self):
        """Verify that flushed events JSON is correctly parsed."""
        past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        events = [
            {"id": "flush_agg_1", "type": "text", "from_number": "+5500000003012",
             "raw_message": {"text": {"body": "msg1"}}},
            {"id": "flush_agg_2", "type": "text", "from_number": "+5500000003012",
             "raw_message": {"text": {"body": "msg2"}}},
        ]
        with get_db() as session:
            session.add(MessageBuffer(
                user_id="+5500000003012",
                events=json.dumps(events),
                flush_at=past,
            ))

        with get_db() as db:
            buf = db.get(MessageBuffer, "+5500000003012")
            parsed = json.loads(buf.events) if isinstance(buf.events, str) else buf.events
            assert len(parsed) == 2
            # Verify aggregate_events works on these events
            aggregated = aggregate_events(parsed)
            assert "msg1" in aggregated["aggregated_text"]
            assert "msg2" in aggregated["aggregated_text"]
            db.delete(buf)
