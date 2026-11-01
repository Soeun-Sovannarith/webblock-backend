from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class GetRepositoryRequest(_message.Message):
    __slots__ = ("project_id",)
    PROJECT_ID_FIELD_NUMBER: _ClassVar[int]
    project_id: str
    def __init__(self, project_id: _Optional[str] = ...) -> None: ...

class GetRepositoryResponse(_message.Message):
    __slots__ = ("repository_id", "clone_url", "full_name", "subdomain")
    REPOSITORY_ID_FIELD_NUMBER: _ClassVar[int]
    CLONE_URL_FIELD_NUMBER: _ClassVar[int]
    FULL_NAME_FIELD_NUMBER: _ClassVar[int]
    SUBDOMAIN_FIELD_NUMBER: _ClassVar[int]
    repository_id: int
    clone_url: str
    full_name: str
    subdomain: str
    def __init__(self, repository_id: _Optional[int] = ..., clone_url: _Optional[str] = ..., full_name: _Optional[str] = ..., subdomain: _Optional[str] = ...) -> None: ...
