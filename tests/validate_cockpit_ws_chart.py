"""End-to-End browser validation of Cockpit TradingView Chart live WebSocket tick streaming."""
import asyncio
from playwright.async_api import async_playwright

async def validate():
    print("=== STARTING PLAYWRIGHT E2E VALIDATION OF COCKPIT TRADINGVIEW WS STREAMING ===")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # Capture console messages
        console_logs = []
        page.on("console", lambda msg: console_logs.append(f"[{msg.type}] {msg.text}"))
        
        url = "http://127.0.0.1:8640"
        print(f"1. Navigating to {url}...")
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        
        # 2. Check Cockpit tab is active and #cockpit-tv-card is rendered
        card = page.locator("#cockpit-tv-card")
        assert await card.is_visible(), "Error: #cockpit-tv-card not visible!"
        print("  ✓ #cockpit-tv-card is mounted and visible")
        
        # 3. Check Canvas engine container and HTML5 Canvas element
        canvas_container = page.locator("#cockpit-gex-canvas-container")
        assert await canvas_container.is_visible(), "Error: #cockpit-gex-canvas-container not visible!"
        canvas_elem = page.locator("#cockpit-gex-canvas-container canvas").first
        await canvas_elem.wait_for(state="attached", timeout=20000)
        box = await canvas_elem.bounding_box()
        assert box and box["width"] > 300 and box["height"] > 200, f"Canvas too small: {box}"
        print(f"  ✓ TradingView Lightweight Canvas active: {int(box['width'])}x{int(box['height'])}px")
        
        # 4. Check WS connection badge
        ws_badge = page.locator("#quote-source-badge")
        await page.wait_for_function("document.getElementById('quote-source-badge')?.textContent.includes('WS')", timeout=15000)
        badge_text = await ws_badge.text_content()
        print(f"  ✓ Quote source status: {badge_text}")
        
        # 5. Wait for live Dhan tick streaming into the chart badge
        print("5. Waiting for live Dhan tick streaming update on #cockpit-chart-badge...")
        await page.wait_for_function(
            "document.getElementById('cockpit-chart-badge')?.textContent.includes('DHAN TICK') || document.getElementById('cockpit-chart-badge')?.textContent.includes('LIVE')",
            timeout=15000
        )
        live_badge_text = await page.locator("#cockpit-chart-badge").text_content()
        print(f"  ✓ Live chart badge received streaming tick: '{live_badge_text.strip()}'")
        
        # 6. Check OHLC HUD updates (move mouse over chart to trigger crosshair HUD)
        canvas_box = await canvas_elem.bounding_box()
        if canvas_box:
            await page.mouse.move(canvas_box["x"] + 100, canvas_box["y"] + 100)
            await page.wait_for_timeout(300)
        ohlc_hud = await page.locator("#chl-ohlc").text_content()
        print(f"  ✓ OHLC forming bar HUD: '{ohlc_hud.strip()}'")
        assert "O:" in ohlc_hud and "C:" in ohlc_hud, f"Invalid OHLC: {ohlc_hud}"
        
        # 7. Test Timeframe Switching (1m, 15m, 5min)
        print("7. Testing Multi-Timeframe buttons...")
        # Switch to 1m
        await page.locator("#btn-cockpit-tf-1m").click()
        await page.wait_for_timeout(600)
        tf_badge_1m = await page.locator("#cockpit-chart-badge").text_content()
        assert "1m" in tf_badge_1m, f"Timeframe 1m failed: {tf_badge_1m}"
        print(f"  ✓ Timeframe 1m active: '{tf_badge_1m.strip()}'")
        
        # Switch to 15m
        await page.locator("#btn-cockpit-tf-15m").click()
        await page.wait_for_timeout(600)
        tf_badge_15m = await page.locator("#cockpit-chart-badge").text_content()
        assert "15m" in tf_badge_15m, f"Timeframe 15m failed: {tf_badge_15m}"
        print(f"  ✓ Timeframe 15m active: '{tf_badge_15m.strip()}'")
        
        # Switch back to 5min
        await page.locator("#btn-cockpit-tf-5m").click()
        await page.wait_for_timeout(600)
        tf_badge_5m = await page.locator("#cockpit-chart-badge").text_content()
        assert "5min" in tf_badge_5m, f"Timeframe 5m failed: {tf_badge_5m}"
        print(f"  ✓ Timeframe 5min active: '{tf_badge_5m.strip()}'")
        
        # 8. Test Dynamic Index Switching (BANKNIFTY)
        print("8. Testing Dynamic Index switching to BANKNIFTY...")
        await page.locator("#index-select").select_option("BANKNIFTY")
        await page.wait_for_function(
            "document.getElementById('cockpit-chart-symbol-badge')?.textContent.includes('BANKNIFTY')",
            timeout=8000
        )
        sym_badge = await page.locator("#cockpit-chart-symbol-badge").text_content()
        print(f"  ✓ Cockpit chart symbol badge updated: '{sym_badge.strip()}'")
        assert sym_badge.strip() == "BANKNIFTY"
        
        # 9. Test Standalone Popout Chart Page
        print("9. Validating Standalone /chart/tradingview popout window...")
        popout_url = "http://127.0.0.1:8640/chart/tradingview?symbol=BANKNIFTY&timeframe=5m"
        req_ctx = await p.request.new_context()
        resp = await req_ctx.get(popout_url)
        assert resp.status == 200, f"Expected 200, got {resp.status}"
        resp_text = await resp.text()
        assert "BANKNIFTY" in resp_text and "TradingView" in resp_text
        print(f"  ✓ Standalone popout endpoint verified (200 OK, BANKNIFTY TradingView)")
        await page.close()
        
        await browser.close()
        print("\n=== ALL VALIDATION CHECKS PASSED SUCCESSFULLY ===")

if __name__ == "__main__":
    asyncio.run(validate())
