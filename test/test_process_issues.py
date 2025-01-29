#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2022, Arcangelo Massari <arcangelo.massari@unibo.it>
#
# Permission to use, copy, modify, and/or distribute this software for any purpose
# with or without fee is hereby granted, provided that the above copyright notice
# and this permission notice appear in all copies.
#
# THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
# REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND
# FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT, INDIRECT,
# OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE,
# DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS
# ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS
# SOFTWARE.

import os
import time
import unittest
from unittest.mock import MagicMock, patch
import json
import requests
from datetime import datetime
from dotenv import load_dotenv

from process_issues import (
    _validate_title,
    answer,
    get_open_issues,
    get_user_id,
    is_in_safe_list,
    process_open_issues,
    validate,
    get_data_to_store,
    _create_deposition_resource,
    _upload_data,
    deposit_on_zenodo,
)
from requests.exceptions import RequestException

load_dotenv()  # Carica le variabili dal file .env


class TestTitleValidation(unittest.TestCase):
    def test_valid_doi_title(self):
        """Test that a valid DOI title is accepted"""
        title = "deposit journal.com doi:10.1007/s42835-022-01029-y"
        is_valid, message = _validate_title(title)
        self.assertTrue(is_valid)
        self.assertEqual(message, "")

    def test_valid_isbn_title(self):
        """Test that a valid ISBN title is accepted"""
        title = "deposit publisher.com isbn:9780134093413"
        is_valid, message = _validate_title(title)
        self.assertTrue(is_valid)
        self.assertEqual(message, "")

    def test_missing_deposit_keyword(self):
        """Test that title without 'deposit' keyword is rejected"""
        title = "submit journal.com doi:10.1007/s42835-022-01029-y"
        is_valid, message = _validate_title(title)
        self.assertFalse(is_valid)
        self.assertIn("title of the issue was not structured correctly", message)

    def test_unsupported_identifier(self):
        """Test that unsupported identifier types are rejected"""
        title = "deposit journal.com arxiv:2203.01234"
        is_valid, message = _validate_title(title)
        self.assertFalse(is_valid)
        self.assertEqual(message, "The identifier schema 'arxiv' is not supported")

    def test_invalid_doi(self):
        """Test that invalid DOI format is rejected"""
        title = "deposit journal.com doi:invalid-doi-format"
        is_valid, message = _validate_title(title)
        self.assertFalse(is_valid)
        self.assertIn("is not a valid DOI", message)

    def test_malformed_title(self):
        """Test that malformed title structure is rejected"""
        title = "deposit doi:10.1007/s42835-022-01029-y"  # missing domain
        is_valid, message = _validate_title(title)
        self.assertFalse(is_valid)
        self.assertIn("title of the issue was not structured correctly", message)

    def test_unsupported_schema(self):
        """Test that an unsupported identifier schema returns appropriate error"""
        title = "deposit journal.com issn:1234-5678"  # issn is not in supported schemas
        is_valid, message = _validate_title(title)
        self.assertFalse(is_valid)
        print("message", message)
        self.assertEqual(message, "The identifier schema 'issn' is not supported")


class TestValidation(unittest.TestCase):
    def test_valid_issue(self):
        """Test that a valid issue with correct title and CSV data is accepted"""
        title = "deposit journal.com doi:10.1007/s42835-022-01029-y"
        body = """"id","title","author","pub_date","venue","volume","issue","page","type","publisher","editor"
"doi:10.1007/978-3-662-07918-8_3","Influence of Dielectric Properties, State, and Electrodes on Electric Strength","Ushakov, Vasily Y.","2004","Insulation of High-Voltage Equipment [isbn:9783642058530 isbn:9783662079188]","","","27-82","book chapter","Springer Science and Business Media LLC [crossref:297]",""
"doi:10.1016/0021-9991(73)90147-2","Flux-corrected transport. I. SHASTA, a fluid transport algorithm that works","Boris, Jay P; Book, David L","1973-1","Journal of Computational Physics [issn:0021-9991]","11","1","38-69","journal article","Elsevier BV [crossref:78]",""
===###===@@@===
"citing_id","cited_id"
"doi:10.1007/s42835-022-01029-y","doi:10.1007/978-3-662-07918-8_3"
"doi:10.1007/s42835-022-01029-y","doi:10.1016/0021-9991(73)90147-2\""""
        is_valid, message = validate(title, body)
        self.assertTrue(is_valid)
        self.assertIn("Thank you for your contribution", message)

    def test_invalid_separator(self):
        """Test that issue with incorrect separator is rejected"""
        title = "deposit journal.com doi:10.1007/s42835-022-01029-y"
        body = """"id","title","author","pub_date","venue","volume","issue","page","type","publisher","editor"
"doi:10.1007/978-3-662-07918-8_3","Test Title","Test Author","2004","Test Venue","1","1","1-10","journal article","Test Publisher",""
WRONG_SEPARATOR
"citing_id","cited_id"
"doi:10.1007/s42835-022-01029-y","doi:10.1007/978-3-662-07918-8_3\""""
        is_valid, message = validate(title, body)
        self.assertFalse(is_valid)
        self.assertIn("Please use the separator", message)

    def test_invalid_title_valid_body(self):
        """Test that issue with invalid title but valid body is rejected"""
        title = "invalid title format"
        body = """"id","title","author","pub_date","venue","volume","issue","page","type","publisher","editor"
"doi:10.1007/978-3-662-07918-8_3","Test Title","Test Author","2004","Test Venue","1","1","1-10","journal article","Test Publisher",""
===###===@@@===
"citing_id","cited_id"
"doi:10.1007/s42835-022-01029-y","doi:10.1007/978-3-662-07918-8_3\""""
        is_valid, message = validate(title, body)
        self.assertFalse(is_valid)
        self.assertIn("title of the issue was not structured correctly", message)

    def test_invalid_csv_structure(self):
        """Test that CSV with wrong column structure returns appropriate error"""
        title = "deposit journal.com doi:10.1007/s42835-022-01029-y"
        body = """"wrong","column","headers"
"data1","data2","data3"
===###===@@@===
"wrong","citation","headers"
"cite1","cite2","cite3"\""""
        is_valid, message = validate(title, body)
        self.assertFalse(is_valid)
        self.assertIn("could not be processed as a CSV", message)

    def test_get_data_to_store_valid_input(self):
        """Test get_data_to_store with valid input data"""
        title = "deposit journal.com doi:10.1234/test"
        body = """"id","title"
"1","Test Title"
===###===@@@===
"citing","cited"
"id1","id2"\""""
        created_at = "2024-01-01T00:00:00Z"
        had_primary_source = "https://github.com/test/1"
        user_id = 12345

        result = get_data_to_store(title, body, created_at, had_primary_source, user_id)

        self.assertEqual(result["data"]["title"], title)
        self.assertEqual(len(result["data"]["metadata"]), 1)
        self.assertEqual(len(result["data"]["citations"]), 1)
        self.assertEqual(result["provenance"]["generatedAtTime"], created_at)
        self.assertEqual(result["provenance"]["wasAttributedTo"], user_id)
        self.assertEqual(result["provenance"]["hadPrimarySource"], had_primary_source)

    def test_get_data_to_store_invalid_csv(self):
        """Test get_data_to_store with invalid CSV format"""
        title = "deposit journal.com doi:10.1234/test"
        # CSV con una sola sezione (manca il separatore)
        body = """"id","title"
"1","Test Title"\""""

        with self.assertRaises(ValueError) as context:
            get_data_to_store(
                title, body, "2024-01-01T00:00:00Z", "https://github.com/test/1", 12345
            )

        # Verifichiamo che l'errore contenga il messaggio corretto
        self.assertIn("Failed to process issue data", str(context.exception))

    def test_get_data_to_store_empty_sections(self):
        """Test get_data_to_store with empty metadata or citations sections"""
        title = "deposit journal.com doi:10.1234/test"
        body = """"id","title"
===###===@@@===
"citing","cited"\""""

        with self.assertRaises(ValueError) as context:
            get_data_to_store(
                title, body, "2024-01-01T00:00:00Z", "https://github.com/test/1", 12345
            )

        self.assertIn("Empty metadata or citations section", str(context.exception))

    def test_get_data_to_store_invalid_separator(self):
        """Test get_data_to_store with invalid separator in body"""
        title = "deposit journal.com doi:10.1234/test"
        body = """"id","title"
INVALID_SEPARATOR
"citing","cited"\""""

        with self.assertRaises(ValueError) as context:
            get_data_to_store(
                title, body, "2024-01-01T00:00:00Z", "https://github.com/test/1", 12345
            )

        self.assertIn("Failed to process issue data", str(context.exception))


class TestUserValidation(unittest.TestCase):
    def setUp(self):
        # Create a real safe list file with actual GitHub user IDs
        with open("safe_list.txt", "w") as f:
            # These are real GitHub user IDs
            f.write("3869247\n")  # The ID of essepuntato
            f.write("42008604\n")  # The ID of arcangelo7

    def tearDown(self):
        # Clean up the test file
        if os.path.exists("safe_list.txt"):
            os.remove("safe_list.txt")

    def test_get_user_id_real_user(self):
        """Test getting ID of a real GitHub user"""
        with patch.dict("os.environ", {"GH_TOKEN": os.environ.get("GH_TOKEN")}):
            user_id = get_user_id("arcangelo7")
            print("user_id", user_id)
            self.assertEqual(user_id, 42008604)

    def test_get_user_id_nonexistent_user(self):
        """Test getting ID of a nonexistent GitHub user"""
        user_id = get_user_id("this_user_definitely_does_not_exist_123456789")
        self.assertIsNone(user_id)

    def test_is_in_safe_list_allowed_user(self):
        """Test with a real allowed GitHub user ID"""
        self.assertTrue(is_in_safe_list(42008604))  # arcangelo7's ID

    def test_is_in_safe_list_not_allowed_user(self):
        """Test with a real but not allowed GitHub user ID"""
        self.assertFalse(is_in_safe_list(106336590))  # vbrandelero's ID

    def test_is_in_safe_list_nonexistent_user(self):
        """Test with a nonexistent user ID"""
        self.assertFalse(is_in_safe_list(999999999))

    @patch("requests.get")
    @patch("time.sleep")
    @patch("time.time")
    def test_get_user_id_rate_limit(self, mock_time, mock_sleep, mock_get):
        """Test rate limit handling in get_user_id"""
        # Mock current time
        current_time = 1000000
        mock_time.return_value = current_time

        # Setup responses
        rate_limited_response = MagicMock()
        rate_limited_response.status_code = 403
        rate_limited_response.headers = {
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": str(current_time + 30),  # Reset in 30 seconds
        }

        success_response = MagicMock()
        success_response.status_code = 200
        success_response.json.return_value = {"id": 12345}

        # First call hits rate limit, second call succeeds
        mock_get.side_effect = [rate_limited_response, success_response]

        with patch.dict("os.environ", {"GH_TOKEN": "fake-token"}):
            user_id = get_user_id("test-user")

        # Verify correct user ID was returned
        self.assertEqual(user_id, 12345)

        # Verify sleep was called with correct duration
        mock_sleep.assert_called_once_with(30)

        # Verify correct number of API calls
        self.assertEqual(mock_get.call_count, 2)

        # Verify API calls were correct
        for call in mock_get.call_args_list:
            args, kwargs = call
            self.assertEqual(args[0], "https://api.github.com/users/test-user")
            self.assertEqual(kwargs["headers"]["Authorization"], "Bearer fake-token")

    @patch("requests.get")
    @patch("time.sleep")  # Mock sleep to speed up test
    def test_get_user_id_connection_error_retry(self, mock_sleep, mock_get):
        """Test retry behavior when connection errors occur"""
        # Configure mock to fail with connection error twice then succeed
        mock_get.side_effect = [
            requests.ConnectionError,
            requests.ConnectionError,
            MagicMock(status_code=200, json=lambda: {"id": 12345}),
        ]

        user_id = get_user_id("test-user")

        self.assertEqual(user_id, 12345)
        self.assertEqual(mock_get.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)
        mock_sleep.assert_called_with(5)  # Verify sleep duration

    @patch("requests.get")
    @patch("time.sleep")
    def test_get_user_id_all_retries_fail(self, mock_sleep, mock_get):
        """Test behavior when all retry attempts fail"""
        # Configure mock to fail all three attempts
        mock_get.side_effect = [
            requests.ConnectionError,
            requests.ConnectionError,
            requests.ConnectionError,
        ]

        user_id = get_user_id("test-user")

        self.assertIsNone(user_id)
        self.assertEqual(mock_get.call_count, 3)
        self.assertEqual(
            mock_sleep.call_count, 3
        )  # Updated to expect 3 sleeps - one for each ConnectionError

    def test_is_in_safe_list_file_not_found(self):
        """Test behavior when safe_list.txt doesn't exist"""
        # Ensure the file doesn't exist
        if os.path.exists("safe_list.txt"):
            os.remove("safe_list.txt")

        # Test with any user ID - should return False when file is missing
        self.assertFalse(is_in_safe_list(42008604))

    @patch("requests.get")
    @patch("time.sleep")
    def test_get_user_id_timeout_retry(self, mock_sleep, mock_get):
        """Test retry behavior when requests timeout"""
        # Configure mock to timeout twice then succeed
        mock_get.side_effect = [
            requests.ReadTimeout,
            requests.ReadTimeout,
            MagicMock(status_code=200, json=lambda: {"id": 12345}),
        ]

        with patch.dict("os.environ", {"GH_TOKEN": "fake-token"}):
            user_id = get_user_id("test-user")

        # Verify correct user ID was returned after retries
        self.assertEqual(user_id, 12345)

        # Verify correct number of attempts
        self.assertEqual(mock_get.call_count, 3)

        # Verify no sleep was called (ReadTimeout doesn't trigger sleep)
        mock_sleep.assert_not_called()

        # Verify API calls were correct
        for call in mock_get.call_args_list:
            args, kwargs = call
            self.assertEqual(args[0], "https://api.github.com/users/test-user")
            self.assertEqual(kwargs["headers"]["Authorization"], "Bearer fake-token")


class TestGitHubAPI(unittest.TestCase):
    """Test GitHub API interaction functionality"""

    def setUp(self):
        self.mock_response = MagicMock()
        self.mock_response.status_code = 200

        # Sample issue data that won't change
        self.sample_issues = [
            {
                "title": "deposit journal.com doi:10.1234/test",
                "body": "test body",
                "number": 1,
                "user": {"login": "test-user"},
                "created_at": "2024-01-01T00:00:00Z",
                "html_url": "https://github.com/test/test/issues/1",
            }
        ]

    @patch("requests.get")
    def test_get_open_issues_success(self, mock_get):
        """Test successful retrieval of open issues"""
        self.mock_response.json.return_value = self.sample_issues
        mock_get.return_value = self.mock_response

        with patch.dict("os.environ", {"GH_TOKEN": "fake-token"}):
            issues = get_open_issues()

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["title"], "deposit journal.com doi:10.1234/test")
        self.assertEqual(issues[0]["number"], "1")

        # Verify API call
        mock_get.assert_called_once()
        args, kwargs = mock_get.call_args
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer fake-token")
        self.assertEqual(kwargs["params"]["labels"], "deposit")

    @patch("requests.get")
    def test_get_open_issues_404(self, mock_get):
        """Test handling of 404 response"""
        self.mock_response.status_code = 404
        mock_get.return_value = self.mock_response

        with patch.dict("os.environ", {"GH_TOKEN": "fake-token"}):
            issues = get_open_issues()

        self.assertEqual(issues, [])

    @patch("requests.get")
    @patch("time.sleep")
    @patch("time.time")
    def test_rate_limit_retry(self, mock_time, mock_sleep, mock_get):
        """Test retry behavior when hitting rate limits"""
        # Mock current time to have consistent test behavior
        current_time = 1000000
        mock_time.return_value = current_time

        # Setup mock responses
        rate_limited_response = MagicMock()
        rate_limited_response.status_code = 403
        rate_limited_response.headers = {
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": str(current_time + 30),  # Reset in 30 seconds
        }

        success_response = MagicMock()
        success_response.status_code = 200
        success_response.json.return_value = [
            {
                "title": "Test Issue",
                "body": "Test Body",
                "number": 1,
                "user": {"login": "test-user"},
                "created_at": "2024-01-01T00:00:00Z",
                "html_url": "https://github.com/test/1",
            }
        ]

        # First call hits rate limit, second call succeeds
        mock_get.side_effect = [rate_limited_response, success_response]

        with patch.dict("os.environ", {"GH_TOKEN": "fake-token"}):
            issues = get_open_issues()

        # Verify rate limit handling
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["title"], "Test Issue")

        # Verify sleep was called with exactly 30 seconds
        mock_sleep.assert_called_once_with(30)

        # Verify correct API calls
        self.assertEqual(mock_get.call_count, 2)
        for call in mock_get.call_args_list:
            args, kwargs = call
            self.assertEqual(kwargs["params"]["labels"], "deposit")
            self.assertEqual(kwargs["headers"]["Authorization"], "Bearer fake-token")

    @patch("requests.get")
    def test_network_error_retry(self, mock_get):
        """Test retry behavior on network errors"""
        mock_get.side_effect = RequestException("Network error")

        with patch.dict("os.environ", {"GH_TOKEN": "fake-token"}):
            with self.assertRaises(RuntimeError) as context:
                get_open_issues()

        self.assertIn("Failed to fetch issues after 3 attempts", str(context.exception))
        self.assertEqual(mock_get.call_count, 3)  # Verify 3 retry attempts

    @patch("requests.get")
    def test_get_open_issues_all_attempts_fail(self, mock_get):
        """Test that empty list is returned when all attempts fail without exception"""
        # Create response that fails but doesn't trigger retry logic
        failed_response = MagicMock()
        failed_response.status_code = 403
        # No rate limit headers, so won't trigger rate limit retry logic
        failed_response.headers = {}

        # Make all attempts return the same failed response
        mock_get.return_value = failed_response

        with patch.dict("os.environ", {"GH_TOKEN": "fake-token"}):
            issues = get_open_issues()

        # Verify empty list is returned
        self.assertEqual(issues, [])

        # Verify we tried MAX_RETRIES times
        self.assertEqual(mock_get.call_count, 3)

    @patch("requests.get")
    @patch("time.sleep")
    @patch("time.time")
    def test_rate_limit_already_expired(self, mock_time, mock_sleep, mock_get):
        """Test rate limit handling when reset time is in the past"""
        # Mock current time
        current_time = 1000000
        mock_time.return_value = current_time

        # Setup response with expired rate limit
        rate_limited_response = MagicMock()
        rate_limited_response.status_code = 403
        rate_limited_response.headers = {
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": str(current_time - 30),  # Reset time in the past
        }

        success_response = MagicMock()
        success_response.status_code = 200
        success_response.json.return_value = [
            {
                "title": "Test Issue",
                "body": "Test Body",
                "number": 1,
                "user": {"login": "test-user"},
                "created_at": "2024-01-01T00:00:00Z",
                "html_url": "https://github.com/test/1",
            }
        ]

        # First call hits expired rate limit, second call succeeds
        mock_get.side_effect = [rate_limited_response, success_response]

        with patch.dict("os.environ", {"GH_TOKEN": "fake-token"}):
            issues = get_open_issues()

        # Verify rate limit handling
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["title"], "Test Issue")

        # Verify sleep was NOT called since rate limit was already expired
        mock_sleep.assert_not_called()

        # Verify correct API calls
        self.assertEqual(mock_get.call_count, 2)


class TestAnswerFunction(unittest.TestCase):
    """Test the answer function that updates GitHub issues"""

    def setUp(self):
        """Set up test environment before each test"""
        self.base_url = (
            "https://api.github.com/repos/opencitations/crowdsourcing/issues"
        )
        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer fake-token",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self.issue_number = "123"

        # Setup environment variable
        self.env_patcher = patch.dict("os.environ", {"GH_TOKEN": "fake-token"})
        self.env_patcher.start()

    def tearDown(self):
        """Clean up after each test"""
        self.env_patcher.stop()

    @patch("requests.post")
    @patch("requests.patch")
    def test_answer_valid_authorized(self, mock_patch, mock_post):
        """Test answering a valid issue from authorized user"""
        # Setup mock responses
        mock_post.return_value.status_code = 201
        mock_patch.return_value.status_code = 200

        # Call function
        answer(
            is_valid=True,
            message="Thank you for your contribution!",
            issue_number=self.issue_number,
            is_authorized=True,
        )

        # Verify label API call
        mock_post.assert_any_call(
            f"{self.base_url}/{self.issue_number}/labels",
            headers=self.headers,
            json={"labels": ["to be processed"]},
            timeout=30,
        )

        # Verify comment API call
        mock_post.assert_any_call(
            f"{self.base_url}/{self.issue_number}/comments",
            headers=self.headers,
            json={"body": "Thank you for your contribution!"},
            timeout=30,
        )

        # Verify issue closure API call
        mock_patch.assert_called_once_with(
            f"{self.base_url}/{self.issue_number}",
            headers=self.headers,
            json={"state": "closed"},
            timeout=30,
        )

    @patch("requests.post")
    @patch("requests.patch")
    def test_answer_invalid_authorized(self, mock_patch, mock_post):
        """Test answering an invalid issue from authorized user"""
        answer(
            is_valid=False,
            message="Invalid format",
            issue_number=self.issue_number,
            is_authorized=True,
        )

        # Verify correct label was used
        mock_post.assert_any_call(
            f"{self.base_url}/{self.issue_number}/labels",
            headers=self.headers,
            json={"labels": ["invalid"]},
            timeout=30,
        )

    @patch("requests.post")
    @patch("requests.patch")
    def test_answer_unauthorized(self, mock_patch, mock_post):
        """Test answering an issue from unauthorized user"""
        answer(
            is_valid=False,
            message="Unauthorized user",
            issue_number=self.issue_number,
            is_authorized=False,
        )

        # Verify correct label was used
        mock_post.assert_any_call(
            f"{self.base_url}/{self.issue_number}/labels",
            headers=self.headers,
            json={"labels": ["rejected"]},
            timeout=30,
        )

    @patch("requests.post")
    def test_answer_label_error(self, mock_post):
        """Test handling of API error when adding label"""
        mock_post.side_effect = RequestException("Network error")

        with self.assertRaises(RequestException):
            answer(
                is_valid=True,
                message="Test message",
                issue_number=self.issue_number,
            )

    @patch("requests.post")
    @patch("requests.patch")
    def test_answer_comment_error(self, mock_patch, mock_post):
        """Test handling of API error when adding comment"""
        # First post (label) succeeds, second post (comment) fails
        mock_post.side_effect = [
            MagicMock(status_code=201),
            RequestException("Network error"),
        ]

        with self.assertRaises(RequestException):
            answer(
                is_valid=True,
                message="Test message",
                issue_number=self.issue_number,
            )

    @patch("requests.post")
    @patch("requests.patch")
    def test_answer_close_error(self, mock_patch, mock_post):
        """Test handling of API error when closing issue"""
        mock_post.return_value = MagicMock(status_code=201)
        mock_patch.side_effect = RequestException("Network error")

        with self.assertRaises(RequestException):
            answer(
                is_valid=True,
                message="Test message",
                issue_number=self.issue_number,
            )


class TestZenodoDeposit(unittest.TestCase):
    """Test Zenodo deposit functionality"""

    def setUp(self):
        """Set up test environment before each test"""
        self.env_patcher = patch.dict("os.environ", {"ZENODO": "fake-token"})
        self.env_patcher.start()

        self.test_data = [
            {
                "data": {
                    "title": "test deposit",
                    "metadata": [{"id": "1", "title": "Test"}],
                    "citations": [{"citing": "1", "cited": "2"}],
                },
                "provenance": {
                    "generatedAtTime": "2024-01-01T00:00:00Z",
                    "wasAttributedTo": 12345,
                    "hadPrimarySource": "https://github.com/test/1",
                },
            }
        ]

    def tearDown(self):
        """Clean up after each test"""
        self.env_patcher.stop()
        if os.path.exists("data_to_store.json"):
            os.remove("data_to_store.json")

    @patch("requests.post")
    def test_create_deposition_resource(self, mock_post):
        """Test creation of Zenodo deposition resource"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "id": "12345",
            "links": {"bucket": "https://zenodo.org/api/bucket/12345"},
        }
        mock_post.return_value = mock_response

        deposition_id, bucket = _create_deposition_resource("2024-01-01")

        self.assertEqual(deposition_id, "12345")
        self.assertEqual(bucket, "https://zenodo.org/api/bucket/12345")

        # Verify API call
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args

        self.assertEqual(kwargs["params"], {"access_token": "fake-token"})
        self.assertEqual(kwargs["headers"], {"Content-Type": "application/json"})
        self.assertEqual(kwargs["timeout"], 30)

        # Verify metadata
        metadata = kwargs["json"]["metadata"]
        self.assertEqual(metadata["upload_type"], "dataset")
        self.assertEqual(metadata["publication_date"], "2024-01-01")
        self.assertIn("OpenCitations crowdsourcing", metadata["title"])

    @patch("requests.put")
    def test_upload_data(self, mock_put):
        """Test uploading data file to Zenodo"""
        mock_put.return_value.status_code = 200

        # Create test file
        with open("data_to_store.json", "w") as f:
            json.dump({"test": "data"}, f)

        _upload_data("2024-01-01", "https://zenodo.org/api/bucket/12345")

        # Verify API call
        mock_put.assert_called_once()
        args, kwargs = mock_put.call_args

        self.assertEqual(
            args[0],
            "https://zenodo.org/api/bucket/12345/2024-01-01_weekly_deposit.json",
        )
        self.assertEqual(kwargs["params"], {"access_token": "fake-token"})
        self.assertEqual(kwargs["timeout"], 30)

    @patch("process_issues._create_deposition_resource")
    @patch("process_issues._upload_data")
    @patch("requests.post")
    def test_deposit_on_zenodo(self, mock_post, mock_upload, mock_create):
        """Test full Zenodo deposit process"""
        # Setup mocks
        mock_create.return_value = ("12345", "https://zenodo.org/api/bucket/12345")
        mock_post.return_value.status_code = 200

        deposit_on_zenodo(self.test_data)

        # Verify API calls order and parameters
        mock_create.assert_called_once_with(datetime.now().strftime("%Y-%m-%d"))
        mock_upload.assert_called_once_with(
            datetime.now().strftime("%Y-%m-%d"), "https://zenodo.org/api/bucket/12345"
        )

        # Verify publish request
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(
            args[0],
            "https://zenodo.org/api/deposit/depositions/12345/actions/publish",
        )
        self.assertEqual(kwargs["params"], {"access_token": "fake-token"})
        self.assertEqual(kwargs["timeout"], 30)

        # Verify cleanup happened
        self.assertFalse(os.path.exists("data_to_store.json"))

    @patch("requests.post")
    def test_create_deposition_resource_error(self, mock_post):
        """Test error handling in deposition creation"""
        mock_post.side_effect = requests.RequestException("API Error")

        with self.assertRaises(requests.RequestException):
            _create_deposition_resource("2024-01-01")

    @patch("requests.put")
    def test_upload_data_error(self, mock_put):
        """Test error handling in data upload"""
        mock_put.side_effect = requests.RequestException("Upload Error")

        with open("data_to_store.json", "w") as f:
            json.dump({"test": "data"}, f)

        with self.assertRaises(requests.RequestException):
            _upload_data("2024-01-01", "https://zenodo.org/api/bucket/12345")

    @patch("process_issues._create_deposition_resource")
    def test_deposit_on_zenodo_create_error(self, mock_create):
        """Test error handling in full deposit process - creation error"""
        mock_create.side_effect = requests.RequestException("Creation Error")

        with self.assertRaises(requests.RequestException):
            deposit_on_zenodo(self.test_data)

        # Verify cleanup happened
        self.assertFalse(os.path.exists("data_to_store.json"))


class TestProcessOpenIssues(unittest.TestCase):
    """Test the main process_open_issues function"""

    def setUp(self):
        """Set up test environment"""
        self.env_patcher = patch.dict(
            "os.environ", {"GH_TOKEN": "fake-gh-token", "ZENODO": "fake-zenodo-token"}
        )
        self.env_patcher.start()

        # Sample issue data with properly formatted CSV and valid DOI
        self.sample_issue = {
            "title": "deposit journal.com doi:10.1007/s42835-022-01029-y",
            "body": """"id","title","author","pub_date","venue","volume","issue","page","type","publisher","editor"
"doi:10.1007/s42835-022-01029-y","Test Title","Test Author","2024","Test Journal","1","1","1-10","journal article","Test Publisher",""
===###===@@@===
"citing_id","cited_id"
"doi:10.1007/s42835-022-01029-y","doi:10.1007/978-3-030-00668-6_8\"""",
            "number": "1",
            "author": {"login": "test-user"},
            "createdAt": "2024-01-01T00:00:00Z",
            "url": "https://github.com/test/1",
        }

    def tearDown(self):
        """Clean up after each test"""
        self.env_patcher.stop()

    @patch("process_issues.get_open_issues")
    @patch("process_issues.get_user_id")
    @patch("process_issues.is_in_safe_list")
    @patch("process_issues.deposit_on_zenodo")
    @patch("process_issues.answer")
    def test_process_valid_authorized_issue(
        self, mock_answer, mock_deposit, mock_safe_list, mock_user_id, mock_get_issues
    ):
        """Test processing a valid issue from authorized user"""
        # Setup mocks
        mock_get_issues.return_value = [self.sample_issue]
        mock_user_id.return_value = 12345
        mock_safe_list.return_value = True

        # Run function
        process_open_issues()

        # Verify user validation
        mock_user_id.assert_called_once_with("test-user")
        mock_safe_list.assert_called_once_with(12345)

        # Verify issue was processed
        mock_answer.assert_called_once()
        args, kwargs = mock_answer.call_args
        self.assertTrue(args[0])  # is_valid
        self.assertIn("Thank you", args[1])  # message
        self.assertEqual(args[2], "1")  # issue_number
        self.assertTrue(kwargs["is_authorized"])

        # Verify data was deposited
        mock_deposit.assert_called_once()
        args, kwargs = mock_deposit.call_args
        deposited_data = args[0][0]
        self.assertEqual(deposited_data["data"]["title"], self.sample_issue["title"])
        self.assertEqual(deposited_data["provenance"]["wasAttributedTo"], 12345)

    @patch("process_issues.get_open_issues")
    @patch("process_issues.get_user_id")
    @patch("process_issues.is_in_safe_list")
    @patch("process_issues.deposit_on_zenodo")
    @patch("process_issues.answer")
    def test_process_unauthorized_user(
        self, mock_answer, mock_deposit, mock_safe_list, mock_user_id, mock_get_issues
    ):
        """Test processing an issue from unauthorized user"""
        # Setup mocks
        mock_get_issues.return_value = [self.sample_issue]
        mock_user_id.return_value = 12345
        mock_safe_list.return_value = False

        # Run function
        process_open_issues()

        # Verify user was checked but not authorized
        mock_user_id.assert_called_once_with("test-user")
        mock_safe_list.assert_called_once_with(12345)

        # Verify appropriate response
        mock_answer.assert_called_once()
        args, kwargs = mock_answer.call_args
        self.assertFalse(args[0])  # is_valid
        self.assertIn("register as a trusted user", args[1])  # message
        self.assertEqual(args[2], "1")  # issue_number
        self.assertFalse(kwargs["is_authorized"])

        # Verify no deposit was made
        mock_deposit.assert_not_called()

    @patch("process_issues.get_open_issues")
    @patch("process_issues.get_user_id")
    @patch("process_issues.is_in_safe_list")
    @patch("process_issues.deposit_on_zenodo")
    @patch("process_issues.answer")
    def test_process_invalid_issue(
        self, mock_answer, mock_deposit, mock_safe_list, mock_user_id, mock_get_issues
    ):
        """Test processing an invalid issue from authorized user"""
        # Create invalid issue (wrong format)
        invalid_issue = self.sample_issue.copy()
        invalid_issue["body"] = "Invalid body without separator"

        # Setup mocks
        mock_get_issues.return_value = [invalid_issue]
        mock_user_id.return_value = 12345
        mock_safe_list.return_value = True

        # Run function
        process_open_issues()

        # Verify response for invalid issue
        mock_answer.assert_called_once()
        args, kwargs = mock_answer.call_args
        self.assertFalse(args[0])  # is_valid
        self.assertIn("separator", args[1])  # message
        self.assertEqual(args[2], "1")  # issue_number
        self.assertTrue(kwargs["is_authorized"])

        # Verify no deposit was made
        mock_deposit.assert_not_called()

    @patch("process_issues.get_open_issues")
    def test_process_no_issues(self, mock_get_issues):
        """Test processing when no issues are present"""
        mock_get_issues.return_value = []

        process_open_issues()
        mock_get_issues.assert_called_once()

    @patch("process_issues.get_open_issues")
    def test_process_error_handling(self, mock_get_issues):
        """Test error handling in process_open_issues"""
        mock_get_issues.side_effect = Exception("Test error")

        with self.assertRaises(Exception) as context:
            process_open_issues()

        self.assertEqual(str(context.exception), "Test error")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
