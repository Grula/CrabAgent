# Playwright Recipes for Scripted Test Steps

Use these when the `browser_*` tools are not enough: repeatable multi-step flows, file uploads,
keyboard shortcuts, transient UI, or console capture during a flow. Write the script to a scratch
file (not into the project) and run it with `bash`: `python flow.py`.

Requires `pip install playwright && playwright install chromium` (already present when CrabAgent's
browser tools work).

## Skeleton: run a flow, capture errors, screenshot each step

```python
from playwright.sync_api import sync_playwright, expect

BASE = "http://localhost:5173"
errors = []

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(f"uncaught: {e}"))

    page.goto(BASE, wait_until="networkidle")
    page.screenshot(path="web-test-artifacts/01-home.png")

    page.get_by_role("link", name="Sign up").click()
    page.get_by_label("Email").fill("qa+1@example.com")
    page.get_by_label("Password").fill("correct-horse-42")
    page.get_by_role("button", name="Create account").click()

    expect(page.get_by_text("Welcome")).to_be_visible(timeout=10_000)
    page.screenshot(path="web-test-artifacts/02-after-signup.png")

    print("ERRORS:", errors)
    browser.close()
```

Prefer role/label/text locators (`get_by_role`, `get_by_label`, `get_by_text`) — they mirror what a
user sees and break less than CSS selectors. `expect(...)` waits automatically; avoid fixed sleeps
except for purely visual animations.

## Transient states (toast, spinner)

```python
page.get_by_role("button", name="Save").click()
toast = page.get_by_role("status")          # or page.get_by_text("Saved")
toast.wait_for(state="visible", timeout=5000)
page.screenshot(path="web-test-artifacts/toast.png")
```

## Keyboard

```python
page.keyboard.press("Tab")
page.keyboard.press("Shift+Tab")
page.keyboard.press("Escape")               # should close a modal
page.keyboard.press("Control+K")            # shortcuts
```

## File upload

```python
page.set_input_files("input[type=file]", "fixtures/sample.pdf")
```

## Phone viewport and dark mode

```python
ctx = browser.new_context(**p.devices["Pixel 7"], color_scheme="dark")
page = ctx.new_page()
```

## Network failures and slow APIs

```python
page.route("**/api/orders", lambda r: r.fulfill(status=500, body="{}"))   # error UI
page.route("**/api/orders", lambda r: r.abort())                           # offline / network error
```

To see loading states, throttle the connection through CDP instead of blocking the handler:

```python
cdp = page.context.new_cdp_session(page)
cdp.send("Network.enable")
cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 1500,
         "downloadThroughput": 50_000, "uploadThroughput": 20_000})
```

Use mocking only to test how the UI reacts to failures; say so in the report.

## Reusing a login

Log in once, save the state, reuse it in later scripts:

```python
ctx.storage_state(path="web-test-artifacts/auth.json")
# later
ctx = browser.new_context(storage_state="web-test-artifacts/auth.json")
```

Never commit `auth.json`; it contains session cookies.
