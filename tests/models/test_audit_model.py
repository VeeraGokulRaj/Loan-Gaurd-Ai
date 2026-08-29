"""
Test cases for app.models.audit.AuditEvent.log_events_bulk.

Covers the bulk audit-event creation path with SHA-256 hash chaining including
positive, negative, edge, boundary and invalid input scenarios.
"""

import hashlib
from datetime import timedelta

import pytest
from django.utils import timezone

from app.models.audit import AuditEvent
from tests.factory.user_factory import UserFactory


def _event_data(event_type="EXCEPTION_CREATED", **overrides):
    data = {
        "event_type": event_type,
        "actor": None,
        "actor_role": AuditEvent.ActorRole.SYSTEM,
        "loan_id": None,
        "batch_id": None,
        "payload": {},
    }
    data.update(overrides)
    return data


@pytest.mark.django_db
class TestLogEventsBulk:
    def test_empty_list_returns_empty(self):
        assert AuditEvent.log_events_bulk([]) == []
        assert AuditEvent.objects.count() == 0

    def test_none_returns_empty(self):
        assert AuditEvent.log_events_bulk(None) == []
        assert AuditEvent.objects.count() == 0

    def test_empty_dict_returns_empty(self):
        assert AuditEvent.log_events_bulk({}) == []
        assert AuditEvent.objects.count() == 0

    def test_single_event_with_defaults(self):
        events = AuditEvent.log_events_bulk([{}])
        assert len(events) == 1
        event = AuditEvent.objects.get()
        assert event.event_type == "UNKNOWN_EVENT"
        assert event.actor is None
        assert event.actor_role == AuditEvent.ActorRole.SYSTEM
        assert event.payload == {}
        assert event.prev_hash == "0" * 64
        assert len(event.event_hash) == 64

    def test_hash_chain_across_bulk_events(self):
        events = AuditEvent.log_events_bulk(
            [
                _event_data(loan_id="LG-1"),
                _event_data(loan_id="LG-2"),
                _event_data(loan_id="LG-3"),
            ]
        )
        assert AuditEvent.objects.count() == 3
        assert events[0].prev_hash == "0" * 64
        assert events[1].prev_hash == events[0].event_hash
        assert events[2].prev_hash == events[1].event_hash

    def test_chain_links_to_previous_audit_trail(self):
        earlier = AuditEvent.objects.create(
            timestamp=timezone.now() - timedelta(minutes=5),
            event_type="EARLIER",
            actor_role=AuditEvent.ActorRole.SYSTEM,
            event_hash="e" * 64,
        )
        events = AuditEvent.log_events_bulk([_event_data(), _event_data()])
        assert events[0].prev_hash == earlier.event_hash
        assert events[1].prev_hash == events[0].event_hash

    def test_event_hash_is_cryptographically_deterministic(self, monkeypatch):
        fixed_ts = timezone.now().replace(microsecond=0)
        monkeypatch.setattr("app.models.audit.timezone.now", lambda: fixed_ts)

        prev_hash = "0" * 64
        ts_str = fixed_ts.isoformat()
        payload_str = '{"request_id": "REQ-1"}'
        hash_input = f"{prev_hash}|{ts_str}|EXCEPTION_CREATED|SYSTEM|LG-42|{payload_str}".encode()
        expected = hashlib.sha256(hash_input).hexdigest()

        events = AuditEvent.log_events_bulk(
            [
                {
                    "event_type": "EXCEPTION_CREATED",
                    "actor": None,
                    "actor_role": None,
                    "loan_id": "LG-42",
                    "payload": {"request_id": "REQ-1"},
                }
            ]
        )
        assert events[0].event_hash == expected

    def test_missing_event_type_defaults_to_unknown(self):
        AuditEvent.log_events_bulk([{"payload": {"a": 1}}])
        assert AuditEvent.objects.get().event_type == "UNKNOWN_EVENT"

    def test_none_payload_defaults_to_empty_dict(self):
        AuditEvent.log_events_bulk([{"payload": None}])
        assert AuditEvent.objects.get().payload == {}

    def test_falsy_payload_defaults_to_empty_dict(self):
        AuditEvent.log_events_bulk([{"payload": ""}])
        assert AuditEvent.objects.get().payload == {}

    def test_actor_role_integer_passthrough(self):
        AuditEvent.log_events_bulk([{"actor_role": AuditEvent.ActorRole.REVIEWER}])
        assert AuditEvent.objects.get().actor_role == AuditEvent.ActorRole.REVIEWER

    def test_actor_role_string_resolution(self):
        AuditEvent.log_events_bulk([{"actor_role": "REVIEWER"}])
        assert AuditEvent.objects.get().actor_role == AuditEvent.ActorRole.REVIEWER

    def test_actor_role_operator_string_normalized(self):
        AuditEvent.log_events_bulk([{"actor_role": "OPERATOR"}])
        assert AuditEvent.objects.get().actor_role == AuditEvent.ActorRole.DATA_OPERATOR

    def test_invalid_actor_role_string_falls_back_to_system(self):
        AuditEvent.log_events_bulk([{"actor_role": "NOT_A_ROLE"}])
        assert AuditEvent.objects.get().actor_role == AuditEvent.ActorRole.SYSTEM

    def test_actor_category_maps_role_for_all_users(self):
        operator = UserFactory.create_data_operator()
        reviewer = UserFactory.create_reviewer()
        consumer = UserFactory.create_data_consumer()
        AuditEvent.log_events_bulk([{"actor": operator}, {"actor": reviewer}, {"actor": consumer}])
        roles = set(AuditEvent.objects.values_list("actor_role", flat=True))
        assert roles == {
            AuditEvent.ActorRole.DATA_OPERATOR,
            AuditEvent.ActorRole.REVIEWER,
            AuditEvent.ActorRole.DATA_CONSUMER,
        }

    def test_actor_username_used_in_hash_input(self, monkeypatch):
        operator = UserFactory.create_data_operator(username="jack_operator")
        fixed_ts = timezone.now().replace(microsecond=0)
        monkeypatch.setattr("app.models.audit.timezone.now", lambda: fixed_ts)

        prev_hash = "0" * 64
        hash_input = (
            f"{prev_hash}|{fixed_ts.isoformat()}|EXCEPTION_CREATED|jack_operator||{{}}".encode()
        )
        expected = hashlib.sha256(hash_input).hexdigest()

        events = AuditEvent.log_events_bulk(
            [{"event_type": "EXCEPTION_CREATED", "actor": operator}]
        )
        assert events[0].event_hash == expected

    def test_batch_size_one_boundary(self):
        events = AuditEvent.log_events_bulk(
            [_event_data(loan_id=f"LG-{i}") for i in range(3)], batch_size=1
        )
        assert AuditEvent.objects.count() == 3
        assert events[1].prev_hash == events[0].event_hash

    def test_zero_batch_size_raises_value_error(self):
        with pytest.raises(ValueError):
            AuditEvent.log_events_bulk([_event_data()], batch_size=0)

    def test_negative_batch_size_raises_value_error(self):
        with pytest.raises(ValueError):
            AuditEvent.log_events_bulk([_event_data()], batch_size=-1)

    def test_large_bulk_crosses_default_batch_boundary(self):
        events = AuditEvent.log_events_bulk([_event_data(loan_id=f"LG-{i}") for i in range(600)])
        assert AuditEvent.objects.count() == 600
        assert events[500].prev_hash == events[499].event_hash
        assert events[599].prev_hash == events[598].event_hash

    def test_non_dict_element_raises_attribute_error(self):
        with pytest.raises(AttributeError):
            AuditEvent.log_events_bulk(["not-a-dict", {"event_type": "X"}])

    def test_non_empty_dict_passed_as_list_raises_attribute_error(self):
        with pytest.raises(AttributeError):
            AuditEvent.log_events_bulk({"event_type": "X"})
