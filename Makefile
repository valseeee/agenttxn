.PHONY: test demo compile
test:
	python -m unittest discover -s tests -v
demo:
	python examples/demo_transaction.py
compile:
	python -m compileall -q src tests examples
