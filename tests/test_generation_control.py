import pytest

from services.generation_control import (
    GenerationCancelled,
    cancel_generation,
    register_generation,
    unregister_generation,
)


def test_generation_cancel_is_bound_to_thread_and_user():
    control = register_generation("request_1234567890", "guest_cancel_test", None)
    assert control is not None
    try:
        assert cancel_generation("request_1234567890", "other_thread", None) is False
        assert cancel_generation("request_1234567890", "guest_cancel_test", 7) is False
        assert cancel_generation("request_1234567890", "guest_cancel_test", None) is True
        with pytest.raises(GenerationCancelled):
            control.check()
    finally:
        unregister_generation("request_1234567890", control)


def test_invalid_request_id_is_not_registered():
    assert register_generation("too-short", "guest_cancel_test", None) is None
    assert cancel_generation("too-short", "guest_cancel_test", None) is False
