from __future__ import annotations

import datetime
import html
from typing import Any

from ...background import (
    delete_dlq_job,
    get_dlq_jobs,
    get_queue_counts,
    purge_dlq,
    retry_all_dlq_jobs,
    retry_dlq_job,
)
from ...exceptions import RedirectException


def _format_failed_at(failed_at: Any) -> str:
    if not failed_at:
        return "—"
    try:
        dt = datetime.datetime.fromtimestamp(float(failed_at))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(failed_at)


def _sanitize_job_item(j: Any) -> dict[str, Any]:
    item = dict(j)
    item["module"] = html.escape(str(item.get("module") or ""))
    item["function"] = html.escape(str(item.get("function") or ""))
    if item.get("error"):
        item["error"] = html.escape(str(item.get("error")))
    item["failed_at_fmt"] = _format_failed_at(item.get("failed_at"))
    return item


class JobsViewsMixin:
    """Admin view mixin for background job queues and Dead Letter Queue management."""

    def _jobs_dashboard(self, request: Any) -> Any:
        self._require_admin_only(request)
        counts = get_queue_counts()
        raw_jobs = get_dlq_jobs(offset=0, limit=100)
        jobs = [_sanitize_job_item(j) for j in raw_jobs]

        return self._render(
            request,
            "jobs.html",
            counts=counts,
            jobs=jobs,
            active="jobs",
            breadcrumbs=[
                {"label": "Dashboard", "url": self.prefix},
                {"label": self.t(request, "Background Jobs & DLQ"), "url": None},
            ],
        )

    def _jobs_retry_one(self, request: Any, job_id: str) -> Any:
        self._require_admin_only(request)
        request.verify_csrf()
        success = retry_dlq_job(job_id)
        if success:
            request.flash(
                "success", self.t(request, "Job requeued successfully for execution.")
            )
        else:
            request.flash("error", self.t(request, "Failed to requeue job from DLQ."))
        raise RedirectException(self.prefix + "/jobs")

    def _jobs_retry_all(self, request: Any) -> Any:
        self._require_admin_only(request)
        request.verify_csrf()
        count = retry_all_dlq_jobs()
        request.flash(
            "success",
            self.t(request, "Requeued {count} failed jobs from DLQ.", count=count),
        )
        raise RedirectException(self.prefix + "/jobs")

    def _jobs_delete_one(self, request: Any, job_id: str) -> Any:
        self._require_admin_only(request)
        request.verify_csrf()
        delete_dlq_job(job_id)
        request.flash("info", self.t(request, "Job removed from Dead Letter Queue."))
        raise RedirectException(self.prefix + "/jobs")

    def _jobs_purge_dlq(self, request: Any) -> Any:
        self._require_admin_only(request)
        request.verify_csrf()
        count = purge_dlq()
        request.flash(
            "info",
            self.t(request, "Purged {count} jobs from Dead Letter Queue.", count=count),
        )
        raise RedirectException(self.prefix + "/jobs")

    def _dispatch_jobs_post_routes(self, request: Any, path: str) -> Any:
        request.verify_csrf()
        if path == "/jobs/retry-all":
            return self._jobs_retry_all(request)
        if path == "/jobs/purge":
            return self._jobs_purge_dlq(request)
        if path.startswith("/jobs/retry/"):
            return self._jobs_retry_one(request, path[len("/jobs/retry/") :])
        if path.startswith("/jobs/delete/"):
            return self._jobs_delete_one(request, path[len("/jobs/delete/") :])
        return None

    def _handle_jobs_routes(self, request: Any, path: str, method: str) -> Any:
        if path == "/jobs":
            return self._jobs_dashboard(request)
        if method == "POST":
            res = self._dispatch_jobs_post_routes(request, path)
            if res is not None:
                return res
        return None
