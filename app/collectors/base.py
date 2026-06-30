from abc import ABC, abstractmethod

from app.schemas import CollectedItem, SourceDefinition


class BaseCollector(ABC):
    @abstractmethod
    def collect(self, source: SourceDefinition) -> list[CollectedItem]:
        raise NotImplementedError

