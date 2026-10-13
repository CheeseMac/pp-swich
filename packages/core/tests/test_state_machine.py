import pytest
from ppswitch_core.enums import CapabilityStatus
from ppswitch_core.errors import ApiError, ErrorCode
from ppswitch_core.state_machine import transition_capability


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (CapabilityStatus.DRAFT, CapabilityStatus.REVIEWED),
        (CapabilityStatus.REVIEWED, CapabilityStatus.VERIFIED),
        (CapabilityStatus.VERIFIED, CapabilityStatus.PUBLISHED),
        (CapabilityStatus.PUBLISHED, CapabilityStatus.DEPRECATED),
        (CapabilityStatus.PUBLISHED, CapabilityStatus.DISABLED),
        (CapabilityStatus.DEPRECATED, CapabilityStatus.DISABLED),
    ],
)
def test_allowed_transitions(current: CapabilityStatus, target: CapabilityStatus) -> None:
    assert transition_capability(current, target) == target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        # publishing without review/verification must fail (acceptance scenario 3)
        (CapabilityStatus.DRAFT, CapabilityStatus.PUBLISHED),
        (CapabilityStatus.REVIEWED, CapabilityStatus.PUBLISHED),
        # published versions never go back for in-place edits
        (CapabilityStatus.PUBLISHED, CapabilityStatus.DRAFT),
        (CapabilityStatus.PUBLISHED, CapabilityStatus.REVIEWED),
        (CapabilityStatus.DISABLED, CapabilityStatus.PUBLISHED),
    ],
)
def test_forbidden_transitions(current: CapabilityStatus, target: CapabilityStatus) -> None:
    with pytest.raises(ApiError) as excinfo:
        transition_capability(current, target)
    assert excinfo.value.http_status == 409
    assert excinfo.value.code in (ErrorCode.CONFLICT,)


def test_draft_cannot_be_published_directly_error_details() -> None:
    with pytest.raises(ApiError) as excinfo:
        transition_capability(CapabilityStatus.DRAFT, CapabilityStatus.PUBLISHED)
    assert excinfo.value.details["current"] == "draft"
    assert excinfo.value.details["target"] == "published"
