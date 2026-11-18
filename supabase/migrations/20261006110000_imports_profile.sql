-- Which broker format an import was recognised as ("Zerodha tradebook", "Mapped by AI", ...),
-- shown on the review screen. Purely descriptive; `parser` (RULES | LLM) stays the audit field.
alter table public.imports add column profile text;
