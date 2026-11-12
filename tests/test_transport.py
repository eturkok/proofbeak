import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import MagicMock, Mock

from proofbeak.adapters.base import ProviderError
from proofbeak.adapters.http import Transport


class TransportTests(unittest.TestCase):
    def transport(self, error):
        transport = Transport()
        transport.opener = Mock()
        transport.opener.open.side_effect = error
        return transport

    def test_permission_and_rate_limit_reason_codes(self):
        for status, reason in [(403, "permission_denied"), (429, "rate_limited"),
                               (412, "concurrent_modification")]:
            transport = self.transport(HTTPError("https://example.test", status, "secret", {}, None))
            with self.assertRaises(ProviderError) as raised:
                transport.request("GET", "https://example.test")
            self.assertEqual(raised.exception.reason, reason)
            self.assertNotIn("secret", str(raised.exception))

    def test_write_timeout_is_ambiguous_without_retry(self):
        transport = self.transport(URLError("secret-token-in-error"))
        with self.assertRaises(ProviderError) as raised:
            transport.request("PUT", "https://example.test")
        self.assertEqual(raised.exception.reason, "write_outcome_unknown")
        self.assertEqual(transport.opener.open.call_count, 1)

    def test_read_timeout_is_an_observation_gap(self):
        transport = self.transport(URLError("network unavailable"))
        with self.assertRaises(ProviderError) as raised:
            transport.request("REPORT", "https://example.test")
        self.assertEqual(raised.exception.reason, "observation_unavailable")

    def test_request_budget_stops_before_network(self):
        transport = Transport(max_calls=0)
        transport.opener = Mock()
        with self.assertRaises(ProviderError) as raised:
            transport.request("GET", "https://example.test")
        self.assertEqual(raised.exception.reason, "request_budget_exceeded")
        transport.opener.open.assert_not_called()

    def test_response_size_is_bounded(self):
        transport = Transport(max_bytes=4)
        transport.opener = MagicMock()
        response = transport.opener.open.return_value.__enter__.return_value
        response.read.return_value = b"12345"
        with self.assertRaises(ProviderError) as raised:
            transport.request("GET", "https://example.test")
        self.assertEqual(raised.exception.reason, "response_too_large")
        response.read.assert_called_once_with(5)

    def test_total_attempts_survive_cleanup_budget_reset_and_count_failed_io(self):
        transport = self.transport(URLError("offline"))
        for _ in range(3):
            with self.assertRaises(ProviderError):
                transport.request("GET", "https://example.test")
        transport.calls = 0
        with self.assertRaises(ProviderError):
            transport.request("DELETE", "https://example.test")
        self.assertEqual(transport.calls, 1)
        self.assertEqual(transport.total_calls, 4)
        transport.max_calls = 1
        with self.assertRaises(ProviderError):
            transport.request("GET", "https://example.test")
        self.assertEqual(transport.total_calls, 4)


if __name__ == "__main__":
    unittest.main()
