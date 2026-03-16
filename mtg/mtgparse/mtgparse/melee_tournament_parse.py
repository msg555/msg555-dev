import json
import logging
import urllib.parse
from datetime import datetime, timezone
from typing import Optional

from bs4 import BeautifulSoup

from mtgparse.common import cached_request
from mtgparse.data_model import (
    DECK_UNKNOWN,
    Card,
    Deck,
    MatchResult,
    Player,
    Tournament,
)

LOGGER = logging.getLogger(__name__)


class MeleeTournament(Tournament):
    def __init__(
        self,
        tournament_id: int,
        *,
        first_constructed_round: int = 0,
    ) -> None:
        self.tournament_id = tournament_id
        self.first_constructed_round = first_constructed_round
        self.rounds: Optional[list[tuple[int, str]]] = None
        self.players: Optional[dict[str, Player]] = None

    def _get_tournament_page(self, *, force: bool = True) -> BeautifulSoup:
        page_html = cached_request(
            f"melee_tournament_{self.tournament_id}",
            "get",
            f"https://melee.gg/Tournament/View/{self.tournament_id}",
            force=force,
        )
        return BeautifulSoup(page_html, "lxml")

    def get_start_date(self) -> Optional[datetime]:
        soup = self._get_tournament_page(force=False)
        for time_span in soup.find_all("span", attrs={"data-toggle": "datetime"}):
            time_value = time_span.get("data-value")
            dt = datetime.strptime(str(time_value), "%m/%d/%Y %I:%M:%S %p")
            return dt.replace(tzinfo=timezone.utc)
        return None

    def get_rounds(self) -> list[tuple[int, str]]:
        if self.rounds is not None:
            return self.rounds

        soup = self._get_tournament_page()
        round_selector = soup.find(id="standings-round-selector-container")
        if not round_selector:
            return []

        self.rounds = []
        for btn in round_selector.find_all("button"):
            round_id = str(btn.get("data-id"))
            round_name = str(btn.get("data-name"))
            if round_id and round_name:
                self.rounds.append((int(round_id), round_name))
        return self.rounds

    def get_decklist(self, deck_id) -> Deck:
        raw_deck = cached_request(
            f"melee_decklist_{deck_id}",
            "get",
            f"https://melee.gg/Decklist/GetDecklistDetails?id={deck_id}",
        )
        deck_data = json.loads(raw_deck)

        main_deck: list[Card] = []
        side_board: list[Card] = []
        for component in deck_data["Components"]:
            is_side_board = component["ComponentDescription"] == "Sideboard"
            for card_record in component["CardRecords"]:
                card = Card(
                    card_record["n"],
                    card_record["q"],
                )
                (side_board if is_side_board else main_deck).append(card)

        return Deck(
            main_deck,
            side_board,
            archetype=deck_data["DecklistName"],
            url=f"https://melee.gg/Decklist/View/{deck_id}",
        )

    def get_players(self) -> dict[str, Player]:
        if self.players is not None:
            return self.players

        self.players = {}

        rounds = self.get_rounds()
        if not rounds:
            return {}

        player_decks = {}
        player_names = {}
        player_urls = {}

        def _ingest_competitor_record(competitor) -> bool:
            player_id = str(competitor["TeamId"])
            decks = competitor["Decklists"]
            player_decks[player_id] = decks[0] if decks else None
            player_names[player_id] = " and ".join(
                player["DisplayName"] for player in competitor["Team"]["Players"]
            )
            player_0 = next(iter(competitor["Team"]["Players"]), None)
            if player_0:
                player_urls[player_id] = (
                    "https://melee.gg/Profile/Index/"
                    + urllib.parse.quote(player_0["Username"])
                )
            return bool(decks)

        # Use first round to ensure we get all player data. Then use first constructed
        # round to overwrite any decklist data.
        for force in (False, True):
            match_results = list(self.page_round_results(rounds[0][0], force=force))
            match_results.extend(
                self.page_round_results(
                    rounds[self.first_constructed_round][0], force=force
                )
            )
            has_decks = False
            for match_result in match_results:
                for competitor in match_result["Competitors"]:
                    if _ingest_competitor_record(competitor):
                        has_decks = True

            if has_decks:
                break

        for force in (False, True):
            has_decks = False
            for competitor in self.page_round_standings(
                rounds[self.first_constructed_round][0],
                force=force,
            ):
                if _ingest_competitor_record(competitor):
                    has_decks = True

            if has_decks:
                break

        for player_id, deck_data in player_decks.items():
            if deck_data:
                deck_id = deck_data["DecklistId"]
                deck = self.get_decklist(deck_id)
            else:
                deck = DECK_UNKNOWN
            self.players[player_id] = Player(
                player_id,
                player_names[player_id],
                deck,
                url=player_urls.get(player_id),
            )

        return self.players

    def page_round_results(self, round_id: int, *, force: bool = False):
        start = 0
        page_size = 100

        while True:
            raw_result = cached_request(
                f"melee_round_result_{round_id}_{start}_{page_size}",
                "post",
                f"https://melee.gg/Match/GetRoundMatches/{round_id}",
                data={
                    "draw": "4",
                    "start": str(start),
                    "length": page_size,
                    "columns[0][data]": "TableNumber",
                    "columns[0][name]": "TableNumber",
                    "order[0][column]": "0",
                    "order[0][dir]": "asc",
                    "search[value]": "",
                    "search[regex]": "false",
                },
                force=force,
            )
            result = json.loads(raw_result)
            yield from result["data"]

            if not result["data"]:
                break

            start += page_size

    def page_round_standings(self, round_id: int, *, force: bool = False):
        start = 0
        page_size = 100

        while True:
            raw_result = cached_request(
                f"melee_round_standings_{round_id}_{start}_{page_size}",
                "post",
                "https://melee.gg/Standing/GetRoundStandings",
                data={
                    "draw": "4",
                    "start": str(start),
                    "length": page_size,
                    "roundId": str(round_id),
                    "columns[0][data]": "Rank",
                    "columns[0][name]": "Rank",
                    "order[0][column]": "0",
                    "order[0][dir]": "asc",
                    "search[value]": "",
                    "search[regex]": "false",
                },
                force=force,
            )
            result = json.loads(raw_result)
            yield from result["data"]

            if not result["data"]:
                break

            start += page_size

    def get_single_round_result(
        self, round_id: int, *, force: bool = False
    ) -> list[MatchResult]:

        missing_results = False
        match_results: list[MatchResult] = []
        for match_result in self.page_round_results(round_id, force=force):
            if match_result.get("LossReasonDescription") == "All Players Absent":
                continue

            comps = match_result["Competitors"]
            if len(comps) == 1:
                match_results.append(
                    MatchResult(
                        str(comps[0]["TeamId"]),
                        None,
                        (0, 0, 0),
                    )
                )
                continue

            if len(comps) != 2:
                raise ValueError("Got unexpected player count")

            if not match_result["HasResult"]:
                missing_results = True
                match_results.append(
                    MatchResult(
                        str(comps[0]["TeamId"]),
                        str(comps[1]["TeamId"]),
                        (0, 0, 0),
                        complete=False,
                    )
                )
                continue

            games = (
                comps[0]["GameWins"] or 0,
                comps[1]["GameWins"] or 0,
                match_result["GameDraws"] or 0,
            )

            if games[1] > games[0]:
                comps[0], comps[1] = comps[1], comps[0]
                games = (games[1], games[0], games[2])

            winner = comps[0]["Team"]["Players"][0]["DisplayName"]
            exp_results = f"{winner} won {games[0]}-{games[1]}-{games[2]}"
            if games[0] == games[1]:
                exp_results = f"{games[0]}-{games[1]}-{games[2]} Draw"

            if exp_results != match_result["ResultString"]:
                print(json.dumps(match_result, indent=2))
                print(exp_results)
                print(match_result["ResultString"])
                raise ValueError("Calcualted result doesn't match")

            match_results.append(
                MatchResult(
                    str(comps[0]["TeamId"]),
                    str(comps[1]["TeamId"]),
                    games,
                )
            )

        if missing_results or not match_results:
            if not force:
                return self.get_single_round_result(round_id, force=True)

        return match_results

    def get_round_results(self) -> list[list[MatchResult]]:
        results = []
        for round_id, _ in self.get_rounds():
            result = self.get_single_round_result(round_id)
            if result is not None:
                results.append(result)
        return results
