from .base_connector import BaseConnector

from configs.settings import DUCKDUCKGO_SEARCH_ENDPOINT

from bs4 import BeautifulSoup
from pathlib import Path
import requests
import os

class WebScraperConnector(BaseConnector):
  def __init__(self):
    self.BASE_DIR = Path(__file__).resolve().parent
    print(self.BASE_DIR)
    self.KNOWLEDGE_BASE_PATH = self.BASE_DIR / "knowledge_base.txt"
       
    # GLOBAL VARIABLEs
    self.HTTP_REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    self.extracted_search_links = []
   # self.KNOWLEDGE_BASE_PATH = "knowledge_base.txt"
    
  def fetch_search_urls(self, search_query: str):
    """Function for fetching webpage urls 
    from duckduckgo and returning the top links."""
    
    self.extracted_search_links = []
    
    try:
      # Getting search results including vid, img, text, links...
      self.search_response = requests.post(
        DUCKDUCKGO_SEARCH_ENDPOINT,
        headers=self.HTTP_REQUEST_HEADERS,
        data={"q": search_query},
        timeout=5
      )
      
      if self.search_response.status_code != 200:
        return f"HTTP ERROR > Status code: {self.search_response.status_code}"
        
    
    except Exception as e:
      return f"REQUEST ERROR: \n{str(e)}"
    
    # Converting raw search result page into html for easy scraping.
    self.parsed_search_html = BeautifulSoup(
      self.search_response.text,
      "html.parser"
    ) 
    
    # Taking every anchor tag in search html page for links.
    for a in self.parsed_search_html.select(".result__a"):
      self.extracted_search_links.append(a.get("href", ""))
      
    # Returning only top links/urls.
    return self.extracted_search_links[:5] # only 3 links
    
  def scrape_page(self, urls: str):
    """ 
    Function for scraping only needed
    text info about the query from the 
    above searched urls.
    """
    
    for url in urls:
      # Getting raw webpage from the url.
      self.webpage_response = requests.get(
        url,
        headers=self.HTTP_REQUEST_HEADERS,
        timeout=5
      )
      
      if self.webpage_response.status_code != 200:
        return f"HTTP ERROR > Status code: {self.webpage_response.status_code}"
      
      # Converting raw webpage content into html for scraping.
      self.parsed_webpage_html = BeautifulSoup(
        self.webpage_response.text,
        "html.parser"
      )
      
      # Removing unwanted tags
      for removable_tags in self.parsed_webpage_html(["script", "style"]):
        removable_tags.decompose()
      
      # Taking every paragraph tag from html page
      self.paragraph_elements = self.parsed_webpage_html.find_all("p")
      self.extracted_page_text = ""
      
      # For every top paragraphs, taking all text info only.
      for paragraph_element in self.paragraph_elements[:20]:
        self.extracted_page_chuck = paragraph_element.get_text() + " "
        self.extracted_page_text += self.extracted_page_chuck
      
      # making cleaned and only limited text
      self.cleaned_page_text = " ".join(self.extracted_page_text.split())[:1500]
        
      """# Writing text into text file
      with open(self.KNOWLEDGE_BASE_PATH, "w") as knowledge_base_file:
        knowledge_base_file.write(self.cleaned_page_text)
      """  
      
    return "Scraping Done, Uploaded into knowledge base.", self.cleaned_page_text
        
  @property
  def name(self):
    return "web_scraper"
    
  def execute(self, search_query: any):
    """
    Main wrapper function combining 
    every functions creating a workflow.
    """
    
    page_urls = self.fetch_search_urls(search_query)
    scraping_status, text = self.scrape_page(page_urls)
    #summarized_text = self.summarize_text_content(user_search_query)
    
    if scraping_status:
      return {
        "query": search_query,
        "connector": "web_scraper",
        "web_scraped_text": text
      }