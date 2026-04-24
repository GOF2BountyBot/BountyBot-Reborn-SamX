from datetime import datetime

import pytest
from api.schemas.message_schemas import (
    EmbedField,
    EmbedPayload,
    Message,
    MessageCreateRequest,
    MessageListResponse,
    MessageResponse,
    MessageSummary,
    MessageUpdateRequest,
)
from pydantic import ValidationError


class TestEmbedField:
    def test_required_fields(self):
        f = EmbedField(name="n", value="v")
        assert f.inline is False
        with pytest.raises(ValidationError):
            EmbedField(value="v")
        with pytest.raises(ValidationError):
            EmbedField(name="n")


class TestEmbedPayload:
    def test_default_fields_list(self):
        ep = EmbedPayload()
        assert isinstance(ep.fields, list) and ep.fields == []

    def test_valid_timestamp(self):
        now = datetime.now()
        ep = EmbedPayload(timestamp=now)
        assert ep.timestamp == now


class TestMessage:
    def test_valid_instantiation(self):
        now = datetime.now()
        msg = Message(id=1, channel_id=2, author_id=3, timestamp=now)
        assert msg.content is None and msg.message_type == "default"

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            Message(channel_id=2, author_id=3, timestamp=datetime.now())


class TestMessageSummary:
    def test_valid_instantiation(self):
        now = datetime.now()
        ms = MessageSummary(id=1, author_id=2, timestamp=now)
        assert ms.content is None

    def test_missing_required(self):
        with pytest.raises(ValidationError):
            MessageSummary(author_id=2, timestamp=datetime.now())


class TestMessageCreateUpdateRequest:
    def test_valid(self):
        ep = EmbedPayload()
        req = MessageCreateRequest(content=ep)
        assert req.message_type == "default"
        req2 = MessageUpdateRequest(content=ep, message_type="x")
        assert req2.content == ep

    def test_missing_content(self):
        with pytest.raises(ValidationError):
            MessageCreateRequest()
        with pytest.raises(ValidationError):
            MessageUpdateRequest()


class TestMessageResponseList:
    def test_message_response_accepts_message_and_summary(self):
        now = datetime.now()
        msg = Message(id=1, channel_id=1, author_id=1, timestamp=now)
        summary = MessageSummary(id=2, author_id=2, timestamp=now)
        resp1 = MessageResponse(status="ok", data=msg)
        assert resp1.data.id == 1
        resp2 = MessageResponse(status="ok", data=summary)
        assert resp2.data.content is None

    def test_message_list_response_list_validation(self):
        now = datetime.now()
        msgs = [Message(id=i, channel_id=1, author_id=1, timestamp=now) for i in (1, 2)]
        lr = MessageListResponse(status="ok", data=msgs)
        assert len(lr.data) == 2 and lr.total_count is None
