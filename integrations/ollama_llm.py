import requests
from configs.settings import OLLAMA_ENDPOINT
import json

#OLLAMA_ENDPOINT = "http://localhost:11434/api/chat"

class OllamaModel:
  def __init__(self, model: str = "gpt-oss:120b-cloud"):
    self.system_instruction = ""
    self.MODEL_NAME = model

  def call_model(self, state, system_ins: str = ""):
    information_passage = ""
    
    if state and system_ins:
      information_passage += f"""
      System Prompt:
      {system_ins}
      
      System State:
      {state}
      """
      
    self.payload = {
      "model": self.MODEL_NAME,
      "stream": False,
      "messages": [{
        "role": "user",
        "content": information_passage
      }]
    }
      
    try:
      self.response = requests.post(
        OLLAMA_ENDPOINT,
        json=self.payload,
        timeout=(10, 120)
      
      )
      
      if self.response.status_code != 200:
         print(f"HTTP ERROR (model side) > status: {self.response.status_code} | returned: {self.response}")
        
    except Exception as e:
      return f"REQUEST ERROR: {str(e)}"
    
    data = self.response.json()
  
    if "message" in data:
      message_field = data.get("message", {})
    
      if len(message_field) > 1 and "content" in message_field:
        content_field = message_field.get("content", "")
      
        try:
          #return f"\n{type(content_field)}"
          return json.loads(content_field)
          #return content_field
          #return self.information_passage
        except Exception as e:
          return f"Invaid structure inside model response: \n{str(e)}"
        
      else:
        return f"No content field inside model response: \n{data}"
    else:
      return f"No message field inside model response: \n{data}"