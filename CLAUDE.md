# For agents

A Claude Code plugin that puts Jev (TypeSafe's System One model) at four points around tool calls. The design and its
decisions live in the `jev-harness` repo under `product/` (`ARCHITECTURES.md`, "The converged design"). This repo is the
build. Nothing in it is admitted as the product design; only Zach admits, in a round there.

## How to verify

```sh
python3 -m unittest discover -s tests -t .      # unit tests, stubbed Jev, no network
sh bin/jev-switch                               # prints the switches
```

Try it in Claude Code with `claude --plugin-dir .`. It needs `TYPESAFE_API_KEY` in the environment; without it every
hook fails open with a notice.

## Rules

- Never print, log or commit the Jev key. Logs hold decisions and scores, never whole outputs.
- Every hook fails open: on any Jev error the call goes on as if the plugin were not here, and the user is told.
- Comments are short lowercase fragments. Plain sentences in docs, no em-dashes.
