import abc
import dataclasses
from typing import Optional


@dataclasses.dataclass
class Card:
    name: str
    count: int


@dataclasses.dataclass
class Deck:
    main_deck: list[Card]
    side_board: list[Card]
    archetype: Optional[str] = None
    author: Optional[str] = None
    url: Optional[str] = None


DECK_UNKNOWN = Deck(
    main_deck=[],
    side_board=[],
    archetype="unknown",
)


@dataclasses.dataclass
class Player:
    ident: str
    name: str
    deck: Deck


@dataclasses.dataclass
class MatchResult:
    """
    Results should always be reported such that p1 is the winner of the match (unless its a draw).

    In the case of a bye p2 should be None and games should be (0, 0, 0)
    """

    p1: str
    p2: Optional[str]
    games: tuple[int, int, int]


class Tournament(abc.ABC):
    @abc.abstractmethod
    def get_players(self) -> dict[str, Player]:
        pass

    @abc.abstractmethod
    def get_round_results(self) -> list[list[MatchResult]]:
        pass
