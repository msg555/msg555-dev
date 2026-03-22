import argparse
import hashlib
import logging
import os
import sys
import time

import anthropic
import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm

from mtgparse.data_model import Deck
from mtgparse.json_tournament import JsonTournament

ANTHROPIC_MODEL = "claude-sonnet-4-6"
LOGGER = logging.getLogger(__name__)


def calculate_cost(model: str, usage) -> float:
    """
    Calculate cost from a response's usage object.
    Returns a breakdown dict plus total.
    """
    MODEL_PRICING = {
        "claude-opus-4-6": {
            "input": 5.00,
            "output": 25.00,
            "cache_write": 6.25,  # 1.25x input price
            "cache_read": 0.50,  # 0.10x input price
        },
        "claude-sonnet-4-6": {
            "input": 3.00,
            "output": 15.00,
            "cache_write": 3.75,
            "cache_read": 0.30,
        },
        "claude-haiku-4-5": {
            "input": 1.00,
            "output": 5.00,
            "cache_write": 1.25,
            "cache_read": 0.10,
        },
    }
    pricing = MODEL_PRICING.get(model)
    if not pricing:
        raise ValueError(f"Unknown model: {model}")

    input_tokens = usage.input_tokens
    output_tokens = usage.output_tokens
    cache_write_tokens = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read_tokens = getattr(usage, "cache_read_input_tokens", 0) or 0

    # Tokens billed as normal input = input_tokens minus any that were cache reads/writes
    # (the API already separates them, so input_tokens here is the uncached portion)
    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    cache_write_cost = (cache_write_tokens / 1_000_000) * pricing["cache_write"]
    cache_read_cost = (cache_read_tokens / 1_000_000) * pricing["cache_read"]
    LOGGER.info(
        "Cost components %.6f %.6f %.6f %.6f",
        input_cost,
        output_cost,
        cache_read_cost,
        cache_write_cost,
    )

    return input_cost + output_cost + cache_write_cost + cache_read_cost


def _extract_archetype(response_text: str) -> str:
    lines = [line for line in response_text.split("\n") if line]
    return lines[-1]


def _anthropic_label_decks(
    arch_desc_md: str,
    decks: list[Deck],
    *,
    cache_path: str = "cache/labels",
    force: bool = False,
) -> None:
    client = anthropic.Anthropic()

    if not os.path.exists(cache_path):
        os.makedirs(cache_path)

    system_prompt = (
        """# Introduction

You are tasked with labelling decks from Magic: The Gathering played at actual tournaments. You will be given the quantity and name of each card in both the main deck and the sideboard of the deck you need to label. To help you accurately label, the next section includes a description of each archetype.

"""
        + arch_desc_md
        + """

# Your Task

Following this prompt you will be given a simple text description of a deck. You must respond with
your labelling of the deck archetype. DO NOT INCLUDE OTHER THOUGHTS.

In the case the deck does not appear to fall under one of the descriptions below please output
"Other - Some-Name" where 'Some-Name' is your attempt at naming the deck yourself. When coming up
with your own deck name you should usually name it by its color and key theme. For instance, a
black, red, and blue deck that made heavy use of the madness mechanic could be called 'Grixis
Madness'.

"""
        + arch_desc_md
    )

    queries = []
    for deck in decks:
        main_deck = sorted(f"- {card.count} {card.name}" for card in deck.main_deck)
        side_board = sorted(f"- {card.count} {card.name}" for card in deck.side_board)
        deck_desc = "\n".join(
            (
                "# Main Deck",
                *main_deck,
                "",
                "# Side board",
                *side_board,
            )
        )
        deck_hash = hashlib.sha256(deck_desc.encode("utf-8")).hexdigest()

        deck_cache_path = os.path.join(cache_path, f"{deck_hash}.txt")
        if not force and os.path.exists(deck_cache_path):
            with open(deck_cache_path, encoding="utf-8") as fdeck:
                deck.archetype = _extract_archetype(fdeck.read())
                continue

        queries.append((deck, deck_hash, deck_desc))

    total_cost = 0.0
    BATCH_SIZE = 1024

    for index in tqdm.trange(0, len(queries), BATCH_SIZE):
        batch_queries = queries[index : index + BATCH_SIZE]

        batch = client.messages.batches.create(
            requests=[
                {
                    "custom_id": deck_hash,
                    "params": {
                        "model": "claude-sonnet-4-6",
                        "max_tokens": 64,
                        "system": [
                            {
                                "type": "text",
                                "text": system_prompt,
                                "cache_control": {"type": "ephemeral"},
                            }
                        ],
                        "messages": [
                            {
                                "role": "user",
                                "content": deck_desc,
                            },
                        ],
                    },
                }
                for _, deck_hash, deck_desc in batch_queries
            ]
        )

        LOGGER.info("Batched %d queries with id %s", len(batch_queries), batch.id)
        while True:
            status = client.beta.messages.batches.retrieve(batch.id)
            if status.processing_status == "ended":
                break
            LOGGER.info("Still processing... (%s)", status.request_counts)
            time.sleep(10)

        deck_mapping = {deck_hash: deck for deck, deck_hash, _ in batch_queries}
        for result in client.beta.messages.batches.results(batch.id):
            deck = deck_mapping[result.custom_id]
            if result.result.type == "succeeded":
                LOGGER.info("Got result for %s: %s", deck.url, deck.archetype)
                deck.archetype = _extract_archetype(
                    result.result.message.content[0].text  # type: ignore
                )

                deck_cache_path = os.path.join(cache_path, f"{result.custom_id}.txt")
                with open(deck_cache_path, "w", encoding="utf-8") as fdeck:
                    fdeck.write(deck.archetype)
            else:
                LOGGER.warning("Failed to generate label for %s", deck.url)

            query_cost = 0.5 * calculate_cost(
                ANTHROPIC_MODEL, result.result.message.usage  # type: ignore
            )
            total_cost += query_cost
            LOGGER.info(
                "Batch query cost: $%.6f - Total cost: $%.6f", query_cost, total_cost
            )


def parse_args():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument(
        "-i",
        "--input",
        default="tournament.json",
        help="tournament json file",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="tournament.json",
        help="updated output tournament file",
    )
    parser.add_argument(
        "--arch",
        required=True,
        help="markdown describing decks in the format",
    )
    parser.add_argument(
        "--cache-path",
        default="cache/labels",
        help="path to labeling cache",
    )
    parser.add_argument(
        "--force",
        action="store_const",
        default=False,
        const=True,
        help="always generate new labels, ignoring existing cache",
    )
    return parser.parse_args()


def label_decks(
    arch_md_path: str,
    decks: list[Deck],
    *,
    cache_path: str = "cache/labels",
    force: bool = False,
) -> None:
    decks_to_label: list[Deck] = []
    for deck in decks:
        if not deck.main_deck:
            deck.archetype = "No Decklist"
            continue
        # if deck.archetype.lower() not in ("unknown", "other", "others", "decklist"):
        #     continue
        decks_to_label.append(deck)

    with open(arch_md_path, "r", encoding="utf-8") as farch:
        arch_desc_md = farch.read()

    with logging_redirect_tqdm():
        _anthropic_label_decks(
            arch_desc_md,
            decks_to_label,
            cache_path=cache_path,
            force=force,
        )


def main():
    logging.basicConfig(level=logging.INFO)
    args = parse_args()

    tour = JsonTournament.from_file(args.input)
    label_decks(
        args.arch,
        [player.deck for player in tour.get_players().values()],
        cache_path=args.cache_path,
        force=args.force,
    )
    tour.save_file(args.output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
