adam@pizzaclawjr:~/code/fastmail$ cat
fastmail_jmap.py  __pycache__/      README.md         token
adam@pizzaclawjr:~/code/fastmail$ cat
fastmail_jmap.py  __pycache__/      README.md         token
adam@pizzaclawjr:~/code/fastmail$ cat fastmail_jmap.py
#!/usr/bin/env python3
"""
Fastmail JMAP API Client

A Python script for authenticating, listing mailboxes, querying mailboxes,
and reading email via Fastmail's JMAP API.

Usage:
    export FASTMAIL_API_TOKEN="your-api-token"
    python fastmail_jmap.py --list-mailboxes
    python fastmail_jmap.py --list-mailboxes --json
    python fastmail_jmap.py --query-mailbox "Inbox"
    python fastmail_jmap.py --query-mailbox "Inbox" --unread --json
    python fastmail_jmap.py --read-email <email-id>
    python fastmail_jmap.py --search "meeting"
    python fastmail_jmap.py --search "" --unread

To get an API token:
    1. Log in to Fastmail web interface
    2. Go to Settings → Privacy & Security → Manage API tokens
    3. Generate a new API token
"""

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from enum import Enum
from functools import wraps
from typing import Any

import urllib.request

# Configure logging
logger = logging.getLogger(__name__)


class JMAPError(Exception):
    """Base exception for JMAP errors."""
    pass


class AuthenticationError(JMAPError):
    """Raised when authentication fails."""
    pass


class ValidationError(JMAPError):
    """Raised when input validation fails."""
    pass


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
    primary_mailbox_id: str | None = None


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

    # Official Fastmail JMAP Session URL (per docs: https://www.fastmail.com/dev/)
    JMAP_SESSION_URL = "https://api.fastmail.com/jmap/session"

    def __init__(self, api_token: str, timeout: int = 30):
        """
        Initialize the client with an API token.

        Args:
            api_token: A Fastmail API token (generate in Settings → Privacy & Security → Manage API tokens)
            timeout: Request timeout in seconds (default: 30)
        """
        self.api_token = api_token
        self.timeout = timeout
        self.session: JMAPSession | None = None
        self._auth_header = self._create_auth_header()

    def _create_auth_header(self) -> str:
        """Create Bearer Auth header with API token."""
        return f"Bearer {self.api_token}"

    def __enter__(self):
        """Context manager entry - authenticate on enter."""
        self.authenticate()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - cleanup if needed."""
        # No cleanup needed for this client
        pass

    @retry_on_failure(max_retries=3, delay=1.0)
    def _make_request(
        self,
        url: str,
        method: str = "GET",
        data: dict | None = None,
        headers: dict | None = None
    ) -> dict:
        """Make an HTTP request to the JMAP API."""
        default_headers = {
            "Authorization": self._auth_header,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "FastmailJMAPClient/1.0",
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
                # Check if we got redirected to a web page (auth failure)
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
                        f"This may indicate an authentication or connectivity issue."
                    )
                try:
                    parsed_response = json.loads(response_body)
                except json.JSONDecodeError as e:
                    raise JMAPError(f"Invalid JSON response: {e}")

                # Validate response format
                if not isinstance(parsed_response, dict):
                    raise JMAPError("Invalid response format: expected JSON object")

                return parsed_response

        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise AuthenticationError(
                    "Authentication failed. Please check your API token."
                )
            if e.code == 403:
                error_body = e.read().decode()
                raise JMAPError(
                    f"HTTP Error 403 (Forbidden): Access blocked by Cloudflare or server.\n"
                    f"Response body: {error_body[:500]}\n\n"
                    f"Possible solutions:\n"
                    f"  1. Ensure you're using a valid Fastmail API token (not your main password)\n"
                    f"  2. Check if your IP is rate-limited or blocked\n"
                    f"  3. Try accessing from a different network/VPN"
                )
            if e.code == 429:
                raise JMAPError(
                    f"HTTP Error 429 (Rate Limited): Too many requests. "
                    f"Please wait a bit before trying again."
                )
            error_body = e.read().decode()
            raise JMAPError(f"HTTP Error {e.code}: {error_body}")
        except urllib.error.URLError as e:
            raise JMAPError(f"Connection error: {e.reason}")
        except TimeoutError:
            raise JMAPError("Request timed out. Please check your internet connection.")

    def authenticate(self) -> JMAPSession:
        """
        Authenticate and get JMAP session information.

        Returns:
            JMAPSession object containing API URLs and account info.
        """
        # The session URL returns the session object directly
        response = self._make_request(self.JMAP_SESSION_URL)

        # Extract account ID (primary account)
        accounts = response.get("accounts", {})
        if not accounts:
            raise JMAPError("No accounts found in JMAP session")

        # Get the primary account (usually the first one)
        account_id = list(accounts.keys())[0]

        # Safely extract session URLs with fallbacks
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
            # Log the error but don't fail - we can still use the session
            logger.warning(f"Could not get primary mailbox: {e}")

        self.session = session
        return session

    def _list_mailboxes_raw(self, session: JMAPSession | None = None) -> list[dict]:
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

        response = self._make_request(
            session.api_url,
            method="POST",
            data=request_data
        )

        method_responses = response.get("methodResponses", [])
        for method, result, tag in method_responses:
            if method == "Mailbox/get":
                return result.get("list", [])
            elif method == "error":
                raise JMAPError(f"API Error: {result}")

        return []

    def list_mailboxes(self) -> list[dict]:
        """
        List all mailboxes (folders) in the account.

        Returns:
            List of mailbox dictionaries containing id, name, role, etc.
        """
        if not self.session:
            self.authenticate()

        return self._list_mailboxes_raw()

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
        # Email IDs are typically alphanumeric strings
        if not email_id.strip():
            raise ValidationError("email_id cannot be whitespace only")

    def _build_filter(self, query: str | None, mailbox_id: str | None,
                      unread_only: bool) -> dict | None:
        """Build JMAP filter criteria."""
        conditions: list[dict] = []

        if mailbox_id:
            conditions.append({"inMailbox": mailbox_id})

        if unread_only:
            conditions.append({"operator": "NOT", "conditions": [{"hasKeyword": "$seen"}]})

        if query:
            text_conditions = {
                "operator": "OR",
                "conditions": [
                    {"subject": query},
                    {"from": query},
                    {"body": query}
                ]
            }
            conditions.append(text_conditions)

        if len(conditions) == 0:
            return None
        elif len(conditions) == 1:
            return conditions[0]
        else:
            return {"operator": "AND", "conditions": conditions}

    def query_mailbox(
        self,
        mailbox_name: str | None = None,
        mailbox_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
        unread_only: bool = False
    ) -> list[dict]:
        """
        Query emails in a specific mailbox.

        Args:
            mailbox_name: Name of the mailbox to search (e.g., "Inbox")
            mailbox_id: Direct mailbox ID (alternative to name)
            limit: Maximum number of emails to return (1-1000)
            offset: Return emails starting at this offset (for pagination)
            unread_only: If True, only return unread emails

        Returns:
            List of email dictionaries.
        """
        self._validate_limit(limit)

        if not self.session:
            self.authenticate()

        session = self.session
        assert session is not None

        # If mailbox_name is provided, find the corresponding ID
        if mailbox_name and not mailbox_id:
            mailboxes = self.list_mailboxes()
            for mailbox in mailboxes:
                if mailbox["name"].lower() == mailbox_name.lower():
                    mailbox_id = mailbox["id"]
                    break
            if not mailbox_id:
                raise JMAPError(f"Mailbox '{mailbox_name}' not found")

        if not mailbox_id:
            raise JMAPError("Either mailbox_name or mailbox_id must be provided")

        # Build filter criteria
        filter_criteria = self._build_filter(None, mailbox_id, unread_only)

        query_request: dict[str, Any] = {
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
                        "fetchAllBodyValues": True,
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

        # Add filter only if needed
        if filter_criteria:
            query_request["methodCalls"][0][1]["filter"] = filter_criteria

        response = self._make_request(
            session.api_url,
            method="POST",
            data=query_request
        )

        emails = []
        method_responses = response.get("methodResponses", [])

        for method, result, tag in method_responses:
            if method == "Email/get":
                emails = result.get("list", [])
            elif method == "error":
                raise JMAPError(f"API Error: {result}")

        return emails

    def search_emails(
        self,
        query: str,
        mailbox_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
        unread_only: bool = False
    ) -> list[dict]:
        """
        Search emails by subject, sender, or body text.

        Args:
            query: Search string (searches in subject, from, and body)
            mailbox_id: Optional mailbox ID to limit search
            limit: Maximum number of results (1-1000)
            offset: Return emails starting at this offset (for pagination)
            unread_only: If True, only return unread emails

        Returns:
            List of matching email dictionaries.
        """
        self._validate_limit(limit)

        if not self.session:
            self.authenticate()

        session = self.session
        assert session is not None

        # Build filter conditions
        filter_criteria = self._build_filter(query, mailbox_id, unread_only)

        search_request: dict[str, Any] = {
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
                        "properties": [
                            "id",
                            "blobId",
                            "threadId",
                            "keywords",
                            "size",
                            "receivedAt",
                            "from",
                            "to",
                            "subject",
                            "preview",
                            "hasAttachment"
                        ],
                        "fetchAllBodyValues": False,
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

        # Add filter only if needed
        if filter_criteria:
            search_request["methodCalls"][0][1]["filter"] = filter_criteria

        response = self._make_request(
            session.api_url,
            method="POST",
            data=search_request
        )

        emails = []
        method_responses = response.get("methodResponses", [])

        for method, result, tag in method_responses:
            if method == "Email/get":
                emails = result.get("list", [])
            elif method == "error":
                raise JMAPError(f"API Error: {result}")

        return emails

    def read_email(
        self,
        email_id: str,
        fetch_body: bool = True,
        fetch_attachments: bool = False
    ) -> dict:
        """
        Read a specific email by ID.

        Args:
            email_id: The JMAP email ID
            fetch_body: Whether to fetch the full body content
            fetch_attachments: Whether to fetch attachment data

        Returns:
            Dictionary containing full email details.

        Raises:
            ValidationError: If email_id is invalid
            JMAPError: If email not found or API error
        """
        self._validate_email_id(email_id)

        if not self.session:
            self.authenticate()

        session = self.session
        assert session is not None

        properties = [
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
        ]

        if fetch_body:
            properties.extend(["bodyValues", "textBody", "htmlBody"])

        if fetch_attachments:
            properties.append("attachments")

        body_properties: dict[str, Any] = {}
        if fetch_body:
            body_properties["fetchAllBodyValues"] = True

        get_request = {
            "using": ["urn:ietf:params:jmap:core", "urn:ietf:params:jmap:mail"],
            "methodCalls": [
                [
                    "Email/get",
                    {
                        "accountId": session.account_id,
                        "ids": [email_id],
                        "properties": properties,
                        **body_properties
                    },
                    "0"
                ]
            ]
        }

        response = self._make_request(
            session.api_url,
            method="POST",
            data=get_request
        )

        method_responses = response.get("methodResponses", [])

        for method, result, tag in method_responses:
            if method == "Email/get":
                emails = result.get("list", [])
                if not emails:
                    raise JMAPError(f"Email '{email_id}' not found")
                return emails[0]
            elif method == "error":
                raise JMAPError(f"API Error: {result}")

        raise JMAPError(f"Email '{email_id}' not found")

    def mark_as_read(self, email_id: str, read: bool = True) -> bool:
        """
        Mark an email as read or unread.

        Args:
            email_id: The email ID to update
            read: True to mark as read, False to mark as unread

        Returns:
            True if successful

        Raises:
            ValidationError: If email_id is invalid
        """
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

        response = self._make_request(
            session.api_url,
            method="POST",
            data=set_request
        )

        method_responses = response.get("methodResponses", [])

        for method, result, tag in method_responses:
            if method == "Email/set":
                updated = result.get("updated", {})
                return email_id in updated
            elif method == "error":
                raise JMAPError(f"API Error: {result}")

        return False

    def mark_emails_as_read(self, email_ids: list[str], read: bool = True) -> list[str]:
        """
        Mark multiple emails as read or unread in a single request.

        Args:
            email_ids: List of email IDs to update
            read: True to mark as read, False to mark as unread

        Returns:
            List of successfully updated email IDs

        Raises:
            ValidationError: If email_ids is empty or contains invalid IDs
        """
        if not email_ids:
            raise ValidationError("email_ids cannot be empty")

        if len(email_ids) > 100:
            raise ValidationError(f"Cannot update more than 100 emails at once, got {len(email_ids)}")

        for email_id in email_ids:
            self._validate_email_id(email_id)

        if not self.session:
            self.authenticate()

        session = self.session
        assert session is not None

        keywords = {"$seen": True} if read else {}

        # Build update dict
        update = {email_id: {"keywords": keywords} for email_id in email_ids}

        set_request = {
            "using": ["urn:ietf:params:jmap:core", "urn:ietf:params:jmap:mail"],
            "methodCalls": [
                [
                    "Email/set",
                    {
                        "accountId": session.account_id,
                        "update": update
                    },
                    "0"
                ]
            ]
        }

        response = self._make_request(
            session.api_url,
            method="POST",
            data=set_request
        )

        method_responses = response.get("methodResponses", [])

        for method, result, tag in method_responses:
            if method == "Email/set":
                updated = result.get("updated", {})
                return list(updated.keys())
            elif method == "error":
                raise JMAPError(f"API Error: {result}")

        return []


def format_email_summary(email: dict) -> str:
    """Format an email as a readable summary."""
    from_addr = email.get("from", [])
    from_str = from_addr[0].get("email", "Unknown") if from_addr else "Unknown"

    subject = email.get("subject", "(no subject)")
    received = email.get("receivedAt", "Unknown")
    preview = email.get("preview", "")
    email_id = email.get("id", "")

    return f"""
┌{'─' * 78}┐
│ ID: {email_id:<67}│
│ From: {from_str:<65}│
│ Subject: {subject:<62}│
│ Received: {received:<60}│
├{'─' * 78}┤
│ {preview[:76]:<76} │
└{'─' * 78}┘
"""


def format_email_full(email: dict) -> str:
    """Format an email with full details."""
    lines = []

    # Headers
    lines.append("=" * 80)
    lines.append(f"Email ID: {email.get('id', 'N/A')}")
    lines.append(f"Thread ID: {email.get('threadId', 'N/A')}")
    lines.append("-" * 80)

    # Addresses
    def format_addresses(addrs: list) -> str:
        if not addrs:
            return "N/A"
        return ", ".join(f"{a.get('name', '')} <{a.get('email', '')}>".strip() for a in addrs)

    from_addr = email.get("from", [])
    lines.append(f"From: {format_addresses(from_addr)}")

    to_addr = email.get("to", [])
    lines.append(f"To: {format_addresses(to_addr)}")

    cc_addr = email.get("cc", [])
    if cc_addr:
        lines.append(f"Cc: {format_addresses(cc_addr)}")

    bcc_addr = email.get("bcc", [])
    if bcc_addr:
        lines.append(f"Bcc: {format_addresses(bcc_addr)}")

    # Subject and dates
    lines.append(f"Subject: {email.get('subject', '(no subject)')}")
    lines.append(f"Sent: {email.get('sentAt', 'N/A')}")
    lines.append(f"Received: {email.get('receivedAt', 'N/A')}")
    lines.append(f"Size: {email.get('size', 0)} bytes")
    lines.append(f"Has Attachments: {email.get('hasAttachment', False)}")
    lines.append("=" * 80)

    # Body
    body_values = email.get("bodyValues", {})
    text_body = email.get("textBody", [])
    html_body = email.get("htmlBody", [])

    if body_values:
        lines.append("\n--- Body ---\n")
        # Prefer text body
        if text_body:
            part_id = text_body[0].get("partId")
            if part_id and part_id in body_values:
                lines.append(body_values[part_id].get("value", ""))
        elif html_body:
            part_id = html_body[0].get("partId")
            if part_id and part_id in body_values:
                lines.append("[HTML Body]")
                lines.append(body_values[part_id].get("value", "")[:2000])
    else:
        preview = email.get("preview", "")
        if preview:
            lines.append(f"\nPreview: {preview}")

    # Attachments
    attachments = email.get("attachments", [])
    if attachments:
        lines.append("\n--- Attachments ---")
        for att in attachments:
            lines.append(f"  - {att.get('name', 'unnamed')} "
                        f"({att.get('type', 'unknown')}, "
                        f"{att.get('size', 0)} bytes)")

    return "\n".join(lines)


def format_mailbox(mailbox: dict) -> str:
    """Format a mailbox for display."""
    name = mailbox.get("name", "Unknown")
    role = mailbox.get("role", "N/A")
    total = mailbox.get("totalEmails", 0)
    unread = mailbox.get("unreadEmails", 0)
    mbox_id = mailbox.get("id", "")

    role_display = f"[{role}]" if role else ""
    return f"  {mbox_id:<30} {role_display:<12} {name:<20} ({unread}/{total} unread)"


def setup_logging(verbose: bool = False) -> None:
    """Setup logging configuration."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )


def main():
    parser = argparse.ArgumentParser(
        description="Fastmail JMAP Client - Manage emails via JMAP API"
    )

    # Authentication options
    parser.add_argument(
        "--token", "-t",
        default=os.environ.get("FASTMAIL_API_TOKEN"),
        help="Fastmail API token (or set FASTMAIL_API_TOKEN env var). Generate at Settings → Privacy & Security → Manage API tokens"
    )

    # Actions
    parser.add_argument(
        "--list-mailboxes", "-l",
        action="store_true",
        help="List all mailboxes (folders)"
    )
    parser.add_argument(
        "--query-mailbox", "-q",
        metavar="MAILBOX",
        help="Query emails in a mailbox (e.g., 'Inbox', 'Sent')"
    )
    parser.add_argument(
        "--mailbox-id",
        help="Mailbox ID (alternative to --query-mailbox name)"
    )
    parser.add_argument(
        "--search", "-s",
        metavar="QUERY",
        help="Search emails by subject, sender, or body"
    )
    parser.add_argument(
        "--read-email", "-r",
        metavar="EMAIL_ID",
        help="Read a specific email by ID"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of emails to return (default: 20, max: 1000)"
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Starting offset for pagination (default: 0)"
    )
    parser.add_argument(
        "--mark-read",
        action="store_true",
        help="Mark the email as read (used with --read-email)"
    )
    parser.add_argument(
        "--unread", "-u",
        action="store_true",
        help="Show only unread emails (used with --search or --query-mailbox)"
    )
    parser.add_argument(
        "--json", "-j",
        action="store_true",
        help="Output results as JSON"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging(args.verbose)

    # Validate authentication
    if not args.token:
        print("Error: API token is required.", file=sys.stderr)
        print("Set FASTMAIL_API_TOKEN environment variable or use --token option.", file=sys.stderr)
        print("\nTo create an API token:", file=sys.stderr)
        print("  1. Log in to Fastmail web interface", file=sys.stderr)
        print("  2. Go to Settings → Privacy & Security → Manage API tokens", file=sys.stderr)
        print("  3. Generate a new API token", file=sys.stderr)
        sys.exit(1)

    # Mask token in logs for security
    logger.debug(f"Using API token: {args.token[:8]}...{args.token[-4:]}")

    try:
        # Use context manager for automatic authentication
        with FastmailJMAPClient(args.token) as client:
            logger.info(f"Authenticated successfully! Account ID: {client.session.account_id}")

            # Execute requested action
            if args.list_mailboxes:
                mailboxes = client.list_mailboxes()
                if args.json:
                    print(json.dumps(mailboxes, indent=2))
                else:
                    print("Mailboxes:")
                    print("-" * 80)
                    print(f"  {'ID':<30} {'Role':<12} {'Name':<20} Unread/Total")
                    print("-" * 80)
                    for mailbox in mailboxes:
                        print(format_mailbox(mailbox))

            elif args.query_mailbox or args.mailbox_id:
                mailbox_name = args.query_mailbox
                mailbox_id = args.mailbox_id

                if mailbox_name:
                    logger.info(f"Querying mailbox '{mailbox_name}'...")
                else:
                    logger.info(f"Querying mailbox ID '{mailbox_id}'...")

                emails = client.query_mailbox(
                    mailbox_name=mailbox_name,
                    mailbox_id=mailbox_id,
                    limit=args.limit,
                    offset=args.offset,
                    unread_only=args.unread
                )

                if args.json:
                    print(json.dumps(emails, indent=2))
                else:
                    print(f"\nFound {len(emails)} email(s):\n")
                    for email in emails:
                        print(format_email_summary(email))

            elif args.search:
                logger.info(f"Searching for '{args.search}'...")
                emails = client.search_emails(
                    args.search,
                    limit=args.limit,
                    offset=args.offset,
                    unread_only=args.unread
                )

                if args.json:
                    print(json.dumps(emails, indent=2))
                else:
                    print(f"\nFound {len(emails)} email(s):\n")
                    for email in emails:
                        print(format_email_summary(email))

            elif args.read_email:
                logger.info(f"Fetching email {args.read_email}...")
                email = client.read_email(
                    args.read_email,
                    fetch_body=True,
                    fetch_attachments=True
                )
                if args.json:
                    print(json.dumps(email, indent=2))
                else:
                    print(format_email_full(email))

                if args.mark_read:
                    if client.mark_as_read(args.read_email, read=True):
                        if not args.json:
                            print("\n[Email marked as read]")

            else:
                parser.print_help()
                print("\n\nExamples:")
                print(f"  {sys.argv[0]} --list-mailboxes")
                print(f"  {sys.argv[0]} --list-mailboxes --json")
                print(f"  {sys.argv[0]} --query-mailbox Inbox --limit 10")
                print(f"  {sys.argv[0]} --query-mailbox Inbox --unread --json")
                print(f"  {sys.argv[0]} --query-mailbox Inbox --limit 20 --offset 20")
                print(f"  {sys.argv[0]} --search 'meeting' --limit 5")
                print(f"  {sys.argv[0]} --search '' --unread")
                print(f"  {sys.argv[0]} --search 'project' --limit 20 --offset 40")
                print(f"  {sys.argv[0]} --read-email <email-id> --mark-read")

    except AuthenticationError as e:
        logger.error(f"Authentication failed: {e}")
        sys.exit(1)
    except ValidationError as e:
        logger.error(f"Validation error: {e}")
        sys.exit(1)
    except JMAPError as e:
        logger.error(f"JMAP Error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Operation cancelled.")
        sys.exit(130)


if __name__ == "__main__":
    main()
adam@pizzaclawjr:~/code/fastmail$
