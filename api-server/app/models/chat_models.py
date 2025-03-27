from typing import List, Dict, Optional
from dataclasses import dataclass, asdict, field

@dataclass
class ChatRequest:
    question: str
    
    def to_dict(self):
        return asdict(self)

@dataclass
class SourceContent:
    source: str
    contents: List[str]
    
    def to_dict(self):
        return asdict(self)

@dataclass
class ChatResponse:
    answer: str
    sources: List[SourceContent] = field(default_factory=list)
    
    def to_dict(self):
        return {
            "answer": self.answer,
            "sources": [source.to_dict() for source in self.sources]
        } 