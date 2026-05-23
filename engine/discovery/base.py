"""
Discovery layer base classes.

Per architecture Section 4.3 — every source produces OpportunityRecord
objects. Classification, fit scoring, exclusion all happen later in
evaluation, never in discovery.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator, Optional


@dataclass
class OpportunityRecord:
    """
    Raw opportunity record produced by a Source.
    
    No classification. No fit scoring. No tier. Those happen in evaluation.
    """
    # Required
    source: str                              # e.g. "jobspy", "greenhouse_api"
    source_url: str                          # canonical posting URL
    employer: str
    title: str
    location: str
    posting_text: str                        # description / body
    date_discovered: datetime
    
    # Optional but commonly present
    source_id: Optional[str] = None          # source-specific identifier
    posted_at: Optional[datetime] = None     # date employer posted (if known)
    is_remote: Optional[bool] = None
    
    # Compensation (if extracted by source)
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_currency: Optional[str] = None
    salary_interval: Optional[str] = None    # yearly, monthly, hourly
    
    # Employer hints from source (refined later in evaluation)
    employer_industry: Optional[str] = None
    
    # Raw payload kept for debugging and re-parsing if parser changes
    raw_payload: dict = field(default_factory=dict)
    
    # Search context (which profile/city/role-type produced this hit)
    search_context: dict = field(default_factory=dict)


@dataclass
class SourceHealth:
    """Health check result for a Source."""
    source: str
    reachable: bool
    last_success: Optional[datetime] = None
    last_error: Optional[str] = None
    notes: Optional[str] = None


class Source(ABC):
    """
    Abstract base for all discovery sources.
    
    Sources only produce records. They never classify, score, or exclude.
    """
    
    name: str = ""  # subclasses override (e.g. "jobspy", "greenhouse_api")
    
    @abstractmethod
    def fetch(self, profile: Any) -> Iterator[OpportunityRecord]:
        """
        Yield raw opportunity records.
        
        `profile` is a Profile dataclass loaded from config/profiles/{p}.yaml.
        For now we accept Any; will tighten when engine/profiles/loader.py exists.
        """
        ...
    
    def health_check(self) -> SourceHealth:
        """Default: assume healthy. Subclasses override for real checks."""
        return SourceHealth(source=self.name, reachable=True)