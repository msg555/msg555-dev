import requests
import itertools
import functools
import os
from bs4 import BeautifulSoup
import re
from Levenshtein import ratio as edit_ratio

EVENT_NAME = "pro-tour-lorwyn-eclipsed"
FORMAT_NAME = "standard"

URL_BASE = "https://magic.gg/news/pro-tour-lorwyn-eclipsed"

URL_BASE = "https://magic.gg/news/pro-tour-lorwyn-eclipsed"

DECKLIST_BUCKETS = [
    "a-e",
    "f-l",
    "m-r",
    "s-z",
]


class Card:
    def __init__(self, name: str, count: int) -> None:
        assert count > 0
        self.name = name
        self.count = count

    @classmethod
    def from_line(cls, line: str) -> "Card":
        parts = line.split(" ", 1)
        if len(parts) != 2:
            print(line)
            raise ValueError("Unexpected card line format")
        return cls(parts[1], int(parts[0]))

    def __str__(self) -> str:
        return f"{self.count} {self.name}"

    def __repr__(self) -> str:
        return repr((self.name, self.count))


class Deck:
    def __init__(self, archetype: str, main_deck: list[Card], side_board: list[Card]) -> None:
        self.archetype = archetype
        self.main_deck = main_deck
        self.side_board = side_board


def get_deck_lists() -> dict[str, Deck]:
    decks = {}
    for bucket in DECKLIST_BUCKETS:
        cache_path = f"cache/deck-{EVENT_NAME}-{FORMAT_NAME}-{bucket}.html"
        if os.path.exists(cache_path):
            with open(cache_path, "r") as fdata:
                soup = BeautifulSoup(fdata.read(), "lxml")
        else:
            resp = requests.get(f"https://magic.gg/decklists/{EVENT_NAME}-{FORMAT_NAME}-decklists-{bucket}")
            resp.raise_for_status()
            with open(cache_path, "wb") as fdata:
                fdata.write(resp.content)
            soup = BeautifulSoup(resp.content, "lxml")

        for deck in soup.find_all("deck-list"):
            main_board = [
                Card.from_line(line.strip())
                for line in deck.find("main-deck").text.split("\n")
                if line.strip()
            ]
            side_board = [
                Card.from_line(line.strip())
                for line in deck.find("side-board").text.split("\n")
                if line.strip()
            ]

            decks[deck.get("deck-title").lower()] = Deck(
                deck.get("subtitle"),
                main_board,
                side_board,
            )

    return decks


def get_round_results(round_num: int):
    cache_path = f"cache/{EVENT_NAME}-results-{round_num}.html"
    if os.path.exists(cache_path):
        with open(cache_path, "r") as fdata:
            soup = BeautifulSoup(fdata.read(), "lxml")
    else:
        resp = requests.get(f"https://magic.gg/news/{EVENT_NAME}-round-{round_num}-results")
        resp.raise_for_status()
        with open(cache_path, "wb") as fdata:
            fdata.write(resp.content)
        soup = BeautifulSoup(resp.content, "lxml")

    results = []
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
                player_2 = "bye"
            else:
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
                        print(cols)
                        raise ValueError(f"Couldn't determine result {repr(cols[3])}")

                    match_record = (int(m.group(2)), int(m.group(3)), int(m.group(4)))
                    assert match_record[0] > match_record[1]
                    if edit_ratio(m.group(1), player_1) < edit_ratio(m.group(1), player_2):
                        player_1, player_2 = player_2, player_1

            results.append((player_1, player_2, match_record))

    return results


def main():
    decks = get_deck_lists()

    @functools.lru_cache(maxsize=None)
    def normalize_name(name: str):
        if name == "Puglisi Clark, Joseph":
            return "joseph puglisi"

        oname = name
        parts = name.lower().split(",", 1)
        if len(parts) == 2:
            name = parts[1].strip() + " " + parts[0].strip()
            if name in decks:
                return name

            name = parts[0].strip() + " " + parts[1].strip()
            if name in decks:
                return name
            name = parts[1].strip() + " " + parts[0].strip()

        best = max((edit_ratio(name, deck_name), deck_name) for deck_name in decks)
        if best[0] < 0.6:
            raise ValueError(f"Could not match up {oname}")

        assert best[0] > 0.6
        return best[1]

    def zip_add(tup1, tup2):
        return tuple(a + b for a, b in zip(tup1, tup2))

    matchup = {}
    total = {}
    players = {}
    player_points = {}

    ROUNDS = [4, 5, 6, 7, 8, 12, 13, 14, 15, 16]
    #ROUNDS = list(range(1, 16)) # include limited for lb testing
    for round_num in ROUNDS:
        round_result = get_round_results(round_num)

        in_round = set()

        assert len(round_result) > 10
        for player_1, player_2, result in round_result:
            if player_2 == "bye":
                player_1 = normalize_name(player_1)
                if player_1 in in_round:
                    raise ValueError(f"Saw {player_1} already in round {round_num}")
                in_round.add(player_1)
                player_points[player_1] = player_points.get(player_1, 0) + 3
                continue

            player_1 = normalize_name(player_1)
            player_2 = normalize_name(player_2)
            deck_1 = decks[player_1]
            deck_2 = decks[player_2]
            arch_1 = deck_1.archetype
            arch_2 = deck_2.archetype

            if player_1 in in_round:
                raise ValueError(f"Saw {player_1} already in round {round_num}")
            if player_2 in in_round:
                raise ValueError(f"Saw {player_2} already in round {round_num}")
            in_round.add(player_1)
            in_round.add(player_2)

            players.setdefault(arch_1, set()).add(player_1)
            players.setdefault(arch_2, set()).add(player_2)

            # Convert game result to match result
            if result[0] == result[1]:
                result = (0, 0, 1)
                player_points[player_1] = player_points.get(player_1, 0) + 1
                player_points[player_2] = player_points.get(player_2, 0) + 1
            else:
                assert result[0] > result[1]
                result = (1, 0, 0)
                player_points[player_1] = player_points.get(player_1, 0) + 3

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
        (arch for arch, pc in players.items()),
        # key=lambda arch: total[arch][0] / sum(total[arch]),
        key=lambda arch: len(players[arch]),
        reverse=True,
    )
    deck_archs = deck_archs[:10]

    if False:
        top_players = sorted(
            decks,
            key=lambda player: player_points.get(player, 0)
        )
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
            row = [arch, str(len(players[arch])), format_record(total[arch])]
            for arch_2 in deck_archs:
                row.append(format_record(matchup[arch].get(arch_2, (0, 0, 0))))
            table.append(row)

        print("\n".join( ",".join(row) for row in table))

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

    if True:
        card_counts = {}
        for player, deck in decks.items():
            card_map = {
                card.name: card.count for card in deck.main_deck
            }
            if card_map.get("Badgermole Cub", 0) < 3 or card_map.get("Nature's Rhythm", 0) < 3:
                continue

            points = player_points.get(player, 0)
            in_main_deck = set()
            for card in deck.main_deck:
                in_main_deck.add(card.name)
                card_counts[card.name] = zip_add(
                    card_counts.get(card.name, (0, 0, 0, 0)),
                    (card.count, card.count * points, 1, card.count)
                )
            for card in deck.side_board:
                card_counts[card.name] = zip_add(
                    card_counts.get(card.name, (0, 0, 0, 0)),
                    (card.count, card.count * points, (0 if card.name in in_main_deck else 1), 0)
                )

        cards = sorted(
            card_counts,
            key=lambda card: card_counts[card][0],
            #key=lambda card: card_counts[card][1] / card_counts[card][0],
            reverse=True,
        )
        print("Card|Unique Decks|Total Count|Total Main|Average Points")
        for card in cards:
            cc = card_counts[card]
            avg_points = cc[1] / cc[0]
            print(f"{card}|{cc[2]}|{cc[0]}|{cc[3]}|{avg_points:.6f}")
            


def main_decklists():
    decks = get_deck_lists()

    counts = {}
    for deck_player, deck in decks.items():
        for card in deck.main_deck:
            counts[card.name] = counts.get(card.name, 0) + card.count
        for card in deck.side_board:
            counts[card.name] = counts.get(card.name, 0) + card.count

    cards = sorted(
        counts,
        key=lambda card: counts[card],
        reverse=True,
    )

    for card in cards:
        print(counts[card], card)



if __name__ == "__main__":
    main()
