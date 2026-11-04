# E08 WF04: table audit

Protocol: `08_workflows/WF04_protocol.json`.

The runner uses only the synthetic qPCR fixture, checks the named sheet and
cell coordinates, and verifies the generated Excel artifact. It must not call
web retrieval for a table-only request.
