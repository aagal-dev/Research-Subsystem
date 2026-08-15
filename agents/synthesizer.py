from services.time_thread import get_live_time
#from integrations.open_router_client import OpenRouterClient
from integrations.ollama_llm import OllamaModel

#Client = OpenRouterClient()
model = OllamaModel()

with open("prompts/synthesizer_prompt.txt") as ins_file:
  synthesizer_prompt = ins_file.read()

def synthesize(connector_results, query):
  temporal_agent_state = {
    "time_and_date": get_live_time(),
    "connector_results": connector_results,
    "searched_query": query
  }
  
  #print(temporal_agent_state)
  
  response = model.call_model(
    system_ins=synthesizer_prompt, 
    state=temporal_agent_state
  )
  
  if response:
    return response
  else:
    return f"Synthesizer faild: {response}"