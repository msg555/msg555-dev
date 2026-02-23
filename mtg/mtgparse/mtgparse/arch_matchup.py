import argparse
import logging

from mtgparse.json_tournament import JsonTournament


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
        "-f",
        "--format",
        choices=("csv", "tabular"),
        default="csv",
        help="output format",
    )
    return parser.parse_args()


def main():
    logging.basicConfig(level=logging.INFO)
    args = parse_args()

    tour = JsonTournament.from_file(args.input)

    players = tour.get_players()

    matchup = {}
    total = {}
    arch_players = {}

    for round_results in tour.get_round_results():
        for round_result in round_results:
            p1 = round_result.p1
            p2 = round_result.p2
            games = round_result.games
            if p2 is None:  # Bye
                continue

            player_1 = players[p1]
            player_2 = players[p2]
            deck_1 = player_1.deck
            deck_2 = player_2.deck
            arch_1 = deck_1.archetype
            arch_2 = deck_2.archetype

            arch_players.setdefault(arch_1, set()).add(p1)
            arch_players.setdefault(arch_2, set()).add(p2)

            # Convert game result to match result
            if games[0] == games[1]:
                result = (0, 0, 1)
            else:
                assert games[0] > games[1]
                result = (1, 0, 0)

            rev_result = (result[1], result[0], result[2])

            matchup.setdefault(arch_1, {})[arch_2] = zip_add(
                matchup.get(arch_1, {}).get(arch_2, (0, 0, 0)),
                result,
            )
            total[arch_1] = zip_add(total.get(arch_1, (0, 0, 0)), result)
            matchup.setdefault(arch_2, {})[arch_1] = zip_add(
                matchup.get(arch_2, {}).get(arch_1, (0, 0, 0)),
                rev_result,
            )
            total[arch_2] = zip_add(total.get(arch_2, (0, 0, 0)), rev_result)

    deck_archs = sorted(
        (arch for arch, pc in arch_players.items()),
        key=lambda arch: len(arch_players[arch]),
        reverse=True,
    )

    def format_record(record) -> str:
        if record == (0, 0, 0):
            return "-"
        win_rate = 100.0 * (record[0] + record[2] / 2.0) / sum(record)
        return f"{win_rate:.2f}% {record[0]}-{record[1]}-{record[2]}"

    if args.format == "csv":
        table = [
            ["Deck Name", "Players", "Total", *deck_archs],
        ]
        for arch in deck_archs:
            row = [arch, str(len(arch_players[arch])), format_record(total[arch])]
            for arch_2 in deck_archs:
                row.append(format_record(matchup[arch].get(arch_2, (0, 0, 0))))
            table.append(row)

        print("\n".join(",".join(row) for row in table))
    elif args.format == "tabular":
        print(len(deck_archs))
        for arch in deck_archs:
            print(arch)
        for arch in deck_archs:
            row = []
            for arch_2 in deck_archs:
                res = matchup[arch].get(arch_2, (0, 0, 0))
                if sum(res) == 0:
                    row.append(0.5)
                else:
                    row.append((res[0] + res[2] / 2.0) / sum(res))
            print(" ".join(f"{num:.6f}" for num in row))


if __name__ == "__main__":
    main()
