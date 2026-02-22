import functools
import itertools
import logging
import os
import re

import matplotlib
import pandas as pd
import plotly.express as px
import requests
from bs4 import BeautifulSoup
from Levenshtein import ratio as edit_ratio

from mtgparse.data_model import Card, MatchResult
from mtgparse.json_tournament import JsonTournament
from mtgparse.melee_tournament_parse import MeleeTournament
from mtgparse.news_parse import NewsTournament

matplotlib.use("QtAgg")
import matplotlib.pyplot as plt
import numpy as np
from sklearn.manifold import MDS


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


def main():
    logging.basicConfig(level=logging.INFO)
    args = parse_args()

    tour = JsonTournament.from_file(args.input)

    matchup = {}
    total = {}
    arch_players = {}
    player_points = {}

    for round_idx, round_results in enumerate(results):
        seen_in_round = set()

        for round_result in round_results:
            p1 = round_result.p1
            p2 = round_result.p2
            games = round_result.games
            if p2 is None:  # Bye
                if p1 in seen_in_round:
                    raise ValueError(f"Saw {p1} already in round index {round_idx}")
                seen_in_round.add(p1)
                player_points[p1] = player_points.get(p1, 0) + 3
                continue

            if p1 in seen_in_round:
                raise ValueError(f"Saw {p1} already in round index {round_idx}")
            if p2 in seen_in_round:
                raise ValueError(f"Saw {p2} already in round index {round_idx}")
            seen_in_round.add(p1)
            seen_in_round.add(p2)

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
                player_points[p1] = player_points.get(p1, 0) + 1
                player_points[p2] = player_points.get(p2, 0) + 1
            else:
                assert games[0] > games[1]
                result = (1, 0, 0)
                player_points[p1] = player_points.get(p1, 0) + 3

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

    if False:
        top_players = sorted(players, key=lambda player: player_points.get(player, 0))
        for player in top_players:
            print(player_points.get(player, 0), player)
        return

    if False:

        def format_record(record) -> str:
            if record == (0, 0, 0):
                return "-"
            win_rate = 100.0 * record[0] / sum(record)
            return f"{win_rate:.2f}% {record[0]}-{record[1]}-{record[2]}"

        table = [
            ["Deck Name", "Players", "Total", *deck_archs],
        ]
        for arch in deck_archs:
            row = [arch, str(len(arch_players[arch])), format_record(total[arch])]
            for arch_2 in deck_archs:
                row.append(format_record(matchup[arch].get(arch_2, (0, 0, 0))))
            table.append(row)

        print("\n".join(",".join(row) for row in table))

    if False:
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
                    row.append(res[0] / sum(res))
            print(" ".join(f"{num:.6f}" for num in row))

    if False:
        card_counts = {}
        for player in players.values():
            deck = player.deck
            card_map = {card.name: card.count for card in deck.main_deck}

            points = player_points.get(player.ident, 0)
            in_main_deck = set()
            for card in deck.main_deck:
                in_main_deck.add(card.name)
                card_counts[card.name] = zip_add(
                    card_counts.get(card.name, (0, 0, 0, 0)),
                    (card.count, card.count * points, 1, card.count),
                )
            for card in deck.side_board:
                card_counts[card.name] = zip_add(
                    card_counts.get(card.name, (0, 0, 0, 0)),
                    (
                        card.count,
                        card.count * points,
                        (0 if card.name in in_main_deck else 1),
                        0,
                    ),
                )

        cards = sorted(
            card_counts,
            key=lambda card: card_counts[card][0],
            # key=lambda card: card_counts[card][1] / card_counts[card][0],
            reverse=True,
        )
        print("Card|Unique Decks|Total Count|Total Main|Average Points")
        for card in cards:
            cc = card_counts[card]
            avg_points = cc[1] / cc[0]
            print(f"{card}|{cc[2]}|{cc[0]}|{cc[3]}|{avg_points:.6f}")

    if True:
        cards = set()
        for player in players.values():
            deck = player.deck
            cards.update(card.name for card in deck.main_deck)
            cards.update(card.name for card in deck.side_board)

        card_index = {card: idx for idx, card in enumerate(cards)}
        player_idents = list(players)

        vecs = np.zeros((len(players), len(cards)))
        for player_idx, player in enumerate(player_idents):
            deck = players[player].deck
            for card in itertools.chain(deck.main_deck, deck.side_board):
                vecs[player_idx][card_index[card.name]] += card.count

        print(vecs)

        mds = MDS(n_components=2, metric=True, max_iter=100, eps=1e-4)
        vec_2d = mds.fit_transform(vecs)

        arch_map = {}
        arch_list = []
        categories = []
        for player in player_idents:
            arch = players[player].deck.archetype
            if arch not in arch_map:
                arch_map[arch] = len(arch_map)
                arch_list.append(arch)
            categories.append(arch_map[arch])
        categories = np.array(categories)

        df = pd.DataFrame(
            {
                "x": vec_2d[:, 0],
                "y": vec_2d[:, 1],
                "name": [players[player].name for player in player_idents],
                "category": [arch_list[c] for c in categories],
                "url": [players[player].deck.url for player in player_idents],
            }
        )

        fig = px.scatter(
            df,
            x="x",
            y="y",
            color="category",
            title="Deck Embedding",
            custom_data=["name", "category", "url"],
            hover_data=["name", "category"],
        )
        # fig.update_traces(
        #    customdata=df[["name", "category", "url"]],
        #    hovertemplate='<br>'.join([
        #        "Name: %{customdata[0]}",
        #        "Category: %{customdata[1]}",
        #        "URL: %{customdata[2]}",
        #    ])
        # )
        fig.update_layout(clickmode="event")

        figure_html = fig.to_html(include_plotlyjs="cdn")
        figure_html = figure_html.replace(
            "</body>",
            """
<script>
var plot = document.getElementsByClassName('plotly-graph-div')[0];
plot.on('plotly_click', function(data){
    var point = data.points[0];
    var url = point.customdata[2];
    if(url) {
        window.open(url, '_blank');
    }
});
</script>
</body>
""",
        )
        fig.show()

        with open("plot.html", "w", encoding="utf-8") as fhtml:
            fhtml.write(figure_html)

        # for arch, arch_idx in arch_map.items():
        #    mask = categories == arch_idx
        #    plt.scatter(vec_2d[mask, 0], vec_2d[mask, 1],
        #               label=arch, s=10, alpha=0.6)
        # plt.legend()
        # plt.show()


# The resulting embedding is in X_2d
# print(X_2d.shape) # Output: (2000, 2)


if __name__ == "__main__":
    main()
