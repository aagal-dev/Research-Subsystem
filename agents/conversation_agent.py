#from integrations.gemini_llm import call_gemini_model
from integrations.ollama_llm import OllamaModel
#from integrations.open_router_models import OpenRouterModel

import json

model = OllamaModel()

with open("./prompts/conversation_agent_instruction.txt") as ins_file:
  conversation_agent_ins = ins_file.read()

def call_conversation_agent(user, results):
  temporal_agent_state = {
    "user_input": user,
    "results": results
  }
  
  response = model.call_model(
    temporal_agent_state,
    conversation_agent_ins
  )
    
  if "response" in response:
    #print(f"\nConversation agent response: \n{response} | returned_type: {type(response)}\n")
    return response.get("response")
    #return response
  else:
    return f"\nNo response field inside conversation agent: \n{response}\n"
    
