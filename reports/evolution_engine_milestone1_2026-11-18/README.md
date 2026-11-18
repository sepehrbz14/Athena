# Evolution Engine Milestone 1

This dated package implements only the immutable experiment registry described by the frozen Milestone 0 protocol. It does not generate candidates, evaluate strategies, optimize parameters, or access prospective holdout data.

The registry is a new, exclusive directory containing `registry.sqlite3`. Create it through `ExperimentRegistry.create(path)`, register a frozen protocol together with the entire attempt batch through `register_protocol_with_attempts(...)`, and only then validate or disposition each attempt. The registration call atomically writes the protocol, raw submitted bytes and hashes, all attempt IDs, and the sealed manifest.

Run independent on-disk verification with:

```powershell
python -m crypto_bot.experiment_registry <registry-directory>
```

The verifier checks the SQLite integrity result, pinned Milestone 0 schemas, immutable candidate-byte hashes, protocol and attempt identities, sealed manifests, cumulative family limits, lifecycle transitions, and the hash-chained event log. Supply `--expected-tip-sha256` when comparing against a separately retained trusted checkpoint.

The fixed unsigned 64-bit D0 seed derivation is unchanged. Registry event records encode the derived seed as a base-10 string to preserve all 64 bits under RFC 8785 canonical JSON's IEEE-754 number model.
