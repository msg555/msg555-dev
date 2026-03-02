import re
from typing import Optional

from bs4 import BeautifulSoup
from Levenshtein import ratio as edit_ratio

from mtgparse.common import cached_request
from mtgparse.data_model import Card, Deck, MatchResult, Player, Tournament


def _get_card_from_line(line: str) -> Card:
    parts = line.strip().split(" ", 1)
    if len(parts) != 2:
        raise ValueError("Unexpected card line format")
    return Card(parts[1], int(parts[0]))


class NewsTournament(Tournament):
    def __init__(self, event_name: str) -> None:
        self.event_name = event_name
        self.decklist_buckets = ["a-m", "n-z"] #["a-e", "f-l", "m-r", "s-z"]
        self.format_name = "standard"
        self.rounds = [4, 5, 6, 7, 8, 12, 13, 14, 15, 16]

        self.players: Optional[dict[str, Player]] = None
        self.normalize_cache: dict[str, str] = {}

    def get_players(self) -> dict[str, Player]:
        if self.players is not None:
            return self.players

        self.players = {}
        for bucket in self.decklist_buckets:
            url = f"https://magic.gg/decklists/{self.event_name}-{self.format_name}-decklists-{bucket}"
            soup = BeautifulSoup(
                cached_request(
                    "deck-{self.event_name}-{self.format_name}-{bucket}.html",
                    "get",
                    url,
                ),
                "lxml",
            )

            for deck_data in soup.find_all("deck-list"):
                main_deck_tag = deck_data.find("main-deck")
                side_board_tag = deck_data.find("side-board")
                if not main_deck_tag or not side_board_tag:
                    continue
                main_deck = [
                    _get_card_from_line(line)
                    for line in main_deck_tag.text.split("\n")
                    if line.strip()
                ]
                side_board = [
                    _get_card_from_line(line)
                    for line in side_board_tag.text.split("\n")
                    if line.strip()
                ]

                ident = str(deck_data.get("deck-title")).lower()
                deck = Deck(
                    main_deck,
                    side_board,
                    archetype=str(deck_data.get("subtitle")),
                    author=None,
                    url=f"{url}#{ident.replace(' ', '-')}",
                )

                self.players[ident] = Player(
                    ident=ident,
                    name=ident,
                    deck=deck,
                )

        return self.players

    def _normalize_name(self, name: str) -> str:
        if name == "Puglisi Clark, Joseph":
            return "joseph puglisi"

        if norm_name := self.normalize_cache.get(name):
            return norm_name

        players = self.get_players()
        norm_name_a = name
        norm_name_b = name

        parts = name.lower().split(",", 1)
        if len(parts) == 2:
            norm_name_b = parts[0].strip() + " " + parts[1].strip()
            norm_name_a = parts[1].strip() + " " + parts[0].strip()

        best = max(
            max(
                (edit_ratio(norm_name_a, player_name), player_name)
                for player_name in players
            ),
            max(
                (edit_ratio(norm_name_b, player_name), player_name)
                for player_name in players
            ),
        )
        return best[1]

    def get_single_round_result(self, round_num: int) -> list[MatchResult]:
        soup = BeautifulSoup(
            cached_request(
                f"{self.event_name}-results-{round_num}.html",
                "get",
                f"https://magic.gg/news/{self.event_name}-round-{round_num}-results",
            ),
            "lxml",
        )

        results: list[MatchResult] = []
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cols = [col.text for col in row.find_all("td")]
                if len(cols) != 4:
                    continue
                if cols[1] != "vs.":
                    continue

                player_1 = cols[0]
                player_2 = cols[2]
                match_record = (0, 0, 0)

                if cols[3].endswith(" bye"):
                    results.append(
                        MatchResult(
                            p1=self._normalize_name(player_1),
                            p2=None,
                            games=(0, 0, 0),
                        )
                    )
                    continue

                m = re.match("([0-9])-([0-9])-([0-9]) Draw", cols[3])
                if m:
                    match_record = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
                    assert match_record[0] == match_record[1]
                else:
                    m = re.match(
                        "(.*) won ([0-9])-([0-9])-([0-9])",
                        cols[3],
                    )
                    if not m:
                        raise ValueError(f"Couldn't determine result {repr(cols[3])}")

                    match_record = (int(m.group(2)), int(m.group(3)), int(m.group(4)))
                    assert match_record[0] > match_record[1]
                    if edit_ratio(m.group(1), player_1) < edit_ratio(
                        m.group(1), player_2
                    ):
                        player_1, player_2 = player_2, player_1

                results.append(
                    MatchResult(
                        p1=self._normalize_name(player_1),
                        p2=self._normalize_name(player_2),
                        games=match_record,
                    )
                )

        return results

    def get_round_results(self) -> list[list[MatchResult]]:
        return [self.get_single_round_result(round_num) for round_num in self.rounds]
