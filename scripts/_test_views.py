from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 430, "height": 932})
    page.goto(
        "https://badroneai.github.io/etimad-plus-viewer/?v=10",
        wait_until="networkidle",
        timeout=120000,
    )
    page.wait_for_timeout(1500)

    def visible(sel):
        return page.locator(sel).evaluate(
            "el => !el.hasAttribute('hidden') && el.classList.contains('is-active')"
        )

    print("home start", visible("#view-home"))
    print("inv hidden", not visible("#view-inventory"))
    print("exp hidden", not visible("#view-explorer"))

    page.click('a[data-nav="explorer"]')
    page.wait_for_timeout(2000)
    print("explorer", visible("#view-explorer"))
    print("home gone", not visible("#view-home"))
    print("brand on explorer?", page.locator("#view-explorer .brand").count() == 0)
    print("stats on explorer?", page.locator("#view-explorer #statStrip").count() == 0)
    print("hash", page.evaluate("location.hash"))

    page.click('a[data-nav="inventory"]')
    page.wait_for_timeout(500)
    print("inventory", visible("#view-inventory"))
    print("catalog", page.locator("#catalog .catalog-card").count())
    print("explorer gone", not visible("#view-explorer"))
    b.close()
