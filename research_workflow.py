# Agents 
from agents.research_planner import invoke_research_planner
from agents.research_executor import invoke_research_executor
from agents.synthesizer import synthesize

# Connector runtime imports 
from connectors.registred_connectors import connector_registry
from connectors.connector_runtime import execute_connectors

import json

def invoke_research_subsystem(user_query: str):
  
  plan = invoke_research_planner(user_query)
  
  print(f"\nBreakdowned Subqueries: \n{plan}\n")
  
  execution_res = invoke_research_executor(plan)
  
  print(f"\nExecution Plan: \n{execution_res}\n")
  
  # Collection of all connector results
  all_results = []
  
  if isinstance(execution_res, dict) and "sq_1" in execution_res:
    for sub_query in execution_res.values():
      for execution in sub_query["executions"]:
          query = execution["query"]
          connectors = connector_registry.get_many(execution["connectors"])
          
          print(f"\nQuery > '{query}' | Coonnectors > {[c.name for c in connectors]}")
      
          print("\nRunning parallel execution...")
          
          results = execute_connectors(connectors, query)
          
          all_results.extend(results)
      
  else:
    print(f"\nNo execution_res inside executor response: \n{execution_res}")
  
  #print("\n\nRESULTS OF CONNECTORS\n\n")
  print(all_results)
  # SYNTHSIZING CONNECTORS RESULTS PER SUBQUERY
  synthesized_results = synthesize(
    connector_results=all_results,
    query=user_query
  )
  
  return synthesized_results
  
  #return None