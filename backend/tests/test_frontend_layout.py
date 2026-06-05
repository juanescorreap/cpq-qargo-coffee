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


# FRONTEND_AUDIT #3 — global HTMX error surfacing.

def test_app_js_loaded_on_full_page(test_client):
    html = test_client.get("/costs/calculator").text
    assert "js/app.js" in html


def test_app_js_has_error_handlers(test_client):
    js = test_client.get("/static/js/app.js")
    assert js.status_code == 200
    assert "htmx:responseError" in js.text
    assert "htmx:sendError" in js.text


# FRONTEND_AUDIT #2 — compiled Tailwind, no runtime CDN.

def test_no_runtime_tailwind_cdn(test_client):
    html = test_client.get("/costs/calculator").text
    assert "css/tailwind.css" in html          # compiled stylesheet linked
    assert "<script src=\"https://cdn.tailwindcss.com" not in html  # no runtime CDN


def test_compiled_tailwind_has_custom_palette(test_client):
    css = test_client.get("/static/css/tailwind.css")
    assert css.status_code == 200
    assert ".bg-espresso{" in css.text         # custom theme color purged-in
    assert ".border-l-4{" in css.text          # utility used in dashboard cards


# FRONTEND_AUDIT #4 — self-hosted vendor JS, no 3rd-party CDN.

def test_vendor_js_self_hosted(test_client):
    html = test_client.get("/costs/calculator").text
    assert "js/vendor/htmx.min.js" in html
    assert "js/vendor/alpine.min.js" in html
    assert "unpkg" not in html and "jsdelivr" not in html
    assert test_client.get("/static/js/vendor/htmx.min.js").status_code == 200
    assert test_client.get("/static/js/vendor/alpine.min.js").status_code == 200


# FRONTEND_AUDIT #7 — a11y + correct document language.

def test_a11y_lang_and_dropdown(test_client):
    html = test_client.get("/costs/calculator").text
    assert 'lang="es"' in html
    assert "aria-haspopup" in html
    assert "keydown.escape" in html
