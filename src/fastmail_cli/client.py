"""JMAP API client for Fastmail."""

import json
import logging
import time
import urllib.request
from dataclasses import dataclass
from enum import Enum
from functools import wraps
from typing import Any, Optional, Union

from fastmail_cli.exceptions import AuthenticationError, JMAPError, NotFoundError, ValidationError

logger = logging.getLogger(__name__)


class MailboxRole(str, Enum):
    """Standard JMAP mailbox roles."""
    INBOX = "inbox"
    SENT = "sent"
    TRASH = "trash"
    DRAFTS = "drafts"
    ARCHIVE = "archive"
    SPAM = "spam"
    TEMPLATES = "templates"


@dataclass
class JMAPSession:
    """Represents a JMAP session."""
    username: str
    api_url: str
    download_url: str
    upload_url: str
    event_source_url: str
    account_id: str
    primary_mailbox_id: Optional[str] = None


def retry_on_failure(max_retries: int = 3, delay: float = 1.0):
    """Decorator to retry on transient failures."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (urllib.error.URLError, TimeoutError) as e:
                    last_exception = e
                    if attempt == max_retries - 1:
                        raise last_exception
                    wait_time = delay * (2 ** attempt)
                    logger.warning(f"Request failed (attempt {attempt + 1}/{max_retries}), retrying in {wait_time}s: {e}")
                    time.sleep(wait_time)
            raise last_exception  # pragma: no cover
        return wrapper
    return decorator


class FastmailJMAPClient:
    """Client for interacting with Fastmail's JMAP API."""

    JMAP_SESSION_URL = "https://api.fastmail.com/jmap/session"

    def __init__(self, api_token: str, timeout: int = 30):
        """
        Initialize the client with an API token.

        Args:
            api_token: A Fastmail API token
            timeout: Request timeout in seconds (default: 30)
        """
        self.api_token = api_token
        self.timeout = timeout
        self.session: Optional[JMAPSession] = None
        self._auth_header = f"Bearer {api_token}"

    def __enter__(self):
        """Context manager entry - authenticate on enter."""
        self.authenticate()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - cleanup if needed."""
        pass

    @retry_on_failure(max_retries=3, delay=1.0)
    def _make_request(
        self,
        url: str,
        method: str = "GET",
        data: Optional[dict] = None,
        headers: Optional[dict] = None
    ) -> dict:
        """Make an HTTP request to the JMAP API."""
        default_headers = {
            "Authorization": self._auth_header,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "fastmail-cli/0.1.0",
        }

        if headers:
            default_headers.update(headers)

        request = urllib.request.Request(
            url,
            data=json.dumps(data).encode() if data else None,
            headers=default_headers,
            method=method
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                response_body = response.read().decode()
                final_url = response.geturl()
                if final_url != url and ("fastmail.com" in final_url and "/.well-known/" not in final_url):
                    raise AuthenticationError(
                        "Authentication failed - received redirect to Fastmail web interface. "
                        "Please check your API token."
                    )
                if not response_body:
                    status = response.getcode()
                    raise JMAPError(
                        f"Server returned empty response (HTTP {status}). "
                        "This may indicate an authentication or connectivity issue."
                    )
                try:
                    parsed_response = json.loads(response_body)
                except json.JSONDecodeError as e:
                    raise JMAPError(f"Invalid JSON response: {e}")

                if not isinstance(parsed_response, dict):
                    raise JMAPError("Invalid response format: expected JSON object")

                return parsed_response

        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise AuthenticationError("Authentication failed. Please check your API token.")
            if e.code == 403:
                error_body = e.read().decode()
                raise JMAPError(
                    f"HTTP Error 403 (Forbidden): Access blocked.\n"
                    f"Response body: {error_body[:500]}"
                )
            if e.code == 429:
                raise JMAPError("HTTP Error 429 (Rate Limited): Too many requests.")
            error_body = e.read().decode()
            raise JMAPError(f"HTTP Error {e.code}: {error_body}")
        except urllib.error.URLError as e:
            raise JMAPError(f"Connection error: {e.reason}")
        except TimeoutError:
            raise JMAPError("Request timed out. Please check your internet connection.")

    def authenticate(self) -> JMAPSession:
        """Authenticate and get JMAP session information."""
        response = self._make_request(self.JMAP_SESSION_URL)

        accounts = response.get("accounts", {})
        if not accounts:
            raise JMAPError("No accounts found in JMAP session")

        account_id = list(accounts.keys())[0]

        api_url = response.get("apiUrl")
        download_url = response.get("downloadUrl")
        upload_url = response.get("uploadUrl")
        event_source_url = response.get("eventSourceUrl")

        if not api_url:
            raise JMAPError("Missing apiUrl in JMAP session response")

        session = JMAPSession(
            username=response.get("username", "unknown"),
            api_url=api_url,
            download_url=download_url or "",
            upload_url=upload_url or "",
            event_source_url=event_source_url or "",
            account_id=account_id,
        )

        # Try to get the primary mailbox
        try:
            mailboxes = self._list_mailboxes_raw(session)
            for mailbox in mailboxes:
                if mailbox.get("role") == MailboxRole.INBOX:
                    session.primary_mailbox_id = mailbox["id"]
                    break
        except JMAPError as e:
            logger.warning(f"Could not get primary mailbox: {e}")

        self.session = session
        return session

    def _list_mailboxes_raw(self, session: Optional[JMAPSession] = None) -> list[dict]:
        """Internal method to get mailboxes list."""
        session = session or self.session
        if not session:
            raise JMAPError("Not authenticated. Call authenticate() first.")

        request_data = {
            "using": ["urn:ietf:params:jmap:core", "urn:ietf:params:jmap:mail"],
            "methodCalls": [
                [
                    "Mailbox/get",
                    {
                        "accountId": session.account_id,
                        "properties": None
                    },
                    "0"
                ]
            ]
        }

        response = self._make_request(session.api_url, method="POST", data=request_data)

        method_responses = response.get("methodResponses", [])
        for method, result, tag in method_responses:
            if method == "Mailbox/get":
                return result.get("list", [])
            elif method == "error":
                raise JMAPError(f"API Error: {result}")

        return []

    def list_mailboxes(self) -> list[dict]:
        """List all mailboxes (folders) in the account."""
        if not self.session:
            self.authenticate()
        return self._list_mailboxes_raw()

    def get_mailbox_by_name(self, name: str) -> dict:
        """Find a mailbox by name (case-insensitive)."""
        mailboxes = self.list_mailboxes()
        name_lower = name.lower()
        for mailbox in mailboxes:
            if mailbox["name"].lower() == name_lower:
                return mailbox
        raise NotFoundError(f"Mailbox '{name}' not found")

    def get_mailbox_by_role(self, role: MailboxRole) -> Optional[dict]:
        """Find a mailbox by role."""
        mailboxes = self.list_mailboxes()
        for mailbox in mailboxes:
            if mailbox.get("role") == role:
                return mailbox
        return None

    def _validate_limit(self, limit: int) -> None:
        """Validate limit parameter."""
        if not isinstance(limit, int):
            raise ValidationError(f"limit must be an integer, got {type(limit).__name__}")
        if limit < 1:
            raise ValidationError(f"limit must be at least 1, got {limit}")
        if limit > 1000:
            raise ValidationError(f"limit cannot exceed 1000, got {limit}")

    def _validate_email_id(self, email_id: str) -> None:
        """Validate email ID format."""
        if not email_id or not isinstance(email_id, str):
            raise ValidationError("email_id must be a non-empty string")
        if not email_id.strip():
            raise ValidationError("email_id cannot be whitespace only")

    def _build_filter(
        self,
        text: Optional[str] = None,
        from_addr: Optional[str] = None,
        to_addr: Optional[str] = None,
        subject: Optional[str] = None,
        mailbox_id: Optional[str] = None,
        has_attachment: bool = False,
        min_size: Optional[int] = None,
        max_size: Optional[int] = None,
        after: Optional[str] = None,
        before: Optional[str] = None,
        unread_only: bool = False,
        flagged_only: bool = False
    ) -> Optional[dict]:
        """Build JMAP filter criteria."""
        conditions: list[dict] = []

        if mailbox_id:
            conditions.append({"inMailbox": mailbox_id})

        if text:
            conditions.append({"text": text})

        if from_addr:
            conditions.append({"from": from_addr})

        if to_addr:
            conditions.append({"to": to_addr})

        if subject:
            conditions.append({"subject": subject})

        if has_attachment:
            conditions.append({"hasAttachment": True})

        if min_size is not None:
            conditions.append({"minSize": min_size})

        if max_size is not None:
            conditions.append({"maxSize": max_size})

        if after:
            conditions.append({"after": after})

        if before:
            conditions.append({"before": before})

        if unread_only:
            conditions.append({"operator": "NOT", "conditions": [{"hasKeyword": "$seen"}]})

        if flagged_only:
            conditions.append({"hasKeyword": "$flagged"})

        if len(conditions) == 0:
            return None
        elif len(conditions) == 1:
            return conditions[0]
        else:
            return {"operator": "AND", "conditions": conditions}

    def query_emails(
        self,
        mailbox_id: Optional[str] = None,
        text: Optional[str] = None,
        from_addr: Optional[str] = None,
        to_addr: Optional[str] = None,
        subject: Optional[str] = None,
        has_attachment: bool = False,
        min_size: Optional[int] = None,
        max_size: Optional[int] = None,
        after: Optional[str] = None,
        before: Optional[str] = None,
        unread_only: bool = False,
        flagged_only: bool = False,
        limit: int = 50,
        offset: int = 0,
        fetch_body: bool = False
    ) -> list[dict]:
        """Query emails with various filters."""
        self._validate_limit(limit)

        if not self.session:
            self.authenticate()

        session = self.session
        assert session is not None

        # Build filter criteria
        filter_criteria = self._build_filter(
            text=text,
            from_addr=from_addr,
            to_addr=to_addr,
            subject=subject,
            mailbox_id=mailbox_id,
            has_attachment=has_attachment,
            min_size=min_size,
            max_size=max_size,
            after=after,
            before=before,
            unread_only=unread_only,
            flagged_only=flagged_only
        )

        properties = [
            "id",
            "blobId",
            "threadId",
            "mailboxIds",
            "keywords",
            "size",
            "receivedAt",
            "from",
            "to",
            "subject",
            "preview",
            "hasAttachment"
        ]

        if fetch_body:
            properties.extend(["bodyValues", "textBody", "htmlBody", "attachments"])

        query_request: dict = {
            "using": ["urn:ietf:params:jmap:core", "urn:ietf:params:jmap:mail"],
            "methodCalls": [
                [
                    "Email/query",
                    {
                        "accountId": session.account_id,
                        "sort": [{"property": "receivedAt", "isAscending": False}],
                        "limit": limit,
                        "position": offset
                    },
                    "0"
                ],
                [
                    "Email/get",
                    {
                        "accountId": session.account_id,
                        "properties": properties,
                        "fetchAllBodyValues": fetch_body,
                        "#ids": {
                            "resultOf": "0",
                            "name": "Email/query",
                            "path": "/ids"
                        }
                    },
                    "1"
                ]
            ]
        }

        if filter_criteria:
            query_request["methodCalls"][0][1]["filter"] = filter_criteria

        response = self._make_request(session.api_url, method="POST", data=query_request)

        method_responses = response.get("methodResponses", [])

        for method, result, tag in method_responses:
            if method == "Email/get":
                return result.get("list", [])
            elif method == "error":
                raise JMAPError(f"API Error: {result}")

        return []

    def get_email(self, email_id: str) -> dict:
        """Get a specific email by ID with full details."""
        self._validate_email_id(email_id)

        if not self.session:
            self.authenticate()

        session = self.session
        assert session is not None

        get_request = {
            "using": ["urn:ietf:params:jmap:core", "urn:ietf:params:jmap:mail"],
            "methodCalls": [
                [
                    "Email/get",
                    {
                        "accountId": session.account_id,
                        "ids": [email_id],
                        "properties": [
                            "id",
                            "blobId",
                            "threadId",
                            "mailboxIds",
                            "keywords",
                            "size",
                            "receivedAt",
                            "messageId",
                            "inReplyTo",
                            "references",
                            "sender",
                            "from",
                            "to",
                            "cc",
                            "bcc",
                            "replyTo",
                            "subject",
                            "sentAt",
                            "hasAttachment",
                            "preview",
                            "bodyValues",
                            "textBody",
                            "htmlBody",
                            "attachments"
                        ],
                        "fetchAllBodyValues": True
                    },
                    "0"
                ]
            ]
        }

        response = self._make_request(session.api_url, method="POST", data=get_request)

        method_responses = response.get("methodResponses", [])

        for method, result, tag in method_responses:
            if method == "Email/get":
                emails = result.get("list", [])
                if not emails:
                    raise NotFoundError(f"Email '{email_id}' not found")
                return emails[0]
            elif method == "error":
                raise JMAPError(f"API Error: {result}")

        raise NotFoundError(f"Email '{email_id}' not found")

    def move_email(self, email_id: str, mailbox_id: str) -> dict:
        """Move an email to a different mailbox."""
        self._validate_email_id(email_id)

        if not self.session:
            self.authenticate()

        session = self.session
        assert session is not None

        # First get the current email to see its mailboxIds
        email = self.get_email(email_id)
        
        # Build new mailboxIds dict - only the target mailbox
        set_request = {
            "using": ["urn:ietf:params:jmap:core", "urn:ietf:params:jmap:mail"],
            "methodCalls": [
                [
                    "Email/set",
                    {
                        "accountId": session.account_id,
                        "update": {
                            email_id: {
                                "mailboxIds": {mailbox_id: True}
                            }
                        }
                    },
                    "0"
                ]
            ]
        }

        response = self._make_request(session.api_url, method="POST", data=set_request)

        method_responses = response.get("methodResponses", [])

        for method, result, tag in method_responses:
            if method == "Email/set":
                updated = result.get("updated", {})
                if email_id not in updated:
                    not_updated = result.get("notUpdated", {})
                    if email_id in not_updated:
                        raise JMAPError(f"Failed to move email: {not_updated[email_id]}")
                    raise JMAPError("Failed to move email")
                # Return the updated email
                return self.get_email(email_id)
            elif method == "error":
                raise JMAPError(f"API Error: {result}")

        raise JMAPError("Failed to move email")

    def mark_as_read(self, email_id: str, read: bool = True) -> dict:
        """Mark an email as read or unread."""
        self._validate_email_id(email_id)

        if not self.session:
            self.authenticate()

        session = self.session
        assert session is not None

        keywords = {"$seen": True} if read else {}

        set_request = {
            "using": ["urn:ietf:params:jmap:core", "urn:ietf:params:jmap:mail"],
            "methodCalls": [
                [
                    "Email/set",
                    {
                        "accountId": session.account_id,
                        "update": {
                            email_id: {
                                "keywords": keywords
                            }
                        }
                    },
                    "0"
                ]
            ]
        }

        response = self._make_request(session.api_url, method="POST", data=set_request)

        method_responses = response.get("methodResponses", [])

        for method, result, tag in method_responses:
            if method == "Email/set":
                updated = result.get("updated", {})
                if email_id not in updated:
                    not_updated = result.get("notUpdated", {})
                    if email_id in not_updated:
                        raise JMAPError(f"Failed to mark email: {not_updated[email_id]}")
                    raise JMAPError("Failed to mark email")
                # Return the updated email
                return self.get_email(email_id)
            elif method == "error":
                raise JMAPError(f"API Error: {result}")

        raise JMAPError("Failed to mark email")
