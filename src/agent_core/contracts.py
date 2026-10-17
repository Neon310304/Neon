from typing import Protocol

from langchain_core.tools import BaseTool


class AgentPack(Protocol):
    """Adapter contract that connects the reusable agent runtime to one project."""

    pack_id: str
    display_name: str

    def system_prompt(self, language: str) -> str:
        """Return project policy and response instructions for the selected language."""

    def tools_for(self, actor_id: str) -> list[BaseTool]:
        """Return least-privilege tools bound to the current project actor."""


class PackRegistry:
    def __init__(self):
        self._packs: dict[str, AgentPack] = {}

    def register(self, pack: AgentPack) -> None:
        if pack.pack_id in self._packs:
            raise ValueError(f"duplicate_pack:{pack.pack_id}")
        self._packs[pack.pack_id] = pack

    def get(self, pack_id: str) -> AgentPack:
        try:
            return self._packs[pack_id]
        except KeyError as exc:
            raise ValueError(f"unknown_pack:{pack_id}") from exc

    def describe(self) -> list[dict[str, str]]:
        return [{"project_id": pack.pack_id, "name": pack.display_name} for pack in self._packs.values()]
