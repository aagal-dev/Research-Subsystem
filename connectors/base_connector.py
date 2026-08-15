from abc import ABC, abstractmethod

class BaseConnector(ABC):
  
  @property
  @abstractmethod
  def name(self):
    pass
  
  @abstractmethod
  def execute(self, task: dict):
    pass
  