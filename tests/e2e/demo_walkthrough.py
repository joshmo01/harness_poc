"""Browser walkthrough of the 6-step demo; saves screenshots to docs/screenshots.

Needs the backend (python -m poc.run_all) and the web app on :5173.
    python tests/e2e/demo_walkthrough.py
"""
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5173"
OUT = Path(__file__).resolve().parents[2] / "docs" / "screenshots"


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    errors = []
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1440, "height": 1000})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        await pg.goto(URL)
        await pg.click("text=Reset demo")
        await pg.wait_for_timeout(1500)

        async def ask(prompt, shot, wait=4000):
            await pg.fill("form input", prompt)
            await pg.click("form button")
            await pg.wait_for_timeout(wait)
            await pg.evaluate("document.querySelector('.chat').scrollTop = 1e6")
            await pg.wait_for_timeout(300)
            await pg.screenshot(path=str(OUT / shot))

        # 1. Underwriter assesses case 1042
        await ask("Assess case 1042", "1-assess-case.png")
        await pg.evaluate("document.querySelector('.chat').scrollTop = 0")
        await pg.wait_for_timeout(300)
        await pg.screenshot(path=str(OUT / "1-assess-case.png"))
        await pg.locator(".approval").last.scroll_into_view_if_needed()
        await pg.screenshot(path=str(OUT / "1b-assess-case-panels.png"))
        # 2. Code mode
        await ask("Analyse the bank statement for case 1042", "2-code-mode.png", 6000)
        # 3a. Underwriter clicks Approve -> denied by Cedar
        await pg.locator(".approval .approve").first.click()
        await pg.wait_for_timeout(1500)
        await pg.screenshot(path=str(OUT / "3-underwriter-approve-denied.png"))
        # 3b. Field officer opens a Pune case -> branch policy
        await pg.click("text=Imran (Field Officer)")
        await ask("Assess case 1042", "3b-field-officer-branch-denied.png")
        # 4. Tamper the bureau tool -> pin check hides it
        await pg.click("text=Ravi (Credit Manager)")
        await pg.click(".tamper input")
        await pg.wait_for_timeout(800)
        await ask("Assess case 1042", "4-tampered-tool-hidden.png")
        await pg.click("aside nav >> text=Registry & pins")
        await pg.wait_for_timeout(800)
        await pg.screenshot(path=str(OUT / "4b-registry-pin-mismatch.png"))
        await pg.click(".tamper input")
        await pg.wait_for_timeout(800)
        # 5. Credit manager approves via the panel
        await ask("Assess case 1042", "5-credit-manager-case.png")
        await pg.locator(".approval .approve").last.click()
        await pg.wait_for_timeout(1500)
        await pg.screenshot(path=str(OUT / "5-credit-manager-approved.png"))
        # Agent cannot approve via chat
        await ask("Approve case 1042", "5b-agent-cannot-approve.png")
        # 6. Decision record
        await pg.click("aside nav >> text=Decision record")
        await pg.wait_for_timeout(1000)
        await pg.screenshot(path=str(OUT / "6-decision-record.png"))
        await b.close()
    if errors:
        print("PAGE ERRORS:", *errors, sep="\n  ")
        sys.exit(1)
    print(f"Walkthrough OK; screenshots in {OUT}")


asyncio.run(main())
