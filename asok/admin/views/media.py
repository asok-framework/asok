from __future__ import annotations

import datetime
import os
from typing import Any, Optional

from ...exceptions import RedirectException
from ..constants import ALLOWED_UPLOAD_MIMES


class MediaViewsMixin:
    # ── Media Manager ────────────────────────────────────────

    _MEDIA_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
    _MEDIA_PREVIEW_LIMIT = 12  # files shown per folder in the grouped overview
    _MEDIA_PER_PAGE = 48  # files per page in the single-folder view

    def _media_manager(self, request: Any) -> Any:
        self._require_admin(request)
        upload_dir = os.path.join(self.app.root_dir, "src/partials/uploads")
        os.makedirs(upload_dir, exist_ok=True)

        files = self._scan_media_files(upload_dir)
        files.sort(key=lambda x: x["mtime"], reverse=True)
        labels = self._upload_folder_labels()

        # ?folder=<name> drills into a single folder with pagination (so a
        # 300-image folder is browsable instead of dumped on one screen).
        folder = request.args.get("folder")
        if folder is not None:
            return self._render_media_folder(request, files, labels, folder)
        return self._render_media_groups(request, files, labels)

    def _render_media_groups(self, request: Any, files: list, labels: dict) -> Any:
        return self._render(
            request,
            "media.html",
            folder_view=False,
            files=files,
            groups=self._group_media_files(files, labels),
            active="media",
            breadcrumbs=[
                {"label": "Dashboard", "url": self.prefix},
                {"label": "Media Manager", "url": None},
            ],
        )

    def _render_media_folder(
        self, request: Any, files: list, labels: dict, folder: str
    ) -> Any:
        folder_files = [f for f in files if f["folder"] == folder]
        pg = self._paginate_media(folder_files, self._media_page_arg(request))
        label = labels.get(folder, folder)
        return self._render(
            request,
            "media.html",
            folder_view=True,
            media_folder=folder,
            media_folder_label=label,
            files=pg["items"],
            page=pg["page"],
            pages=pg["pages"],
            total=pg["total"],
            active="media",
            breadcrumbs=[
                {"label": "Dashboard", "url": self.prefix},
                {"label": "Media Manager", "url": self.prefix + "/media"},
                {"label": label or folder or "Uncategorized", "url": None},
            ],
        )

    @staticmethod
    def _media_page_arg(request: Any) -> int:
        try:
            return max(1, int(request.args.get("page", 1)))
        except (TypeError, ValueError):
            return 1

    def _paginate_media(self, files: list, page: int) -> dict[str, Any]:
        per_page = self._MEDIA_PER_PAGE
        total = len(files)
        pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, pages))
        start = (page - 1) * per_page
        return {
            "items": files[start : start + per_page],
            "page": page,
            "pages": pages,
            "total": total,
        }

    def _scan_media_files(self, upload_dir: str) -> list[dict[str, Any]]:
        """Walk the uploads dir and build the per-file dicts consumed by the template."""
        # SECURITY: filenames/paths are auto-escaped by the template engine on render.
        files: list[dict[str, Any]] = []
        for root, _dirs, filenames in os.walk(upload_dir):
            for f in filenames:
                if f.startswith("."):
                    continue
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, upload_dir)
                stat = os.stat(full_path)
                files.append(
                    {
                        "name": f,
                        "rel_path": rel_path,
                        "url": f"/uploads/{rel_path}",
                        "size": round(stat.st_size / 1024, 1),  # KB
                        "mtime": datetime.datetime.fromtimestamp(
                            stat.st_mtime
                        ).strftime("%Y-%m-%d %H:%M"),
                        "is_image": f.lower().endswith(self._MEDIA_IMAGE_EXTS),
                        "folder": os.path.dirname(rel_path).replace("\\", "/"),
                    }
                )
        return files

    def _upload_folder_labels(self) -> dict[str, str]:
        """Map an upload_to folder to a human label of the model/field that writes it."""
        labels: dict[str, str] = {}
        for entry in getattr(self, "_registered", {}).values():
            self._collect_entry_upload_folders(entry, labels)
        return labels

    def _collect_entry_upload_folders(
        self, entry: dict[str, Any], labels: dict[str, str]
    ) -> None:
        model = entry.get("model")
        if not model:
            return
        label = entry.get("label") or getattr(model, "__name__", "")
        for name, field in getattr(model, "_fields", {}).items():
            folder = self._file_field_folder(field)
            if folder:
                labels.setdefault(folder, f"{label} · {name}")

    @staticmethod
    def _file_field_folder(field: Any) -> str:
        if not getattr(field, "is_file", False):
            return ""
        return (getattr(field, "upload_to", "") or "").strip("/").replace("\\", "/")

    def _group_media_files(
        self, files: list[dict[str, Any]], folder_labels: dict[str, str]
    ) -> list[dict[str, Any]]:
        """Group files by their folder; named folders first (alpha), root ('') last."""
        from collections import OrderedDict

        buckets: "OrderedDict[str, list]" = OrderedDict()
        for f in files:
            buckets.setdefault(f["folder"], []).append(f)
        limit = self._MEDIA_PREVIEW_LIMIT
        groups = [
            {
                "folder": folder,
                "label": folder_labels.get(folder, folder),
                "count": len(items),
                "preview": items[:limit],
                "has_more": len(items) > limit,
                "files": items,
            }
            for folder, items in buckets.items()
        ]
        groups.sort(key=lambda g: (g["folder"] == "", g["label"].lower()))
        return groups

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

    def _flash_upload_results(
        self, request: Any, count: int, errors: list[str]
    ) -> None:
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
