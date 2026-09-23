.PHONY: help seed audit smoke cost core ladder pair report clean

.DEFAULT_GOAL := help

help:     ## list targets
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-8s %s\n", $$1, $$2}'

seed:     ## build the database
	python -m agent.db

audit:    ## check questions for ties, ambiguity and weak invariants
	python -m evals.audit

smoke:    ## full loop + ablation test, no API key needed
	python -m evals.smoke

cost:     ## 3 tasks, one arm, to measure spend (writes out/engineered__limit3.json, ignored by report)
	python -m evals.run_corpus --arm engineered --limit 3

core:     ## naive + engineered + 3 ablations, 3 trials each
	python -m evals.run_corpus --arm core --trials 3

ladder:   ## the verification ladder: none vs answer-free vs calibrated
	python -m evals.run_corpus --arm no_invariants answer_free engineered --trials 3

pair:     ## just naive vs engineered, 3 trials
	python -m evals.run_corpus --arm naive engineered --trials 3

report:   ## table + out/exit_reasons.png + out/ablation.png
	python -m evals.report

clean:
	rm -f out/*.json out/*.png data/*.duckdb
