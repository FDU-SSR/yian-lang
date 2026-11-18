# Performance corpus

Three unmodified Kubernetes YAML documents vendored for the LIBYAML benchmark.
They are fixed by upstream commit and read without network access at runtime.
Both the C reference and the YIAN port parse the identical bytes.

- project: kube-prometheus
- repository: https://github.com/prometheus-operator/kube-prometheus
- commit: `b6c2f62b287524652592e6e2f7ea0d3054cb8ac6`
- license: Apache-2.0 (`LICENSE`)

| file | upstream path | bytes |
| --- | --- | ---: |
| `kubernetes-alertmanagerconfig-crd.yaml` | `manifests/setup/0alertmanagerConfigCustomResourceDefinition.yaml` | 768244 |
| `kubernetes-prometheus-rules.yaml` | `manifests/prometheus-prometheusRule.yaml` | 17233 |
| `kubernetes-prometheus.yaml` | `manifests/prometheus-prometheus.yaml` | 1339 |

`provenance.json` records the SHA-256 of every file. The selection covers a large
nested Kubernetes CRD schema, an alert-rule document and a compact resource
document. Each file is a single YAML document without directives; the benchmark
parses all three in the listed order per iteration. This is a fixed real-world
corpus, not a synthetic generator, and it is not claimed to represent every YAML
distribution.
