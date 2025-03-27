from typing import List, Dict, Optional
from dataclasses import dataclass

@dataclass
class ChatRequest:
    question: str

@dataclass
class SourceContent:
    source: str
    contents: List[str]

@dataclass
class ChatResponse:
    answer: str
    sources: List[SourceContent] 