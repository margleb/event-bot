from dataclasses import dataclass, field

from event_bot.models import Profile


@dataclass
class ProfileStore:
    drafts: dict[int, Profile] = field(default_factory=dict)
    confirmed: dict[int, Profile] = field(default_factory=dict)
