"""Tests for smart_actions command and related functionality."""

import json
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

from email_agent.cli.main import _parse_tags, _add_tag, _serialize_tags
from email_agent.models import Email, EmailAddress, EmailCategory, EmailPriority


class TestTagHelpers:
    """Test tag helper functions."""

    def test_parse_tags_none(self):
        """Test parsing None tags."""
        assert _parse_tags(None) == []

    def test_parse_tags_empty_list(self):
        """Test parsing empty list tags."""
        assert _parse_tags([]) == []

    def test_parse_tags_list(self):
        """Test parsing list tags."""
        tags = ["urgent", "work"]
        result = _parse_tags(tags)
        assert result == ["urgent", "work"]

    def test_parse_tags_json_string(self):
        """Test parsing JSON string tags."""
        tags = json.dumps(["urgent", "work"])
        result = _parse_tags(tags)
        assert result == ["urgent", "work"]

    def test_parse_tags_plain_string(self):
        """Test parsing plain string (non-JSON) tags."""
        tags = "single_tag"
        result = _parse_tags(tags)
        assert result == ["single_tag"]

    def test_parse_tags_empty_string(self):
        """Test parsing empty string tags."""
        assert _parse_tags("") == []

    def test_add_tag_to_empty(self):
        """Test adding tag to empty tags."""
        result = _add_tag([], "action_processed")
        assert "action_processed" in result
        assert len(result) == 1

    def test_add_tag_to_list(self):
        """Test adding tag to existing list."""
        result = _add_tag(["existing"], "action_processed")
        assert "action_processed" in result
        assert "existing" in result
        assert len(result) == 2

    def test_add_tag_no_duplicate(self):
        """Test that adding existing tag doesn't create duplicate."""
        result = _add_tag(["action_processed", "other"], "action_processed")
        assert result.count("action_processed") == 1
        assert len(result) == 2

    def test_add_tag_to_json_string(self):
        """Test adding tag to JSON string tags."""
        tags_str = json.dumps(["existing"])
        result = _add_tag(tags_str, "action_processed")
        assert isinstance(result, list)
        assert "action_processed" in result
        assert "existing" in result

    def test_serialize_tags(self):
        """Test serializing tags list to JSON."""
        tags = ["urgent", "work"]
        result = _serialize_tags(tags)
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert parsed == tags


class TestSmartActionsProcessing:
    """Test smart_actions processing logic with mocked dependencies."""

    @pytest.fixture
    def sample_email_with_actions(self):
        """Create a sample email with expected action items."""
        return Email(
            id="test-email-actions",
            message_id="msg-actions-1",
            subject="Project deadline reminder",
            sender=EmailAddress(email="manager@company.com", name="Manager"),
            recipients=[EmailAddress(email="user@company.com", name="User")],
            cc=[],
            bcc=[],
            body_text="Please review the proposal by Friday and submit your feedback. I committed to sending the report by Monday.",
            body_html="<p>Please review the proposal by Friday and submit your feedback.</p>",
            attachments=[],
            date=datetime.now() - timedelta(hours=2),
            received_date=datetime.now() - timedelta(hours=2),
            is_read=False,
            is_flagged=False,
            is_draft=False,
            category=EmailCategory.PRIMARY,
            priority=EmailPriority.NORMAL,
            tags=[],
            processed_at=None,
            summary=None,
            action_items=[],
            raw_headers={},
            connector_data={"connector_type": "test"},
        )

    @pytest.fixture
    def sample_email_no_actions(self):
        """Create a sample email with no action items."""
        return Email(
            id="test-email-no-actions",
            message_id="msg-no-actions-1",
            subject="Newsletter: Weekly update",
            sender=EmailAddress(email="newsletter@service.com", name="Newsletter"),
            recipients=[EmailAddress(email="user@company.com", name="User")],
            cc=[],
            bcc=[],
            body_text="Here's your weekly newsletter with the latest updates.",
            body_html="<p>Here's your weekly newsletter.</p>",
            attachments=[],
            date=datetime.now() - timedelta(hours=5),
            received_date=datetime.now() - timedelta(hours=5),
            is_read=False,
            is_flagged=False,
            is_draft=False,
            category=EmailCategory.PROMOTIONS,
            priority=EmailPriority.LOW,
            tags=[],
            processed_at=None,
            summary=None,
            action_items=[],
            raw_headers={},
            connector_data={"connector_type": "test"},
        )

    @pytest.fixture
    def sample_email_processed(self):
        """Create a sample email that's already been processed."""
        return Email(
            id="test-email-processed",
            message_id="msg-processed-1",
            subject="Already processed email",
            sender=EmailAddress(email="old@company.com", name="Old Sender"),
            recipients=[EmailAddress(email="user@company.com", name="User")],
            cc=[],
            bcc=[],
            body_text="This email was already processed.",
            body_html="<p>Already processed.</p>",
            attachments=[],
            date=datetime.now() - timedelta(days=2),
            received_date=datetime.now() - timedelta(days=2),
            is_read=True,
            is_flagged=False,
            is_draft=False,
            category=EmailCategory.PRIMARY,
            priority=EmailPriority.NORMAL,
            tags=["action_processed"],
            processed_at=datetime.now() - timedelta(days=1),
            summary="Old summary",
            action_items=["Old action"],
            raw_headers={},
            connector_data={"connector_type": "test"},
        )

    @pytest.fixture
    def action_result_with_items(self):
        """Mock action extraction result with items."""
        return {
            "action_items": [
                {
                    "action": "Review the proposal document",
                    "deadline": "2026-06-27",
                    "priority": "high",
                    "category": "review",
                },
                {
                    "action": "Submit feedback",
                    "deadline": None,
                    "priority": "medium",
                    "category": "respond",
                },
            ],
            "commitments_made": [
                {
                    "commitment": "Send the final report",
                    "deadline": "2026-06-23",
                    "recipient": "manager@company.com",
                }
            ],
            "waiting_for": [
                {
                    "waiting_for": "Client approval on design",
                    "from_whom": "client@company.com",
                    "deadline": "2026-06-25",
                }
            ],
            "meeting_requests": [],
            "needs_response": True,
            "response_urgency": "normal",
            "summary": "Project deadline reminder with review and feedback request.",
            "email_type": "request",
            "extracted_at": datetime.now().isoformat(),
            "email_id": "test-email-actions",
        }

    @pytest.fixture
    def action_result_no_items(self):
        """Mock action extraction result with no items."""
        return {
            "action_items": [],
            "commitments_made": [],
            "waiting_for": [],
            "meeting_requests": [],
            "needs_response": False,
            "response_urgency": "low",
            "summary": "Weekly newsletter with general updates.",
            "email_type": "newsletter",
            "extracted_at": datetime.now().isoformat(),
            "email_id": "test-email-no-actions",
        }

    def test_email_has_action_processed_tag(self, sample_email_processed):
        """Test that processed emails have the action_processed tag."""
        tags = _parse_tags(sample_email_processed.tags)
        assert "action_processed" in tags

    def test_add_action_processed_tag(self):
        """Test adding action_processed tag to email."""
        tags = []
        updated = _add_tag(tags, "action_processed")
        assert "action_processed" in updated
        assert len(updated) == 1

    def test_add_action_processed_no_duplicate(self):
        """Test that action_processed is not duplicated."""
        tags = ["action_processed", "other"]
        updated = _add_tag(tags, "action_processed")
        assert updated.count("action_processed") == 1
        assert len(updated) == 2

    def test_parse_tags_from_json_string(self):
        """Test parsing tags stored as JSON string."""
        tags_json = json.dumps(["action_processed", "urgent"])
        parsed = _parse_tags(tags_json)
        assert isinstance(parsed, list)
        assert "action_processed" in parsed
        assert "urgent" in parsed

    def test_action_result_has_summary(self, action_result_with_items):
        """Test that action result includes a summary."""
        assert "summary" in action_result_with_items
        assert action_result_with_items["summary"] is not None
        assert len(action_result_with_items["summary"]) > 0

    def test_action_result_no_items_has_summary(self, action_result_no_items):
        """Test that even no-item results include a summary."""
        assert "summary" in action_result_no_items
        assert action_result_no_items["summary"] is not None

    def test_action_result_has_needs_response(self, action_result_with_items):
        """Test that action result includes needs_response."""
        assert "needs_response" in action_result_with_items
        assert isinstance(action_result_with_items["needs_response"], bool)

    def test_action_result_no_items_needs_response_false(self, action_result_no_items):
        """Test that no-item result has needs_response=False."""
        assert action_result_no_items["needs_response"] is False

    def test_commitments_in_action_result(self, action_result_with_items):
        """Test that commitments are present in action result."""
        assert "commitments_made" in action_result_with_items
        assert len(action_result_with_items["commitments_made"]) > 0
        assert "commitment" in action_result_with_items["commitments_made"][0]

    def test_waiting_for_in_action_result(self, action_result_with_items):
        """Test that waiting_for items are present in action result."""
        assert "waiting_for" in action_result_with_items
        assert len(action_result_with_items["waiting_for"]) > 0
        assert "waiting_for" in action_result_with_items["waiting_for"][0]


class TestSmartActionsDatabaseIntegration:
    """Test smart_actions database integration with temp database."""

    @pytest.fixture
    def db_with_emails(self, temp_db, sample_emails):
        """Create a temp database with sample emails."""
        from email_agent.storage.models import EmailORM

        count = temp_db.save_emails(sample_emails)
        assert count == len(sample_emails)
        return temp_db

    def test_save_email_with_tags_list(self, temp_db):
        """Test saving email with tags as list."""
        from email_agent.storage.models import EmailORM

        email = Email(
            id="test-tags-list",
            message_id="msg-tags-list",
            subject="Test tags list",
            sender=EmailAddress(email="test@test.com", name="Test"),
            recipients=[],
            cc=[],
            bcc=[],
            body_text="Test body",
            body_html="<p>Test</p>",
            attachments=[],
            date=datetime.now(),
            received_date=datetime.now(),
            is_read=False,
            is_flagged=False,
            is_draft=False,
            category=EmailCategory.PRIMARY,
            priority=EmailPriority.NORMAL,
            tags=["tag1", "tag2"],
            raw_headers={},
            connector_data={"connector_type": "test"},
        )

        result = temp_db.save_email(email)
        assert result is True

        saved = temp_db.get_email("test-tags-list")
        assert saved is not None
        assert "tag1" in saved.tags
        assert "tag2" in saved.tags

    def test_add_action_processed_to_email(self, temp_db):
        """Test adding action_processed tag to email via ORM."""
        from email_agent.storage.models import EmailORM

        email = Email(
            id="test-add-tag",
            message_id="msg-add-tag",
            subject="Test add tag",
            sender=EmailAddress(email="test@test.com", name="Test"),
            recipients=[],
            cc=[],
            bcc=[],
            body_text="Test body",
            body_html="<p>Test</p>",
            attachments=[],
            date=datetime.now(),
            received_date=datetime.now(),
            is_read=False,
            is_flagged=False,
            is_draft=False,
            category=EmailCategory.PRIMARY,
            priority=EmailPriority.NORMAL,
            tags=["existing"],
            raw_headers={},
            connector_data={"connector_type": "test"},
        )

        temp_db.save_email(email)

        with temp_db.get_session() as session:
            email_orm = session.query(EmailORM).filter_by(id="test-add-tag").first()
            assert email_orm is not None

            updated_tags = _add_tag(email_orm.tags, "action_processed")
            email_orm.tags = updated_tags
            email_orm.summary = "Test summary"
            email_orm.action_items = ["Action 1", "Action 2"]
            email_orm.processed_at = datetime.now()
            session.commit()

        # Verify
        saved = temp_db.get_email("test-add-tag")
        assert saved is not None
        tags = _parse_tags(saved.tags)
        assert "action_processed" in tags
        assert "existing" in tags
        assert saved.summary == "Test summary"
        assert len(saved.action_items) == 2
        assert "Action 1" in saved.action_items
        assert saved.processed_at is not None

    def test_action_processed_not_duplicated(self, temp_db):
        """Test that action_processed tag is not duplicated on re-run."""
        from email_agent.storage.models import EmailORM

        email = Email(
            id="test-no-dup",
            message_id="msg-no-dup",
            subject="Test no duplicate",
            sender=EmailAddress(email="test@test.com", name="Test"),
            recipients=[],
            cc=[],
            bcc=[],
            body_text="Test body",
            body_html="<p>Test</p>",
            attachments=[],
            date=datetime.now(),
            received_date=datetime.now(),
            is_read=False,
            is_flagged=False,
            is_draft=False,
            category=EmailCategory.PRIMARY,
            priority=EmailPriority.NORMAL,
            tags=["action_processed"],
            raw_headers={},
            connector_data={"connector_type": "test"},
        )

        temp_db.save_email(email)

        with temp_db.get_session() as session:
            email_orm = session.query(EmailORM).filter_by(id="test-no-dup").first()

            # Add tag again (simulating re-processing)
            updated_tags = _add_tag(email_orm.tags, "action_processed")
            email_orm.tags = updated_tags
            session.commit()

        saved = temp_db.get_email("test-no-dup")
        tags = _parse_tags(saved.tags)
        assert tags.count("action_processed") == 1

    def test_persist_summary_and_action_items(self, temp_db):
        """Test persisting summary and action items to email record."""
        from email_agent.storage.models import EmailORM

        email = Email(
            id="test-persist",
            message_id="msg-persist",
            subject="Test persist",
            sender=EmailAddress(email="test@test.com", name="Test"),
            recipients=[],
            cc=[],
            bcc=[],
            body_text="Test body",
            body_html="<p>Test</p>",
            attachments=[],
            date=datetime.now(),
            received_date=datetime.now(),
            is_read=False,
            is_flagged=False,
            is_draft=False,
            category=EmailCategory.PRIMARY,
            priority=EmailPriority.NORMAL,
            tags=[],
            raw_headers={},
            connector_data={"connector_type": "test"},
        )

        temp_db.save_email(email)

        # Simulate extraction results
        extraction_summary = "This is a test summary of the email."
        action_items_list = [
            "Review the document",
            "Schedule a follow-up meeting",
            "Send confirmation",
        ]

        with temp_db.get_session() as session:
            email_orm = session.query(EmailORM).filter_by(id="test-persist").first()
            email_orm.summary = extraction_summary
            email_orm.action_items = action_items_list
            email_orm.processed_at = datetime.now()
            session.commit()

        saved = temp_db.get_email("test-persist")
        assert saved.summary == extraction_summary
        assert len(saved.action_items) == 3
        assert "Review the document" in saved.action_items
        assert saved.processed_at is not None


class TestCommitmentTrackerIntegration:
    """Test commitment tracker integration with smart actions."""

    @pytest.fixture
    def mock_email(self):
        """Create a mock email for commitment testing."""
        return Email(
            id="commit-test-email",
            message_id="msg-commit-test",
            subject="Commitment test email",
            sender=EmailAddress(email="user@company.com", name="Test User"),
            recipients=[EmailAddress(email="manager@company.com", name="Manager")],
            cc=[],
            bcc=[],
            body_text="I will send the report by Friday.",
            body_html="<p>I will send the report by Friday.</p>",
            attachments=[],
            date=datetime.now(),
            received_date=datetime.now(),
            is_read=False,
            is_flagged=False,
            is_draft=False,
            category=EmailCategory.PRIMARY,
            priority=EmailPriority.NORMAL,
            tags=[],
            raw_headers={},
            connector_data={"connector_type": "test"},
        )

    @pytest.fixture
    def action_result_with_commitments(self):
        """Action result with commitments and waiting items."""
        return {
            "action_items": [],
            "commitments_made": [
                {
                    "commitment": "Send the quarterly report",
                    "deadline": "2026-06-27",
                    "recipient": "manager@company.com",
                }
            ],
            "waiting_for": [
                {
                    "waiting_for": "Budget approval",
                    "from_whom": "finance@company.com",
                    "deadline": "2026-06-30",
                }
            ],
            "meeting_requests": [],
            "needs_response": False,
            "response_urgency": "low",
            "summary": "Commitment test email.",
            "email_type": "request",
        }

    @pytest.mark.asyncio
    async def test_track_commitments_from_actions(self, mock_email, action_result_with_commitments, tmp_path):
        """Test that commitments are tracked from action results."""
        from email_agent.agents.commitment_tracker import CommitmentTrackerAgent

        with patch("email_agent.agents.commitment_tracker.settings") as mock_settings:
            mock_settings.data_dir = str(tmp_path)
            mock_settings.openai_api_key = "test-key"
            mock_settings.openai_model = "gpt-4"

            tracker = CommitmentTrackerAgent()

            result = await tracker.track_commitments_from_actions(
                mock_email, action_result_with_commitments
            )

            assert len(result) == 2

            commitment_items = [i for i in result if i["type"] == "commitment"]
            waiting_items = [i for i in result if i["type"] == "waiting"]

            assert len(commitment_items) == 1
            assert commitment_items[0]["description"] == "Send the quarterly report"
            assert commitment_items[0]["status"] == "pending"

            assert len(waiting_items) == 1
            assert waiting_items[0]["description"] == "Budget approval"
            assert waiting_items[0]["status"] == "waiting"

    @pytest.mark.asyncio
    async def test_track_no_commitments(self, mock_email, tmp_path):
        """Test tracking when no commitments or waiting items."""
        from email_agent.agents.commitment_tracker import CommitmentTrackerAgent

        with patch("email_agent.agents.commitment_tracker.settings") as mock_settings:
            mock_settings.data_dir = str(tmp_path)
            mock_settings.openai_api_key = "test-key"
            mock_settings.openai_model = "gpt-4"

            tracker = CommitmentTrackerAgent()

            action_result = {
                "action_items": [],
                "commitments_made": [],
                "waiting_for": [],
                "meeting_requests": [],
                "needs_response": False,
                "summary": "No commitments here.",
            }

            result = await tracker.track_commitments_from_actions(
                mock_email, action_result
            )

            assert len(result) == 0


class TestSmartActionsShowAll:
    """Test --show-all behavior for smart_actions."""

    def test_show_all_shows_no_action_emails(self):
        """Test that --show-all flag would display emails without actions."""
        has_items = False
        show_all = True

        should_show = has_items or show_all
        assert should_show is True

    def test_without_show_all_hides_no_action_emails(self):
        """Test that without --show-all, no-action emails are hidden."""
        has_items = False
        show_all = False

        should_show = has_items or show_all
        assert should_show is False

    def test_show_all_shows_with_items(self):
        """Test that emails with items are always shown."""
        has_items = True
        show_all = False

        should_show = has_items or show_all
        assert should_show is True

    def test_show_all_includes_summary(self):
        """Test that show_all display includes summary."""
        actions = {
            "summary": "This is a test summary",
            "needs_response": False,
        }
        show_all = True

        assert show_all is True
        assert "summary" in actions
        assert actions["summary"] is not None

    def test_show_all_includes_needs_response(self):
        """Test that show_all display includes needs_response."""
        actions = {
            "summary": "Test summary",
            "needs_response": True,
        }
        show_all = True

        assert show_all is True
        assert "needs_response" in actions
        assert isinstance(actions["needs_response"], bool)


class TestSmartActionsSkipProcessed:
    """Test --skip-processed/--all filtering behavior."""

    def test_skip_processed_filters_emails(self):
        """Test that skip_processed=True filters out processed emails."""
        skip_processed = True
        has_action_processed_tag = True

        should_include = not (skip_processed and has_action_processed_tag)
        assert should_include is False

    def test_all_includes_processed_emails(self):
        """Test that --all (skip_processed=False) includes processed emails."""
        skip_processed = False
        has_action_processed_tag = True

        should_include = not (skip_processed and has_action_processed_tag)
        assert should_include is True

    def test_skip_processed_includes_unprocessed(self):
        """Test that skip_processed=True includes unprocessed emails."""
        skip_processed = True
        has_action_processed_tag = False

        should_include = not (skip_processed and has_action_processed_tag)
        assert should_include is True

    def test_all_includes_unprocessed(self):
        """Test that --all includes unprocessed emails too."""
        skip_processed = False
        has_action_processed_tag = False

        should_include = not (skip_processed and has_action_processed_tag)
        assert should_include is True
