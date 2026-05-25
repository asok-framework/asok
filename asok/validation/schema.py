from __future__ import annotations

import datetime
from typing import Any, Optional, Union

from ..context import request_var
from ..orm import Field


class SchemaMeta(type):
    """Metaclass for all Asok Schemas.

    Automatically discovers and isolates Field definitions from class
    attributes.
    """

    def __new__(mcs, name, bases, attrs):
        if name == "Schema":
            return super().__new__(mcs, name, bases, attrs)
        fields = {k: v for k, v in attrs.items() if isinstance(v, Field)}
        attrs["_fields"] = fields
        for k in fields:
            attrs.pop(k)
        return super().__new__(mcs, name, bases, attrs)


class Schema(metaclass=SchemaMeta):
    """Base class for defining structured data schemas for serialization and

    deserialization.
    """

    def __init__(self, many: bool = False, request: Optional[Any] = None):
        """Initialize the schema.

        Args:
            many: If True, the schema expects a list of objects/dicts.
            request: Optional request context for generating absolute URLs.
                     If not provided, it will attempt to fetch from global
                     context.
        """
        self.many = many
        self._request = request

    @property
    def request(self) -> Optional[Any]:
        """Return the current request context."""
        if self._request:
            return self._request
        return request_var.get()

    def dump(
        self, obj: Union[Any, list[Any]]
    ) -> Union[dict[str, Any], list[dict[str, Any]]]:
        """Serialize an object or list of objects into a dictionary

        representation.
        """
        if self.many:
            return [self._serialize(item) for item in obj]
        return self._serialize(obj)

    def _serialize(self, obj: Any) -> dict[str, Any]:
        """Perform recursive serialization on a single object instance."""
        data = {}
        for field_name in self._fields:
            if hasattr(obj, field_name):
                value = getattr(obj, field_name)
            elif isinstance(obj, dict):
                value = obj.get(field_name)
            else:
                value = None

            if isinstance(value, (datetime.date, datetime.datetime)):
                data[field_name] = value.isoformat()
            else:
                data[field_name] = value
        return data

    def load(
        self, data: Union[dict[str, Any], list[dict[str, Any]]]
    ) -> Union[dict[str, Any], list[dict[str, Any]]]:
        """Deserialize external data into a clean dictionary or list of

        dictionaries.
        """
        if self.many:
            return [self._deserialize(item) for item in data]
        return self._deserialize(data)

    def _deserialize(self, data: dict[str, Any]) -> dict[str, Any]:
        """Clean and filter external data based on schema fields."""
        clean_data = {}
        for field_name in self._fields:
            clean_data[field_name] = data.get(field_name)
        return clean_data
