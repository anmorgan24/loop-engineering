.PHONY: seed smoke naive engineered report all clean

seed:        ## build the database
	python -m agent.db

smoke:       ## full loop test, no API key needed
	python -m evals.smoke

naive:       ## the before corpus
	python -m evals.run_corpus --loop naive

engineered:  ## the after corpus
	python -m evals.run_corpus --loop engineered

report:      ## table + out/exit_reasons.png
	python -m evals.report

all: seed naive engineered report

clean:
	rm -f out/*.json out/*.png data/*.duckdb
