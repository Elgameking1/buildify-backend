"""Shared pagination primitives.

Every list endpoint accepts the same `?page=&size=` parameters and returns the
same envelope, so the React client can use one generic hook for all of them.
"""

import math
from collections.abc import Sequence
from typing import Annotated, Generic, TypeVar

from fastapi import Depends, Query
from pydantic import BaseModel

T = TypeVar("T")

MAX_PAGE_SIZE = 100


class PageParams(BaseModel):
    page: int = 1
    size: int = 20

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size

    @property
    def limit(self) -> int:
        return self.size


def page_params(
    page: Annotated[int, Query(ge=1, description="1-indexed page number")] = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE, description="Items per page")] = 20,
) -> PageParams:
    return PageParams(page=page, size=size)


PageParamsDep = Annotated[PageParams, Depends(page_params)]


class Page(BaseModel, Generic[T]):  # noqa: UP046 - see the docstring below
    """Generic response envelope for every list endpoint.

    Written with `Generic[T]` rather than PEP 695's `class Page[T]`: the newer
    syntax is a hard SyntaxError on anything below Python 3.12, which makes the
    whole file unparseable to any editor or tool still pointed at an older
    interpreter. The runtime behaviour is identical, and this is the form
    pydantic's own documentation uses.
    """

    items: list[T]
    total: int
    page: int
    size: int
    pages: int

    @classmethod
    def build(cls, items: Sequence[T], total: int, params: PageParams) -> "Page[T]":
        return cls(
            items=list(items),
            total=total,
            page=params.page,
            size=params.size,
            pages=max(1, math.ceil(total / params.size)) if total else 0,
        )
