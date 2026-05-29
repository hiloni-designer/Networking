"""
LinkedIn browser automation using Playwright.
All actions include randomised delays to mimic human behaviour.
"""

import asyncio
import random
import json
import re
from datetime import datetime
from typing import Optional
from playwright.async_api import async_playwright, Page, Browser, BrowserContext
from loguru import logger
from config.settings import settings


# ─── Human-like delay ─────────────────────────────────────────

async def human_delay(min_s: float = None, max_s: float = None):
    lo = min_s or settings.min_delay_seconds
    hi = max_s or settings.max_delay_seconds
    await asyncio.sleep(random.uniform(lo, hi))


async def short_delay():
    await asyncio.sleep(random.uniform(1.5, 3.5))


# ─── Browser manager ──────────────────────────────────────────

class LinkedInBrowser:
    def __init__(self):
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self._logged_in = False

    async def start(self, headless: bool = True):
        """Launch browser with stealth settings."""
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-extensions",
            ],
        )
        self.context = await self.browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            timezone_id="Asia/Kolkata",
        )
        # Remove navigator.webdriver flag
        await self.context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        self.page = await self.context.new_page()
        logger.info("Browser started")

    async def stop(self):
        if self.page:
            await self.page.close()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        logger.info("Browser stopped")

    # ─── Auth ─────────────────────────────────────────────────

    async def login(self) -> bool:
        """Log in to LinkedIn. Returns True if successful."""
        try:
            await self.page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
            await short_delay()

            await self.page.fill("#username", settings.linkedin_email)
            await short_delay()
            await self.page.fill("#password", settings.linkedin_password)
            await short_delay()

            await self.page.click('[type="submit"]')
            await self.page.wait_for_load_state("networkidle", timeout=15000)
            await short_delay()

            # Check for checkpoint / CAPTCHA
            url = self.page.url
            if "checkpoint" in url or "captcha" in url:
                logger.warning("LinkedIn checkpoint detected — manual intervention needed")
                return False

            if "feed" in url or "mynetwork" in url:
                self._logged_in = True
                logger.info("LinkedIn login successful")
                return True

            logger.error(f"Login failed — unexpected URL: {url}")
            return False

        except Exception as e:
            logger.error(f"Login error: {e}")
            return False

    async def ensure_logged_in(self) -> bool:
        if self._logged_in:
            # Verify session is still alive
            try:
                await self.page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
                if "login" in self.page.url:
                    self._logged_in = False
                    return await self.login()
                return True
            except Exception:
                return await self.login()
        return await self.login()

    # ─── Search ───────────────────────────────────────────────

    async def search_people(self, query: str, max_results: int = 20) -> list[dict]:
        """
        Search LinkedIn for people matching a query.
        Returns list of {name, url, headline} dicts.
        """
        results = []
        try:
            encoded = query.replace(" ", "%20")
            url = f"https://www.linkedin.com/search/results/people/?keywords={encoded}&origin=GLOBAL_SEARCH_HEADER"
            await self.page.goto(url, wait_until="domcontentloaded")
            await human_delay(3, 6)

            page_num = 1
            while len(results) < max_results:
                # Extract profile cards
                cards = await self.page.query_selector_all(
                    "li.reusable-search__result-container"
                )
                if not cards:
                    # Try alternate selector
                    cards = await self.page.query_selector_all(
                        "[data-view-name='search-entity-result-universal-template']"
                    )

                for card in cards:
                    if len(results) >= max_results:
                        break
                    try:
                        link_el = await card.query_selector("a[href*='/in/']")
                        if not link_el:
                            continue
                        href = await link_el.get_attribute("href")
                        profile_url = re.sub(r'\?.*$', '', href)
                        if not profile_url or profile_url in [r["url"] for r in results]:
                            continue

                        name_el = await card.query_selector(
                            "span[aria-hidden='true'], .entity-result__title-text span"
                        )
                        name = (await name_el.inner_text()).strip() if name_el else "Unknown"

                        headline_el = await card.query_selector(
                            ".entity-result__primary-subtitle"
                        )
                        headline = (
                            (await headline_el.inner_text()).strip()
                            if headline_el else ""
                        )

                        results.append({
                            "url": profile_url,
                            "name": name,
                            "headline": headline,
                        })
                    except Exception as inner_e:
                        logger.debug(f"Card parse error: {inner_e}")
                        continue

                # Next page
                next_btn = await self.page.query_selector(
                    "button[aria-label='Next']"
                )
                if not next_btn or len(results) >= max_results:
                    break

                await next_btn.click()
                await human_delay(3, 7)
                page_num += 1
                if page_num > 5:
                    break

            logger.info(f"Search '{query}' → {len(results)} profiles")
        except Exception as e:
            logger.error(f"Search failed for '{query}': {e}")

        return results

    # ─── Profile scraper ──────────────────────────────────────

    async def scrape_profile(self, profile_url: str) -> dict:
        """
        Visit a LinkedIn profile and extract all available data.
        Returns a profile dict.
        """
        data = {
            "linkedin_url": profile_url,
            "full_name": None,
            "headline": None,
            "current_role": None,
            "current_company": None,
            "location": None,
            "about": None,
            "skills": None,
            "linked_website_url": None,
            "profile_picture_url": None,
            "connection_degree": None,
        }

        try:
            clean_url = re.sub(r'\?.*$', '', profile_url.rstrip('/'))
            await self.page.goto(clean_url, wait_until="domcontentloaded")
            await human_delay(3, 6)

            # Scroll to load lazy content
            for _ in range(3):
                await self.page.evaluate("window.scrollBy(0, 600)")
                await asyncio.sleep(1.2)

            # Name
            try:
                name_el = await self.page.query_selector("h1.text-heading-xlarge, h1[class*='heading']")
                if name_el:
                    data["full_name"] = (await name_el.inner_text()).strip()
            except Exception:
                pass

            # Headline
            try:
                headline_el = await self.page.query_selector(
                    ".text-body-medium.break-words, [class*='headline']"
                )
                if headline_el:
                    data["headline"] = (await headline_el.inner_text()).strip()
            except Exception:
                pass

            # Location
            try:
                loc_el = await self.page.query_selector(
                    ".text-body-small.inline.t-black--light.break-words"
                )
                if loc_el:
                    data["location"] = (await loc_el.inner_text()).strip()
            except Exception:
                pass

            # About
            try:
                about_el = await self.page.query_selector(
                    "#about ~ div .inline-show-more-text, "
                    "section[data-section='summary'] span"
                )
                if about_el:
                    data["about"] = (await about_el.inner_text()).strip()
            except Exception:
                pass

            # Current role + company (from experience section)
            try:
                exp_items = await self.page.query_selector_all(
                    ".experience-item, [data-section='experience'] li"
                )
                if exp_items:
                    first = exp_items[0]
                    role_el = await first.query_selector(
                        ".mr1.t-bold span, [class*='title']"
                    )
                    company_el = await first.query_selector(
                        ".t-14.t-normal span, [class*='company']"
                    )
                    if role_el:
                        data["current_role"] = (await role_el.inner_text()).strip()
                    if company_el:
                        data["current_company"] = (await company_el.inner_text()).strip()
            except Exception:
                pass

            # Fallback: parse headline for role
            if not data["current_role"] and data["headline"]:
                parts = data["headline"].split(" at ")
                if len(parts) == 2:
                    data["current_role"] = parts[0].strip()
                    data["current_company"] = parts[1].strip()
                else:
                    data["current_role"] = data["headline"]

            # Skills
            try:
                skills_section = await self.page.query_selector_all(
                    ".skills-section .skill-card-skill-info__skill-name, "
                    "[data-section='skills'] span"
                )
                if skills_section:
                    skill_list = []
                    for s in skills_section[:15]:
                        txt = (await s.inner_text()).strip()
                        if txt:
                            skill_list.append(txt)
                    data["skills"] = ", ".join(skill_list)
            except Exception:
                pass

            # Website URL in contact info
            try:
                contact_btn = await self.page.query_selector(
                    "a[href*='/overlay/contact-info/']"
                )
                if contact_btn:
                    await contact_btn.click()
                    await short_delay()
                    web_el = await self.page.query_selector(
                        "section.pv-contact-info__contact-type a[href^='http']"
                    )
                    if web_el:
                        data["linked_website_url"] = await web_el.get_attribute("href")
                    # Close modal
                    close_btn = await self.page.query_selector(
                        "button[aria-label='Dismiss'], button.artdeco-modal__dismiss"
                    )
                    if close_btn:
                        await close_btn.click()
                    await short_delay()
            except Exception:
                pass

            # Profile picture
            try:
                img_el = await self.page.query_selector(
                    ".pv-top-card-profile-picture__image, img.profile-photo-edit__preview"
                )
                if img_el:
                    data["profile_picture_url"] = await img_el.get_attribute("src")
            except Exception:
                pass

            # Connection degree (1st/2nd/3rd)
            try:
                degree_el = await self.page.query_selector(
                    ".dist-value, [class*='distance']"
                )
                if degree_el:
                    data["connection_degree"] = (await degree_el.inner_text()).strip()
            except Exception:
                pass

            logger.info(f"Scraped: {data.get('full_name')} — {data.get('current_role')} @ {data.get('current_company')}")

        except Exception as e:
            logger.error(f"Profile scrape failed for {profile_url}: {e}")

        return data

    # ─── Send connection invite ────────────────────────────────

    async def send_connection_invite(self, profile_url: str) -> bool:
        """
        Send a connection request to a LinkedIn profile.
        Returns True if invite was sent successfully.
        """
        try:
            clean_url = re.sub(r'\?.*$', '', profile_url.rstrip('/'))
            await self.page.goto(clean_url, wait_until="domcontentloaded")
            await human_delay(2, 5)

            # Look for Connect button
            connect_btn = await self.page.query_selector(
                "button[aria-label*='Connect'], "
                "button:has-text('Connect')"
            )

            if not connect_btn:
                # May be in the More menu
                more_btn = await self.page.query_selector(
                    "button[aria-label='More actions'], button:has-text('More')"
                )
                if more_btn:
                    await more_btn.click()
                    await short_delay()
                    connect_btn = await self.page.query_selector(
                        "li-icon[type='connect'] + span, "
                        "[aria-label*='Connect'], "
                        "div[class*='dropdown'] button:has-text('Connect')"
                    )

            if not connect_btn:
                logger.warning(f"No Connect button found for {profile_url} — may already be connected or 3rd+ degree")
                return False

            await connect_btn.click()
            await short_delay()

            # Handle "Add a note" modal — click Send Without Note for invite
            # (personalised message is sent AFTER connection, per our design)
            send_btn = await self.page.query_selector(
                "button[aria-label='Send without a note'], "
                "button:has-text('Send without a note'), "
                "button:has-text('Send now')"
            )
            if send_btn:
                await send_btn.click()
                await short_delay()
                logger.info(f"Invite sent to {profile_url}")
                return True

            # Some flows show a direct "Send" button
            confirm_btn = await self.page.query_selector(
                "button[aria-label='Send invitation'], button:has-text('Done')"
            )
            if confirm_btn:
                await confirm_btn.click()
                await short_delay()
                logger.info(f"Invite sent to {profile_url}")
                return True

            logger.warning(f"Could not confirm invite send for {profile_url}")
            return False

        except Exception as e:
            logger.error(f"Failed to send invite to {profile_url}: {e}")
            return False

    # ─── Send message ─────────────────────────────────────────

    async def send_message(self, profile_url: str, message_text: str) -> bool:
        """
        Send a LinkedIn message to an existing connection.
        Returns True if message was sent successfully.
        """
        try:
            clean_url = re.sub(r'\?.*$', '', profile_url.rstrip('/'))
            await self.page.goto(clean_url, wait_until="domcontentloaded")
            await human_delay(2, 5)

            # Find Message button
            msg_btn = await self.page.query_selector(
                "button[aria-label*='Message'], button:has-text('Message')"
            )
            if not msg_btn:
                logger.warning(f"No Message button found for {profile_url}")
                return False

            await msg_btn.click()
            await human_delay(1, 3)

            # Find the message input area
            msg_input = await self.page.query_selector(
                "div[role='textbox'][contenteditable='true'], "
                ".msg-form__contenteditable"
            )
            if not msg_input:
                logger.error("Message input not found")
                return False

            await msg_input.click()
            await short_delay()

            # Type message with human-like pace
            for char in message_text:
                await msg_input.type(char, delay=random.randint(30, 90))

            await short_delay()

            # Send
            send_btn = await self.page.query_selector(
                "button[class*='msg-form__send-button'], "
                "button[aria-label='Send'], "
                "button:has-text('Send')"
            )
            if not send_btn:
                logger.error("Send button not found")
                return False

            await send_btn.click()
            await short_delay()
            logger.info(f"Message sent to {profile_url} ({len(message_text)} chars)")
            return True

        except Exception as e:
            logger.error(f"Failed to send message to {profile_url}: {e}")
            return False

    # ─── Check new connections ────────────────────────────────

    async def get_recent_connections(self, max_items: int = 30) -> list[dict]:
        """
        Return recently accepted connections from My Network.
        Returns list of {url, name, connected_at} dicts.
        """
        connections = []
        try:
            await self.page.goto(
                "https://www.linkedin.com/mynetwork/invite-connect/connections/",
                wait_until="domcontentloaded",
            )
            await human_delay(3, 5)

            items = await self.page.query_selector_all(
                "li.mn-connection-card, [data-view-name='connection-card']"
            )

            for item in items[:max_items]:
                try:
                    link_el = await item.query_selector("a[href*='/in/']")
                    if not link_el:
                        continue
                    href = await link_el.get_attribute("href")
                    profile_url = re.sub(r'\?.*$', '', href)

                    name_el = await item.query_selector(
                        ".mn-connection-card__name, span[class*='name']"
                    )
                    name = (await name_el.inner_text()).strip() if name_el else "Unknown"

                    connections.append({
                        "url": profile_url,
                        "name": name,
                        "connected_at": datetime.utcnow().isoformat(),
                    })
                except Exception:
                    continue

            logger.info(f"Found {len(connections)} recent connections")
        except Exception as e:
            logger.error(f"Failed to fetch connections: {e}")

        return connections
