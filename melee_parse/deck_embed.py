import json

import plotly.express as px
import pandas as pd
from sklearn.manifold import MDS
import numpy as np


# Load the tournament data
with open("tournament.json", "r", encoding="utf-8") as fdata:
    tour = json.load(fdata)
players = tour["players"]


# Find the set of all cards being played
all_cards = set()
for player in players.values():
    deck = player["deck"]
    all_cards.update(card["name"] for card in deck["main_deck"])
    all_cards.update(card["name"] for card in deck["side_board"])

# Create mapping of card names to dimension index
card_index = {
    card: idx
    for idx, card in enumerate(all_cards)
}

# Create deck vectors for each player. vecs[player_index][card_index] is the number of copies of a
# card a given player is playing.
vecs = np.zeros((len(players), len(all_cards)))
player_ids = list(players.keys())
for player_index, player_id in enumerate(player_ids):
    deck = players[player_id]["deck"]
    for card in deck["main_deck"]:
        vecs[player_index][card_index[card["name"]]] += card["count"]
    for card in deck["side_board"]:
        vecs[player_index][card_index[card["name"]]] += card["count"]

# Perform embedding using multi dimensional scaling 
# https://en.wikipedia.org/wiki/Multidimensional_scaling
mds = MDS(n_components=2, metric=True, max_iter=100, eps=1e-4)
vec_2d = mds.fit_transform(vecs)

# Setup pandas datafram for plotly
df = pd.DataFrame({
    "x": vec_2d[:, 0],
    "y": vec_2d[:, 1],
    "name": [players[player_id]["name"] for player_id in player_ids],
    "category": [players[player_id]["deck"]["archetype"] for player_id in player_ids],
    "url": [players[player_id]["deck"]["url"] for player_id in player_ids],
})

# Setup scatter plot
fig = px.scatter(
    df,
    x="x",
    y="y",
    color="category",
    title="Deck Embedding",
    custom_data=["name","category","url"],
    hover_data=["name","category"],
)
fig.update_layout(clickmode="event")

# Add extra javascript to let you click on links to decklists
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
