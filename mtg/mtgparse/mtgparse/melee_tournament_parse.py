import requests
import time
import json
import os

from bs4 import BeautifulSoup
from typing import Optional
from mtgparse.data_model import Card, Deck, Player, Tournament, MatchResult
from mtgparse.common import cached_request


class MeleeTournament(Tournament):
    def __init__(self, tournament_id: int) -> None:
        self.tournament_id = tournament_id
        self.rounds: Optional[list[tuple[int, str]]] = None
        self.players: Optional[dict[str, Player]] = None

    def get_rounds(self) -> list[tuple[int, str]]:
        if self.rounds is not None:
            return self.rounds

        page_html = cached_request(
            f"melee_tournament_{self.tournament_id}",
            "get",
            f"https://melee.gg/Tournament/View/{self.tournament_id}",
        )
        soup = BeautifulSoup(page_html, "lxml")

        round_selector = soup.find(id="standings-round-selector-container")
        if not round_selector:
            raise ValueError("Could not find tournament round IDs")

        self.rounds = []
        for btn in round_selector.find_all("button"):
            round_id = btn.get("data-id")
            round_name = btn.get("data-name")
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

    # 	https://melee.gg/Standing/GetRoundStandings

    def get_players(self) -> dict[str, Player]:
        if self.players is not None:
            return self.players

        self.players = {}

        rounds = self.get_rounds()

        player_decks = {}
        player_names = {}
        rc = 0
        for match_result in self.page_round_results(rounds[0][0]):
            for competitor in match_result["Competitors"]:
                rc += 1
                decks = competitor["Decklists"]
                assert len(decks) == 1
                player_decks[str(competitor["TeamId"])] = decks[0]
                player_names[str(competitor["TeamId"])] = " and ".join(
                    player["DisplayName"]
                    for player in competitor["Team"]["Players"]
                )

        for player_id, deck_data in player_decks.items():
            deck_id = deck_data["DecklistId"]
            deck = self.get_decklist(deck_id)
            self.players[player_id] = Player(
                player_id,
                player_names[player_id],
                deck,
            )


        print(rc, len(self.players))
        return self.players

    def page_round_results(self, round_id: int):
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
            )
            result = json.loads(raw_result)
            for item in result["data"]:
                yield item

            if not result["data"]:
                break

            start += page_size

    def page_round_standings(self, round_id: int):
        start = 0
        page_size = 100

        while True:
            raw_result = cached_request(
                f"melee_round_standings_{round_id}_{start}_{page_size}",
                "post",
                f"https://melee.gg/Standing/GetRoundStandings",
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
            )
            result = json.loads(raw_result)
            for item in result["data"]:
                yield item

            if not result["data"]:
                break

            start += page_size

    def get_single_round_result(self, round_id: int) -> list[MatchResult]:
        match_results: list[MatchResult] = []
        for match_result in self.page_round_results(round_id):
            if not match_result["HasResult"]:
                raise ValueError("Match result not entered yet")
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

        return match_results

    def get_round_results(self) -> list[list[MatchResult]]:
        rounds = self.get_rounds()
        return [
            self.get_single_round_result(round_id)
            for round_id, _ in rounds
        ]
    

if __name__ == "__main__":
    t = MeleeTournament(331949)

    result = t.get_players()
    print(result)
