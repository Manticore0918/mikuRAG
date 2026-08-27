# Gold evaluation corpus v1

This directory contains a wholly synthetic, redistributable gold set for
mikuRAG retrieval evaluation. No statement describes a real organization,
person, customer, incident, credential, or internal policy. Identifiers,
hostnames, URLs, limits, and procedures are fictional test data.

The corpus is licensed under `CC0-1.0`; see `LICENSE.txt`. It contains Markdown,
HTML, PDF, Python, and TypeScript sources so source-specific locator behavior is
part of the evaluation rather than simulated in observations.

`manifest.json` contains 64 reviewed questions. Each qrel points to stable
passage and locator IDs whose `locator_match` must resolve against the real
extractor output. The set deliberately includes unsupported and conflicting
evidence cases. `headline_eligible` remains false until split discipline,
graded qrels, and a frozen provider-backed report are completed.

Do not edit this version in place after results are published. Copy it to a new
version directory and assign new IDs whenever source facts, qrels, or questions
change.
