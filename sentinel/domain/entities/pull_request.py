from dataclasses import dataclass


@dataclass
class PullRequest:
    repo: str
    pr_number: int
