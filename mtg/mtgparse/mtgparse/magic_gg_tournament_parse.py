import re
from typing import Optional

from bs4 import BeautifulSoup
from Levenshtein import ratio as edit_ratio

from mtgparse.calc_ranks import get_top_cut
from mtgparse.common import cached_request
from mtgparse.data_model import Card, Deck, MatchResult, Player, Tournament


def _get_card_from_line(line: str) -> Card:
    parts = line.strip().split(" ", 1)
    if len(parts) != 2:
        raise ValueError("Unexpected card line format")
    return Card(parts[1], int(parts[0]))


class MagicGGTournament(Tournament):
    def __init__(
        self,
        event_name: str,
        format_name: str,
        rounds: int,
        decklist_buckets: list[str],
        top_cut_rounds: int,
    ) -> None:
        self.event_name = event_name
        self.format_name = format_name
        self.rounds = rounds
        self.decklist_buckets = decklist_buckets
        self.top_cut_rounds = top_cut_rounds

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
                    f"deck-{self.event_name}-{self.format_name}-{bucket}.html",
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
                (edit_ratio(norm_name_a, player_name.lower()), player_name)
                for player_name in players
            ),
            max(
                (edit_ratio(norm_name_b, player_name.lower()), player_name)
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

    def _parse_round_results(self, doc: str, is_final: bool = False) -> list[str]:
        soup = BeautifulSoup(doc, "lxml")

        # pylint: disable=unused-variable
        results: list[str] = []
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cols = [col.text for col in row.find_all("td")]
                if is_final:
                    if len(cols) < 4:
                        continue
                    rank, points, first_name, last_name, *_ = cols
                    name = self._normalize_name(f"{first_name} {last_name}")
                else:
                    if len(cols) < 3:
                        continue
                    rank, full_name, points, *_ = cols
                    name = self._normalize_name(full_name)

                results.append(name)

        return results

    def get_top_cut_results(self) -> list[list[MatchResult]]:
        """
        Infer top cut resutls based on final standings and pre-top-cut standings.
        Note that using this method we don't actually have the real game results
        so we just treat it as 1-0-0 game result to have a minimal impact on game
        statistics.
        """
        final_standings = self._parse_round_results(
            cached_request(
                f"{self.event_name}-final-standings.html",
                "get",
                f"https://magic.gg/news/{self.event_name}-final-standings",
            ),
            is_final=True,
        )
        pre_cut_standings = self._parse_round_results(
            cached_request(
                f"{self.event_name}-round-{self.rounds}-standings.html",
                "get",
                f"https://magic.gg/news/{self.event_name}-round-{self.rounds}-standings",
            )
        )
        cut_off_rank = 2**self.top_cut_rounds

        final_standings = final_standings[:cut_off_rank]
        pre_cut_standings = pre_cut_standings[:cut_off_rank]
        if len(final_standings) < cut_off_rank:
            return [[] for _ in range(self.top_cut_rounds)]

        final_rank = {name: rank for rank, name in enumerate(final_standings)}
        order = get_top_cut(pre_cut_standings, self.top_cut_rounds)

        result = []
        for _ in range(self.top_cut_rounds):
            round_results = []
            new_order = []
            for ind in range(0, len(order), 2):
                p1 = order[ind]
                p2 = order[ind + 1]
                if final_rank[p2] < final_rank[p1]:
                    p1, p2 = p2, p1
                round_results.append(MatchResult(p1, p2, (1, 0, 0)))
                new_order.append(p1)
            result.append(round_results)
            order = new_order

        return result

    def get_round_results(self) -> list[list[MatchResult]]:
        result = [
            self.get_single_round_result(round_num + 1)
            for round_num in range(self.rounds)
        ]
        result.extend(self.get_top_cut_results())
        return result
