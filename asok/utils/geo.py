from __future__ import annotations

import os
import socket
import struct
from typing import Any, Optional


class IPLocation:
    """Zero-dependency IP-to-Location lookup engine.

    Uses binary search for high performance on local CSV databases.
    Supported format: .asok/geo.csv (ip_from_int, ip_to_int, city, country, lat, lon)
    """

    _instance: Optional[IPLocation] = None
    _data: list[tuple[int, int, dict[str, Any]]] = []
    _loaded: bool = False

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.path.join(os.getcwd(), ".asok", "geo.csv")

    @classmethod
    def get_instance(cls) -> IPLocation:
        if cls._instance is None:
            cls._instance = IPLocation()
        return cls._instance

    def _ip_to_int(self, ip: str) -> int:
        """Convert IPv4 string to integer."""
        try:
            return struct.unpack("!I", socket.inet_aton(ip))[0]
        except (OSError, socket.error):
            return 0

    def _load_data(self) -> None:
        """Load and parse the local CSV database if it exists."""
        if self._loaded:
            return

        if not os.path.exists(self.db_path):
            self._loaded = True
            return

        try:
            with open(self.db_path, "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.strip().split(",")
                    if len(parts) >= 6:
                        try:
                            start = int(parts[0])
                            end = int(parts[1])
                            info = {
                                "city": parts[2],
                                "country": parts[3],
                                "lat": float(parts[4]),
                                "lon": float(parts[5]),
                            }
                            self._data.append((start, end, info))
                        except ValueError:
                            continue
            # Ensure data is sorted for binary search
            self._data.sort(key=lambda x: x[0])
        except Exception:
            pass

        self._loaded = True

    def lookup(self, ip: str) -> dict[str, Any]:
        """Perform a binary search for the given IP address."""
        self._load_data()

        if not self._data:
            return {"city": "Unknown", "country": "Unknown", "lat": 0.0, "lon": 0.0}

        ip_int = self._ip_to_int(ip)
        if ip_int == 0:
            return {"city": "Unknown", "country": "Unknown", "lat": 0.0, "lon": 0.0}

        low = 0
        high = len(self._data) - 1

        while low <= high:
            mid = (low + high) // 2
            start, end, info = self._data[mid]

            if start <= ip_int <= end:
                return info
            elif ip_int < start:
                high = mid - 1
            else:
                low = mid + 1

        return {"city": "Unknown", "country": "Unknown", "lat": 0.0, "lon": 0.0}
