from __future__ import annotations

import datetime
import os
from typing import Any

from ...exceptions import RedirectException


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

    def _delete_media(self, request: Any, rel_path: str) -> None:
        self._require_admin(request)
        # SECURITY: Prevent path traversal with comprehensive validation
        rel_path = rel_path.lstrip("/")

        # Normalize path first to detect encoded or obfuscated traversal attempts
        # e.g., %2e%2e, .%2F, double slashes, etc.
        normalized = os.path.normpath(rel_path)

        # Check for path traversal sequences in normalized path
        if (
            ".." in normalized
            or normalized.startswith("/")
            or normalized.startswith("\\")
        ):
            return self._forbid(request)

        base_dir = os.path.abspath(
            os.path.join(self.app.root_dir, "src/partials/uploads")
        )
        full_path = os.path.abspath(os.path.join(base_dir, normalized))
        try:
            common = os.path.commonpath([full_path, base_dir])
        except ValueError:
            common = ""
        if common != base_dir:
            return self._forbid(request)

        # Reject symlinks to avoid deleting files outside the media directory
        if os.path.islink(full_path):
            return self._forbid(request)

        if os.path.isfile(full_path):
            os.remove(full_path)
            request.flash("success", self.t(request, "File deleted"))
        else:
            request.flash("error", self.t(request, "File not found"))

        raise RedirectException(self.prefix + "/media")

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
            ext = os.path.splitext(file.filename)[1].lower()

            # Sorting logic based on user requirements
            if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
                subdir = "images"
            elif ext == ".pdf":
                subdir = "pdfs"
            else:
                subdir = "others"

            # UploadedFile.save(path) prepends src/partials/uploads in Asok
            rel_dest = os.path.join(subdir, file.filename)
            try:
                file.save(rel_dest)
                count += 1
            except ValueError as e:
                # Capture validation errors (magic bytes, mime-type mismatch, etc.)
                errors.append(f"{file.filename}: {str(e)}")

        if errors:
            for err in errors:
                request.flash("error", err)

        if count > 0:
            request.flash(
                "success",
                self.t(request, "Successfully uploaded {count} file(s)", count=count),
            )

        raise RedirectException(self.prefix + "/media")
