"""
Tests pour les nouvelles règles de validation: month et base64
"""

from asok.validation import Validator


def test_month_valid():
    """Test que les formats de mois valides passent"""
    v = Validator({"birth_month": "2024-01"})
    assert v.rule("birth_month", "month")
    assert len(v.errors) == 0

    v = Validator({"birth_month": "2024-12"})
    assert v.rule("birth_month", "month")

    v = Validator({"birth_month": "1999-06"})
    assert v.rule("birth_month", "month")


def test_month_invalid_format():
    """Test que les formats de mois invalides échouent"""
    # Mois invalide (13)
    v = Validator({"birth_month": "2024-13"})
    assert not v.rule("birth_month", "month")
    assert "birth_month" in v.errors

    # Mois invalide (00)
    v = Validator({"birth_month": "2024-00"})
    assert not v.rule("birth_month", "month")

    # Format court
    v = Validator({"birth_month": "24-01"})
    assert not v.rule("birth_month", "month")

    # Séparateur incorrect
    v = Validator({"birth_month": "2024/01"})
    assert not v.rule("birth_month", "month")

    # Texte
    v = Validator({"birth_month": "invalid"})
    assert not v.rule("birth_month", "month")


def test_month_empty():
    """Test que les valeurs vides passent (sauf si required)"""
    v = Validator({"birth_month": ""})
    assert v.rule("birth_month", "month")

    v = Validator({})
    assert v.rule("birth_month", "month")


def test_month_required():
    """Test la combinaison month + required"""
    v = Validator({"birth_month": ""})
    assert not v.rule("birth_month", "required|month")
    assert "birth_month" in v.errors
    assert "required" in v.errors["birth_month"].lower()


def test_base64_data_uri():
    """Test que les data URIs base64 valides passent"""
    # Image PNG
    v = Validator({"avatar": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAUA="})
    assert v.rule("avatar", "base64")
    assert len(v.errors) == 0

    # Image JPEG
    v = Validator({"avatar": "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD="})
    assert v.rule("avatar", "base64")


def test_base64_plain():
    """Test que les chaînes base64 simples passent"""
    v = Validator({"signature": "iVBORw0KGgoAAAANSUhEUgAAAAUA="})
    assert v.rule("signature", "base64")


def test_base64_invalid():
    """Test que les valeurs non-base64 échouent"""
    # Caractères invalides
    v = Validator({"avatar": "data:image/png;base64,invalid@#$%"})
    assert not v.rule("avatar", "base64")
    assert "avatar" in v.errors

    # Format data URI incorrect
    v = Validator({"avatar": "data:image/png,notbase64"})
    assert not v.rule("avatar", "base64")

    # Texte simple
    v = Validator({"avatar": "just some text"})
    assert not v.rule("avatar", "base64")


def test_base64_empty():
    """Test que les valeurs vides passent (sauf si required)"""
    v = Validator({"avatar": ""})
    assert v.rule("avatar", "base64")

    v = Validator({})
    assert v.rule("avatar", "base64")


def test_base64_required():
    """Test la combinaison base64 + required"""
    v = Validator({"avatar": ""})
    assert not v.rule("avatar", "required|base64")
    assert "avatar" in v.errors
    assert "required" in v.errors["avatar"].lower()


def test_month_custom_message():
    """Test les messages d'erreur personnalisés pour month"""
    v = Validator({"birth_month": "invalid"})
    v.rule("birth_month", "month", {"month": "Le mois doit être au format YYYY-MM"})
    assert v.errors["birth_month"] == "Le mois doit être au format YYYY-MM"


def test_base64_custom_message():
    """Test les messages d'erreur personnalisés pour base64"""
    v = Validator({"avatar": "invalid@#$"})
    v.rule("avatar", "base64", {"base64": "Image base64 invalide"})
    assert v.errors["avatar"] == "Image base64 invalide"


def test_combined_rules_with_new_validators():
    """Test la combinaison de plusieurs règles incluant les nouvelles"""
    # month + required
    v = Validator({"month": "2024-05"})
    assert v.rule("month", "required|month")

    # base64 + required
    v = Validator({"sig": "data:image/png;base64,abc123=="})
    assert v.rule("sig", "required|base64")

    # Échec combiné
    v = Validator({"month": "", "sig": ""})
    assert not v.rules(
        {
            "month": "required|month",
            "sig": "required|base64",
        }
    )
    assert "month" in v.errors
    assert "sig" in v.errors
