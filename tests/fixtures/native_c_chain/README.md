# Native C package chain fixture

This Linux x86-64 fixture exercises three-package static archive linking, one
package-local C shim and the `anx test` native root. `libmid.a` contains
`mid_value` and `mid_bridge`; `libleaf.a` needs `mid_bridge`, so linking the
archives once in consumer-to-dependency order fails without a linker group.
Together, the app shim and archives return 42. The `testkit` archive is a
dev-only dependency and returns 7 from the single test source.

The archives are local test inputs and are not tracked. Generate them before
running the fixture:

```sh
python tests/fixtures/native_c_chain/build_archives.py
```

With `anx`, `yianc` and `YIAN_LIB` built from the current checkout, the focused
acceptance commands are:

```sh
anx build tests/fixtures/native_c_chain
anx run tests/fixtures/native_c_chain
anx test tests/fixtures/native_c_chain --pointer-mode fat
```

Expected app output is `42`; the test output is `42 and test-only 7`. The test
package map must carry the app's native shim through `nativeRoots`, while the
app remains unavailable as an imported package. Normal build must not reach
the `testkit` archive. These commands are local acceptance probes, not a
benchmark workload.
