import json
import requests
from typing import List, Optional, Sequence

def get_page_source(url: str) -> str:
    """
    Get the source HTML of the webpage specified by the URL.
    """
    try:
        response = requests.get(url)
        return response.text
    except Exception as e:
        return f"Error getting source HTML: {e}"
    
def scrape_page(url: str, html_content: str, absolute_urls: bool) -> str:
    """
    Scrapes the HTML content of a page and extracts all links.
    Args:
        url (str): The URL from which to extract links.
        html_content (str): The HTML content of the page.
        absolute_urls (bool): Whether to return absolute URLs or relative URLs.
    Returns:
        str: A JSON string containing the list of links found on the page.
    """
    from urllib.parse import urljoin

    from bs4 import BeautifulSoup

    # Parse the HTML content with BeautifulSoup
    soup = BeautifulSoup(html_content, "lxml")

    # Find all the anchor elements and extract their href attributes
    anchors = soup.find_all("a")
    if absolute_urls:
        base_url = url
        links = [urljoin(base_url, anchor.get("href", "")) for anchor in anchors]
    else:
        links = [anchor.get("href", "") for anchor in anchors]
    # Return the list of links as a JSON string. Duplicated link
    # only appears once in the list
    return json.dumps(list(set(links)))

async def get_elements(
    url: str, selector: str, attributes: Sequence[str]
) -> List[dict]:
    """
    Extracts elements from the page based on the provided selector and attributes.
    Args:
        url (str): The URL of the page to query.
        selector (str): The CSS selector to find elements.
        attributes (Sequence[str]): The attributes to extract from each element.
    Returns:
        List[dict]: A list of dictionaries, each containing the specified attributes of the elements found.
    Raises:
        ValueError: If the URL is invalid or if no elements are found with the given selector.
        requests.RequestException: If there's an error fetching the URL.
    """
    if not url or not url.strip():
        raise ValueError("URL cannot be empty")
    
    try:
        response = requests.get(url)
        response.raise_for_status()  # Raise an exception for bad status codes
        
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response.content, 'html.parser')
        elements = soup.select(selector)
        
        if not elements:
            raise ValueError(f"No elements found with selector: {selector}")
        
        results = []
        for element in elements:
            result = {}
            for attribute in attributes:
                if attribute == "innerText":
                    val = element.get_text(strip=True)
                else:
                    val = element.get(attribute)
                if val is not None and val.strip() != "":
                    result[attribute] = val
            if result:
                results.append(result)
        return results
        
    except requests.RequestException as e:
        raise requests.RequestException(f"Error fetching URL {url}: {str(e)}")
    except Exception as e:
        raise ValueError(f"Error processing elements: {str(e)}")
