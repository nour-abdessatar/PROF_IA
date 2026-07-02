
revoke execute on function public.has_role(uuid, public.app_role) from public, anon, authenticated;
revoke execute on function public.handle_new_user() from public, anon, authenticated;

-- Tighten bucket listing: only allow reading specific objects, not listing the whole bucket via API. Public read of individual files still works via public URL.
drop policy if exists "course_files_public_read" on storage.objects;
create policy "course_files_auth_read" on storage.objects
  for select to authenticated using (bucket_id = 'course-files');
-- Bucket stays public so the public URL of an uploaded PDF still resolves; only directory listing is restricted.
