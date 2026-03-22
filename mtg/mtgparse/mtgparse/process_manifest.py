import abc
import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Annotated, Literal, Optional

import jinja2
import yaml
from pydantic import BaseModel, Field, TypeAdapter

from mtgparse.anthropic_label_archetypes import label_decks
from mtgparse.arch_cluster import create_embedding_html
from mtgparse.calc_ranks import calc_ranks
from mtgparse.data_model import Tournament
from mtgparse.json_tournament import JsonTournament
from mtgparse.magic_gg_tournament_parse import MagicGGTournament
from mtgparse.melee_tournament_parse import MeleeTournament

LOGGER = logging.getLogger(__name__)

SCRIPT_DIR = os.path.dirname(__file__)


class AutoLabel(BaseModel):
    enabled: bool = True
    force: bool = False
    format_markdown: str


class TournamentMetadata(BaseModel):
    title: str
    format: str
    auto_label: Optional[AutoLabel] = None
    top_cut_rounds: int = 3
    required_points: dict[int, int] = {9: 18}
    limited_rounds: list[int] = []
    sim_rounds: int = 50000
    start_date: Optional[datetime] = None
    active: bool = False

    @abc.abstractmethod
    def get_url(self) -> str:
        pass

    @abc.abstractmethod
    def get_tournament(self) -> Tournament:
        pass


def mex(arr: list[int]) -> int:
    """min excluded"""
    st = set(arr)
    for idx in range(len(st)):
        if idx not in st:
            return idx
    return len(st)


class MeleeTournamentMetadata(TournamentMetadata):
    type: Literal["melee"]
    melee_id: int

    def get_url(self) -> str:
        return f"https://melee.gg/Tournament/View/{self.melee_id}"

    def get_tournament(self) -> Tournament:
        return MeleeTournament(
            self.melee_id,
            first_constructed_round=mex(self.limited_rounds),
        )


class MagicGGTournamentMetadata(TournamentMetadata):
    type: Literal["magic_gg"]
    rounds: int
    event_name: str
    decklist_buckets: list[str]

    def get_url(self) -> str:
        return f"https://magic.gg/events/{self.event_name}"

    def get_tournament(self) -> Tournament:
        return MagicGGTournament(
            self.event_name,
            self.format,
            self.rounds,
            self.decklist_buckets,
            self.top_cut_rounds,
        )


ManifestData = TypeAdapter(
    dict[
        str,
        Annotated[
            MeleeTournamentMetadata | MagicGGTournamentMetadata,
            Field(discriminator="type"),
        ],
    ]
)


def parse_args():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument(
        "-m",
        "--manifest",
        default=os.path.normpath(os.path.join(SCRIPT_DIR, "../manifest.yaml")),
        help="Manifest file controlling tournament metadata",
    )
    parser.add_argument(
        "-t",
        "--tournament",
        action="append",
        help="Force process a specific tournament",
    )
    parser.add_argument(
        "--all",
        default=False,
        action="store_const",
        const=True,
        help="Force process every tournament",
    )
    parser.add_argument(
        "--no-scrape",
        default=False,
        action="store_const",
        const=True,
        help="If set load tournament from json instead of scraping",
    )
    parser.add_argument(
        "--embedding",
        default=False,
        action="store_const",
        const=True,
        help="If set regenerate deck archetype embeddings",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default=os.path.normpath(os.path.join(SCRIPT_DIR, "../../../docs/tournaments")),
        help="Manifest file controlling tournament metadata",
    )
    parser.add_argument(
        "--python-simulation",
        action="store_const",
        const=True,
        default=False,
        help="Use Python instead of Go for simulation",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO)

    with open(args.manifest, "r", encoding="utf-8") as fconfig:
        manifest_raw = yaml.safe_load(fconfig)

    now = datetime.now(tz=timezone.utc)
    manifest = ManifestData.validate_python(manifest_raw)
    for tournament_id, tournament_meta in manifest.items():
        tour = tournament_meta.get_tournament()

        start_date = tournament_meta.start_date
        if start_date is None:
            start_date = tour.get_start_date()
            tournament_meta.start_date = start_date

        if start_date is None:
            LOGGER.error(
                "No start date known for %s. Please manually enter one in manifest",
                tournament_id,
            )
            return 1

        if not args.all:
            if args.tournament:
                if tournament_id not in args.tournament:
                    continue
            elif not tournament_meta.active:
                continue

        assert tournament_meta.start_date is not None
        if now < tournament_meta.start_date:
            LOGGER.info("Contest %s hasn't started yet", tournament_id)
            continue

        json_path = os.path.join(args.output_dir, f"{tournament_id}.json")
        if args.no_scrape:
            json_tour = JsonTournament.from_file(json_path)
        else:
            LOGGER.info("Scraping %s", tournament_id)
            json_tour = JsonTournament.from_tournament(tour)

        if not any(round_results for round_results in json_tour.get_round_results()):
            LOGGER.info("No data for %s yet", tournament_id)
            continue

        if tournament_meta.auto_label and tournament_meta.auto_label.enabled:
            label_decks(
                tournament_meta.auto_label.format_markdown,
                [player.deck for player in json_tour.get_players().values()],
                force=tournament_meta.auto_label.force,
            )

        json_tour.model.title = tournament_meta.title
        json_tour.model.source_url = tournament_meta.get_url()
        json_tour.model.limited_rounds = tournament_meta.limited_rounds
        json_tour.model.top_cut_rounds = tournament_meta.top_cut_rounds
        json_tour.model.start_date = start_date.isoformat()

        if not args.no_scrape:
            json_tour.save_file(json_path)

        if args.python_simulation:
            ranks = calc_ranks(
                json_tour,
                top_cut_rounds=tournament_meta.top_cut_rounds,
                required_points=tournament_meta.required_points,
                sim_rounds=tournament_meta.sim_rounds,
            )
        else:
            result = subprocess.run(
                [
                    "go",
                    "run",
                    "calc_ranks.go",
                    "-i",
                    json_path,
                    "--sim-rounds",
                    str(tournament_meta.sim_rounds),
                    "--top-cut",
                    str(tournament_meta.top_cut_rounds),
                    "--required-points",
                    ",".join(
                        f"{key}:{val}"
                        for key, val in tournament_meta.required_points.items()
                    ),
                ],
                cwd=os.path.join(SCRIPT_DIR, ".."),
                capture_output=True,
                check=True,
            )
            ranks = json.loads(result.stdout)

        with open(
            os.path.join(args.output_dir, f"ranks/{tournament_id}.json"),
            "w",
            encoding="utf-8",
        ) as f_ranks:
            json.dump(ranks, f_ranks)

        embedding_path = os.path.join(
            args.output_dir, f"../embeddings-{tournament_id}.html"
        )
        if args.embedding or not os.path.exists(embedding_path):
            create_embedding_html(json_tour, embedding_path)

    has_any_data = {
        tournament_id
        for tournament_id in manifest
        if os.path.exists(os.path.join(args.output_dir, f"{tournament_id}.json"))
    }
    has_embeddings = {
        tournament_id
        for tournament_id in manifest
        if os.path.exists(
            os.path.join(args.output_dir, f"../embeddings-{tournament_id}.html")
        )
    }

    jinja_env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(os.path.join(SCRIPT_DIR, "templates")),
        autoescape=True,
    )
    template = jinja_env.get_template("index.html.j2")
    sorted_tournaments = sorted(
        manifest.items(),
        key=lambda item: (
            -item[1].start_date.timestamp(),  # type: ignore
            item[0],
        ),
    )
    index_html = template.render(
        tournaments=sorted_tournaments,
        has_embeddings=has_embeddings,
        has_any_data=has_any_data,
    )
    index_path = os.path.normpath(os.path.join(args.output_dir, "../index.html"))
    with open(index_path, "w", encoding="utf-8") as f_index:
        f_index.write(index_html)
    LOGGER.info("Wrote index to %s", index_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
