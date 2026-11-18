-- Phase 2: Supabase Storage bucket for uploaded broker files (CLAUDE.md rule 7: "Upload creates an
-- `imports` row and stores the file in Supabase Storage"). The imports table stores only the
-- object path, never the file.
--
-- The backend writes and reads these objects with the *caller's own JWT*, not a service key, so
-- Storage's row level security below is what keeps users apart: each user can only touch objects
-- under a top-level folder named after their own user id ("<auth.uid()>/<import id>/<file>").
-- The backend builds that path itself from the verified JWT (rule 13); these policies are the
-- second lock on the same door.

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'imports',
  'imports',
  false,                         -- private: no public URLs, every read is authenticated
  5242880,                       -- 5 MB; a year of trades is a few hundred KB
  array[
    'text/csv',
    'application/vnd.ms-excel',  -- some browsers label .csv files this way
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
  ]
)
on conflict (id) do nothing;

create policy imports_objects_insert_own on storage.objects
  for insert to authenticated
  with check (bucket_id = 'imports' and (storage.foldername(name))[1] = (select auth.uid())::text);

create policy imports_objects_select_own on storage.objects
  for select to authenticated
  using (bucket_id = 'imports' and (storage.foldername(name))[1] = (select auth.uid())::text);

-- No update or delete policy: an uploaded file is the evidence behind an import and is kept
-- (rule 2's spirit; a discarded import keeps its file, only its status changes).
