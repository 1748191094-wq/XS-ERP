from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_community_search_is_a_real_form_and_latest_request_wins():
    community = source("client/src/views/CommunityView.vue")

    assert 'aria-label="搜索社区帖子"' in community
    assert '@click="focusSearch"' in community
    assert 'ref="searchInput"' in community
    assert "searchInput.value?.focus({preventScroll:true})" in community
    assert 'role="search" @submit.prevent="submitSearch"' in community
    assert 'type="submit"' in community
    assert 'aria-label="清除搜索"' in community
    assert '找到 {{posts.length}} 条帖子' in community
    assert "feedRequest?.abort()" in community
    assert "requestSequence !== feedRequestSequence" in community
    assert "isRequestCancelled(e)" in community
    assert "loadingFeed && hasLoaded" in community


def test_client_recovers_from_stale_lazy_chunks_and_shows_navigation_progress():
    router = source("client/src/router/index.ts")
    vite = source("client/vite.config.ts")
    interactions = source("client/src/assets/interactions.css")

    assert "router.onError" in router
    assert "DYNAMIC_IMPORT_ERROR" in router
    assert "window.location.replace(clientRouteUrl(to.fullPath))" in router
    assert "client-navigating" in router
    assert "emptyOutDir: false" in vite
    assert "html.client-navigating::before" in interactions


def test_expired_session_clears_client_state_without_a_refresh():
    api = source("client/src/api/client.ts")
    main = source("client/src/main.ts")
    auth = source("client/src/stores/auth.ts")

    assert "CLIENT_SESSION_EXPIRED_EVENT" in api
    assert "request_cancelled" in api
    assert "window.addEventListener(CLIENT_SESSION_EXPIRED_EVENT" in main
    assert "auth.clearSession()" in main
    assert "clearSession()" in auth


def test_desktop_community_columns_scroll_independently_without_affecting_post_page():
    layout = source("client/src/layouts/ClientLayout.vue")
    community_styles = source("client/src/assets/community.css")

    assert "const communityFeedPage = computed(() => route.name === 'community')" in layout
    assert "'page-community-feed': communityFeedPage" in layout
    assert ".page-container.page-community-feed" in community_styles
    assert "height:calc(100dvh - 68px)" in community_styles
    assert ".page-community-feed .x-feed" in community_styles
    assert ".page-community-feed .x-community-aside" in community_styles
    assert "overflow-y:auto" in community_styles
    assert "overscroll-behavior-y:contain" in community_styles
