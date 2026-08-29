from dataclasses import dataclass


@dataclass(frozen=True)
class SourceBrand:
    name: str
    mark: str


SOURCE_BRANDS = {
    "kudago": SourceBrand(name="KudaGo", mark="K"),
    "timepad": SourceBrand(name="timepad", mark="tp"),
    "ticketmaster": SourceBrand(name="Ticketmaster", mark="★"),
}
DEFAULT_SOURCE_BRAND = SourceBrand(name="Источник", mark="↗")


def source_brand(source_id: str | None) -> SourceBrand:
    return SOURCE_BRANDS.get((source_id or "").casefold(), DEFAULT_SOURCE_BRAND)
