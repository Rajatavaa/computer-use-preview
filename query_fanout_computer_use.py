"""
Query Fanout with Computer Use API
Integrates query fanout functionality with Gemini Computer Use API
to query multiple AI services and collect results.
"""

import sys
import asyncio
import argparse
import os
import json
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

# Windows event loop fix
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from agent import BrowserAgent
from computers import BrowserbaseComputer

SCREEN_SIZE = (1440, 900)

# Define the services to query
SERVICES = {
    "chatgpt": {
        "url": "https://chatgpt.com/",
        "name": "ChatGPT"
    },
    "perplexity": {
        "url": "https://www.perplexity.ai/",
        "name": "Perplexity"
    }}

def wait_for_response(browser_computer, max_wait=60) -> bool:
    import time

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
    try:
        import time

        page = browser_computer._page
        time.sleep(3)

        # Extract the main answer and sources
        result = page.evaluate("""
            () => {
                const answer = document.querySelector('[class*="Answer"]')?.innerText ||
                              document.querySelector('article')?.innerText || "";

                const sources = Array.from(document.querySelectorAll('a[href^="http"]'))
                    .filter(a => a.href && a.textContent.trim())
                    .slice(0, 10)
                    .map(a => ({ url: a.href, title: a.textContent.trim() }));

                const relatedQueries = Array.from(document.querySelectorAll('[class*="related"]'))
                    .map(el => el.textContent.trim())
                    .filter(t => t && t.length > 5);

                return { answer, sources, relatedQueries };
            }
        """)

        return result
    except Exception as e:
        return {"answer": "", "sources": [], "relatedQueries": [], "error": str(e)}


def query_service(service_name: str, query: str) -> dict:
    """
    Query a single service using the computer use API with Browserbase (no login required).

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

    # Construct the full prompt for the agent
    if service_name == "chatgpt":
        full_query = f"""
        You are on the ChatGPT homepage. Your task is to submit a query WITH WEB SEARCH enabled.

        Steps:
        1. Find the text input box (large textarea at the middle of the page with "Ask anything" written inside)
        2. Click on the textarea to focus it
        3. IMPORTANT: Before typing, look for and click the Web search option with a world web symbol beside Attach,Click it (it enables web search). This is CRITICAL!
        5. Then type this exact query: {query}
        6. Press Enter to send the query
        7. Wait 5-10 seconds for the response to start appearing, then YOUR JOB IS DONE

        IMPORTANT:
        - You MUST click the Search button to enable web search, otherwise ChatGPT will only use its training data
        - Do NOT wait for the entire response to complete. Just submit the query and wait a few seconds for it to start responding

        Use these functions: click_at, type_text_at (with press_enter=True), wait_5_seconds
        """
    elif service_name == "perplexity":
        full_query = f"""
        You are on the Perplexity homepage. Submit this query and wait for the answer.

        Steps:
        1. Find the search input box
        2. Click on it to focus
        3. Type this exact query: {query}
        4. Press Enter to submit
        5. Wait for the complete answer with sources

        Use these functions: click_at, type_text_at (with press_enter=True), wait_5_seconds
        """
    else:
        full_query = f"Go to {service_info['name']} and search for: {query}. Extract and provide the results."

    print(f"\n{'='*60}")
    print(f"Querying {service_info['name']}...")
    print(f"Query: {query}")
    print(f"{'='*60}\n")

    try:
        # Create computer environment with Browserbase
        env = BrowserbaseComputer(
            screen_size=SCREEN_SIZE,
            initial_url=service_info['url'],
        )

        with env as browser_computer:
            import time
            print("Waiting for page to load...")
            time.sleep(5)  # Give page time to load initially

            # Create agent to submit the query
            agent = BrowserAgent(
                browser_computer=browser_computer,
                query=full_query,
                model_name='gemini-2.5-computer-use-preview-10-2025',
                verbose=True
            )

            # Run the agent to submit query
            try:
                agent.agent_loop()
            except Exception as e:
                if "Target page, context or browser has been closed" in str(e):
                    print("⚠️  Browser session closed unexpectedly - possible timeout")
                    raise Exception("Browser session timed out. Try reducing the complexity of your query or increasing the session timeout.") from e
                raise

            # Wait for response to complete
            if service_name == "chatgpt":
                try:
                    wait_for_response(browser_computer, max_wait=120)
                except Exception as e:
                    if "Target page, context or browser has been closed" in str(e):
                        print("⚠️  Browser closed while waiting for response")
                        raise Exception("Browser session expired while waiting for ChatGPT response.") from e
                    raise

            # Extract structured data based on service
            extracted_data = {}
            if service_name == "chatgpt":
                extracted_data = extract_chatgpt_data(browser_computer)
            elif service_name == "perplexity":
                extracted_data = extract_perplexity_data(browser_computer)

            result = {
                "service": service_name,
                "service_name": service_info['name'],
                "query": query,
                "agent_reasoning": agent.final_reasoning,
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


def fanout_query(query: str, services: list = None, output_file: str = None) -> list:
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
            print(f"✓ Success")
            print(f"\nAgent Reasoning: {result.get('agent_reasoning', 'No reasoning')}\n")

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
