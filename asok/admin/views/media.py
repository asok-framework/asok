from __future__ import annotations

import datetime
import os
from typing import Any, Optional

from ...exceptions import RedirectException
from ..constants import ALLOWED_UPLOAD_MIMES


class MediaViewsMixin:
    # ── Media Manager ────────────────────────────────────────

    def _media_manager(self, request: Any) -> Any:
        self._require_admin(request)
        upload_dir = os.path.join(self.app.root_dir, "src/partials/uploads")
        os.makedirs(upload_dir, exist_ok=True)

        files = []
        for root, dirs, filenames in os.walk(upload_dir):
            for f in filenames:
                if f.startswith("."):
                    continue
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, upload_dir)
                stat = os.stat(full_path)

                # Check if it's an image
                is_img = f.lower().endswith(
                    (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
                )

                # SECURITY: Escape HTML to prevent XSS in filenames
                from html import escape

                files.append(
                    {
                        "name": escape(f),
                        "rel_path": escape(rel_path),
                        "url": f"/uploads/{escape(rel_path)}",
                        "size": round(stat.st_size / 1024, 1),  # KB
                        "mtime": datetime.datetime.fromtimestamp(
                            stat.st_mtime
                        ).strftime("%Y-%m-%d %H:%M"),
                        "is_image": is_img,
                    }
                )

        # Sort by most recent
        files.sort(key=lambda x: x["mtime"], reverse=True)

        return self._render(
            request,
            "media.html",
            files=files,
            active="media",
            breadcrumbs=[
                {"label": "Dashboard", "url": self.prefix},
                {"label": "Media Manager", "url": None},
            ],
        )

    def _has_traversal_seqs(self, normalized: str) -> bool:
        return (
            ".." in normalized
            or normalized.startswith("/")
            or normalized.startswith("\\")
        )

    def _is_safe_under_base(self, base_dir: str, full_path: str) -> bool:
        try:
            common = os.path.commonpath([full_path, base_dir])
        except ValueError:
            return False
        if common != base_dir:
            return False
        return not os.path.islink(full_path)

    def _delete_media(self, request: Any, rel_path: str) -> None:
        self._require_admin(request)
        # Normalize path first to detect encoded or obfuscated traversal attempts
        normalized = os.path.normpath(rel_path.lstrip("/"))

        if self._has_traversal_seqs(normalized):
            return self._forbid(request)

        base_dir = os.path.abspath(
            os.path.join(self.app.root_dir, "src/partials/uploads")
        )
        full_path = os.path.abspath(os.path.join(base_dir, normalized))

        if not self._is_safe_under_base(base_dir, full_path):
            return self._forbid(request)

        if os.path.isfile(full_path):
            os.remove(full_path)
            request.flash("success", self.t(request, "File deleted"))
        else:
            request.flash("error", self.t(request, "File not found"))

        raise RedirectException(self.prefix + "/media")

    def _get_upload_subdir(self, filename: str) -> str:
        ext = os.path.splitext(filename)[1].lower()
        if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
            return "images"
        if ext == ".pdf":
            return "pdfs"
        return "others"

    def _upload_single_file(self, file: Any) -> tuple[bool, Optional[str]]:
        subdir = self._get_upload_subdir(file.filename)
        rel_dest = os.path.join(subdir, file.filename)
        try:
            file.save(rel_dest, allowed_types=list(ALLOWED_UPLOAD_MIMES))
            return True, None
        except ValueError as e:
            return False, f"{file.filename}: {str(e)}"

    def _flash_upload_results(self, request: Any, count: int, errors: list[str]) -> None:
        if errors:
            for err in errors:
                request.flash("error", err)
        if count > 0:
            request.flash(
                "success",
                self.t(request, "Successfully uploaded {count} file(s)", count=count),
            )

    def _media_upload(self, request: Any) -> None:
        self._require_admin(request)
        if request.method != "POST":
            raise RedirectException(self.prefix + "/media")
        if not request.files:
            request.flash("error", self.t(request, "No files selected"))
            raise RedirectException(self.prefix + "/media")

        count = 0
        errors = []
        for file in request.all_files:
            success, err = self._upload_single_file(file)
            if success:
                count += 1
            else:
                errors.append(err)

        self._flash_upload_results(request, count, errors)
        raise RedirectException(self.prefix + "/media")
