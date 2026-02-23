"""
Parse an MTG Tournament into JSON format.
"""

import argparse
import logging
import sys

from mtgparse.data_model import Tournament
from mtgparse.json_tournament import JsonTournament
from mtgparse.melee_tournament_parse import MeleeTournament
from mtgparse.news_parse import NewsTournament

LOGGER = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument(
        "--melee-id",
        help="melee tournament ID",
    )
    parser.add_argument(
        "--magic-gg-event",
        help="magic.gg event name",
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

    if sum(1 if arg else 0 for arg in (args.melee_id, args.magic_gg_event)) != 1:
        LOGGER.error("Must have exactly one of --melee-id, --magic-gg-event")
        return 1

    tour: Tournament
    if args.magic_gg_event:
        tour = NewsTournament(args.magic_gg_event)
    elif args.melee_id:
        tour = MeleeTournament(args.melee_id)
    else:
        assert False

    JsonTournament.from_tournament(tour).save_file(args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
