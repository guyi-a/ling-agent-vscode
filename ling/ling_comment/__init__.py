"""Deterministic comment generation for Ling."""

from .pipeline import CommentResult, generate_comment
from .targets import CommentError, CommentTarget, TargetAnalysis

__all__ = [
    "CommentError",
    "CommentResult",
    "CommentTarget",
    "TargetAnalysis",
    "generate_comment",
]
