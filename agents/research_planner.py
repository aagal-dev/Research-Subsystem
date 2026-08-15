from integrations.ollama_llm import OllamaModel
from services.time_thread import get_live_time

model = OllamaModel()

with open("prompts/research_planner_prompt(1).txt") as ins_file:
  research_planner_prompt = ins_file.read()

def invoke_research_planner(user):
  temporal_agent_state = {
    "time_and_date": get_live_time(),
    "query": user
  }
  
  response = model.call_model(
    system_ins=research_planner_prompt,
    state=temporal_agent_state
  )
  
  return response