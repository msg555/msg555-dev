package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"math"
	"math/rand"
	"os"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"sync"
)

// ─── Data model ───────────────────────────────────────────────────────────────

type Card struct {
	Name  string `json:"name"`
	Count int    `json:"count"`
}

type Deck struct {
	MainDeck  []Card  `json:"main_deck"`
	SideBoard []Card  `json:"side_board"`
	Archetype string  `json:"archetype"`
	Author    *string `json:"author"`
	URL       *string `json:"url"`
}

type Player struct {
	Ident string  `json:"ident"`
	Name  string  `json:"name"`
	Deck  Deck    `json:"deck"`
	URL   *string `json:"url"`
}

// MatchResult defaults complete=true when absent, matching the Python pydantic model.
type MatchResult struct {
	P1       string
	P2       *string
	Games    [3]int
	Complete bool
}

func (mr *MatchResult) UnmarshalJSON(data []byte) error {
	var raw struct {
		P1       string  `json:"p1"`
		P2       *string `json:"p2"`
		Games    [3]int  `json:"games"`
		Complete *bool   `json:"complete"`
	}
	if err := json.Unmarshal(data, &raw); err != nil {
		return err
	}
	mr.P1 = raw.P1
	mr.P2 = raw.P2
	mr.Games = raw.Games
	mr.Complete = raw.Complete == nil || *raw.Complete
	return nil
}

type Tournament struct {
	Title         string              `json:"title"`
	SourceURL     string              `json:"source_url"`
	LimitedRounds []int               `json:"limited_rounds"`
	TopCutRounds  int                 `json:"top_cut_rounds"`
	Players       map[string]Player   `json:"players"`
	RoundResults  [][]MatchResult     `json:"round_results"`
}

func archetype(deck Deck) string {
	if deck.Archetype == "" {
		return "unknown"
	}
	return deck.Archetype
}

// ─── Player tracking ──────────────────────────────────────────────────────────

const minPercentage = 1.0 / 3.0

type PlayerData struct {
	topCutRoundIdx    int
	topCutPoints      int
	points            int
	constructedPoints int
	rounds            int
	matchRecord       [3]int
	constructedRecord [3]int
	gameRecord        [3]int
}

func (pd *PlayerData) clone() *PlayerData {
	cp := *pd
	return &cp
}

func (pd *PlayerData) recordMatch(games [3]int, reverse bool, limited bool) {
	pd.rounds++
	if reverse {
		games[0], games[1] = games[1], games[0]
	}
	var match [3]int
	var points int
	switch {
	case games[0] > games[1]:
		match, points = [3]int{1, 0, 0}, 3
	case games[1] > games[0]:
		match, points = [3]int{0, 1, 0}, 0
	default:
		match, points = [3]int{0, 0, 1}, 1
	}
	for i := range pd.matchRecord {
		pd.matchRecord[i] += match[i]
	}
	if !limited {
		for i := range pd.constructedRecord {
			pd.constructedRecord[i] += match[i]
		}
	}
	if pd.rounds <= pd.topCutRoundIdx {
		pd.points += points
		for i := range pd.gameRecord {
			pd.gameRecord[i] += games[i]
		}
		if !limited {
			pd.constructedPoints += points
		}
	} else {
		pd.topCutPoints += points
	}
}

func (pd *PlayerData) matchWinPct() float64 {
	if pd.rounds == 0 {
		return 0.5
	}
	rounds := pd.rounds
	if rounds > pd.topCutRoundIdx {
		rounds = pd.topCutRoundIdx
	}
	mwp := float64(pd.points) / float64(3*rounds)
	if mwp < minPercentage {
		return minPercentage
	}
	return mwp
}

func (pd *PlayerData) gameWinPct() float64 {
	total := pd.gameRecord[0] + pd.gameRecord[1] + pd.gameRecord[2]
	if total == 0 {
		return 0.5
	}
	gwp := float64(pd.gameRecord[0]*3+pd.gameRecord[2]) / float64(3*total)
	if gwp < minPercentage {
		return minPercentage
	}
	return gwp
}

// ─── Tiebreaker key ───────────────────────────────────────────────────────────

// tieKey mirrors the Python tiebreakers() tuple. Smaller = better rank.
type tieKey struct {
	negTopCutPoints int
	negPoints       int
	negOMW          float64
	negGW           float64
	negOGW          float64
	playerID        string
}

func (a tieKey) less(b tieKey) bool {
	if a.negTopCutPoints != b.negTopCutPoints {
		return a.negTopCutPoints < b.negTopCutPoints
	}
	if a.negPoints != b.negPoints {
		return a.negPoints < b.negPoints
	}
	if a.negOMW != b.negOMW {
		return a.negOMW < b.negOMW
	}
	if a.negGW != b.negGW {
		return a.negGW < b.negGW
	}
	if a.negOGW != b.negOGW {
		return a.negOGW < b.negOGW
	}
	return a.playerID < b.playerID
}

func makeTieKey(pid string, playerData map[string]*PlayerData, playerMatchups map[string][]string) tieKey {
	pd := playerData[pid]
	opps := playerMatchups[pid]
	var omw, ogw float64
	n := len(opps)
	if n == 0 {
		// Replicate melee.gg default for players with no opponents
		omw, ogw = 0.3333, 0.3333
		n = 1
	} else {
		for _, opp := range opps {
			omw += playerData[opp].matchWinPct()
			ogw += playerData[opp].gameWinPct()
		}
	}
	fn := float64(n)
	return tieKey{
		negTopCutPoints: -pd.topCutPoints,
		negPoints:       -pd.points,
		negOMW:          -(omw / fn),
		negGW:           -pd.gameWinPct(),
		negOGW:          -(ogw / fn),
		playerID:        pid,
	}
}

// ─── Simulation stats ─────────────────────────────────────────────────────────

const maxPower = 9

type PlayerStats struct {
	pointThresholds []int
	topP2           [maxPower]int
	madeCutoff      []int
	rankBest        int
	rankWorst       int
	count           int
}

func newPlayerStats(thresholds []int) *PlayerStats {
	return &PlayerStats{
		pointThresholds: thresholds,
		madeCutoff:      make([]int, len(thresholds)),
		rankBest:        -1,
		rankWorst:       -1,
	}
}

func (ps *PlayerStats) recordRank(rank, points int) {
	ps.count++
	if ps.rankBest < 0 || rank < ps.rankBest {
		ps.rankBest = rank
	}
	if ps.rankWorst < 0 || rank > ps.rankWorst {
		ps.rankWorst = rank
	}
	for i := 0; i < maxPower; i++ {
		if rank < 1<<i {
			ps.topP2[i]++
		}
	}
	for i, threshold := range ps.pointThresholds {
		if points >= threshold {
			ps.madeCutoff[i]++
		}
	}
}

func (ps *PlayerStats) merge(other *PlayerStats) {
	ps.count += other.count
	if other.rankBest >= 0 && (ps.rankBest < 0 || other.rankBest < ps.rankBest) {
		ps.rankBest = other.rankBest
	}
	if other.rankWorst >= 0 && (ps.rankWorst < 0 || other.rankWorst > ps.rankWorst) {
		ps.rankWorst = other.rankWorst
	}
	for i := range ps.topP2 {
		ps.topP2[i] += other.topP2[i]
	}
	for i := range ps.madeCutoff {
		ps.madeCutoff[i] += other.madeCutoff[i]
	}
}

// ─── Beta distribution (Marsaglia-Tsang + Johnk) ────────────────────────────

func gammaSample(rng *rand.Rand, alpha float64) float64 {
	if alpha < 1.0 {
		return gammaSample(rng, 1.0+alpha) * math.Pow(rng.Float64(), 1.0/alpha)
	}
	d := alpha - 1.0/3.0
	c := 1.0 / math.Sqrt(9.0*d)
	for {
		x := rng.NormFloat64()
		v := 1.0 + c*x
		if v <= 0 {
			continue
		}
		v3 := v * v * v
		u := rng.Float64()
		if u < 1.0-0.0331*(x*x)*(x*x) {
			return d * v3
		}
		if math.Log(u) < 0.5*x*x+d*(1.0-v3+math.Log(v3)) {
			return d * v3
		}
	}
}

func betaSample(rng *rand.Rand, alpha, beta float64) float64 {
	x := gammaSample(rng, alpha)
	y := gammaSample(rng, beta)
	return x / (x + y)
}

// ─── Tournament bracket helpers ───────────────────────────────────────────────

func calcOrd(topCut int) []int {
	order := []int{0}
	for i := 0; i < topCut; i++ {
		n := make([]int, 0, len(order)*2)
		for _, v := range order {
			n = append(n, v, 2*len(order)-v-1)
		}
		order = n
	}
	return order
}

func getTopCut(orderedPlayers []string, topCutRounds int) ([]string, error) {
	need := 1 << topCutRounds
	if len(orderedPlayers) < need {
		return nil, fmt.Errorf("too few players for top cut: have %d, need %d", len(orderedPlayers), need)
	}
	ord := calcOrd(topCutRounds)
	result := make([]string, len(ord))
	for i, idx := range ord {
		result[i] = orderedPlayers[idx]
	}
	return result, nil
}

func sampleMatchups(rng *rand.Rand, matchups map[string]map[string][3]int) map[string]map[string]float64 {
	archs := make([]string, 0, len(matchups))
	for a := range matchups {
		archs = append(archs, a)
	}
	sort.Strings(archs)
	result := make(map[string]map[string]float64, len(archs))
	for _, a := range archs {
		result[a] = map[string]float64{a: 0.5}
	}
	for i, a1 := range archs {
		for _, a2 := range archs[i+1:] {
			g := matchups[a1][a2]
			prob := betaSample(rng, float64(8+g[0]), float64(8+g[1]))
			result[a1][a2] = prob
			result[a2][a1] = 1.0 - prob
		}
	}
	return result
}

// ─── Simulation state (deep-copyable) ────────────────────────────────────────

type simState struct {
	playerData     map[string]*PlayerData
	playerMatchups map[string][]string
	topCutPlayers  []string
}

func (s *simState) clone() *simState {
	pd := make(map[string]*PlayerData, len(s.playerData))
	for k, v := range s.playerData {
		pd[k] = v.clone()
	}
	pm := make(map[string][]string, len(s.playerMatchups))
	for k, v := range s.playerMatchups {
		cp := make([]string, len(v))
		copy(cp, v)
		pm[k] = cp
	}
	tcp := make([]string, len(s.topCutPlayers))
	copy(tcp, s.topCutPlayers)
	return &simState{playerData: pd, playerMatchups: pm, topCutPlayers: tcp}
}

// ─── calc_ranks ───────────────────────────────────────────────────────────────

func calcRanks(
	tour *Tournament,
	roundLimit int,
	topCutRounds int,
	simRounds int,
	requiredPoints map[int]int,
) map[string]map[string]any {
	players := tour.Players
	allRounds := tour.RoundResults
	roundTotal := len(allRounds)
	topCutRoundIdx := roundTotal - topCutRounds

	limitedRounds := make(map[int]bool, len(tour.LimitedRounds))
	for _, r := range tour.LimitedRounds {
		limitedRounds[r] = true
	}

	playerData := make(map[string]*PlayerData, len(players))
	for pid := range players {
		playerData[pid] = &PlayerData{topCutRoundIdx: topCutRoundIdx}
	}
	playerMatchups := make(map[string][]string, len(players))
	for pid := range players {
		playerMatchups[pid] = nil
	}
	var topCutPlayers []string

	tiebreakers := func(pid string) tieKey {
		return makeTieKey(pid, playerData, playerMatchups)
	}
	sortByTie := func(ids []string) {
		sort.SliceStable(ids, func(i, j int) bool {
			return tiebreakers(ids[i]).less(tiebreakers(ids[j]))
		})
	}

	archMatchup := make(map[string]map[string][3]int)
	hasRoundPending := make(map[string]bool)

	for roundIdx, roundResults := range allRounds {
		if roundLimit > 0 && roundLimit <= roundIdx {
			break
		}
		if len(roundResults) == 0 {
			break
		}
		if len(hasRoundPending) > 0 {
			log.Fatal("assertion failed: prior round has pending results")
		}

		// Entering top cut: seed the bracket
		if roundIdx == roundTotal-topCutRounds {
			rem := playersWithRounds(playerData, roundIdx)
			sortByTie(rem)
			tc, err := getTopCut(rem, topCutRounds)
			if err != nil {
				log.Fatal(err)
			}
			topCutPlayers = tc
		}

		seen := make(map[string]bool)
		for _, rr := range roundResults {
			if !rr.Complete {
				hasRoundPending[rr.P1] = true
				if rr.P2 != nil {
					hasRoundPending[*rr.P2] = true
				}
				continue
			}
			for _, pid := range pids(rr.P1, rr.P2) {
				if seen[pid] {
					log.Fatalf("player %s seen twice in round %d", pid, roundIdx)
				}
				if playerData[pid].rounds != roundIdx {
					log.Fatalf("player %s has wrong round count in round %d", pid, roundIdx)
				}
				seen[pid] = true
			}
			isLimited := limitedRounds[roundIdx]
			if rr.P2 == nil {
				if rr.Games[1] == 2 {
					playerData[rr.P1].recordMatch([3]int{0, 2, 0}, false, isLimited)
				} else {
					playerData[rr.P1].recordMatch([3]int{2, 0, 0}, false, isLimited)
				}
				continue
			}
			p2 := *rr.P2
			if roundIdx < topCutRoundIdx {
				playerMatchups[rr.P1] = append(playerMatchups[rr.P1], p2)
				playerMatchups[p2] = append(playerMatchups[p2], rr.P1)
			}
			playerData[rr.P1].recordMatch(rr.Games, false, isLimited)
			playerData[p2].recordMatch(rr.Games, true, isLimited)

			if !limitedRounds[roundIdx] {
				a1 := archetype(players[rr.P1].Deck)
				a2 := archetype(players[p2].Deck)
				g := rr.Games
				addMatchup(archMatchup, a1, a2, g)
				addMatchup(archMatchup, a2, a1, [3]int{g[1], g[0], g[2]})
			}
		}
	}

	// Disable sim if tournament is complete
	if len(allRounds) == 0 {
		simRounds = 0
	} else if last := allRounds[len(allRounds)-1]; len(last) > 0 {
		complete := true
		for _, rr := range last {
			if !rr.Complete {
				complete = false
				break
			}
		}
		if complete {
			simRounds = 0
		}
	}

	// Collect point thresholds in sorted order
	rpKeys := sortedKeys(requiredPoints)
	pointThresholds := make([]int, len(rpKeys))
	for i, k := range rpKeys {
		pointThresholds[i] = requiredPoints[k]
	}

	// ─── Monte Carlo simulation ───────────────────────────────────────────────

	playerStats := make(map[string]*PlayerStats)
	if simRounds > 0 {
		for pid := range players {
			playerStats[pid] = newPlayerStats(pointThresholds)
		}
		initState := &simState{
			playerData:     playerData,
			playerMatchups: playerMatchups,
			topCutPlayers:  topCutPlayers,
		}

		numWorkers := runtime.NumCPU()
		if numWorkers > simRounds {
			numWorkers = simRounds
		}

		type result struct{ stats map[string]*PlayerStats }
		results := make(chan result, numWorkers)

		var wg sync.WaitGroup
		base := simRounds / numWorkers
		extra := simRounds % numWorkers
		for w := 0; w < numWorkers; w++ {
			count := base
			if w < extra {
				count++
			}
			wg.Add(1)
			go func(count int) {
				defer wg.Done()
				rng := rand.New(rand.NewSource(rand.Int63()))
				localStats := make(map[string]*PlayerStats, len(players))
				for pid := range players {
					localStats[pid] = newPlayerStats(pointThresholds)
				}
				for i := 0; i < count; i++ {
					st := initState.clone()
					runSimulation(st, rng, players, allRounds, roundTotal, topCutRoundIdx, topCutRounds, limitedRounds, requiredPoints, archMatchup, localStats)
				}
				results <- result{stats: localStats}
			}(count)
		}
		go func() { wg.Wait(); close(results) }()

		for r := range results {
			for pid, s := range r.stats {
				playerStats[pid].merge(s)
			}
		}
	}

	// ─── Build output ─────────────────────────────────────────────────────────

	sorted := make([]string, 0, len(players))
	for pid := range players {
		sorted = append(sorted, pid)
	}
	sortByTie(sorted)

	output := make(map[string]map[string]any, len(players))
	for rank, pid := range sorted {
		pd := playerData[pid]
		tk := tiebreakers(pid)
		entry := map[string]any{
			"rank":          rank + 1,
			"record":        fmt.Sprintf("%d-%d-%d", pd.matchRecord[0], pd.matchRecord[1], pd.matchRecord[2]),
			"rounds":        pd.rounds,
			"round_pending": hasRoundPending[pid],
			"points":        pd.points,
			"omw":           -tk.negOMW,
			"gw":            pd.gameWinPct(),
			"ogw":           -tk.negOGW,
		}
		if len(tour.LimitedRounds) > 0 {
			entry["constructed_record"] = fmt.Sprintf("%d-%d-%d", pd.constructedRecord[0], pd.constructedRecord[1], pd.constructedRecord[2])
			entry["constructed_points"] = pd.constructedPoints
		}
		if stats, ok := playerStats[pid]; ok && stats.count > 0 {
			entry["rank_best"] = stats.rankBest + 1
			entry["rank_worst"] = stats.rankWorst + 1
			for i, threshold := range pointThresholds {
				entry[fmt.Sprintf("cutoff_%d", threshold)] = float64(stats.madeCutoff[i]) / float64(simRounds)
			}
			for i := 0; i < maxPower; i++ {
				entry[fmt.Sprintf("top_%d", 1<<i)] = float64(stats.topP2[i]) / float64(simRounds)
			}
		}
		output[pid] = entry
	}
	return output
}

// runSimulation runs one full simulation on a cloned state and records stats.
func runSimulation(
	st *simState,
	rng *rand.Rand,
	players map[string]Player,
	allRounds [][]MatchResult,
	roundTotal, topCutRoundIdx, topCutRounds int,
	limitedRounds map[int]bool,
	requiredPoints map[int]int,
	archMatchup map[string]map[string][3]int,
	localStats map[string]*PlayerStats,
) {
	archProbs := sampleMatchups(rng, archMatchup)

	localTie := func(pid string) tieKey {
		return makeTieKey(pid, st.playerData, st.playerMatchups)
	}
	localSort := func(ids []string) {
		sort.SliceStable(ids, func(i, j int) bool {
			return localTie(ids[i]).less(localTie(ids[j]))
		})
	}

	var simulateRound func(roundIdx int, partial bool)
	simulateRound = func(roundIdx int, partial bool) {
		// Active players: present this round and meeting point requirements
		rem := make([]string, 0, len(players))
		for pid, pd := range st.playerData {
			if pd.rounds == roundIdx && pd.points >= requiredPoints[roundIdx] {
				rem = append(rem, pid)
			}
		}

		type pair struct {
			p1 string
			p2 *string
		}
		var pairings []pair

		if partial {
			for _, mr := range allRounds[roundIdx] {
				if !mr.Complete {
					p2 := *mr.P2
					pairings = append(pairings, pair{mr.P1, &p2})
				}
			}
		} else if roundIdx < roundTotal-topCutRounds {
			if roundIdx+1 < topCutRoundIdx {
				// Random pairing within points bracket
				keys := make(map[string]float64, len(rem))
				for _, pid := range rem {
					keys[pid] = rng.Float64()
				}
				sort.SliceStable(rem, func(a, b int) bool {
					pa, pb := st.playerData[rem[a]].points, st.playerData[rem[b]].points
					if pa != pb {
						return pa < pb
					}
					return keys[rem[a]] < keys[rem[b]]
				})
			} else {
				localSort(rem)
			}
			// Power pair
			paired := make(map[string]bool, len(rem))
			for rankA, p1 := range rem {
				if paired[p1] {
					continue
				}
				past := make(map[string]bool, len(st.playerMatchups[p1]))
				for _, opp := range st.playerMatchups[p1] {
					past[opp] = true
				}
				found := false
				for rankB := rankA + 1; rankB < len(rem); rankB++ {
					p2 := rem[rankB]
					if !paired[p2] && !past[p2] {
						p2c := p2
						pairings = append(pairings, pair{p1, &p2c})
						paired[p2] = true
						found = true
						break
					}
				}
				if !found {
					pairings = append(pairings, pair{p1, nil})
				}
			}
		} else {
			// Top cut single elimination
			if roundIdx == topCutRoundIdx {
				localSort(rem)
				tc, err := getTopCut(rem, topCutRounds)
				if err != nil {
					log.Fatal(err)
				}
				st.topCutPlayers = tc
				rem = st.topCutPlayers
			} else {
				advancing := rem[:0]
				for _, pid := range st.topCutPlayers {
					if st.playerData[pid].topCutPoints/3 == roundIdx-topCutRoundIdx {
						advancing = append(advancing, pid)
					}
				}
				rem = advancing
			}
			for i := 0; i+1 < len(rem); i += 2 {
				p2c := rem[i+1]
				pairings = append(pairings, pair{rem[i], &p2c})
			}
		}

		// Record matchups before playing (needed for OMW calc in tiebreakers)
		if roundIdx < topCutRoundIdx {
			for _, pr := range pairings {
				if pr.p2 != nil {
					st.playerMatchups[pr.p1] = append(st.playerMatchups[pr.p1], *pr.p2)
					st.playerMatchups[*pr.p2] = append(st.playerMatchups[*pr.p2], pr.p1)
				}
			}
		}

		// Intentional draw detection (last Swiss round before top cut)
		intentionalDraws := make(map[[2]string]bool)
		if !partial && roundIdx+1 == topCutRoundIdx {
			cutOffRank := 1 << topCutRounds
			origBreakers := make(map[string]tieKey, len(rem))
			for _, pid := range rem {
				origBreakers[pid] = localTie(pid)
			}
			for _, pr := range pairings {
				if pr.p2 == nil {
					continue
				}
				p1, p2 := pr.p1, *pr.p2
				p2Break := origBreakers[p2]
				p2Break.negPoints-- // simulate p2 gaining 1 point from a draw

				worstRank := 1 // p1 is already ahead of p2
				for _, other := range pairings {
					if other.p2 == nil || other.p1 == p1 {
						continue
					}
					op1, op2 := other.p1, *other.p2
					worstCase := 0
					for _, outcome := range [][2]int{{1, 1}, {3, 0}, {0, 3}} {
						if intentionalDraws[[2]string{op1, op2}] && outcome != [2]int{1, 1} {
							continue
						}
						op1Break := origBreakers[op1]
						op2Break := origBreakers[op2]
						op1Break.negPoints -= outcome[0]
						op2Break.negPoints -= outcome[1]
						rank := 0
						if op1Break.less(p2Break) {
							rank++
						}
						if op2Break.less(p2Break) {
							rank++
						}
						if rank > worstCase {
							worstCase = rank
						}
					}
					worstRank += worstCase
					if worstRank >= cutOffRank || worstCase == 0 {
						break
					}
				}
				if worstRank < cutOffRank {
					intentionalDraws[[2]string{p1, p2}] = true
				}
			}
		}

		// Simulate matches
		isLimited := limitedRounds[roundIdx]
		for _, pr := range pairings {
			if pr.p2 == nil {
				st.playerData[pr.p1].recordMatch([3]int{2, 0, 0}, false, isLimited)
				continue
			}
			p1, p2 := pr.p1, *pr.p2
			if intentionalDraws[[2]string{p1, p2}] {
				st.playerData[p1].recordMatch([3]int{0, 0, 3}, false, isLimited)
				st.playerData[p2].recordMatch([3]int{0, 0, 3}, false, isLimited)
				continue
			}
			prob := 0.5
			if !isLimited {
				a1 := archetype(players[p1].Deck)
				a2 := archetype(players[p2].Deck)
				if ap, ok := archProbs[a1]; ok {
					if p, ok := ap[a2]; ok {
						prob = p
					}
				}
			}
			games := [2]int{}
			for games[0] < 2 && games[1] < 2 {
				if rng.Float64() < prob {
					games[0]++
				} else {
					games[1]++
				}
			}
			st.playerData[p1].recordMatch([3]int{games[0], games[1], 0}, false, isLimited)
			st.playerData[p2].recordMatch([3]int{games[1], games[0], 0}, false, isLimited)
		}
	}

	// Run each round
	for roundIdx, roundResults := range allRounds {
		if len(roundResults) == 0 {
			simulateRound(roundIdx, false)
		} else {
			anyIncomplete := false
			for _, rr := range roundResults {
				if !rr.Complete {
					anyIncomplete = true
					break
				}
			}
			if anyIncomplete {
				simulateRound(roundIdx, true)
			}
		}
	}

	// Record final rankings
	allPIDs := make([]string, 0, len(players))
	for pid := range players {
		allPIDs = append(allPIDs, pid)
	}
	sort.SliceStable(allPIDs, func(i, j int) bool {
		return localTie(allPIDs[i]).less(localTie(allPIDs[j]))
	})
	for rank, pid := range allPIDs {
		localStats[pid].recordRank(rank, st.playerData[pid].points)
	}
}

// ─── Small helpers ────────────────────────────────────────────────────────────

func playersWithRounds(playerData map[string]*PlayerData, rounds int) []string {
	result := make([]string, 0, len(playerData))
	for pid, pd := range playerData {
		if pd.rounds == rounds {
			result = append(result, pid)
		}
	}
	return result
}

func pids(p1 string, p2 *string) []string {
	if p2 == nil {
		return []string{p1}
	}
	return []string{p1, *p2}
}

func addMatchup(m map[string]map[string][3]int, a1, a2 string, g [3]int) {
	if m[a1] == nil {
		m[a1] = make(map[string][3]int)
	}
	cur := m[a1][a2]
	m[a1][a2] = [3]int{cur[0] + g[0], cur[1] + g[1], cur[2] + g[2]}
}

func sortedKeys(m map[int]int) []int {
	keys := make([]int, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Ints(keys)
	return keys
}

// ─── CLI ──────────────────────────────────────────────────────────────────────

func parseRequiredPoints(s string) (map[int]int, error) {
	result := make(map[int]int)
	if s == "" {
		return result, nil
	}
	for _, part := range strings.Split(s, ",") {
		kv := strings.SplitN(strings.TrimSpace(part), ":", 2)
		if len(kv) != 2 {
			return nil, fmt.Errorf("invalid entry %q: want roundIdx:points", part)
		}
		k, err := strconv.Atoi(strings.TrimSpace(kv[0]))
		if err != nil {
			return nil, fmt.Errorf("invalid round in %q: %w", part, err)
		}
		v, err := strconv.Atoi(strings.TrimSpace(kv[1]))
		if err != nil {
			return nil, fmt.Errorf("invalid points in %q: %w", part, err)
		}
		result[k] = v
	}
	return result, nil
}

func main() {
	var inputFile string
	flag.StringVar(&inputFile, "input", "tournament.json", "tournament JSON file")
	flag.StringVar(&inputFile, "i", "tournament.json", "tournament JSON file (shorthand)")
	roundsFlag := flag.Int("rounds", 0, "process only first N rounds (0 = all)")
	topCutFlag := flag.Int("top-cut", 3, "number of top-cut rounds")
	simRoundsFlag := flag.Int("sim-rounds", 0, "number of Monte Carlo simulation iterations")
	outputFlag := flag.String("output", "", "output file (default: stdout)")
	rpFlag := flag.String("required-points", "9:18", "required points per round, e.g. 9:18,12:24")
	flag.Parse()

	data, err := os.ReadFile(inputFile)
	if err != nil {
		log.Fatalf("reading %s: %v", inputFile, err)
	}
	var tour Tournament
	if err := json.Unmarshal(data, &tour); err != nil {
		log.Fatalf("parsing tournament JSON: %v", err)
	}

	rp, err := parseRequiredPoints(*rpFlag)
	if err != nil {
		log.Fatalf("--required-points: %v", err)
	}

	ranks := calcRanks(&tour, *roundsFlag, *topCutFlag, *simRoundsFlag, rp)

	out, err := json.Marshal(ranks)
	if err != nil {
		log.Fatalf("encoding output: %v", err)
	}

	if *outputFlag == "" {
		os.Stdout.Write(out)
	} else {
		if err := os.WriteFile(*outputFlag, out, 0644); err != nil {
			log.Fatalf("writing %s: %v", *outputFlag, err)
		}
	}
}
