import argparse
import itertools
import logging

import matplotlib
import numpy as np
import pandas as pd
import plotly.express as px
from sklearn.manifold import MDS

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


def main():
    logging.basicConfig(level=logging.INFO)
    args = parse_args()

    tour = JsonTournament.from_file(args.input)
    players = tour.get_players()

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


if __name__ == "__main__":
    main()
