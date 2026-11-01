from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Hosting(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    HOSTING_UNSPECIFIED: _ClassVar[Hosting]
    HOSTING_ENTITLED: _ClassVar[Hosting]
    HOSTING_ENDED: _ClassVar[Hosting]

class Publish(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    PUBLISH_UNSPECIFIED: _ClassVar[Publish]
    PUBLISH_ALLOWED: _ClassVar[Publish]
    PUBLISH_LIMIT_REACHED: _ClassVar[Publish]

class HostingReason(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    HOSTING_REASON_UNSPECIFIED: _ClassVar[HostingReason]
    HOSTING_REASON_TRIAL_ENDED: _ClassVar[HostingReason]
    HOSTING_REASON_GRACE_EXPIRED: _ClassVar[HostingReason]
    HOSTING_REASON_PLAN_CANCELED: _ClassVar[HostingReason]
    HOSTING_REASON_OVER_WEBSITE_LIMIT: _ClassVar[HostingReason]
    HOSTING_REASON_NOT_ADMITTED: _ClassVar[HostingReason]
HOSTING_UNSPECIFIED: Hosting
HOSTING_ENTITLED: Hosting
HOSTING_ENDED: Hosting
PUBLISH_UNSPECIFIED: Publish
PUBLISH_ALLOWED: Publish
PUBLISH_LIMIT_REACHED: Publish
HOSTING_REASON_UNSPECIFIED: HostingReason
HOSTING_REASON_TRIAL_ENDED: HostingReason
HOSTING_REASON_GRACE_EXPIRED: HostingReason
HOSTING_REASON_PLAN_CANCELED: HostingReason
HOSTING_REASON_OVER_WEBSITE_LIMIT: HostingReason
HOSTING_REASON_NOT_ADMITTED: HostingReason

class GetEntitlementRequest(_message.Message):
    __slots__ = ("project_id",)
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    project_id: str
    def __init__(self, project_id: _Optional[str] = ...) -> None: ...

class GetEntitlementResponse(_message.Message):
    __slots__ = ("project_id", "hosting", "publish", "reason", "plan_code", "publishes_used", "publishes_limit", "period_end")
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    HOSTING_FIELD_NUMBER: _ClassVar[int]
    PUBLISH_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    PLAN_CODE_FIELD_NUMBER: _ClassVar[int]
    PUBLISHES_USED_FIELD_NUMBER: _ClassVar[int]
    PUBLISHES_LIMIT_FIELD_NUMBER: _ClassVar[int]
    PERIOD_END_FIELD_NUMBER: _ClassVar[int]
    project_id: str
    hosting: Hosting
    publish: Publish
    reason: HostingReason
    plan_code: str
    publishes_used: int
    publishes_limit: int
    period_end: str
    def __init__(self, project_id: _Optional[str] = ..., hosting: _Optional[_Union[Hosting, str]] = ..., publish: _Optional[_Union[Publish, str]] = ..., reason: _Optional[_Union[HostingReason, str]] = ..., plan_code: _Optional[str] = ..., publishes_used: _Optional[int] = ..., publishes_limit: _Optional[int] = ..., period_end: _Optional[str] = ...) -> None: ...
