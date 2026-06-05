"""FRONTEND_AUDIT #1: HTMX fragment vs full-page rendering.

Pages extend _layout.html, which serves the full document (base.html) on a normal
load and only the content block (_bare.html) when the request carries HX-Request.
This prevents a whole document (navbar/footer) being injected into #main-content
on HTMX navigation.
"""

import pytest


@pytest.mark.parametrize("path", ["/costs/calculator", "/stores", "/products"])
def test_full_load_is_complete_document(test_client, path):
    html = test_client.get(path).text
    assert "<!DOCTYPE" in html
    assert "<nav" in html            # navbar present
    assert 'id="main-content"' in html


@pytest.mark.parametrize("path", ["/costs/calculator", "/stores", "/products"])
def test_htmx_request_returns_bare_fragment(test_client, path):
    html = test_client.get(path, headers={"HX-Request": "true"}).text
    assert "<!DOCTYPE" not in html   # no document shell
    assert "<nav" not in html        # no duplicated navbar
    assert 'id="main-content"' not in html  # not the base wrapper
