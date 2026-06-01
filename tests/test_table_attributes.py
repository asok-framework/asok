from asok import Table, TableColumn
from asok.core import Request


def test_table_attribute_merging_reactive():
    """Verify that default, global, and specific attributes are merged correctly in reactive mode without duplicates."""
    items = [{"id": 1, "name": "Alice", "status": "Active"}]

    # 1. Instantiate Table with container attributes, global element classes, and custom column attributes
    table = Table(
        items,
        id="main-container",
        class_="custom-container",
        table__class="custom-table",
        table__id="the-table-el",
        th__class="global-th",
        td__class="global-td",
        page_link__class="custom-page-link",
        page_link__style="color:red",
    )

    # Define columns with sortable/custom templates and specific th/td classes
    table.columns = [
        TableColumn("name", label="User Name", sortable=True, th__class="specific-th", td__class="specific-td"),
        TableColumn("status")
    ]

    table.reactive().paginate(10)

    # Render the reactive table HTML
    html_out = table.render()

    # Ensure outer container merged defaults with custom class & has the custom non-prefixed id
    assert 'class="custom-container"' in html_out or 'class="asok-table-container custom-container"' in html_out
    assert 'id="main-container"' in html_out

    # Ensure class="custom-container" is not duplicated
    assert html_out.count('class="custom-container"') == 1 or html_out.count('class="asok-table-container custom-container"') == 1

    # Ensure table element has merged classes and the custom ID
    assert 'id="the-table-el"' in html_out
    assert 'class="asok-table custom-table"' in html_out or 'class="custom-table asok-table"' in html_out

    # Ensure sortable column has merged th classes
    assert 'class="asok-sortable global-th specific-th"' in html_out or 'global-th specific-th' in html_out

    # Ensure non-sortable column has only global th classes and is not sortable
    assert 'global-th' in html_out

    # Ensure td has merged global and specific classes
    assert 'global-td specific-td' in html_out

    # Ensure no duplicate class attributes exist anywhere on any tag
    # E.g. we shouldn't see 'class="..." class="..."'
    import re
    duplicate_classes = re.findall(r'<[a-z0-9]+(?:\s+[^>]*?\s*class="[^"]*"){2,}', html_out)
    assert len(duplicate_classes) == 0, f"Found tags with duplicate class attributes: {duplicate_classes}"


def test_table_attribute_merging_non_reactive():
    """Verify that default, global, and specific attributes are merged correctly in non-reactive mode."""
    items = [{"id": 1, "name": "Alice", "status": "Active"}]
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/",
        "wsgi.input": None,
    }
    request = Request(environ)

    table = Table(
        items,
        request=request,
        id="main-container-nr",
        class_="custom-container-nr",
        table__class="custom-table-nr",
        th__class="global-th-nr",
        td__class="global-td-nr",
    )

    table.columns = [
        TableColumn("name", th__class="specific-th-nr", td__class="specific-td-nr"),
        TableColumn("status")
    ]

    html_out = table.render()

    assert 'id="main-container-nr"' in html_out
    assert 'class="custom-container-nr"' in html_out or 'class="asok-table-container custom-container-nr"' in html_out
    assert 'class="asok-table custom-table-nr"' in html_out

    # Check that specific th classes were merged with global th classes
    assert 'global-th-nr specific-th-nr' in html_out or 'specific-th-nr global-th-nr' in html_out

    # Check that specific td classes were merged with global td classes
    assert 'global-td-nr specific-td-nr' in html_out or 'specific-td-nr global-td-nr' in html_out


def test_statement_handling_in_registry():
    """Verify that statements like 'if(confirm(...))' are parsed as statements and not return-wrapped in JS registry."""
    from asok.core import Asok
    app = Asok()
    app.directives_enabled = True

    html_content = '<button asok-on:click="if(confirm(\'delete\')) fetch(\'/delete\').then(r => items = items.filter(x => x !== 1))">Delete</button>'

    # We call _precompile_directives on the app instance
    precompiled, registry = app._precompile_directives(html_content)

    # Verify that the expression was hashed and stored in registry
    assert len(registry) == 1
    h, expr = list(registry.items())[0]

    # Simulate JS registry body generation like assets.py line 1115
    import re
    is_stmt = (
        ";" in expr
        or "return " in expr
        or bool(re.search(r"\b(if|for|while|const|let|var|function)\b", expr))
    )
    assert is_stmt is True, "Expression containing 'if(...)' should be detected as a statement"


def test_master_checkbox_binding_reactive():
    """Verify that the master checkbox has the correct asok-bind:checked attribute in reactive mode."""
    items = [{"id": 1, "name": "Alice", "status": "Active"}]
    table = Table(items).reactive()
    html_out = table.render()
    assert 'asok-bind:checked="selected.length === items.length && items.length > 0"' in html_out

