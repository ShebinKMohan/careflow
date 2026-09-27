# Retrieval questions (M2-08)

`retrieval.jsonl`: one labelled question per line, for `eval/rag/run_retrieval.py`.

- `id`, `lang` (`en` or `ar`), `question`, and `gold_section_ids`: the handbook sections
  (`services/rag/handbook`) that answer it. Any one of them counts as a hit.
- 60 answerable questions, 30 English and 30 Arabic. Every one of the handbook's 25 sections is
  the answer to at least one question in each language.
- 10 out-of-scope questions (`*-out-*`, 5 per language) with no gold section. They are not
  scored. Their best dense cosine shows where an "I don't know" threshold would have to sit
  (M2-09).

The questions were written by the coding agent (`agent:claude-code/claude-opus-5-5`) after it had
read the handbook, in plain words a visitor might use, and deliberately not copied from it. They
were written before any retrieval run, and nothing was tuned on them (held out). They are not real
users' questions: the report says so. Synthetic; the clinic is fictional.
