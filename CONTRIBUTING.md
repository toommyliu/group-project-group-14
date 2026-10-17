# Contributing

1. Create a feature branch (`git checkout -b feat/amazing-feature`)
2. Commit your changes (`git commit -m 'Add amazing feature'`)
3. Make sure the test suite passes (`uv run pytest`)
4. Push to the branch (`git push origin feat/amazing-feature`)
5. Open a Pull Request

### Branch Naming

- `feat/` - New features
- `fix/` - Bug fixes
- `docs/` - Documentation updates
- `refactor/` - Code refactoring

### Commit Messages

We recommend following [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) for consistent and readable commit history:

- `feat(auth): add user authentication endpoint`
- `fix(database): resolve connection timeout issue`
- `docs(readme): update setup instructions`

Alternatively, you can just use simple commit messages:

- `Add user authentication endpoint`
- `Fix database connection timeout issue`
- `Update README with setup instructions`
## AI-generated contributions

AI coding assistants (Claude Code, Copilot, Cursor, ChatGPT, …) are welcome here. This project itself was built with a lot of help from them. They make good contributions faster. They also make it easy to submit code nobody has understood. These rules keep the first and prevent the second. A pull request that breaks them will be sent back, however good the code looks.

1. **You are the author.** Whatever tool wrote it, you are answering for every line. Read the whole diff before you open the pull request, and be ready to explain any part of it in review. "The AI did that" is not an answer to a review question.
2. **No blind refactors.** If an assistant suggests a redesign, you must be able to explain why it is better: what problem it fixes, and what it costs. If you can't, don't submit it.
3. **Keep the diff the size of the change.** Assistants like to rewrite a whole file to change three lines, reorder imports, rename things and "tidy up" code nobody asked about. Check for that and undo it. Disproportionately large diffs for small changes will be rejected, because nobody can review them.
4. **Respect the existing design.** Don't replace a class, a module boundary or a data format because an assistant prefers another one. The existing design was usually thought through, and the reason is usually in the module's docstring. If you still think it is wrong, ask its original author before changing it. Assistants learn from a lot of average code, and the design already here is often the better one.
5. **Verify it yourself.** Run the tests (`uv run pytest`) and run the feature by hand. Read the tests the assistant wrote, too: generated tests often pass because they test nothing (they assert on a mock, or on what the code already returns). A fix needs a test that failed before it.
6. **Disclose it.** Say in the pull request when a substantial part was AI-generated, and which tool you used. One line is enough: *"Drafted with Claude Code; I rewrote the parser and checked the tests."* That is far easier for the reviewer than guessing from strange, unrelated changes. Adding a `Co-Authored-By:` line for the tool to the commit is also fine.
7. **Never give an assistant real student data.** Opengrader handles student work, which in the US is protected by FERPA. Don't paste real names, IDs, grades, scans or submission files into an AI tool, and don't run one on a real class folder. Use the files in `samples/`, or a copy made with `opengrader agent prepare`, which removes names and IDs. Never put API keys or passwords in a prompt either.
8. **Mind the license.** Opengrader is MIT-licensed. Don't submit generated code that reproduces another project's code under an incompatible license. If an assistant hands you a large block that looks copied from somewhere, find out where it came from first.

For assistants working on the code, `AGENTS.md` describes the project's rules, and each module's docstring says why it is built the way it is. Point your assistant at them before it starts.
