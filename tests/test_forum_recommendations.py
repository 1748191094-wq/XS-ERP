from datetime import datetime, timedelta, timezone

from app.models.client import ClientAccount, ForumCategory, ForumPost
from app.services.forum_recommendations import (
    RecommendationProfile,
    rank_recommended_posts,
)


def _post(
    post_id: int,
    author: ClientAccount,
    category: ForumCategory,
    *,
    likes: int,
    created_at: datetime,
) -> ForumPost:
    return ForumPost(
        id=post_id,
        author_id=author.id,
        category_id=category.id,
        title=f"帖子 {post_id}",
        content="用于验证作者多样性重排",
        status="published",
        is_pinned=False,
        is_featured=False,
        view_count=30,
        like_count=likes,
        comment_count=2,
        created_at=created_at,
        updated_at=created_at,
        author=author,
        category=category,
    )


def test_recommendation_reranks_repeated_authors_for_feed_diversity():
    now = datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc)
    category = ForumCategory(id=1, name="维修交流", slug="repair", enabled=True)
    author_a = ClientAccount(id=1, nickname="作者甲")
    author_b = ClientAccount(id=2, nickname="作者乙")
    candidates = [
        _post(1, author_a, category, likes=8, created_at=now - timedelta(minutes=5)),
        _post(2, author_a, category, likes=7, created_at=now - timedelta(minutes=6)),
        _post(3, author_b, category, likes=6, created_at=now - timedelta(minutes=7)),
    ]

    ranked = rank_recommended_posts(candidates, RecommendationProfile(), now=now)

    assert [item.post.id for item in ranked] == [1, 3, 2]
    assert all(item.reason for item in ranked)
