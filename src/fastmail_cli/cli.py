"""CLI interface for fastmail-cli using Click."""

import json
import logging
import os
import sys
from datetime import datetime
from typing import Any, Optional, Union

import click

from fastmail_cli.client import FastmailJMAPClient, MailboxRole
from fastmail_cli.exceptions import AuthenticationError, JMAPError, NotFoundError, ValidationError


# Default output function - always JSON
def output_json(data: Union[dict, list], success: bool = True) -> None:
    """Output data as JSON."""
    result = {
        "success": success,
        "data": data
    }
    click.echo(json.dumps(result, indent=2))


def output_error(message: str) -> None:
    """Output error as JSON to stderr."""
    result = {
        "success": False,
        "error": message
    }
    click.echo(json.dumps(result, indent=2), err=True)


def get_client() -> FastmailJMAPClient:
    """Get authenticated client from environment."""
    token = os.environ.get("FASTMAIL_API_TOKEN")
    if not token:
        output_error(
            "FASTMAIL_API_TOKEN environment variable not set. "
            "Generate a token at: Settings → Privacy & Security → Manage API tokens"
        )
        sys.exit(1)
    return FastmailJMAPClient(token)


def parse_size(size_str: str) -> int:
    """Parse size string (e.g., '1M', '500K') to bytes."""
    size_str = size_str.upper().strip()
    multipliers = {
        'K': 1024,
        'M': 1024 ** 2,
        'G': 1024 ** 3,
    }
    
    for suffix, multiplier in multipliers.items():
        if size_str.endswith(suffix):
            try:
                return int(float(size_str[:-1]) * multiplier)
            except ValueError:
                raise click.BadParameter(f"Invalid size: {size_str}")
    
    try:
        return int(size_str)
    except ValueError:
        raise click.BadParameter(f"Invalid size: {size_str}")


def parse_date(date_str: str) -> str:
    """Parse date string to ISO 8601 format with time."""
    date_str = date_str.strip()
    
    # Try various date formats
    formats = [
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
    ]
    
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            # Return in JMAP format (ISO 8601)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    
    raise click.BadParameter(f"Invalid date format: {date_str}. Use ISO 8601 (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)")


# Main CLI group
@click.group()
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose logging')
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """Fastmail CLI - Manage emails via JMAP API.
    
    Requires FASTMAIL_API_TOKEN environment variable to be set.
    All output is in JSON format.
    """
    # Setup logging
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Ensure context object exists
    ctx.ensure_object(dict)


# List command group
@cli.group(name='list')
def list_cmd() -> None:
    """List mailboxes or emails."""
    pass


@list_cmd.command(name='mailboxes')
def list_mailboxes() -> None:
    """List all mailboxes/folders."""
    try:
        with get_client() as client:
            mailboxes = client.list_mailboxes()
            output_json(mailboxes)
    except AuthenticationError as e:
        output_error(f"Authentication failed: {e}")
        sys.exit(1)
    except JMAPError as e:
        output_error(str(e))
        sys.exit(1)


@list_cmd.command(name='emails')
@click.option('--mailbox', '-m', default='Inbox', help='Mailbox name (default: Inbox)')
@click.option('--limit', '-l', default=50, type=click.IntRange(1, 1000), help='Maximum number of emails (1-1000)')
@click.option('--offset', '-o', default=0, type=int, help='Offset for pagination')
def list_emails(mailbox: str, limit: int, offset: int) -> None:
    """List emails in a mailbox (default: INBOX, 50 emails)."""
    try:
        with get_client() as client:
            # Resolve mailbox name to ID
            try:
                mbox = client.get_mailbox_by_name(mailbox)
                mailbox_id = mbox['id']
            except NotFoundError:
                output_error(f"Mailbox '{mailbox}' not found")
                sys.exit(1)
            
            emails = client.query_emails(
                mailbox_id=mailbox_id,
                limit=limit,
                offset=offset
            )
            output_json(emails)
    except AuthenticationError as e:
        output_error(f"Authentication failed: {e}")
        sys.exit(1)
    except JMAPError as e:
        output_error(str(e))
        sys.exit(1)


@cli.command()
@click.option('--text', '-t', help='Full-text search')
@click.option('--from', 'from_addr', help='Filter by sender')
@click.option('--to', help='Filter by recipient')
@click.option('--subject', '-s', help='Filter by subject')
@click.option('--mailbox', '-m', help='Filter by mailbox name')
@click.option('--has-attachment', is_flag=True, help='Only emails with attachments')
@click.option('--min-size', help='Minimum size (e.g., 1000000 or 1M)')
@click.option('--max-size', help='Maximum size (e.g., 1000000 or 1M)')
@click.option('--after', help='Received after date (ISO 8601)')
@click.option('--before', help='Received before date (ISO 8601)')
@click.option('--unread', is_flag=True, help='Only unread emails')
@click.option('--flagged', is_flag=True, help='Only flagged emails')
@click.option('--limit', '-l', default=50, type=click.IntRange(1, 1000), help='Maximum results (1-1000)')
@click.option('--offset', '-o', default=0, type=int, help='Offset for pagination')
def search(
    text: Optional[str],
    from_addr: Optional[str],
    to: Optional[str],
    subject: Optional[str],
    mailbox: Optional[str],
    has_attachment: bool,
    min_size: Optional[str],
    max_size: Optional[str],
    after: Optional[str],
    before: Optional[str],
    unread: bool,
    flagged: bool,
    limit: int,
    offset: int
) -> None:
    """Search emails with filters.
    
    Examples:
        fastmail-cli search --text "meeting notes"
        fastmail-cli search --from "alice@example.com"
        fastmail-cli search --to "bob" --subject "project"
        fastmail-cli search --mailbox Sent --limit 10
        fastmail-cli search --has-attachment
        fastmail-cli search --min-size 1000000
        fastmail-cli search --after 2024-01-01 --before 2024-12-31
        fastmail-cli search --unread
        fastmail-cli search --flagged
        fastmail-cli search --from "boss" --has-attachment --after 2024-06-01 --limit 20
    """
    try:
        with get_client() as client:
            # Resolve mailbox if specified
            mailbox_id = None
            if mailbox:
                try:
                    mbox = client.get_mailbox_by_name(mailbox)
                    mailbox_id = mbox['id']
                except NotFoundError:
                    output_error(f"Mailbox '{mailbox}' not found")
                    sys.exit(1)
            
            # Parse size parameters
            min_size_bytes = parse_size(min_size) if min_size else None
            max_size_bytes = parse_size(max_size) if max_size else None
            
            # Parse date parameters
            after_parsed = parse_date(after) if after else None
            before_parsed = parse_date(before) if before else None
            
            emails = client.query_emails(
                text=text,
                from_addr=from_addr,
                to_addr=to,
                subject=subject,
                mailbox_id=mailbox_id,
                has_attachment=has_attachment,
                min_size=min_size_bytes,
                max_size=max_size_bytes,
                after=after_parsed,
                before=before_parsed,
                unread_only=unread,
                flagged_only=flagged,
                limit=limit,
                offset=offset
            )
            output_json(emails)
    except AuthenticationError as e:
        output_error(f"Authentication failed: {e}")
        sys.exit(1)
    except click.BadParameter as e:
        output_error(str(e))
        sys.exit(1)
    except JMAPError as e:
        output_error(str(e))
        sys.exit(1)


@cli.command()
@click.argument('email_id')
def get(email_id: str) -> None:
    """Get email details by ID."""
    try:
        with get_client() as client:
            email = client.get_email(email_id)
            output_json(email)
    except AuthenticationError as e:
        output_error(f"Authentication failed: {e}")
        sys.exit(1)
    except NotFoundError as e:
        output_error(str(e))
        sys.exit(1)
    except JMAPError as e:
        output_error(str(e))
        sys.exit(1)


@cli.command()
@click.argument('email_id')
@click.option('--to', 'destination', required=True, help='Destination mailbox name')
def move(email_id: str, destination: str) -> None:
    """Move an email to another mailbox.
    
    Examples:
        fastmail-cli move EMAIL_ID --to Archive
        fastmail-cli move EMAIL_ID --to Trash
    """
    try:
        with get_client() as client:
            # Resolve destination mailbox
            try:
                mbox = client.get_mailbox_by_name(destination)
                mailbox_id = mbox['id']
            except NotFoundError:
                output_error(f"Mailbox '{destination}' not found")
                sys.exit(1)
            
            email = client.move_email(email_id, mailbox_id)
            output_json(email)
    except AuthenticationError as e:
        output_error(f"Authentication failed: {e}")
        sys.exit(1)
    except NotFoundError as e:
        output_error(str(e))
        sys.exit(1)
    except JMAPError as e:
        output_error(str(e))
        sys.exit(1)


@cli.command(name='mark-read')
@click.argument('email_id')
@click.option('--unread', is_flag=True, help='Mark as unread instead')
def mark_read(email_id: str, unread: bool) -> None:
    """Mark an email as read or unread.
    
    Examples:
        fastmail-cli mark-read EMAIL_ID
        fastmail-cli mark-read EMAIL_ID --unread
    """
    try:
        with get_client() as client:
            email = client.mark_as_read(email_id, read=not unread)
            output_json(email)
    except AuthenticationError as e:
        output_error(f"Authentication failed: {e}")
        sys.exit(1)
    except NotFoundError as e:
        output_error(str(e))
        sys.exit(1)
    except JMAPError as e:
        output_error(str(e))
        sys.exit(1)


if __name__ == '__main__':
    cli()
