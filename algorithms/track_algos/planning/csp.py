"""A constraint satisfaction problem (CSP) and its solver.

A CSP is three things:

    variables     the decisions to make            which roads to change
    domains       the options for each decision    two-way, one-way, closed
    constraints   rules every answer must obey     nobody is trapped, at most 2 changes

The solver is backtracking search with forward checking:

    1. Pick the next undecided variable.
    2. Try each value still left in its domain.
    3. Forward check: for every variable still undecided, cross out the values
       that would now break a rule. If some variable has nothing left, this
       choice cannot lead to an answer: undo it and try the next value
       (that is the backtrack).
    4. When every variable has a value, the assignment is a solution.

It returns every solution, because the road-change planner scores them all.

A constraint is a function that takes a partial assignment (a dict of the
variables decided so far) and returns False only when that assignment already
breaks the rule, whatever the undecided variables turn out to be.
"""


class CSP:
    def __init__(self, variables: list, domains: dict, constraints: list):
        self.variables = list(variables)
        self.domains = {v: list(domains[v]) for v in self.variables}
        self.constraints = list(constraints)
        # what the search did, for the map and the report
        self.tried = 0          # values tried
        self.crossed_out = 0    # values removed by forward checking
        self.dead_ends = 0      # choices undone because a domain emptied

    def consistent(self, assignment: dict) -> bool:
        return all(rule(assignment) for rule in self.constraints)

    def solve(self, progress=None) -> list[dict]:
        self.progress = progress
        solutions: list[dict] = []
        domains = self._forward_check({}, self.domains)
        if domains is not None:
            self._search({}, domains, solutions)
        return solutions

    def _search(self, assignment: dict, domains: dict, solutions: list) -> None:
        if len(assignment) == len(self.variables):
            solutions.append(dict(assignment))
            return
        variable = next(v for v in self.variables if v not in assignment)
        for value in domains[variable]:
            self.tried += 1
            if self.progress and self.tried % 50 == 0:
                self.progress.step("CSP", f"{self.tried:,} choices tried, {len(solutions):,} valid "
                                          f"layouts, {self.dead_ends:,} dead ends")
            assignment[variable] = value
            remaining = self._forward_check(assignment, domains)
            if remaining is None:
                self.dead_ends += 1
            else:
                self._search(assignment, remaining, solutions)
            del assignment[variable]

    def _forward_check(self, assignment: dict, domains: dict) -> dict | None:
        """Each undecided variable keeps only the values that still fit the
        rules next to what is decided. None if any variable is left with none."""
        remaining = {}
        for variable in self.variables:
            if variable in assignment:
                remaining[variable] = [assignment[variable]]
                continue
            keep = [value for value in domains[variable]
                    if self.consistent({**assignment, variable: value})]
            self.crossed_out += len(domains[variable]) - len(keep)
            if not keep:
                return None
            remaining[variable] = keep
        return remaining
