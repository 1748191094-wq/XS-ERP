from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Header, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.api.helpers import ok
from app.core.client_auth import (
    ClientContext,
    get_client_context,
    get_optional_client_context,
)
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import BusinessError
from app.models.client import (
    ClientAttachment,
    ClientNotification,
    ForumCategory,
    ForumComment,
    ForumCommentLike,
    ForumFavorite,
    ForumPost,
    ForumPostLike,
    ForumPostSignal,
    ForumReport,
)
from app.schemas.client import (
    ForumCommentCreate,
    ForumPostCreate,
    ForumPostUpdate,
    ForumReportCreate,
    ForumSignalBatch,
)
from app.services.client_uploads import save_client_image
from app.services.client_profiles import client_avatar_url
from app.services.forum_recommendations import (
    load_recommendation_profile,
    rank_recommended_posts,
)


router = APIRouter(prefix="/api/client/forum", tags=["client-forum"])


def _clean_text(value: str) -> str:
    if "\x00" in value:
        raise BusinessError("内容包含非法字符", code="invalid_content", status_code=400)
    return value.strip()


def _post_data(
    db: Session,
    row: ForumPost,
    account_id: int | None = None,
    *,
    liked: bool | None = None,
    favorited: bool | None = None,
    images: list[ClientAttachment] | None = None,
    recommendation_reason: str | None = None,
) -> dict:
    if liked is None:
        liked = bool(
            account_id
            and db.scalar(
                select(ForumPostLike.id).where(
                    ForumPostLike.post_id == row.id,
                    ForumPostLike.account_id == account_id,
                )
            )
        )
    if favorited is None:
        favorited = bool(
            account_id
            and db.scalar(
                select(ForumFavorite.id).where(
                    ForumFavorite.post_id == row.id,
                    ForumFavorite.account_id == account_id,
                )
            )
        )
    if images is None:
        images = list(
            db.scalars(
                select(ClientAttachment)
                .where(
                    ClientAttachment.resource_type == "forum_post",
                    ClientAttachment.resource_id == row.id,
                )
                .order_by(ClientAttachment.id)
            )
        )
    return {
        "id": row.id,
        "title": row.title,
        "content": row.content,
        "status": row.status,
        "is_pinned": row.is_pinned,
        "is_featured": row.is_featured,
        "view_count": row.view_count,
        "like_count": row.like_count,
        "comment_count": row.comment_count,
        "liked": liked,
        "favorited": favorited,
        "recommendation_reason": recommendation_reason,
        "author": {
            "id": row.author.id,
            "username": row.author.username,
            "identifier": f"@{row.author.username}",
            "nickname": row.author.nickname,
            "avatar_url": client_avatar_url(row.author),
        },
        "category": {
            "id": row.category.id,
            "name": row.category.name,
            "slug": row.category.slug,
        },
        "images": [
            {"id": image.id, "url": f"/api/client/forum/images/{image.id}"}
            for image in images
        ],
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _post_list_data(
    db: Session,
    rows: list[ForumPost],
    account_id: int | None,
    reasons: dict[int, str] | None = None,
) -> list[dict]:
    """Hydrate feed state in three bulk queries instead of per-card queries."""

    if not rows:
        return []
    post_ids = [row.id for row in rows]
    liked_ids: set[int] = set()
    favorite_ids: set[int] = set()
    if account_id is not None:
        liked_ids = set(
            db.scalars(
                select(ForumPostLike.post_id).where(
                    ForumPostLike.account_id == account_id,
                    ForumPostLike.post_id.in_(post_ids),
                )
            )
        )
        favorite_ids = set(
            db.scalars(
                select(ForumFavorite.post_id).where(
                    ForumFavorite.account_id == account_id,
                    ForumFavorite.post_id.in_(post_ids),
                )
            )
        )
    image_map: dict[int, list[ClientAttachment]] = {post_id: [] for post_id in post_ids}
    for image in db.scalars(
        select(ClientAttachment)
        .where(
            ClientAttachment.resource_type == "forum_post",
            ClientAttachment.resource_id.in_(post_ids),
        )
        .order_by(ClientAttachment.resource_id, ClientAttachment.id)
    ):
        image_map.setdefault(image.resource_id, []).append(image)
    reasons = reasons or {}
    return [
        _post_data(
            db,
            row,
            account_id,
            liked=row.id in liked_ids,
            favorited=row.id in favorite_ids,
            images=image_map.get(row.id, []),
            recommendation_reason=reasons.get(row.id),
        )
        for row in rows
    ]


def _comment_data(db: Session, row: ForumComment, account_id: int) -> dict:
    liked = bool(
        db.scalar(
            select(ForumCommentLike.id).where(
                ForumCommentLike.comment_id == row.id,
                ForumCommentLike.account_id == account_id,
            )
        )
    )
    return {
        "id": row.id,
        "post_id": row.post_id,
        "parent_id": row.parent_id,
        "content": row.content,
        "like_count": row.like_count,
        "liked": liked,
        "author": {
            "id": row.author.id,
            "username": row.author.username,
            "identifier": f"@{row.author.username}",
            "nickname": row.author.nickname,
            "avatar_url": client_avatar_url(row.author),
        },
        "created_at": row.created_at,
    }


def _post(db: Session, post_id: int, *, include_hidden: bool = False) -> ForumPost:
    stmt = (
        select(ForumPost)
        .where(ForumPost.id == post_id, ForumPost.deleted_at.is_(None))
        .options(joinedload(ForumPost.author), joinedload(ForumPost.category))
    )
    if not include_hidden:
        stmt = stmt.where(ForumPost.status == "published")
    row = db.scalar(stmt)
    if not row:
        raise BusinessError("帖子不存在", code="forum_post_not_found", status_code=404)
    return row


@router.get("/categories")
def categories(db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(
        select(ForumCategory)
        .where(ForumCategory.enabled.is_(True))
        .order_by(ForumCategory.sort_order, ForumCategory.id)
    )
    return ok(
        [
            {
                "id": row.id,
                "name": row.name,
                "slug": row.slug,
                "description": row.description,
            }
            for row in rows
        ]
    )


@router.get("/posts")
def posts(
    category: str | None = Query(default=None, max_length=100),
    sort: str = Query(default="recommended", pattern="^(recommended|latest|hot)$"),
    q: str = Query(default="", max_length=100),
    page: int = Query(default=1, ge=1, le=10000),
    page_size: int = Query(default=20, ge=1, le=50),
    context: ClientContext | None = Depends(get_optional_client_context),
    db: Session = Depends(get_db),
) -> dict:
    stmt = (
        select(ForumPost)
        .where(ForumPost.status == "published", ForumPost.deleted_at.is_(None))
        .options(joinedload(ForumPost.author), joinedload(ForumPost.category))
    )
    if category:
        stmt = stmt.join(ForumCategory).where(ForumCategory.slug == category)
    if q.strip():
        term = f"%{q.strip()}%"
        stmt = stmt.where(
            ForumPost.title.like(term) | ForumPost.content.like(term)
        )
    account_id = context.account.id if context else None
    if account_id is not None:
        stmt = stmt.where(
            ~ForumPost.id.in_(
                select(ForumPostSignal.post_id).where(
                    ForumPostSignal.account_id == account_id,
                    ForumPostSignal.not_interested.is_(True),
                )
            )
        )
    feed_meta = {
        "strategy": "multi_signal_v1",
        "personalized": False,
        "sources": ["recent", "engaging", "interest"],
        "description": "按内容新鲜度、互动质量与作者多样性排序",
    }
    if sort == "latest":
        stmt = stmt.order_by(ForumPost.is_pinned.desc(), ForumPost.created_at.desc())
    elif sort == "hot":
        stmt = stmt.order_by(
            ForumPost.is_pinned.desc(),
            (ForumPost.like_count + ForumPost.comment_count * 2 + ForumPost.view_count / 20).desc(),
            ForumPost.created_at.desc(),
        )
    else:
        profile = load_recommendation_profile(db, account_id)
        source_rows = [
            list(
                db.scalars(stmt.order_by(ForumPost.created_at.desc()).limit(300)).unique()
            ),
            list(
                db.scalars(
                    stmt.order_by(
                        (
                            ForumPost.like_count * 3
                            + ForumPost.comment_count * 5
                            + ForumPost.view_count / 20
                        ).desc(),
                        ForumPost.created_at.desc(),
                    ).limit(200)
                ).unique()
            ),
        ]
        interest_filters = []
        if profile.category_affinity:
            top_categories = sorted(
                profile.category_affinity,
                key=profile.category_affinity.get,
                reverse=True,
            )[:8]
            interest_filters.append(ForumPost.category_id.in_(top_categories))
        if profile.author_affinity:
            top_authors = sorted(
                profile.author_affinity,
                key=profile.author_affinity.get,
                reverse=True,
            )[:20]
            interest_filters.append(ForumPost.author_id.in_(top_authors))
        if interest_filters:
            interest_predicate = interest_filters[0]
            for predicate in interest_filters[1:]:
                interest_predicate = interest_predicate | predicate
            source_rows.append(
                list(
                    db.scalars(
                        stmt.where(interest_predicate)
                        .order_by(ForumPost.created_at.desc())
                        .limit(250)
                    ).unique()
                )
            )
        candidate_map = {
            candidate.id: candidate
            for source in source_rows
            for candidate in source
        }
        candidates = list(candidate_map.values())
        ranked = rank_recommended_posts(candidates, profile)
        page_items = ranked[(page - 1) * page_size : page * page_size]
        rows = [item.post for item in page_items]
        reasons = {item.post.id: item.reason for item in page_items}
        feed_meta["personalized"] = bool(account_id and profile.has_history)
        if feed_meta["personalized"]:
            feed_meta["description"] = "已结合你的阅读、喜欢、收藏和回复，并保持作者多样性"
        return ok(
            {
                "items": _post_list_data(db, rows, account_id, reasons),
                "page": page,
                "page_size": page_size,
                "feed": feed_meta,
            }
        )
    rows = list(
        db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)).unique()
    )
    return ok(
        {
            "items": _post_list_data(db, rows, account_id),
            "page": page,
            "page_size": page_size,
            "feed": feed_meta,
        }
    )


@router.post("/signals")
def record_feed_signals(
    payload: ForumSignalBatch,
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    post_ids = [item.post_id for item in payload.items]
    valid_ids = set(
        db.scalars(
            select(ForumPost.id).where(
                ForumPost.id.in_(post_ids),
                ForumPost.status == "published",
                ForumPost.deleted_at.is_(None),
            )
        )
    )
    if valid_ids != set(post_ids):
        raise BusinessError("帖子不存在", code="forum_post_not_found", status_code=404)

    existing = {
        row.post_id: row
        for row in db.scalars(
            select(ForumPostSignal).where(
                ForumPostSignal.account_id == context.account.id,
                ForumPostSignal.post_id.in_(post_ids),
            )
        )
    }
    now = datetime.now(timezone.utc)
    for item in payload.items:
        row = existing.get(item.post_id)
        if row is None:
            row = ForumPostSignal(
                post_id=item.post_id,
                account_id=context.account.id,
                impression_count=0,
                dwell_time_ms=0,
                not_interested=False,
            )
            db.add(row)
            existing[item.post_id] = row
        if item.impression:
            row.impression_count += 1
            row.last_impression_at = now
        if item.dwell_time_ms:
            row.dwell_time_ms = min(86_400_000, row.dwell_time_ms + item.dwell_time_ms)
            row.last_dwell_at = now
        if item.not_interested is not None:
            row.not_interested = item.not_interested
            row.not_interested_at = now if item.not_interested else None
    db.commit()
    return ok({"recorded": len(payload.items)})


@router.post("/posts", status_code=201)
def create_post(
    payload: ForumPostCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=100),
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    existing = db.scalar(
        select(ForumPost).where(ForumPost.idempotency_key == idempotency_key)
    )
    if existing:
        if existing.author_id != context.account.id:
            raise BusinessError("重复请求标识冲突", code="idempotency_conflict", status_code=409)
        return ok(_post_data(db, existing, context.account.id))
    category = db.scalar(
        select(ForumCategory).where(
            ForumCategory.id == payload.category_id, ForumCategory.enabled.is_(True)
        )
    )
    if not category:
        raise BusinessError("社区分类不存在", code="forum_category_not_found", status_code=404)
    row = ForumPost(
        author_id=context.account.id,
        category_id=category.id,
        title=_clean_text(payload.title),
        content=_clean_text(payload.content),
        status="published",
        idempotency_key=idempotency_key,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    row.author = context.account
    row.category = category
    return ok(_post_data(db, row, context.account.id))


@router.get("/posts/{post_id}")
def post_detail(
    post_id: int,
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    row = _post(db, post_id)
    row.view_count += 1
    db.commit()
    comments = list(
        db.scalars(
            select(ForumComment)
            .where(
                ForumComment.post_id == post_id,
                ForumComment.status == "published",
                ForumComment.deleted_at.is_(None),
            )
            .options(joinedload(ForumComment.author))
            .order_by(ForumComment.created_at)
        ).unique()
    )
    return ok(
        {
            "post": _post_data(db, row, context.account.id),
            "comments": [_comment_data(db, item, context.account.id) for item in comments],
        }
    )


@router.patch("/posts/{post_id}")
def update_post(
    post_id: int,
    payload: ForumPostUpdate,
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    row = _post(db, post_id, include_hidden=True)
    if row.author_id != context.account.id:
        raise BusinessError("只能修改自己的帖子", code="forum_post_owner_required", status_code=403)
    values = payload.model_dump(exclude_unset=True)
    if "category_id" in values:
        category = db.scalar(
            select(ForumCategory).where(
                ForumCategory.id == values["category_id"], ForumCategory.enabled.is_(True)
            )
        )
        if not category:
            raise BusinessError("社区分类不存在", code="forum_category_not_found", status_code=404)
        row.category_id = category.id
        row.category = category
    if values.get("title") is not None:
        row.title = _clean_text(values["title"])
    if values.get("content") is not None:
        row.content = _clean_text(values["content"])
    db.commit()
    return ok(_post_data(db, row, context.account.id))


@router.delete("/posts/{post_id}")
def delete_post(
    post_id: int,
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    row = _post(db, post_id, include_hidden=True)
    if row.author_id != context.account.id:
        raise BusinessError("只能删除自己的帖子", code="forum_post_owner_required", status_code=403)
    row.deleted_at = datetime.now(timezone.utc)
    row.deleted_by_client_id = context.account.id
    db.commit()
    return ok({"deleted": True})


@router.post("/posts/{post_id}/comments", status_code=201)
def create_comment(
    post_id: int,
    payload: ForumCommentCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=100),
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    post = _post(db, post_id)
    existing = db.scalar(
        select(ForumComment).where(ForumComment.idempotency_key == idempotency_key)
    )
    if existing:
        if existing.author_id != context.account.id:
            raise BusinessError("重复请求标识冲突", code="idempotency_conflict", status_code=409)
        return ok(_comment_data(db, existing, context.account.id))
    parent = None
    if payload.parent_id:
        parent = db.scalar(
            select(ForumComment).where(
                ForumComment.id == payload.parent_id,
                ForumComment.post_id == post.id,
                ForumComment.deleted_at.is_(None),
            )
        )
        if not parent:
            raise BusinessError("回复的评论不存在", code="forum_comment_not_found", status_code=404)
        if parent.parent_id is not None:
            raise BusinessError("第一阶段仅支持一级回复", code="forum_reply_depth_exceeded", status_code=400)
    row = ForumComment(
        post_id=post.id,
        author_id=context.account.id,
        parent_id=parent.id if parent else None,
        content=_clean_text(payload.content),
        idempotency_key=idempotency_key,
    )
    db.add(row)
    post.comment_count += 1
    target_account_id = parent.author_id if parent else post.author_id
    if target_account_id != context.account.id:
        db.add(
            ClientNotification(
                account_id=target_account_id,
                notification_type="forum_reply",
                title="社区有新回复",
                content=f"{context.account.nickname} 回复了你的内容。",
                resource_type="forum_post",
                resource_id=post.id,
            )
        )
    db.commit()
    db.refresh(row)
    row.author = context.account
    return ok(_comment_data(db, row, context.account.id))


@router.delete("/comments/{comment_id}")
def delete_comment(
    comment_id: int,
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    row = db.scalar(
        select(ForumComment).where(
            ForumComment.id == comment_id, ForumComment.deleted_at.is_(None)
        )
    )
    if not row:
        raise BusinessError("评论不存在", code="forum_comment_not_found", status_code=404)
    if row.author_id != context.account.id:
        raise BusinessError("只能删除自己的评论", code="forum_comment_owner_required", status_code=403)
    row.deleted_at = datetime.now(timezone.utc)
    row.deleted_by_client_id = context.account.id
    post = db.get(ForumPost, row.post_id)
    if post:
        post.comment_count = max(0, post.comment_count - 1)
    db.commit()
    return ok({"deleted": True})


@router.post("/posts/{post_id}/like")
def toggle_post_like(
    post_id: int,
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    post = _post(db, post_id)
    row = db.scalar(
        select(ForumPostLike).where(
            ForumPostLike.post_id == post.id,
            ForumPostLike.account_id == context.account.id,
        )
    )
    if row:
        db.delete(row)
        post.like_count = max(0, post.like_count - 1)
        liked = False
    else:
        db.add(ForumPostLike(post_id=post.id, account_id=context.account.id))
        post.like_count += 1
        liked = True
    db.commit()
    return ok({"liked": liked, "like_count": post.like_count})


@router.post("/comments/{comment_id}/like")
def toggle_comment_like(
    comment_id: int,
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    comment = db.scalar(
        select(ForumComment).where(
            ForumComment.id == comment_id,
            ForumComment.status == "published",
            ForumComment.deleted_at.is_(None),
        )
    )
    if not comment:
        raise BusinessError("评论不存在", code="forum_comment_not_found", status_code=404)
    row = db.scalar(
        select(ForumCommentLike).where(
            ForumCommentLike.comment_id == comment.id,
            ForumCommentLike.account_id == context.account.id,
        )
    )
    if row:
        db.delete(row)
        comment.like_count = max(0, comment.like_count - 1)
        liked = False
    else:
        db.add(ForumCommentLike(comment_id=comment.id, account_id=context.account.id))
        comment.like_count += 1
        liked = True
    db.commit()
    return ok({"liked": liked, "like_count": comment.like_count})


@router.post("/posts/{post_id}/favorite")
def toggle_favorite(
    post_id: int,
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    post = _post(db, post_id)
    row = db.scalar(
        select(ForumFavorite).where(
            ForumFavorite.post_id == post.id,
            ForumFavorite.account_id == context.account.id,
        )
    )
    if row:
        db.delete(row)
        favorited = False
    else:
        db.add(ForumFavorite(post_id=post.id, account_id=context.account.id))
        favorited = True
    db.commit()
    return ok({"favorited": favorited})


@router.post("/reports", status_code=201)
def report_content(
    payload: ForumReportCreate,
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    if payload.post_id:
        _post(db, payload.post_id)
    if payload.comment_id and not db.scalar(
        select(ForumComment.id).where(
            ForumComment.id == payload.comment_id,
            ForumComment.deleted_at.is_(None),
        )
    ):
        raise BusinessError("评论不存在", code="forum_comment_not_found", status_code=404)
    existing = db.scalar(
        select(ForumReport.id).where(
            ForumReport.post_id == payload.post_id,
            ForumReport.comment_id == payload.comment_id,
            ForumReport.reporter_id == context.account.id,
        )
    )
    if existing:
        raise BusinessError("已经举报过该内容", code="forum_report_exists", status_code=409)
    row = ForumReport(
        post_id=payload.post_id,
        comment_id=payload.comment_id,
        reporter_id=context.account.id,
        reason=_clean_text(payload.reason),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return ok({"id": row.id, "status": row.status})


@router.get("/me/posts")
def my_posts(
    context: ClientContext = Depends(get_client_context), db: Session = Depends(get_db)
) -> dict:
    rows = db.scalars(
        select(ForumPost)
        .where(ForumPost.author_id == context.account.id, ForumPost.deleted_at.is_(None))
        .options(joinedload(ForumPost.author), joinedload(ForumPost.category))
        .order_by(ForumPost.created_at.desc())
    )
    return ok([_post_data(db, row, context.account.id) for row in rows])


@router.get("/me/favorites")
def my_favorites(
    context: ClientContext = Depends(get_client_context), db: Session = Depends(get_db)
) -> dict:
    rows = db.scalars(
        select(ForumPost)
        .join(ForumFavorite, ForumFavorite.post_id == ForumPost.id)
        .where(
            ForumFavorite.account_id == context.account.id,
            ForumPost.status == "published",
            ForumPost.deleted_at.is_(None),
        )
        .options(joinedload(ForumPost.author), joinedload(ForumPost.category))
        .order_by(ForumFavorite.created_at.desc())
    )
    return ok([_post_data(db, row, context.account.id) for row in rows.unique()])


@router.post("/posts/{post_id}/images", status_code=201)
async def upload_post_image(
    post_id: int,
    file: UploadFile = File(...),
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    post = _post(db, post_id, include_hidden=True)
    if post.author_id != context.account.id:
        raise BusinessError("只能上传到自己的帖子", code="forum_post_owner_required", status_code=403)
    count = db.scalar(
        select(func.count(ClientAttachment.id)).where(
            ClientAttachment.account_id == context.account.id,
            ClientAttachment.resource_type == "forum_post",
            ClientAttachment.resource_id == post.id,
        )
    ) or 0
    if count >= min(9, settings.client_max_uploads_per_resource):
        raise BusinessError("帖子图片数量已达上限", code="attachment_limit_reached", status_code=409)
    content = await file.read(settings.client_max_image_bytes + 1)
    stored = save_client_image(
        filename=file.filename or "image.jpg",
        content_type=file.content_type,
        content=content,
        folder=f"forum/{post.id}",
    )
    row = ClientAttachment(
        account_id=context.account.id,
        resource_type="forum_post",
        resource_id=post.id,
        attachment_type="image",
        original_filename=file.filename or stored.original_filename,
        storage_path=stored.storage_path,
        content_type=stored.content_type,
        file_size=stored.file_size,
        sha256=stored.sha256,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return ok({"id": row.id, "url": f"/api/client/forum/images/{row.id}"})


@router.get("/images/{attachment_id}")
def forum_image(attachment_id: int, db: Session = Depends(get_db)):
    row = db.scalar(
        select(ClientAttachment)
        .join(ForumPost, ForumPost.id == ClientAttachment.resource_id)
        .where(
            ClientAttachment.id == attachment_id,
            ClientAttachment.resource_type == "forum_post",
            ForumPost.status == "published",
            ForumPost.deleted_at.is_(None),
        )
    )
    if not row:
        raise BusinessError("图片不存在", code="forum_image_not_found", status_code=404)
    from fastapi.responses import FileResponse
    from app.storage.local import LocalStorageService

    path = LocalStorageService().absolute_path(row.storage_path)
    if not path.is_file():
        raise BusinessError("图片文件已丢失", code="forum_image_missing", status_code=404)
    return FileResponse(path, media_type=row.content_type)
