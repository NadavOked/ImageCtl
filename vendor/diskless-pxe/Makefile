.PHONY: render test verify package hostcheck
render:
	./bin/render-ipxe.sh
verify:
	./tools/verify-artifacts.sh .
test: render verify
	./ci/run.sh
package: test
	./bin/package.sh
hostcheck:
	./tools/check-host.sh
