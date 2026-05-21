from __future__ import annotations

from dataclasses import dataclass, field

from compiler.analysis.ty.context import TypeCtx


@dataclass
class Impl:
    generics: list[int]
    target: int
    trait: int | None
    methods: dict[str, int] = field(default_factory=dict[str, int])


class ImplRegistry:
    def __init__(self, type_ctx: TypeCtx):
        self.__ctx = type_ctx

    def register_impl(self, generics: list[int], target: int, trait: int | None) -> Impl:
        raise NotImplementedError("ImplRegistry.register_impl is not implemented yet")
