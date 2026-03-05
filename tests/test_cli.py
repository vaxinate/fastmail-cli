"""Tests for fastmail-cli."""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

# Ensure src is in path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from fastmail_cli.cli import cli, parse_date, parse_size


class TestParseSize:
    """Test size parsing."""
    
    def test_bytes(self):
        assert parse_size('1000') == 1000
        assert parse_size('1000000') == 1000000
    
    def test_kilobytes(self):
        assert parse_size('1K') == 1024
        assert parse_size('500K') == 500 * 1024
    
    def test_megabytes(self):
        assert parse_size('1M') == 1024 ** 2
        assert parse_size('10M') == 10 * (1024 ** 2)
    
    def test_gigabytes(self):
        assert parse_size('1G') == 1024 ** 3
    
    def test_invalid(self):
        with pytest.raises(Exception):
            parse_size('invalid')


class TestParseDate:
    """Test date parsing."""
    
    def test_date_only(self):
        result = parse_date('2024-01-01')
        assert result == '2024-01-01T00:00:00Z'
    
    def test_datetime(self):
        result = parse_date('2024-12-31T23:59:59')
        assert result == '2024-12-31T23:59:59Z'
    
    def test_invalid(self):
        with pytest.raises(Exception):
            parse_date('invalid')


class TestCLI:
    """Test CLI commands."""
    
    def setup_method(self):
        self.runner = CliRunner()
    
    def test_no_token(self):
        """Test error when FASTMAIL_API_TOKEN is not set."""
        # Ensure token is not set
        env = os.environ.copy()
        env.pop('FASTMAIL_API_TOKEN', None)
        
        result = self.runner.invoke(cli, ['list', 'mailboxes'], env=env)
        assert result.exit_code == 1
        
        output = json.loads(result.output)
        assert output['success'] == False
        assert 'FASTMAIL_API_TOKEN' in output['error']
    
    @patch('fastmail_cli.cli.FastmailJMAPClient')
    def test_list_mailboxes(self, mock_client_class):
        """Test list mailboxes command."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        
        mock_client.list_mailboxes.return_value = [
            {'id': 'mbox1', 'name': 'Inbox', 'role': 'inbox'},
            {'id': 'mbox2', 'name': 'Sent', 'role': 'sent'},
        ]
        
        env = {'FASTMAIL_API_TOKEN': 'test-token'}
        result = self.runner.invoke(cli, ['list', 'mailboxes'], env=env)
        
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output['success'] == True
        assert len(output['data']) == 2
    
    @patch('fastmail_cli.cli.FastmailJMAPClient')
    def test_list_emails(self, mock_client_class):
        """Test list emails command."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        
        mock_client.get_mailbox_by_name.return_value = {'id': 'inbox-id'}
        mock_client.query_emails.return_value = [
            {'id': 'email1', 'subject': 'Test'},
        ]
        
        env = {'FASTMAIL_API_TOKEN': 'test-token'}
        result = self.runner.invoke(cli, ['list', 'emails'], env=env)
        
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output['success'] == True
    
    @patch('fastmail_cli.cli.FastmailJMAPClient')
    def test_get_email(self, mock_client_class):
        """Test get email command."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        
        mock_client.get_email.return_value = {'id': 'email1', 'subject': 'Test'}
        
        env = {'FASTMAIL_API_TOKEN': 'test-token'}
        result = self.runner.invoke(cli, ['get', 'email1'], env=env)
        
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output['success'] == True
        assert output['data']['id'] == 'email1'
    
    @patch('fastmail_cli.cli.FastmailJMAPClient')
    def test_search(self, mock_client_class):
        """Test search command."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        
        mock_client.query_emails.return_value = [
            {'id': 'email1', 'subject': 'Meeting notes'},
        ]
        
        env = {'FASTMAIL_API_TOKEN': 'test-token'}
        result = self.runner.invoke(cli, ['search', '--text', 'meeting'], env=env)
        
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output['success'] == True
    
    @patch('fastmail_cli.cli.FastmailJMAPClient')
    def test_move(self, mock_client_class):
        """Test move command."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        
        mock_client.get_mailbox_by_name.return_value = {'id': 'archive-id'}
        mock_client.move_email.return_value = {'id': 'email1', 'mailboxIds': {'archive-id': True}}
        
        env = {'FASTMAIL_API_TOKEN': 'test-token'}
        result = self.runner.invoke(cli, ['move', 'email1', '--to', 'Archive'], env=env)
        
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output['success'] == True
    
    @patch('fastmail_cli.cli.FastmailJMAPClient')
    def test_mark_read(self, mock_client_class):
        """Test mark-read command."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        
        mock_client.mark_as_read.return_value = {'id': 'email1', 'keywords': {'$seen': True}}
        
        env = {'FASTMAIL_API_TOKEN': 'test-token'}
        result = self.runner.invoke(cli, ['mark-read', 'email1'], env=env)
        
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output['success'] == True
    
    @patch('fastmail_cli.cli.FastmailJMAPClient')
    def test_mark_unread(self, mock_client_class):
        """Test mark-read --unread command."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        
        mock_client.mark_as_read.return_value = {'id': 'email1', 'keywords': {}}
        
        env = {'FASTMAIL_API_TOKEN': 'test-token'}
        result = self.runner.invoke(cli, ['mark-read', 'email1', '--unread'], env=env)
        
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output['success'] == True


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
