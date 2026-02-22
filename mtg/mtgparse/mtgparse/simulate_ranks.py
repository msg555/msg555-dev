"""
Compute player ranks

See https://mtg.fandom.com/wiki/Tiebreaker for documentation on how to compute tiebreakers
"""

import argparse
import functools
import itertools
import logging
import os
import re
from fractions import Fraction

import requests
from bs4 import BeautifulSoup
from Levenshtein import ratio as edit_ratio

from mtgparse.data_model import Card, MatchResult
from mtgparse.json_tournament import JsonTournament
from mtgparse.melee_tournament_parse import MeleeTournament
from mtgparse.news_parse import NewsTournament


def zip_add(tup1, tup2):
    return tuple(a + b for a, b in zip(tup1, tup2))


def parse_args():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument(
        "-i",
        "--input",
        default="tournament.json",
        help="tournament json file",
    )

    return parser.parse_args()


class PlayerData:
    MIN_PERCENTAGE = Fraction(1, 3)

    def __init__(self) -> None:
        self.points = 0
        self.rounds = 0
        self.match_record = (0, 0, 0)
        self.game_record = (0, 0, 0)

    def record_match(
        self, games: tuple[int, int, int], *, reverse: bool = False
    ) -> None:
        self.rounds += 1
        if reverse:
            games = (games[1], games[0], games[2])
        self.game_record = zip_add(self.game_record, games)
        if games[0] > games[1]:
            match = (1, 0, 0)
            self.points += 3
        elif games[1] > games[0]:
            match = (0, 1, 0)
        else:
            match = (0, 0, 1)
            self.points += 1
        self.match_record = zip_add(self.match_record, match)

    @property
    def match_win_percentage(self) -> Fraction:
        match_count = sum(self.match_record)
        if not match_count:
            return Fraction(1, 2)
        return max(
            self.MIN_PERCENTAGE,
            Fraction(self.match_record[0], match_count),
        )

    @property
    def game_win_percentage(self) -> Fraction:
        game_count = sum(self.game_record)
        if not game_count:
            return Fraction(1, 2)
        return max(
            self.MIN_PERCENTAGE,
            Fraction(self.game_record[0], game_count),
        )


def main():
    logging.basicConfig(level=logging.INFO)
    args = parse_args()

    tour = JsonTournament.from_file(args.input)
    players = tour.get_players()
    player_data = {player_id: PlayerData() for player_id in players}
    player_matchups = {player_id: [] for player_id in players}

    for round_idx, round_results in enumerate(tour.get_round_results()):
        if args.rounds and args.rounds <= round_idx:
            break

        seen_in_round = set()
        for round_result in round_results:
            p1 = round_result.p1
            p2 = round_result.p2
            games = round_result.games

            for player_id in (p1, p2):
                if player_id is None:
                    continue
                if player_id in seen_in_round:
                    raise ValueError(
                        f"Saw {player_id} already in round index {round_idx}"
                    )
                if player_data[player_id].rounds != round_idx:
                    raise ValueError(
                        f"Player {player_id} has unexpected number of rounds in round index {round_idx}"
                    )
                seen_in_round.add(player_id)

            if p2 is None:  # Bye
                player_data[p1].record_match((2, 0, 0))
                continue

            player_matchups[p1].append(p2)
            player_matchups[p2].append(p1)
            player_data[p1].record_match(games)
            player_data[p2].record_match(games, reverse=True)

    def tiebreakers(player_id):
        pd = player_data[player_id]
        opp_match_win_perc = Fraction(0)
        opp_game_win_perc = Fraction(0)
        total_opponents = 0
        for opponent in player_matchups[player_id]:
            total_opponents += 1
            opp_match_win_perc += player_data[opponent].match_win_percentage
            opp_game_win_perc += player_data[opponent].game_win_percentage

        if total_opponents == 0:
            # Replicate melee.gg's sorting. This doesn't really matter after round 1.
            opp_match_win_perc = Fraction(3333, 10000)
            opp_game_win_perc = Fraction(3333, 10000)
            total_opponents = 1

        return (
            -pd.points,
            -opp_match_win_perc / total_opponents,
            -pd.game_win_percentage,
            -opp_game_win_perc / total_opponents,
            player_id,
        )

    top_players = sorted(
        players,
        key=tiebreakers,
    )
    for idx, player in enumerate(top_players):
        print(
            idx + 1,
            player,
            players[player].name,
            [float(f) for f in tiebreakers(player)],
        )


if __name__ == "__main__":
    main()
