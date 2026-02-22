import requests
import json


def get_round_standings():
    resp = requests.post(
        "https://melee.gg/Standing/GetRoundStandings",
        data={
            "draw": "3",
            "start": "0",
            "length": "25",
            "roundid": "1161811",
            "columns[0][data]": "Rank",
            "columns[0][name]": "Rank",
            "order[0][column]": "0",
            "order[0][dir]": "asc",
            "search[value]": "",
            "search[regex]": "false",
        }
    )

    print(resp.content)
    print(json.dumps(resp.json(), indent=2))


def get_round_matches():
    resp = requests.post(
        "https://melee.gg/Match/GetRoundMatches/1161803",
        data={
            "draw": "4",
            "start": "0",
            "length": "25",
            "columns[0][data]": "TableNumber",
            "columns[0][name]": "TableNumber",
            "order[0][column]": "0",
            "order[0][dir]": "asc",
            "search[value]": "",
            "search[regex]": "false",
        },
    )

    print(resp.content)
    print(json.dumps(resp.json(), indent=2))


if __name__ == "__main__":
    get_round_matches()
