class ConnectorRegistry:
  
  def __init__(self):
    self.__connectors__ = {}
    
  def register(self, connector):
    self.__connectors__[connector.name] = connector
    
  def get(self, name):
      return self.__connectors__[name]
      
  def get_many(self, names: list) -> list:
    return [self.get(name) for name in names]
    
  def avaliable(self):
    return self.__connectors__
    

