from research_workflow import invoke_research_subsystem
#from agents.conversation_agent import call_conversation_agent

import json

user = input("\nRESEARCH (type a query, topic to research > ")

research_report = invoke_research_subsystem(user_query=user)

print(f"\nRESEARCH RESULTS: \n{json.dumps(research_report, indent=2)}")

#response = call_conversation_agent(user, research_report)
#print(response)