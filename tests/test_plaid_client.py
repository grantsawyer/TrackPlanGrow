"""PlaidGateway._call: network failures become PlaidError (so sync_all carries on), with one retry for a dropped
connection. No network: the "method" is a stand-in for an SDK endpoint."""

import pytest
import urllib3

from fintrack.plaid_client import PlaidError, PlaidGateway


class Response:
    def to_dict(self):
        return {"ok": True}


def endpoint(*failures):
    """An SDK endpoint stand-in that raises each of `failures` in turn, then answers."""
    calls = []

    def method(request):
        calls.append(request)
        if len(calls) <= len(failures):
            raise failures[len(calls) - 1]
        return Response()
    return method, calls


def gateway():
    return PlaidGateway.__new__(PlaidGateway)          # no settings or SDK client needed for _call


def dropped():
    return urllib3.exceptions.ProtocolError("Connection aborted.", ConnectionResetError())


def test_dropped_connection_is_retried_once():
    method, calls = endpoint(dropped())
    assert gateway()._call(method, "req") == {"ok": True}
    assert len(calls) == 2


def test_dropped_twice_is_a_network_plaid_error():
    method, calls = endpoint(dropped(), dropped())
    with pytest.raises(PlaidError) as err:
        gateway()._call(method, "req")
    assert err.value.code == "NETWORK_ERROR" and len(calls) == 2


def test_unreachable_plaid_is_a_network_plaid_error_without_retry():
    method, calls = endpoint(urllib3.exceptions.MaxRetryError(None, "/accounts/get", "name resolution failed"))
    with pytest.raises(PlaidError) as err:
        gateway()._call(method, "req")
    assert err.value.code == "NETWORK_ERROR" and len(calls) == 1
