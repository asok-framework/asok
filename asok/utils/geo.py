from __future__ import annotations

import logging
import os
import socket
import struct
from typing import Any, Optional

logger = logging.getLogger("asok.utils.geo")


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
        except Exception as e:
            logger.warning(f"Failed to load GeoIP database: {e}", exc_info=True)

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


def iso_to_flag(iso_code: str) -> str:
    """Convert ISO alpha-2 code to emoji flag."""
    if not iso_code or len(iso_code) != 2:
        return ""
    return "".join(chr(ord(c.upper()) + 127397) for c in iso_code)


class Countries:
    """Global utility for accessing country information."""

    @staticmethod
    def all() -> list[dict[str, str]]:
        """Return a list of all countries with rich metadata."""
        return [
            {
                "iso": c[0],
                "dial_code": c[1],
                "name": c[2],
                "flag": iso_to_flag(c[0]),
                "capital": c[3],
                "continent": c[4],
                "currency": c[5],
                "languages": c[6],
            }
            for c in get_dial_codes()
        ]

    @staticmethod
    def get(iso: str) -> Optional[dict[str, str]]:
        """Get rich information for a specific country by ISO alpha-2 code."""
        iso = iso.upper()
        for c in get_dial_codes():
            if c[0] == iso:
                return {
                    "iso": c[0],
                    "dial_code": c[1],
                    "name": c[2],
                    "flag": iso_to_flag(c[0]),
                    "capital": c[3],
                    "continent": c[4],
                    "currency": c[5],
                    "languages": c[6],
                }
        return None

    @staticmethod
    def search(query: str) -> list[dict[str, str]]:
        """Search countries by name, ISO, capital or continent."""
        query = query.upper()
        results = []
        for c in get_dial_codes():
            # Match against ISO, Name, Capital or Continent
            if any(query in str(field).upper() for field in c):
                results.append(
                    {
                        "iso": c[0],
                        "dial_code": c[1],
                        "name": c[2],
                        "flag": iso_to_flag(c[0]),
                        "capital": c[3],
                        "continent": c[4],
                        "currency": c[5],
                        "languages": c[6],
                    }
                )
        return results

    @staticmethod
    def distance(
        lat1: float, lon1: float, lat2: float, lon2: float, unit: str = "km"
    ) -> float:
        """Calculate distance between two coordinates using the Haversine formula."""
        import math

        r = 6371  # Earth radius in km
        if unit.lower() == "miles":
            r = 3958.8

        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(math.radians(lat1))
            * math.cos(math.radians(lat2))
            * math.sin(dlon / 2) ** 2
        )
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return r * c

    @staticmethod
    def static_map(
        lat: float,
        lon: float,
        zoom: int = 13,
        size: str = "600x300",
        provider: str = "osm",
    ) -> str:
        """Generate a static map image URL."""
        if provider == "osm":
            # Using staticmap.openstreetmap.de or similar
            return f"https://staticmap.openstreetmap.de/staticmap.php?center={lat},{lon}&zoom={zoom}&size={size}&markers={lat},{lon},ol-marker"
        elif provider == "google":
            return f"https://maps.googleapis.com/maps/api/staticmap?center={lat},{lon}&zoom={zoom}&size={size}&markers={lat},{lon}"
        return ""

    @staticmethod
    def get_timezone(iso_code: str) -> str:
        """Estimate primary timezone for a country (rough estimation)."""
        # Mapping for major countries
        zones = {
            "FR": "Europe/Paris",
            "US": "America/New_York",
            "GB": "Europe/London",
            "DE": "Europe/Berlin",
            "JP": "Asia/Tokyo",
            "CN": "Asia/Shanghai",
            "IN": "Asia/Kolkata",
            "BR": "America/Sao_Paulo",
            "RU": "Europe/Moscow",
            "CA": "America/Toronto",
            "AU": "Australia/Sydney",
        }
        return zones.get(iso_code.upper(), "UTC")


def get_dial_codes() -> list[tuple[str, str, str, str, str, str, str]]:
    """Return a complete list of (ISO, dial_code, name, capital, continent, currency, languages)."""
    return [
        ("AF", "+93", "Afghanistan", "Kabul", "Asia", "AFN", "Pashto, Dari"),
        ("AX", "+358", "Aland Islands", "Mariehamn", "Europe", "EUR", "Swedish"),
        ("AL", "+355", "Albania", "Tirana", "Europe", "ALL", "Albanian"),
        ("DZ", "+213", "Algeria", "Algiers", "Africa", "DZD", "Arabic, Berber"),
        (
            "AS",
            "+1-684",
            "American Samoa",
            "Pago Pago",
            "Oceania",
            "USD",
            "English, Samoan",
        ),
        ("AD", "+376", "Andorra", "Andorra la Vella", "Europe", "EUR", "Catalan"),
        ("AO", "+244", "Angola", "Luanda", "Africa", "AOA", "Portuguese"),
        ("AI", "+1-264", "Anguilla", "The Valley", "North America", "XCD", "English"),
        ("AQ", "+672", "Antarctica", "None", "Antarctica", "None", "None"),
        (
            "AG",
            "+1-268",
            "Antigua and Barbuda",
            "Saint John's",
            "North America",
            "XCD",
            "English",
        ),
        ("AR", "+54", "Argentina", "Buenos Aires", "South America", "ARS", "Spanish"),
        ("AM", "+374", "Armenia", "Yerevan", "Asia", "AMD", "Armenian"),
        (
            "AW",
            "+297",
            "Aruba",
            "Oranjestad",
            "North America",
            "AWG",
            "Dutch, Papiamento",
        ),
        ("AU", "+61", "Australia", "Canberra", "Oceania", "AUD", "English"),
        ("AT", "+43", "Austria", "Vienna", "Europe", "EUR", "German"),
        ("AZ", "+994", "Azerbaijan", "Baku", "Asia", "AZN", "Azerbaijani"),
        ("BS", "+1-242", "Bahamas", "Nassau", "North America", "BSD", "English"),
        ("BH", "+973", "Bahrain", "Manama", "Asia", "BHD", "Arabic"),
        ("BD", "+880", "Bangladesh", "Dhaka", "Asia", "BDT", "Bengali"),
        ("BB", "+1-246", "Barbados", "Bridgetown", "North America", "BBD", "English"),
        ("BY", "+375", "Belarus", "Minsk", "Europe", "BYN", "Belarusian, Russian"),
        ("BE", "+32", "Belgium", "Brussels", "Europe", "EUR", "Dutch, French, German"),
        ("BZ", "+501", "Belize", "Belmopan", "North America", "BZD", "English"),
        ("BJ", "+229", "Benin", "Porto-Novo", "Africa", "XOF", "French"),
        ("BM", "+1-441", "Bermuda", "Hamilton", "North America", "BMD", "English"),
        ("BT", "+975", "Bhutan", "Thimphu", "Asia", "BTN", "Dzongkha"),
        (
            "BO",
            "+591",
            "Bolivia",
            "Sucre",
            "South America",
            "BOB",
            "Spanish, Quechua, Aymara",
        ),
        (
            "BQ",
            "+599",
            "Bonaire",
            "Kralendijk",
            "North America",
            "USD",
            "Dutch, Papiamento",
        ),
        (
            "BA",
            "+387",
            "Bosnia and Herzegovina",
            "Sarajevo",
            "Europe",
            "BAM",
            "Bosnian, Croatian, Serbian",
        ),
        ("BW", "+267", "Botswana", "Gaborone", "Africa", "BWP", "English, Setswana"),
        ("BV", "+47", "Bouvet Island", "None", "Antarctica", "NOK", "None"),
        ("BR", "+55", "Brazil", "Brasilia", "South America", "BRL", "Portuguese"),
        (
            "IO",
            "+246",
            "British Indian Ocean Territory",
            "Diego Garcia",
            "Africa",
            "USD",
            "English",
        ),
        ("BN", "+673", "Brunei", "Bandar Seri Begawan", "Asia", "BND", "Malay"),
        ("BG", "+359", "Bulgaria", "Sofia", "Europe", "BGN", "Bulgarian"),
        ("BF", "+226", "Burkina Faso", "Ouagadougou", "Africa", "XOF", "French"),
        (
            "BI",
            "+257",
            "Burundi",
            "Gitega",
            "Africa",
            "BIF",
            "Kirundi, French, English",
        ),
        ("KH", "+855", "Cambodia", "Phnom Penh", "Asia", "KHR", "Khmer"),
        ("CM", "+237", "Cameroon", "Yaounde", "Africa", "XAF", "French, English"),
        ("CA", "+1", "Canada", "Ottawa", "North America", "CAD", "English, French"),
        ("CV", "+238", "Cape Verde", "Praia", "Africa", "CVE", "Portuguese"),
        (
            "KY",
            "+1-345",
            "Cayman Islands",
            "George Town",
            "North America",
            "KYD",
            "English",
        ),
        (
            "CF",
            "+236",
            "Central African Republic",
            "Bangui",
            "Africa",
            "XAF",
            "Sango, French",
        ),
        ("TD", "+235", "Chad", "N'Djamena", "Africa", "XAF", "French, Arabic"),
        ("CL", "+56", "Chile", "Santiago", "South America", "CLP", "Spanish"),
        ("CN", "+86", "China", "Beijing", "Asia", "CNY", "Mandarin"),
        (
            "CX",
            "+61",
            "Christmas Island",
            "Flying Fish Cove",
            "Oceania",
            "AUD",
            "English",
        ),
        (
            "CC",
            "+61",
            "Cocos (Keeling) Islands",
            "West Island",
            "Oceania",
            "AUD",
            "English",
        ),
        ("CO", "+57", "Colombia", "Bogota", "South America", "COP", "Spanish"),
        (
            "KM",
            "+269",
            "Comoros",
            "Moroni",
            "Africa",
            "KMF",
            "Arabic, French, Comorian",
        ),
        ("CG", "+242", "Congo", "Brazzaville", "Africa", "XAF", "French"),
        (
            "CD",
            "+243",
            "Congo, Democratic Republic of the",
            "Kinshasa",
            "Africa",
            "CDF",
            "French",
        ),
        (
            "CK",
            "+682",
            "Cook Islands",
            "Avarua",
            "Oceania",
            "NZD",
            "English, Cook Islands Maori",
        ),
        ("CR", "+506", "Costa Rica", "San Jose", "North America", "CRC", "Spanish"),
        ("CI", "+225", "Cote d'Ivoire", "Yamoussoukro", "Africa", "XOF", "French"),
        ("HR", "+385", "Croatia", "Zagreb", "Europe", "EUR", "Croatian"),
        ("CU", "+53", "Cuba", "Havana", "North America", "CUP", "Spanish"),
        (
            "CW",
            "+599",
            "Curacao",
            "Willemstad",
            "North America",
            "ANG",
            "Dutch, Papiamento",
        ),
        ("CY", "+357", "Cyprus", "Nicosia", "Europe", "EUR", "Greek, Turkish"),
        ("CZ", "+420", "Czech Republic", "Prague", "Europe", "CZK", "Czech"),
        ("DK", "+45", "Denmark", "Copenhagen", "Europe", "DKK", "Danish"),
        ("DJ", "+253", "Djibouti", "Djibouti", "Africa", "DJF", "Arabic, French"),
        ("DM", "+1-767", "Dominica", "Roseau", "North America", "XCD", "English"),
        (
            "DO",
            "+1",
            "Dominican Republic",
            "Santo Domingo",
            "North America",
            "DOP",
            "Spanish",
        ),
        ("EC", "+593", "Ecuador", "Quito", "South America", "USD", "Spanish"),
        ("EG", "+20", "Egypt", "Cairo", "Africa", "EGP", "Arabic"),
        (
            "SV",
            "+503",
            "El Salvador",
            "San Salvador",
            "North America",
            "USD",
            "Spanish",
        ),
        (
            "GQ",
            "+240",
            "Equatorial Guinea",
            "Malabo",
            "Africa",
            "XAF",
            "Spanish, French, Portuguese",
        ),
        (
            "ER",
            "+291",
            "Eritrea",
            "Asmara",
            "Africa",
            "ERN",
            "Tigrinya, Arabic, English",
        ),
        ("EE", "+372", "Estonia", "Tallinn", "Europe", "EUR", "Estonian"),
        ("ET", "+251", "Ethiopia", "Addis Ababa", "Africa", "ETB", "Amharic"),
        (
            "FK",
            "+500",
            "Falkland Islands",
            "Stanley",
            "South America",
            "FKP",
            "English",
        ),
        ("FO", "+298", "Faroe Islands", "Torshavn", "Europe", "DKK", "Faroese, Danish"),
        ("FJ", "+679", "Fiji", "Suva", "Oceania", "FJD", "English, Fijian, Hindi"),
        ("FI", "+358", "Finland", "Helsinki", "Europe", "EUR", "Finnish, Swedish"),
        ("FR", "+33", "France", "Paris", "Europe", "EUR", "French"),
        ("GF", "+594", "French Guiana", "Cayenne", "South America", "EUR", "French"),
        ("PF", "+689", "French Polynesia", "Papeete", "Oceania", "XPF", "French"),
        (
            "TF",
            "+262",
            "French Southern Territories",
            "Port-aux-Francais",
            "Antarctica",
            "EUR",
            "French",
        ),
        ("GA", "+241", "Gabon", "Libreville", "Africa", "XAF", "French"),
        ("GM", "+220", "Gambia", "Banjul", "Africa", "GMD", "English"),
        ("GE", "+995", "Georgia", "Tbilisi", "Asia", "GEL", "Georgian"),
        ("DE", "+49", "Germany", "Berlin", "Europe", "EUR", "German"),
        ("GH", "+233", "Ghana", "Accra", "Africa", "GHS", "English"),
        ("GI", "+350", "Gibraltar", "Gibraltar", "Europe", "GIP", "English"),
        ("GR", "+30", "Greece", "Athens", "Europe", "EUR", "Greek"),
        ("GL", "+299", "Greenland", "Nuuk", "North America", "DKK", "Greenlandic"),
        (
            "GD",
            "+1-473",
            "Grenada",
            "Saint George's",
            "North America",
            "XCD",
            "English",
        ),
        ("GP", "+590", "Guadeloupe", "Basse-Terre", "North America", "EUR", "French"),
        ("GU", "+1-671", "Guam", "Hagatna", "Oceania", "USD", "English, Chamorro"),
        (
            "GT",
            "+502",
            "Guatemala",
            "Guatemala City",
            "North America",
            "GTQ",
            "Spanish",
        ),
        ("GG", "+44", "Guernsey", "Saint Peter Port", "Europe", "GBP", "English"),
        ("GN", "+224", "Guinea", "Conakry", "Africa", "GNF", "French"),
        ("GW", "+245", "Guinea-Bissau", "Bissau", "Africa", "XOF", "Portuguese"),
        ("GY", "+592", "Guyana", "Georgetown", "South America", "GYD", "English"),
        (
            "HT",
            "+509",
            "Haiti",
            "Port-au-Prince",
            "North America",
            "HTG",
            "French, Haitian Creole",
        ),
        (
            "HM",
            "+672",
            "Heard and McDonald Islands",
            "None",
            "Antarctica",
            "AUD",
            "None",
        ),
        (
            "VA",
            "+379",
            "Vatican City",
            "Vatican City",
            "Europe",
            "EUR",
            "Italian, Latin",
        ),
        ("HN", "+504", "Honduras", "Tegucigalpa", "North America", "HNL", "Spanish"),
        ("HK", "+852", "Hong Kong", "Hong Kong", "Asia", "HKD", "Chinese, English"),
        ("HU", "+36", "Hungary", "Budapest", "Europe", "HUF", "Hungarian"),
        ("IS", "+354", "Iceland", "Reykjavik", "Europe", "ISK", "Icelandic"),
        ("IN", "+91", "India", "New Delhi", "Asia", "INR", "Hindi, English"),
        ("ID", "+62", "Indonesia", "Jakarta", "Asia", "IDR", "Indonesian"),
        ("IR", "+98", "Iran", "Tehran", "Asia", "IRR", "Persian"),
        ("IQ", "+964", "Iraq", "Baghdad", "Asia", "IQD", "Arabic, Kurdish"),
        ("IE", "+353", "Ireland", "Dublin", "Europe", "EUR", "English, Irish"),
        ("IM", "+44", "Isle of Man", "Douglas", "Europe", "GBP", "English, Manx"),
        ("IL", "+972", "Israel", "Jerusalem", "Asia", "ILS", "Hebrew"),
        ("IT", "+39", "Italy", "Rome", "Europe", "EUR", "Italian"),
        ("JM", "+1", "Jamaica", "Kingston", "North America", "JMD", "English"),
        ("JP", "+81", "Japan", "Tokyo", "Asia", "JPY", "Japanese"),
        ("JE", "+44", "Jersey", "Saint Helier", "Europe", "GBP", "English, French"),
        ("JO", "+962", "Jordan", "Amman", "Asia", "JOD", "Arabic"),
        ("KZ", "+7", "Kazakhstan", "Astana", "Asia", "KZT", "Kazakh, Russian"),
        ("KE", "+254", "Kenya", "Nairobi", "Africa", "KES", "English, Swahili"),
        (
            "KI",
            "+686",
            "Kiribati",
            "South Tarawa",
            "Oceania",
            "AUD",
            "English, Gilbertese",
        ),
        ("KP", "+850", "North Korea", "Pyongyang", "Asia", "KPW", "Korean"),
        ("KR", "+82", "South Korea", "Seoul", "Asia", "KRW", "Korean"),
        ("KW", "+965", "Kuwait", "Kuwait City", "Asia", "KWD", "Arabic"),
        ("KG", "+996", "Kyrgyzstan", "Bishkek", "Asia", "KGS", "Kyrgyz, Russian"),
        ("LA", "+856", "Laos", "Vientiane", "Asia", "LAK", "Lao"),
        ("LV", "+371", "Latvia", "Riga", "Europe", "EUR", "Latvian"),
        ("LB", "+961", "Lebanon", "Beirut", "Asia", "LBP", "Arabic, French"),
        ("LS", "+266", "Lesotho", "Maseru", "Africa", "LSL", "English, Sesotho"),
        ("LR", "+231", "Liberia", "Monrovia", "Africa", "LRD", "English"),
        ("LY", "+218", "Libya", "Tripoli", "Africa", "LYD", "Arabic"),
        ("LI", "+423", "Liechtenstein", "Vaduz", "Europe", "CHF", "German"),
        ("LT", "+370", "Lithuania", "Vilnius", "Europe", "EUR", "Lithuanian"),
        (
            "LU",
            "+352",
            "Luxembourg",
            "Luxembourg",
            "Europe",
            "EUR",
            "French, German, Luxembourgish",
        ),
        ("MO", "+853", "Macao", "Macao", "Asia", "MOP", "Chinese, Portuguese"),
        ("MK", "+389", "North Macedonia", "Skopje", "Europe", "MKD", "Macedonian"),
        (
            "MG",
            "+261",
            "Madagascar",
            "Antananarivo",
            "Africa",
            "MGA",
            "Malagasy, French",
        ),
        ("MW", "+265", "Malawi", "Lilongwe", "Africa", "MWK", "English, Chichewa"),
        ("MY", "+60", "Malaysia", "Kuala Lumpur", "Asia", "MYR", "Malay"),
        ("MV", "+960", "Maldives", "Male", "Asia", "MVR", "Dhivehi"),
        ("ML", "+223", "Mali", "Bamako", "Africa", "XOF", "French"),
        ("MT", "+356", "Malta", "Valletta", "Europe", "EUR", "Maltese, English"),
        (
            "MH",
            "+692",
            "Marshall Islands",
            "Majuro",
            "Oceania",
            "USD",
            "English, Marshallese",
        ),
        (
            "MQ",
            "+596",
            "Martinique",
            "Fort-de-France",
            "North America",
            "EUR",
            "French",
        ),
        ("MR", "+222", "Mauritania", "Nouakchott", "Africa", "MRU", "Arabic"),
        ("MU", "+230", "Mauritius", "Port Louis", "Africa", "MUR", "English"),
        ("YT", "+262", "Mayotte", "Mamoudzou", "Africa", "EUR", "French"),
        ("MX", "+52", "Mexico", "Mexico City", "North America", "MXN", "Spanish"),
        ("FM", "+691", "Micronesia", "Palikir", "Oceania", "USD", "English"),
        ("MD", "+373", "Moldova", "Chisinau", "Europe", "MDL", "Romanian"),
        ("MC", "+377", "Monaco", "Monaco", "Europe", "EUR", "French"),
        ("MN", "+976", "Mongolia", "Ulaanbaatar", "Asia", "MNT", "Mongolian"),
        ("ME", "+382", "Montenegro", "Podgorica", "Europe", "EUR", "Montenegrin"),
        ("MS", "+1-664", "Montserrat", "Plymouth", "North America", "XCD", "English"),
        ("MA", "+212", "Morocco", "Rabat", "Africa", "MAD", "Arabic, Berber"),
        ("MZ", "+258", "Mozambique", "Maputo", "Africa", "MZN", "Portuguese"),
        ("MM", "+95", "Myanmar", "Naypyidaw", "Asia", "MMK", "Burmese"),
        (
            "NA",
            "+264",
            "Namibia",
            "Windhoek",
            "Africa",
            "NAD, ZAR",
            "English, Afrikaans, German",
        ),
        ("NR", "+674", "Nauru", "Yaren", "Oceania", "AUD", "Nauruan, English"),
        ("NP", "+977", "Nepal", "Kathmandu", "Asia", "NPR", "Nepali"),
        ("NL", "+31", "Netherlands", "Amsterdam", "Europe", "EUR", "Dutch"),
        ("NC", "+687", "New Caledonia", "Noumea", "Oceania", "XPF", "French"),
        ("NZ", "+64", "New Zealand", "Wellington", "Oceania", "NZD", "English, Maori"),
        ("NI", "+505", "Nicaragua", "Managua", "North America", "NIO", "Spanish"),
        ("NE", "+227", "Niger", "Niamey", "Africa", "XOF", "French"),
        ("NG", "+234", "Nigeria", "Abuja", "Africa", "NGN", "English"),
        ("NU", "+683", "Niue", "Alofi", "Oceania", "NZD", "Niuean, English"),
        (
            "NF",
            "+672",
            "Norfolk Island",
            "Kingston",
            "Oceania",
            "AUD",
            "English, Norfuk",
        ),
        (
            "MP",
            "+1-670",
            "Northern Mariana Islands",
            "Saipan",
            "Oceania",
            "USD",
            "English, Chamorro",
        ),
        ("NO", "+47", "Norway", "Oslo", "Europe", "NOK", "Norwegian"),
        ("OM", "+968", "Oman", "Muscat", "Asia", "OMR", "Arabic"),
        ("PK", "+92", "Pakistan", "Islamabad", "Asia", "PKR", "Urdu, English"),
        ("PW", "+680", "Palau", "Ngerulmud", "Oceania", "USD", "Palauan, English"),
        ("PS", "+970", "Palestine", "East Jerusalem", "Asia", "None", "Arabic"),
        ("PA", "+507", "Panama", "Panama City", "North America", "PAB, USD", "Spanish"),
        (
            "PG",
            "+675",
            "Papua New Guinea",
            "Port Moresby",
            "Oceania",
            "PGK",
            "English, Tok Pisin, Hiri Motu",
        ),
        (
            "PY",
            "+595",
            "Paraguay",
            "Asuncion",
            "South America",
            "PYG",
            "Spanish, Guarani",
        ),
        ("PE", "+51", "Peru", "Lima", "South America", "PEN", "Spanish"),
        ("PH", "+63", "Philippines", "Manila", "Asia", "PHP", "Filipino, English"),
        ("PN", "+870", "Pitcairn", "Adamstown", "Oceania", "NZD", "English, Pitkern"),
        ("PL", "+48", "Poland", "Warsaw", "Europe", "PLN", "Polish"),
        ("PT", "+351", "Portugal", "Lisbon", "Europe", "EUR", "Portuguese"),
        (
            "PR",
            "+1-787",
            "Puerto Rico",
            "San Juan",
            "North America",
            "USD",
            "Spanish, English",
        ),
        ("QA", "+974", "Qatar", "Doha", "Asia", "QAR", "Arabic"),
        ("RE", "+262", "Reunion", "Saint-Denis", "Africa", "EUR", "French"),
        ("RO", "+40", "Romania", "Bucharest", "Europe", "RON", "Romanian"),
        ("RU", "+7", "Russia", "Moscow", "Europe/Asia", "RUB", "Russian"),
        (
            "RW",
            "+250",
            "Rwanda",
            "Kigali",
            "Africa",
            "RWF",
            "Kinyarwanda, French, English",
        ),
        (
            "BL",
            "+590",
            "Saint Barthelemy",
            "Gustavia",
            "North America",
            "EUR",
            "French",
        ),
        ("SH", "+290", "Saint Helena", "Jamestown", "Africa", "SHP", "English"),
        (
            "KN",
            "+1-869",
            "Saint Kitts and Nevis",
            "Basseterre",
            "North America",
            "XCD",
            "English",
        ),
        ("LC", "+1-758", "Saint Lucia", "Castries", "North America", "XCD", "English"),
        ("MF", "+590", "Saint Martin", "Marigot", "North America", "EUR", "French"),
        (
            "PM",
            "+508",
            "Saint Pierre and Miquelon",
            "Saint-Pierre",
            "North America",
            "EUR",
            "French",
        ),
        (
            "VC",
            "+1-784",
            "Saint Vincent and the Grenadines",
            "Kingstown",
            "North America",
            "XCD",
            "English",
        ),
        ("WS", "+685", "Samoa", "Apia", "Oceania", "WST", "Samoan, English"),
        ("SM", "+378", "San Marino", "San Marino", "Europe", "EUR", "Italian"),
        (
            "ST",
            "+239",
            "Sao Tome and Principe",
            "Sao Tome",
            "Africa",
            "STN",
            "Portuguese",
        ),
        ("SA", "+966", "Saudi Arabia", "Riyadh", "Asia", "SAR", "Arabic"),
        ("SN", "+221", "Senegal", "Dakar", "Africa", "XOF", "French"),
        ("RS", "+381", "Serbia", "Belgrade", "Europe", "RSD", "Serbian"),
        (
            "SC",
            "+248",
            "Seychelles",
            "Victoria",
            "Africa",
            "SCR",
            "Seychellois Creole, English, French",
        ),
        ("SL", "+232", "Sierra Leone", "Freetown", "Africa", "SLL", "English"),
        (
            "SG",
            "+65",
            "Singapore",
            "Singapore",
            "Asia",
            "SGD",
            "English, Malay, Mandarin, Tamil",
        ),
        (
            "SX",
            "+1-721",
            "Sint Maarten",
            "Philipsburg",
            "North America",
            "ANG",
            "Dutch, English",
        ),
        ("SK", "+421", "Slovakia", "Bratislava", "Europe", "EUR", "Slovak"),
        ("SI", "+386", "Slovenia", "Ljubljana", "Europe", "EUR", "Slovenian"),
        ("SB", "+677", "Solomon Islands", "Honiara", "Oceania", "SBD", "English"),
        ("SO", "+252", "Somalia", "Mogadishu", "Africa", "SOS", "Somali, Arabic"),
        (
            "ZA",
            "+27",
            "South Africa",
            "Pretoria",
            "Africa",
            "ZAR",
            "Zulu, Xhosa, Afrikaans, English...",
        ),
        (
            "GS",
            "+500",
            "South Georgia",
            "King Edward Point",
            "Antarctica",
            "GBP",
            "English",
        ),
        ("SS", "+211", "South Sudan", "Juba", "Africa", "SSP", "English"),
        ("ES", "+34", "Spain", "Madrid", "Europe", "EUR", "Spanish"),
        ("LK", "+94", "Sri Lanka", "Colombo", "Asia", "LKR", "Sinhala, Tamil"),
        ("SD", "+249", "Sudan", "Khartoum", "Africa", "SDG", "Arabic, English"),
        ("SR", "+597", "Suriname", "Paramaribo", "South America", "SRD", "Dutch"),
        (
            "SJ",
            "+47",
            "Svalbard and Jan Mayen",
            "Longyearbyen",
            "Europe",
            "NOK",
            "Norwegian",
        ),
        ("SZ", "+268", "Eswatini", "Mbabane", "Africa", "SZL", "English, Swazi"),
        ("SE", "+46", "Sweden", "Stockholm", "Europe", "SEK", "Swedish"),
        (
            "CH",
            "+41",
            "Switzerland",
            "Bern",
            "Europe",
            "CHF",
            "German, French, Italian, Romansh",
        ),
        ("SY", "+963", "Syria", "Damascus", "Asia", "SYP", "Arabic"),
        ("TW", "+886", "Taiwan", "Taipei", "Asia", "TWD", "Mandarin"),
        ("TJ", "+992", "Tajikistan", "Dushanbe", "Asia", "TJS", "Tajik"),
        ("TZ", "+255", "Tanzania", "Dodoma", "Africa", "TZS", "Swahili, English"),
        ("TH", "+66", "Thailand", "Bangkok", "Asia", "THB", "Thai"),
        ("TL", "+670", "Timor-Leste", "Dili", "Asia", "USD", "Tetum, Portuguese"),
        ("TG", "+228", "Togo", "Lome", "Africa", "XOF", "French"),
        ("TK", "+690", "Tokelau", "None", "Oceania", "NZD", "Tokelauan, English"),
        ("TO", "+676", "Tonga", "Nuku'alofa", "Oceania", "TOP", "Tongan, English"),
        (
            "TT",
            "+1-868",
            "Trinidad and Tobago",
            "Port of Spain",
            "North America",
            "TTD",
            "English",
        ),
        ("TN", "+216", "Tunisia", "Tunis", "Africa", "TND", "Arabic"),
        ("TR", "+90", "Turkey", "Ankara", "Asia/Europe", "TRY", "Turkish"),
        ("TM", "+993", "Turkmenistan", "Ashgabat", "Asia", "TMT", "Turkmen"),
        (
            "TC",
            "+1-649",
            "Turks and Caicos Islands",
            "Cockburn Town",
            "North America",
            "USD",
            "English",
        ),
        ("TV", "+688", "Tuvalu", "Funafuti", "Oceania", "AUD", "Tuvaluan, English"),
        ("UG", "+256", "Uganda", "Kampala", "Africa", "UGX", "English, Swahili"),
        ("UA", "+380", "Ukraine", "Kyiv", "Europe", "UAH", "Ukrainian"),
        ("AE", "+971", "United Arab Emirates", "Abu Dhabi", "Asia", "AED", "Arabic"),
        ("GB", "+44", "United Kingdom", "London", "Europe", "GBP", "English"),
        (
            "US",
            "+1",
            "United States",
            "Washington, D.C.",
            "North America",
            "USD",
            "English",
        ),
        (
            "UM",
            "+1",
            "United States Minor Outlying Islands",
            "None",
            "Oceania",
            "USD",
            "English",
        ),
        ("UY", "+598", "Uruguay", "Montevideo", "South America", "UYU", "Spanish"),
        ("UZ", "+998", "Uzbekistan", "Tashkent", "Asia", "UZS", "Uzbek"),
        (
            "VU",
            "+678",
            "Vanuatu",
            "Port Vila",
            "Oceania",
            "VUV",
            "Bislama, English, French",
        ),
        ("VE", "+58", "Venezuela", "Caracas", "South America", "VES", "Spanish"),
        ("VN", "+84", "Vietnam", "Hanoi", "Asia", "VND", "Vietnamese"),
        (
            "VG",
            "+1-284",
            "British Virgin Islands",
            "Road Town",
            "North America",
            "USD",
            "English",
        ),
        (
            "VI",
            "+1-340",
            "U.S. Virgin Islands",
            "Charlotte Amalie",
            "North America",
            "USD",
            "English",
        ),
        ("WF", "+681", "Wallis and Futuna", "Mata-Utu", "Oceania", "XPF", "French"),
        (
            "EH",
            "+212",
            "Western Sahara",
            "Laayoune",
            "Africa",
            "MAD",
            "Arabic, Berber, Spanish",
        ),
        ("YE", "+967", "Yemen", "Sanaa", "Asia", "YER", "Arabic"),
        ("ZM", "+260", "Zambia", "Lusaka", "Africa", "ZMW", "English"),
        (
            "ZW",
            "+263",
            "Zimbabwe",
            "Harare",
            "Africa",
            "ZWL",
            "English, Shona, Ndebele",
        ),
    ]
