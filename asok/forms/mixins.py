from __future__ import annotations

import enum
from typing import Any, Optional


class SchemaMixin:
    """Contains static factory methods for form field definitions."""

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
        attrs["preview"] = preview
        attrs["max_width"] = max_width
        attrs["max_height"] = max_height
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
        attrs["searchable"] = searchable
        attrs["allow_custom"] = allow_custom
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
        attrs["start_label"] = start_label
        attrs["end_label"] = end_label
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
        attrs["length"] = length
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
        attrs["max_stars"] = max_stars
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
        attrs["start_label"] = start_label
        attrs["end_label"] = end_label
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
        attrs["max_files"] = max_files
        attrs["preview"] = preview
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
        attrs["min_chars"] = min_chars
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
        attrs["default_country"] = default_country
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
        attrs["height"] = height
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
        attrs["max_files"] = max_files
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
        attrs["width"] = width
        attrs["height"] = height
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
