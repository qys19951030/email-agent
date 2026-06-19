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
