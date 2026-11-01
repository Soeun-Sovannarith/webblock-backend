import json
import uuid
import pytest
from pydantic import ValidationError
from app.schemas.events import GenerationCancelledEvent, GenerationCompletedEvent, GenerationRequestedEvent
from app.services import event_bus, jcs, project_client

PROJECT_ID = str(uuid.uuid4())
REQUEST_ID = str(uuid.uuid4())
CAPTURED = {"componentCatalogVersion": "0.1.0", "generationContractId": str(uuid.uuid4()),
            "pages": [{"pageId": str(uuid.uuid4()), "path": "/", "name": "Home", "sortOrder": 0, "seoTitle": None,
                       "seoDescription": None, "puckTree": {"root": {"props": {"title": "Shop"}}, "content": []}}]}
INPUT_HASH = jcs.sha256_hex(CAPTURED)
CODEGEN_INPUT = {"generationRequestId": REQUEST_ID, "projectId": PROJECT_ID, "businessTypeCode": "clothing_shop",
                 "capturedInput": CAPTURED, "capturedInputHash": INPUT_HASH,
                 "generationContract": {"generationContractId": CAPTURED["generationContractId"],
                                        "apiContractVersion": "1", "productTypes": []}}


def _requested_body(**overrides):
    """project.generation_requested.v1 as the Project Service writes it (its GenerationRequested record)."""
    body = {"schema_version": 1, "message_id": str(uuid.uuid4()), "message_type": "project.generation_requested.v1",
            "occurred_at": "2026-10-29T10:00:00.123456Z", "correlation_id": REQUEST_ID, "project_id": PROJECT_ID,
            "generation_request_id": REQUEST_ID, "captured_input_hash": INPUT_HASH}
    return {**body, **overrides}


def test_generation_requested_event_reads_projects_snake_case_body():
    event = GenerationRequestedEvent.model_validate({**_requested_body(), "a_later_optional_field": 1})
    assert str(event.generation_request_id) == REQUEST_ID
    assert event.captured_input_hash == INPUT_HASH


@pytest.mark.parametrize("overrides", [{"captured_input_hash": "ABC"}, {"message_type": "project.other.v1"},
                                       {"schema_version": 2}, {"correlation_id": "bad id!"}])
def test_generation_requested_event_refuses_a_malformed_body(overrides):
    with pytest.raises(ValidationError):
        GenerationRequestedEvent.model_validate(_requested_body(**overrides))


def test_generation_completed_event_is_the_envelope_plus_three_fields():
    run_id = uuid.uuid4()
    event = GenerationCompletedEvent(message_id=event_bus.uuid7(), correlation_id=REQUEST_ID, project_id=PROJECT_ID,
                                     generation_request_id=REQUEST_ID, generation_run_id=run_id, outcome="succeeded")
    body = json.loads(event.model_dump_json())
    assert set(body) == {"schema_version", "message_id", "message_type", "occurred_at", "correlation_id", "project_id",
                         "generation_request_id", "generation_run_id", "outcome"}
    assert body["message_type"] == "ai_codegen.generation_completed.v1"
    assert body["schema_version"] == 1 and body["generation_run_id"] == str(run_id)
    with pytest.raises(ValidationError):
        GenerationCompletedEvent(message_id=uuid.uuid4(), correlation_id=REQUEST_ID, project_id=PROJECT_ID,
                                 generation_request_id=REQUEST_ID, generation_run_id=run_id, outcome="failed", extra=1)


def test_uuid7_has_version_7_and_sorts_by_time():
    first, second = event_bus.uuid7(), event_bus.uuid7()
    assert first.version == 7 and first.variant == uuid.RFC_4122
    assert first.int >> 80 <= second.int >> 80


class _FakeChannel:
    def __init__(self):
        self.published, self.declared, self.bound = [], [], []
    def confirm_delivery(self): pass
    def exchange_declare(self, **kw): self.declared.append(("exchange", kw))
    def queue_declare(self, **kw): self.declared.append(("queue", kw))
    def queue_bind(self, **kw): self.bound.append(kw)
    def basic_publish(self, **kw): self.published.append(kw)


class _FakeConnection:
    def __init__(self, channel): self._channel = channel
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def channel(self): return self._channel


def test_publish_generation_completed_sends_to_ai_codegen_events(monkeypatch):
    channel = _FakeChannel()
    monkeypatch.setattr("pika.BlockingConnection", lambda params: _FakeConnection(channel))
    run_id = uuid.uuid4()
    assert event_bus.publish_generation_completed(REQUEST_ID, run_id, PROJECT_ID, "FAILED") is True
    [sent] = channel.published
    body = json.loads(sent["body"])
    assert (sent["exchange"], sent["routing_key"]) == ("ai_codegen.events", "generation_completed.v1")
    assert sent["properties"].type == "ai_codegen.generation_completed.v1"
    assert sent["properties"].message_id == body["message_id"] and uuid.UUID(body["message_id"]).version == 7
    assert body["outcome"] == "failed" and body["correlation_id"] == REQUEST_ID


def test_publish_generation_completed_graceful_on_no_broker(monkeypatch):
    monkeypatch.setattr("pika.BlockingConnection", lambda *args, **kwargs: (_ for _ in ()).throw(Exception("No broker")))
    assert event_bus.publish_generation_completed(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "SUCCEEDED") is False


def test_consumer_queues_are_quorum_queues_with_a_dead_letter_queue():
    channel = _FakeChannel()
    event_bus._declare_queue(channel, event_bus.QUEUE_GENERATION_REQUESTED, "generation_requested.v1")
    queues = {kw["queue"]: kw["arguments"] for kind, kw in channel.declared if kind == "queue"}
    main = queues["ai_codegen.generation_requested.v1"]
    assert main["x-queue-type"] == "quorum" and main["x-delivery-limit"] == event_bus.DELIVERY_LIMIT
    assert main["x-dead-letter-routing-key"] == "ai_codegen.generation_requested.v1.dlq"
    assert "ai_codegen.generation_requested.v1.dlq" in queues
    assert channel.bound == [{"exchange": "project.events", "queue": "ai_codegen.generation_requested.v1",
                              "routing_key": "generation_requested.v1"}]


class _FakeAck:
    def __init__(self): self.acked, self.nacked = [], []
    def basic_ack(self, delivery_tag): self.acked.append(delivery_tag)
    def basic_nack(self, delivery_tag, requeue): self.nacked.append(requeue)


class _Method:
    delivery_tag = 7


@pytest.mark.parametrize("body, handler, acked, nacked", [
    (json.dumps(_requested_body()).encode(), lambda e: {"status": "QUEUED"}, [7], []),
    (b"not json", lambda e: None, [], [False]),
    (json.dumps(_requested_body(captured_input_hash="x")).encode(), lambda e: None, [], [False]),
    (json.dumps(_requested_body()).encode(), lambda e: (_ for _ in ()).throw(event_bus.RetryLater("later")), [], [True]),
])
def test_consumer_acks_after_the_handler_and_dead_letters_malformed_messages(monkeypatch, body, handler, acked, nacked):
    monkeypatch.setattr(event_bus, "RETRY_DELAY_SECONDS", 0)
    ch = _FakeAck()
    event_bus._handle(ch, _Method(), body, GenerationRequestedEvent, handler)
    assert (ch.acked, ch.nacked) == (acked, nacked)


# --- harness.handle_generation_requested_event ----------------------------------------------------------------------

FETCHED = project_client.CodegenInputResult("ok", CODEGEN_INPUT)


def _patch_harness(monkeypatch, *, existing_run=None, fetched=FETCHED):
    from app.services import harness, store
    calls = {"published": [], "runs": [], "failed": []}
    monkeypatch.setattr(store, "latest_run_for_request", lambda db, rid: existing_run and existing_run["harness_run_id"])
    monkeypatch.setattr(store, "get_run", lambda db, rid: existing_run)
    monkeypatch.setattr(event_bus, "publish_generation_completed", lambda *a: calls["published"].append(a))
    monkeypatch.setattr(project_client, "fetch_codegen_input", lambda rid: fetched)
    monkeypatch.setattr(store, "save_input_version", lambda db, req, **kw: calls.__setitem__("version", req) or "v1")
    monkeypatch.setattr(store, "baseline_request", lambda db, pid: None)
    monkeypatch.setattr(store, "create_run", lambda db, **kw: calls["runs"].append(kw) or "run-1")
    monkeypatch.setattr(store, "append_log", lambda run_id, msg: None)
    monkeypatch.setattr(harness, "_fail_request", lambda event, cid, code, msg: calls["failed"].append(code) or {"status": "FAILED"})
    return harness, calls


def test_generation_requested_queues_a_run_for_the_hashed_input(monkeypatch):
    harness, calls = _patch_harness(monkeypatch)
    result = harness.handle_generation_requested_event(GenerationRequestedEvent.model_validate(_requested_body()))
    assert result == {"run_id": "run-1", "status": "QUEUED"}
    assert calls["runs"][0]["input_hash"] == INPUT_HASH
    assert calls["runs"][0]["contract_id"] == CAPTURED["generationContractId"]
    page = calls["version"]["captured_input"]["pages"][0]
    assert (page["path"], page["puck_tree"]) == ("/", CAPTURED["pages"][0]["puckTree"])


def test_generation_requested_again_republishes_a_finished_run(monkeypatch):
    run = {"harness_run_id": "run-0", "status": "SUCCEEDED", "project_id": PROJECT_ID}
    harness, calls = _patch_harness(monkeypatch, existing_run=run)
    result = harness.handle_generation_requested_event(GenerationRequestedEvent.model_validate(_requested_body()))
    assert result["status"] == "REPUBLISHED"
    assert calls["published"] == [(REQUEST_ID, "run-0", PROJECT_ID, "SUCCEEDED")] and calls["runs"] == []


def test_generation_requested_again_while_running_does_nothing(monkeypatch):
    run = {"harness_run_id": "run-0", "status": "RUNNING", "project_id": PROJECT_ID}
    harness, calls = _patch_harness(monkeypatch, existing_run=run)
    result = harness.handle_generation_requested_event(GenerationRequestedEvent.model_validate(_requested_body()))
    assert result["status"] == "ALREADY_RUNNING" and calls["published"] == [] and calls["runs"] == []


def test_generation_requested_retries_when_project_is_unavailable(monkeypatch):
    harness, _ = _patch_harness(monkeypatch, fetched=project_client.CodegenInputResult("unavailable"))
    with pytest.raises(event_bus.RetryLater):
        harness.handle_generation_requested_event(GenerationRequestedEvent.model_validate(_requested_body()))


def test_generation_requested_stops_when_the_request_is_no_longer_generating(monkeypatch):
    harness, calls = _patch_harness(monkeypatch, fetched=project_client.CodegenInputResult("not_generating"))
    result = harness.handle_generation_requested_event(GenerationRequestedEvent.model_validate(_requested_body()))
    assert result["status"] == "IGNORED" and calls["runs"] == []


def test_generation_requested_refuses_an_input_with_another_hash(monkeypatch):
    harness, calls = _patch_harness(monkeypatch)
    event = GenerationRequestedEvent.model_validate(_requested_body(captured_input_hash="0" * 64))
    assert harness.handle_generation_requested_event(event)["status"] == "FAILED"
    assert calls["failed"] == ["INPUT_HASH_MISMATCH"] and calls["runs"] == []


def test_generation_requested_refuses_an_input_of_another_request(monkeypatch):
    other = {**CODEGEN_INPUT, "generationRequestId": str(uuid.uuid4())}
    harness, calls = _patch_harness(monkeypatch, fetched=project_client.CodegenInputResult("ok", other))
    harness.handle_generation_requested_event(GenerationRequestedEvent.model_validate(_requested_body()))
    assert calls["failed"] == ["INPUT_HASH_MISMATCH"]


def test_handle_generation_cancelled_event(monkeypatch):
    from app.services import harness, store
    mock_run_id = str(uuid.uuid4())
    event = GenerationCancelledEvent(message_id=uuid.uuid4(), occurred_at="2026-10-29T10:00:00Z",
                                     correlation_id=REQUEST_ID, project_id=PROJECT_ID, generation_request_id=REQUEST_ID)
    monkeypatch.setattr(store, "cancel_active_run_for_request", lambda db, req_id: mock_run_id)
    result = harness.handle_generation_cancelled_event(event)
    assert result == {"cancelled": True, "run_id": mock_run_id}
