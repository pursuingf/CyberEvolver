from agents import RunContextWrapper, function_tool

import json

from tagent.agent_dependencies import AgentDeps
from tagent.tools.browser_utils import scrape_page, get_elements, get_page_source

@function_tool
async def get_page_source_tool(ctx: RunContextWrapper[AgentDeps], url: str) -> str:
    """
    Get the source HTML of the webpage specified by the URL.
    If the page source is very long (>30,000 characters), it will be truncated to show
    the first 10,000 and last 5,000 characters.

    Args:
        url (str): The URL of the webpage to get the source HTML of.

    Returns:
        str: The source HTML of the webpage, potentially truncated if very long.
    """
    page_source = get_page_source(url)
    
    # Check length using character count
    if len(page_source) > 30000:
        truncation_message = "WARNING: Page source has been truncated as it exceeds 30,000 characters. Please consider using extract_text_tool, get_elements_tool, or extract_hyperlinks_tool for more focused content extraction.\n\n"
        first_part = page_source[:10000]
        last_part = page_source[-5000:]
        return truncation_message + first_part + "\n\n... [truncated middle content] ...\n\n" + last_part
    
    return page_source
    
@function_tool
async def extract_text_tool(ctx: RunContextWrapper[AgentDeps], url: str) -> str:
    """
    Extracts text from the webpage specified by the URL.

    Args:
        url (str): The URL of the webpage to extract text from.
    Returns:
        str: The extracted text from the page.
    """
    from bs4 import BeautifulSoup

    source_html = get_page_source(url)

    # Parse the HTML content with BeautifulSoup
    soup = BeautifulSoup(source_html, "lxml")

    return " ".join(text for text in soup.stripped_strings)
    
@function_tool
async def get_elements_tool(
    ctx: RunContextWrapper[AgentDeps],
    url: str,
    selector: str, 
    attributes: list[str] | None
) -> str:
    """
    Extracts elements from the current page based on the provided selector and attributes.

    Args:
        url (str): The URL of the webpage to extract elements from.
        selector (str): The CSS selector to find elements.
        attributes (list[str] | None): The attributes to extract from each element.
    Returns:
        str: A JSON string containing the list of elements found on the page.
    """
    elements = await get_elements(url, selector, attributes or ["innerText"])
    
    return json.dumps(elements, ensure_ascii=False) 

@function_tool
async def extract_hyperlinks_tool(ctx: RunContextWrapper[AgentDeps], url: str, absolute_urls: bool | None) -> str:
    """
    Extracts hyperlinks from the source HTML of the webpage specified by the URL.

    Args:
        url (str): The URL of the webpage to extract hyperlinks from.
        absolute_urls (bool | None): Whether to return absolute URLs or relative URLs.
    Returns:
        str: A JSON string containing the list of hyperlinks found on the page.
    """
    source_html = get_page_source(url)
    return scrape_page(url, source_html, absolute_urls=bool(absolute_urls))
    
    