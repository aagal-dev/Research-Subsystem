from .base_connector import BaseConnector

class YouTubeConnector(BaseConnector):
  @property
  def name(self):
    return "youtube_transcripts"
    
  def execute(self, query):
    
    return {
      "connector": self.name,
      "query": query,
      "results": {
        "r1": "transcript_1",
        "r2": "transcript_2"
      }
    }