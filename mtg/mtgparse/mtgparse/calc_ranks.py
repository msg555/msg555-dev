"""
Compute player ranks

See https://mtg.fandom.com/wiki/Tiebreaker for documentation on how to compute tiebreakers
"""

import argparse
import contextlib
import copy
import json
import logging
import random
import sys
from fractions import Fraction
from typing import Optional, Sequence

import pandas
import tqdm
from scipy.stats import beta

from mtgparse.data_model import Tournament
from mtgparse.json_tournament import JsonTournament


def calc_ord(top_cut: int) -> list[int]:
    order = [0]
    for _ in range(top_cut):
        n_order = []
        for i in order:
            n_order.append(i)
            n_order.append(2 * len(order) - i - 1)
        order = n_order
    return order


def get_top_cut(ordered_players: Sequence[str], top_cut_rounds: int) -> list[str]:
    if len(ordered_players) < 2**top_cut_rounds:
        raise ValueError("Too few players for configured top cut")
    return [ordered_players[ind] for ind in calc_ord(top_cut_rounds)]


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
    parser.add_argument(
        "--rounds",
        type=int,
        default=0,
        help="show results after a certain number of rounds. defaults to processing all rounds",
    )
    parser.add_argument(
        "--top-cut",
        type=int,
        default=3,
        help="number of rounds in top-cut",
    )
    parser.add_argument(
        "--sim-rounds",
        type=int,
        default=0,
        help="Number of rounds in simulation",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output file path. Defaults to stdout",
    )
    parser.add_argument(
        "--format",
        choices=("csv", "json"),
        default="csv",
        help="Output format",
    )
    return parser.parse_args()


class PlayerData:
    MIN_PERCENTAGE = Fraction(1, 3)

    def __init__(self, top_cut_round_idx: int) -> None:
        self.top_cut_round_idx = top_cut_round_idx
        self.top_cut_points = 0
        self.points = 0
        self.rounds = 0
        self.match_record = (0, 0, 0)
        self.game_record = (0, 0, 0)

    def record_match(
        self,
        games: tuple[int, int, int],
        *,
        reverse: bool = False,
    ) -> None:
        self.rounds += 1
        if reverse:
            games = (games[1], games[0], games[2])

        points = 0
        if games[0] > games[1]:
            match = (1, 0, 0)
            points = 3
        elif games[1] > games[0]:
            match = (0, 1, 0)
            points = 0
        else:
            match = (0, 0, 1)
            points = 1

        # This is just for record-keeping, not used in breakers
        self.match_record = zip_add(self.match_record, match)

        if self.rounds <= self.top_cut_round_idx:
            self.points += points

            # This is just used for breakers calculations
            self.game_record = zip_add(self.game_record, games)
        else:
            # After top-cut do not affect other breakers
            self.top_cut_points += points

    @property
    def match_win_percentage(self) -> Fraction:
        if not self.rounds:
            return Fraction(1, 2)
        return max(
            self.MIN_PERCENTAGE,
            Fraction(self.points, 3 * min(self.rounds, self.top_cut_round_idx)),
        )

    @property
    def game_win_percentage(self) -> Fraction:
        game_count = sum(self.game_record)
        if not game_count:
            return Fraction(1, 2)
        return max(
            self.MIN_PERCENTAGE,
            Fraction(self.game_record[0] * 3 + self.game_record[2], 3 * game_count),
        )


class PlayerStats:
    MAX_POWER = 9

    def __init__(
        self,
        point_thresholds: list[int],
    ):
        self.point_thresholds = list(point_thresholds)
        self.wins = 0
        self.top_p2 = [0 for _ in range(self.MAX_POWER)]
        self.made_cutoff = [0 for _ in self.point_thresholds]
        self.day_2 = 0
        self.rank_best = None
        self.rank_worst = None

    def record_rank(self, rank: int, points: int) -> None:
        if self.rank_best is None or rank < self.rank_best:
            self.rank_best = rank
        if self.rank_worst is None or self.rank_worst < rank:
            self.rank_worst = rank
        for ind in range(self.MAX_POWER):
            if rank < 2**ind:
                self.top_p2[ind] += 1
        for index, point_threshold in enumerate(self.point_thresholds):
            if points >= point_threshold:
                self.made_cutoff[index] += 1

    def display(self) -> str:
        return str(self.top_p2)

    def sort_key(self):
        return ([-x for x in self.top_p2], [-x for x in self.made_cutoff])


def sample_matchups(
    matchups: dict[str, dict[str, tuple[int, int, int]]],
) -> dict[str, dict[str, float]]:
    all_archs = list(matchups)
    result: dict[str, dict[str, float]] = {arch: {} for arch in all_archs}
    for ind, arch_1 in enumerate(all_archs):
        result[arch_1][arch_1] = 0.5
        for arch_2 in all_archs[ind + 1 :]:
            games = matchups[arch_1].get(arch_2, (0, 0, 0))
            prob = beta.rvs(8 + games[0], 8 + games[1])
            result[arch_1][arch_2] = prob
            result[arch_2][arch_1] = 1.0 - prob
    return result


def calc_ranks(
    tour: Tournament,
    *,
    round_limit: int = 0,
    top_cut_rounds: int = 3,
    sim_rounds: int = 0,
    limited_rounds: Optional[list[int]] = None,
    required_points: dict[int, int] = None,
):
    players = tour.get_players()
    all_round_results = tour.get_round_results()

    top_cut_round_idx = len(all_round_results) - top_cut_rounds
    player_data = {player_id: PlayerData(top_cut_round_idx) for player_id in players}
    player_matchups: dict[str, list[str]] = {player_id: [] for player_id in players}

    # At the time of the top cut these are frozen as the (ordered) list of
    # players in the top cut.
    top_cut_players: list[str] = []

    st_limited_rounds = set(limited_rounds or ())
    required_points = required_points or {}

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
            -pd.top_cut_points,
            -pd.points,
            -opp_match_win_perc / total_opponents,
            -pd.game_win_percentage,
            -opp_game_win_perc / total_opponents,
            player_id,
        )

    arch_matchup: dict[str, dict[str, tuple[int, int, int]]] = {}
    round_total = len(all_round_results)
    has_round_pending: set[str] = set()
    for round_idx, round_results in enumerate(all_round_results):
        if round_limit and round_limit <= round_idx:
            break
        if not round_results:
            # Empty results means the round hasn't been recorded yet.
            break

        # If we're heading into top cut need to calculate who made it.
        if round_idx == round_total - top_cut_rounds:
            # Setup single elimination structure
            rem_players = [
                player_id
                for player_id, pdata in player_data.items()
                if pdata.rounds == round_idx
            ]
            rem_players.sort(key=tiebreakers)
            top_cut_players.clear()
            top_cut_players.extend(get_top_cut(rem_players, top_cut_rounds))

        seen_in_round = set()
        for round_result in round_results:
            p1 = round_result.p1
            p2 = round_result.p2
            games = round_result.games

            if not round_result.complete:
                has_round_pending.add(p1)
                if p2:
                    has_round_pending.add(p2)
                continue

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

            if round_idx < top_cut_round_idx:
                player_matchups[p1].append(p2)
                player_matchups[p2].append(p1)
            player_data[p1].record_match(games)
            player_data[p2].record_match(games, reverse=True)

            # Record matchup result
            if round_idx not in st_limited_rounds:
                arch_1 = players[p1].deck.archetype
                arch_2 = players[p2].deck.archetype
                arch_matchup.setdefault(arch_1, {})[arch_2] = zip_add(
                    arch_matchup.get(arch_1, {}).get(arch_2, (0, 0, 0)),
                    games,
                )
                arch_matchup.setdefault(arch_2, {})[arch_1] = zip_add(
                    arch_matchup.get(arch_2, {}).get(arch_1, (0, 0, 0)),
                    (games[1], games[0], games[2]),
                )

    arch_matchup_probs: dict[str, dict[str, float]] = {}

    def simulate_round(round_idx, partial: bool = False):
        # only include players who haven't missed a round (assumed to have dropped)
        rem_players = [
            player_id
            for player_id, pdata in player_data.items()
            if pdata.rounds == round_idx
            and pdata.points >= required_points.get(round_idx, 0)
        ]

        pairings = []
        if partial:
            # If round results are partial we should already have all pairings with
            # some results reported. Already reported results should be accounted
            # for so we just need to simulate unreported results.
            for match_result in all_round_results[round_idx]:
                if not match_result.complete:
                    pairings.append((match_result.p1, match_result.p2))
        elif round_idx < round_total - top_cut_rounds:
            if round_idx < round_total - top_cut_rounds - 1:
                # Pair randomly among players with the same number of points.
                # We reuse the power pairing logic but just order players by their
                # points and break ties using a random key.
                player_sort_key = {
                    player_id: random.random() for player_id in rem_players
                }
                rem_players.sort(
                    key=lambda player_id: (
                        player_data[player_id].points,
                        player_sort_key[player_id],
                    )
                )
            else:
                # Do power pairing on sorted rankings
                rem_players.sort(key=tiebreakers)

            # Do power pairing!
            paired = set()
            for rank, p1 in enumerate(rem_players):
                if p1 in paired:
                    continue

                # Pair with next rank that we haven't played before
                past_matchups = player_matchups[p1]
                for pair_rank in range(rank + 1, len(rem_players)):
                    p2 = rem_players[pair_rank]
                    if p2 not in past_matchups:
                        pairings.append((p1, p2))
                        paired.add(p2)
                        break
                else:
                    # Give bye if no way to pair
                    pairings.append((p1, None))
        else:
            if round_idx == round_total - top_cut_rounds:
                # Setup single elimination structure
                rem_players.sort(key=tiebreakers)
                top_cut_players.clear()
                top_cut_players.extend(get_top_cut(rem_players, top_cut_rounds))
                rem_players = top_cut_players
            else:
                rem_players = [
                    player_id
                    for player_id in top_cut_players
                    if player_data[player_id].top_cut_points // 3
                    == round_idx - top_cut_round_idx
                ]

            assert len(rem_players) == 2 ** (round_total - round_idx)
            for index in range(0, len(rem_players), 2):
                pairings.append((rem_players[index], rem_players[index + 1]))

        # Mark matchups first
        if round_idx < top_cut_round_idx:
            for p1, p2 in pairings:
                if p2 is not None:
                    player_matchups[p1].append(p2)
                    player_matchups[p2].append(p1)

        # To help calculate IDs we'll assume that breakers do not change as a
        # result of this round. Generally relative order of breakers will not change
        # as a result of this round (but can in some uncommon cases) so this
        # should be a relatively accurate assumption.

        intentional_draws = set()
        if not partial and round_idx == round_total - top_cut_rounds - 1:
            cut_off_rank = 2**top_cut_rounds

            orig_breakers = {
                player_id: tiebreakers(player_id) for player_id in rem_players
            }
            for p1, p2 in pairings:
                if p2 is None:
                    continue

                # We will ID if even the lower player has a guaranteed spot in top
                # cut.
                p2_breakers = list(orig_breakers[p2])
                p2_breakers[1] -= 1  # Give one point for draw

                worst_rank = 1  # Start at 1 since behind p1
                for oth_p1, oth_p2 in pairings:
                    if oth_p1 == p1 or oth_p2 is None:
                        continue

                    worst_case_rank = 0
                    for outcome in ((1, 1), (3, 0), (0, 3)):
                        if (oth_p1, oth_p2) in intentional_draws:
                            if outcome != (1, 1):
                                continue
                        oth_p1_break = list(orig_breakers[oth_p1])
                        oth_p2_break = list(orig_breakers[oth_p2])
                        oth_p1_break[1] -= outcome[0]
                        oth_p2_break[1] -= outcome[1]
                        outcome_rank = 0
                        if p2_breakers > oth_p1_break:
                            outcome_rank += 1
                        if p2_breakers > oth_p2_break:
                            outcome_rank += 1
                        worst_case_rank = max(worst_case_rank, outcome_rank)

                    worst_rank += worst_case_rank
                    if worst_rank >= cut_off_rank or worst_case_rank == 0:
                        break

                if worst_rank < cut_off_rank:
                    intentional_draws.add((p1, p2))

        for p1, p2 in pairings:
            if p2 is None:
                player_data[p1].record_match((2, 0, 0))
                continue
            if (p1, p2) in intentional_draws:
                player_data[p1].record_match((0, 0, 0))
                player_data[p2].record_match((0, 0, 0))
                continue

            if round_idx in st_limited_rounds:
                matchup_prob = 0.5
            else:
                matchup_prob = arch_matchup_probs.get(
                    players[p1].deck.archetype, {}
                ).get(players[p2].deck.archetype, 0.5)
            games = [0, 0]
            while all(g < 2 for g in games):
                winner = 0 if random.random() < matchup_prob else 1
                games[winner] += 1

            player_data[p1].record_match((games[0], games[1], 0))
            player_data[p2].record_match((games[1], games[0], 0))

    if not all_round_results:
        sim_rounds = 0  # Empty tournament?
    elif all_round_results[-1] and all(
        result.complete for result in all_round_results[-1]
    ):
        sim_rounds = 0  # Tournament is over, nothing to simulate

    player_stats = {}
    if sim_rounds:
        point_thresholds = list(required_points.values())
        player_stats = {
            player_id: PlayerStats(point_thresholds)
            for player_id in players
        }

        init_player_data = copy.deepcopy(player_data)
        init_player_matchups = copy.deepcopy(player_matchups)
        init_top_cut_players = copy.deepcopy(top_cut_players)

        for _ in tqdm.trange(sim_rounds):
            player_data = copy.deepcopy(init_player_data)
            player_matchups = copy.deepcopy(init_player_matchups)
            top_cut_players = copy.deepcopy(init_top_cut_players)
            arch_matchup_probs = sample_matchups(arch_matchup)

            for round_idx, round_results in enumerate(all_round_results):
                if not round_results:
                    simulate_round(round_idx)
                elif any(not result.complete for result in round_results):
                    simulate_round(round_idx, True)

            top_players = sorted(players, key=tiebreakers)
            for rank, player_id in enumerate(top_players):
                player_stats[player_id].record_rank(rank, player_data[player_id].points)

        player_data = copy.deepcopy(init_player_data)
        player_matchups = copy.deepcopy(init_player_matchups)
        top_cut_players = copy.deepcopy(init_top_cut_players)
        top_players = sorted(players, key=tiebreakers)

    output_data = {}

    rem_players = sorted(player_data, key=tiebreakers)
    for rank, player_id in enumerate(rem_players):
        pdata = player_data[player_id]
        _, _, opp_match_win_perc, _, opp_game_win_perc, _ = tiebreakers(player_id)

        player_output = {
            "rank": rank + 1,
            "record": "-".join(str(x) for x in pdata.match_record),
            "round_pending": player_id in has_round_pending,
            "points": pdata.points,
            "omw": float(-opp_match_win_perc),
            "gw": float(pdata.game_win_percentage),
            "ogw": float(-opp_game_win_perc),
        }

        if player_stats:
            stats = player_stats[player_id]
            player_output["rank_best"] = stats.rank_best + 1
            player_output["rank_worst"] = stats.rank_worst + 1
            player_output.update(
                {
                    f"cutoff_{point_threshold}": stats.made_cutoff[index] / sim_rounds
                    for index, point_threshold in enumerate(point_thresholds)
                }
            )
            player_output.update(
                {
                    f"top_{2 ** ind}": stats.top_p2[ind] / sim_rounds
                    for ind in range(0, PlayerStats.MAX_POWER)
                }
            )

        output_data[player_id] = player_output

    return output_data


def output_csv(fobj, output_data) -> None:
    df = pandas.DataFrame(
        [
            {"player": player_id} | player_output
            for player_id, player_output in output_data.items()
        ]
    )
    df.to_csv(fobj, index=False, sep=",")


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    args = parse_args()

    tour = JsonTournament.from_file(args.input)
    ranks = calc_ranks(
        tour,
        round_limit=args.rounds,
        top_cut_rounds=args.top_cut,
        required_points={9: 18},
        sim_rounds=args.sim_rounds,
    )

    with (
        open(args.output, "w", encoding="utf-8")
        if args.output
        else contextlib.nullcontext(sys.stdout)
    ) as out_f:
        if args.format == "csv":
            output_csv(out_f, ranks)
        else:
            json.dump(ranks, out_f)

    return 0


if __name__ == "__main__":
    sys.exit(main())
