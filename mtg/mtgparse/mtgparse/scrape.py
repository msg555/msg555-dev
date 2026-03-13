"""
Parse an MTG Tournament into JSON format.
"""

import argparse
import logging
import sys

from mtgparse.data_model import Tournament
from mtgparse.json_tournament import JsonTournament
from mtgparse.melee_tournament_parse import MeleeTournament

LOGGER = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument(
        "--melee-id",
        help="melee tournament ID",
        required=True,
    )
    parser.add_argument(
        "-o",
        "--output",
        default="tournament.json",
        help="tournament output file path",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    args = parse_args()

    tour: Tournament
    if args.melee_id:
        tour = MeleeTournament(args.melee_id)
    else:
        assert False

    JsonTournament.from_tournament(tour).save_file(args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
