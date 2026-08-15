from .base_connector import BaseConnector

class GitHubConnector(BaseConnector):
  @property
  def name(self):
    return "github"
    
  def execute(self, query):
    
    return {
      "connector": self.name,
      "query": query,
      "results": {
        "r1": "repo_1",
        "r2": "repo_12"
      }
    }