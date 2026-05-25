from __future__ import annotations

import datetime
import json
from html import escape
from typing import Any

from asok.templates import _extract_nested_attrs, _render_attrs
from asok.utils.geo import get_dial_codes, iso_to_flag

from .utils import _filter_nested_attrs, html_safe_json


def render_textarea(field: Any, val: str, merged: dict[str, Any]) -> str:
    attrs = {"id": field.name, "name": field.name, **merged}
    return f"<textarea{_render_attrs(attrs)}>{val}</textarea>"


def render_select(field: Any, val: str, merged: dict[str, Any]) -> str:
    esc = escape
    select_attrs = _filter_nested_attrs(merged)
    attrs = {"id": field.name, "name": field.name, **select_attrs}
    option_attrs = _extract_nested_attrs(merged, "option")
    options_html = ""
    for opt_val, opt_label in field.choices or []:
        sel = " selected" if str(opt_val) == str(field.value) else ""
        options_html += f'<option value="{esc(str(opt_val))}"{sel}{_render_attrs(option_attrs)}>{esc(str(opt_label))}</option>'
    return f"<select{_render_attrs(attrs)}>{options_html}</select>"


def render_checkbox(field: Any, val: str, merged: dict[str, Any]) -> str:
    attrs = {"type": "checkbox", "id": field.name, "name": field.name, **merged}
    if field.value and field.value != "0":
        attrs["checked"] = True
    return f"<input{_render_attrs(attrs)}>"


def render_radio(field: Any, val: str, merged: dict[str, Any]) -> str:
    esc = escape
    label_attrs = _extract_nested_attrs(merged, "label")
    input_attrs_base = _extract_nested_attrs(merged, "input")
    # If no specific input__* attributes, fall back to filtered main attrs
    if not input_attrs_base:
        input_attrs_base = _filter_nested_attrs(merged)

    html = ""
    for opt_val, opt_label in field.choices or []:
        radio_attrs = {
            "type": "radio",
            "name": field.name,
            "value": str(opt_val),
            **input_attrs_base,
        }
        radio_attrs.pop("id", None)
        if str(opt_val) == str(field.value):
            radio_attrs["checked"] = True
        html += f"<label{_render_attrs(label_attrs)}><input{_render_attrs(radio_attrs)}> {esc(str(opt_label))}</label>"
    return html


def render_dropdown(
    field: Any, val: str, merged: dict[str, Any], overrides: dict[str, Any]
) -> str:
    esc = escape
    render_items = []
    current_label = "Select..."
    items_to_process = field.items or []
    if not items_to_process and field.choices:
        items_to_process = [{"id": v, "name": lbl} for v, lbl in field.choices]

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

            title = get_val(item, field.item_meta["title"])
            if title is None:
                title = str(item)

            subtitle = get_val(item, field.item_meta["subtitle"])
            image = get_val(item, field.item_meta["image"])

        if str(tid) == str(field.value):
            current_label = title
        render_items.append(
            {"id": tid, "title": title, "subtitle": subtitle, "image": image}
        )

    container_attrs = _extract_nested_attrs(merged, "container")
    container_class = container_attrs.get("class", "")
    container_attrs["class"] = f"asok-dropdown {container_class}".strip()
    # Still respect overrides for backward compatibility or direct calls
    if "class" in overrides:
        container_attrs["class"] = overrides["class"]

    trigger_attrs = _extract_nested_attrs(merged, "trigger")
    trigger_class = trigger_attrs.get("class", "")
    trigger_attrs["class"] = f"asok-dropdown-trigger {trigger_class}".strip()
    trigger_attrs["type"] = "button"
    trigger_attrs["asok-on:click"] = "open = !open"

    menu_attrs = _extract_nested_attrs(merged, "menu")
    menu_class = menu_attrs.get("class", "")
    menu_attrs["class"] = f"asok-dropdown-menu {menu_class}".strip()
    menu_attrs["asok-show"] = "open"
    menu_attrs["asok-on:click.outside"] = "open = false"
    menu_attrs["asok-cloak"] = True

    item_attrs_base = _extract_nested_attrs(merged, "item")
    if not item_attrs_base:
        item_attrs_base = _extract_nested_attrs(merged, "option")
    item_class_base = item_attrs_base.get("class", "")

    searchable = field.item_meta["searchable"]

    html_out = f"<div{_render_attrs(container_attrs)} asok-state=\"{{ open: false, search: '', label: '{esc(str(current_label))}' }}\">"
    html_out += f"  <button{_render_attrs(trigger_attrs)}>"
    html_out += f'    <span asok-text="label">{esc(str(current_label))}</span>'
    html_out += '    <svg class="asok-dropdown-arrow" asok-class:rotate-180="open" width="20" height="20" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clip-rule="evenodd" /></svg>'
    html_out += "  </button>"
    html_out += f"  <div{_render_attrs(menu_attrs)}>"
    if searchable:
        search_attrs = _extract_nested_attrs(merged, "search")
        search_class = search_attrs.get("class", "")
        search_attrs["class"] = f"asok-dropdown-search {search_class}".strip()
        html_out += f'    <div{_render_attrs(search_attrs)}><input type="text" asok-model="search" placeholder="Search..." asok-on:keydown.escape="open = false"></div>'
    html_out += '    <div class="asok-dropdown-items">'
    for ri in render_items:
        s_cond = f"!search || '{esc(str(ri['title'])).lower()}'.includes(search.toLowerCase())"
        click = f"Asok.selectDropdown($, '{esc(str(ri['id']))}', '{esc(str(ri['title']))}', $refs.input_{field.name})"

        item_attrs = dict(item_attrs_base)
        item_attrs["class"] = f"asok-dropdown-item {item_class_base}".strip()
        item_attrs["asok-show"] = s_cond
        item_attrs["asok-on:click"] = click

        html_out += f"      <div{_render_attrs(item_attrs)}>"
        if ri["image"]:
            html_out += f'        <img src="{esc(str(ri["image"]))}" class="asok-dropdown-item-img">'
        html_out += '        <div class="asok-dropdown-item-content">'
        html_out += f'          <div class="asok-dropdown-item-title">{esc(str(ri["title"]))}</div>'
        if ri["subtitle"]:
            html_out += f'          <div class="asok-dropdown-item-subtitle">{esc(str(ri["subtitle"]))}</div>'
        html_out += "        </div>"
        html_out += "      </div>"
    html_out += "    </div>"
    html_out += "  </div>"
    html_out += f'  <input type="hidden" name="{field.name}" id="{field.name}" value="{esc(str(field.value))}" asok-ref="input_{field.name}">'
    html_out += "</div>"
    return html_out


def render_image(field: Any, val: str, merged: dict[str, Any]) -> str:
    preview = field.attrs.get("preview", True)
    max_width = field.attrs.get("max_width", 200)
    max_height = field.attrs.get("max_height", 200)

    container_attrs = _extract_nested_attrs(merged, "container")
    container_class = container_attrs.get("class", "")
    container_attrs["class"] = f"asok-image-upload {container_class}".strip()

    input_attrs = _extract_nested_attrs(merged, "input")
    if not input_attrs:
        input_attrs = _filter_nested_attrs(merged)
    input_class = input_attrs.get("class", "")
    input_attrs["class"] = input_class.strip()

    preview_attrs = _extract_nested_attrs(merged, "preview")
    preview_class = preview_attrs.get("class", "")
    preview_attrs["class"] = f"asok-image-preview {preview_class}".strip()

    if preview:
        # Wrap in container with asok-state for CSP-compliant preview
        initial_preview = field.value if field.value else ""
        state = html_safe_json({"preview": initial_preview})

        html_out = f'<div{_render_attrs(container_attrs)} asok-state="{state}">'

        # File input with asok-on:change instead of inline onchange
        file_attrs = {
            "type": "file",
            "id": field.name,
            "name": field.name,
            "accept": "image/*",
            **input_attrs,
        }
        file_attrs.pop("preview", None)
        file_attrs.pop("max_width", None)
        file_attrs.pop("max_height", None)

        # Use asok-on:change for CSP compliance (no inline event handlers)
        change_handler = "Asok.previewImage($event, $)"
        html_out += (
            f'<input{_render_attrs(file_attrs)} asok-on:change="{change_handler}">'
        )

        # Preview image bound to state (asok-cloak hides until Asok loads)
        preview_style = (
            f"max-width:{max_width}px;max-height:{max_height}px;margin-top:10px;"
        )
        if "style" in preview_attrs:
            preview_style = f"{preview_style} {preview_attrs['style']}".strip()
        preview_attrs["style"] = preview_style
        preview_attrs["asok-show"] = "preview"
        preview_attrs["asok-bind:src"] = "preview"
        preview_attrs["alt"] = "Preview"
        preview_attrs["asok-cloak"] = True

        html_out += f'<br><img{_render_attrs(preview_attrs)}>'
        html_out += "</div>"
    else:
        # No preview - just a simple file input
        file_attrs = {
            "type": "file",
            "id": field.name,
            "name": field.name,
            "accept": "image/*",
            **input_attrs,
        }
        file_attrs.pop("preview", None)
        file_attrs.pop("max_width", None)
        file_attrs.pop("max_height", None)
        html_out = f"<input{_render_attrs(file_attrs)}>"

    return html_out


def render_tags(field: Any, val: str, merged: dict[str, Any]) -> str:
    esc = escape
    searchable = field.attrs.get("searchable", True)

    # Parse current value (can be JSON array, comma-separated, or empty)
    current_values = []
    if field.value:
        try:
            current_values = (
                json.loads(field.value) if isinstance(field.value, str) else field.value
            )
        except (json.JSONDecodeError, TypeError):
            # Fallback to comma-separated
            current_values = [
                v.strip() for v in str(field.value).split(",") if v.strip()
            ]

    # Build available options from choices
    available_options = []
    current_labels = {}
    if field.choices:
        for val_opt, label in field.choices:
            available_options.append({"value": str(val_opt), "label": str(label)})
            if str(val_opt) in current_values:
                current_labels[str(val_opt)] = str(label)

    # Create state with selected tags
    selected_tags = [
        {"value": v, "label": current_labels.get(v, v)} for v in current_values
    ]
    state = html_safe_json(
        {"selected": selected_tags, "open": False, "search": ""}
    )

    container_attrs = _extract_nested_attrs(merged, "container")
    container_class = container_attrs.get("class", "")
    container_attrs["class"] = f"asok-tags {container_class}".strip()

    selected_attrs = _extract_nested_attrs(merged, "selected")
    selected_class = selected_attrs.get("class", "")
    selected_attrs["class"] = f"asok-tags-selected {selected_class}".strip()

    tag_attrs = _extract_nested_attrs(merged, "tag")
    tag_class = tag_attrs.get("class", "")
    tag_attrs["class"] = f"asok-tag {tag_class}".strip()

    add_attrs = _extract_nested_attrs(merged, "add")
    add_class = add_attrs.get("class", "")
    add_attrs["class"] = f"asok-tags-add {add_class}".strip()

    menu_attrs = _extract_nested_attrs(merged, "menu")
    menu_class = menu_attrs.get("class", "")
    menu_attrs["class"] = f"asok-tags-menu {menu_class}".strip()

    search_attrs = _extract_nested_attrs(merged, "search")
    search_class = search_attrs.get("class", "")
    search_attrs["class"] = f"asok-tags-search {search_class}".strip()

    option_attrs_base = _extract_nested_attrs(merged, "option")
    option_class_base = option_attrs_base.get("class", "")

    html_out = f'<div{_render_attrs(container_attrs)} asok-state="{state}">'

    # Display selected tags
    html_out += f'  <div{_render_attrs(selected_attrs)}>'
    html_out += '    <template asok-for="tag in selected">'
    html_out += f'      <span{_render_attrs(tag_attrs)}>'
    html_out += '        <span asok-text="tag.label"></span>'
    html_out += (
        f'        <button type="button" class="asok-tag-remove" asok-on:click="Asok.removeTag($, tag, $refs.input_{field.name})">×</button>'
    )
    html_out += "      </span>"
    html_out += "    </template>"

    # Add button
    add_attrs["type"] = "button"
    add_attrs["asok-on:click"] = "open = !open"
    html_out += f'    <button{_render_attrs(add_attrs)}>+ Add</button>'
    html_out += "  </div>"

    # Dropdown menu with options
    menu_attrs["asok-show"] = "open"
    menu_attrs["asok-on:click.outside"] = "open = false"
    menu_attrs["asok-cloak"] = True
    html_out += f'  <div{_render_attrs(menu_attrs)}>'
    if searchable:
        search_attrs["type"] = "text"
        search_attrs["asok-model"] = "search"
        search_attrs["placeholder"] = "Search..."
        search_attrs["asok-on:keydown.escape"] = "open = false"
        html_out += f'    <input{_render_attrs(search_attrs)}>'
    html_out += '    <div class="asok-tags-options">'

    for opt in available_options:
        search_cond = (
            f"!search || '{esc(opt['label']).lower()}'.includes(search.toLowerCase())"
            if searchable
            else "true"
        )
        already_selected = f"selected.some(t => t.value === '{esc(opt['value'])}')"
        click_action = f"Asok.addTag($, {{'value': '{esc(opt['value'])}', 'label': '{esc(opt['label'])}'}}, $refs.input_{field.name})"

        option_attrs = dict(option_attrs_base)
        option_attrs["class"] = f"asok-tags-option {option_class_base}".strip()
        option_attrs["asok-show"] = f"{search_cond} && !{already_selected}"
        option_attrs["asok-on:click"] = click_action

        html_out += f'      <div{_render_attrs(option_attrs)}>{esc(opt["label"])}</div>'

    html_out += "    </div>"
    html_out += "  </div>"

    # Hidden input to store selected values as JSON array
    # SECURITY: Use html_safe_json for proper escaping
    value_json = html_safe_json(current_values)
    html_out += f'  <input type="hidden" name="{field.name}" id="{field.name}" value="{value_json}" asok-ref="input_{field.name}">'
    html_out += "</div>"

    return html_out


def render_daterange(field: Any, val: str, merged: dict[str, Any]) -> str:
    esc = escape
    start_label = field.attrs.get("start_label", "From")
    end_label = field.attrs.get("end_label", "To")

    # Parse current value (expected as JSON with {start: "...", end: "..."})
    start_value = ""
    end_value = ""
    if field.value:
        try:
            if isinstance(field.value, str):
                parsed = json.loads(field.value)
                start_value = parsed.get("start", "")
                end_value = parsed.get("end", "")
            elif isinstance(field.value, dict):
                start_value = field.value.get("start", "")
                end_value = field.value.get("end", "")
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

    # Use asok-state for reactive date range (CSP-compliant)
    state = html_safe_json({"start": start_value, "end": end_value})

    container_attrs = _extract_nested_attrs(merged, "container")
    container_class = container_attrs.get("class", "")
    container_attrs["class"] = f"asok-daterange {container_class}".strip()

    field_attrs_base = _extract_nested_attrs(merged, "field")
    field_class_base = field_attrs_base.get("class", "")

    label_attrs_base = _extract_nested_attrs(merged, "label")
    label_class_base = label_attrs_base.get("class", "")

    input_attrs_base = _extract_nested_attrs(merged, "input")
    if not input_attrs_base:
        input_attrs_base = _filter_nested_attrs(merged)
    input_class_base = input_attrs_base.get("class", "")

    html_out = f'<div{_render_attrs(container_attrs)} asok-state="{state}">'

    # Start date input with asok-on:change
    field_attrs = dict(field_attrs_base)
    field_attrs["class"] = f"asok-daterange-field {field_class_base}".strip()
    html_out += f"  <div{_render_attrs(field_attrs)}>"

    label_attrs = dict(label_attrs_base)
    label_attrs["class"] = f"asok-daterange-label {label_class_base}".strip()
    html_out += f'    <label{_render_attrs(label_attrs)}>{esc(start_label)}</label>'

    start_attrs = {
        "type": "date",
        "id": f"{field.name}_start",
        **input_attrs_base,
    }
    start_attrs["class"] = f"asok-daterange-input {input_class_base}".strip()
    if "future" in field.rules:
        start_attrs["min"] = datetime.date.today().isoformat()

    # Use asok-model for two-way binding and update hidden input
    update_hidden = f"Asok.updateHiddenJson($refs.hidden_{field.name}, {{'start':start,'end':end}})"
    html_out += f'    <input{_render_attrs(start_attrs)} asok-model="start" asok-on:change="{update_hidden}">'
    html_out += "  </div>"

    # End date input restricted by start date
    field_attrs = dict(field_attrs_base)
    field_attrs["class"] = f"asok-daterange-field {field_class_base}".strip()
    html_out += f"  <div{_render_attrs(field_attrs)}>"

    label_attrs = dict(label_attrs_base)
    label_attrs["class"] = f"asok-daterange-label {label_class_base}".strip()
    html_out += f'    <label{_render_attrs(label_attrs)}>{esc(end_label)}</label>'

    end_attrs = {
        "type": "date",
        "id": f"{field.name}_end",
        "asok-bind:min": "start",
        **input_attrs_base,
    }
    end_attrs["class"] = f"asok-daterange-input {input_class_base}".strip()
    html_out += f'    <input{_render_attrs(end_attrs)} asok-model="end" asok-on:change="{update_hidden}">'
    html_out += "  </div>"

    # Hidden input to store the range as JSON
    # SECURITY: Use html_safe_json for proper escaping
    value_json = html_safe_json({"start": start_value, "end": end_value})
    html_out += f'  <input type="hidden" name="{field.name}" id="{field.name}" value="{value_json}" asok-ref="hidden_{field.name}">'
    html_out += "</div>"

    return html_out


def render_toggle(field: Any, val: str, merged: dict[str, Any]) -> str:
    container_attrs = _extract_nested_attrs(merged, "container")
    container_class = container_attrs.get("class", "")
    container_attrs["class"] = f"asok-toggle {container_class}".strip()

    html_out = f"<div{_render_attrs(container_attrs)}>"

    input_attrs_base = _extract_nested_attrs(merged, "input")
    if not input_attrs_base:
        input_attrs_base = _filter_nested_attrs(merged)

    checkbox_attrs = {
        "type": "checkbox",
        "id": field.name,
        "name": field.name,
        **input_attrs_base,
    }
    checkbox_class = checkbox_attrs.get("class", "")
    checkbox_attrs["class"] = f"peer sr-only {checkbox_class}".strip()

    if field.value and field.value != "0":
        checkbox_attrs["checked"] = True

    slider_attrs = _extract_nested_attrs(merged, "slider")
    slider_class = slider_attrs.get("class", "")
    slider_attrs["class"] = f"asok-toggle-slider {slider_class}".strip()
    slider_attrs["for"] = field.name

    html_out += f"<input{_render_attrs(checkbox_attrs)}>"
    html_out += f"<label{_render_attrs(slider_attrs)}></label>"
    html_out += "</div>"
    return html_out


def render_otp(field: Any, val: str, merged: dict[str, Any]) -> str:
    length = field.attrs.get("length", 6)
    container_attrs = _extract_nested_attrs(merged, "container")
    container_class = container_attrs.get("class", "")
    container_attrs["class"] = f"asok-otp {container_class}".strip()

    # Split current value into individual digits
    current_value = str(field.value) if field.value else ""
    # Pad with empty strings if needed
    digits = list(current_value[:length]) + [""] * (
        length - len(current_value[:length])
    )

    # SECURITY: Use html_safe_json for proper JSON escaping in HTML attribute
    state_json = html_safe_json({"digits": digits})
    html_out = f'<div{_render_attrs(container_attrs)} asok-state="{state_json}">'

    input_attrs_base = _extract_nested_attrs(merged, "input")
    input_class_base = input_attrs_base.get("class", "")

    for i in range(length):
        input_attrs = {
            "type": "text",
            "maxlength": "1",
            **input_attrs_base,
            "asok-model": f"digits[{i}]",
        }
        input_attrs["class"] = f"asok-otp-input {input_class_base}".strip()
        # Auto-focus next input on keyup
        next_focus = "Asok.handleOtpKeyup($event)"
        html_out += f'<input{_render_attrs(input_attrs)} asok-on:keyup="{next_focus}">'

    # Hidden input to store the complete OTP. Bound to the reactive 'digits' array.
    html_out += f'<input type="hidden" name="{field.name}" id="{field.name}" asok-bind:value="digits.join(\'\')" asok-ref="hidden_{field.name}">'
    html_out += "</div>"
    return html_out


def render_month(field: Any, val: str, merged: dict[str, Any]) -> str:
    attrs = {"type": "month", "id": field.name, "name": field.name, **merged}
    attrs["value"] = val
    return f"<input{_render_attrs(attrs)}>"


def render_rating(field: Any, val: str, merged: dict[str, Any]) -> str:
    max_stars = field.attrs.get("max_stars", 5)
    current_rating = int(field.value) if field.value else 0

    container_attrs = _extract_nested_attrs(merged, "container")
    container_class = container_attrs.get("class", "")
    container_attrs["class"] = f"asok-rating {container_class}".strip()

    html_out = f'<div{_render_attrs(container_attrs)} asok-state=\'{{"rating": {current_rating}, "hover": 0}}\'>'

    star_attrs_base = _extract_nested_attrs(merged, "star")
    star_class_base = star_attrs_base.get("class", "")

    for i in range(1, max_stars + 1):
        star_attrs = {
            **star_attrs_base,
            "asok-on:click": f"Asok.setRating($, {i}, $refs.hidden_{field.name})",
            "asok-on:mouseenter": f"hover={i}",
            "asok-on:mouseleave": "hover=0",
        }
        star_attrs["class"] = f"asok-rating-star {star_class_base}".strip()
        # Show filled star if rated or hovered
        filled_condition = f"(hover >= {i}) || (hover === 0 && rating >= {i})"
        html_out += f"<span{_render_attrs(star_attrs)}>"
        html_out += f'<svg asok-show="{filled_condition}" width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>'
        html_out += f'<svg asok-show="!({filled_condition})" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>'
        html_out += "</span>"

    html_out += f'<input type="hidden" name="{field.name}" id="{field.name}" value="{current_rating}" asok-ref="hidden_{field.name}">'
    html_out += "</div>"
    return html_out


def render_timerange(field: Any, val: str, merged: dict[str, Any]) -> str:
    esc = escape
    start_label = field.attrs.get("start_label", "From")
    end_label = field.attrs.get("end_label", "To")

    # Parse current value
    start_value = ""
    end_value = ""
    if field.value:
        try:
            if isinstance(field.value, str):
                parsed = json.loads(field.value)
                start_value = parsed.get("start", "")
                end_value = parsed.get("end", "")
            elif isinstance(field.value, dict):
                start_value = field.value.get("start", "")
                end_value = field.value.get("end", "")
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

    state = html_safe_json({"start": start_value, "end": end_value})
    container_attrs = _extract_nested_attrs(merged, "container")
    container_class = container_attrs.get("class", "")
    container_attrs["class"] = f"asok-timerange {container_class}".strip()
    html_out = f'<div{_render_attrs(container_attrs)} asok-state="{state}">'

    field_attrs_base = _extract_nested_attrs(merged, "field")
    field_class_base = field_attrs_base.get("class", "")

    label_attrs_base = _extract_nested_attrs(merged, "label")
    label_class_base = label_attrs_base.get("class", "")

    input_attrs_base = _extract_nested_attrs(merged, "input")
    input_class_base = input_attrs_base.get("class", "")
    update_hidden = f"Asok.updateHiddenJson($refs.hidden_{field.name}, {{'start':start,'end':end}})"

    # Start time input
    field_attrs = dict(field_attrs_base)
    field_attrs["class"] = f"asok-timerange-field {field_class_base}".strip()
    html_out += f"  <div{_render_attrs(field_attrs)}>"

    label_attrs = dict(label_attrs_base)
    label_attrs["class"] = f"asok-timerange-label {label_class_base}".strip()
    html_out += f"    <label{_render_attrs(label_attrs)}>{esc(start_label)}</label>"

    start_attrs = {
        "type": "time",
        "id": f"{field.name}_start",
        **input_attrs_base,
    }
    start_attrs["class"] = f"asok-timerange-input {input_class_base}".strip()
    html_out += f'    <input{_render_attrs(start_attrs)} asok-model="start" asok-on:change="{update_hidden}">'
    html_out += "  </div>"

    # End time input
    field_attrs = dict(field_attrs_base)
    field_attrs["class"] = f"asok-timerange-field {field_class_base}".strip()
    html_out += f"  <div{_render_attrs(field_attrs)}>"

    label_attrs = dict(label_attrs_base)
    label_attrs["class"] = f"asok-timerange-label {label_class_base}".strip()
    html_out += f"    <label{_render_attrs(label_attrs)}>{esc(end_label)}</label>"

    end_attrs = {
        "type": "time",
        "id": f"{field.name}_end",
        **input_attrs_base,
    }
    end_attrs["class"] = f"asok-timerange-input {input_class_base}".strip()
    html_out += f'    <input{_render_attrs(end_attrs)} asok-model="end" asok-on:change="{update_hidden}">'
    html_out += "  </div>"

    # Hidden input
    # SECURITY: Use html_safe_json for proper escaping
    value_json = html_safe_json({"start": start_value, "end": end_value})
    html_out += f'  <input type="hidden" name="{field.name}" id="{field.name}" value="{value_json}" asok-ref="hidden_{field.name}">'
    html_out += "</div>"
    return html_out


def render_files(field: Any, val: str, merged: dict[str, Any]) -> str:
    max_files = field.attrs.get("max_files", 10)
    preview_enabled = field.attrs.get("preview", True)

    container_attrs = _extract_nested_attrs(merged, "container")
    container_class = container_attrs.get("class", "")
    container_attrs["class"] = f"asok-files {container_class}".strip()

    input_attrs = _extract_nested_attrs(merged, "input")
    if not input_attrs:
        input_attrs = _filter_nested_attrs(merged)
    input_class = input_attrs.get("class", "")
    input_attrs["class"] = input_class.strip()

    preview_attrs = _extract_nested_attrs(merged, "preview")
    preview_class = preview_attrs.get("class", "")
    preview_attrs["class"] = f"asok-files-preview {preview_class}".strip()

    item_attrs = _extract_nested_attrs(merged, "item")
    item_class = item_attrs.get("class", "")
    item_attrs["class"] = f"asok-file-item {item_class}".strip()

    img_attrs = _extract_nested_attrs(merged, "img")
    img_class = img_attrs.get("class", "")
    img_attrs["class"] = img_class.strip()
    img_style = img_attrs.get("style", "")
    if not img_style:
        img_attrs["style"] = "max-width:100px;max-height:100px;"

    btn_attrs = _extract_nested_attrs(merged, "btn")
    btn_class = btn_attrs.get("class", "")
    btn_attrs["class"] = btn_class.strip()

    state = html_safe_json({"files": []})
    html_out = f'<div{_render_attrs(container_attrs)} asok-state="{state}">'

    file_attrs = {
        "type": "file",
        "id": field.name,
        "name": field.name,
        "multiple": True,
        "accept": "*/*",
        **input_attrs,
    }
    file_attrs.pop("max_files", None)
    file_attrs.pop("preview", None)

    # Handle file selection
    change_handler = f"Asok.handleFilesChange($event, $, {max_files})"
    html_out += f'<input{_render_attrs(file_attrs)} asok-on:change="{change_handler}">'

    if preview_enabled:
        html_out += f'  <div{_render_attrs(preview_attrs)}>'
        html_out += '  <template asok-for="(file, index) in files">'
        html_out += f'    <div{_render_attrs(item_attrs)}>'

        img_attrs["asok-show"] = "file.url"
        img_attrs["asok-bind:src"] = "file.url"
        html_out += f'      <img{_render_attrs(img_attrs)}>'
        html_out += '      <span asok-text="file.name"></span>'

        btn_attrs["type"] = "button"
        btn_attrs["asok-on:click"] = "files=files.filter((_,i)=>i!==index)"
        html_out += f'      <button{_render_attrs(btn_attrs)}>×</button>'
        html_out += "    </div>"
        html_out += "  </template>"
        html_out += "</div>"

    html_out += "</div>"
    return html_out


def render_autocomplete(field: Any, val: str, merged: dict[str, Any]) -> str:
    min_chars = field.attrs.get("min_chars", 1)
    items = field.items or field.choices or []

    # SECURITY: Store items in state instead of inline JS to prevent injection
    state = html_safe_json(
        {"query": field.value or "", "show": False, "filtered": items, "all": items}
    )

    container_attrs = _extract_nested_attrs(merged, "container")
    container_class = container_attrs.get("class", "")
    container_attrs["class"] = f"asok-autocomplete {container_class}".strip()

    input_attrs = _extract_nested_attrs(merged, "input")
    if not input_attrs:
        input_attrs = _filter_nested_attrs(merged)
    input_class = input_attrs.get("class", "")
    input_attrs["class"] = input_class.strip()

    menu_attrs = _extract_nested_attrs(merged, "menu")
    menu_class = menu_attrs.get("class", "")
    menu_attrs["class"] = f"asok-autocomplete-menu {menu_class}".strip()

    item_attrs_base = _extract_nested_attrs(merged, "item")
    if not item_attrs_base:
        item_attrs_base = _extract_nested_attrs(merged, "option")
    item_class_base = item_attrs_base.get("class", "")

    html_out = f'<div{_render_attrs(container_attrs)} asok-state="{state}">'

    # Input with filtering
    input_attrs["type"] = "text"
    input_attrs["id"] = field.name
    input_attrs["name"] = field.name
    input_attrs["autocomplete"] = "off"

    # SECURITY: Use state.all instead of hardcoding JSON in JS
    filter_logic = f"Asok.filterAutocomplete($, {min_chars})"
    html_out += f'<input{_render_attrs(input_attrs)} asok-model="query" asok-on:input="{filter_logic}" asok-on:blur="setTimeout(()=>show=false,200)">'

    # Suggestions dropdown
    menu_attrs["asok-show"] = "show && filtered.length > 0"
    menu_attrs["asok-cloak"] = True
    html_out += f'<div{_render_attrs(menu_attrs)}>'
    html_out += '  <template asok-for="item in filtered">'
    select_action = f"Asok.selectAutocomplete($, item, $refs.input_{field.name})"

    item_attrs = dict(item_attrs_base)
    item_attrs["class"] = f"asok-autocomplete-item {item_class_base}".strip()
    item_attrs["asok-on:click"] = select_action
    item_attrs["asok-text"] = "item"

    html_out += f'    <div{_render_attrs(item_attrs)}></div>'
    html_out += "  </template>"
    html_out += "</div>"

    html_out += "</div>"
    return html_out


def render_cascading(field: Any, val: str, merged: dict[str, Any]) -> str:
    esc = escape
    choices_dict = field.choices or {}

    parents = list(choices_dict.keys())
    state_dict = {
        "parent": "",
        "child": "",
        "children": [],
        "map": choices_dict,
    }

    state = html_safe_json(state_dict)

    container_attrs = _extract_nested_attrs(merged, "container")
    container_class = container_attrs.get("class", "")
    container_attrs["class"] = f"asok-cascading {container_class}".strip()

    select_attrs_base = _extract_nested_attrs(merged, "select")
    if not select_attrs_base:
        select_attrs_base = _filter_nested_attrs(merged)
    select_class_base = select_attrs_base.get("class", "")

    option_attrs = _extract_nested_attrs(merged, "option")

    html_out = f'<div{_render_attrs(container_attrs)} asok-state="{state}">'

    # Parent select
    parent_attrs = dict(select_attrs_base)
    parent_attrs["class"] = f"asok-cascading-parent {select_class_base}".strip()
    parent_attrs["asok-model"] = "parent"
    parent_attrs["asok-on:change"] = "children=map[parent]||[];child=''"

    html_out += f"<select{_render_attrs(parent_attrs)}>"
    html_out += f'<option value=""{_render_attrs(option_attrs)}>Select...</option>'
    for parent in parents:
        html_out += f'<option value="{esc(parent)}"{_render_attrs(option_attrs)}>{esc(parent)}</option>'
    html_out += "</select>"

    # Child select
    child_attrs = dict(select_attrs_base)
    child_attrs["class"] = f"asok-cascading-child {select_class_base}".strip()
    child_attrs["asok-model"] = "child"
    child_attrs["asok-show"] = "children.length > 0"
    child_attrs["asok-cloak"] = True

    html_out += f"<select{_render_attrs(child_attrs)}>"
    html_out += f'<option value=""{_render_attrs(option_attrs)}>Select...</option>'
    html_out += '<template asok-for="option in children">'

    child_opt_attrs = dict(option_attrs)
    child_opt_attrs["asok-bind:value"] = "option"
    child_opt_attrs["asok-text"] = "option"
    html_out += f'<option{_render_attrs(child_opt_attrs)}></option>'
    html_out += "</template>"
    html_out += "</select>"

    # Hidden input to store the selection
    html_out += f'<input type="hidden" name="{field.name}" id="{field.name}" asok-bind:value="parent+\' > \'+child">'
    html_out += "</div>"
    return html_out


def render_phone(field: Any, val: str, merged: dict[str, Any]) -> str:
    default_country = field.attrs.get("default_country", "US")

    countries = get_dial_codes()

    default_code = next((c[1] for c in countries if c[0] == default_country), "+1")
    state = html_safe_json({"code": default_code, "number": ""})

    container_attrs = _extract_nested_attrs(merged, "container")
    container_class = container_attrs.get("class", "")
    container_attrs["class"] = f"asok-phone {container_class}".strip()

    select_attrs = _extract_nested_attrs(merged, "select")
    select_class = select_attrs.get("class", "")
    select_attrs["class"] = f"asok-phone-code {select_class}".strip()

    input_attrs = _extract_nested_attrs(merged, "input")
    if not input_attrs:
        input_attrs = _filter_nested_attrs(merged)
    input_class = input_attrs.get("class", "")
    input_attrs["class"] = input_class.strip()

    html_out = f'<div{_render_attrs(container_attrs)} asok-state="{state}">'

    # Country code select
    select_attrs["asok-model"] = "code"
    html_out += f"<select{_render_attrs(select_attrs)}>"
    for code, dial, name, *rest in countries:
        selected = " selected" if code == default_country else ""
        flag = iso_to_flag(code)
        html_out += f'<option value="{dial}"{selected}>{flag} {dial}</option>'
    html_out += "</select>"

    # Phone number input
    update_hidden = f"Asok.updateHiddenValue($refs.hidden_{field.name}, code+number)"

    input_attrs["type"] = "tel"
    input_attrs["asok-model"] = "number"
    input_attrs["asok-on:input"] = update_hidden
    if "placeholder" not in input_attrs:
        input_attrs["placeholder"] = "Phone number"

    html_out += f'<input{_render_attrs(input_attrs)}>'

    # Hidden input to store complete phone
    html_out += f'<input type="hidden" name="{field.name}" id="{field.name}" asok-bind:value="code+number" asok-ref="hidden_{field.name}">'
    html_out += "</div>"
    return html_out


def render_wysiwyg(field: Any, val: str, merged: dict[str, Any]) -> str:
    esc = escape
    height = field.attrs.get("height", 300)
    current_content = field.value or ""

    state = html_safe_json({"content": current_content})

    container_attrs = _extract_nested_attrs(merged, "container")
    container_class = container_attrs.get("class", "")
    container_attrs["class"] = f"asok-wysiwyg {container_class}".strip()

    toolbar_attrs = _extract_nested_attrs(merged, "toolbar")
    toolbar_class = toolbar_attrs.get("class", "")
    toolbar_attrs["class"] = f"asok-wysiwyg-toolbar {toolbar_class}".strip()

    editor_attrs = _extract_nested_attrs(merged, "editor")
    if not editor_attrs:
        editor_attrs = _filter_nested_attrs(merged)
    editor_class = editor_attrs.get("class", "")
    editor_attrs["class"] = f"asok-wysiwyg-editor {editor_class}".strip()

    btn_attrs_base = _extract_nested_attrs(merged, "btn")
    btn_class_base = btn_attrs_base.get("class", "")

    html_out = f'<div{_render_attrs(container_attrs)} asok-state="{state}">'

    # Toolbar - limited to safe commands only
    html_out += f"  <div{_render_attrs(toolbar_attrs)}>"

    bold_btn = dict(btn_attrs_base)
    bold_btn["class"] = f"asok-wysiwyg-btn-bold {btn_class_base}".strip()
    bold_btn["type"] = "button"
    bold_btn["asok-on:click"] = "document.execCommand('bold')"
    html_out += f'<button{_render_attrs(bold_btn)}><b>B</b></button>'

    italic_btn = dict(btn_attrs_base)
    italic_btn["class"] = f"asok-wysiwyg-btn-italic {btn_class_base}".strip()
    italic_btn["type"] = "button"
    italic_btn["asok-on:click"] = "document.execCommand('italic')"
    html_out += f'<button{_render_attrs(italic_btn)}><i>I</i></button>'

    under_btn = dict(btn_attrs_base)
    under_btn["class"] = f"asok-wysiwyg-btn-underline {btn_class_base}".strip()
    under_btn["type"] = "button"
    under_btn["asok-on:click"] = "document.execCommand('underline')"
    html_out += f'<button{_render_attrs(under_btn)}><u>U</u></button>'

    list_btn = dict(btn_attrs_base)
    list_btn["class"] = f"asok-wysiwyg-btn-list {btn_class_base}".strip()
    list_btn["type"] = "button"
    list_btn["asok-on:click"] = "document.execCommand('insertUnorderedList')"
    html_out += f'<button{_render_attrs(list_btn)}>• List</button>'
    html_out += "</div>"

    # Editor (contenteditable div)
    update_hidden = f"Asok.updateWysiwyg($event, $, $refs.hidden_{field.name})"
    editor_style = f"min-height:{height}px;border:1px solid #ddd;padding:10px;"
    if "style" in editor_attrs:
         editor_style = f"{editor_style} {editor_attrs['style']}".strip()
    editor_attrs["style"] = editor_style
    editor_attrs["contenteditable"] = "true"
    editor_attrs["asok-on:input"] = update_hidden

    html_out += f'<div{_render_attrs(editor_attrs)}>{esc(current_content)}</div>'

    # Hidden input to store HTML
    html_out += f'<input type="hidden" name="{field.name}" id="{field.name}" value="{esc(current_content)}" asok-ref="hidden_{field.name}">'
    html_out += "</div>"
    return html_out


def render_dropzone(field: Any, val: str, merged: dict[str, Any]) -> str:
    max_files = field.attrs.get("max_files", 10)

    container_attrs = _extract_nested_attrs(merged, "container")
    container_class = container_attrs.get("class", "")
    container_attrs["class"] = f"asok-dropzone {container_class}".strip()

    area_attrs = _extract_nested_attrs(merged, "area")
    if not area_attrs:
        area_attrs = _filter_nested_attrs(merged)
    area_class = area_attrs.get("class", "")
    area_attrs["class"] = f"asok-dropzone-area {area_class}".strip()
    area_style = area_attrs.get("style", "")
    if not area_style:
        area_attrs["style"] = "border:2px dashed #ccc;padding:40px;text-align:center;cursor:pointer;"

    input_attrs = _extract_nested_attrs(merged, "input")
    input_class = input_attrs.get("class", "")
    input_attrs["class"] = input_class.strip()

    list_attrs = _extract_nested_attrs(merged, "list")
    list_class = list_attrs.get("class", "")
    list_attrs["class"] = f"asok-dropzone-files {list_class}".strip()

    item_attrs_base = _extract_nested_attrs(merged, "item")
    item_class_base = item_attrs_base.get("class", "")

    btn_attrs_base = _extract_nested_attrs(merged, "btn")
    btn_class_base = btn_attrs_base.get("class", "")

    state = html_safe_json({"files": [], "dragging": False})
    html_out = f'<div{_render_attrs(container_attrs)} asok-state="{state}">'

    # Drop zone div - copy exact syntax from working 'files' component
    drop_handler = f"Asok.handleDropzoneDrop($event, $, {max_files}, $refs.input_{field.name})"
    area_attrs["asok-on:dragover.prevent"] = "dragging=true"
    area_attrs["asok-on:dragleave"] = "dragging=false"
    area_attrs["asok-on:drop.prevent"] = drop_handler
    area_attrs["asok-bind:class"] = "dragging?'dragging':''"

    html_out += f'<div{_render_attrs(area_attrs)}>'
    html_out += f'<p>Drag & drop files here or <label for="{field.name}" style="color:blue;cursor:pointer;">browse</label></p>'
    html_out += "</div>"

    # Hidden file input - copy exact syntax from working 'files' component
    file_attrs = {
        "type": "file",
        "id": field.name,
        "name": field.name,
        "multiple": True,
        "asok-ref": f"input_{field.name}",
        **input_attrs,
    }
    file_attrs_style = file_attrs.get("style", "")
    file_attrs["style"] = f"display:none; {file_attrs_style}".strip()

    change_handler = f"Asok.handleDropzoneChange($event, $, {max_files})"
    html_out += f'<input{_render_attrs(file_attrs)} asok-on:change="{change_handler}">'

    # File list
    html_out += f'  <ul{_render_attrs(list_attrs)}>'
    html_out += '  <template asok-for="(file, index) in files">'

    item_attrs = dict(item_attrs_base)
    item_attrs["class"] = item_class_base.strip()
    html_out += f'    <li{_render_attrs(item_attrs)}><span asok-text="file.name"></span> '

    btn_attrs = dict(btn_attrs_base)
    btn_attrs["class"] = btn_class_base.strip()
    btn_attrs["type"] = "button"
    btn_attrs["asok-on:click"] = f"Asok.removeDropzoneFile($, index, $refs.input_{field.name})"
    html_out += f'<button{_render_attrs(btn_attrs)}>×</button></li>'
    html_out += "  </template>"
    html_out += "</ul>"

    html_out += "</div>"
    return html_out


def render_signature(field: Any, val: str, merged: dict[str, Any]) -> str:
    width = field.attrs.get("width", 400)
    height = field.attrs.get("height", 200)

    state = html_safe_json({"drawing": False})

    container_attrs = _extract_nested_attrs(merged, "container")
    container_class = container_attrs.get("class", "")
    container_attrs["class"] = f"asok-signature {container_class}".strip()

    canvas_attrs = _extract_nested_attrs(merged, "canvas")
    if not canvas_attrs:
        canvas_attrs = _filter_nested_attrs(merged)
    canvas_class = canvas_attrs.get("class", "")
    canvas_attrs["class"] = canvas_class.strip()
    canvas_style = canvas_attrs.get("style", "")
    if not canvas_style:
        canvas_attrs["style"] = "border:1px solid #ccc;cursor:crosshair;touch-action:none;"

    btn_attrs = _extract_nested_attrs(merged, "btn")
    btn_class = btn_attrs.get("class", "")
    btn_attrs["class"] = btn_class.strip()

    html_out = f'<div{_render_attrs(container_attrs)} asok-state="{state}">'

    # Canvas element with drawing handlers
    canvas_id = f"canvas_{field.name}"
    canvas_attrs["id"] = canvas_id
    canvas_attrs["width"] = width
    canvas_attrs["height"] = height
    canvas_attrs["asok-ref"] = f"canvas_{field.name}"

    # Handlers pour le dessin
    mousedown = f"Asok.startSignatureDrawing($event, $, $refs.canvas_{field.name})"
    mousemove = f"Asok.drawSignature($event, $, $refs.canvas_{field.name})"
    mouseup = f"Asok.stopSignatureDrawing($, $refs.canvas_{field.name}, $refs.hidden_{field.name})"
    mouseleave = "drawing=false"

    canvas_attrs["asok-on:mousedown"] = mousedown
    canvas_attrs["asok-on:mousemove"] = mousemove
    canvas_attrs["asok-on:mouseup"] = mouseup
    canvas_attrs["asok-on:mouseleave"] = mouseleave

    html_out += f"<canvas{_render_attrs(canvas_attrs)}></canvas>"

    # Clear button
    clear_handler = f"Asok.clearSignature($refs.canvas_{field.name}, $refs.hidden_{field.name})"
    btn_attrs["type"] = "button"
    btn_attrs["asok-on:click"] = clear_handler
    html_out += (
        f'<br><button{_render_attrs(btn_attrs)}>Clear</button>'
    )

    # Hidden input to store base64 signature
    html_out += f'<input type="hidden" name="{field.name}" id="{field.name}" asok-ref="hidden_{field.name}" value="{val}">'

    html_out += "</div>"
    return html_out


def render_transfer(field: Any, val: str, merged: dict[str, Any]) -> str:
    items = field.items or field.choices or []

    state = html_safe_json(
        {"available": items, "selected": [], "h_avail": [], "h_sel": []}
    )
    container_attrs = _extract_nested_attrs(merged, "container")
    container_class = container_attrs.get("class", "")
    container_attrs["class"] = f"asok-transfer {container_class}".strip()
    html_out = f'<div{_render_attrs(container_attrs)} asok-state="{state}">'

    lists_attrs = _extract_nested_attrs(merged, "lists")
    lists_class = lists_attrs.get("class", "")
    lists_attrs["class"] = f"asok-transfer-lists {lists_class}".strip()
    # Default layout style if no class provided
    if not lists_class:
        lists_attrs["style"] = "display:flex;gap:20px;"

    html_out += f"  <div{_render_attrs(lists_attrs)}>"

    list_attrs_base = _extract_nested_attrs(merged, "list")
    list_class_base = list_attrs_base.get("class", "")

    button_attrs_base = _extract_nested_attrs(merged, "button")
    button_class_base = button_attrs_base.get("class", "")

    # Available list
    avail_attrs = dict(list_attrs_base)
    avail_attrs["class"] = f"asok-transfer-avail {list_class_base}".strip()
    if not list_class_base:
        avail_attrs["style"] = "flex:1;"
    html_out += f"  <div{_render_attrs(avail_attrs)}>"
    html_out += "    <h4>Available</h4>"
    html_out += '    <select multiple style="width:100%;height:200px;" asok-on:change="Asok.updateTransferSelection($, \'h_avail\', $event)">'
    html_out += '      <template asok-for="item in available">'
    html_out += '        <option asok-bind:value="item.id || item" asok-text="item.name || item" asok-on:dblclick="Asok.moveTransferItemRight($, item)" asok-bind:style="h_avail.includes(String(item.id || item)) ? \'background-color: #e7f3ff;\' : \'\'"></option>'
    html_out += "      </template>"
    html_out += "    </select>"
    html_out += "  </div>"

    # Actions buttons
    btns_attrs = _extract_nested_attrs(merged, "actions")
    btns_class = btns_attrs.get("class", "")
    btns_attrs["class"] = f"asok-transfer-actions {btns_class}".strip()
    if not btns_class:
        btns_attrs["style"] = (
            "display:flex;flex-direction:column;justify-content:center;gap:10px;"
        )
    html_out += f"  <div{_render_attrs(btns_attrs)}>"

    right_btn = dict(button_attrs_base)
    right_btn["class"] = f"asok-transfer-btn-right {button_class_base}".strip()
    right_btn["type"] = "button"
    right_btn["asok-on:click"] = "Asok.moveTransferRight($)"
    html_out += f"    <button{_render_attrs(right_btn)}>→</button>"

    left_btn = dict(button_attrs_base)
    left_btn["class"] = f"asok-transfer-btn-left {button_class_base}".strip()
    left_btn["type"] = "button"
    left_btn["asok-on:click"] = "Asok.moveTransferLeft($)"
    html_out += f"    <button{_render_attrs(left_btn)}>←</button>"
    html_out += "  </div>"

    # Selected list
    sel_list_attrs = dict(list_attrs_base)
    sel_list_attrs["class"] = f"asok-transfer-selected {list_class_base}".strip()
    if not list_class_base:
        sel_list_attrs["style"] = "flex:1;"
    html_out += f"  <div{_render_attrs(sel_list_attrs)}>"
    html_out += "    <h4>Selected</h4>"
    html_out += '    <select multiple style="width:100%;height:200px;" asok-on:change="Asok.updateTransferSelection($, \'h_sel\', $event)">'
    html_out += '      <template asok-for="item in selected">'
    html_out += '        <option asok-bind:value="item.id || item" asok-text="item.name || item" asok-on:dblclick="Asok.moveTransferItemLeft($, item)" asok-bind:style="h_sel.includes(String(item.id || item)) ? \'background-color: #e7f3ff;\' : \'\'"></option>'
    html_out += "      </template>"
    html_out += "    </select>"
    html_out += "  </div>"

    html_out += "  </div>"

    # Hidden input to store selected IDs
    html_out += f'<input type="hidden" name="{field.name}" id="{field.name}" asok-bind:value="JSON.stringify(selected.map(i=>i.id||i))">'
    html_out += "</div>"
    return html_out


def render_treeselect(field: Any, val: str, merged: dict[str, Any]) -> str:
    items = field.items or field.choices or []

    state = html_safe_json({"tree": items, "selected": "", "expanded": []})
    container_attrs = _extract_nested_attrs(merged, "container")
    container_class = container_attrs.get("class", "")
    container_attrs["class"] = f"asok-treeselect {container_class}".strip()
    html_out = f'<div{_render_attrs(container_attrs)} asok-state="{state}">'

    tree_attrs = _extract_nested_attrs(merged, "tree")
    tree_class = tree_attrs.get("class", "")
    tree_attrs["class"] = f"asok-tree {tree_class}".strip()
    if not tree_class:
        tree_attrs["style"] = (
            "border:1px solid #ddd;padding:10px;max-height:300px;overflow-y:auto;"
        )
    html_out += f"  <div{_render_attrs(tree_attrs)}>"

    item_attrs_base = _extract_nested_attrs(merged, "item")
    item_class_base = item_attrs_base.get("class", "")

    html_out += '  <template asok-for="item in tree">'

    item_wrapper_attrs = dict(item_attrs_base)
    item_wrapper_attrs["class"] = f"asok-tree-item {item_class_base}".strip()
    if not item_class_base:
        item_wrapper_attrs["style"] = "margin:2px 0;"
    html_out += f"    <div{_render_attrs(item_wrapper_attrs)}>"

    html_out += (
        f'      <div style="display:flex;align-items:center;padding:5px;cursor:pointer;border-radius:4px;" asok-on:click="Asok.selectTreeItem($, item.id, $refs.hidden_{field.name})" asok-bind:style="selected==item.id ? \'background:#e7f3ff;color:#0056b3\' : \'\'">'
    )
    html_out += "        <span style=\"width:20px;text-align:center;cursor:pointer;user-select:none;\" asok-on:click.stop=\"Asok.toggleTreeExpansion($, item.id)\" asok-text=\"item.children && item.children.length > 0 ? (expanded.includes(item.id) ? '▾' : '▸') : '•'\"></span>"
    html_out += '        <span asok-text="item.name"></span>'
    html_out += "      </div>"
    html_out += '      <template asok-if="item.children && item.children.length > 0 && expanded.includes(item.id)">'
    html_out += '        <div style="margin-left:20px;margin-top:2px;border-left:1px solid #eee;">'
    html_out += '          <template asok-for="child in item.children">'
    html_out += (
        f'            <div style="display:flex;align-items:center;padding:4px 10px;cursor:pointer;border-radius:3px;margin:1px 0;" asok-on:click.stop="Asok.selectTreeItem($, child.id, $refs.hidden_{field.name})" asok-bind:style="selected==child.id ? \'background:#e7f3ff;color:#0056b3\' : \'\'">'
    )
    html_out += '              <span style="color:#ccc;margin-right:8px;">└</span>'
    html_out += '              <span asok-text="child.name"></span>'
    html_out += "            </div>"
    html_out += "          </template>"
    html_out += "        </div>"
    html_out += "      </template>"
    html_out += "    </div>"
    html_out += "  </template>"
    html_out += "</div>"

    html_out += f'<input type="hidden" name="{field.name}" id="{field.name}" asok-model="selected" asok-ref="hidden_{field.name}">'
    html_out += "</div>"
    return html_out
