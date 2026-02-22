
import dataclasses

def bo3_match_prob(game_prob: float) -> float:
    return game_prob * game_prob + game_prob * game_prob * (1 - game_prob) * 2


@dataclasses.dataclass
class GameMode:
    best_of: int
    win_prob: float
    loss_steps: int
    win_steps: int
    protection_used: int


def solve_match(wins: int, losses: int, mode: GameMode, loss_cost: float, win_cost: float) -> float:
    if wins * 2 > mode.best_of:
        return win_cost
    if losses * 2 > mode.best_of:
        return loss_cost
    result = (
        1 +
        mode.win_prob * solve_match(wins + 1, losses, mode, loss_cost, win_cost) +
        (1.0 - mode.win_prob) * solve_match(wins, losses + 1, mode, loss_cost, win_cost)
    )
    if wins or losses:
        # Can always throw match
        result = min(result, loss_cost)
    return result

def solve(
    game_modes: list[GameMode],
    steps_per_tier: int = 6,
    num_tiers: int = 4,
    max_protection: int = 3,
) -> tuple[float, int]:
    states = []
    for tier in range(num_tiers):
        for step in range(steps_per_tier):
            states.append((step, tier, 0))
            if step <= 2 and tier > 0:
                for protection in range(max_protection):
                    states.append((step, tier, 1 + protection))

    end_state = (0, num_tiers, max_protection)
    states.append(end_state)
    state_index = {state: index for index, state in enumerate(states)}

    last = 0
    e_games = [(0, 0) for tier, step, _ in states]
    while True:
        ne_games = []
        for step, tier, protection in states:
            if tier == num_tiers:
                ne_games.append((0, 0))
                continue

            costs = []
            for mode_index, mode in enumerate(game_modes):
                if step >= mode.loss_steps:
                    loss_state = (step - mode.loss_steps, tier, max(0, protection - mode.protection_used))
                elif protection >= mode.protection_used:
                    loss_state = (0, tier, protection - mode.protection_used)
                elif tier:
                    loss_state = (step + steps_per_tier - mode.loss_steps, tier - 1,  0)
                else:
                    loss_state = (0, 0, 0)

                if step + mode.win_steps >= steps_per_tier:
                    if tier + 1 == num_tiers:
                        win_state = end_state
                    else:
                        win_state = (step + mode.win_steps - steps_per_tier, tier + 1, max_protection)
                else:
                    win_state = (step + mode.win_steps, tier, 0 if step + mode.win_steps >= 2 else max(0, protection - mode.protection_used))

                costs.append((
                    solve_match(
                        0,
                        0,
                        mode,
                        e_games[state_index[loss_state]][0],
                        e_games[state_index[win_state]][0]
                    ),
                    mode_index
                ))

            ne_games.append(min(costs))

        e_games = ne_games

        if abs(e_games[0][0] - last) < 1e-9:
            break
        last = e_games[0][0]

    return e_games[0]


def solve_all(win_prob: float) -> list[float]:
    result = [0.0, 0.0, 0.0]
    
    ranks = [
        (1, 0, 2, 0, 2), # bronze
        (2, 1, 2, 2, 2), # silver/gold
        (2, 1, 1, 2, 2), # plat/diamond
    ]
    for weight, bo1_loss, bo1_win, bo3_loss, bo3_win in ranks:
        bo1 = GameMode(1, win_prob, bo1_loss, bo1_win, 1)
        bo3 = GameMode(3, win_prob, bo3_loss, bo3_win, 2)
        result[0] += weight * solve([bo1])[0]
        result[1] += weight * solve([bo3])[0]
        result[2] += weight * solve([bo1, bo3])[0]
        print(solve([bo1, bo3]))

    return result


if __name__ == "__main__":
    win_prob = 0.70
    print(solve_all(win_prob))

