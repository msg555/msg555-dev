from pydantic import BaseModel

from mtgparse.data_model import MatchResult, Player, Tournament


class TournamentModel(BaseModel):
    title: str = ""
    source_url: str = ""
    limited_rounds: list[int] = []
    top_cut_rounds: int = 3
    start_date: str = ""
    players: dict[str, Player]
    round_results: list[list[MatchResult]]


class JsonTournament(Tournament):
    def __init__(self, model: TournamentModel) -> None:
        self.model = model

    @classmethod
    def from_file(cls, path: str) -> "JsonTournament":
        with open(path, "r", encoding="utf-8") as fdata:
            data = fdata.read()
        return cls(TournamentModel.model_validate_json(data))

    @classmethod
    def from_tournament(cls, tour: Tournament) -> "JsonTournament":
        return cls(
            TournamentModel(
                players=tour.get_players(),
                round_results=tour.get_round_results(),
            )
        )

    def save_file(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fdata:
            fdata.write(self.model.model_dump_json())

    def get_players(self) -> dict[str, Player]:
        return self.model.players

    def get_round_results(self) -> list[list[MatchResult]]:
        return self.model.round_results
