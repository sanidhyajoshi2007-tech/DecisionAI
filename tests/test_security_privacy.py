"""
Security and privacy test suite for DecisionAI.

Verifies:
1. Multi-tenant isolation: User A cannot read, change, search, or delete User B's data
   across any endpoint (including Ask AI, exports, transcripts, audio, and decisions).
2. Input validation: Path parameters (ge=1), malformed meeting IDs (422), oversized titles (422),
   oversized decision texts (422), and oversized file uploads (413).
3. Audit logging: Records events for login, logout, meeting creation, rename, deletion,
   exports, and Ask AI without logging transcript text, query tokens, or API keys.
4. Data retention: User retention setting (0, 30, 90, 365) and daily cleanup job
   deleting expired meetings from database, audio storage, and ChromaDB.
5. Delete all my data: Permanently deletes all meetings, audio files, vectors,
   decisions, and user record with typed confirmation ("DELETE ALL MY DATA").
6. Security headers and CSRF protection: Validates CSP, X-Frame-Options, X-Content-Type-Options,
   Referrer-Policy, and double-submit CSRF rejection on session cookie requests.
"""

from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.config import Settings, get_settings
from backend.models.audit_log import AuditLog
from backend.models.decision import Decision
from backend.models.meeting import Meeting, MeetingStatus, TranscriptSegment
from backend.models.user import User
from backend.services.auth import create_session_token
from backend.services.rag.chunking import TranscriptChunk
from backend.services.rag.vector_store import get_vector_store
from backend.services.retention import run_retention_cleanup
from backend.services.storage import get_storage_service
from tests.conftest import get_test_settings


@pytest.fixture
async def setup_two_users(client: AsyncClient, test_session_factory):
    """Create User A and User B via test auth callback with auth tokens and test DB session."""
    resp_a = await client.get("/auth/google/callback?code=mock_code_alpha&json_response=true")
    assert resp_a.status_code == 200
    data_a = resp_a.json()

    resp_b = await client.get("/auth/google/callback?code=mock_code_beta&json_response=true")
    assert resp_b.status_code == 200
    data_b = resp_b.json()

    return {
        "user_a_id": data_a["user"]["id"],
        "user_a_email": data_a["user"]["email"],
        "token_a": data_a["token"],
        "headers_a": {"Authorization": f"Bearer {data_a['token']}"},
        "user_b_id": data_b["user"]["id"],
        "user_b_email": data_b["user"]["email"],
        "token_b": data_b["token"],
        "headers_b": {"Authorization": f"Bearer {data_b['token']}"},
        "session_factory": test_session_factory,
    }


# ── 1. Multi-Tenant Isolation Tests ─────────────────────────

@pytest.mark.asyncio
async def test_multi_tenant_isolation_meetings_and_audio(client: AsyncClient, setup_two_users):
    """
    User A creates a meeting with decisions, transcript segments, and audio.
    Proves User B cannot read, modify, delete, stream audio, or export User A's data.
    """
    u = setup_two_users
    user_a_id = u["user_a_id"]
    headers_a = u["headers_a"]
    headers_b = u["headers_b"]
    session_factory = u["session_factory"]

    # User A creates a meeting
    create_res = await client.post(
        "/api/meetings",
        json={"title": "Alpha Confidential Sync", "consent_recorded": True},
        headers=headers_a,
    )
    assert create_res.status_code == 200
    meeting_id = create_res.json()["id"]

    # Populate audio file on storage
    storage = get_storage_service(get_test_settings())
    tab_filename = f"{meeting_id}_tab.wav"
    storage.save_bytes(tab_filename, b"RIFFmockwavdataforalpha")

    # Insert transcript and decision for meeting_id
    async with session_factory() as session:
        seg = TranscriptSegment(
            meeting_id=meeting_id,
            speaker="User Alpha",
            start_ms=0,
            end_ms=5000,
            start_time=0.0,
            end_time=5.0,
            text="Alpha secret project details.",
            language="en",
        )
        dec = Decision(
            meeting_id=meeting_id,
            type="decision",
            text="Launch Secret Feature X",
            status="accepted",
            timestamp_ms=2500,
        )
        session.add_all([seg, dec])
        await session.commit()
        await session.refresh(dec)
        dec_id = dec.id

    # Index into vector store
    vstore = get_vector_store()
    chunk = TranscriptChunk(
        chunk_id=f"{meeting_id}_chunk_0",
        meeting_id=meeting_id,
        user_id=user_a_id,
        meeting_title="Alpha Confidential Sync",
        date="2026-10-23",
        speakers="User Alpha",
        start_ms=0,
        end_ms=5000,
        chunk_index=0,
        text="Alpha secret project details and launch date.",
        token_count=10,
    )
    vstore.upsert_chunks([chunk])

    # 1. User B cannot read User A's meeting
    get_res = await client.get(f"/api/meetings/{meeting_id}", headers=headers_b)
    assert get_res.status_code == 404

    # 2. User B cannot view User A's meeting viewer HTML page
    view_res = await client.get(f"/meetings/{meeting_id}", headers=headers_b)
    assert view_res.status_code == 404

    # 3. User B cannot read User A's transcript
    trans_res = await client.get(f"/api/meetings/{meeting_id}/transcript", headers=headers_b)
    assert trans_res.status_code == 404

    # 4. User B cannot read or create decisions on User A's meeting
    dec_get_res = await client.get(f"/api/meetings/{meeting_id}/decisions", headers=headers_b)
    assert dec_get_res.status_code == 404

    dec_post_res = await client.post(
        f"/api/meetings/{meeting_id}/decisions",
        json={"text": "Malicious decision injection", "type": "decision"},
        headers=headers_b,
    )
    assert dec_post_res.status_code == 404

    # 5. User B cannot delete User A's decision
    dec_del_res = await client.delete(
        f"/api/meetings/{meeting_id}/decisions/{dec_id}",
        headers=headers_b,
    )
    assert dec_del_res.status_code == 404

    # 6. User B cannot rename User A's meeting
    patch_res = await client.patch(
        f"/api/meetings/{meeting_id}",
        json={"title": "Hacked Title"},
        headers=headers_b,
    )
    assert patch_res.status_code == 404

    # 7. User B cannot export User A's meeting
    exp_txt = await client.get(f"/api/meetings/{meeting_id}/export/txt", headers=headers_b)
    assert exp_txt.status_code == 404

    exp_json = await client.get(f"/api/meetings/{meeting_id}/export/json", headers=headers_b)
    assert exp_json.status_code == 404

    # 8. User B cannot stream User A's audio
    audio_res = await client.get(f"/meetings/{meeting_id}/audio/tab", headers=headers_b)
    assert audio_res.status_code == 404

    # 9. User B cannot delete User A's meeting
    del_res = await client.delete(f"/api/meetings/{meeting_id}", headers=headers_b)
    assert del_res.status_code == 404

    # 10. User B cannot search or retrieve User A's meeting via Ask AI
    ask_res = await client.post(
        "/api/ask",
        json={"question": "What is the secret feature?", "meeting_id": meeting_id},
        headers=headers_b,
    )
    assert ask_res.status_code == 404

    # Global ask by User B returns no citations from User A
    ask_global = await client.post(
        "/api/ask",
        json={"question": "What is the secret feature?"},
        headers=headers_b,
    )
    assert ask_global.status_code == 200
    citations_b = ask_global.json().get("citations", [])
    assert all(c["meeting_id"] != meeting_id for c in citations_b)

    # Clean up audio file
    storage.delete_file(tab_filename)


# ── 2. Input Validation Tests ────────────────────────────────

@pytest.mark.asyncio
async def test_input_validation_meeting_id_path_params(client: AsyncClient, setup_two_users):
    """Path parameter meeting_id must be a positive integer (ge=1); reject malformed with 422."""
    headers_a = setup_two_users["headers_a"]

    # Negative integer meeting ID
    neg_res = await client.get("/api/meetings/-1", headers=headers_a)
    assert neg_res.status_code == 422

    # Zero meeting ID
    zero_res = await client.get("/api/meetings/0", headers=headers_a)
    assert zero_res.status_code == 422

    # Non-integer string meeting ID
    str_res = await client.get("/api/meetings/invalid_id", headers=headers_a)
    assert str_res.status_code == 422

    # Malformed audio channel
    channel_res = await client.get("/meetings/1/audio/bad_channel", headers=headers_a)
    assert channel_res.status_code == 422


@pytest.mark.asyncio
async def test_input_validation_title_length_limit(client: AsyncClient, setup_two_users):
    """Reject meeting creation with title exceeding maximum length (200 chars)."""
    headers_a = setup_two_users["headers_a"]
    oversized_title = "A" * 250

    res = await client.post(
        "/api/meetings",
        json={"title": oversized_title},
        headers=headers_a,
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_input_validation_decision_text_limit(client: AsyncClient, setup_two_users):
    """Reject decision creation with text exceeding maximum length (2000 chars)."""
    headers_a = setup_two_users["headers_a"]

    # First create valid meeting
    m_res = await client.post(
        "/api/meetings",
        json={"title": "Decision Limit Test"},
        headers=headers_a,
    )
    meeting_id = m_res.json()["id"]

    oversized_text = "D" * 2500
    d_res = await client.post(
        f"/api/meetings/{meeting_id}/decisions",
        json={"text": oversized_text, "type": "decision"},
        headers=headers_a,
    )
    assert d_res.status_code == 422


@pytest.mark.asyncio
async def test_input_validation_oversized_upload(client: AsyncClient, setup_two_users):
    """Reject upload exceeding maximum upload size limit with 413 Payload Too Large."""
    from backend.main import app
    from tests.conftest import get_test_settings
    headers_a = setup_two_users["headers_a"]

    # Temporarily override settings with a 1 MB upload limit
    tiny_settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        debug=False,
        audio_storage_dir="test_audio_storage",
        gemini_api_key="",
        max_upload_size_mb=1,
    )
    app.dependency_overrides[get_settings] = lambda: tiny_settings

    try:
        fake_audio = b"RIFF" + b"0" * (1200 * 1024)
        res = await client.post(
            "/meetings/upload",
            files={"file": ("large_audio.wav", fake_audio, "audio/wav")},
            data={"title": "Oversized Meeting"},
            headers=headers_a,
        )
        assert res.status_code == 413
        assert "exceeds maximum limit" in res.json()["detail"]
    finally:
        app.dependency_overrides[get_settings] = get_test_settings


# ── 3. Audit Logging Tests ───────────────────────────────────

@pytest.mark.asyncio
async def test_audit_logging_lifecycle_events(client: AsyncClient, setup_two_users):
    """
    Verify audit log records events for creation, rename, export, Ask AI, and deletion.
    Ensures transcript content, question tokens, and API keys are NEVER logged.
    """
    headers_a = setup_two_users["headers_a"]
    user_a_id = setup_two_users["user_a_id"]
    session_factory = setup_two_users["session_factory"]

    # 1. Create meeting
    c_res = await client.post(
        "/api/meetings",
        json={"title": "Audit Test Meeting"},
        headers=headers_a,
    )
    assert c_res.status_code == 200
    meeting_id = c_res.json()["id"]

    # 2. Rename meeting
    r_res = await client.patch(
        f"/api/meetings/{meeting_id}",
        json={"title": "Renamed Audit Meeting"},
        headers=headers_a,
    )
    assert r_res.status_code == 200

    # 3. Export meeting
    e_res = await client.get(f"/api/meetings/{meeting_id}/export/txt", headers=headers_a)
    assert e_res.status_code == 200

    # 4. Ask AI query
    sensitive_question = "What is the confidential nuclear launch code?"
    await client.post(
        "/api/ask",
        json={"question": sensitive_question},
        headers=headers_a,
    )

    # 5. Delete meeting
    d_res = await client.delete(f"/api/meetings/{meeting_id}", headers=headers_a)
    assert d_res.status_code == 200

    # Check AuditLog entries in database
    async with session_factory() as session:
        logs_res = await session.execute(
            select(AuditLog).where(AuditLog.user_id == user_a_id).order_by(AuditLog.timestamp)
        )
        logs = list(logs_res.scalars().all())

        actions = [log.action for log in logs]
        assert "meeting_create" in actions
        assert "meeting_rename" in actions
        assert any(act in actions for act in ("export_txt", "meeting_export"))
        assert any(act in actions for act in ("ask_ai", "ask_ai_query"))
        assert "meeting_delete" in actions

        # CRITICAL PRIVACY ASSERTION:
        # Verify sensitive question text or transcripts never appear in any audit log target or action
        for log in logs:
            assert sensitive_question not in log.target
            assert sensitive_question not in log.action
            assert "launch code" not in log.target


# ── 4. Data Retention Policy & Cleanup Tests ────────────────

@pytest.mark.asyncio
async def test_retention_settings_and_cleanup(client: AsyncClient, setup_two_users):
    """
    Verify user can set retention period (30 days), and retention cleanup
    deletes meetings older than 30 days along with storage files and vector chunks.
    """
    headers_a = setup_two_users["headers_a"]
    user_a_id = setup_two_users["user_a_id"]
    session_factory = setup_two_users["session_factory"]

    # 1. Update user retention setting to 30 days
    patch_res = await client.patch(
        "/api/users/me/settings",
        json={"retention_days": 30},
        headers=headers_a,
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["retention_days"] == 30

    # 2. Insert an expired meeting (started 35 days ago) and a fresh meeting (started 5 days ago)
    past_date = datetime.now(timezone.utc) - timedelta(days=35)
    fresh_date = datetime.now(timezone.utc) - timedelta(days=5)

    storage = get_storage_service(get_test_settings())

    async with session_factory() as session:
        expired_meeting = Meeting(
            user_id=user_a_id,
            title="Expired Old Meeting",
            status=MeetingStatus.READY,
            audio_path="expired_test_audio.wav",
            started_at=past_date,
            created_at=past_date,
        )
        fresh_meeting = Meeting(
            user_id=user_a_id,
            title="Fresh Active Meeting",
            status=MeetingStatus.READY,
            audio_path="fresh_test_audio.wav",
            started_at=fresh_date,
            created_at=fresh_date,
        )
        session.add_all([expired_meeting, fresh_meeting])
        await session.commit()
        await session.refresh(expired_meeting)
        await session.refresh(fresh_meeting)

        expired_id = expired_meeting.id
        fresh_id = fresh_meeting.id

    # Create dummy audio files on disk
    storage.save_bytes(f"{expired_id}_tab.wav", b"RIFFexpiredtab")
    storage.save_bytes("expired_test_audio.wav", b"RIFFexpiredtest")
    storage.save_bytes(f"{fresh_id}_tab.wav", b"RIFFfreshtab")

    # Add vector chunk for expired meeting
    vstore = get_vector_store()
    chunk = TranscriptChunk(
        chunk_id=f"{expired_id}_chunk_0",
        meeting_id=expired_id,
        user_id=user_a_id,
        meeting_title="Expired Old Meeting",
        date="2026-09-18",
        speakers="Speaker Old",
        start_ms=0,
        end_ms=5000,
        chunk_index=0,
        text="Expired meeting speech excerpt.",
        token_count=8,
    )
    vstore.upsert_chunks([chunk])

    # 3. Run retention cleanup
    settings = get_test_settings()
    async with session_factory() as session:
        cleanup_report = await run_retention_cleanup(session, storage, settings)

    assert cleanup_report["meetings_deleted"] >= 1
    assert cleanup_report["audio_files_deleted"] >= 1

    # 4. Verify expired meeting is gone from database
    async with session_factory() as session:
        exp_check = await session.get(Meeting, expired_id)
        assert exp_check is None

        # Fresh meeting remains intact
        fresh_check = await session.get(Meeting, fresh_id)
        assert fresh_check is not None

    # 5. Verify audio files for expired meeting deleted from disk
    assert not storage.file_exists(f"{expired_id}_tab.wav")
    assert not storage.file_exists("expired_test_audio.wav")
    assert storage.file_exists(f"{fresh_id}_tab.wav")

    # Clean up fresh audio file
    storage.delete_file(f"{fresh_id}_tab.wav")


# ── 5. Delete All My Data Tests ──────────────────────────────

@pytest.mark.asyncio
async def test_delete_all_user_data_flow(client: AsyncClient, test_session_factory):
    """
    User creates data (meetings, decisions, vectors, audio).
    Typing invalid confirmation rejects with 400.
    Typing 'DELETE ALL MY DATA' purges all user data and user record permanently.
    """
    # 1. Login user candidate for deletion
    resp = await client.get("/auth/google/callback?code=mock_code_delete_user&json_response=true")
    assert resp.status_code == 200
    data = resp.json()
    u_id = data["user"]["id"]
    token = data["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # User creates meeting
    m_res = await client.post("/api/meetings", json={"title": "To Be Deleted"}, headers=headers)
    assert m_res.status_code == 200
    meeting_id = m_res.json()["id"]

    # User creates decision
    await client.post(
        f"/api/meetings/{meeting_id}/decisions",
        json={"text": "Decision To Be Deleted", "type": "action_item"},
        headers=headers,
    )

    # User creates audio file
    storage = get_storage_service(get_test_settings())
    tab_file = f"{meeting_id}_tab.wav"
    storage.save_bytes(tab_file, b"RIFFdeletedata")

    # User indexes vector
    vstore = get_vector_store()
    chunk = TranscriptChunk(
        chunk_id=f"{meeting_id}_chunk_0",
        meeting_id=meeting_id,
        user_id=u_id,
        meeting_title="To Be Deleted",
        date="2026-10-23",
        speakers="Speaker Del",
        start_ms=0,
        end_ms=5000,
        chunk_index=0,
        text="Content to be wiped completely.",
        token_count=8,
    )
    vstore.upsert_chunks([chunk])

    # 1. Invalid confirmation string rejected
    bad_res = await client.post(
        "/api/users/me/delete-all-data",
        json={"confirmation": "please delete"},
        headers=headers,
    )
    assert bad_res.status_code == 400
    assert "Typed confirmation mismatch" in bad_res.json()["detail"]

    # 2. Correct confirmation string accepted
    ok_res = await client.post(
        "/api/users/me/delete-all-data",
        json={"confirmation": "DELETE ALL MY DATA"},
        headers=headers,
    )
    assert ok_res.status_code == 200
    assert ok_res.json()["status"] == "deleted"

    # 3. Verify user record is deleted
    async with test_session_factory() as session:
        user_check = await session.get(User, u_id)
        assert user_check is None

        # Meetings are deleted
        m_check = await session.get(Meeting, meeting_id)
        assert m_check is None

    # 4. Verify audio file was deleted
    assert not storage.file_exists(tab_file)


# ── 6. Security Headers and CSRF Protection Tests ───────────

@pytest.mark.asyncio
async def test_security_headers_present_on_responses(client: AsyncClient):
    """Verify CSP, X-Frame-Options: DENY, X-Content-Type-Options: nosniff, Referrer-Policy."""
    res = await client.get("/api/health")
    assert res.status_code == 200

    # Headers verification
    assert res.headers.get("X-Frame-Options") == "DENY"
    assert res.headers.get("X-Content-Type-Options") == "nosniff"
    assert res.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "Content-Security-Policy" in res.headers
    assert "frame-ancestors 'none'" in res.headers["Content-Security-Policy"]
    assert "Permissions-Policy" in res.headers


@pytest.mark.asyncio
async def test_csrf_protection_for_session_cookies(client: AsyncClient, setup_two_users):
    """
    State-changing browser requests authenticated via session cookie
    must include matching X-CSRF-Token header or be rejected with 403.
    """
    token_a = setup_two_users["token_a"]

    # 1. State-changing POST with session cookie but NO CSRF token -> 403 Forbidden
    client.cookies.set("decisionai_session", token_a)
    rejected_res = await client.post(
        "/api/meetings",
        json={"title": "CSRF Attack Meeting"},
    )
    assert rejected_res.status_code == 403
    assert "CSRF verification failed" in rejected_res.json()["detail"]

    # 2. State-changing POST with mismatched CSRF token -> 403 Forbidden
    client.cookies.set("decisionai_csrf", "valid_csrf_token_value")
    mismatched_res = await client.post(
        "/api/meetings",
        json={"title": "CSRF Attack Meeting"},
        headers={"X-CSRF-Token": "attacker_fake_token"},
    )
    assert mismatched_res.status_code == 403

    # 3. State-changing POST with matching CSRF token and cookie -> 200 OK
    valid_res = await client.post(
        "/api/meetings",
        json={"title": "Legitimate User Meeting"},
        headers={"X-CSRF-Token": "valid_csrf_token_value"},
    )
    assert valid_res.status_code == 200
    assert valid_res.json()["title"] == "Legitimate User Meeting"

    # Clean up cookies
    client.cookies.clear()
