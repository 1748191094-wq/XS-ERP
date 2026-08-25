from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import exp, log1p

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.client import (
    ForumComment,
    ForumFavorite,
    ForumPost,
    ForumPostLike,
    ForumPostSignal,
)


@dataclass(slots=True)
class RecommendationProfile:
    account_id: int | None = None
    category_affinity: dict[int, float] = field(default_factory=dict)
    author_affinity: dict[int, float] = field(default_factory=dict)
    signals: dict[int, ForumPostSignal] = field(default_factory=dict)
    has_history: bool = False


@dataclass(slots=True)
class RankedForumPost:
    post: ForumPost
    score: float
    reason: str


def _recency_weight(created_at: datetime, now: datetime, half_life_days: float = 90.0) -> float:
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - created_at).total_seconds() / 86_400)
    return exp(-age_days / half_life_days)


def load_recommendation_profile(
    db: Session, account_id: int | None, *, now: datetime | None = None
) -> RecommendationProfile:
    profile = RecommendationProfile(account_id=account_id)
    if account_id is None:
        return profile

    now = now or datetime.now(timezone.utc)
    category_affinity: defaultdict[int, float] = defaultdict(float)
    author_affinity: defaultdict[int, float] = defaultdict(float)

    engagement_queries = (
        (
            select(ForumPost.category_id, ForumPost.author_id, ForumPostLike.created_at)
            .join(ForumPostLike, ForumPostLike.post_id == ForumPost.id)
            .where(ForumPostLike.account_id == account_id),
            3.0,
        ),
        (
            select(ForumPost.category_id, ForumPost.author_id, ForumFavorite.created_at)
            .join(ForumFavorite, ForumFavorite.post_id == ForumPost.id)
            .where(ForumFavorite.account_id == account_id),
            4.0,
        ),
        (
            select(ForumPost.category_id, ForumPost.author_id, ForumComment.created_at)
            .join(ForumComment, ForumComment.post_id == ForumPost.id)
            .where(
                ForumComment.author_id == account_id,
                ForumComment.deleted_at.is_(None),
                ForumComment.status == "published",
            ),
            5.0,
        ),
    )
    for stmt, weight in engagement_queries:
        for category_id, author_id, created_at in db.execute(stmt):
            value = weight * _recency_weight(created_at, now)
            category_affinity[category_id] += value
            author_affinity[author_id] += value

    signal_rows = list(
        db.scalars(select(ForumPostSignal).where(ForumPostSignal.account_id == account_id))
    )
    profile.signals = {row.post_id: row for row in signal_rows}
    dwell_rows = db.execute(
        select(
            ForumPost.category_id,
            ForumPost.author_id,
            ForumPostSignal.dwell_time_ms,
            ForumPostSignal.last_dwell_at,
        )
        .join(ForumPostSignal, ForumPostSignal.post_id == ForumPost.id)
        .where(
            ForumPostSignal.account_id == account_id,
            ForumPostSignal.dwell_time_ms >= 2_000,
            ForumPostSignal.not_interested.is_(False),
        )
    )
    for category_id, author_id, dwell_time_ms, last_dwell_at in dwell_rows:
        dwell_strength = min(4.0, log1p(dwell_time_ms / 2_000))
        value = dwell_strength * _recency_weight(last_dwell_at or now, now, 45.0)
        category_affinity[category_id] += value
        author_affinity[author_id] += value * 0.8

    profile.category_affinity = dict(category_affinity)
    profile.author_affinity = dict(author_affinity)
    profile.has_history = bool(category_affinity or author_affinity or signal_rows)
    return profile


def _score_candidate(
    post: ForumPost, profile: RecommendationProfile, now: datetime
) -> RankedForumPost | None:
    signal = profile.signals.get(post.id)
    if signal and signal.not_interested:
        return None

    created_at = post.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    age_hours = max(0.0, (now - created_at).total_seconds() / 3_600)

    # A transparent approximation of X's multi-action scorer for this small forum.
    freshness = 5.5 * exp(-age_hours / 96.0) + 1.2 * exp(-age_hours / 720.0)
    engagement = (
        2.2 * log1p(max(0, post.like_count))
        + 3.4 * log1p(max(0, post.comment_count))
        + 0.35 * log1p(max(0, post.view_count))
    )
    category_interest = profile.category_affinity.get(post.category_id, 0.0)
    author_interest = profile.author_affinity.get(post.author_id, 0.0)
    personalization = 2.2 * log1p(category_interest) + 2.7 * log1p(author_interest)
    editorial = (3.0 if post.is_featured else 0.0) + (8.0 if post.is_pinned else 0.0)
    seen_penalty = 0.0
    if signal:
        seen_penalty = min(5.0, 1.45 * log1p(signal.impression_count))
    exploration = 0.8 if not signal and author_interest == 0 else 0.0
    score = freshness + engagement + personalization + editorial + exploration - seen_penalty

    if author_interest >= 3.0:
        reason = f"因为你常看 {post.author.nickname} 的分享"
    elif category_interest >= 3.0:
        reason = f"因为你关注 {post.category.name}"
    elif post.is_featured:
        reason = "社区精选内容"
    elif post.comment_count >= 3:
        reason = "社区正在热议"
    elif age_hours <= 24:
        reason = "社区新帖"
    else:
        reason = f"探索 {post.category.name}"
    return RankedForumPost(post=post, score=score, reason=reason)


def rank_recommended_posts(
    posts: list[ForumPost],
    profile: RecommendationProfile,
    *,
    now: datetime | None = None,
) -> list[RankedForumPost]:
    """Score independently, then attenuate repeated authors like X Home Mixer."""

    now = now or datetime.now(timezone.utc)
    scored = [item for post in posts if (item := _score_candidate(post, profile, now))]
    scored.sort(key=lambda item: (item.post.is_pinned, item.score, item.post.created_at), reverse=True)

    author_positions: defaultdict[int, int] = defaultdict(int)
    adjusted: list[RankedForumPost] = []
    decay_factor = 0.48
    floor = 0.35
    for item in scored:
        position = author_positions[item.post.author_id]
        author_positions[item.post.author_id] += 1
        multiplier = (1.0 - floor) * (decay_factor**position) + floor
        adjusted.append(
            RankedForumPost(
                post=item.post,
                score=item.score * multiplier,
                reason=item.reason,
            )
        )
    adjusted.sort(
        key=lambda item: (item.post.is_pinned, item.score, item.post.created_at), reverse=True
    )
    return adjusted
