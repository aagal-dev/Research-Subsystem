from integrations.ollama_llm import OllamaModel
from services.time_thread import get_live_time

model = OllamaModel()

with open("prompts/research_executor_prompt(1).txt") as ins_file:
  research_executor_prompt = ins_file.read()

available_connectors = [
    {
        "name": "web_scraper",
        "description": "Find relevant URLs for a query and scrape content from the top 5–10 results."
    },
    {
        "name": "youtube_transcripts",
        "description": "given a query, the connector finds relevant YouTube videos and return their transcripts."
    },
    {
        "name": "research_papers",
        "description": "Search for relevant academic and scientific research papers."
    }
]

def invoke_research_executor(plan):
  temporal_agent_state = {
    "time_and_date": get_live_time(),
    "plan": plan,
    "available_connectors": available_connectors
  }
  
  #print(temporal_agent_state)
  
  response = model.call_model(
    system_ins=research_executor_prompt,
    state=temporal_agent_state
  )
  
  return response