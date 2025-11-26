# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import os
import sys
import termcolor
from ..playwright.playwright import PlaywrightComputer

# Fix for Windows event loop policy - set before any imports that might use asyncio
if sys.platform == 'win32':
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from playwright.sync_api import sync_playwright


class BrowserbaseComputer(PlaywrightComputer):
    def __init__(
        self,
        screen_size: tuple[int, int],
        initial_url: str = "https://www.google.com",
    ):
        super().__init__(screen_size, initial_url)

    def __enter__(self):
        print("Creating session...")

        self._playwright = sync_playwright().start()

        import browserbase
        self._browserbase = browserbase.Browserbase(
            api_key=os.environ["BROWSERBASE_API_KEY"],
            timeout=60.0  # Increase timeout to 60 seconds for slow connections
        )

        # Get extension ID if available, otherwise use None
        extension_id = os.environ.get("BROWSERBASE_EXTENSION_ID")

        session_params = {
            "project_id": os.environ["BROWSERBASE_PROJECT_ID"],
            "proxies": True,  # Already uses residential IPs
            "keep_alive": True,
            "timeout": 900,  # Increased to 15 minutes for long-running queries
            "browser_settings": {
                # DEVELOPER PLAN: Block ads to reduce fingerprinting surface
                "block_ads": True,

                # DEVELOPER PLAN: Auto-solve captchas (enabled by default)
                "solve_captchas": True,

                # DEVELOPER PLAN: Enable session recording for debugging
                "record_session": True,
                "log_session": True,

                # DEVELOPER PLAN: Optimized fingerprinting for ChatGPT
                "fingerprint": {
                    "screen": {
                        "maxWidth": 1920,
                        "maxHeight": 1080,
                        "minWidth": 1280,  # More realistic desktop minimum
                        "minHeight": 800,
                    },
                    "browsers": ["chrome"],  # Single browser type for consistency
                    "operatingSystems": ["windows", "macos"],  # Most common desktop
                    "locales": ["en-US"],  # Single locale to appear more natural
                    "httpVersion": 2,
                    "devices": ["desktop"],  # Explicitly desktop
                },
                "viewport": {
                    "width": self._screen_size[0],
                    "height": self._screen_size[1],
                },
            }
        }

        # Add extension_id only if it exists
        if extension_id:
            session_params["extension_id"] = extension_id

        self._session = self._browserbase.sessions.create(**session_params)

        self._browser = self._playwright.chromium.connect_over_cdp(
            self._session.connect_url
        )
        self._context = self._browser.contexts[0]
        self._context.set_default_timeout(120000)  # 120 seconds for Cloudflare
        self._context.set_default_navigation_timeout(120000)

        # Grant permissions for better OAuth handling
        self._context.grant_permissions(['geolocation', 'notifications'])

        self._page = self._context.pages[0]
        self._page.set_default_timeout(120000)
        self._page.set_default_navigation_timeout(120000)

        # Navigate with less strict requirements
        try:
            self._page.goto(self._initial_url, wait_until="domcontentloaded", timeout=120000)
        except Exception as e:
            print(f"Initial navigation warning: {e}")
            # Continue anyway, the page might still load

        # Wait for potential Cloudflare challenges to complete
        import time
        print("Waiting for any anti-bot challenges to complete...")
        time.sleep(10)

        self._context.on("page", self._handle_new_page)

        termcolor.cprint(
            f"Session started at https://browserbase.com/sessions/{self._session.id}",
            color="green",
            attrs=["bold"],
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._page.close()

        if self._context:
            self._context.close()

        if self._browser:
            self._browser.close()

        self._playwright.stop()
