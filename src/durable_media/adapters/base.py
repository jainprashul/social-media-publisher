from dataclasses import dataclass
from typing import Protocol

class AdapterError(RuntimeError):
    error_class = 'adapter_error'
class TimeoutError(AdapterError): error_class='timeout'
class RateLimitError(AdapterError): error_class='rate_limit'
class ServerError(AdapterError): error_class='5xx'
class AuthenticationError(AdapterError): error_class='authentication'
class ValidationError(AdapterError): error_class='validation'
class MalformedResponseError(AdapterError): error_class='malformed_response'
class UnknownRemoteStateError(AdapterError): error_class='unknown_remote'

@dataclass(frozen=True)
class AdapterResult:
    external_id: str
    url: str
    verified: bool = True
    payload: dict | None = None

class Publisher(Protocol):
 def validate_target(self,target): ...
 def create_upload(self,job): ...
 def upload(self,remote,file_path): ...
 def wait_until_ready(self,remote): ...
 def publish(self,remote,job): ...
 def verify(self,result): ...
 def rollback_or_cleanup(self,remote): ...
