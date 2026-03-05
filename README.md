# fastmail-cli

A CLI tool for managing Fastmail emails via JMAP API.

## Installation

```bash
pip install -e .
```

## Configuration

Set the `FASTMAIL_API_TOKEN` environment variable:

```bash
export FASTMAIL_API_TOKEN="your-api-token"
```

Generate a token at: Fastmail Settings → Privacy & Security → Manage API tokens

## Usage

All commands output JSON.

### List mailboxes

```bash
fastmail-cli list mailboxes
```

### List emails

```bash
# Default: INBOX, 50 emails
fastmail-cli list emails

# Specific mailbox and limit
fastmail-cli list emails --mailbox Sent --limit 10
```

### Search emails

```bash
# Full-text search
fastmail-cli search --text "meeting notes"

# Filter by header fields
fastmail-cli search --from "alice@example.com"
fastmail-cli search --to "bob" --subject "project"

# Filter by mailbox
fastmail-cli search --mailbox Sent --limit 10

# Attachments and size
fastmail-cli search --has-attachment
fastmail-cli search --min-size 1000000  # > 1MB

# Date range (ISO 8601)
fastmail-cli search --after 2024-01-01 --before 2024-12-31

# Status filters
fastmail-cli search --unread
fastmail-cli search --flagged

# Combine filters
fastmail-cli search --from "boss" --has-attachment --after 2024-06-01 --limit 20
```

### Get email details

```bash
fastmail-cli get EMAIL_ID
```

### Move email

```bash
fastmail-cli move EMAIL_ID --to Archive
fastmail-cli move EMAIL_ID --to Trash
```

### Mark as read/unread

```bash
# Mark as read
fastmail-cli mark-read EMAIL_ID

# Mark as unread
fastmail-cli mark-read EMAIL_ID --unread
```

## License

MIT
