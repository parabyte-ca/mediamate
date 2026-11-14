"""
Unit tests for the update-check and upgrade-request feature.

Coverage
────────
  Semver parsing             — valid tags, v-prefix, non-semver rejection
  Version comparison         — tuple ordering used for update detection
  Helper connected logic     — fresh / stale / future timestamp / interval tolerance
  Upgrade state derivation   — None, requested, stale-requested, running,
                               stale-running, succeeded, failed
  Folder file reading        — oversized, symlink, non-object, malformed JSON
  get_status                 — builds correct payload from DB + folder
  fetch_and_cache            — success path updates cache; failure keeps previous
  _maybe_notify              — sent once per new version, not repeated
  request_upgrade            — validation guards, atomic write, folder missing
  GET /api/admin/updates     — 200 with correct shape, 401 without auth
  POST /api/admin/updates/check — proxies fetch_and_cache
  POST /api/admin/updates/upgrade — 400 on bad version, 401 without auth
"""
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.helpers import from_address
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import Setting
from app.routers.updates import router
from app.services.updater import (
    derive_upgrade_state,
    get_status,
    is_helper_connected,
    normalise_version,
    parse_gh_repo,
    parse_semver,
    request_upgrade,
)


# ── Test infrastructure ───────────────────────────────────────────────────────


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


@pytest.fixture()
def upgrade_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture()
def client(db_session):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _write(folder: str, name: str, data: dict) -> None:
    path = os.path.join(folder, name)
    with open(path, "w") as f:
        json.dump(data, f)


def _seed_cache(db_session, version="2.5.0") -> None:
    db_session.add(Setting(
        key="update_release_cache",
        value=json.dumps({
            "version": version,
            "name": f"v{version}",
            "notes": "Test release",
            "url": "https://github.com/example/repo/releases/tag/v" + version,
            "published_at": "2026-10-01T00:00:00Z",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "check_error": None,
        }),
    ))
    db_session.commit()


# ── Semver ────────────────────────────────────────────────────────────────────


class TestParseSemver:
    def test_plain(self):
        assert parse_semver("1.2.3") == (1, 2, 3)

    def test_v_prefix(self):
        assert parse_semver("v2.4.0") == (2, 4, 0)

    def test_zeros(self):
        assert parse_semver("0.0.0") == (0, 0, 0)

    def test_two_part_rejected(self):
        assert parse_semver("2.4") is None

    def test_alpha_rejected(self):
        assert parse_semver("2.4.0-beta") is None

    def test_word_rejected(self):
        assert parse_semver("latest") is None

    def test_empty_rejected(self):
        assert parse_semver("") is None

    def test_comparison_works(self):
        assert parse_semver("2.4.0") > parse_semver("2.3.0")  # type: ignore[operator]
        assert parse_semver("2.3.1") > parse_semver("2.3.0")  # type: ignore[operator]
        assert parse_semver("3.0.0") > parse_semver("2.99.99")  # type: ignore[operator]


class TestNormaliseVersion:
    def test_two_part(self):
        assert normalise_version("2.4") == "2.4.0"

    def test_three_part(self):
        assert normalise_version("2.4.1") == "2.4.1"

    def test_v_prefix(self):
        assert normalise_version("v2.4.0") == "2.4.0"


# ── Helper connected ──────────────────────────────────────────────────────────


def _agent(minutes_ago: float = 5, every: int = 15) -> dict:
    ts = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return {"last_seen": ts.isoformat(), "every_minutes": every}


class TestHelperConnected:
    def test_fresh_is_connected(self):
        assert is_helper_connected(_agent(minutes_ago=1)) is True

    def test_just_within_grace(self):
        # grace = max(3, 2*15+1) = 31 minutes; 30 min ago should pass
        assert is_helper_connected(_agent(minutes_ago=30)) is True

    def test_just_outside_grace(self):
        # 32 minutes ago should fail for every_minutes=15
        assert is_helper_connected(_agent(minutes_ago=32)) is False

    def test_grace_scales_with_interval(self):
        # every=5 → grace = max(3, 11) = 11 min; 10 min ago passes
        assert is_helper_connected(_agent(minutes_ago=10, every=5)) is True
        # 13 min ago fails
        assert is_helper_connected(_agent(minutes_ago=13, every=5)) is False

    def test_future_timestamp_rejected(self):
        future_ts = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        assert is_helper_connected({"last_seen": future_ts, "every_minutes": 15}) is False

    def test_just_future_tolerated(self):
        # Up to 1 minute in the future is tolerated
        future_ts = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
        assert is_helper_connected({"last_seen": future_ts, "every_minutes": 15}) is True

    def test_missing_last_seen(self):
        assert is_helper_connected({}) is False

    def test_bad_timestamp(self):
        assert is_helper_connected({"last_seen": "not-a-date", "every_minutes": 15}) is False


# ── Upgrade state derivation ──────────────────────────────────────────────────


class TestDeriveUpgradeState:
    def test_empty_folder(self, upgrade_dir):
        state, msg = derive_upgrade_state(upgrade_dir)
        assert state is None
        assert msg is None

    def test_fresh_request(self, upgrade_dir):
        _write(upgrade_dir, "request.json", {
            "id": "abc", "version": "2.5.0",
            "requested_by": "admin",
            "requested_at": datetime.now(timezone.utc).isoformat(),
        })
        state, _ = derive_upgrade_state(upgrade_dir)
        assert state == "requested"

    def test_stale_request_becomes_failed(self, upgrade_dir):
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        _write(upgrade_dir, "request.json", {
            "id": "abc", "version": "2.5.0",
            "requested_by": "admin", "requested_at": old_ts,
        })
        state, msg = derive_upgrade_state(upgrade_dir)
        assert state == "failed"
        assert "never picked" in (msg or "")

    def test_running(self, upgrade_dir):
        _write(upgrade_dir, "status.json", {
            "state": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
        })
        state, _ = derive_upgrade_state(upgrade_dir)
        assert state == "running"

    def test_stale_running_becomes_failed(self, upgrade_dir):
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
        _write(upgrade_dir, "status.json", {"state": "running", "started_at": old_ts})
        state, msg = derive_upgrade_state(upgrade_dir)
        assert state == "failed"
        assert "stopped reporting" in (msg or "")

    def test_succeeded(self, upgrade_dir):
        _write(upgrade_dir, "status.json", {"state": "succeeded"})
        state, _ = derive_upgrade_state(upgrade_dir)
        assert state == "succeeded"

    def test_failed(self, upgrade_dir):
        _write(upgrade_dir, "status.json", {"state": "failed", "message": "build error"})
        state, msg = derive_upgrade_state(upgrade_dir)
        assert state == "failed"
        assert msg == "build error"

    def test_running_status_overrides_request(self, upgrade_dir):
        _write(upgrade_dir, "request.json", {
            "id": "abc", "version": "2.5.0",
            "requested_by": "admin",
            "requested_at": datetime.now(timezone.utc).isoformat(),
        })
        _write(upgrade_dir, "status.json", {
            "state": "running", "started_at": datetime.now(timezone.utc).isoformat()})
        state, _ = derive_upgrade_state(upgrade_dir)
        assert state == "running"

    def test_new_request_overrides_a_finished_status(self, upgrade_dir):
        # The helper deletes request.json before writing any status, so a request
        # still present is newer than the result of the previous upgrade.
        _write(upgrade_dir, "request.json", {
            "id": "abc", "version": "2.5.0",
            "requested_by": "admin",
            "requested_at": datetime.now(timezone.utc).isoformat(),
        })
        _write(upgrade_dir, "status.json", {"state": "succeeded"})
        state, _ = derive_upgrade_state(upgrade_dir)
        assert state == "requested"


# ── Folder file reading (defensive) ──────────────────────────────────────────


class TestReadJsonFile:
    """get_status must never crash on malformed folder contents."""

    def test_oversized_file_ignored(self, upgrade_dir, db_session):
        path = os.path.join(upgrade_dir, "agent.json")
        with open(path, "w") as f:
            f.write('{"last_seen": "' + "x" * 66000 + '"}')
        with patch("app.services.updater._get_folder", return_value=upgrade_dir):
            status = get_status(db_session)
        assert status["helper"]["configured"] is False

    def test_non_object_json_ignored(self, upgrade_dir, db_session):
        path = os.path.join(upgrade_dir, "agent.json")
        with open(path, "w") as f:
            f.write('"just a string"')
        with patch("app.services.updater._get_folder", return_value=upgrade_dir):
            status = get_status(db_session)
        assert status["helper"]["configured"] is False

    def test_malformed_json_ignored(self, upgrade_dir, db_session):
        path = os.path.join(upgrade_dir, "agent.json")
        with open(path, "w") as f:
            f.write("{not valid json")
        with patch("app.services.updater._get_folder", return_value=upgrade_dir):
            status = get_status(db_session)
        assert status["helper"]["configured"] is False

    def test_symlink_ignored(self, upgrade_dir, db_session):
        target = os.path.join(upgrade_dir, "real.json")
        with open(target, "w") as f:
            json.dump({"last_seen": datetime.now(timezone.utc).isoformat(), "every_minutes": 15}, f)
        link = os.path.join(upgrade_dir, "agent.json")
        os.symlink(target, link)
        with patch("app.services.updater._get_folder", return_value=upgrade_dir):
            status = get_status(db_session)
        assert status["helper"]["configured"] is False


# ── get_status ────────────────────────────────────────────────────────────────


class TestGetStatus:
    def test_no_update_when_no_cache(self, db_session, upgrade_dir):
        with patch("app.services.updater._get_folder", return_value=upgrade_dir):
            status = get_status(db_session)
        assert status["update_available"] is False
        assert status["latest_release"] is None
        assert status["can_upgrade"] is False

    def test_update_available_when_newer(self, db_session, upgrade_dir):
        _seed_cache(db_session, version="2.5.0")
        with patch("app.services.updater._get_folder", return_value=upgrade_dir), \
             patch("app.services.updater.get_current_version", return_value="2.4.0"):
            status = get_status(db_session)
        assert status["update_available"] is True

    def test_no_update_when_same_version(self, db_session, upgrade_dir):
        _seed_cache(db_session, version="2.4.0")
        with patch("app.services.updater._get_folder", return_value=upgrade_dir), \
             patch("app.services.updater.get_current_version", return_value="2.4.0"):
            status = get_status(db_session)
        assert status["update_available"] is False

    def test_can_upgrade_requires_helper(self, db_session, upgrade_dir):
        _seed_cache(db_session, version="2.5.0")
        # No agent.json → helper not connected → can_upgrade False
        with patch("app.services.updater._get_folder", return_value=upgrade_dir), \
             patch("app.services.updater.get_current_version", return_value="2.4.0"):
            status = get_status(db_session)
        assert status["can_upgrade"] is False

    def test_can_upgrade_true_with_connected_helper(self, db_session, upgrade_dir):
        _seed_cache(db_session, version="2.5.0")
        _write(upgrade_dir, "agent.json", {
            "last_seen": datetime.now(timezone.utc).isoformat(),
            "every_minutes": 15,
        })
        with patch("app.services.updater._get_folder", return_value=upgrade_dir), \
             patch("app.services.updater.get_current_version", return_value="2.4.0"):
            status = get_status(db_session)
        assert status["can_upgrade"] is True

    def test_log_included_when_requested(self, db_session, upgrade_dir):
        log_path = os.path.join(upgrade_dir, "upgrade.log")
        with open(log_path, "w") as f:
            f.write("upgrade started\nupgrade done\n")
        with patch("app.services.updater._get_folder", return_value=upgrade_dir):
            status = get_status(db_session, include_log=True)
        assert "upgrade done" in status.get("upgrade_log", "")

    def test_log_absent_by_default(self, db_session, upgrade_dir):
        with patch("app.services.updater._get_folder", return_value=upgrade_dir):
            status = get_status(db_session)
        assert "upgrade_log" not in status


# ── fetch_and_cache ───────────────────────────────────────────────────────────


class TestFetchAndCache:
    @pytest.mark.asyncio
    async def test_success_updates_cache(self, db_session, upgrade_dir):
        release = {
            "version": "2.5.0", "name": "v2.5.0",
            "notes": "New stuff", "url": "https://example.com",
            "published_at": "2026-10-01T00:00:00Z",
        }
        db_session.add(Setting(key="app_source_url", value="https://github.com/owner/repo"))
        db_session.commit()

        with patch("app.services.updater._fetch_latest_release", new=AsyncMock(return_value=release)), \
             patch("app.services.updater._get_folder", return_value=upgrade_dir), \
             patch("app.services.updater._maybe_notify"):
            from app.services.updater import fetch_and_cache
            await fetch_and_cache(db_session)

        from app.services.updater import _load_cache
        cache = _load_cache(db_session)
        assert cache["version"] == "2.5.0"
        assert cache["check_error"] is None
        assert cache["checked_at"]

    @pytest.mark.asyncio
    async def test_failure_keeps_previous_release(self, db_session, upgrade_dir):
        _seed_cache(db_session, version="2.4.5")
        db_session.add(Setting(key="app_source_url", value="https://github.com/owner/repo"))
        db_session.commit()

        with patch("app.services.updater._fetch_latest_release", side_effect=Exception("Network error")), \
             patch("app.services.updater._get_folder", return_value=upgrade_dir):
            from app.services.updater import fetch_and_cache
            await fetch_and_cache(db_session)

        from app.services.updater import _load_cache
        cache = _load_cache(db_session)
        assert cache["version"] == "2.4.5"          # previous release preserved
        assert "Network error" in cache["check_error"]
        assert cache["checked_at"]

    @pytest.mark.asyncio
    async def test_bad_tag_rejected(self, db_session, upgrade_dir):
        db_session.add(Setting(key="app_source_url", value="https://github.com/owner/repo"))
        db_session.commit()

        async def _bad_release(*_a, **_kw):
            raise ValueError("Release tag 'nightly' is not strict semver")

        with patch("app.services.updater._fetch_latest_release", new=_bad_release), \
             patch("app.services.updater._get_folder", return_value=upgrade_dir):
            from app.services.updater import fetch_and_cache
            await fetch_and_cache(db_session)

        from app.services.updater import _load_cache
        cache = _load_cache(db_session)
        assert "nightly" in (cache.get("check_error") or "")


# ── _maybe_notify ─────────────────────────────────────────────────────────────


class TestMaybeNotify:
    def _call(self, db_session, version: str, apprise_url: str = "mock://test"):
        from app.services.updater import _maybe_notify
        db_session.add(Setting(key="apprise_url", value=apprise_url))
        db_session.commit()
        with patch("app.services.updater.get_current_version", return_value="2.4.0"), \
             patch("app.services.updater.notify") as mock_notify:
            _maybe_notify(db_session, version)
            return mock_notify

    def test_notification_sent_for_new_version(self, db_session):
        mock_notify = self._call(db_session, "2.5.0")
        mock_notify.assert_called_once()

    def test_notification_not_sent_twice(self, db_session):
        db_session.add(Setting(key="update_notified_version", value="2.5.0"))
        db_session.commit()
        mock_notify = self._call(db_session, "2.5.0")
        mock_notify.assert_not_called()

    def test_notification_not_sent_for_same_version(self, db_session):
        mock_notify = self._call(db_session, "2.4.0")  # same as current
        mock_notify.assert_not_called()

    def test_notification_not_sent_for_older_version(self, db_session):
        mock_notify = self._call(db_session, "2.3.0")  # older than current
        mock_notify.assert_not_called()


# ── request_upgrade ───────────────────────────────────────────────────────────


class TestRequestUpgrade:
    def test_rejects_when_no_cache(self, db_session, upgrade_dir):
        with patch("app.services.updater._get_folder", return_value=upgrade_dir), \
             patch("app.services.updater.get_current_version", return_value="2.4.0"):
            with pytest.raises(ValueError, match="No release information"):
                request_upgrade(db_session, "2.5.0", "admin")

    def test_rejects_non_latest_version(self, db_session, upgrade_dir):
        _seed_cache(db_session, version="2.5.0")
        _write(upgrade_dir, "agent.json", {
            "last_seen": datetime.now(timezone.utc).isoformat(), "every_minutes": 15,
        })
        with patch("app.services.updater._get_folder", return_value=upgrade_dir), \
             patch("app.services.updater.get_current_version", return_value="2.4.0"):
            with pytest.raises(ValueError, match="does not match"):
                request_upgrade(db_session, "2.4.9", "admin")

    def test_rejects_non_newer_version(self, db_session, upgrade_dir):
        _seed_cache(db_session, version="2.4.0")
        _write(upgrade_dir, "agent.json", {
            "last_seen": datetime.now(timezone.utc).isoformat(), "every_minutes": 15,
        })
        with patch("app.services.updater._get_folder", return_value=upgrade_dir), \
             patch("app.services.updater.get_current_version", return_value="2.4.0"):
            with pytest.raises(ValueError, match="not newer"):
                request_upgrade(db_session, "2.4.0", "admin")

    def test_rejects_when_already_in_progress(self, db_session, upgrade_dir):
        _seed_cache(db_session, version="2.5.0")
        _write(upgrade_dir, "agent.json", {
            "last_seen": datetime.now(timezone.utc).isoformat(), "every_minutes": 15,
        })
        _write(upgrade_dir, "status.json", {
            "state": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
        })
        with patch("app.services.updater._get_folder", return_value=upgrade_dir), \
             patch("app.services.updater.get_current_version", return_value="2.4.0"):
            with pytest.raises(ValueError, match="already in progress"):
                request_upgrade(db_session, "2.5.0", "admin")

    def test_rejects_when_helper_not_connected(self, db_session, upgrade_dir):
        _seed_cache(db_session, version="2.5.0")
        with patch("app.services.updater._get_folder", return_value=upgrade_dir), \
             patch("app.services.updater.get_current_version", return_value="2.4.0"):
            with pytest.raises(ValueError, match="not connected"):
                request_upgrade(db_session, "2.5.0", "admin")

    def test_writes_request_json_atomically(self, db_session, upgrade_dir):
        _seed_cache(db_session, version="2.5.0")
        _write(upgrade_dir, "agent.json", {
            "last_seen": datetime.now(timezone.utc).isoformat(), "every_minutes": 15,
        })
        with patch("app.services.updater._get_folder", return_value=upgrade_dir), \
             patch("app.services.updater.get_current_version", return_value="2.4.0"), \
             patch("app.services.updater.notify"):
            request_upgrade(db_session, "2.5.0", "admin@example.com")

        req_path = os.path.join(upgrade_dir, "request.json")
        assert os.path.isfile(req_path)
        assert not os.path.isfile(req_path + ".tmp")  # temp file cleaned up
        with open(req_path) as f:
            req = json.load(f)
        assert req["version"] == "2.5.0"
        assert req["requested_by"] == "admin@example.com"
        assert "id" in req
        assert "requested_at" in req


# ── rollback ──────────────────────────────────────────────────────────────────


def _helper_up(upgrade_dir):
    _write(upgrade_dir, "agent.json", {
        "last_seen": datetime.now(timezone.utc).isoformat(), "every_minutes": 15,
    })


def _request(db_session, upgrade_dir, current, version, **kw):
    with patch("app.services.updater._get_folder", return_value=upgrade_dir), \
         patch("app.services.updater.get_current_version", return_value=current), \
         patch("app.services.updater.notify"):
        request_upgrade(db_session, version, "admin", **kw)


def _status(db_session, upgrade_dir, current):
    from app.services.updater import get_status
    with patch("app.services.updater._get_folder", return_value=upgrade_dir), \
         patch("app.services.updater.get_current_version", return_value=current):
        return get_status(db_session)


class TestRollback:
    def test_an_upgrade_remembers_the_version_it_replaced(self, db_session, upgrade_dir):
        _seed_cache(db_session, version="2.7.0")
        _helper_up(upgrade_dir)
        _request(db_session, upgrade_dir, "2.6.1", "2.7.0")
        os.remove(os.path.join(upgrade_dir, "request.json"))   # the helper picked it up
        # ...and once running 2.7.0, the status offers going back to 2.6.1
        st = _status(db_session, upgrade_dir, "2.7.0")
        assert st["previous_version"] == "2.6.1" and st["can_rollback"] is True

    def test_nothing_to_roll_back_before_any_upgrade(self, db_session, upgrade_dir):
        _helper_up(upgrade_dir)
        st = _status(db_session, upgrade_dir, "2.7.0")
        assert st["previous_version"] is None and st["can_rollback"] is False

    def test_no_rollback_offered_when_the_upgrade_never_took_effect(self, db_session, upgrade_dir):
        _seed_cache(db_session, version="2.7.0")
        _helper_up(upgrade_dir)
        _request(db_session, upgrade_dir, "2.6.1", "2.7.0")
        # still (or automatically back) on 2.6.1: nothing older to go to
        st = _status(db_session, upgrade_dir, "2.6.1")
        assert st["previous_version"] is None and st["can_rollback"] is False

    def test_rollback_requests_the_previous_version(self, db_session, upgrade_dir):
        _seed_cache(db_session, version="2.7.0")
        _helper_up(upgrade_dir)
        _request(db_session, upgrade_dir, "2.6.1", "2.7.0")
        os.remove(os.path.join(upgrade_dir, "request.json"))

        _request(db_session, upgrade_dir, "2.7.0", "2.6.1", rollback=True)

        with open(os.path.join(upgrade_dir, "request.json")) as f:
            assert json.load(f)["version"] == "2.6.1"
        # the recorded previous version is kept, not overwritten with 2.7.0
        assert _status(db_session, upgrade_dir, "2.6.1")["previous_version"] is None

    def test_rollback_to_any_other_version_is_refused(self, db_session, upgrade_dir):
        _seed_cache(db_session, version="2.7.0")
        _helper_up(upgrade_dir)
        _request(db_session, upgrade_dir, "2.6.1", "2.7.0")
        os.remove(os.path.join(upgrade_dir, "request.json"))
        with pytest.raises(ValueError, match="not the previous version"):
            _request(db_session, upgrade_dir, "2.7.0", "2.5.0", rollback=True)
        assert not os.path.exists(os.path.join(upgrade_dir, "request.json"))

    def test_rollback_without_history_is_refused(self, db_session, upgrade_dir):
        _helper_up(upgrade_dir)
        with pytest.raises(ValueError, match="no earlier version"):
            _request(db_session, upgrade_dir, "2.7.0", "2.6.1", rollback=True)

    def test_rollback_still_needs_the_helper_and_no_upgrade_running(self, db_session, upgrade_dir):
        _seed_cache(db_session, version="2.7.0")
        _helper_up(upgrade_dir)
        _request(db_session, upgrade_dir, "2.6.1", "2.7.0")
        # request.json from the upgrade is still pending -> in progress
        with pytest.raises(ValueError, match="already in progress"):
            _request(db_session, upgrade_dir, "2.7.0", "2.6.1", rollback=True)
        os.remove(os.path.join(upgrade_dir, "request.json"))
        os.remove(os.path.join(upgrade_dir, "agent.json"))
        with pytest.raises(ValueError, match="not connected"):
            _request(db_session, upgrade_dir, "2.7.0", "2.6.1", rollback=True)

    def test_a_failed_validation_does_not_overwrite_the_recorded_version(self, db_session, upgrade_dir):
        _seed_cache(db_session, version="2.7.0")
        with pytest.raises(ValueError):          # helper not connected
            _request(db_session, upgrade_dir, "2.6.1", "2.7.0")
        assert _status(db_session, upgrade_dir, "2.7.0")["previous_version"] is None

    def test_endpoint_passes_the_rollback_flag(self, client, upgrade_dir):
        with patch("app.services.updater._get_folder", return_value=upgrade_dir), \
             patch("app.services.updater.request_upgrade") as req:
            client.post("/admin/updates/upgrade", json={"version": "2.6.1", "rollback": True})
            client.post("/admin/updates/upgrade", json={"version": "2.6.1", "rollback": "yes"})
        assert req.call_args_list[0].kwargs == {"rollback": True}
        assert req.call_args_list[1].kwargs == {"rollback": False}

    def test_previous_version_is_never_exposed_as_a_setting(self):
        from app.routers.settings import _PRIVATE_KEYS
        assert "update_previous_version" in _PRIVATE_KEYS


# ── HTTP endpoints ────────────────────────────────────────────────────────────


class TestUpdateEndpoints:
    _AUTH = {"Authorization": "Bearer test-token"}

    def test_get_status_without_header_works_for_local_requests(self, client, upgrade_dir):
        with patch("app.services.updater._get_folder", return_value=upgrade_dir):
            r = client.get("/admin/updates")
        assert r.status_code == 200

    def test_get_status_200(self, client, upgrade_dir):
        with patch("app.services.updater._get_folder", return_value=upgrade_dir):
            r = client.get("/admin/updates", headers=self._AUTH)
        assert r.status_code == 200
        data = r.json()
        assert "current_version" in data
        assert "helper" in data
        assert "can_upgrade" in data

    def test_check_now_calls_fetch(self, client, upgrade_dir):
        with patch("app.services.updater._get_folder", return_value=upgrade_dir), \
             patch("app.services.updater.fetch_and_cache", new=AsyncMock()), \
             patch("app.services.updater.check_now_rate_limited", return_value=True):
            r = client.post("/admin/updates/check", headers=self._AUTH)
        assert r.status_code == 200

    def test_upgrade_without_header_reaches_handler_for_local_requests(self, client, upgrade_dir):
        with patch("app.services.updater._get_folder", return_value=upgrade_dir), \
             patch("app.services.updater.request_upgrade", side_effect=ValueError("nope")):
            r = client.post("/admin/updates/upgrade", json={"version": "2.5.0"})
        assert r.status_code == 400  # handler ran; not blocked as 401

    def test_upgrade_400_on_bad_version(self, client, upgrade_dir):
        with patch("app.services.updater._get_folder", return_value=upgrade_dir), \
             patch("app.services.updater.request_upgrade", side_effect=ValueError("Version mismatch")):
            r = client.post("/admin/updates/upgrade", json={"version": "2.5.0"}, headers=self._AUTH)
        assert r.status_code == 400
        assert "Version mismatch" in r.json()["detail"]

    def test_upgrade_422_missing_version(self, client, upgrade_dir):
        with patch("app.services.updater._get_folder", return_value=upgrade_dir):
            r = client.post("/admin/updates/upgrade", json={}, headers=self._AUTH)
        assert r.status_code == 422


class TestUpdateEndpointsBehindAuthMiddleware:
    """The real auth boundary: AuthMiddleware protects /api/admin/updates."""

    @pytest.fixture()
    def secured(self, db_session, upgrade_dir):
        from app.middleware.auth import AuthMiddleware
        from app.services.token import make_token

        db_session.add(Setting(key="ui_password", value="hunter2"))
        db_session.add(Setting(key="session_secret", value="s3cret"))
        db_session.commit()

        app = FastAPI()
        app.add_middleware(AuthMiddleware)
        app.include_router(router, prefix="/api")

        def _override():
            yield db_session

        app.dependency_overrides[get_db] = _override
        with patch("app.middleware.auth.SessionLocal", return_value=db_session), \
             patch.object(db_session, "close"), \
             patch("app.services.updater._get_folder", return_value=upgrade_dir):
            yield TestClient(from_address(app, "192.168.1.20")), make_token("s3cret", "hunter2")

    def test_cloudflare_request_without_token_is_rejected(self, secured):
        client, _ = secured
        r = client.get("/api/admin/updates", headers={"cf-connecting-ip": "1.2.3.4"})
        assert r.status_code == 401

    def test_cloudflare_request_with_garbage_token_is_rejected(self, secured):
        client, _ = secured
        r = client.get("/api/admin/updates", headers={
            "cf-connecting-ip": "1.2.3.4", "Authorization": "Bearer garbage"})
        assert r.status_code == 401

    def test_cloudflare_request_with_valid_token_is_allowed(self, secured):
        client, token = secured
        r = client.get("/api/admin/updates", headers={
            "cf-connecting-ip": "1.2.3.4", "Authorization": f"Bearer {token}"})
        assert r.status_code == 200

    def test_local_request_is_allowed(self, secured):
        client, _ = secured
        assert client.get("/api/admin/updates").status_code == 200


# ── parse_gh_repo ─────────────────────────────────────────────────────────────


class TestParseGhRepo:
    def test_standard(self):
        assert parse_gh_repo("https://github.com/parabyte-ca/screenmate") == ("parabyte-ca", "screenmate")

    def test_git_suffix(self):
        assert parse_gh_repo("https://github.com/owner/repo.git") == ("owner", "repo")

    def test_trailing_slash(self):
        assert parse_gh_repo("https://github.com/owner/repo/") == ("owner", "repo")

    def test_non_github_returns_none(self):
        assert parse_gh_repo("https://gitlab.com/owner/repo") is None

    def test_empty_returns_none(self):
        assert parse_gh_repo("") is None


# ── Upgrade phase (progress steps) ────────────────────────────────────────────

from app.services.updater import derive_upgrade_phase  # noqa: E402


@pytest.mark.parametrize("state,log,phase", [
    (None, "", None),
    ("requested", "", "requested"),
    ("running", "", "started"),
    ("running", "2026-10-31T14:00:00Z ==> Pulling latest code (branch: main) ...", "downloading"),
    ("running", "==> Pulling Docker image (v0.10.1) ...\n==> Pinned image version", "downloading"),
    ("running", "==> Building Docker image ...", "downloading"),
    ("running", "==> Pulling Docker image ...\n==> Restarting container ...", "restarting"),
    ("running", "==> Waiting for health check ...", "restarting"),
    ("succeeded", "==> Deployment complete.", "done"),
    ("failed", "==> Restarting container ...", "failed"),
])
def test_upgrade_phase_follows_the_log(state, log, phase):
    assert derive_upgrade_phase(state, log) == phase


def test_status_reports_the_phase_of_a_running_upgrade(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_UPGRADE_FOLDER", str(tmp_path))
    (tmp_path / "status.json").write_text(json.dumps({
        "state": "running", "started_at": datetime.now(timezone.utc).isoformat()}))
    (tmp_path / "upgrade.log").write_text("==> Pulling Docker image (v9.9.9) ...\n")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        s = get_status(db)
    assert s["upgrade_state"] == "running" and s["upgrade_phase"] == "downloading"
    assert "upgrade_log" not in s



# ── upgrade timings (progress estimate) ──────────────────────────────────────


class TestUpgradeTimings:
    def _finish(self, db_session, version, minutes):
        from app.services import updater
        at = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        updater._set_json_setting(db_session, "update_pending", {"version": version, "at": at.isoformat()})
        with patch("app.services.updater.get_current_version", return_value=version):
            return updater.record_upgrade_finished(db_session)

    def test_request_then_restart_records_the_duration(self, db_session, upgrade_dir):
        from app.services import updater
        _seed_cache(db_session, version="2.7.0")
        _helper_up(upgrade_dir)
        _request(db_session, upgrade_dir, "2.6.1", "2.7.0")
        assert updater._get_json_setting(db_session, "update_pending")["version"] == "2.7.0"
        later = datetime.now(timezone.utc) + timedelta(minutes=9)
        with patch("app.services.updater.get_current_version", return_value="2.7.0"):
            took = updater.record_upgrade_finished(db_session, now=later)
        assert 535 <= took <= 545
        assert updater.upgrade_durations(db_session) == [took]
        assert updater._get_json_setting(db_session, "update_pending") is None

    def test_estimate_is_the_median_of_recent_upgrades(self, db_session):
        from app.services import updater
        assert updater.upgrade_estimate(db_session) is None
        for m in (8, 6, 40, 7):           # one slow outlier
            self._finish(db_session, "2.7.0", m)
        assert updater.upgrade_estimate(db_session) == 450          # median of 6, 7, 8, 40 min
        for m in (5, 5, 5):               # only the latest 5 count
            self._finish(db_session, "2.7.0", m)
        assert updater.upgrade_estimate(db_session) == 300

    def test_keeps_the_last_ten(self, db_session):
        from app.services import updater
        for m in range(1, 13):
            self._finish(db_session, "2.7.0", m)
        assert len(updater.upgrade_durations(db_session)) == 10
        assert updater.upgrade_durations(db_session)[0] == 180

    def test_a_restart_on_another_version_does_not_count(self, db_session):
        from app.services import updater
        updater._set_json_setting(db_session, "update_pending",
                                  {"version": "2.7.0", "at": datetime.now(timezone.utc).isoformat()})
        with patch("app.services.updater.get_current_version", return_value="2.6.1"):
            assert updater.record_upgrade_finished(db_session) is None
        assert updater._get_json_setting(db_session, "update_pending")     # still waiting
        assert updater.upgrade_durations(db_session) == []

    def test_abandoned_requests_are_forgotten(self, db_session):
        from app.services import updater
        updater._set_json_setting(db_session, "update_pending", {"version": "2.7.0", "at": (
            datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()})
        with patch("app.services.updater.get_current_version", return_value="2.6.1"):
            updater.record_upgrade_finished(db_session)
        assert updater._get_json_setting(db_session, "update_pending") is None
        assert self._finish(db_session, "2.7.0", 300) is None             # 5 h: not a real timing

    def test_garbage_is_ignored(self, db_session):
        from app.services import updater
        updater._set_json_setting(db_session, "update_durations", ["x", -3, 120, 99999])
        assert updater.upgrade_durations(db_session) == [120]
        updater._set_json_setting(db_session, "update_pending", {"version": "2.7.0", "at": "nope"})
        assert updater.record_upgrade_finished(db_session) is None

    def test_status_reports_the_estimate(self, db_session, upgrade_dir):
        for m in (6, 8):
            self._finish(db_session, "2.7.0", m)
        s = _status(db_session, upgrade_dir, "2.7.0")
        assert s["upgrade_estimate_seconds"] == 420 and s["upgrade_history_count"] == 2


# ── stage-by-stage progress (the Netwatch-style tracker) ─────────────────────

def _progress_folder(tmp_path, status=None, request=None, log=""):
    import json as _json
    if status is not None:
        (tmp_path / "status.json").write_text(_json.dumps(status))
    if request is not None:
        (tmp_path / "request.json").write_text(_json.dumps(request))
    (tmp_path / "upgrade.log").write_text(log)
    return str(tmp_path)


RUN_LOG = """2026-11-14T13:00:05Z ==> Pulling latest code (branch: main) ...
2026-11-14T13:00:09Z ==> Checking out tag/version: v0.43.1 ...
2026-11-14T13:00:10Z ==> Running pre-flight migration test ...
2026-11-14T13:00:30Z ==> Pulling Docker image (0.43.1) ...
Some docker output without a timestamp
2026-11-14T13:01:40Z ==> Restarting container ...
2026-11-14T13:01:45Z ==> Waiting for health check ...
"""


def test_progress_marks_each_stage_from_the_log(tmp_path):
    from app.services import updater
    folder = _progress_folder(tmp_path, {"state": "running", "started_at": "2026-11-14T13:00:00Z", "version": "0.43.1"},
                              log=RUN_LOG)
    p = updater.upgrade_progress(folder, "running", RUN_LOG)
    by = {s["key"]: s for s in p["stages"]}
    assert p["stage"] == "health" and p["failed_at"] is None
    assert by["download"]["at"].startswith("2026-11-14T13:00:30") and by["restart"]["reached"]
    assert not by["done"]["reached"] and p["last_line"] == "Waiting for health check ..."
    assert p["started_at"].startswith("2026-11-14T13:00:00") and p["version"] == "0.43.1"


def test_progress_success_and_failure(tmp_path):
    from app.services import updater
    ok = {"state": "succeeded", "started_at": "2026-11-14T13:00:00Z", "finished_at": "2026-11-14T13:02:10Z"}
    p = updater.upgrade_progress(_progress_folder(tmp_path, ok, log=RUN_LOG), "succeeded", RUN_LOG)
    assert p["stage"] == "done" and all(s["reached"] for s in p["stages"]) and p["finished_at"]
    short = RUN_LOG.split("Pulling Docker")[0]
    bad = {"state": "failed", "message": "update.sh exited 1", "finished_at": "2026-11-14T13:00:40Z"}
    p = updater.upgrade_progress(_progress_folder(tmp_path, bad, log=short), "failed", short)
    assert p["failed_at"] == "test"
    assert p["started_at"].startswith("2026-11-14T13:00:05")      # older helper: first log line


def test_progress_while_waiting_for_the_helper(tmp_path):
    from app.services import updater
    req = {"version": "0.43.1", "requested_at": "2026-11-14T12:59:00Z", "action": "rollback"}
    p = updater.upgrade_progress(_progress_folder(tmp_path, request=req), "requested", "")
    assert p["stage"] == "requested" and p["action"] == "rollback" and p["stages"][0]["at"]
    assert updater.upgrade_progress(str(tmp_path), None, "") is None
