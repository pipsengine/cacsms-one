"""Isolated Playwright research visits restricted to explicitly approved hosts."""

import asyncio
import ipaddress
import os
import socket
from pathlib import Path
from urllib.parse import urlsplit


class BrowserResearchError(RuntimeError):
    pass


def _host_addresses(hostname):
    try:
        return {
            ipaddress.ip_address(row[4][0].split('%', 1)[0])
            for row in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        }
    except (OSError, ValueError) as error:
        raise BrowserResearchError(f'Cannot resolve approved website host {hostname}') from error


def validate_research_url(value, approved_hosts):
    parsed = urlsplit(value)
    test_local_host = (
        os.environ.get('SPORTS_BROWSER_ALLOW_LOCAL_TESTS') == '1'
        and parsed.hostname in ('localhost','127.0.0.1')
    )
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
            or parsed.password or (
                parsed.port not in (None,443) and not test_local_host
            )
            or parsed.fragment):
        if not (test_local_host and parsed.scheme == 'http'
                and not parsed.username and not parsed.password
                and not parsed.fragment):
            raise BrowserResearchError('Browser research accepts HTTPS URLs without credentials or custom ports')
    hostname = parsed.hostname.casefold().rstrip('.')
    if hostname not in approved_hosts:
        raise BrowserResearchError(f'Website host {hostname} has not been approved by an administrator')
    local_test = os.environ.get('SPORTS_BROWSER_ALLOW_LOCAL_TESTS') == '1'
    if local_test and hostname in ('localhost', '127.0.0.1'):
        return hostname
    addresses = _host_addresses(hostname)
    if not addresses or any(
        address.is_private or address.is_loopback or address.is_link_local
        or address.is_multicast or address.is_reserved or address.is_unspecified
        for address in addresses
    ):
        raise BrowserResearchError('Browser research cannot visit private or local network addresses')
    return hostname


from browser_slots import holds_browser_slot


@holds_browser_slot('research', wait=120)
async def _research_async(urls, approved_hosts, fields, capture_directory, host_map):
    try:
        from playwright.async_api import async_playwright
    except ImportError as error:
        raise BrowserResearchError(
            'Playwright is not installed. Install backend requirements and run `python -m playwright install chromium`.'
        ) from error

    capture_directory.mkdir(parents=True, exist_ok=True)
    results = []
    async with async_playwright() as playwright:
        resolver_rules = [
            f'MAP {hostname} {address}' for hostname,address in host_map.items()
        ]
        resolver_rules.append('MAP * ~NOTFOUND')
        try:
            browser = await playwright.chromium.launch(
                headless=True,
                args=[f"--host-resolver-rules={','.join(resolver_rules)}"],
            )
        except Exception as error:
            raise BrowserResearchError(
                'Chromium could not start. Install it with `python -m playwright install chromium`.'
            ) from error
        try:
            context = await browser.new_context(
                java_script_enabled=True,
                accept_downloads=False,
                service_workers='block',
                viewport={'width': 1365, 'height': 900},
            )
            for index, value in enumerate(urls[:10], 1):
                requested_host = validate_research_url(value, approved_hosts)
                page = await context.new_page()
                page.set_default_navigation_timeout(20_000)
                page.set_default_timeout(8_000)

                async def guard(route):
                    target = urlsplit(route.request.url)
                    host = (target.hostname or '').casefold().rstrip('.')
                    local_test = os.environ.get('SPORTS_BROWSER_ALLOW_LOCAL_TESTS') == '1'
                    if (target.scheme != 'https'
                            and not (local_test and target.scheme == 'http'
                                     and host in ('localhost', '127.0.0.1'))):
                        await route.abort()
                        return
                    if host != requested_host and not (
                        local_test and host in ('localhost', '127.0.0.1')
                    ):
                        await route.abort()
                        return
                    if not (local_test and host in ('localhost', '127.0.0.1')):
                        try:
                            addresses = _host_addresses(host)
                        except BrowserResearchError:
                            await route.abort()
                            return
                        if any(
                            address.is_private or address.is_loopback
                            or address.is_link_local or address.is_reserved
                            for address in addresses
                        ):
                            await route.abort()
                            return
                    await route.continue_()

                await page.route('**/*', guard)
                item = {
                    'url': value,
                    'requested_host': requested_host,
                    'title': '',
                    'text': '',
                    'status_code': None,
                    'screenshot': None,
                    'fields': {},
                    'error': None,
                }
                try:
                    response = await page.goto(value, wait_until='domcontentloaded')
                    item['status_code'] = response.status if response else None
                    item['url'] = page.url
                    item['title'] = (await page.title())[:300]
                    if response and response.status >= 400:
                        raise BrowserResearchError(
                            f'Website returned HTTP {response.status}; page extraction was stopped'
                        )
                    page_text = (await page.locator('body').inner_text())[:50_000]
                    blocked_markers = (
                        'captcha','verify you are human','verify that you are human',
                        'unusual traffic','access denied','subscribe to continue',
                        'subscription required','paywall',
                    )
                    if any(marker in page_text.casefold() for marker in blocked_markers):
                        raise BrowserResearchError(
                            'Page appears to require a challenge, subscription or access verification; extraction was stopped'
                        )
                    item['text'] = page_text
                    for field, selector in fields.items():
                        values = await page.locator(selector).all_inner_texts()
                        item['fields'][field] = [text.strip()[:4000] for text in values[:50] if text.strip()]
                    screenshot = capture_directory / f'page-{index}.png'
                    await page.screenshot(path=str(screenshot), full_page=False, timeout=10_000)
                    item['screenshot'] = str(screenshot)
                except BrowserResearchError as error:
                    item['error'] = str(error)[:500]
                    try:
                        screenshot = capture_directory / f'page-{index}.png'
                        await page.screenshot(path=str(screenshot),full_page=False,timeout=10_000)
                        item['screenshot'] = str(screenshot)
                    except Exception as screenshot_error:
                        item['error'] = (
                            f'{item["error"]}; screenshot failed: {str(screenshot_error)[:200]}'
                        )
                except Exception as error:
                    item['error'] = str(error)[:500]
                finally:
                    await page.close()
                results.append(item)
            await context.close()
        finally:
            await browser.close()
    return results


def research_pages(urls, approved_hosts, fields, capture_directory):
    host_map = {}
    for url in urls[:10]:
        hostname = validate_research_url(url,approved_hosts)
        if os.environ.get('SPORTS_BROWSER_ALLOW_LOCAL_TESTS') == '1' and hostname in ('localhost','127.0.0.1'):
            host_map[hostname] = '127.0.0.1'
            continue
        from source_permissions import SourceNotPermitted, require
        try:
            require(url, 'research')
        except SourceNotPermitted as error:
            raise BrowserResearchError(str(error)) from error
        addresses = _host_addresses(hostname)
        if not addresses:
            raise BrowserResearchError(f'No public IP address found for {hostname}')
        address = sorted(addresses,key=lambda value: value.version)[0]
        host_map[hostname] = f'[{address}]' if address.version == 6 else str(address)
    return asyncio.run(
        _research_async(urls,approved_hosts,fields,Path(capture_directory),host_map)
    )
