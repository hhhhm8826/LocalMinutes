import json
import httpx
import pytest
from google.genai.errors import APIError
from meeting_minutes.ai_common import AIFailure, safe_diagnostic
from meeting_minutes.gemini_failure import failure

SECRET = 'FAKE-KEY-and-private-transcript'


@pytest.mark.parametrize('status,code', [(400,'AI_BAD_REQUEST'), (429,'AI_RATE_LIMIT'),
    (500,'AI_SERVER_ERROR'), (503,'AI_SERVICE_UNAVAILABLE')])
def test_http_diagnostics_drop_upstream_content(status,code):
    response=httpx.Response(status,headers={'retry-after':'7','x-secret':SECRET})
    error=APIError(status,{'error':{'message':SECRET,'status':'UNAVAILABLE','details':[
        {'@type':'type.googleapis.com/google.rpc.RetryInfo','retryDelay':'8.5s'},
        {'@type':'private','value':SECRET}]}},response)
    result=failure(error)
    assert result.code==code
    assert result.diagnostic=={'category':'http_api','http_status':status,'provider_status':'UNAVAILABLE','retry_after_seconds':9}
    assert SECRET not in str(result)+repr(result)+json.dumps(result.diagnostic)


@pytest.mark.parametrize('cls,category,code',[(httpx.ConnectTimeout,'connect_timeout','AI_TIMEOUT'),
 (httpx.ReadTimeout,'read_timeout','AI_TIMEOUT'), (httpx.ConnectError,'connect_error','AI_NETWORK'),
 (httpx.RemoteProtocolError,'protocol_error','AI_NETWORK')])
def test_transport_kind_without_exception_text(cls,category,code):
    result=failure(cls(SECRET))
    assert result.code==code and result.diagnostic=={'category':category}
    assert SECRET not in str(result)+json.dumps(result.diagnostic)


def test_untrusted_diagnostic_values_never_persist():
    value={'category':[SECRET],'provider_status':{'secret':SECRET},'http_status':True,
           'retry_after_seconds':999999,'message':SECRET,'headers':{'authorization':SECRET}}
    assert safe_diagnostic(value)=={}
    assert AIFailure('AI_NETWORK',value).diagnostic=={}
    result=failure(APIError(503,{'error':{'message':SECRET,'status':SECRET,'details':None}}))
    assert result.diagnostic=={'category':'http_api','http_status':503}
