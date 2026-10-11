from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from asok import Asok
from asok.admin import Admin
from asok.background import purge_dlq
from asok.exceptions import RedirectException


class DummyAdminUser:
    id = 1
    email = "admin@example.com"
    is_admin = True
    two_factor_enabled = False


class TestAdminJobsDashboard:
    def setup_method(self) -> None:
        purge_dlq()
        self.app = Asok()
        self.admin = Admin(self.app, url_prefix="/admin")

    def teardown_method(self) -> None:
        purge_dlq()

    def _make_admin_request(self, path: str, method: str = "GET") -> MagicMock:
        req = MagicMock()
        req.path = path
        req.method = method
        req.user = DummyAdminUser()
        req.session = {"user_id": 1, "is_admin": True}
        req.headers = {}
        req.environ = {}
        req.args = {}
        req.cookies = {}
        req.cookies_dict = {}
        req.flash = MagicMock()
        req.csrf_input = MagicMock(
            return_value='<input type="hidden" name="_csrf" value="tok">'
        )
        return req

    def test_jobs_dashboard_rendering(self) -> None:
        from asok.background import _push_local_dlq

        dummy_job = {
            "id": "job-abc",
            "module": "app.tasks",
            "function": "send_newsletter",
            "args": ["newsletter_1"],
            "kwargs": {},
            "queue": "high",
            "retries": 3,
            "retry_count": 3,
        }
        _push_local_dlq(dummy_job, "Connection to SMTP timed out")

        req = self._make_admin_request("/admin/jobs", method="GET")
        res = self.admin.dispatch(req)
        assert res is not None
        assert "stat-card" in res
        assert "app.tasks.send_newsletter" in res
        assert "Connection to SMTP timed out" in res
        assert "inherit" not in res  # No broken border-color: inherit

    def test_jobs_dashboard_empty_state_and_icon(self) -> None:
        purge_dlq()
        req = self._make_admin_request("/admin/jobs", method="GET")
        res = self.admin.dispatch(req)
        assert res is not None
        assert "No failed jobs" in res
        assert "The Dead Letter Queue is currently empty." in res
        # Check-circle SVG is properly rendered
        assert 'polyline points="22 4 12 14.01 9 11.01"' in res
        assert "inherit" not in res

    def test_jobs_dashboard_french_translation(self) -> None:
        purge_dlq()
        req = self._make_admin_request("/admin/jobs", method="GET")
        req.args = {"lang": "fr"}
        res = self.admin.dispatch(req)
        assert res is not None
        assert (
            "Tâches d&#x27;arrière-plan &amp; DLQ" in res
            or "Tâches d'arrière-plan & DLQ" in res
        )
        assert (
            "Superviser l&#x27;état des files de tâches" in res
            or "Superviser l'état des files de tâches" in res
        )
        assert "Aucune tâche échouée" in res
        assert "Haute priorité" in res
        assert "File par défaut" in res
        assert "Basse priorité" in res
        assert "Tâches différées" in res
        assert "Dead Letter Queue (DLQ)" in res
        assert "La Dead Letter Queue est actuellement vide" in res
        assert 'polyline points="22 4 12 14.01 9 11.01"' in res

    def test_jobs_retry_one_action(self) -> None:
        from asok.background import _push_local_dlq

        dummy_job = {
            "id": "job-retry-1",
            "module": "tests.test_enterprise_features",
            "function": "sample_retry_task",
            "args": [1],
            "kwargs": {},
            "queue": "default",
            "retries": 0,
        }
        _push_local_dlq(dummy_job, "Fail")

        req = self._make_admin_request("/admin/jobs/retry/job-retry-1", method="POST")
        with pytest.raises(RedirectException) as exc:
            self.admin.dispatch(req)
        assert exc.value.url == "/admin/jobs"
        req.flash.assert_called_with(
            "success", "Job requeued successfully for execution."
        )

    def test_jobs_retry_all_action(self) -> None:
        req = self._make_admin_request("/admin/jobs/retry-all", method="POST")
        with pytest.raises(RedirectException) as exc:
            self.admin.dispatch(req)
        assert exc.value.url == "/admin/jobs"
        assert req.flash.called

    def test_jobs_delete_one_action(self) -> None:
        from asok.background import _push_local_dlq

        dummy_job = {
            "id": "job-delete-1",
            "module": "tests.test_enterprise_features",
            "function": "sample_retry_task",
            "args": [1],
            "kwargs": {},
            "queue": "default",
            "retries": 0,
        }
        _push_local_dlq(dummy_job, "Fail")

        req = self._make_admin_request("/admin/jobs/delete/job-delete-1", method="POST")
        with pytest.raises(RedirectException) as exc:
            self.admin.dispatch(req)
        assert exc.value.url == "/admin/jobs"
        req.flash.assert_called_with("info", "Job removed from Dead Letter Queue.")

    def test_jobs_purge_action(self) -> None:
        req = self._make_admin_request("/admin/jobs/purge", method="POST")
        with pytest.raises(RedirectException) as exc:
            self.admin.dispatch(req)
        assert exc.value.url == "/admin/jobs"
        assert req.flash.called
