"""Built-in email parsers + factory."""
from engine.discovery.email_parsers.linkedin_parser import LinkedInParser
from engine.discovery.email_parsers.newsletter_parser import NewsletterParser
from engine.discovery.email_parsers.recruiter_parser import RecruiterParser

__all__ = [
    "LinkedInParser",
    "NewsletterParser",
    "RecruiterParser",
    "default_parsers",
]


def default_parsers(
    recruiter_domains: list[str],
    newsletter_senders: list[str],
):
    """Order matters: more specific matchers first."""
    return [
        LinkedInParser(),
        RecruiterParser(domains=recruiter_domains),
        NewsletterParser(senders=newsletter_senders),
    ]
