PY := app/asr-venv/bin/python

.PHONY: test test-pr test-ci test-contracts test-live test-vm test-python test-swift test-bats coverage venv

test:            ## everything but the real-model checks
	scripts/test.sh

test-pr:         ## the same on the committed tree, with a report; MC_CI_POST=1 also posts it (every pull request)
	scripts/ci.sh local

test-ci:         ## the same in a macOS 15 virtual machine set up from nothing, with a report (before a release)
	scripts/ci.sh vm

test-contracts:  ## the fakes' assumptions against this Mac's real Minutes and launchd
	scripts/test.sh --only contracts --contracts

test-live:       ## plus real models, real Claude, fresh-HOME install; needs MC_LIVE_MEETING (before a release)
	scripts/test.sh --live

test-vm:         ## the built .dmg on a fresh macOS 15 virtual machine (Tart; before a release)
	scripts/test-vm.sh

test-python:
	scripts/test.sh --only python

test-swift:
	scripts/test.sh --only swift

test-bats:
	scripts/test.sh --only bats

coverage: test-python
	open app/.coverage-html/index.html

venv:            ## the Python environment without the models or the service
	./install.sh --no-service
