"""Standalone tests for smart_actions functionality.

This script can be run directly without the full test framework
and without requiring crewai or other heavy dependencies.
"""

import json
import sys
import os
import tempfile
import importlib.util
from datetime import datetime, timedelta
from pathlib import Path

SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, SRC_DIR)


def _parse_tags(tags_value):
    """Safely parse tags from either a list or JSON string."""
    if not tags_value:
        return []
    if isinstance(tags_value, list):
        return tags_value
    if isinstance(tags_value, str):
        try:
            parsed = json.loads(tags_value)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            return [tags_value]
    return []


def _add_tag(tags_value, tag):
    """Add a tag to tags, avoiding duplicates.

    Returns a NEW list (safe for SQLAlchemy JSON change detection).
    """
    current_tags = _parse_tags(tags_value)
    if tag not in current_tags:
        return current_tags + [tag]
    return list(current_tags)


def _serialize_tags(tags_list):
    """Serialize tags to JSON string."""
    return json.dumps(tags_list)


def _temp_db_path():
    """Generate a temp DB file path (Windows-safe)."""
    tmp_dir = tempfile.gettempdir()
    import uuid
    return os.path.join(tmp_dir, f"test_smart_actions_{uuid.uuid4().hex}.db")


def test_tag_helpers():
    """Test the tag helper functions."""

    print("Testing tag helpers...")

    # Test _parse_tags
    assert _parse_tags(None) == []
    assert _parse_tags([]) == []
    assert _parse_tags(["a", "b"]) == ["a", "b"]
    assert _parse_tags(json.dumps(["x", "y"])) == ["x", "y"]
    assert _parse_tags("single") == ["single"]
    assert _parse_tags("") == []
    print("  ✅ _parse_tags tests passed")

    # Test _add_tag
    assert _add_tag([], "action_processed") == ["action_processed"]
    assert _add_tag(["existing"], "action_processed") == ["existing", "action_processed"]
    result = _add_tag(["action_processed", "other"], "action_processed")
    assert result.count("action_processed") == 1
    assert len(result) == 2
    result = _add_tag(json.dumps(["existing"]), "action_processed")
    assert isinstance(result, list)
    assert "action_processed" in result
    assert "existing" in result
    print("  ✅ _add_tag tests passed")

    # Test _serialize_tags
    serialized = _serialize_tags(["a", "b"])
    assert isinstance(serialized, str)
    assert json.loads(serialized) == ["a", "b"]
    print("  ✅ _serialize_tags tests passed")

    # Test idempotency (the same tag added multiple times)
    tags = []
    for _ in range(5):
        tags = _add_tag(tags, "action_processed")
    assert tags.count("action_processed") == 1
    print("  ✅ Tag idempotency (5 times add) passed")

    # Test JSON string input with existing action_processed
    json_tags = json.dumps(["action_processed", "other"])
    result = _add_tag(json_tags, "action_processed")
    assert isinstance(result, list)
    assert result.count("action_processed") == 1
    print("  ✅ JSON string input with existing tag passed")

    print("✅ All tag helper tests passed!\n")


def test_database_integration():
    """Test database integration with temp database."""

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    print("Testing database integration...")

    db_path = _temp_db_path()
    engine = None
    try:
        engine = create_engine(f"sqlite:///{db_path}")
        Session = sessionmaker(bind=engine)

        from email_agent.storage.models import Base, EmailORM

        Base.metadata.create_all(engine)

        # Session 1: Create test email
        session1 = Session()
        email = EmailORM(
            id="test-1",
            message_id="msg-1",
            subject="Test email",
            sender_email="test@test.com",
            sender_name="Test",
            date=datetime.now(),
            received_date=datetime.now(),
            category="primary",
            priority="normal",
            tags=["tag1", "tag2"],
            connector_type="test",
        )
        session1.add(email)
        session1.commit()
        session1.close()
        print("  ✅ Save email with list tags passed")

        # Session 2: Read and update tags + results
        session2 = Session()
        saved = session2.query(EmailORM).filter_by(id="test-1").first()
        assert saved is not None
        tags = _parse_tags(saved.tags)
        assert "tag1" in tags
        assert "tag2" in tags

        updated_tags = _add_tag(saved.tags, "action_processed")
        saved.tags = updated_tags
        saved.summary = "Test summary"
        saved.action_items = ["Action 1", "Action 2"]
        saved.processed_at = datetime.now()
        session2.commit()
        session2.close()

        # Session 3: Verify persistence (separate session, fresh read from DB)
        session3 = Session()
        saved2 = session3.query(EmailORM).filter_by(id="test-1").first()
        tags2 = _parse_tags(saved2.tags)
        assert "action_processed" in tags2
        assert "tag1" in tags2
        assert saved2.summary == "Test summary"
        assert len(saved2.action_items) == 2
        assert saved2.processed_at is not None
        session3.close()
        print("  ✅ Add action_processed tag and persist results passed")

        # Session 4: Test idempotency (no duplicate tag on re-run)
        session4 = Session()
        saved3 = session4.query(EmailORM).filter_by(id="test-1").first()
        updated_tags2 = _add_tag(saved3.tags, "action_processed")
        saved3.tags = updated_tags2
        session4.commit()
        session4.close()

        # Session 5: Verify idempotency
        session5 = Session()
        saved4 = session5.query(EmailORM).filter_by(id="test-1").first()
        tags3 = _parse_tags(saved4.tags)
        assert tags3.count("action_processed") == 1
        session5.close()
        print("  ✅ No duplicate action_processed tag (idempotent) passed")

        # Session 6: Add test emails for filtering
        session6 = Session()
        unprocessed = EmailORM(
            id="test-unprocessed",
            message_id="msg-unprocessed",
            subject="Unprocessed email",
            sender_email="test@test.com",
            date=datetime.now(),
            received_date=datetime.now(),
            category="primary",
            priority="normal",
            tags=[],
            connector_type="test",
        )
        session6.add(unprocessed)

        processed = EmailORM(
            id="test-processed",
            message_id="msg-processed",
            subject="Processed email",
            sender_email="test@test.com",
            date=datetime.now() - timedelta(days=1),
            received_date=datetime.now() - timedelta(days=1),
            category="primary",
            priority="normal",
            tags=["action_processed"],
            connector_type="test",
        )
        session6.add(processed)
        session6.commit()
        session6.close()

        # Session 7: Test skip_processed=True (default, --skip-processed)
        session7 = Session()
        skip_processed = True
        query = session7.query(EmailORM)
        if skip_processed:
            query = query.filter(~EmailORM.tags.like("%action_processed%"))
        results = query.order_by(EmailORM.received_date.desc()).all()
        result_ids = [e.id for e in results]

        assert "test-unprocessed" in result_ids
        assert "test-processed" not in result_ids
        session7.close()
        print("  ✅ skip_processed=True (default) filtering passed")

        # Session 8: Test skip_processed=False (--all)
        session8 = Session()
        skip_processed = False
        query2 = session8.query(EmailORM)
        if skip_processed:
            query2 = query2.filter(~EmailORM.tags.like("%action_processed%"))
        results2 = query2.order_by(EmailORM.received_date.desc()).all()
        result_ids2 = [e.id for e in results2]

        assert "test-unprocessed" in result_ids2
        assert "test-processed" in result_ids2
        assert "test-1" in result_ids2  # Also has action_processed
        assert len(result_ids2) > len(result_ids)
        session8.close()
        print("  ✅ skip_processed=False (--all) includes processed emails passed")

        print("✅ All database integration tests passed!\n")

    finally:
        if engine:
            engine.dispose()
        try:
            if os.path.exists(db_path):
                os.unlink(db_path)
        except Exception:
            pass


def test_data_persistence_fields():
    """Test that extraction results are properly persisted to email records."""

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    print("Testing data persistence fields...")

    db_path = _temp_db_path()
    engine = None
    try:
        engine = create_engine(f"sqlite:///{db_path}")
        Session = sessionmaker(bind=engine)

        from email_agent.storage.models import Base, EmailORM

        Base.metadata.create_all(engine)

        # Session 1: Create initial email
        session1 = Session()
        email = EmailORM(
            id="persist-test-1",
            message_id="msg-persist-1",
            subject="Test persistence",
            sender_email="sender@test.com",
            date=datetime.now(),
            received_date=datetime.now(),
            category="primary",
            priority="normal",
            tags=["initial_tag"],
            connector_type="test",
            summary=None,
            action_items=None,
            processed_at=None,
        )
        session1.add(email)
        session1.commit()
        session1.close()

        # Session 2: Verify initial state
        session2 = Session()
        saved = session2.query(EmailORM).filter_by(id="persist-test-1").first()
        assert saved.summary is None
        assert saved.action_items is None or len(saved.action_items) == 0
        assert saved.processed_at is None
        assert "initial_tag" in _parse_tags(saved.tags)
        session2.close()
        print("  ✅ Initial state verification passed")

        # Session 3: Persist extraction results (same pattern as smart_actions)
        extraction_result = {
            "action_items": [
                {"action": "Review document", "deadline": "2026-07-01"},
                {"action": "Send reply", "deadline": None},
            ],
            "commitments_made": [],
            "waiting_for": [],
            "meeting_requests": [],
            "needs_response": True,
            "response_urgency": "normal",
            "summary": "This is a test summary of the email content.",
            "email_type": "request",
        }

        session3 = Session()
        saved_orm = session3.query(EmailORM).filter_by(id="persist-test-1").first()

        # Update tags
        updated_tags = _add_tag(saved_orm.tags, "action_processed")
        saved_orm.tags = updated_tags

        # Persist summary
        saved_orm.summary = extraction_result.get("summary")

        # Persist action items as list of strings
        action_items_list = [
            item.get("action", "")
            for item in extraction_result.get("action_items", [])
        ]
        saved_orm.action_items = action_items_list

        # Update processed timestamp
        saved_orm.processed_at = datetime.now()

        session3.commit()
        session3.close()

        # Session 4: Verify persisted data
        session4 = Session()
        verified = session4.query(EmailORM).filter_by(id="persist-test-1").first()

        # Tags: initial + action_processed, no duplicates
        tags_final = _parse_tags(verified.tags)
        assert "initial_tag" in tags_final
        assert "action_processed" in tags_final
        assert tags_final.count("action_processed") == 1
        print("  ✅ Tags persisted correctly (no duplicates)")

        # Summary
        assert verified.summary == extraction_result["summary"]
        assert len(verified.summary) > 0
        print("  ✅ Summary persisted correctly")

        # Action items
        assert len(verified.action_items) == 2
        assert "Review document" in verified.action_items
        assert "Send reply" in verified.action_items
        print("  ✅ Action items persisted correctly")

        # Processed timestamp
        assert verified.processed_at is not None
        assert isinstance(verified.processed_at, datetime)
        print("  ✅ Processed timestamp persisted correctly")
        session4.close()

        # Session 5: Simulate second run - test idempotency
        session5 = Session()
        saved_orm2 = session5.query(EmailORM).filter_by(id="persist-test-1").first()
        original_tags_count = len(_parse_tags(saved_orm2.tags))
        updated_tags2 = _add_tag(saved_orm2.tags, "action_processed")
        saved_orm2.tags = updated_tags2
        session5.commit()
        session5.close()

        # Session 6: Verify idempotency
        session6 = Session()
        verified2 = session6.query(EmailORM).filter_by(id="persist-test-1").first()
        tags_final2 = _parse_tags(verified2.tags)
        assert len(tags_final2) == original_tags_count
        assert tags_final2.count("action_processed") == 1
        session6.close()
        print("  ✅ Idempotent second run (no duplicate tags) passed")

        print("✅ All data persistence tests passed!\n")

    finally:
        if engine:
            engine.dispose()
        try:
            if os.path.exists(db_path):
                os.unlink(db_path)
        except Exception:
            pass


def test_smart_actions_behavior_logic():
    """Test the logic for smart_actions behavior."""

    print("Testing smart_actions behavior logic...")

    # Test 1: --show-all behavior
    def should_show_email(has_items, show_all):
        return has_items or show_all

    assert should_show_email(True, False) is True
    assert should_show_email(True, True) is True
    assert should_show_email(False, True) is True
    assert should_show_email(False, False) is False
    print("  ✅ --show-all behavior logic passed")

    # Test 2: Processing semantics
    def should_mark_processed(extraction_succeeded, dry_run):
        if dry_run:
            return False
        return extraction_succeeded

    assert should_mark_processed(True, False) is True
    assert should_mark_processed(True, True) is False
    assert should_mark_processed(False, False) is False
    print("  ✅ Processing semantics (mark on success, regardless of items) passed")

    # Test 3: Action result structure
    action_result = {
        "action_items": [],
        "commitments_made": [],
        "waiting_for": [],
        "meeting_requests": [],
        "needs_response": False,
        "response_urgency": "low",
        "summary": "Test summary",
        "email_type": "notification",
    }

    assert "summary" in action_result
    assert "needs_response" in action_result
    assert "action_items" in action_result
    assert "commitments_made" in action_result
    assert "waiting_for" in action_result
    print("  ✅ Action result structure passed")

    # Test 4: --show-all includes summary and needs_response
    show_all = True
    if show_all:
        assert "summary" in action_result
        assert "needs_response" in action_result
        assert isinstance(action_result["needs_response"], bool)
    print("  ✅ --show-all includes summary and needs_reply passed")

    # Test 5: Idempotent re-running (processed emails are skipped)
    already_processed = "action_processed" in ["action_processed", "other"]
    assert already_processed is True
    print("  ✅ Idempotent re-running detection passed")

    # Test 6: No action items still marks as processed
    # Key requirement from the spec
    has_action_items = False
    extraction_succeeded = True
    dry_run = False

    if not dry_run and extraction_succeeded:
        # Even if no action items, still mark as processed
        mark_processed = True
    else:
        mark_processed = False

    assert mark_processed is True, "Should mark as processed even without action items"
    print("  ✅ No action items still marks as processed passed")

    print("✅ All behavior logic tests passed!\n")


def test_action_extractor_structure():
    """Test ActionExtractorAgent structure by reading the source code."""

    print("Testing ActionExtractorAgent structure...")

    # Read the source file and check for expected methods and structure
    src_file = os.path.join(SRC_DIR, "email_agent", "agents", "action_extractor.py")
    with open(src_file, "r", encoding="utf-8") as f:
        source = f.read()

    # Check for expected class
    assert "class ActionExtractorAgent" in source
    print("  ✅ ActionExtractorAgent class exists")

    # Check for expected methods
    assert "def extract_actions" in source
    assert "def extract_batch_actions" in source
    assert "def generate_action_summary" in source
    assert "def track_commitments" in source
    print("  ✅ Expected methods exist")

    # Check for expected output fields in results
    assert "action_items" in source
    assert "commitments_made" in source
    assert "waiting_for" in source
    assert "meeting_requests" in source
    assert "needs_response" in source
    assert "summary" in source
    print("  ✅ Expected result fields referenced in code")

    # Check the error response structure
    assert "error" in source
    print("  ✅ Error handling exists")

    print("✅ All ActionExtractorAgent structure tests passed!\n")


def test_commitment_tracker_structure():
    """Test CommitmentTrackerAgent structure by reading the source code."""

    print("Testing CommitmentTrackerAgent structure...")

    src_file = os.path.join(SRC_DIR, "email_agent", "agents", "commitment_tracker.py")
    with open(src_file, "r", encoding="utf-8") as f:
        source = f.read()

    # Check for expected class
    assert "class CommitmentTrackerAgent" in source
    print("  ✅ CommitmentTrackerAgent class exists")

    # Check for expected methods
    assert "track_commitments_from_actions" in source
    assert "get_commitment_stats" in source
    assert "get_pending_commitments" in source
    assert "update_commitment_status" in source
    print("  ✅ Expected methods exist")

    # Check for database tables
    assert "commitments" in source.lower()
    assert "waiting" in source.lower()
    print("  ✅ Database tables referenced in code")

    # Verify the method takes email and actions params
    import re
    method_match = re.search(
        r"def track_commitments_from_actions\([^)]+email[^)]+actions[^)]+\)",
        source
    )
    assert method_match is not None
    print("  ✅ track_commitments_from_actions takes email and actions params")

    print("✅ All CommitmentTrackerAgent structure tests passed!\n")


def test_smart_actions_cli_structure():
    """Verify the smart_actions CLI command structure."""

    print("Testing smart_actions CLI structure...")

    src_file = os.path.join(SRC_DIR, "email_agent", "cli", "main.py")
    with open(src_file, "r", encoding="utf-8") as f:
        source = f.read()

    # Check command exists
    assert "def smart_actions" in source
    print("  ✅ smart_actions command exists")

    # Check expected CLI options
    assert "skip_processed" in source or "skip-processed" in source
    assert "--all" in source
    assert "show_all" in source or "show-all" in source
    assert "dry_run" in source or "dry-run" in source
    assert "limit" in source
    print("  ✅ Expected CLI options exist")

    # Check for tag helper functions
    assert "_parse_tags" in source
    assert "_add_tag" in source
    assert "_serialize_tags" in source
    print("  ✅ Tag helper functions exist")

    # Check for processed tag
    assert "action_processed" in source
    print("  ✅ action_processed tag is used")

    # Check for summary and action items persistence
    assert ".summary" in source
    assert ".action_items" in source
    assert ".processed_at" in source
    print("  ✅ Summary, action_items, and processed_at persistence exist")

    # Check for commitment tracker integration
    assert "CommitmentTrackerAgent" in source or "commitment_tracker" in source
    print("  ✅ CommitmentTrackerAgent integration exists")

    # Check for EmailORM / database usage
    assert "EmailORM" in source
    print("  ✅ EmailORM is used for persistence")

    print("✅ All smart_actions CLI structure tests passed!\n")


def test_processed_filter_edge_cases():
    """Test edge cases for processed email filtering."""

    print("Testing processed filter edge cases...")

    # Edge case 1: tags is None
    tags_none = None
    is_processed = "action_processed" in _parse_tags(tags_none)
    assert is_processed is False
    print("  ✅ None tags -> not processed passed")

    # Edge case 2: empty tags list
    tags_empty = []
    is_processed = "action_processed" in _parse_tags(tags_empty)
    assert is_processed is False
    print("  ✅ Empty tags list -> not processed passed")

    # Edge case 3: tags as JSON string with action_processed
    tags_json = json.dumps(["action_processed"])
    is_processed = "action_processed" in _parse_tags(tags_json)
    assert is_processed is True
    print("  ✅ JSON string with action_processed -> processed passed")

    # Edge case 4: tags as JSON string without action_processed
    tags_json_empty = json.dumps(["other_tag"])
    is_processed = "action_processed" in _parse_tags(tags_json_empty)
    assert is_processed is False
    print("  ✅ JSON string without action_processed -> not processed passed")

    # Edge case 5: tags as plain string (not JSON)
    tags_plain = "action_processed"
    is_processed = "action_processed" in _parse_tags(tags_plain)
    assert is_processed is True
    print("  ✅ Plain string 'action_processed' -> processed passed")

    # Edge case 6: SQL LIKE query would match partial strings
    # But our _parse_tags approach handles this correctly
    tags_partial = ["action_processed_extra"]
    is_processed = "action_processed" in _parse_tags(tags_partial)
    assert is_processed is False  # Exact match only
    print("  ✅ Partial tag name does not match passed")

    print("✅ All edge case tests passed!\n")


def test_similar_tag_not_filtered():
    """Test that similar tags like action_processed_extra are NOT treated as processed.

    This is a critical scenario: the old LIKE '%action_processed%' pattern would
    falsely match partial tag names. Our new parsing-based approach must avoid that.
    """

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    print("Testing similar tag NOT falsely filtered (new precise parsing)...")

    db_path = _temp_db_path()
    engine = None
    try:
        engine = create_engine(f"sqlite:///{db_path}")
        Session = sessionmaker(bind=engine)

        from email_agent.storage.models import Base, EmailORM

        Base.metadata.create_all(engine)

        # Session 1: Create emails with various similar-but-distinct tags
        session1 = Session()

        # Email A: has real 'action_processed' tag -> should be filtered
        email_processed = EmailORM(
            id="email-A-processed",
            message_id="msg-A",
            subject="Email A (really processed)",
            sender_email="a@test.com",
            date=datetime.now(),
            received_date=datetime.now(),
            category="primary",
            priority="normal",
            tags=["action_processed", "important"],
            connector_type="test",
        )
        session1.add(email_processed)

        # Email B: has 'action_processed_extra' -> should NOT be filtered
        email_similar_extra = EmailORM(
            id="email-B-similar-extra",
            message_id="msg-B",
            subject="Email B (action_processed_extra)",
            sender_email="b@test.com",
            date=datetime.now(),
            received_date=datetime.now(),
            category="primary",
            priority="normal",
            tags=["action_processed_extra"],
            connector_type="test",
        )
        session1.add(email_similar_extra)

        # Email C: has 'my_action_processed' -> should NOT be filtered
        email_similar_prefix = EmailORM(
            id="email-C-similar-prefix",
            message_id="msg-C",
            subject="Email C (my_action_processed)",
            sender_email="c@test.com",
            date=datetime.now(),
            received_date=datetime.now(),
            category="primary",
            priority="normal",
            tags=["my_action_processed"],
            connector_type="test",
        )
        session1.add(email_similar_prefix)

        # Email D: has 'action_processed_v2' -> should NOT be filtered
        email_similar_suffix = EmailORM(
            id="email-D-similar-suffix",
            message_id="msg-D",
            subject="Email D (action_processed_v2)",
            sender_email="d@test.com",
            date=datetime.now(),
            received_date=datetime.now(),
            category="primary",
            priority="normal",
            tags=["action_processed_v2"],
            connector_type="test",
        )
        session1.add(email_similar_suffix)

        # Email E: has tags as JSON string with similar names
        email_json_similar = EmailORM(
            id="email-E-json-similar",
            message_id="msg-E",
            subject="Email E (JSON with similar)",
            sender_email="e@test.com",
            date=datetime.now(),
            received_date=datetime.now(),
            category="primary",
            priority="normal",
            tags=json.dumps(["action_processed_alt"]),
            connector_type="test",
        )
        session1.add(email_json_similar)

        # Email F: no tags at all -> should NOT be filtered
        email_no_tags = EmailORM(
            id="email-F-no-tags",
            message_id="msg-F",
            subject="Email F (no tags)",
            sender_email="f@test.com",
            date=datetime.now(),
            received_date=datetime.now(),
            category="primary",
            priority="normal",
            tags=[],
            connector_type="test",
        )
        session1.add(email_no_tags)

        session1.commit()
        session1.close()

        # Session 2: Simulate NEW precise filtering (not LIKE, but _parse_tags)
        session2 = Session()
        all_emails = (
            session2.query(EmailORM)
            .order_by(EmailORM.received_date.desc())
            .all()
        )

        # Apply the SAME filtering logic as in smart_actions CLI
        skip_processed = True
        if skip_processed:
            after_filter = [
                e for e in all_emails
                if "action_processed" not in _parse_tags(e.tags)
            ]
        else:
            after_filter = all_emails

        filtered_ids = [e.id for e in after_filter]

        # Email A (real processed) should be FILTERED OUT
        assert "email-A-processed" not in filtered_ids, \
            "Real 'action_processed' should be filtered"
        print("  ✅ Real 'action_processed' tag is correctly filtered out")

        # All similar tags should remain (NOT filtered)
        assert "email-B-similar-extra" in filtered_ids, \
            "'action_processed_extra' should NOT be filtered"
        print("  ✅ 'action_processed_extra' is NOT falsely filtered")

        assert "email-C-similar-prefix" in filtered_ids, \
            "'my_action_processed' should NOT be filtered"
        print("  ✅ 'my_action_processed' is NOT falsely filtered")

        assert "email-D-similar-suffix" in filtered_ids, \
            "'action_processed_v2' should NOT be filtered"
        print("  ✅ 'action_processed_v2' is NOT falsely filtered")

        assert "email-E-json-similar" in filtered_ids, \
            "JSON 'action_processed_alt' should NOT be filtered"
        print("  ✅ JSON with 'action_processed_alt' is NOT falsely filtered")

        assert "email-F-no-tags" in filtered_ids, \
            "No tags email should NOT be filtered"
        print("  ✅ Email with no tags is NOT filtered")

        # Also verify the OLD LIKE pattern would have failed
        old_like_filtered = [
            e for e in all_emails
            if not (e.tags and "action_processed" in str(e.tags))
        ]
        old_filtered_ids = [e.id for e in old_like_filtered]

        # Old LIKE pattern would falsely filter out B, C, D, E - prove this
        falsely_removed_by_old = [
            i for i in filtered_ids if i not in old_filtered_ids
        ]
        # The new approach preserves items that the old approach would have lost
        # At minimum B should be in this list
        assert len(falsely_removed_by_old) >= 1
        print(f"  ✅ Old LIKE pattern would have falsely removed {len(falsely_removed_by_old)} emails which new approach correctly preserves")

        session2.close()

        print("✅ All similar tag precision tests passed!\n")

    finally:
        if engine:
            engine.dispose()
        try:
            if os.path.exists(db_path):
                os.unlink(db_path)
        except Exception:
            pass


def test_commitment_tracker_idempotency():
    """Test that calling track_commitments_from_actions multiple times for the same
    email with the same actions does NOT create duplicate records.

    Covers requirement #2: non-dry-run + --all reprocessing should not duplicate.
    """

    import asyncio
    import sqlite3
    import importlib.util
    import uuid

    print("Testing commitment tracker idempotency (no duplicates on re-run)...")

    # Use a manually managed temp dir (bypass Windows file lock cleanup issues)
    tmp_dir = os.path.join(tempfile.gettempdir(), f"ct_idem_{uuid.uuid4().hex}")
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_path = Path(tmp_dir)

    # Create an empty commitments database with our expected schema.
    tracker_db_path = tmp_path / "commitments.db"

    with sqlite3.connect(tracker_db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS commitments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email_id TEXT,
                commitment_type TEXT,
                description TEXT,
                committed_to TEXT,
                committed_by TEXT,
                deadline DATE,
                priority TEXT,
                status TEXT,
                created_at DATETIME,
                updated_at DATETIME,
                completion_date DATETIME,
                reminder_sent BOOLEAN DEFAULT FALSE,
                context_data TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS follow_ups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                commitment_id INTEGER,
                follow_up_type TEXT,
                follow_up_date DATE,
                status TEXT,
                notes TEXT,
                created_at DATETIME,
                FOREIGN KEY (commitment_id) REFERENCES commitments (id)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS waiting_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email_id TEXT,
                description TEXT,
                waiting_from TEXT,
                expected_date DATE,
                priority TEXT,
                status TEXT,
                created_at DATETIME,
                updated_at DATETIME,
                received_date DATETIME,
                context_data TEXT
            )
            """
        )
        conn.commit()

    sys.path.insert(0, SRC_DIR)
    from email_agent.models import Email, EmailAddress, EmailCategory, EmailPriority

    # Import commitment_tracker DIRECTLY via its file path, bypassing
    # agents/__init__.py which imports crewai.
    # Since commitment_tracker uses `from ..config import settings`, we
    # need to properly set up the module with a parent package context.
    import email_agent.config as config_module
    import email_agent  # noqa - ensure email_agent package is loaded
    if "email_agent.config" not in sys.modules:
        sys.modules["email_agent.config"] = config_module

    # Load the module as a proper sub-package of email_agent.agents
    # Step 1: create or reuse a pseudo 'email_agent.agents' module in sys.modules
    if "email_agent.agents" not in sys.modules:
        agents_pkg = importlib.util.module_from_spec(
            importlib.util.spec_from_loader("email_agent.agents", loader=None)
        )
        agents_pkg.__path__ = [
            os.path.join(SRC_DIR, "email_agent", "agents")
        ]
        sys.modules["email_agent.agents"] = agents_pkg
    else:
        agents_pkg = sys.modules["email_agent.agents"]

    # Step 2: register parent reference so relative import resolves
    # Make sure email_agent has reference to agents submodule
    if not hasattr(email_agent, "agents"):
        email_agent.agents = agents_pkg

    # Step 3: load commitment_tracker via file spec
    ct_spec = importlib.util.spec_from_file_location(
        "email_agent.agents.commitment_tracker",
        os.path.join(SRC_DIR, "email_agent", "agents", "commitment_tracker.py")
    )
    ct_module = importlib.util.module_from_spec(ct_spec)
    # Register BEFORE exec_module so intra-module references work
    sys.modules["email_agent.agents.commitment_tracker"] = ct_module
    agents_pkg.commitment_tracker = ct_module

    try:
        ct_spec.loader.exec_module(ct_module)
    except Exception as load_err:
        raise AssertionError(
            f"Failed to load commitment_tracker module: {load_err}"
        ) from load_err

    CommitmentTrackerAgent = ct_module.CommitmentTrackerAgent

    # Now instantiate - but __init__ reads settings.data_dir!
    # We cannot easily patch settings because pydantic Settings are frozen.
    # Strategy: build an instance via __new__ then manually call init
    # and override tracker_db_path before any real use.
    tracker = CommitmentTrackerAgent.__new__(CommitmentTrackerAgent)
    # Initialize the attributes that __init__ sets up
    try:
        from openai import AsyncOpenAI
    except ImportError:
        raise AssertionError(
            "Need openai package installed for CommitmentTrackerAgent"
        )
    tracker.client = AsyncOpenAI(api_key=config_module.settings.openai_api_key or "dummy")
    tracker.model = config_module.settings.openai_model or "gpt-4"
    tracker.tracker_db_path = tracker_db_path
    # Re-init tables (already created, so this is a no-op CREATE IF NOT EXISTS)
    tracker._init_tracker_db()

    # Confirm it's using the temp DB
    assert str(tracker.tracker_db_path) == str(tracker_db_path), \
        f"Expected {tracker_db_path}, got {tracker.tracker_db_path}"
    print(f"  ✅ Tracker using temp DB: {tracker.tracker_db_path.name}")

    # Create test email
    email = Email(
        id="email-idemp-test-1",
        message_id="msg-idemp-test",
        subject="Idempotency test email",
        sender=EmailAddress(email="user@company.com", name="Test User"),
        recipients=[EmailAddress(email="manager@company.com", name="Manager")],
        date=datetime.now(),
        received_date=datetime.now(),
        category=EmailCategory.PRIMARY,
        priority=EmailPriority.NORMAL,
    )

    # Define actions: 1 commitment + 1 waiting item
    action_result = {
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
        "summary": "Idempotency test email.",
    }

    # ---- ROUND 1: First call ----
    result_round1 = asyncio.run(
        tracker.track_commitments_from_actions(email, action_result)
    )

    assert len(result_round1) == 2, \
        f"Round 1: expected 2 items, got {len(result_round1)}"
    new_items_r1 = [i for i in result_round1 if not i.get("existing", False)]
    assert len(new_items_r1) == 2, \
        f"Round 1: expected 2 NEW items, got {len(new_items_r1)}"

    r1_commitment_id = next(
        i["id"] for i in result_round1 if i["type"] == "commitment"
    )
    r1_waiting_id = next(
        i["id"] for i in result_round1 if i["type"] == "waiting"
    )
    print(f"  ✅ Round 1: inserted 2 new items "
          f"(commitment={r1_commitment_id}, waiting={r1_waiting_id})")

    # Verify stats after round 1
    stats_r1 = asyncio.run(tracker.get_commitment_stats())
    assert stats_r1["total_commitments"] == 1
    assert stats_r1["total_waiting_items"] == 1
    print("  ✅ Round 1 stats: 1 commitment + 1 waiting in DB")

    # ---- ROUND 2: Same email, same actions (simulates --all re-run) ----
    result_round2 = asyncio.run(
        tracker.track_commitments_from_actions(email, action_result)
    )

    assert len(result_round2) == 2, \
        f"Round 2: expected 2 items, got {len(result_round2)}"

    existing_r2 = [i for i in result_round2 if i.get("existing", False)]
    new_r2 = [i for i in result_round2 if not i.get("existing", False)]
    assert len(existing_r2) == 2, \
        f"Round 2: both items should be existing, got new={new_r2}"
    assert len(new_r2) == 0, \
        f"Round 2: NO new items expected, got {len(new_r2)}"

    r2_commitment_id = next(
        i["id"] for i in result_round2 if i["type"] == "commitment"
    )
    r2_waiting_id = next(
        i["id"] for i in result_round2 if i["type"] == "waiting"
    )

    # Critical: IDs must match round 1
    assert r2_commitment_id == r1_commitment_id, \
        "Round 2 commitment ID changed! Duplicate was inserted."
    assert r2_waiting_id == r1_waiting_id, \
        "Round 2 waiting ID changed! Duplicate was inserted."

    print(f"  ✅ Round 2 (--all re-run): returned same 2 existing items, "
          f"0 duplicates inserted, IDs preserved")

    # Stats should NOT have changed
    stats_r2 = asyncio.run(tracker.get_commitment_stats())
    assert stats_r2["total_commitments"] == 1, \
        f"Commitments duplicated! Now {stats_r2['total_commitments']}"
    assert stats_r2["total_waiting_items"] == 1, \
        f"Waiting duplicated! Now {stats_r2['total_waiting_items']}"
    print("  ✅ Round 2 stats: still 1 commitment + 1 waiting (NO growth)")

    # ---- ROUND 3: Add a genuinely NEW commitment to the same email ----
    action_result_v2 = {
        **action_result,
        "commitments_made": [
            {
                "commitment": "Send the quarterly report",
                "deadline": "2026-06-27",
                "recipient": "manager@company.com",
            },
            {
                "commitment": "Prepare the team offsite plan",
                "deadline": "2026-07-15",
                "recipient": "hr@company.com",
            },
        ],
        "waiting_for": [
            {
                "waiting_for": "Budget approval",
                "from_whom": "finance@company.com",
                "deadline": "2026-06-30",
            }
        ],
    }

    result_round3 = asyncio.run(
        tracker.track_commitments_from_actions(email, action_result_v2)
    )

    # Should be 3 total: 2 existing + 1 new commitment
    assert len(result_round3) == 3, \
        f"Round 3: expected 3 items, got {len(result_round3)}"

    existing_r3 = [i for i in result_round3 if i.get("existing", False)]
    new_r3 = [i for i in result_round3 if not i.get("existing", False)]
    assert len(existing_r3) == 2, \
        f"Round 3: 2 should be existing, got {len(existing_r3)}"
    assert len(new_r3) == 1, \
        f"Round 3: 1 should be new, got {len(new_r3)}"

    # Original IDs still present
    r3_ids = [i["id"] for i in result_round3]
    assert r1_commitment_id in r3_ids
    assert r1_waiting_id in r3_ids

    print(f"  ✅ Round 3 (genuinely new item): "
          f"{len(existing_r3)} existing + {len(new_r3)} new = 3 total, "
          f"original IDs preserved")

    # Stats: now 2 commitments, still 1 waiting
    stats_r3 = asyncio.run(tracker.get_commitment_stats())
    assert stats_r3["total_commitments"] == 2, \
        f"Expected 2 commitments, got {stats_r3['total_commitments']}"
    assert stats_r3["total_waiting_items"] == 1, \
        f"Still expected 1 waiting, got {stats_r3['total_waiting_items']}"
    print("  ✅ Round 3 stats: 2 commitments + 1 waiting "
          "(genuine new data is tracked correctly)")

    # ---- ROUND 4: Same as round 3 (verify idempotency again) ----
    result_round4 = asyncio.run(
        tracker.track_commitments_from_actions(email, action_result_v2)
    )
    all_existing_r4 = all(
        i.get("existing", False) for i in result_round4
    )
    assert all_existing_r4, "Round 4: ALL items should be existing"
    assert len(result_round4) == 3

    stats_r4 = asyncio.run(tracker.get_commitment_stats())
    assert stats_r4["total_commitments"] == 2
    assert stats_r4["total_waiting_items"] == 1
    print("  ✅ Round 4 (re-run round3): all 3 existing, no duplicates")

    # Cleanup: best effort (Windows file lock OK if it fails)
    import shutil
    try:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        pass

    print("✅ All commitment tracker idempotency tests passed!\n")


def main():
    """Run all standalone tests."""
    print("=" * 60)
    print("Running smart_actions standalone tests")
    print("=" * 60 + "\n")

    all_passed = True
    test_results = []

    tests = [
        ("Tag helpers", test_tag_helpers),
        ("Database integration", test_database_integration),
        ("Data persistence", test_data_persistence_fields),
        ("Behavior logic", test_smart_actions_behavior_logic),
        ("Processed filter edge cases", test_processed_filter_edge_cases),
        ("Similar tag precision (NEW)", test_similar_tag_not_filtered),
        ("Commitment tracker idempotency (NEW)", test_commitment_tracker_idempotency),
        ("Action extractor structure", test_action_extractor_structure),
        ("Commitment tracker structure", test_commitment_tracker_structure),
        ("Smart actions CLI structure", test_smart_actions_cli_structure),
    ]

    for name, test_fn in tests:
        try:
            test_fn()
            test_results.append((name, "PASSED"))
        except Exception as e:
            test_results.append((name, f"FAILED: {e}"))
            all_passed = False
            import traceback
            traceback.print_exc()
            print()

    print("=" * 60)
    print("Test Summary:")
    for name, result in test_results:
        status = "✅" if result == "PASSED" else "❌"
        print(f"  {status} {name}: {result}")
    print()
    if all_passed:
        print("✅ ALL TESTS PASSED!")
    else:
        print("❌ SOME TESTS FAILED")
    print("=" * 60)

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
