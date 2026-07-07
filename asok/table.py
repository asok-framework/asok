from __future__ import annotations

import html
from typing import TYPE_CHECKING, Any, Callable, Optional, Union

from .templates import SafeString, _extract_nested_attrs, _render_attrs

if TYPE_CHECKING:
    from .orm import Query
    from .request import Request


def _merge_class(merged: dict, v: Any) -> None:
    existing = merged.get("class", "") or merged.get("class_", "")
    new_val = v or ""
    if new_val:
        merged["class"] = f"{existing} {new_val}".strip()
    merged.pop("class_", None)


def _merge_style(merged: dict, v: Any) -> None:
    existing = merged.get("style", "")
    if existing and v:
        if not existing.endswith(";"):
            existing += ";"
        merged["style"] = f"{existing}{v}"
    elif v:
        merged["style"] = v


def _resolve_merge_key(k: str) -> str:
    if k.endswith("_") and k != "_":
        return k.rstrip("_")
    return k


def _merge_single_attr(merged: dict, k: str, v: Any) -> None:
    if k in ("class", "class_"):
        _merge_class(merged, v)
    elif k == "style":
        _merge_style(merged, v)
    else:
        merged[_resolve_merge_key(k)] = v


def _merge_attrs(
    default_attrs: dict[str, Any], *other_attrs_list: dict[str, Any]
) -> dict[str, Any]:
    """Helper to merge attribute dictionaries, combining classes and styles cleanly."""
    merged = dict(default_attrs)
    for other in other_attrs_list:
        if other:
            for k, v in other.items():
                _merge_single_attr(merged, k, v)
    return merged


class TableColumn:
    """Configuration for a single table column."""

    def __init__(
        self,
        name: str,
        label: Optional[str] = None,
        render: Optional[Callable[[Any], str]] = None,
        sortable: bool = False,
        template: Optional[str] = None,
        class_: str = "",
        **kwargs,
    ):
        self.name = name
        self.label = label or name.replace("_", " ").title()
        self.render_fn = render
        self.sortable = sortable
        self.template = template
        self.class_ = class_
        self.attrs = kwargs

    def _get_default_val(self, row: Any) -> Any:
        if isinstance(row, (str, int, float, bool)) or row is None:
            return row
        if isinstance(row, dict):
            return row.get(self.name, "")
        return getattr(row, self.name, "")

    def _format_val(self, val: Any) -> str:
        if val is True:
            return '<span class="asok-badge asok-badge-success">Yes</span>'
        if val is False:
            return '<span class="asok-badge asok-badge-danger">No</span>'
        if val is None:
            return '<span class="text-muted">—</span>'
        return html.escape(str(val))

    def render(self, row: Any) -> str:
        """Render the column value for a given data row, applying custom formatting if defined."""
        if self.render_fn:
            return self.render_fn(row)
        return self._format_val(self._get_default_val(row))


class Table:
    """Powerful, automated data table with search, filters, and pagination."""

    def __init__(
        self,
        query: Union[Query, list, dict],
        request: Optional[Request] = None,
        columns: Optional[list[Union[str, TableColumn]]] = None,
        class_: str = "asok-table-container",
        **attrs,
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
        self._columns = self._detect_columns(query, columns)

    def _wrap_column(self, col: Union[str, TableColumn]) -> TableColumn:
        return TableColumn(col) if isinstance(col, str) else col

    def _detect_columns(
        self,
        query: Union[Query, list, dict],
        columns: Optional[list[Union[str, TableColumn]]],
    ) -> list[TableColumn]:
        if columns:
            return [self._wrap_column(c) for c in columns]
        return self._detect_columns_fallback(query)

    def _detect_columns_fallback(self, query: Any) -> list[TableColumn]:
        if hasattr(query, "model") and hasattr(query.model, "_fields"):
            return self._detect_columns_from_model(query)
        if isinstance(query, list) and len(query) > 0:
            return self._detect_columns_from_list(query)
        return []

    def _detect_columns_from_model(self, query: Any) -> list[TableColumn]:
        res = []
        for name, field in query.model._fields.items():
            if name == "id" or getattr(field, "hidden", False):
                continue
            res.append(TableColumn(name, label=field.label))
        return res

    def _detect_cols_dict(self, first: dict) -> list[TableColumn]:
        return [TableColumn(k) for k in first.keys()]

    def _detect_cols_fields(self, first: Any) -> list[TableColumn]:
        res = []
        fields = getattr(first, "_fields", getattr(type(first), "_fields", {}))
        for name, field in fields.items():
            if name == "id" or getattr(field, "hidden", False):
                continue
            label = getattr(field, "label", None) or name.replace("_", " ").title()
            res.append(TableColumn(name, label=label))
        return res

    def _detect_cols_dict_attrs(self, first: Any) -> list[TableColumn]:
        res = []
        for key in first.__dict__.keys():
            if not key.startswith("_"):
                res.append(TableColumn(key))
        return res

    def _detect_columns_from_list(self, query: list) -> list[TableColumn]:
        first = query[0]
        if isinstance(first, dict):
            return self._detect_cols_dict(first)
        if hasattr(first, "_fields") or hasattr(type(first), "_fields"):
            return self._detect_cols_fields(first)
        if not hasattr(first, "__dict__"):
            return [TableColumn("value", label="Item")]
        return self._detect_cols_dict_attrs(first)

    @property
    def columns(self) -> list[TableColumn]:
        """Get the list of configured table columns."""
        return self._columns

    @columns.setter
    def columns(self, cols: list[Union[str, TableColumn]]):
        """Set the list of configured table columns."""
        self._columns = []
        for col in cols:
            if isinstance(col, str):
                self._columns.append(TableColumn(col))
            else:
                self._columns.append(col)

    def searchable(self, fields: list[str]) -> Table:
        """Specify which fields should be included in the keyword search."""
        self._search_fields = fields
        return self

    def filterable(self, filters: dict[str, list[Any]]) -> Table:
        """Configure filter dropdowns for specific columns."""
        self._filters.update(filters)
        return self

    def actions(
        self,
        actions_list: list[Union[tuple[str, str, str], tuple[str, str, str, dict]]],
    ) -> Table:
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
        """Set the number of items to display per page."""
        self._per_page = per_page
        return self

    def reactive(self, enabled: bool = True) -> Table:
        """Enable client-side reactivity using Asok directives."""
        self._reactive = enabled
        return self

    def _limit_list_size(self, lst: list) -> list:
        return lst[:10_000] if len(lst) > 10_000 else lst

    def _get_data_from_collection(self) -> dict[str, Any]:
        if isinstance(self.query, dict) and "items" in self.query:
            items = self.query.get("items", [])
            if isinstance(items, list):
                self.query["items"] = self._limit_list_size(items)
            return self.query
        limited_query = (
            self._limit_list_size(self.query)
            if isinstance(self.query, list)
            else self.query
        )
        return {
            "items": limited_query,
            "total": len(limited_query),
            "pages": 1,
            "current_page": 1,
        }

    def _get_search_query(self) -> Optional[str]:
        if not self.request:
            return None
        q = self.request.get("search")
        if q and len(q) > 200:
            return q[:200]
        return q

    def _apply_search(self, q: Any) -> Any:
        search_query = self._get_search_query()
        if not (search_query and self._search_fields):
            return q
        for i, field in enumerate(self._search_fields):
            if i == 0:
                q = q.where(field, "LIKE", f"%{search_query}%")
            else:
                q = q.or_where(field, "LIKE", f"%{search_query}%")
        return q

    def _apply_filters(self, q: Any) -> Any:
        if not self.request:
            return q
        for key in self._filters:
            val = self.request.get(f"filter_{key}")
            if val:
                q = q.where(key, val)
        return q

    def _get_data_from_query(self) -> dict[str, Any]:
        q = self._apply_search(self.query)
        q = self._apply_filters(q)
        page = int(self.request.get("page", 1)) if self.request else 1
        return q.paginate(page=page, per_page=self._per_page)

    def _get_data(self) -> dict[str, Any]:
        """Process the query with search, filters, and pagination.

        SECURITY: Search query length limits prevent LIKE query DoS.
        """
        if isinstance(self.query, (list, dict)):
            return self._get_data_from_collection()
        return self._get_data_from_query()

    def _fields_item_to_dict(self, item: Any) -> dict:
        row = {}
        fields = getattr(item, "_fields", getattr(type(item), "_fields", {}))
        for name in fields:
            row[name] = getattr(item, name)
        row["id"] = getattr(item, "id", None)
        return row

    def _item_to_dict(self, item: Any) -> dict:
        if hasattr(item, "to_dict"):
            return item.to_dict()
        if isinstance(item, dict):
            return item
        if hasattr(item, "_fields") or hasattr(type(item), "_fields"):
            return self._fields_item_to_dict(item)
        return {"value": item}

    def _to_json_list(self, items: list) -> list[dict]:
        """Convert objects or dicts to a plain list of dicts for JS."""
        return [self._item_to_dict(item) for item in items]

    def _render_reactive_bulk(self) -> str:
        bulk_attrs = _extract_nested_attrs(self.attrs, "bulk")
        merged_bulk = _merge_attrs({"class": "asok-bulk-actions"}, bulk_attrs)
        html_out = f'<div asok-show="selected.length > 0" asok-cloak {_render_attrs(merged_bulk)}>'
        html_out += '<span asok-text="selected.length + \' item(s) selected\'" class="mr-3 font-bold"></span>'
        if self._actions:
            for label, url, icon, opts in self._actions:
                if "delete" in label.lower():
                    # Bulk AJAX: sends selected array as JSON
                    js_bulk = f"if(confirm('Delete ' + selected.length + ' items?')) fetch('{url.split('{')[0]}bulk-delete', {{method:'POST', body: JSON.stringify(selected)}}).then(r => {{ if(r.ok) {{ items = items.filter(i => !selected.includes(i.id || i.value)); selected = []; }} }})"
                    html_out += f'<button class="asok-btn-bulk asok-btn-danger" asok-on:click="{js_bulk}">{label}</button>'
        html_out += "</div>"
        return html_out

    def _render_reactive_search(self) -> str:
        search_container_attrs = _extract_nested_attrs(self.attrs, "search_container")
        merged_search_container = _merge_attrs(
            {"class": "asok-table-search"}, search_container_attrs
        )
        html_out = f'<div asok-show="selected.length === 0" {_render_attrs(merged_search_container)}>'
        search_input_attrs = _extract_nested_attrs(self.attrs, "search")
        merged_search_input = _merge_attrs(
            {"class": "asok-search-input"}, search_input_attrs
        )
        html_out += f'<input type="text" asok-model="search" asok-on:input="page = 1" placeholder="Search..." {_render_attrs(merged_search_input)}>'
        html_out += "</div>"
        return html_out

    def _render_reactive_th(self, col: TableColumn) -> str:
        global_th_attrs = _extract_nested_attrs(self.attrs, "th")
        col_th_attrs = _extract_nested_attrs(col.attrs, "th")
        if col.sortable:
            default_th = {
                "class": "asok-sortable",
                "style": "cursor:pointer",
                "asok-on:click": f"sortDir = (sortCol === '{col.name}' ? -sortDir : 1); sortCol = '{col.name}'; page = 1",
            }
        else:
            default_th = {}
        merged_th = _merge_attrs(default_th, global_th_attrs, col_th_attrs)

        html_out = f"<th {_render_attrs(merged_th)}>"
        html_out += f"{html.escape(col.label)} "
        if col.sortable:
            html_out += f'<span class="asok-sort-icon" asok-class:asok-sort-asc="sortCol==\'{col.name}\' && sortDir==1" asok-class:asok-sort-desc="sortCol==\'{col.name}\' && sortDir==-1"></span>'
        html_out += "</th>"
        return html_out

    def _render_reactive_actions(self) -> str:
        html_out = '<td class="asok-table-actions">'
        for label, url_pattern, icon, opts in self._actions:
            # Convert {id} to ${item.id} for JS template literal
            js_url = url_pattern.replace("{id}", "${item.id || item.value}")

            if opts.get("ajax"):
                method = opts.get("method", "POST")
                confirm_msg = opts.get("confirm", f"Are you sure you want to {label}?")
                confirm_logic = (
                    f"if(confirm('{confirm_msg}')) "
                    if opts.get("confirm") is not False
                    else ""
                )

                # JS Logic: fetch -> update state
                js_click = f"{confirm_logic}fetch(`{js_url}`, {{method:'{method}'}}).then(r => {{ if(r.ok) items = items.filter(i => (i.id || i.value) !== (item.id || item.value)) }})"
                html_out += f'<button asok-on:click="{js_click}" class="asok-btn-table" title="{label}" style="border:none;cursor:pointer">{label}</button>'
            else:
                html_out += f'<a asok-bind:href="`{js_url}`" class="asok-btn-table" title="{label}">{label}</a>'
        html_out += "</td>"
        return html_out

    def _render_reactive_row(self) -> str:
        import re

        tr_attrs = _extract_nested_attrs(self.attrs, "tr")
        default_tr = {
            "asok-class:asok-row-selected": "selected.includes(item.id || item.value)"
        }
        merged_tr = _merge_attrs(default_tr, tr_attrs)
        html_out = f"<tr {_render_attrs(merged_tr)}>"

        # Row Checkbox
        html_out += '<td><input type="checkbox" asok-bind:checked="selected.includes(item.id || item.value)" asok-on:change="const id = item.id || item.value; if($el.checked) { if(!selected.includes(id)) selected.push(id) } else { selected = selected.filter(x => x !== id) }"></td>'

        for col in self._columns:
            global_td_attrs = _extract_nested_attrs(self.attrs, "td")
            col_td_attrs = _extract_nested_attrs(col.attrs, "td")
            default_td = {}
            if col.class_:
                default_td["class"] = col.class_
            merged_td = _merge_attrs(default_td, global_td_attrs, col_td_attrs)

            if col.template:
                # Clean newlines to prevent multiline string syntax errors in AST validation
                clean_template = col.template.replace("\n", " ").replace("\r", " ")
                # Convert {{ field }} to ' + item.field + '
                js_t = re.sub(r"\{\{\s*(.*?)\s*\}\}", r"' + (\1) + '", clean_template)
                # Wrap in quotes for asok-html and escape for HTML attribute safety
                esc_val = html.escape(f"'{js_t}'")
                html_out += (
                    f'<td asok-html="{esc_val}" {_render_attrs(merged_td)}></td>'
                )
            else:
                merged_td["asok-text"] = f"item.{col.name}"
                html_out += f"<td {_render_attrs(merged_td)}></td>"

        # Actions in reactive mode
        if self._actions:
            html_out += self._render_reactive_actions()

        html_out += "</tr>"
        return html_out

    def _reactive_state_json(self) -> str:
        import json

        raw_items = []
        if isinstance(self.query, (list, dict)):
            raw_items = (
                self.query["items"] if isinstance(self.query, dict) else self.query
            )
        else:
            raw_items = self.query.all()
        items_json = self._to_json_list(raw_items)
        return json.dumps(items_json).replace("'", "&#39;")

    def _render_reactive_header(self, filter_base: str) -> str:
        header_attrs = _extract_nested_attrs(self.attrs, "header")
        merged_header = _merge_attrs({"class": "asok-table-header"}, header_attrs)
        html_out = f"<div {_render_attrs(merged_header)}>"
        html_out += self._render_reactive_bulk()
        html_out += self._render_reactive_search()
        total_attrs = _extract_nested_attrs(self.attrs, "total")
        merged_total = _merge_attrs({"class": "asok-table-total"}, total_attrs)
        html_out += f"<div asok-text=\"'Showing ' + Math.min({filter_base}.length, perPage) + ' / ' + {filter_base}.length + ' entries'\" {_render_attrs(merged_total)}></div>"
        html_out += "</div>"
        return html_out

    def _render_reactive_table_body(self, filter_base: str) -> str:
        wrapper_attrs = _extract_nested_attrs(self.attrs, "wrapper")
        merged_wrapper = _merge_attrs({"class": "asok-table-wrapper"}, wrapper_attrs)
        table_attrs = _extract_nested_attrs(self.attrs, "table")
        merged_table = _merge_attrs({"class": "asok-table"}, table_attrs)
        html_out = f"<div {_render_attrs(merged_wrapper)}><table {_render_attrs(merged_table)}><thead><tr>"

        checkbox_attrs = _extract_nested_attrs(self.attrs, "checkbox")
        merged_checkbox = _merge_attrs({"class": "asok-table-checkbox"}, checkbox_attrs)
        html_out += f'<th {_render_attrs(merged_checkbox)}><input type="checkbox" asok-bind:checked="selected.length === items.length && items.length > 0" asok-on:change="selected = $el.checked ? items.map(i => i.id || i.value) : []"></th>'

        for col in self._columns:
            html_out += self._render_reactive_th(col)

        if self._actions:
            actions_th_attrs = _extract_nested_attrs(self.attrs, "actions_th")
            merged_actions_th = _merge_attrs({}, actions_th_attrs)
            html_out += f"<th {_render_attrs(merged_actions_th)}>Actions</th>"

        html_out += "</tr></thead>"
        sort_expr = ".sort((a,b) => (a[sortCol] > b[sortCol] ? 1 : -1) * sortDir)"
        slice_expr = ".slice((page-1)*perPage, page*perPage)"
        tbody_attrs = _extract_nested_attrs(self.attrs, "tbody")
        html_out += f'<tbody {_render_attrs(tbody_attrs)}><template asok-for="item in {filter_base}{sort_expr}{slice_expr}">'
        html_out += self._render_reactive_row()
        html_out += "</template></tbody></table></div>"
        return html_out

    def _render_reactive_footer(self, filter_base: str) -> str:
        footer_attrs = _extract_nested_attrs(self.attrs, "footer")
        merged_footer = _merge_attrs({"class": "asok-table-footer"}, footer_attrs)
        html_out = f'<div asok-show="{filter_base}.length > perPage" asok-cloak {_render_attrs(merged_footer)}>'
        html_out += f"<div asok-text=\"'Page ' + page + ' of ' + Math.ceil({filter_base}.length / perPage)\"></div>"
        pagination_attrs = _extract_nested_attrs(self.attrs, "pagination")
        merged_pagination = _merge_attrs({"class": "asok-pagination"}, pagination_attrs)
        html_out += f"<div {_render_attrs(merged_pagination)}>"
        page_link_attrs = _extract_nested_attrs(self.attrs, "page_link")

        prev_attrs = _merge_attrs(
            {
                "class": "asok-page-link",
                "asok-on:click": "page = Math.max(1, page - 1)",
                "asok-bind:disabled": "page === 1",
            },
            page_link_attrs,
        )
        html_out += f"<button {_render_attrs(prev_attrs)}>&laquo; Prev</button>"

        next_attrs = _merge_attrs(
            {
                "class": "asok-page-link",
                "asok-on:click": f"if(page < Math.ceil({filter_base}.length / perPage)) page++",
                "asok-bind:disabled": f"page >= Math.ceil({filter_base}.length / perPage)",
            },
            page_link_attrs,
        )
        html_out += f"<button {_render_attrs(next_attrs)}>Next &raquo;</button>"
        html_out += "</div></div>"
        return html_out

    def render_reactive(self) -> str:
        """Render the table in reactive mode using client-side directives and state management."""
        items_str = self._reactive_state_json()
        container_attrs = {k: v for k, v in self.attrs.items() if "__" not in k}
        merged_container = _merge_attrs({"class": self.class_}, container_attrs)
        html_out = f'<div {_render_attrs(merged_container)} asok-state=\'{{ items: {items_str}, search: "", sortCol: "", sortDir: 1, page: 1, perPage: {self._per_page}, selected: [] }}\'>'

        filter_base = "items.filter(i => !search || Object.values(i).some(v => String(v).toLowerCase().includes(search.toLowerCase())))"
        html_out += self._render_reactive_header(filter_base)
        html_out += self._render_reactive_table_body(filter_base)
        html_out += self._render_reactive_footer(filter_base)
        html_out += "</div>"
        return html_out

    def _render_single_filter(self, key: str, choices: list, request: Any) -> str:
        current = request.get(f"filter_{key}") if request else ""
        label = key.replace("_", " ").title()
        filter_select_attrs = _extract_nested_attrs(self.attrs, "filter")
        merged_filter_select = _merge_attrs(
            {
                "class": "asok-filter-select",
                "name": f"filter_{key}",
                "onchange": "this.form.submit()",
            },
            filter_select_attrs,
        )
        html_out = f"<select {_render_attrs(merged_filter_select)}>"
        html_out += f'<option value="">— {html.escape(label)} —</option>'
        for val, lab in choices:
            sel = "selected" if str(val) == str(current) else ""
            html_out += f'<option value="{html.escape(str(val))}" {sel}>{html.escape(lab)}</option>'
        html_out += "</select>"
        return html_out

    def _render_server_filters(self, request: Any) -> str:
        filter_container_attrs = _extract_nested_attrs(self.attrs, "filter_container")
        merged_filter_container = _merge_attrs(
            {"class": "asok-table-filters"}, filter_container_attrs
        )
        html_out = f'<div {_render_attrs(merged_filter_container)}><form method="GET" class="asok-filter-form">'
        search_q = request.get("search", "") if request else ""
        if search_q:
            html_out += (
                f'<input type="hidden" name="search" value="{html.escape(search_q)}">'
            )
        for key, choices in self._filters.items():
            html_out += self._render_single_filter(key, choices, request)
        html_out += "</form></div>"
        return html_out

    def _render_server_search(self, request: Any) -> str:
        search_container_attrs = _extract_nested_attrs(self.attrs, "search_container")
        merged_search_container = _merge_attrs(
            {"class": "asok-table-search"}, search_container_attrs
        )
        html_out = f"<div {_render_attrs(merged_search_container)}>"
        html_out += '<form method="GET" class="asok-search-form">'
        # Keep filters if present
        if request:
            for k in self._filters:
                v = request.get(f"filter_{k}")
                if v:
                    html_out += f'<input type="hidden" name="filter_{k}" value="{html.escape(v)}">'

        search_input_attrs = _extract_nested_attrs(self.attrs, "search")
        search_val = request.get("search", "") if request else ""
        merged_search_input = _merge_attrs(
            {
                "class": "asok-search-input",
                "type": "text",
                "name": "search",
                "value": search_val,
                "placeholder": "Search...",
            },
            search_input_attrs,
        )
        html_out += f"<input {_render_attrs(merged_search_input)}>"
        html_out += "</form></div>"
        return html_out

    def _render_server_header(self) -> str:
        if not self._search_fields and not self._filters:
            return ""
        header_attrs = _extract_nested_attrs(self.attrs, "header")
        merged_header = _merge_attrs({"class": "asok-table-header"}, header_attrs)
        html_out = f"<div {_render_attrs(merged_header)}>"

        # Filters
        if self._filters:
            html_out += self._render_server_filters(self.request)

        # Search
        if self._search_fields:
            html_out += self._render_server_search(self.request)

        html_out += "</div>"
        return html_out

    def _render_row_cols(self, row: Any) -> str:
        html_out = ""
        for col in self._columns:
            global_td_attrs = _extract_nested_attrs(self.attrs, "td")
            col_td_attrs = _extract_nested_attrs(col.attrs, "td")
            default_td = {"class": col.class_} if col.class_ else {}
            merged_td = _merge_attrs(default_td, global_td_attrs, col_td_attrs)
            html_out += f"<td {_render_attrs(merged_td)}>{col.render(row)}</td>"
        return html_out

    def _get_action_url(self, row: Any, url_pattern: str) -> str:
        if hasattr(row, "id"):
            return url_pattern.replace("{id}", html.escape(str(row.id)))
        if isinstance(row, dict) and "id" in row:
            return url_pattern.replace("{id}", html.escape(str(row["id"])))
        return url_pattern

    def _render_row_actions(self, row: Any) -> str:
        if not self._actions:
            return ""
        html_out = '<td class="asok-table-actions">'
        for label, url_pattern, icon, opts in self._actions:
            url = self._get_action_url(row, url_pattern)
            html_out += f'<a href="{url}" class="asok-btn-table" title="{html.escape(label)}">{html.escape(label)}</a>'
        html_out += "</td>"
        return html_out

    def _render_server_row(self, row: Any) -> str:
        tr_attrs = _extract_nested_attrs(self.attrs, "tr")
        merged_tr = _merge_attrs({}, tr_attrs)
        return f"<tr {_render_attrs(merged_tr)}>{self._render_row_cols(row)}{self._render_row_actions(row)}</tr>"

    def _render_server_thead(self) -> str:
        thead_attrs = _extract_nested_attrs(self.attrs, "thead")
        html_out = f"<thead {_render_attrs(thead_attrs)}><tr>"
        for col in self._columns:
            global_th_attrs = _extract_nested_attrs(self.attrs, "th")
            col_th_attrs = _extract_nested_attrs(col.attrs, "th")
            merged_th = _merge_attrs({}, global_th_attrs, col_th_attrs)
            html_out += f"<th {_render_attrs(merged_th)}>{html.escape(col.label)}</th>"
        if self._actions:
            actions_th_attrs = _extract_nested_attrs(self.attrs, "actions_th")
            merged_actions_th = _merge_attrs({}, actions_th_attrs)
            html_out += f"<th {_render_attrs(merged_actions_th)}>Actions</th>"
        html_out += "</tr></thead>"
        return html_out

    def _render_server_tbody(self, items: list) -> str:
        tbody_attrs = _extract_nested_attrs(self.attrs, "tbody")
        html_out = f"<tbody {_render_attrs(tbody_attrs)}>"
        if not items:
            col_count = len(self._columns) + (1 if self._actions else 0)
            html_out += f'<tr><td colspan="{col_count}" class="text-center py-8 text-muted">No records found.</td></tr>'
        else:
            for row in items:
                html_out += self._render_server_row(row)
        html_out += "</tbody>"
        return html_out

    def _render_server_table(self, items: list) -> str:
        wrapper_attrs = _extract_nested_attrs(self.attrs, "wrapper")
        merged_wrapper = _merge_attrs({"class": "asok-table-wrapper"}, wrapper_attrs)
        table_attrs = _extract_nested_attrs(self.attrs, "table")
        merged_table = _merge_attrs({"class": "asok-table"}, table_attrs)
        return f"<div {_render_attrs(merged_wrapper)}><table {_render_attrs(merged_table)}>{self._render_server_thead()}{self._render_server_tbody(items)}</table></div>"

    def _render_pagination_links(self, curr: int, total_pages: int) -> str:
        html_out = ""
        page_link_attrs = _extract_nested_attrs(self.attrs, "page_link")
        if curr > 1:
            prev_attrs = _merge_attrs(
                {"class": "asok-page-link", "href": f"?page={curr - 1}"},
                page_link_attrs,
            )
            html_out += f"<a {_render_attrs(prev_attrs)}>&laquo; Prev</a>"

        for p in range(1, total_pages + 1):
            active = "active" if p == curr else ""
            link_attrs = _merge_attrs(
                {"class": f"asok-page-link {active}".strip(), "href": f"?page={p}"},
                page_link_attrs,
            )
            html_out += f"<a {_render_attrs(link_attrs)}>{p}</a>"

        if curr < total_pages:
            next_attrs = _merge_attrs(
                {"class": "asok-page-link", "href": f"?page={curr + 1}"},
                page_link_attrs,
            )
            html_out += f"<a {_render_attrs(next_attrs)}>Next &raquo;</a>"
        return html_out

    def _render_server_footer(self, data: dict, items: list) -> str:
        if data["pages"] <= 1:
            return ""
        footer_attrs = _extract_nested_attrs(self.attrs, "footer")
        merged_footer = _merge_attrs({"class": "asok-table-footer"}, footer_attrs)
        html_out = f"<div {_render_attrs(merged_footer)}>"
        html_out += f'<div class="asok-table-info">Showing {len(items)} of {data["total"]} entries</div>'
        pagination_attrs = _extract_nested_attrs(self.attrs, "pagination")
        merged_pagination = _merge_attrs({"class": "asok-pagination"}, pagination_attrs)
        html_out += f"<div {_render_attrs(merged_pagination)}>"
        html_out += self._render_pagination_links(data["current_page"], data["pages"])
        html_out += "</div></div>"
        return html_out

    def render(self) -> str:
        """Generate the HTML representation of the table (reactive or server-side)."""
        if self._reactive:
            return self.render_reactive()

        data = self._get_data()
        items = data["items"]

        # Build UI parts. Filter non-prefixed attrs for the container
        container_attrs = {k: v for k, v in self.attrs.items() if "__" not in k}
        merged_container = _merge_attrs({"class": self.class_}, container_attrs)
        html_out = f"<div {_render_attrs(merged_container)} asok-state=\"{{ search: '', filters: {{}} }}\">"

        # Header (Search & Filters)
        html_out += self._render_server_header()

        # Table Wrapper & Table tag
        html_out += self._render_server_table(items)

        # Footer (Pagination)
        html_out += self._render_server_footer(data, items)

        html_out += "</div>"
        return html_out

    def __str__(self) -> str:
        return SafeString(self.render())
