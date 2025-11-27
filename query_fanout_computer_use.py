"""
Query Fanout with Playwright
Uses pure Playwright for browser automation to query multiple AI services.
No external AI (Gemini) needed - just direct DOM manipulation.
"""

import sys
import asyncio
import argparse
import os
import json
import time
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

# Windows event loop fix
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from computers import BrowserbaseComputer, PlaywrightComputer

SCREEN_SIZE = (1440, 900)

SERVICES = {
    "chatgpt": {
        "url": "https://chatgpt.com/",
        "name": "ChatGPT"
    },
    "perplexity": {
        "url": "https://www.perplexity.ai/",
        "name": "Perplexity"
    }}

def submit_chatgpt_query(page, query: str) -> bool:
    """Submit a query to ChatGPT using Playwright with web search enabled."""
    try:
        print("Submitting query to ChatGPT...")

        time.sleep(3)

        textarea_selectors = [
            'textarea[placeholder*="Ask"]',
            'textarea[data-id="root"]',
            '#prompt-textarea',
            'textarea',
        ]

        textarea = None
        for selector in textarea_selectors:
            try:
                textarea = page.wait_for_selector(selector, timeout=5000)
                if textarea:
                    print(f"Found textarea with selector: {selector}")
                    break
            except:
                continue

        if not textarea:
            print("Could not find textarea, trying to click in the center area")
            page.click('body')
            time.sleep(1)
            textarea = page.query_selector('textarea')

        if not textarea:
            raise Exception("Could not find ChatGPT input textarea")

        textarea.click()
        time.sleep(0.5)

        # Look for web search toggle button and enable it
        web_search_selectors = [
            'button[aria-label*="Search"]',
            'button[aria-label*="search"]',
            'button[data-testid*="search"]',
            '[aria-label*="web"]',
            'button:has(svg):near(textarea)',  # Button with icon near textarea
        ]

        for selector in web_search_selectors:
            try:
                search_btn = page.query_selector(selector)
                if search_btn:
                    print(f"Found web search button: {selector}")
                    search_btn.click()
                    time.sleep(1)
                    break
            except:
                continue

        textarea.fill(query)
        time.sleep(0.5)

        textarea.press('Enter')
        print("Query submitted!")

        return True

    except Exception as e:
        print(f"Error submitting ChatGPT query: {e}")
        return False


def wait_for_cloudflare(page, max_wait=30) -> bool:
    """Wait for Cloudflare challenge to complete."""
    print("Checking for Cloudflare protection...")

    for i in range(max_wait):
        title = page.title().lower()

        if "just a moment" in title or "checking" in title or "cloudflare" in title:
            if i % 5 == 0:
                print(f"  Waiting for Cloudflare... ({i}s)")
            time.sleep(1)
        else:
            print(f"  Cloudflare passed! Page title: {page.title()}")
            return True

    print("  Cloudflare timeout - proceeding anyway")
    return False


def submit_perplexity_query(page, query: str, captured_responses: list) -> bool:
    """
    Submit a query to Perplexity using Playwright.
    Uses selectors from perplex_query.py seleniumbase implementation.

    Args:
        page: Playwright page object
        query: The query to submit
        captured_responses: List to store captured SSE responses (passed by reference)
    """
    try:
        print("Submitting query to Perplexity...")

        wait_for_cloudflare(page, max_wait=30)

        time.sleep(3)

        print(f"Page title: {page.title()}")

        input_selector = '#ask-input'

        input_elem = page.query_selector(input_selector)

        if not input_elem:
            print(f"Primary selector '{input_selector}' not found, trying alternatives...")
            # Fallback selectors
            fallback_selectors = [
                'textarea[placeholder*="Ask"]',
                'textarea',
                '[contenteditable="true"]',
            ]
            for selector in fallback_selectors:
                input_elem = page.query_selector(selector)
                if input_elem:
                    print(f"Found input with fallback selector: {selector}")
                    break

        if not input_elem:
            raise Exception("Could not find Perplexity input")

        input_elem.click()
        time.sleep(0.5)

        input_elem.fill(query)
        print(f"Typed query: {query}")
        time.sleep(2)

        try:
            close_btn = page.query_selector('button[data-testid="floating-signup-close-button"]')
            if close_btn:
                print("Closing signup popup...")
                close_btn.click()
                time.sleep(1)
        except:
            pass  # Popup might not appear

        submit_btn = page.query_selector('button[aria-label="Submit"]')
        if submit_btn:
            print("Clicking submit button...")
            submit_btn.click()
        else:
            # Fallback: press Enter
            print("Submit button not found, pressing Enter...")
            input_elem.press('Enter')

        print("Query submitted!")
        return True

    except Exception as e:
        print(f"Error submitting Perplexity query: {e}")
        return False


def setup_perplexity_cdp_capture(page) -> dict:
    """
    Set up CDP network capture to intercept Perplexity SSE responses.
    Uses Chrome DevTools Protocol similar to perplex_query.py
    Returns a dict that will be populated with the request_id and responses.
    """
    capture_data = {
        'request_id': None,
        'responses': []
    }

    # Get the CDP session from Playwright
    cdp = page.context.new_cdp_session(page)

    # Enable network with large buffer for SSE streams (like perplex_query.py)
    cdp.send("Network.enable", {
        "maxTotalBufferSize": 100000000,  # 100MB
        "maxResourceBufferSize": 50000000,  # 50MB
        "maxPostDataSize": 10000000,  # 10MB
    })

    def on_response_received(params):
        url = params.get('response', {}).get('url', '')
        if '/rest/sse/perplexity_ask' in url:
            request_id = params.get('requestId')
            capture_data['request_id'] = request_id
            print(f"🎯 CDP captured perplexity_ask request: {request_id}")

    # Listen for responses
    cdp.on("Network.responseReceived", on_response_received)

    capture_data['cdp'] = cdp
    return capture_data


def get_perplexity_sse_body(capture_data: dict) -> str:
    """
    Get the SSE response body using CDP after the response is complete.
    """
    try:
        cdp = capture_data.get('cdp')
        request_id = capture_data.get('request_id')

        if not cdp or not request_id:
            print("No CDP session or request_id available")
            return ""

        print(f"Fetching response body for request: {request_id}")
        result = cdp.send("Network.getResponseBody", {"requestId": request_id})
        body = result.get('body', '')
        print(f"✓ Got SSE response body: {len(body)} bytes")
        return body
    except Exception as e:
        print(f"Error getting SSE body: {e}")
        return ""


def wait_for_perplexity_response(page, max_wait=90) -> bool:
    """Wait for Perplexity to finish generating response."""
    print("Waiting for Perplexity response to complete...")

    last_content_length = 0
    stable_count = 0

    for i in range(max_wait):
        try:
            # Check page state
            state = page.evaluate("""
                () => {
                    // Check for the stop/generating button (means still generating)
                    const stopBtn = document.querySelector('button[aria-label*="Stop"], button[aria-label*="stop"]');
                    const isGenerating = stopBtn && stopBtn.offsetParent !== null;

                    // Check for loading/spinner indicators
                    const hasSpinner = document.querySelector('[class*="animate-spin"], [class*="loading"], svg[class*="animate"]') !== null;

                    // Look for the answer content in various places
                    let contentLength = 0;
                    const contentSelectors = [
                        '[class*="prose"]',
                        '[class*="markdown"]',
                        '[data-testid*="answer"]',
                        'article',
                        'main'
                    ];

                    for (const sel of contentSelectors) {
                        const el = document.querySelector(sel);
                        if (el) {
                            const len = el.innerText.length;
                            if (len > contentLength) contentLength = len;
                        }
                    }

                    // Check for "related" section which appears after answer is done
                    const hasRelated = document.querySelector('[class*="related"], [class*="Related"]') !== null;

                    // Check for sources/citations
                    const sourceCount = document.querySelectorAll('[class*="citation"], [class*="source"] a, a[href^="http"]').length;

                    return {
                        isGenerating: isGenerating || hasSpinner,
                        contentLength: contentLength,
                        hasRelated: hasRelated,
                        sourceCount: sourceCount
                    };
                }
            """)

            content_length = state.get('contentLength', 0)
            is_generating = state.get('isGenerating', False)
            has_related = state.get('hasRelated', False)

            # Content is stable if it hasn't changed for a few iterations
            if content_length == last_content_length and content_length > 200:
                stable_count += 1
            else:
                stable_count = 0
            last_content_length = content_length

            # Done if: not generating, has substantial content, and content is stable
            if not is_generating and content_length > 500 and stable_count >= 3:
                print(f"✓ Response complete ({content_length} chars, {state.get('sourceCount', 0)} sources)")
                time.sleep(3)  # Extra wait for related queries to render
                return True

            # Also done if we see the related section
            if has_related and content_length > 300:
                print(f"✓ Response complete with related section ({content_length} chars)")
                time.sleep(2)
                return True

            if i % 5 == 0:
                status = "generating..." if is_generating else f"loading... (stable: {stable_count})"
                print(f"  {status} - {content_length} chars ({i}s)")

            time.sleep(1)
        except Exception as e:
            if "closed" in str(e).lower():
                print(f"  Browser closed: {e}")
                return False
            if i % 10 == 0:
                print(f"  Error checking state: {e}")
            time.sleep(1)

    print(f"⚠️  Response timeout after {max_wait}s - proceeding anyway")
    return True


def extract_related_queries_from_sse(captured_responses: list) -> list:
    """
    Extract related_queries from captured SSE responses.
    Based on perplex_query.py regex extraction.
    """
    import re

    related_queries = []

    for response in captured_responses:
        body = response.get('body', '')
        # Use the same regex pattern from perplex_query.py
        matches = re.findall(r'related_queries": \[(.*?)\]', body)
        if matches:
            queries = re.findall(r'"([^"]*)"', matches[0])
            queries = [q for q in queries if q and q.strip() not in [',', ' ']]
            related_queries.extend(queries)

    # Remove duplicates while preserving order
    seen = set()
    unique_queries = []
    for q in related_queries:
        if q not in seen:
            seen.add(q)
            unique_queries.append(q)

    return unique_queries


def wait_for_response(browser_computer, max_wait=60) -> bool:
    """Wait for ChatGPT response to complete"""
    page = browser_computer._page
    print("Waiting for ChatGPT response...")

    for i in range(max_wait):
        try:
            is_responding = page.evaluate("""
                () => {
                    // Check if the stop button is visible (means it's generating)
                    const stopButton = document.querySelector('button[aria-label*="Stop"]');
                    const isGenerating = stopButton && stopButton.offsetParent !== null;

                    // Check if there's response content
                    const hasResponse = document.querySelectorAll('[data-message-author-role="assistant"]').length > 0;

                    return {
                        isGenerating: isGenerating,
                        hasResponse: hasResponse
                    };
                }
            """)

            if is_responding['hasResponse'] and not is_responding['isGenerating']:
                print("✓ Response complete")
                time.sleep(2)  # Extra wait for any final rendering
                return True

            if i % 5 == 0:
                status = "generating..." if is_responding.get('isGenerating') else "waiting for response..."
                print(f"  {status} ({i}s)")
            time.sleep(1)
        except Exception as e:
            if i % 10 == 0:
                print(f"  Waiting... ({i}s)")
            time.sleep(1)

    print("⚠️  Response timeout - proceeding anyway")
    return False



def extract_chatgpt_data(browser_computer, debug=True) -> dict:
    try:
        import time

        page = browser_computer._page
        time.sleep(2)

        print("Extracting data from ChatGPT response...")

        # First, run debug extraction to see what's available
        if debug:
            debug_result = page.evaluate("""
                () => {
                    return {
                        hasSearchIndicator: document.querySelector('[class*="search"]') !== null,
                        hasSearchedText: document.body.innerText.toLowerCase().includes('searched'),
                        allClassNames: Array.from(document.querySelectorAll('[class*="search"], [class*="query"], [class*="source"], [data-testid]'))
                            .slice(0, 20)
                            .map(el => ({
                                tag: el.tagName,
                                className: el.className,
                                text: el.innerText?.substring(0, 50),
                                testId: el.getAttribute('data-testid')
                            })),
                        citationCount: document.querySelectorAll('sup, [class*="citation"]').length,
                        linkCount: document.querySelectorAll('a[href^="http"]').length
                    };
                }
            """)
            print(f"Debug info: {json.dumps(debug_result, indent=2)}")

        extraction_result = page.evaluate(r"""
            () => {
                try {
                    const queries = [];
                    const sourcesList = [];

                    // Extract the assistant's response text
                    const assistantMessages = document.querySelectorAll('[data-message-author-role="assistant"]');
                    let responseText = '';

                    if (assistantMessages.length > 0) {
                        const lastMessage = assistantMessages[assistantMessages.length - 1];
                        responseText = lastMessage.innerText || '';
                    }

                    // Look for the web search UI elements (ChatGPT shows "Searched X sites" when using web search)
                    // Try multiple possible selectors
                    const searchSelectors = [
                        '[class*="SearchStatus"]',
                        '[class*="search-status"]',
                        '[aria-label*="search"]',
                        '[data-testid*="search"]',
                        'button[aria-label*="Searched"]',
                        'div:has(> svg) + span:contains("Searched")'
                    ];

                    searchSelectors.forEach(selector => {
                        try {
                            const elements = document.querySelectorAll(selector);
                            elements.forEach(el => {
                                const text = el.innerText?.trim() || el.getAttribute('aria-label') || '';
                                if (text && (text.includes('Searched') || text.includes('sites'))) {
                                    queries.push(text);
                                }
                            });
                        } catch (e) {
                            // Selector might not be valid, skip it
                        }
                    });

                    // Look for search queries in the full page text
                    const bodyText = document.body.innerText;
                    if (bodyText.toLowerCase().includes('searched')) {
                        // Extract the "Searched X sites" pattern
                        const searchedMatch = bodyText.match(/Searched\s+\d+\s+sites?/i);
                        if (searchedMatch) {
                            queries.push(searchedMatch[0]);
                        }
                    }

                    // Try to extract queries from the response content
                    const queryPatterns = [
                        /(?:searched for|searching for|I'll search for)[:\s]+["']([^"']+)["']/gi,
                        /(?:query|queries)[:\s]+["']([^"']+)["']/gi,
                        /\[Search:\s*([^\]]+)\]/gi
                    ];

                    queryPatterns.forEach(pattern => {
                        let match;
                        while ((match = pattern.exec(responseText)) !== null) {
                            if (match[1] && match[1].trim().length > 3) {
                                queries.push(match[1].trim());
                            }
                        }
                    });

                    // Look for citation/source links (these appear when ChatGPT uses web search)
                    const citations = document.querySelectorAll('sup a, [class*="citation"] a, [data-testid*="citation"]');
                    citations.forEach(citation => {
                        const url = citation.href || citation.getAttribute('href');
                        const text = citation.getAttribute('title') || citation.innerText || '';
                        if (url && url.startsWith('http') && !url.includes('chatgpt.com')) {
                            sourcesList.push({
                                title: text.substring(0, 100) || 'Source',
                                url: url
                            });
                        }
                    });

                    // Look for all external links in the response
                    const links = document.querySelectorAll('a[href^="http"]');
                    const seenUrls = new Set(sourcesList.map(s => s.url));
                    links.forEach(link => {
                        const url = link.href;
                        const title = link.innerText?.trim() || link.getAttribute('aria-label') || url;

                        if (url && !seenUrls.has(url)) {
                            // Filter out ChatGPT's own links
                            if (!url.includes('chatgpt.com') && !url.includes('openai.com')) {
                                sourcesList.push({
                                    title: title.substring(0, 100),
                                    url: url
                                });
                                seenUrls.add(url);
                            }
                        }
                    });

                    return {
                        queries: [...new Set(queries)].slice(0, 10),
                        response: responseText.substring(0, 2000),
                        sources: sourcesList.slice(0, 10),
                        error: null
                    };
                } catch (error) {
                    console.error("Extraction error:", error);
                    return {
                        error: error.toString(),
                        queries: [],
                        response: '',
                        sources: []
                    };
                }
            }
        """)

        print(f"Extracted {len(extraction_result.get('queries', []))} queries, {len(extraction_result.get('sources', []))} sources")
        return extraction_result
    except Exception as e:
        return {"queries": [], "response": "", "sources": [], "error": str(e)}


def extract_perplexity_data(browser_computer) -> dict:
    """Extract structured data from Perplexity including related queries"""
    try:
        page = browser_computer._page
        time.sleep(2)

        # Extract the main answer, sources, and related queries
        result = page.evaluate("""
            () => {
                // Extract main answer content
                const answerSelectors = [
                    '[class*="prose"]',
                    '[class*="Answer"]',
                    '[class*="response"]',
                    'article',
                    'main [class*="markdown"]'
                ];

                let answer = "";
                for (const sel of answerSelectors) {
                    const el = document.querySelector(sel);
                    if (el && el.innerText.length > 50) {
                        answer = el.innerText;
                        break;
                    }
                }

                // Extract sources/citations
                const sources = Array.from(document.querySelectorAll('a[href^="http"]'))
                    .filter(a => {
                        const href = a.href;
                        return href &&
                               !href.includes('perplexity.ai') &&
                               !href.includes('google.com') &&
                               a.textContent.trim().length > 0;
                    })
                    .slice(0, 15)
                    .map(a => ({ url: a.href, title: a.textContent.trim().substring(0, 100) }));

                // Extract related queries - Perplexity shows these at the bottom
                const relatedQueries = [];

                // Try multiple selectors for related queries
                const relatedSelectors = [
                    '[class*="related"] button',
                    '[class*="Related"] button',
                    '[class*="suggestion"]',
                    '[class*="Suggestion"]',
                    'button[class*="query"]',
                    // Related questions are often in a section at the bottom
                    '[class*="follow-up"]',
                    '[class*="FollowUp"]'
                ];

                for (const sel of relatedSelectors) {
                    const elements = document.querySelectorAll(sel);
                    elements.forEach(el => {
                        const text = el.innerText?.trim();
                        if (text && text.length > 10 && text.length < 200) {
                            relatedQueries.push(text);
                        }
                    });
                }

                // Also look for any buttons/links that look like questions
                document.querySelectorAll('button, [role="button"]').forEach(btn => {
                    const text = btn.innerText?.trim();
                    if (text && text.includes('?') && text.length > 15 && text.length < 150) {
                        if (!relatedQueries.includes(text)) {
                            relatedQueries.push(text);
                        }
                    }
                });

                // Remove duplicates
                const uniqueRelated = [...new Set(relatedQueries)];

                return {
                    answer: answer.substring(0, 3000),
                    sources,
                    relatedQueries: uniqueRelated.slice(0, 10)
                };
            }
        """)

        print(f"Extracted from DOM: {len(result.get('answer', ''))} chars answer, {len(result.get('sources', []))} sources, {len(result.get('relatedQueries', []))} related queries")
        return result
    except Exception as e:
        print(f"Error extracting Perplexity data: {e}")
        return {"answer": "", "sources": [], "relatedQueries": [], "error": str(e)}


def query_service(service_name: str, query: str) -> dict:
    """
    Query a single service using Playwright with Browserbase (no login required).

    Args:
        service_name: Name of the service to query
        query: The query to execute

    Returns:
        Dictionary containing the service name, query, and extracted results
    """
    if service_name not in SERVICES:
        return {
            "service": service_name,
            "query": query,
            "error": f"Unknown service: {service_name}",
            "timestamp": datetime.now().isoformat()
        }

    service_info = SERVICES[service_name]

    print(f"\n{'='*60}")
    print(f"Querying {service_info['name']} (using Playwright)...")
    print(f"Query: {query}")
    print(f"{'='*60}\n")

    try:
        # Create computer environment with Browserbase
        env = BrowserbaseComputer(
            screen_size=SCREEN_SIZE,
            initial_url=service_info['url'],
        )

        with env as browser_computer:
            page = browser_computer._page
            print("Waiting for page to load...")
            time.sleep(5)  # Give page time to load initially

            # Set up CDP capture for Perplexity (before submitting query)
            cdp_capture = None
            if service_name == "perplexity":
                cdp_capture = setup_perplexity_cdp_capture(page)

            # Submit query using Playwright
            submit_success = False
            if service_name == "chatgpt":
                submit_success = submit_chatgpt_query(page, query)
            elif service_name == "perplexity":
                submit_success = submit_perplexity_query(page, query, [])

            if not submit_success:
                raise Exception(f"Failed to submit query to {service_name}")

            # Wait for response to complete
            if service_name == "chatgpt":
                try:
                    wait_for_response(browser_computer, max_wait=120)
                except Exception as e:
                    if "Target page, context or browser has been closed" in str(e):
                        print("⚠️  Browser closed while waiting for response")
                        raise Exception("Browser session expired while waiting for ChatGPT response.") from e
                    raise
            elif service_name == "perplexity":
                # Wait for Perplexity response with proper detection
                wait_for_perplexity_response(page, max_wait=60)

            # Extract structured data based on service
            extracted_data = {}
            if service_name == "chatgpt":
                extracted_data = extract_chatgpt_data(browser_computer)
            elif service_name == "perplexity":
                # Try to get related queries from CDP-captured SSE response
                related_queries_from_sse = []
                if cdp_capture and cdp_capture.get('request_id'):
                    sse_body = get_perplexity_sse_body(cdp_capture)
                    if sse_body:
                        # Parse related_queries from SSE body
                        import re
                        matches = re.findall(r'related_queries": \[(.*?)\]', sse_body)
                        if matches:
                            queries = re.findall(r'"([^"]*)"', matches[0])
                            related_queries_from_sse = [q for q in queries if q and q.strip() not in [',', ' ']]
                            print(f"Extracted {len(related_queries_from_sse)} related queries from SSE")

                # Also extract from DOM as fallback/additional data
                extracted_data = extract_perplexity_data(browser_computer)

                # Use SSE-captured queries if available, otherwise use DOM
                if related_queries_from_sse:
                    extracted_data['relatedQueries'] = related_queries_from_sse
                    print(f"Related queries from SSE: {related_queries_from_sse}")

            result = {
                "service": service_name,
                "service_name": service_info['name'],
                "query": query,
                "method": "playwright",  # Indicate we used Playwright
                "extracted_data": extracted_data,
                "timestamp": datetime.now().isoformat(),
                "success": True
            }

    except Exception as e:
        import traceback
        traceback.print_exc()
        result = {
            "service": service_name,
            "service_name": service_info['name'],
            "query": query,
            "error": str(e),
            "timestamp": datetime.now().isoformat(),
            "success": False
        }

    return result


def fanout_query(query: str, services: list = None, output_file: str = None) -> list: #type:ignore
    """
    Execute a query across multiple services (fanout pattern).

    Args:
        query: The query to execute
        services: List of service names to query (default: all services)
        output_file: Optional file to save results to

    Returns:
        List of results from all services
    """
    if services is None:
        services = list(SERVICES.keys())

    results = []

    print(f"\n{'='*60}")
    print(f"QUERY FANOUT")
    print(f"Query: {query}")
    print(f"Services: {', '.join(services)}")
    print(f"{'='*60}\n")

    # Query each service sequentially
    for service in services:
        result = query_service(service, query)
        results.append(result)

        # Print result summary
        print(f"\n{'='*60}")
        print(f"Result from {result.get('service_name', service)}:")
        if result.get('success'):
            print(f"✓ Success (method: {result.get('method', 'unknown')})")

            # Print extracted data based on service
            extracted = result.get('extracted_data', {})
            if service == 'chatgpt':
                queries = extracted.get('queries', [])
                response = extracted.get('response', '')
                sources = extracted.get('sources', [])
                print(f"Extracted Queries ({len(queries)}): {queries}")
                print(f"Response Preview: {response[:300]}..." if len(response) > 300 else f"Response: {response}")
                print(f"Extracted Sources ({len(sources)}): {[s.get('title') for s in sources[:5]]}")
            elif service == 'perplexity':
                answer = extracted.get('answer', '')
                sources = extracted.get('sources', [])
                related = extracted.get('relatedQueries', [])
                print(f"Answer Preview: {answer[:200]}...")
                print(f"Sources ({len(sources)}): {[s.get('title') for s in sources[:3]]}")
                print(f"Related Queries ({len(related)}): {related}")

            if extracted.get('error'):
                print(f"\nExtraction Warning: {extracted.get('error')}")
        else:
            print(f"✗ Failed")
            print(f"Error: {result.get('error', 'Unknown error')}")
        print(f"{'='*60}\n")

    # Save results to file if requested
    if output_file:
        with open(output_file, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"\nResults saved to: {output_file}")

    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run query fanout using Computer Use API across multiple AI services."
    )
    parser.add_argument(
        "--query",
        type=str,
        required=True,
        help="The query to execute across all services.",
    )
    parser.add_argument(
        "--services",
        type=str,
        nargs='+',
        choices=list(SERVICES.keys()),
        default=None,
        help="Specific services to query (default: all services).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output file to save results (JSON format).",
    )

    args = parser.parse_args()

    # Run fanout query
    results = fanout_query(
        query=args.query,
        services=args.services,
        output_file=args.output
    )

    # Print summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"Total services queried: {len(results)}")
    successful = sum(1 for r in results if r.get('success'))
    print(f"Successful queries: {successful}")
    print(f"Failed queries: {len(results) - successful}")
    print(f"{'='*60}\n")

    return 0


if __name__ == "__main__":
    main()
