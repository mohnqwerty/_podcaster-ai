"""Type stubs override for `feedparser`.

The official `feedparser` package ships without type stubs, so pyright infers
`Any` for `entry.get(...)` results and can't see the `entries` attribute on
the parsed feed object.

This stub declares the surface we use across the project's RSS sources:
`parse()` returns a `FeedParserDict` with an `entries` list, and each
entry supports `.get(key)` returning `Any` (so callers can `.strip()` etc
without type-checker complaints).
"""

from typing import Any, Iterator, MutableMapping

class FeedParserDict(MutableMapping[str, Any]):
    def __getitem__(self, key: str) -> Any: ...
    def __setitem__(self, key: str, value: Any) -> None: ...
    def __delitem__(self, key: str) -> None: ...
    def __iter__(self) -> Iterator[str]: ...
    def __len__(self) -> int: ...
    entries: list[FeedParserDict]
    feed: FeedParserDict
    bozo: bool
    version: str
    headers: Any
    href: str

def parse(
    url_or_file: Any,
    *,
    agent: str = ...,
    referrer: str = ...,
    response_headers: Any = ...,
    request_headers: Any = ...,
    content_type: str = ...,
    modified: Any = ...,
    etag: str = ...,
    location: str = ...,
    bozo: bool = ...,
    replace_headers: bool = ...,
    handler: Any = None,
    **kwargs: Any,
) -> FeedParserDict: ...

# Common module-level exports
version: str
__version__: str
UserAgent: str
SUPPORTED_VERSIONS: list[str]
