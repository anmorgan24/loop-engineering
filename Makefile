.PHONY: seed smoke cost core all report clean

seed:     ## build the database
	python -m agent.db

smoke:    ## full loop + ablation test, no API key needed
	python -m evals.smoke

cost:     ## 3 tasks, one arm, to measure spend before committing
	python -m evals.run_corpus --arm engineered --limit 3

core:     ## naive + engineered + 2 ablations, 3 trials each
	python -m evals.run_corpus --arm core --trials 3

pair:     ## just naive vs engineered, 3 trials
	python -m evals.run_corpus --arm naive engineered --trials 3

report:   ## table + out/exit_reasons.png + out/ablation.png
	python -m evals.report

clean:
	rm -f out/*.json out/*.png data/*.duckdb
