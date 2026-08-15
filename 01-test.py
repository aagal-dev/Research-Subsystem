from connectors.web_scraper_v3_1 import WebScraperConnector

import json

web_scraper = WebScraperConnector()

results = web_scraper.execute(
  search_query=""
)

print(json.dumps(results, indent=2))