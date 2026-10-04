"""以**用户身份**调飞书 OpenAPI（云文档 / 知识库 / 搜索）。只做 HTTP，不碰库。

所有请求都带 user_access_token：可读范围 = 员工本人在飞书里的权限（方案 §1.2）。
本文件里没有、也不许出现 tenant_access_token。

错误统一成 FeishuApiError(kind, code)，kind 是有限枚举，路由层据此给 Agent 翻译成人话。
响应体不进日志、不进异常消息（rag-service 那行日志的教训）。
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

BASE_URL = "https://open.feishu.cn/open-apis"
TIMEOUT_SECONDS = 15.0
# 飞书 5xx / 超时退避一次（方案 §5.3 错误映射）。测试置 0。
RETRY_BACKOFF_SECONDS = 0.5

KIND_FORBIDDEN = "forbidden"
KIND_NOT_FOUND = "not_found"
KIND_SCOPE_MISSING = "scope_missing"
KIND_TOKEN_INVALID = "token_invalid"
KIND_UNAVAILABLE = "unavailable"
KIND_BAD_REQUEST = "bad_request"

# 飞书错误码 → kind。没列到的非 0 码归 bad_request（不重试、如实报错码）。
_CODE_KINDS = {
    # 云文档：当前用户没有该文档权限
    "1770032": KIND_FORBIDDEN,
    # 知识库：没有知识空间 / 节点的阅读权限
    "131006": KIND_FORBIDDEN,
    # 文档 / 节点不存在（或已删除）
    "1770002": KIND_NOT_FOUND,
    "131005": KIND_NOT_FOUND,
    # 应用 scope 不足：响应 error.permission_violations 里列出缺的权限
    "99991679": KIND_SCOPE_MISSING,
    # access_token 无效 / 过期：强制刷新后再试一次
    "99991668": KIND_TOKEN_INVALID,
    "99991677": KIND_TOKEN_INVALID,
    # 频控 / 服务端错误
    "99991400": KIND_UNAVAILABLE,
    "1770001": KIND_BAD_REQUEST,
}


class FeishuApiError(Exception):
    def __init__(self, kind: str, code: str = "", violations: Optional[list[str]] = None):
        super().__init__(f"feishu api failed: {kind} {code}".strip())
        self.kind = kind
        self.code = code
        # scope 不足时缺的权限名（飞书给的是 scope 字符串，不是敏感信息）
        self.violations = violations or []


@dataclass(frozen=True)
class WikiNode:
    node_token: str
    obj_token: str
    obj_type: str
    title: str
    space_id: str
    has_child: bool


@dataclass(frozen=True)
class SearchHit:
    token: str
    doc_type: str
    title: str
    owner_id: str = ""


@dataclass(frozen=True)
class SearchResult:
    hits: list[SearchHit] = field(default_factory=list)
    has_more: bool = False
    total: int = 0


async def _call(method: str, path: str, access_token: str, **kwargs: Any) -> dict:
    """发一次请求，返回 data。5xx / 网络错误退避重试一次。"""
    for attempt in (0, 1):
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                resp = await client.request(
                    method, BASE_URL + path, headers={"Authorization": f"Bearer {access_token}"}, **kwargs
                )
        except httpx.HTTPError:
            if attempt == 0:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS)
                continue
            raise FeishuApiError(KIND_UNAVAILABLE, "network") from None
        if resp.status_code >= 500:
            if attempt == 0:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS)
                continue
            raise FeishuApiError(KIND_UNAVAILABLE, f"http_{resp.status_code}")
        try:
            body = resp.json()
        except ValueError:
            raise FeishuApiError(KIND_UNAVAILABLE, "bad_body") from None
        if not isinstance(body, dict):
            raise FeishuApiError(KIND_UNAVAILABLE, "bad_body")
        code = body.get("code")
        if code == 0:
            return body.get("data") or {}
        code_s = str(code)
        kind = _CODE_KINDS.get(code_s, KIND_BAD_REQUEST)
        violations = []
        if kind == KIND_SCOPE_MISSING:
            err = body.get("error") or {}
            for v in err.get("permission_violations") or []:
                subject = v.get("subject") if isinstance(v, dict) else None
                if subject:
                    violations.append(str(subject))
        raise FeishuApiError(kind, code_s, violations)
    raise FeishuApiError(KIND_UNAVAILABLE, "retry_exhausted")  # pragma: no cover


async def docx_raw_content(access_token: str, document_id: str) -> str:
    data = await _call("GET", f"/docx/v1/documents/{document_id}/raw_content", access_token, params={"lang": 0})
    return data.get("content") or ""


async def wiki_get_node(access_token: str, token: str) -> WikiNode:
    data = await _call("GET", "/wiki/v2/spaces/get_node", access_token, params={"token": token, "obj_type": "wiki"})
    node = data.get("node") or {}
    if not node:
        raise FeishuApiError(KIND_NOT_FOUND, "empty_node")
    return WikiNode(
        node_token=node.get("node_token") or token,
        obj_token=node.get("obj_token") or "",
        obj_type=node.get("obj_type") or "",
        title=node.get("title") or "",
        space_id=str(node.get("space_id") or ""),
        has_child=bool(node.get("has_child")),
    )


async def search_docs(access_token: str, query: str, count: int) -> SearchResult:
    """云文档搜索。只支持 user_access_token —— 结果天然只含本人有权限的文档。"""
    data = await _call(
        "POST",
        "/suite/docs-api/search/object",
        access_token,
        json={"search_key": query, "count": count, "offset": 0},
    )
    hits = [
        SearchHit(
            token=e.get("docs_token") or "",
            doc_type=e.get("docs_type") or "",
            title=e.get("title") or "",
            owner_id=e.get("owner_id") or "",
        )
        for e in data.get("docs_entities") or []
        if isinstance(e, dict)
    ]
    return SearchResult(hits=hits, has_more=bool(data.get("has_more")), total=int(data.get("total") or len(hits)))
