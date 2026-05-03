from __future__ import annotations

import html
from typing import TYPE_CHECKING, Any, Callable, Optional, Union

from .templates import SafeString

if TYPE_CHECKING:
    from .orm import Query
    from .request import Request


class TableColumn:
    """Configuration for a single table column."""
    def __init__(
        self,
        name: str,
        label: Optional[str] = None,
        render: Optional[Callable[[Any], str]] = None,
        sortable: bool = False,
        template: Optional[str] = None,
        class_: str = ""
    ):
        self.name = name
        self.label = label or name.replace("_", " ").title()
        self.render_fn = render
        self.sortable = sortable
        self.template = template
        self.class_ = class_

    def render(self, row: Any) -> str:
        if self.render_fn:
            return self.render_fn(row)

        # Default rendering
        if isinstance(row, (str, int, float, bool)) or row is None:
            val = row
        elif isinstance(row, dict):
            val = row.get(self.name, "")
        else:
            val = getattr(row, self.name, "")

        if val is True:
            return '<span class="asok-badge asok-badge-success">Yes</span>'
        if val is False:
            return '<span class="asok-badge asok-badge-danger">No</span>'
        if val is None:
            return '<span class="text-muted">—</span>'

        return html.escape(str(val))


class Table:
    """Powerful, automated data table with search, filters, and pagination."""

    def __init__(
        self,
        query: Union[Query, list, dict],
        request: Optional[Request] = None,
        columns: Optional[list[Union[str, TableColumn]]] = None,
        class_: str = "asok-table-container",
        **attrs
    ):
        self.query = query
        self.request = request
        self._columns: list[TableColumn] = []
        self._search_fields: list[str] = []
        self._filters: dict[str, list[Any]] = {}
        self._actions: list[tuple] = []
        self._per_page = 10
        self._reactive = False
        self.class_ = class_
        self.attrs = attrs

        # Initialize columns
        if columns:
            for col in columns:
                if isinstance(col, str):
                    self._columns.append(TableColumn(col))
                else:
                    self._columns.append(col)
        elif hasattr(query, "model") and hasattr(query.model, "_fields"):
            # Auto-detect columns from Model
            for name, field in query.model._fields.items():
                if name == "id" or getattr(field, "hidden", False):
                    continue
                self._columns.append(TableColumn(name, label=field.label))
        elif isinstance(query, list) and len(query) > 0:
            # Auto-detect from simple list, list of dicts, or list of Models
            first = query[0]
            if isinstance(first, dict):
                for key in first.keys():
                    self._columns.append(TableColumn(key))
            elif hasattr(first, "_fields") or hasattr(type(first), "_fields"):
                # It's a Model instance
                fields = getattr(first, "_fields", getattr(type(first), "_fields", {}))
                for name, field in fields.items():
                    if name == "id" or getattr(field, "hidden", False):
                        continue
                    label = getattr(field, "label", None) or name.replace("_", " ").title()
                    self._columns.append(TableColumn(name, label=label))
            elif not hasattr(first, "__dict__"):
                # Simple list (strings, numbers)
                self._columns.append(TableColumn("value", label="Item"))
            elif hasattr(first, "__dict__"):
                # Generic object, use __dict__ keys excluding private ones
                for key in first.__dict__.keys():
                    if not key.startswith("_"):
                        self._columns.append(TableColumn(key))

    def searchable(self, fields: list[str]) -> Table:
        self._search_fields = fields
        return self

    def filterable(self, filters: dict[str, list[Any]]) -> Table:
        self._filters.update(filters)
        return self

    def actions(self, actions_list: list[Union[tuple[str, str, str], tuple[str, str, str, dict]]]) -> Table:
        """Add action buttons. Format: (label, url_pattern, icon, options)"""
        processed = []
        for a in actions_list:
            if len(a) == 3:
                processed.append((*a, {}))
            else:
                processed.append(a)
        self._actions = processed
        return self

    def paginate(self, per_page: int = 10) -> Table:
        self._per_page = per_page
        return self

    def reactive(self, enabled: bool = True) -> Table:
        """Enable client-side reactivity using Asok directives."""
        self._reactive = enabled
        return self

    def _get_data(self) -> dict[str, Any]:
        """Process the query with search, filters, and pagination."""
        if isinstance(self.query, (list, dict)):
            if isinstance(self.query, dict) and "items" in self.query:
                return self.query
            return {"items": self.query, "total": len(self.query), "pages": 1, "current_page": 1}

        # It's a Query object
        q = self.query

        # 1. Apply Search
        search_query = self.request.get("search") if self.request else None
        if search_query and self._search_fields:
            for i, field in enumerate(self._search_fields):
                if i == 0:
                    q = q.where(field, "LIKE", f"%{search_query}%")
                else:
                    q = q.or_where(field, "LIKE", f"%{search_query}%")

        # 2. Apply Filters
        if self.request:
            for key in self._filters:
                val = self.request.get(f"filter_{key}")
                if val:
                    q = q.where(key, val)

        # 3. Apply Pagination
        page = int(self.request.get("page", 1)) if self.request else 1
        return q.paginate(page=page, per_page=self._per_page)

    def _to_json_list(self, items: list) -> list[dict]:
        """Convert objects or dicts to a plain list of dicts for JS."""
        result = []
        for item in items:
            row = {}
            if hasattr(item, "to_dict"):
                row = item.to_dict()
            elif isinstance(item, dict):
                row = item
            elif hasattr(item, "_fields") or hasattr(type(item), "_fields"):
                fields = getattr(item, "_fields", getattr(type(item), "_fields", {}))
                for name in fields:
                    row[name] = getattr(item, name)
                row["id"] = getattr(item, "id", None)
            else:
                row["value"] = item
            result.append(row)
        return result

    def render_reactive(self) -> str:
        import json
        raw_items = []
        if isinstance(self.query, (list, dict)):
            raw_items = self.query["items"] if isinstance(self.query, dict) else self.query
        else:
            raw_items = self.query.all()

        items_json = self._to_json_list(raw_items)
        items_str = json.dumps(items_json).replace("'", "&#39;")

        # State includes selection
        html_out = f'<div class="{self.class_}" asok-state=\'{{ items: {items_str}, search: "", sortCol: "", sortDir: 1, page: 1, perPage: {self._per_page}, selected: [] }}\'>'

        # Header
        html_out += '<div class="asok-table-header">'

        # Bulk Actions Bar (Hidden by default)
        html_out += '<div class="asok-bulk-actions" asok-show="selected.length > 0" asok-cloak>'
        html_out += '<span asok-text="selected.length + \' item(s) selected\'" class="mr-3 font-bold"></span>'
        if self._actions:
            for label, url, icon, opts in self._actions:
                if "delete" in label.lower():
                     # Bulk AJAX: sends selected array as JSON
                     js_bulk = f"if(confirm(\'Delete \' + selected.length + \' items?\')) fetch(\'{url.split('{')[0]}bulk-delete\', {{method:\'POST\', body: JSON.stringify(selected)}}).then(r => {{ if(r.ok) {{ items = items.filter(i => !selected.includes(i.id || i.value)); selected = []; }} }})"
                     html_out += f'<button class="asok-btn-bulk asok-btn-danger" asok-on:click="{js_bulk}">{label}</button>'
        html_out += '</div>'

        html_out += '<div class="asok-table-search" asok-show="selected.length === 0">'
        html_out += '<input type="text" asok-model="search" asok-on:input="page = 1" placeholder="Search..." class="asok-search-input">'
        html_out += '</div>'

        # Total count reactive
        filter_base = "items.filter(i => !search || Object.values(i).some(v => String(v).toLowerCase().includes(search.toLowerCase())))"
        html_out += f'<div class="asok-table-total" asok-text="\'Showing \' + Math.min({filter_base}.length, perPage) + \' / \' + {filter_base}.length + \' entries\'"></div>'
        html_out += '</div>'

        html_out += '<div class="asok-table-wrapper"><table class="asok-table"><thead><tr>'

        # Master Checkbox
        html_out += '<th class="asok-table-checkbox"><input type="checkbox" asok-on:change="selected = $el.checked ? items.map(i => i.id || i.value) : []"></th>'

        for col in self._columns:
            sort_action = f"sortDir = (sortCol === \'{col.name}\' ? -sortDir : 1); sortCol = \'{col.name}\'; page = 1"
            html_out += f'<th asok-on:click="{sort_action}" style="cursor:pointer" class="asok-sortable">'
            html_out += f'{html.escape(col.label)} '
            html_out += f'<span class="asok-sort-icon" asok-class:asok-sort-asc="sortCol==\'{col.name}\' && sortDir==1" asok-class:asok-sort-desc="sortCol==\'{col.name}\' && sortDir==-1"></span>'
            html_out += '</th>'

        if self._actions:
            html_out += '<th>Actions</th>'

        html_out += '</tr></thead>'

        sort_expr = ".sort((a,b) => (a[sortCol] > b[sortCol] ? 1 : -1) * sortDir)"
        slice_expr = ".slice((page-1)*perPage, page*perPage)"

        html_out += f'<tbody><template asok-for="item in {filter_base}{sort_expr}{slice_expr}">'
        html_out += '<tr asok-class:asok-row-selected="selected.includes(item.id || item.value)">'

        # Row Checkbox
        html_out += '<td><input type="checkbox" asok-bind:checked="selected.includes(item.id || item.value)" asok-on:change="const id = item.id || item.value; if($el.checked) { if(!selected.includes(id)) selected.push(id) } else { selected = selected.filter(x => x !== id) }"></td>'

        import re
        for col in self._columns:
            if col.template:
                # Convert {{ field }} to ' + item.field + '
                js_t = re.sub(r'\{\{\s*(.*?)\s*\}\}', r'\' + (\1) + \'', col.template)
                # Wrap in quotes for asok-html
                html_out += f'<td asok-html="\'{js_t}\'"></td>'
            else:
                html_out += f'<td asok-text="item.{col.name}"></td>'

        # Actions in reactive mode
        if self._actions:
            html_out += '<td class="asok-table-actions">'
            for label, url_pattern, icon, opts in self._actions:
                # Convert {id} to ${item.id} for JS template literal
                js_url = url_pattern.replace("{id}", "${item.id || item.value}")

                if opts.get("ajax"):
                    method = opts.get("method", "POST")
                    confirm_msg = opts.get("confirm", f"Are you sure you want to {label}?")
                    confirm_logic = f"if(confirm('{confirm_msg}')) " if opts.get("confirm") is not False else ""

                    # JS Logic: fetch -> update state
                    js_click = f"{confirm_logic}fetch(`{js_url}`, {{method:'{method}'}}).then(r => {{ if(r.ok) items = items.filter(i => (i.id || i.value) !== (item.id || item.value)) }})"
                    html_out += f'<button asok-on:click="{js_click}" class="asok-btn-table" title="{label}" style="border:none;cursor:pointer">{label}</button>'
                else:
                    html_out += f'<a asok-bind:href="`{js_url}`" class="asok-btn-table" title="{label}">{label}</a>'
            html_out += '</td>'

        html_out += '</tr></template></tbody></table></div>'

        # Reactive Pagination Footer (Hidden if only one page)
        html_out += f'<div class="asok-table-footer" asok-show="{filter_base}.length > perPage" asok-cloak>'
        html_out += f'<div asok-text="\'Page \' + page + \' of \' + Math.ceil({filter_base}.length / perPage)"></div>'
        html_out += '<div class="asok-pagination">'
        html_out += '<button class="asok-page-link" asok-on:click="page = Math.max(1, page - 1)" asok-bind:disabled="page === 1">&laquo; Prev</button>'
        html_out += f'<button class="asok-page-link" asok-on:click="if(page < Math.ceil({filter_base}.length / perPage)) page++" asok-bind:disabled="page >= Math.ceil({filter_base}.length / perPage)">Next &raquo;</button>'
        html_out += '</div></div>'

        html_out += '</div>'
        return html_out

    def render(self) -> str:
        if self._reactive:
            return self.render_reactive()

        data = self._get_data()
        items = data["items"]

        # Build UI parts
        html_out = f'<div class="{self.class_}" asok-state="{{ search: \'\', filters: {{}} }}">'

        # Header (Search & Filters)
        if self._search_fields or self._filters:
            html_out += '<div class="asok-table-header">'

            # Filters
            if self._filters:
                html_out += '<div class="asok-table-filters"><form method="GET" class="asok-filter-form">'
                # Keep search query if present
                search_q = self.request.get("search", "") if self.request else ""
                if search_q:
                    html_out += f'<input type="hidden" name="search" value="{html.escape(search_q)}">'

                for key, choices in self._filters.items():
                    current = self.request.get(f"filter_{key}") if self.request else ""
                    label = key.replace("_", " ").title()
                    html_out += f'<select name="filter_{key}" onchange="this.form.submit()" class="asok-filter-select">'
                    html_out += f'<option value="">— {html.escape(label)} —</option>'
                    for val, lab in choices:
                        sel = "selected" if str(val) == str(current) else ""
                        html_out += f'<option value="{html.escape(str(val))}" {sel}>{html.escape(lab)}</option>'
                    html_out += '</select>'
                html_out += '</form></div>'

            # Search
            if self._search_fields:
                html_out += '<div class="asok-table-search">'
                html_out += '<form method="GET" class="asok-search-form">'
                # Keep filters if present
                if self.request:
                    for k in self._filters:
                        v = self.request.get(f"filter_{k}")
                        if v:
                            html_out += f'<input type="hidden" name="filter_{k}" value="{html.escape(v)}">'

                html_out += f'<input type="text" name="search" value="{html.escape(self.request.get("search", "") if self.request else "")}" placeholder="Search..." class="asok-search-input">'
                html_out += '</form></div>'

            html_out += '</div>'

        # Table
        html_out += '<div class="asok-table-wrapper">'
        html_out += '<table class="asok-table">'

        # THEAD
        html_out += '<thead><tr>'
        for col in self._columns:
            html_out += f'<th>{html.escape(col.label)}</th>'
        if self._actions:
            html_out += '<th>Actions</th>'
        html_out += '</tr></thead>'

        # TBODY
        html_out += '<tbody>'
        if not items:
            col_count = len(self._columns) + (1 if self._actions else 0)
            html_out += f'<tr><td colspan="{col_count}" class="text-center py-8 text-muted">No records found.</td></tr>'
        else:
            for row in items:
                html_out += '<tr>'
                for col in self._columns:
                    html_out += f'<td class="{col.class_}">{col.render(row)}</td>'

                # Actions
                if self._actions:
                    html_out += '<td class="asok-table-actions">'
                    for label, url_pattern, icon, opts in self._actions:
                        # Simple placeholder replacement
                        url = url_pattern
                        if hasattr(row, "id"):
                            url = url.replace("{id}", str(row.id))
                        elif isinstance(row, dict) and "id" in row:
                            url = url.replace("{id}", str(row["id"]))

                        html_out += f'<a href="{url}" class="asok-btn-table" title="{label}">{label}</a>'
                    html_out += '</td>'

                html_out += '</tr>'
        html_out += '</tbody></table></div>'

        # Footer (Pagination)
        if data["pages"] > 1:
            html_out += '<div class="asok-table-footer">'
            html_out += f'<div class="asok-table-info">Showing {len(items)} of {data["total"]} entries</div>'
            html_out += '<div class="asok-pagination">'

            curr = data["current_page"]
            if curr > 1:
                html_out += f'<a href="?page={curr-1}" class="asok-page-link">&laquo; Prev</a>'

            for p in range(1, data["pages"] + 1):
                active = "active" if p == curr else ""
                html_out += f'<a href="?page={p}" class="asok-page-link {active}">{p}</a>'

            if curr < data["pages"]:
                html_out += f'<a href="?page={curr+1}" class="asok-page-link">Next &raquo;</a>'

            html_out += '</div></div>'

        html_out += '</div>'
        return html_out

    def __str__(self) -> str:
        return SafeString(self.render())
