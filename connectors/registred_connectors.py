# Connector runtime imports 
from connectors.connector_registry import ConnectorRegistry

# Connectors imports
from connectors.github import GitHubConnector
from connectors.news import NewsConnector
from connectors.web_scraper_v3_fixed import WebScraperConnector
from connectors.research_papers import ResearchPapersConnector
from connectors.youtube import YouTubeConnector

connector_registry = ConnectorRegistry()
    
# Registering connectors
connector_registry.register(GitHubConnector())
connector_registry.register(NewsConnector())
connector_registry.register(WebScraperConnector())
connector_registry.register(ResearchPapersConnector())
connector_registry.register(YouTubeConnector())
