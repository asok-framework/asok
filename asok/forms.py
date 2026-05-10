from __future__ import annotations

import enum
import json
from html import escape
from typing import TYPE_CHECKING, Any, Callable, Optional, Union

from .orm import Model
from .templates import SafeString
from .validation import Validator

if TYPE_CHECKING:
    from .request import Request


def _merge_attrs(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Merge base attributes with overrides, stripping trailing underscores from keys."""
    merged = dict(base)
    for k, v in overrides.items():
        key = k.rstrip("_") if k.endswith("_") and k != "_" else k
        merged[key] = v
    return merged


def _render_attrs(attrs: dict[str, Any]) -> str:
    """Render a dictionary of attributes into a space-separated HTML string."""
    parts = ""
    for k, v in attrs.items():
        if v is True:
            parts += f" {escape(k)}"
        elif v is not False and v is not None:
            parts += f' {escape(k)}="{escape(str(v))}"'
    return parts


class Renderable:
    """A lazy-rendering wrapper for HTML elements.

    Supports both direct string conversion: `{{ field.label }}`
    and parameter-based invocation: `{{ field.label(class='btn') }}`.
    """

    def __init__(self, render_fn: Callable[..., str]):
        self._render_fn = render_fn

    def __str__(self) -> str:
        return SafeString(self._render_fn())

    def __call__(self, **attrs: Any) -> SafeString:
        return SafeString(self._render_fn(**attrs))


class FormField:
    """Represents a single field within a Form, including its value, validation errors, and rendering logic."""

    def __init__(
        self,
        name: str,
        label: str,
        field_type: str,
        rules: str = "",
        messages: Optional[Union[str, dict[str, str]]] = None,
        choices: Optional[list[tuple[Any, str]]] = None,
        **attrs: Any,
    ):
        self.name: str = name
        self._label: str = label
        self.type: str = field_type
        self.rules: str = rules
        if isinstance(messages, str):
            rule_names = [r.split(":")[0] for r in rules.split("|")] if rules else []
            self.messages: dict[str, str] = {r: messages for r in rule_names}
        else:
            self.messages: dict[str, str] = messages or {}
        self.choices: Optional[list[tuple[Any, str]]] = choices

        # Dropdown specific data
        self.items = attrs.pop("items", None)
        self.item_meta = {
            "title": attrs.pop("title", "name"),
            "subtitle": attrs.pop("subtitle", None),
            "image": attrs.pop("image", None),
            "searchable": attrs.pop("searchable", True)
        }

        attrs = dict(attrs)  # never mutate the schema dict (shared in templates)
        self.readonly: bool = attrs.pop("readonly", False)
        self.attrs: dict[str, Any] = attrs
        self.value: Any = ""
        self._error: str = ""

    @property
    def error(self) -> Renderable:
        """Render the field's validation error message."""
        return Renderable(self.render_error)

    @property
    def label(self) -> Renderable:
        """Render the field's HTML label."""
        return Renderable(self.render_label)

    @property
    def input(self) -> Renderable:
        """Render the field's HTML input element."""
        return Renderable(self.render_input)

    def render_label(self, **overrides: Any) -> str:
        """Internal method for rendering the <label> HTML."""
        if self.type == "hidden":
            return ""
        attrs = _merge_attrs({"for": self.name}, overrides)
        return f"<label{_render_attrs(attrs)}>{escape(self._label)}</label>"

    def _format_value_for_input(self) -> str:
        """Format value appropriately for HTML5 input types."""
        if not self.value:
            return ""

        val_str = str(self.value)

        # Format for HTML5 date/time inputs
        if self.type == "date":
            # Convert "YYYY-MM-DD HH:MM:SS" or "YYYY-MM-DDTHH:MM:SS" to "YYYY-MM-DD"
            if "T" in val_str:
                return val_str.split("T")[0]
            elif " " in val_str:
                return val_str.split(" ")[0]
            return val_str[:10] if len(val_str) >= 10 else val_str

        elif self.type == "datetime-local":
            # Convert to "YYYY-MM-DDTHH:MM" (no seconds, no timezone)
            val_str = val_str.replace(" ", "T")  # Convert space to T
            # Remove seconds, microseconds, and timezone
            if "T" in val_str:
                parts = val_str.split("T")
                date_part = parts[0]
                time_part = parts[1] if len(parts) > 1 else "00:00"
                # Remove timezone info if present (+00:00, Z, etc.)
                time_part = time_part.split("+")[0].split("-")[0].split("Z")[0]
                # Keep only HH:MM
                time_parts = time_part.split(":")
                time_part = (
                    ":".join(time_parts[:2]) if len(time_parts) >= 2 else time_part
                )
                return f"{date_part}T{time_part}"
            return val_str

        elif self.type == "time":
            # Convert to "HH:MM"
            if "T" in val_str:
                val_str = val_str.split("T")[1]
            elif " " in val_str:
                val_str = val_str.split(" ")[1]
            # Remove seconds and keep only HH:MM
            time_parts = val_str.split(":")
            return ":".join(time_parts[:2]) if len(time_parts) >= 2 else val_str

        return val_str

    def render_input(self, **overrides: Any) -> str:
        """Internal method for rendering the HTML input element (input, select, or textarea)."""
        esc = escape
        val = esc(self._format_value_for_input())

        if self.readonly:
            display = val if val else "—"
            return f'<div class="readonly-value">{display}</div>'

        base = dict(self.attrs)
        if self._error:
            existing = base.get("class", "")
            base["class"] = f"{existing} input-error".strip()

        merged = _merge_attrs(base, overrides)

        if self.type == "textarea":
            attrs = {"id": self.name, "name": self.name, **merged}
            return f"<textarea{_render_attrs(attrs)}>{val}</textarea>"

        if self.type == "select":
            attrs = {"id": self.name, "name": self.name, **merged}
            options_html = ""
            for opt_val, opt_label in self.choices or []:
                sel = " selected" if str(opt_val) == str(self.value) else ""
                options_html += f'<option value="{esc(str(opt_val))}"{sel}>{esc(str(opt_label))}</option>'
            return f"<select{_render_attrs(attrs)}>{options_html}</select>"

        if self.type == "checkbox":
            attrs = {"type": "checkbox", "id": self.name, "name": self.name, **merged}
            if self.value and self.value != "0":
                attrs["checked"] = True
            return f"<input{_render_attrs(attrs)}>"

        if self.type == "radio":
            html = ""
            for opt_val, opt_label in self.choices or []:
                radio_attrs = {
                    "type": "radio",
                    "name": self.name,
                    "value": str(opt_val),
                    **merged,
                }
                radio_attrs.pop("id", None)
                if str(opt_val) == str(self.value):
                    radio_attrs["checked"] = True
                html += f"<label><input{_render_attrs(radio_attrs)}> {esc(str(opt_label))}</label>"
            return html

        if self.type == "dropdown":
            render_items = []
            current_label = "Select..."
            items_to_process = self.items or []
            if not items_to_process and self.choices:
                items_to_process = [{"id": v, "name": lbl} for v, lbl in self.choices]

            def get_val(obj, key):
                if not key:
                    return None
                if isinstance(obj, dict):
                    return obj.get(key)
                return getattr(obj, key, None)

            for item in items_to_process:
                if isinstance(item, (str, int, float)):
                    tid = item
                    title = item
                    subtitle = None
                    image = None
                else:
                    tid = get_val(item, "id")
                    if tid is None:
                        tid = get_val(item, "pk")

                    title = get_val(item, self.item_meta["title"])
                    if title is None:
                        title = str(item)

                    subtitle = get_val(item, self.item_meta["subtitle"])
                    image = get_val(item, self.item_meta["image"])

                if str(tid) == str(self.value):
                    current_label = title
                render_items.append({"id": tid, "title": title, "subtitle": subtitle, "image": image})

            container_attrs = _merge_attrs({"class": "asok-dropdown"}, overrides)
            trigger_class = overrides.get("trigger_class", self.attrs.get("trigger_class", ""))
            menu_class = overrides.get("menu_class", self.attrs.get("menu_class", ""))
            item_class = overrides.get("item_class", self.attrs.get("item_class", ""))
            searchable = self.item_meta["searchable"]

            html = f'<div{_render_attrs(container_attrs)} asok-state="{{ open: false, search: \'\', label: \'{esc(str(current_label))}\' }}">'
            html += f'  <button type="button" class="asok-dropdown-trigger {esc(trigger_class)}" asok-on:click="open = !open">'
            html += f'    <span asok-text="label">{esc(str(current_label))}</span>'
            html += '    <svg class="asok-dropdown-arrow" width="20" height="20" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clip-rule="evenodd" /></svg>'
            html += '  </button>'
            html += f'  <div class="asok-dropdown-menu {esc(menu_class)}" asok-show="open" asok-on:click.outside="open = false" asok-cloak>'
            if searchable:
                html += '    <div class="asok-dropdown-search"><input type="text" asok-model="search" placeholder="Search..." asok-on:keydown.escape="open = false"></div>'
            html += '    <div class="asok-dropdown-items">'
            for ri in render_items:
                s_cond = f"!search || '{esc(str(ri['title'])).lower()}'.includes(search.toLowerCase())"
                click = f"label = '{esc(str(ri['title']))}'; open = false; $refs.input_{self.name}.value = '{esc(str(ri['id']))}'; $refs.input_{self.name}.dispatchEvent(new Event('change'))"
                html += f'      <div class="asok-dropdown-item {esc(item_class)}" asok-show="{s_cond}" asok-on:click="{click}">'
                if ri["image"]:
                    html += f'        <img src="{esc(str(ri["image"]))}" class="asok-dropdown-item-img">'
                html += '        <div class="asok-dropdown-item-content">'
                html += f'          <div class="asok-dropdown-item-title">{esc(str(ri["title"]))}</div>'
                if ri["subtitle"]:
                    html += f'          <div class="asok-dropdown-item-subtitle">{esc(str(ri["subtitle"]))}</div>'
                html += '        </div>'
                html += '      </div>'
            html += '    </div>'
            html += '  </div>'
            html += f'  <input type="hidden" name="{self.name}" id="{self.name}" value="{esc(str(self.value))}" asok-ref="input_{self.name}">'
            html += '</div>'
            return html

        if self.type == "image":
            preview = self.attrs.get("preview", True)
            max_width = self.attrs.get("max_width", 200)
            max_height = self.attrs.get("max_height", 200)

            if preview:
                # Wrap in container with asok-state for CSP-compliant preview
                initial_preview = self.value if self.value else ""
                state = json.dumps({"preview": initial_preview}).replace('"', '&quot;')

                container_attrs = _merge_attrs({"class": "asok-image-upload"}, {})
                html = f'<div{_render_attrs(container_attrs)} asok-state="{state}">'

                # File input with asok-on:change instead of inline onchange
                file_attrs = {"type": "file", "id": self.name, "name": self.name, "accept": "image/*", **merged}
                file_attrs.pop("preview", None)
                file_attrs.pop("max_width", None)
                file_attrs.pop("max_height", None)

                # Use asok-on:change for CSP compliance (no inline event handlers)
                change_handler = (
                    "const f=$event.target.files[0];"
                    "if(f){"
                    "const r=new FileReader();"
                    "r.onload=(e)=>{preview=e.target.result};"
                    "r.readAsDataURL(f)"
                    "}"
                )
                html += f'<input{_render_attrs(file_attrs)} asok-on:change="{change_handler}">'

                # Preview image bound to state (asok-cloak hides until Asok loads)
                preview_style = f"max-width:{max_width}px;max-height:{max_height}px;margin-top:10px;"
                html += f'<br><img asok-show="preview" asok-bind:src="preview" style="{preview_style}" alt="Preview" asok-cloak>'
                html += '</div>'
            else:
                # No preview - just a simple file input
                file_attrs = {"type": "file", "id": self.name, "name": self.name, "accept": "image/*", **merged}
                file_attrs.pop("preview", None)
                file_attrs.pop("max_width", None)
                file_attrs.pop("max_height", None)
                html = f"<input{_render_attrs(file_attrs)}>"

            return html

        if self.type == "tags":
            searchable = self.attrs.get("searchable", True)
            # allow_custom = self.attrs.get("allow_custom", False)  # Reserved for future use

            # Parse current value (can be JSON array, comma-separated, or empty)
            current_values = []
            if self.value:
                try:
                    current_values = json.loads(self.value) if isinstance(self.value, str) else self.value
                except (json.JSONDecodeError, TypeError):
                    # Fallback to comma-separated
                    current_values = [v.strip() for v in str(self.value).split(",") if v.strip()]

            # Build available options from choices
            available_options = []
            current_labels = {}
            if self.choices:
                for val, label in self.choices:
                    available_options.append({"value": str(val), "label": str(label)})
                    if str(val) in current_values:
                        current_labels[str(val)] = str(label)

            # Create state with selected tags
            selected_tags = [{"value": v, "label": current_labels.get(v, v)} for v in current_values]
            state = json.dumps({"selected": selected_tags, "open": False, "search": ""}).replace('"', '&quot;')

            container_attrs = _merge_attrs({"class": "asok-tags"}, {})
            html = f'<div{_render_attrs(container_attrs)} asok-state="{state}">'

            # Display selected tags
            html += '  <div class="asok-tags-selected">'
            html += '    <template asok-for="tag in selected">'
            html += '      <span class="asok-tag">'
            html += '        <span asok-text="tag.label"></span>'
            html += '        <button type="button" class="asok-tag-remove" asok-on:click="selected = selected.filter(t => t.value !== tag.value); $refs.input_' + self.name + '.value = JSON.stringify(selected.map(t => t.value)); $refs.input_' + self.name + '.dispatchEvent(new Event(\'change\'))">×</button>'
            html += '      </span>'
            html += '    </template>'

            # Add button
            html += '    <button type="button" class="asok-tags-add" asok-on:click="open = !open">+ Add</button>'
            html += '  </div>'

            # Dropdown menu with options
            html += '  <div class="asok-tags-menu" asok-show="open" asok-on:click.outside="open = false" asok-cloak>'
            if searchable:
                html += '    <input type="text" class="asok-tags-search" asok-model="search" placeholder="Search..." asok-on:keydown.escape="open = false">'
            html += '    <div class="asok-tags-options">'

            for opt in available_options:
                search_cond = f"!search || '{esc(opt['label']).lower()}'.includes(search.toLowerCase())" if searchable else "true"
                already_selected = f"selected.some(t => t.value === '{esc(opt['value'])}')"
                click_action = f"if(!{already_selected}){{selected.push({{value:'{esc(opt['value'])}',label:'{esc(opt['label'])}'}});$refs.input_{self.name}.value=JSON.stringify(selected.map(t=>t.value));$refs.input_{self.name}.dispatchEvent(new Event('change'))}};open=false"
                html += f'      <div class="asok-tags-option" asok-show="{search_cond} && !{already_selected}" asok-on:click="{click_action}">{esc(opt["label"])}</div>'

            html += '    </div>'
            html += '  </div>'

            # Hidden input to store selected values as JSON array
            value_json = json.dumps(current_values)
            html += f'  <input type="hidden" name="{self.name}" id="{self.name}" value=\'{esc(value_json)}\' asok-ref="input_{self.name}">'
            html += '</div>'

            return html

        if self.type == "daterange":
            start_label = self.attrs.get("start_label", "From")
            end_label = self.attrs.get("end_label", "To")

            # Parse current value (expected as JSON with {start: "...", end: "..."})
            start_value = ""
            end_value = ""
            if self.value:
                try:
                    if isinstance(self.value, str):
                        parsed = json.loads(self.value)
                        start_value = parsed.get("start", "")
                        end_value = parsed.get("end", "")
                    elif isinstance(self.value, dict):
                        start_value = self.value.get("start", "")
                        end_value = self.value.get("end", "")
                except (json.JSONDecodeError, TypeError, AttributeError):
                    pass

            # Use asok-state for reactive date range (CSP-compliant)
            state = json.dumps({"start": start_value, "end": end_value}).replace('"', '&quot;')
            container_attrs = _merge_attrs({"class": "asok-daterange"}, {})
            html = f'<div{_render_attrs(container_attrs)} asok-state="{state}">'

            # Start date input with asok-on:change
            html += '  <div class="asok-daterange-field">'
            html += f'    <label class="asok-daterange-label">{esc(start_label)}</label>'
            start_attrs = {"type": "date", "id": f"{self.name}_start"}
            if "future" in self.rules:
                import datetime
                start_attrs["min"] = datetime.date.today().isoformat()

            # Use asok-model for two-way binding and update hidden input
            update_hidden = f"$refs.hidden_{self.name}.value=JSON.stringify({{start:start,end:end}});$refs.hidden_{self.name}.dispatchEvent(new Event('change'))"
            html += f'    <input{_render_attrs(start_attrs)} asok-model="start" asok-on:change="{update_hidden}">'
            html += '  </div>'

            # End date input restricted by start date
            html += '  <div class="asok-daterange-field">'
            html += f'    <label class="asok-daterange-label">{esc(end_label)}</label>'
            end_attrs = {"type": "date", "id": f"{self.name}_end", "asok-bind:min": "start"}
            html += f'    <input{_render_attrs(end_attrs)} asok-model="end" asok-on:change="{update_hidden}">'
            html += '  </div>'

            # Hidden input to store the range as JSON
            value_json = json.dumps({"start": start_value, "end": end_value})
            html += f'  <input type="hidden" name="{self.name}" id="{self.name}" value=\'{esc(value_json)}\' asok-ref="hidden_{self.name}">'
            html += '</div>'

            return html

        if self.type == "toggle":
            # Toggle switch (styled checkbox)
            container_attrs = _merge_attrs({"class": "asok-toggle"}, {})
            html = f'<div{_render_attrs(container_attrs)}>'

            checkbox_attrs = {"type": "checkbox", "id": self.name, "name": self.name, **merged}
            if self.value and self.value != "0":
                checkbox_attrs["checked"] = True

            html += f'<input{_render_attrs(checkbox_attrs)}>'
            html += f'<label for="{self.name}" class="asok-toggle-slider"></label>'
            html += '</div>'
            return html

        if self.type == "otp":
            # OTP input with separate boxes
            length = self.attrs.get("length", 6)
            container_attrs = _merge_attrs({"class": "asok-otp"}, {})

            # Split current value into individual digits
            current_value = str(self.value) if self.value else ""
            # Pad with empty strings if needed
            digits = list(current_value[:length]) + [""] * (
                length - len(current_value[:length])
            )

            # Use double quotes for JSON and escape correctly
            state_json = json.dumps({"digits": digits})
            html = f'<div{_render_attrs(container_attrs)} asok-state="{esc(state_json)}">'

            for i in range(length):
                input_attrs = {
                    "type": "text",
                    "maxlength": "1",
                    "class": "asok-otp-input",
                    "asok-model": f"digits[{i}]",
                }
                # Auto-focus next input on keyup
                next_focus = "if($event.target.value && $event.key !== 'Backspace'){const next=$event.target.nextElementSibling;if(next && next.tagName==='INPUT')next.focus()}"
                html += f'<input{_render_attrs(input_attrs)} asok-on:keyup="{next_focus}">'

            # Hidden input to store the complete OTP. Bound to the reactive 'digits' array.
            html += f'<input type="hidden" name="{self.name}" id="{self.name}" asok-bind:value="digits.join(\'\')" asok-ref="hidden_{self.name}">'
            html += "</div>"
            return html

        if self.type == "month":
            # Month/Year picker (HTML5 type="month")
            attrs = {"type": "month", "id": self.name, "name": self.name, **merged}
            attrs["value"] = val
            return f"<input{_render_attrs(attrs)}>"

        if self.type == "rating":
            # Star rating
            max_stars = self.attrs.get("max_stars", 5)
            current_rating = int(self.value) if self.value else 0

            container_attrs = _merge_attrs({"class": "asok-rating"}, {})
            html = f'<div{_render_attrs(container_attrs)} asok-state=\'{{"rating": {current_rating}, "hover": 0}}\'>'

            for i in range(1, max_stars + 1):
                star_attrs = {
                    "class": "asok-rating-star",
                    "asok-on:click": f"rating={i};$refs.hidden_{self.name}.value={i};$refs.hidden_{self.name}.dispatchEvent(new Event('change'))",
                    "asok-on:mouseenter": f"hover={i}",
                    "asok-on:mouseleave": "hover=0",
                }
                # Show filled star if rated or hovered
                filled_condition = f"(hover >= {i}) || (hover === 0 && rating >= {i})"
                html += f'<span{_render_attrs(star_attrs)}>'
                html += f'<svg asok-show="{filled_condition}" width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>'
                html += f'<svg asok-show="!({filled_condition})" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>'
                html += '</span>'

            html += f'<input type="hidden" name="{self.name}" id="{self.name}" value="{current_rating}" asok-ref="hidden_{self.name}">'
            html += '</div>'
            return html

        if self.type == "timerange":
            # Time range picker (similar to daterange but with time inputs)
            start_label = self.attrs.get("start_label", "From")
            end_label = self.attrs.get("end_label", "To")

            # Parse current value
            start_value = ""
            end_value = ""
            if self.value:
                try:
                    if isinstance(self.value, str):
                        parsed = json.loads(self.value)
                        start_value = parsed.get("start", "")
                        end_value = parsed.get("end", "")
                    elif isinstance(self.value, dict):
                        start_value = self.value.get("start", "")
                        end_value = self.value.get("end", "")
                except (json.JSONDecodeError, TypeError, AttributeError):
                    pass

            state = json.dumps({"start": start_value, "end": end_value}).replace('"', '&quot;')
            container_attrs = _merge_attrs({"class": "asok-timerange"}, {})
            html = f'<div{_render_attrs(container_attrs)} asok-state="{state}">'

            # Start time input
            html += '  <div class="asok-timerange-field">'
            html += f'    <label class="asok-timerange-label">{esc(start_label)}</label>'
            start_attrs = {"type": "time", "id": f"{self.name}_start"}
            update_hidden = f"$refs.hidden_{self.name}.value=JSON.stringify({{start:start,end:end}});$refs.hidden_{self.name}.dispatchEvent(new Event('change'))"
            html += f'    <input{_render_attrs(start_attrs)} asok-model="start" asok-on:change="{update_hidden}">'
            html += '  </div>'

            # End time input
            html += '  <div class="asok-timerange-field">'
            html += f'    <label class="asok-timerange-label">{esc(end_label)}</label>'
            end_attrs = {"type": "time", "id": f"{self.name}_end"}
            html += f'    <input{_render_attrs(end_attrs)} asok-model="end" asok-on:change="{update_hidden}">'
            html += '  </div>'

            # Hidden input
            value_json = json.dumps({"start": start_value, "end": end_value})
            html += f'  <input type="hidden" name="{self.name}" id="{self.name}" value=\'{esc(value_json)}\' asok-ref="hidden_{self.name}">'
            html += '</div>'
            return html

        if self.type == "files":
            # Multi-file upload with previews
            max_files = self.attrs.get("max_files", 10)
            preview_enabled = self.attrs.get("preview", True)

            state = json.dumps({"files": []}).replace('"', '&quot;')
            container_attrs = _merge_attrs({"class": "asok-files"}, {})
            html = f'<div{_render_attrs(container_attrs)} asok-state="{state}">'

            file_attrs = {"type": "file", "id": self.name, "name": self.name, "multiple": True, "accept": "*/*", **merged}
            file_attrs.pop("max_files", None)
            file_attrs.pop("preview", None)

            # Handle file selection
            change_handler = (
                "const fileList=Array.from($event.target.files);"
                f"if(fileList.length>{max_files}){{alert('Maximum {max_files} files');return}};"
                "files=fileList.map((f,i)=>({name:f.name,size:f.size,url:URL.createObjectURL(f)}))"
            )
            html += f'<input{_render_attrs(file_attrs)} asok-on:change="{change_handler}">'

            if preview_enabled:
                html += '<div class="asok-files-preview">'
                html += '  <template asok-for="(file, index) in files">'
                html += '    <div class="asok-file-item">'
                html += '      <img asok-show="file.url" asok-bind:src="file.url" style="max-width:100px;max-height:100px;">'
                html += '      <span asok-text="file.name"></span>'
                html += '      <button type="button" asok-on:click="files=files.filter((_,i)=>i!==index)">×</button>'
                html += '    </div>'
                html += '  </template>'
                html += '</div>'

            html += '</div>'
            return html

        if self.type == "autocomplete":
            # Autocomplete with suggestions
            min_chars = self.attrs.get("min_chars", 1)
            items = self.items or self.choices or []

            state = json.dumps({"query": self.value or "", "show": False, "filtered": items}).replace('"', '&quot;')
            container_attrs = _merge_attrs({"class": "asok-autocomplete"}, {})
            html = f'<div{_render_attrs(container_attrs)} asok-state="{state}">'

            # Input with filtering
            input_attrs = {"type": "text", "id": self.name, "name": self.name, "autocomplete": "off", **merged}
            items_json = json.dumps(items).replace('"', '&quot;').replace("'", "\\'")
            filter_logic = (
                f"const all={items_json};"
                f"if(query.length>={min_chars}){{filtered=all.filter(item=>String(item).toLowerCase().includes(query.toLowerCase()));show=true}}else{{show=false}}"
            )
            html += f'<input{_render_attrs(input_attrs)} asok-model="query" asok-on:input="{filter_logic}" asok-on:blur="setTimeout(()=>show=false,200)">'

            # Suggestions dropdown
            html += '<div class="asok-autocomplete-menu" asok-show="show && filtered.length > 0" asok-cloak>'
            html += '  <template asok-for="item in filtered">'
            select_action = "query=String(item);show=false;$refs.input_" + self.name + ".value=query"
            html += f'    <div class="asok-autocomplete-item" asok-on:click="{select_action}" asok-text="item"></div>'
            html += '  </template>'
            html += '</div>'

            html += '</div>'
            return html

        if self.type == "cascading":
            # Cascading select (dependent dropdowns)
            choices_dict = self.choices or {}

            parents = list(choices_dict.keys())
            state_dict = {"parent": "", "child": "", "children": [], "map": choices_dict}
            state = json.dumps(state_dict).replace('"', '&quot;')
            container_attrs = _merge_attrs({"class": "asok-cascading"}, {})
            html = f'<div{_render_attrs(container_attrs)} asok-state="{state}">'

            # Parent select
            html += '<select asok-model="parent" asok-on:change="children=map[parent]||[];child=\'\'">'
            html += '<option value="">Select...</option>'
            for parent in parents:
                html += f'<option value="{esc(parent)}">{esc(parent)}</option>'
            html += '</select>'

            # Child select
            html += '<select asok-model="child" asok-show="children.length > 0" asok-cloak>'
            html += '<option value="">Select...</option>'
            html += '<template asok-for="option in children">'
            html += '<option asok-bind:value="option" asok-text="option"></option>'
            html += '</template>'
            html += '</select>'

            # Hidden input to store the selection
            html += f'<input type="hidden" name="{self.name}" id="{self.name}" asok-bind:value="parent+\' > \'+child">'
            html += '</div>'
            return html

        if self.type == "phone":
            # International phone input
            default_country = self.attrs.get("default_country", "US")

            from .utils.geo import get_dial_codes, iso_to_flag
            countries = get_dial_codes()

            default_code = next((c[1] for c in countries if c[0] == default_country), "+1")
            state = json.dumps({"code": default_code, "number": ""}).replace('"', '&quot;')
            container_attrs = _merge_attrs({"class": "asok-phone"}, {})
            html = f'<div{_render_attrs(container_attrs)} asok-state="{state}">'

            # Country code select
            html += '<select asok-model="code" class="asok-phone-code">'
            for code, dial, name in countries:
                selected = " selected" if code == default_country else ""
                flag = iso_to_flag(code)
                html += f'<option value="{dial}"{selected}>{flag} {dial}</option>'
            html += '</select>'

            # Phone number input
            update_hidden = f"$refs.hidden_{self.name}.value=code+number;$refs.hidden_{self.name}.dispatchEvent(new Event('change'))"
            html += f'<input type="tel" asok-model="number" asok-on:input="{update_hidden}" placeholder="Phone number">'

            # Hidden input to store complete phone
            html += f'<input type="hidden" name="{self.name}" id="{self.name}" asok-bind:value="code+number" asok-ref="hidden_{self.name}">'
            html += '</div>'
            return html

        if self.type == "wysiwyg":
            # Rich text editor (simplified Quill-like)
            height = self.attrs.get("height", 300)
            current_content = self.value or ""

            state = json.dumps({"content": current_content}).replace('"', '&quot;')
            container_attrs = _merge_attrs({"class": "asok-wysiwyg"}, {})
            html = f'<div{_render_attrs(container_attrs)} asok-state="{state}">'

            # Toolbar
            html += '<div class="asok-wysiwyg-toolbar">'
            html += '<button type="button" asok-on:click="document.execCommand(\'bold\')"><b>B</b></button>'
            html += '<button type="button" asok-on:click="document.execCommand(\'italic\')"><i>I</i></button>'
            html += '<button type="button" asok-on:click="document.execCommand(\'underline\')"><u>U</u></button>'
            html += '<button type="button" asok-on:click="document.execCommand(\'insertUnorderedList\')">• List</button>'
            html += '</div>'

            # Editor (contenteditable div)
            update_hidden = f"$refs.hidden_{self.name}.value=$event.target.innerHTML;content=$event.target.innerHTML"
            html += f'<div class="asok-wysiwyg-editor" contenteditable="true" style="min-height:{height}px;border:1px solid #ddd;padding:10px;" asok-on:input="{update_hidden}">{esc(current_content)}</div>'

            # Hidden input to store HTML
            html += f'<input type="hidden" name="{self.name}" id="{self.name}" value="{esc(current_content)}" asok-ref="hidden_{self.name}">'
            html += '</div>'
            return html

        if self.type == "dropzone":
            # Drag and drop file upload
            max_files = self.attrs.get("max_files", 10)

            state = json.dumps({"files": [], "dragging": False}).replace('"', '&quot;')
            container_attrs = _merge_attrs({"class": "asok-dropzone"}, {})

            html = f'<div{_render_attrs(container_attrs)} asok-state="{state}">'

            # Drop zone div - copy exact syntax from working 'files' component
            drop_handler = (
                "dragging=false;"
                "const fileList=Array.from($event.dataTransfer.files);"
                f"if(fileList.length>{max_files}){{alert('Max {max_files} files');return}};"
                f"const dt=new DataTransfer();for(let i=0;i<fileList.length;i++)dt.items.add(fileList[i]);$refs.input_{self.name}.files=dt.files;"
                "files=fileList.map((f,i)=>({name:f.name,size:f.size,_file:f}))"
            )
            drag_handlers = (
                "asok-on:dragover.prevent=\"dragging=true\" "
                "asok-on:dragleave=\"dragging=false\" "
                f"asok-on:drop.prevent=\"{drop_handler}\""
            )
            html += f'<div class="asok-dropzone-area" {drag_handlers} asok-bind:class="dragging?\'dragging\':\'\'" style="border:2px dashed #ccc;padding:40px;text-align:center;cursor:pointer;">'
            html += f'<p>Drag & drop files here or <label for="{self.name}" style="color:blue;cursor:pointer;">browse</label></p>'
            html += '</div>'

            # Hidden file input - copy exact syntax from working 'files' component
            file_attrs = {"type": "file", "id": self.name, "name": self.name, "multiple": True, "style": "display:none;", "asok-ref": f"input_{self.name}"}
            change_handler = (
                "const fileList=Array.from($event.target.files);"
                f"if(fileList.length>{max_files}){{alert('Maximum {max_files} files');return}};"
                "files=fileList.map((f,i)=>({name:f.name,size:f.size,_file:f}))"
            )
            html += f'<input{_render_attrs(file_attrs)} asok-on:change="{change_handler}">'

            # File list
            html += '<ul class="asok-dropzone-files">'
            html += '  <template asok-for="(file, index) in files">'
            html += '    <li><span asok-text="file.name"></span> <button type="button" asok-on:click="files=files.filter((_,i)=>i!==index); const dt=new DataTransfer(); files.forEach(f=>dt.items.add(f._file)); $refs.input_' + self.name + '.files=dt.files;">×</button></li>'
            html += '  </template>'
            html += '</ul>'

            html += '</div>'
            return html

        if self.type == "signature":
            # Signature pad with canvas drawing
            width = self.attrs.get("width", 400)
            height = self.attrs.get("height", 200)

            state = json.dumps({"drawing": False}).replace('"', '&quot;')
            container_attrs = _merge_attrs({"class": "asok-signature"}, {})
            html = f'<div{_render_attrs(container_attrs)} asok-state="{state}">'

            # Canvas element with drawing handlers
            canvas_id = f"canvas_{self.name}"
            canvas_attrs = {
                "id": canvas_id,
                "width": width,
                "height": height,
                "style": "border:1px solid #ccc;cursor:crosshair;touch-action:none;",
                "asok-ref": f"canvas_{self.name}"
            }

            # Handlers pour le dessin
            mousedown = f"drawing=true;const c=$refs.canvas_{self.name};const ctx=c.getContext('2d');const r=c.getBoundingClientRect();ctx.beginPath();ctx.moveTo($event.clientX-r.left,$event.clientY-r.top);ctx.lineWidth=2;ctx.lineCap='round';ctx.strokeStyle='#000'"
            mousemove = f"if(drawing){{const c=$refs.canvas_{self.name};const ctx=c.getContext('2d');const r=c.getBoundingClientRect();ctx.lineTo($event.clientX-r.left,$event.clientY-r.top);ctx.stroke()}}"
            mouseup = f"drawing=false;$refs.hidden_{self.name}.value=$refs.canvas_{self.name}.toDataURL()"
            mouseleave = "drawing=false"

            canvas_attrs["asok-on:mousedown"] = mousedown
            canvas_attrs["asok-on:mousemove"] = mousemove
            canvas_attrs["asok-on:mouseup"] = mouseup
            canvas_attrs["asok-on:mouseleave"] = mouseleave

            html += f'<canvas{_render_attrs(canvas_attrs)}></canvas>'

            # Clear button
            clear_handler = f"const c=$refs.canvas_{self.name};c.getContext('2d').clearRect(0,0,c.width,c.height);$refs.hidden_{self.name}.value=''"
            html += f'<br><button type="button" asok-on:click="{clear_handler}">Clear</button>'

            # Hidden input to store base64 signature
            html += f'<input type="hidden" name="{self.name}" id="{self.name}" asok-ref="hidden_{self.name}" value="{val}">'

            html += '</div>'
            return html

        if self.type == "transfer":
            # Transfer list (dual listbox)
            items = self.items or self.choices or []

            state = json.dumps({"available": items, "selected": [], "h_avail": [], "h_sel": []}).replace('"', '&quot;')
            container_attrs = _merge_attrs({"class": "asok-transfer"}, {})
            html = f'<div{_render_attrs(container_attrs)} asok-state="{state}">'

            html += '<div class="asok-transfer-lists" style="display:flex;gap:20px;">'

            # Available list
            html += '  <div style="flex:1;">'
            html += '    <h4>Available</h4>'
            html += '    <select multiple style="width:100%;height:200px;" asok-on:change="h_avail=Array.from($event.target.selectedOptions).map(o=>o.value)">'
            html += '      <template asok-for="item in available">'
            html += '        <option asok-bind:value="item.id || item" asok-text="item.name || item" asok-on:dblclick="selected.push(item);available=available.filter(i=>i!==item)" asok-bind:style="h_avail.includes(String(item.id || item)) ? \'background-color: #e7f3ff;\' : \'\'"></option>'
            html += '      </template>'
            html += '    </select>'
            html += '  </div>'

            html += '  <div style="display:flex;flex-direction:column;justify-content:center;gap:10px;">'
            html += '    <button type="button" asok-on:click="const move=available.filter(i=>h_avail.includes(String(i.id||i)));selected=[...selected,...move];available=available.filter(i=>!move.includes(i));h_avail=[]">→</button>'
            html += '    <button type="button" asok-on:click="const move=selected.filter(i=>h_sel.includes(String(i.id||i)));available=[...available,...move];selected=selected.filter(i=>!move.includes(i));h_sel=[]">←</button>'
            html += '  </div>'

            # Selected list
            html += '  <div style="flex:1;">'
            html += '    <h4>Selected</h4>'
            html += '    <select multiple style="width:100%;height:200px;" asok-on:change="h_sel=Array.from($event.target.selectedOptions).map(o=>o.value)">'
            html += '      <template asok-for="item in selected">'
            html += '        <option asok-bind:value="item.id || item" asok-text="item.name || item" asok-on:dblclick="available.push(item);selected=selected.filter(i=>i!==item)" asok-bind:style="h_sel.includes(String(item.id || item)) ? \'background-color: #e7f3ff;\' : \'\'"></option>'
            html += '      </template>'
            html += '    </select>'
            html += '  </div>'

            html += '</div>'

            # Hidden input to store selected IDs
            html += f'<input type="hidden" name="{self.name}" id="{self.name}" asok-bind:value="JSON.stringify(selected.map(i=>i.id||i))">'
            html += '</div>'
            return html

        if self.type == "treeselect":
            # Tree select (hierarchical selection - simplified)
            items = self.items or self.choices or []

            state = json.dumps({"tree": items, "selected": "", "expanded": []}).replace('"', '&quot;')
            container_attrs = _merge_attrs({"class": "asok-treeselect"}, {})
            html = f'<div{_render_attrs(container_attrs)} asok-state="{state}">'

            # Simple tree rendering (would need recursive template in real implementation)
            html += '<div class="asok-tree" style="border:1px solid #ddd;padding:10px;max-height:300px;overflow-y:auto;">'
            html += '  <template asok-for="item in tree">'
            html += '    <div class="asok-tree-item" style="margin:2px 0;">'
            html += '      <div style="display:flex;align-items:center;padding:5px;cursor:pointer;border-radius:4px;" asok-on:click="selected=item.id;$refs.hidden_' + self.name + '.value=item.id" asok-bind:style="selected==item.id ? \'background:#e7f3ff;color:#0056b3\' : \'\'">'
            html += '        <span style="width:20px;text-align:center;cursor:pointer;user-select:none;" asok-on:click.stop="expanded.includes(item.id) ? (expanded=expanded.filter(i=>i!==item.id)) : expanded.push(item.id)" asok-text="item.children && item.children.length > 0 ? (expanded.includes(item.id) ? \'▾\' : \'▸\') : \'•\'"></span>'
            html += '        <span asok-text="item.name"></span>'
            html += '      </div>'
            html += '      <template asok-if="item.children && item.children.length > 0 && expanded.includes(item.id)">'
            html += '        <div style="margin-left:20px;margin-top:2px;border-left:1px solid #eee;">'
            html += '          <template asok-for="child in item.children">'
            html += '            <div style="display:flex;align-items:center;padding:4px 10px;cursor:pointer;border-radius:3px;margin:1px 0;" asok-on:click.stop="selected=child.id;$refs.hidden_' + self.name + '.value=child.id" asok-bind:style="selected==child.id ? \'background:#e7f3ff;color:#0056b3\' : \'\'">'
            html += '              <span style="color:#ccc;margin-right:8px;">└</span>'
            html += '              <span asok-text="child.name"></span>'
            html += '            </div>'
            html += '          </template>'
            html += '        </div>'
            html += '      </template>'
            html += '    </div>'
            html += '  </template>'
            html += '</div>'

            html += f'<input type="hidden" name="{self.name}" id="{self.name}" asok-model="selected" asok-ref="hidden_{self.name}">'
            html += '</div>'
            return html

            return html

        attrs = {"type": self.type, "id": self.name, "name": self.name, **merged}
        if self.type != "file":
            attrs["value"] = val
        return f"<input{_render_attrs(attrs)}>"

    def render_error(self, **overrides: Any) -> str:
        """Internal method for rendering the error message <div>."""
        if not self._error:
            return ""
        attrs = _merge_attrs({"class": "form-error"}, overrides)
        return f"<div{_render_attrs(attrs)}>{escape(self._error)}</div>"

    def render(self, **overrides: Any) -> str:
        """Render the complete form group (label, input, error)."""
        if self.type == "hidden":
            return self.render_input(**overrides)
        if self.type == "title":
            attrs = _merge_attrs({"class": "form-title"}, overrides)
            return f"<h3{_render_attrs(attrs)}>{escape(self._label)}</h3>"
        if self.type == "checkbox":
            return (
                f'<div class="form-group">'
                f"<label>{self.render_input(**overrides)} {escape(self._label)}</label>"
                f"{self.render_error()}"
                f"</div>"
            )
        return (
            f'<div class="form-group">'
            f"{self.render_label()}"
            f"{self.render_input(**overrides)}"
            f"{self.render_error()}"
            f"</div>"
        )

    def __call__(self, **overrides: Any) -> SafeString:
        return SafeString(self.render(**overrides))

    def __str__(self) -> str:
        return SafeString(self.render())


class Form:
    """A powerful, schema-based form handling system.

    Supports automatic validation, model pre-filling, and HTML rendering.
    """

    def __init__(
        self, fields_dict: dict[str, tuple], request: Optional[Request] = None
    ):
        """Initialize a form with a field schema and optional request binding.

        Example:
            form = Form({"name": Form.text("Name", "required")}, request)
        """
        if not isinstance(fields_dict, dict):
            raise TypeError(
                "fields_dict must be a dict; got {}".format(type(fields_dict).__name__)
            )
        if not fields_dict:
            raise ValueError("Form fields_dict cannot be empty.")

        self._request: Optional[Request] = request
        self._schema: dict[str, tuple] = fields_dict
        self._is_template: bool = request is None
        self._fields: dict[str, FormField] = {}

        is_post = bool(request) and request.method == "POST"
        for name, definition in fields_dict.items():
            field_type, label, rules, messages, choices, attrs = definition
            field = FormField(
                name, label, field_type, rules, messages, choices, **attrs
            )
            if is_post and not field.readonly:
                # Checkboxes need special handling: unchecked = not in form data
                if field_type == "checkbox":
                    field.value = "1" if request.form.get(name) else "0"
                else:
                    field.value = request.form.get(name, "")
            self._fields[name] = field

    def _bind(self, request: Request) -> Form:
        """Internal helper for creating a bound copy of the form."""
        return Form(self._schema, request)

    def bind(self, request: Request) -> Form:
        """Attach a request to this form instance."""
        self._request = request
        self._is_template = False
        is_post = request.method == "POST"
        for name, field in self._fields.items():
            if is_post and not field.readonly:
                # Checkboxes need special handling: unchecked = not in form data
                if field.type == "checkbox":
                    field.value = "1" if request.form.get(name) else "0"
                else:
                    field.value = request.form.get(name, "")
        return self

    def validate(self, request: Optional[Request] = None, csrf: bool = True) -> bool:
        """Run validation rules against the submitted request data.

        Returns True if all fields are valid, False otherwise.
        If the form is not yet bound to a request, it will attempt to bind using the provided request.

        Args:
            request: The request object to validate (if not already bound).
            csrf: If True, automatically performs CSRF verification.
        """
        if request is not None and self._request is None:
            self.bind(request)

        if not self._request:
            raise RuntimeError(
                "Form.validate() requires a bound request. "
                "Ensure you accessed the form via request.shared() or called form.bind(request)."
            )

        if self._request.method != "POST":
            return False

        # 1. CSRF Verification
        if csrf:
            self._request.verify_csrf()

        schema = {}
        for name, field in self._fields.items():
            if not field.rules:
                continue
            if field.messages:
                schema[name] = (field.rules, field.messages)
            else:
                schema[name] = field.rules

        # from .validation import Validator

        v = Validator(
            self._request.form, self._request.files, translate=self._request.__
        )
        result = v.rules(schema)

        # Update field-level errors
        for name, error in v.errors.items():
            if name in self._fields:
                self._fields[name]._error = error

        return result

    def reset(self) -> Form:
        """Clear all field values and validation errors for a fresh state.

        Returns the Form instance for easy method chaining in templates.
        """
        for field in self._fields.values():
            field.value = ""
            field._error = ""
        return self

    def fill(self, source: Any) -> Form:
        """Pre-fill field values from a model instance or a dictionary."""
        is_post = self._request and self._request.method == "POST"
        is_dict = isinstance(source, dict)
        for name, field in self._fields.items():
            if is_post and not field.readonly:
                continue
            if is_dict:
                if name in source:
                    val = source[name]
                    # Extract .value from Enum objects for form fields
                    if val is not None and isinstance(val, enum.Enum):
                        val = val.value
                    field.value = val if val is not None else ""
            else:
                if hasattr(source, name):
                    val = getattr(source, name)
                    # Extract .value from Enum objects for form fields
                    if val is not None and isinstance(val, enum.Enum):
                        val = val.value
                    field.value = val if val is not None else ""
        return self

    @property
    def errors(self):
        return {name: f._error for name, f in self._fields.items() if f._error}

    @property
    def data(self):
        """Return submitted values as a dict, ready for Model.create(**form.data)."""
        return {name: f.value for name, f in self._fields.items()}

    @property
    def csrf(self) -> SafeString:
        """Render the CSRF token hidden input.

        Requires the form to be bound to a request.
        """
        if not self._request:
            return SafeString("")
        return self._request.csrf_input()

    @property
    def hidden_fields(self) -> SafeString:
        """Render all hidden fields in the form (including CSRF)."""
        html = [str(self.csrf)]
        for field in self._fields.values():
            if field.type == "hidden":
                html.append(str(field))
        return SafeString("\n".join(html))

    def __getattribute__(self, name):
        if not name.startswith("_"):
            fields = super().__getattribute__("_fields")
            if name in fields:
                return fields[name]
        return super().__getattribute__(name)

    @classmethod
    def from_model(
        cls: type[Form],
        model: type[Model],
        request: Optional[Request] = None,
        include_fields: Optional[list[str]] = None,
        exclude_fields: Optional[list[str]] = None,
    ) -> Form:
        """Generate a Form instance automatically from a Model class."""
        if not hasattr(model, "_fields"):
            raise TypeError(
                "Form.from_model() expected a Model class; got {}".format(
                    type(model).__name__
                )
            )

        include = set(include_fields) if include_fields else None
        exclude = set(exclude_fields or [])

        schema = {}
        for name, field in model._fields.items():
            if include is not None and name not in include:
                continue
            if name in exclude:
                continue
            if name == "id":
                continue
            if getattr(field, "is_timestamp", False):
                continue
            if getattr(field, "is_soft_delete", False):
                continue
            if getattr(field, "is_slug", False) and getattr(
                field, "populate_from", None
            ):
                continue
            if getattr(field, "is_vector", False):
                continue
            if getattr(field, "hidden", False) and not getattr(
                field, "is_password", False
            ):
                continue
            if getattr(field, "protected", False) and name != "password":
                # Protected fields like 'is_admin' should not be in auto-forms
                # We allow 'password' because it's protected but necessary for signups
                continue

            # Use field.label if defined, otherwise generate from field name
            label = field.label if field.label else name.replace("_", " ").title()

            # Use field.messages if defined for custom error messages
            messages = field.messages if field.messages else None

            # Build validation rules by combining auto-generated and custom rules
            rules_parts = []
            is_password = getattr(field, "is_password", False)
            if not field.nullable and not is_password:
                rules_parts.append("required")
            if getattr(field, "is_email", False):
                rules_parts.append("email")
            max_length = getattr(field, "max_length", None)
            if max_length:
                rules_parts.append(f"max:{max_length}")

            # Add custom rules from field definition
            if field.rules:
                rules_parts.append(field.rules)

            rules = "|".join(rules_parts)
            attrs = {}
            if max_length:
                attrs["maxlength"] = max_length

            # Check if field has explicit form_type specified
            form_type = getattr(field, "form_type", None)
            if form_type:
                # Use the specified form type directly
                form_method = getattr(cls, form_type, None)
                if form_method and callable(form_method):
                    # Handle different form types with appropriate parameters
                    if form_type == "toggle":
                        schema[name] = form_method(label, rules, messages, **attrs)
                    elif form_type == "rating":
                        max_stars = attrs.pop("max_stars", 5)
                        schema[name] = form_method(label, rules, messages, max_stars=max_stars, **attrs)
                    elif form_type == "otp":
                        length = attrs.pop("length", 6)
                        schema[name] = form_method(label, rules, messages, length=length, **attrs)
                    elif form_type == "month":
                        schema[name] = form_method(label, rules, messages, **attrs)
                    elif form_type == "timerange":
                        schema[name] = form_method(label, rules, messages, **attrs)
                    elif form_type == "wysiwyg":
                        height = attrs.pop("height", 300)
                        schema[name] = form_method(label, rules, messages, height=height, **attrs)
                    elif form_type == "phone":
                        schema[name] = form_method(label, rules, messages, **attrs)
                    elif form_type == "autocomplete":
                        items = attrs.pop("items", [])
                        schema[name] = form_method(label, items, rules, messages, **attrs)
                    elif form_type == "signature":
                        width = attrs.pop("width", 400)
                        height = attrs.pop("height", 200)
                        schema[name] = form_method(label, rules, messages, width=width, height=height, **attrs)
                    else:
                        # Default: call with standard params
                        schema[name] = form_method(label, rules, messages, **attrs)
                    continue

            if is_password:
                schema[name] = cls.password(label, "", messages, **attrs)
            elif getattr(field, "is_file", False):
                schema[name] = cls.file(label, rules, messages, **attrs)
            elif getattr(field, "is_tel", False):
                rules = f"tel|{rules}".strip("|")
                schema[name] = cls.tel(label, rules, messages, **attrs)
            elif getattr(field, "is_url", False):
                rules = f"url|{rules}".strip("|")
                schema[name] = cls.url(label, rules, messages, **attrs)
            elif getattr(field, "is_color", False):
                rules = f"color|{rules}".strip("|")
                schema[name] = cls.color(label, rules, messages, **attrs)
            elif getattr(field, "is_time", False):
                schema[name] = cls.time(label, rules, messages, **attrs)
            elif getattr(field, "is_datetime", False):
                schema[name] = cls.datetime_local(label, rules, messages, **attrs)
            elif getattr(field, "is_enum", False):
                schema[name] = cls.enum(label, field.enum_class, rules, messages, **attrs)
            elif getattr(field, "is_json", False):
                schema[name] = cls.json(label, rules, messages, **attrs)
            elif getattr(field, "is_decimal", False):
                precision = getattr(field, "precision", None)
                if precision is not None:
                    attrs["step"] = (
                        f"0.{'0' * (precision - 1)}1" if precision > 0 else "1"
                    )
                schema[name] = cls.number(label, rules, messages, **attrs)
            elif getattr(field, "is_uuid", False):
                attrs["readonly"] = True
                schema[name] = cls.text(label, rules, messages, **attrs)
            elif getattr(field, "is_foreign_key", False):
                target = field.related_model
                if getattr(field, "dropdown", False):
                    try:
                        items = target.all()
                    except Exception:
                        items = []
                    schema[name] = cls.dropdown(
                        label, items,
                        title=getattr(field, "dropdown_title", "name"),
                        subtitle=getattr(field, "dropdown_subtitle", None),
                        image=getattr(field, "dropdown_image", None),
                        searchable=getattr(field, "dropdown_searchable", True),
                        rules=rules, messages=messages, **attrs
                    )
                else:
                    try:
                        choices = [(o.id, str(o)) for o in target.all()]
                    except Exception:
                        choices = []
                    choices = [("", "— None —")] + choices
                    schema[name] = cls.select(label, choices, rules, messages, **attrs)
            elif getattr(field, "is_dropdown", False):
                schema[name] = cls.dropdown(
                    label, [],
                    searchable=getattr(field, "dropdown_searchable", True),
                    choices=field.choices,
                    rules=rules, messages=messages, **attrs
                )
            elif getattr(field, "is_boolean", False):
                schema[name] = cls.checkbox(label, "", messages, **attrs)
            elif field.sql_type == "INTEGER":
                # Treat INTEGER fields starting with "is_" or "has_" as checkboxes
                if name.startswith("is_") or name.startswith("has_"):
                    schema[name] = cls.checkbox(label, "", messages, **attrs)
                else:
                    schema[name] = cls.number(label, rules, messages, **attrs)
            elif field.sql_type == "REAL":
                precision = getattr(field, "precision", None)
                if precision is not None:
                    attrs["step"] = (
                        f"0.{'0' * (precision - 1)}1" if precision > 0 else "1"
                    )
                schema[name] = cls.number(label, rules, messages, **attrs)
            elif getattr(field, "is_email", False):
                schema[name] = cls.email(label, rules, messages, **attrs)
            elif getattr(field, "is_text", False):
                schema[name] = cls.textarea(label, rules, messages, **attrs)
            else:
                schema[name] = cls.text(label, rules, messages, **attrs)

        if not schema:
            raise ValueError(
                "Form.from_model({}) produced no fields (check include/exclude).".format(
                    getattr(model, "__name__", "model")
                )
            )

        return cls(schema, request)

    # --- Static factory methods for defining schemas ---

    @staticmethod
    def text(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Standard text input."""
        return ("text", label, rules, messages, None, attrs)

    @staticmethod
    def email(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Email input with browser-side validation."""
        return ("email", label, rules, messages, None, attrs)

    @staticmethod
    def password(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Password input (characters masked)."""
        return ("password", label, rules, messages, None, attrs)

    @staticmethod
    def textarea(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Multi-line text area."""
        return ("textarea", label, rules, messages, None, attrs)

    @staticmethod
    def number(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Numeric input."""
        return ("number", label, rules, messages, None, attrs)

    @staticmethod
    def hidden(rules: str = "", **attrs: Any) -> tuple:
        """Hidden input field."""
        return ("hidden", "", rules, None, None, attrs)

    @staticmethod
    def select(
        label: str,
        choices: list[tuple[Any, str]],
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Dropdown selection list."""
        return ("select", label, rules, messages, choices, attrs)

    @staticmethod
    def file(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """File upload input."""
        return ("file", label, rules, messages, None, attrs)

    @staticmethod
    def image(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        preview: bool = True,
        max_width: int = 200,
        max_height: int = 200,
        **attrs: Any,
    ) -> tuple:
        """Image upload with live preview.

        Args:
            label: Field label
            rules: Validation rules (e.g., "required|ext:jpg,png|size:2M")
            messages: Custom error messages
            preview: Show image preview (default True)
            max_width: Max preview width in pixels
            max_height: Max preview height in pixels
            **attrs: Additional HTML attributes

        Example:
            avatar = form.image("Avatar", rules="ext:jpg,png|size:2M", preview=True)
        """
        attrs['preview'] = preview
        attrs['max_width'] = max_width
        attrs['max_height'] = max_height
        return ("image", label, rules, messages, None, attrs)

    @staticmethod
    def tags(
        label: str,
        choices: Optional[list[tuple[str, str]]] = None,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        searchable: bool = True,
        allow_custom: bool = False,
        **attrs: Any,
    ) -> tuple:
        """Multi-select with tag-style UI.

        Args:
            label: Field label
            choices: List of (value, label) tuples for available options
            rules: Validation rules
            messages: Custom error messages
            searchable: Enable search filtering (default True)
            allow_custom: Allow creating custom tags not in choices (default False)
            **attrs: Additional HTML attributes

        Example:
            tags = form.tags("Skills", choices=[("python", "Python"), ("js", "JavaScript")], searchable=True)
        """
        attrs['searchable'] = searchable
        attrs['allow_custom'] = allow_custom
        return ("tags", label, rules, messages, choices, attrs)

    @staticmethod
    def daterange(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        start_label: str = "From",
        end_label: str = "To",
        **attrs: Any,
    ) -> tuple:
        """Date range picker with start and end dates.

        Args:
            label: Field label
            rules: Validation rules
            messages: Custom error messages
            start_label: Label for start date input (default "From")
            end_label: Label for end date input (default "To")
            **attrs: Additional HTML attributes

        Example:
            daterange = form.daterange("Booking Period", start_label="Check-in", end_label="Check-out")
        """
        attrs['start_label'] = start_label
        attrs['end_label'] = end_label
        return ("daterange", label, rules, messages, None, attrs)

    @staticmethod
    def toggle(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Toggle switch (modern alternative to checkbox).

        Args:
            label: Field label
            rules: Validation rules
            messages: Custom error messages
            **attrs: Additional HTML attributes

        Example:
            notifications = form.toggle("Enable Notifications", rules="required")
        """
        return ("toggle", label, rules, messages, None, attrs)

    @staticmethod
    def otp(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        length: int = 6,
        **attrs: Any,
    ) -> tuple:
        """OTP input with separate boxes for each digit.

        Args:
            label: Field label
            rules: Validation rules
            messages: Custom error messages
            length: Number of digits (default 6)
            **attrs: Additional HTML attributes

        Example:
            code = form.otp("Verification Code", length=6, rules="required")
        """
        attrs['length'] = length
        return ("otp", label, rules, messages, None, attrs)

    @staticmethod
    def month(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Month/Year picker.

        Args:
            label: Field label
            rules: Validation rules
            messages: Custom error messages
            **attrs: Additional HTML attributes

        Example:
            expiry = form.month("Card Expiry", rules="required")
        """
        return ("month", label, rules, messages, None, attrs)

    @staticmethod
    def rating(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        max_stars: int = 5,
        **attrs: Any,
    ) -> tuple:
        """Star rating input.

        Args:
            label: Field label
            rules: Validation rules
            messages: Custom error messages
            max_stars: Maximum number of stars (default 5)
            **attrs: Additional HTML attributes

        Example:
            rating = form.rating("Rate this product", max_stars=5, rules="required")
        """
        attrs['max_stars'] = max_stars
        return ("rating", label, rules, messages, None, attrs)

    @staticmethod
    def timerange(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        start_label: str = "From",
        end_label: str = "To",
        **attrs: Any,
    ) -> tuple:
        """Time range picker with start and end times.

        Args:
            label: Field label
            rules: Validation rules
            messages: Custom error messages
            start_label: Label for start time input (default "From")
            end_label: Label for end time input (default "To")
            **attrs: Additional HTML attributes

        Example:
            hours = form.timerange("Business Hours", start_label="Opens", end_label="Closes")
        """
        attrs['start_label'] = start_label
        attrs['end_label'] = end_label
        return ("timerange", label, rules, messages, None, attrs)

    @staticmethod
    def files(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        max_files: int = 10,
        preview: bool = True,
        **attrs: Any,
    ) -> tuple:
        """Multi-file upload with previews.

        Args:
            label: Field label
            rules: Validation rules
            messages: Custom error messages
            max_files: Maximum number of files (default 10)
            preview: Show image previews (default True)
            **attrs: Additional HTML attributes

        Example:
            photos = form.files("Product Photos", max_files=5, rules="ext:jpg,png")
        """
        attrs['max_files'] = max_files
        attrs['preview'] = preview
        return ("files", label, rules, messages, None, attrs)

    @staticmethod
    def autocomplete(
        label: str,
        items: Optional[list] = None,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        min_chars: int = 1,
        **attrs: Any,
    ) -> tuple:
        """Autocomplete input with suggestions.

        Args:
            label: Field label
            items: List of suggestions (strings or dicts)
            rules: Validation rules
            messages: Custom error messages
            min_chars: Minimum characters before showing suggestions (default 1)
            **attrs: Additional HTML attributes

        Example:
            city = form.autocomplete("City", items=["Paris", "London", "New York"], min_chars=2)
        """
        attrs['min_chars'] = min_chars
        return ("autocomplete", label, rules, messages, items, attrs)

    @staticmethod
    def cascading(
        label: str,
        choices: Optional[dict] = None,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Cascading select (dependent dropdowns).

        Args:
            label: Field label
            choices: Dict mapping parent values to child options
            rules: Validation rules
            messages: Custom error messages
            **attrs: Additional HTML attributes

        Example:
            location = form.cascading("Location", choices={
                "France": ["Paris", "Lyon", "Marseille"],
                "UK": ["London", "Manchester", "Edinburgh"]
            })
        """
        return ("cascading", label, rules, messages, choices, attrs)

    @staticmethod
    def phone(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        default_country: str = "US",
        **attrs: Any,
    ) -> tuple:
        """International phone input with country selector.

        Args:
            label: Field label
            rules: Validation rules
            messages: Custom error messages
            default_country: Default country code (default "US")
            **attrs: Additional HTML attributes

        Example:
            mobile = form.phone("Mobile Number", default_country="FR", rules="required")
        """
        attrs['default_country'] = default_country
        return ("phone", label, rules, messages, None, attrs)

    @staticmethod
    def wysiwyg(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        height: int = 300,
        **attrs: Any,
    ) -> tuple:
        """Rich text editor (WYSIWYG).

        Args:
            label: Field label
            rules: Validation rules
            messages: Custom error messages
            height: Editor height in pixels (default 300)
            **attrs: Additional HTML attributes

        Example:
            content = form.wysiwyg("Article Content", height=400, rules="required")
        """
        attrs['height'] = height
        return ("wysiwyg", label, rules, messages, None, attrs)

    @staticmethod
    def dropzone(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        max_files: int = 10,
        **attrs: Any,
    ) -> tuple:
        """Drag and drop file upload zone.

        Args:
            label: Field label
            rules: Validation rules
            messages: Custom error messages
            max_files: Maximum number of files (default 10)
            **attrs: Additional HTML attributes

        Example:
            files = form.dropzone("Drop files here", max_files=5, rules="ext:pdf,doc")
        """
        attrs['max_files'] = max_files
        return ("dropzone", label, rules, messages, None, attrs)

    @staticmethod
    def signature(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        width: int = 400,
        height: int = 200,
        **attrs: Any,
    ) -> tuple:
        """Signature pad (canvas-based).

        Args:
            label: Field label
            rules: Validation rules
            messages: Custom error messages
            width: Canvas width in pixels (default 400)
            height: Canvas height in pixels (default 200)
            **attrs: Additional HTML attributes

        Example:
            signature = form.signature("Sign Here", width=500, height=150, rules="required")
        """
        attrs['width'] = width
        attrs['height'] = height
        return ("signature", label, rules, messages, None, attrs)

    @staticmethod
    def transfer(
        label: str,
        items: Optional[list] = None,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Transfer list (dual listbox).

        Args:
            label: Field label
            items: Available items (will be split into available/selected)
            rules: Validation rules
            messages: Custom error messages
            **attrs: Additional HTML attributes

        Example:
            permissions = form.transfer("Permissions", items=[
                {"id": 1, "name": "Read"},
                {"id": 2, "name": "Write"}
            ])
        """
        return ("transfer", label, rules, messages, items, attrs)

    @staticmethod
    def treeselect(
        label: str,
        items: Optional[list] = None,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Tree select (hierarchical selection).

        Args:
            label: Field label
            items: Hierarchical items with children
            rules: Validation rules
            messages: Custom error messages
            **attrs: Additional HTML attributes

        Example:
            category = form.treeselect("Category", items=[
                {"id": 1, "name": "Electronics", "children": [
                    {"id": 2, "name": "Phones"},
                    {"id": 3, "name": "Laptops"}
                ]}
            ])
        """
        return ("treeselect", label, rules, messages, items, attrs)



    @staticmethod
    def checkbox(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Single checkbox."""
        return ("checkbox", label, rules, messages, None, attrs)

    @staticmethod
    def radio(
        label: str,
        choices: list[tuple[Any, str]],
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Radio button group."""
        return ("radio", label, rules, messages, choices, attrs)

    @staticmethod
    def title(label: str, **attrs: Any) -> tuple:
        """Non-input title element for form organization."""
        return ("title", label, "", None, None, attrs)

    @staticmethod
    def date(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Date picker."""
        return ("date", label, rules, messages, None, attrs)

    @staticmethod
    def datetime_local(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Date and time picker."""
        return ("datetime-local", label, rules, messages, None, attrs)

    @staticmethod
    def time(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Time picker."""
        return ("time", label, rules, messages, None, attrs)

    @staticmethod
    def search(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Search input."""
        return ("search", label, rules, messages, None, attrs)

    @staticmethod
    def url(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """URL input with validation."""
        return ("url", label, rules, messages, None, attrs)

    @staticmethod
    def tel(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Telephone input with validation."""
        return ("tel", label, rules, messages, None, attrs)

    @staticmethod
    def color(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Color picker."""
        return ("color", label, rules, messages, None, attrs)

    @staticmethod
    def range(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Range input (slider)."""
        return ("range", label, rules, messages, None, attrs)

    @staticmethod
    def json(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """JSON input (renders as a textarea with JSON validation)."""
        real_rules = f"{rules}|json".strip("|")
        return ("textarea", label, real_rules, messages, None, attrs)

    @staticmethod
    def dropdown(
        label: str,
        items: Any,
        title: str = "name",
        subtitle: Optional[str] = None,
        image: Optional[str] = None,
        searchable: bool = True,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Rich dropdown selection from a list of objects or Query results."""
        attrs["items"] = items
        attrs["title"] = title
        attrs["subtitle"] = subtitle
        attrs["image"] = image
        attrs["searchable"] = searchable
        return ("dropdown", label, rules, messages, None, attrs)

    @staticmethod
    def enum(
        label: str,
        enum_class: type[enum.Enum],
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Generate a select field from a Python Enum class."""
        choices = [(e.value, e.name.replace("_", " ").title()) for e in enum_class]
        # Add automatic validation to ensure value is in the enum
        valid_values = ",".join(str(e.value) for e in enum_class)
        in_rule = f"in:{valid_values}"
        if rules:
            rules = f"{rules}|{in_rule}"
        else:
            rules = in_rule
        return ("select", label, rules, messages, choices, attrs)
