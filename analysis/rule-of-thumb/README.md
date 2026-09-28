# Rule of thumb: how it was chosen and tested

`rule_test.py` scores simple fleet-sizing rules against the model in the paper
([The Heat Death of the Codebase?](https://poly.io/writing/heat-death-of-the-codebase/), equations 4–6),
on randomly drawn teams: one to six reviewers, 0–80% of checking automated, α 0.02–0.4, β 0.001–0.035
(log-uniform), p 0–0.05, baseline rework 0.2–0.5, 4–10 changes per agent per day, 150–300 reviewed lines
an hour, 3–5 review hours a day, 100–250 lines per change. Multi-project teams (two or three codebases
sharing their reviewers) are searched exhaustively over every split of agents between codebases.

Output is nearly flat past the best fleet size, so being close to the best output is easy. Each rule is
therefore scored on two things:

- **near**: its fleet gets at least 90% of the best finished output;
- **lean**: it runs no more than one agent per codebase beyond the smallest fleet that gets within 5% of
  the best (it doesn't pay for agents that add nothing);
- **good**: both.

The rule in the paper is *one agent per reviewer, divided by the share of checking still done by people,
at most five per codebase*. Headline results (`results.txt`, `results.json`, seed 1):

| | near | lean | good |
|---|---|---|---|
| Rule, one codebase (20,000 teams) | 0.84 | 0.82 | 0.67 |
| Rule, two codebases (4,000 teams) | 0.76 | 0.79 | 0.57 |
| Rule, three codebases (4,000 teams) | 0.69 | 0.81 | 0.54 |
| "Always 4", one codebase | 0.76 | 0.75 | 0.54 |
| "Always 5", one codebase | 0.81 | 0.58 | 0.47 |
| "Two per reviewer, max 6", one codebase | 0.86 | 0.57 | 0.47 |
| Sizing formula (equations 1–2), one codebase | 0.99 | 0.63 | 0.62 |

The rule and its cap were chosen on the same draws they are scored on; the candidates are all listed in
`results.txt`, and caps of four and five tie on the combined score.

```
python rule_test.py --n 20000 --seed 1 --json results.json > results.txt
```
Needs numpy.
