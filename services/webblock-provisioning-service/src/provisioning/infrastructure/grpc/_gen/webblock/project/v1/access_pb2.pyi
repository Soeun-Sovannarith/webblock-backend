from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class CheckAccessRequest(_message.Message):
    __slots__ = ("user_id", "project_id", "action")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    ACTION_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    project_id: str
    action: str
    def __init__(self, user_id: _Optional[str] = ..., project_id: _Optional[str] = ..., action: _Optional[str] = ...) -> None: ...

class CheckAccessResponse(_message.Message):
    __slots__ = ("allowed", "reason")
    ALLOWED_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    allowed: bool
    reason: str
    def __init__(self, allowed: _Optional[bool] = ..., reason: _Optional[str] = ...) -> None: ...
