from research_workflow import invoke_research_subsystem

import json

user = input("\nRESEARCH (type a query, topic to research > ")

response = invoke_research_subsystem(user_query=user)

print(f"\nRESEARCH RESULTS: \n{json.dumps(response, indent=2)}")
