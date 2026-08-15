from .base_connector import BaseConnector

class ResearchPapersConnector(BaseConnector):
  @property
  def name(self):
    return "research_papers"
    
  def execute(self, query):
    
    return {
      "connector": self.name,
      "query": query,
      "results": {
        "r1": "paper_1",
        "r2": "paper2"
      }
    }