import argparse
import collections
import itertools
import logging

import matplotlib
import numpy as np
import pandas as pd
import plotly.express as px
from sklearn.manifold import MDS

from mtgparse.data_model import Tournament
from mtgparse.json_tournament import JsonTournament

matplotlib.use("QtAgg")


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


def create_embedding_html(
    tour: Tournament,
    output_file: str,
    *,
    show: bool = False,
) -> bool:
    players = tour.get_players()

    cards: set[str] = set()
    for player in players.values():
        deck = player.deck
        cards.update(card.name for card in deck.main_deck)
        cards.update(card.name for card in deck.side_board)

    if not cards:
        return False

    player_idents = [
        player_id
        for player_id, player_data in players.items()
        if player_data.deck.main_deck
    ]
    dist = np.zeros((len(player_idents), len(player_idents)))

    card_total = []
    card_counts = []
    for player_id in player_idents:
        deck = players[player_id].deck

        player_card_counts: dict[str, int] = collections.Counter()
        player_card_total = 0
        for card in itertools.chain(deck.main_deck, deck.side_board):
            player_card_counts[card.name] += card.count
            player_card_total += card.count

        assert player_card_total > 0
        card_counts.append(player_card_counts)
        card_total.append(player_card_total)

    for p1_idx, p1_cc in enumerate(card_counts):
        for p2_idx, p2_cc in enumerate(card_counts[p1_idx + 1 :], start=p1_idx + 1):
            overlap = 0
            for card_name, card_count in p1_cc.items():
                overlap += min(card_count, p2_cc.get(card_name, 0))
            dist[p1_idx][p2_idx] = dist[p2_idx][p1_idx] = 1 - overlap / max(
                card_total[p1_idx], card_total[p2_idx]
            )

    mds = MDS(n_components=2, dissimilarity="precomputed", max_iter=100, eps=1e-4)
    vec_2d = mds.fit_transform(dist)

    arch_map: dict[str, int] = {}
    arch_list: list[str] = []
    list_categories: list[int] = []
    for player_id in player_idents:
        arch = players[player_id].deck.archetype
        if arch not in arch_map:
            arch_map[arch] = len(arch_map)
            arch_list.append(arch)
        list_categories.append(arch_map[arch])

    categories = np.array(list_categories)

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
    if show:
        fig.show()

    with open(output_file, "w", encoding="utf-8") as fhtml:
        fhtml.write(figure_html)

    return True


def main():
    logging.basicConfig(level=logging.INFO)
    args = parse_args()

    tour = JsonTournament.from_file(args.input)
    create_embedding_html(tour, "plot.html", show=True)


if __name__ == "__main__":
    main()
