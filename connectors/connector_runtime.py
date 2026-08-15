from concurrent.futures import ThreadPoolExecutor, as_completed

#from connectors.connector_result_cleaner import clean_connector_results

def execute_connectors(connectors: list, query: str) -> list:
    """
    Starts all connectors in parallel
    and collect results.
    
    Return: list
    """
    
    results = []

    with ThreadPoolExecutor(max_workers=len(connectors)) as executor:
      futures = {
          executor.submit(connector.execute, query): connector
          for connector in connectors
      }

      for future in as_completed(futures):
        connector = futures[future]

        try:
          result = future.result()

          results.append(result)

        except Exception as e:
          results.append({
            "connector": connector.name,
            "query": query,
            "error": str(e)
          })
          
    
    #cleaned_connector_results = clean_connector_results(results)
    return results
    