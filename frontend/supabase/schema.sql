-- =====================================================================
-- Prof IA — Supabase schema
-- Run this entire file in the Supabase SQL editor of your project
-- (https://app.supabase.com → SQL → New query → paste → Run).
-- =====================================================================

-- ---- Roles ----------------------------------------------------------
do $$ begin
  create type public.app_role as enum ('admin', 'student');
exception when duplicate_object then null; end $$;

-- ---- Profiles -------------------------------------------------------
create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text not null,
  username text,
  ai_name text,
  lang text default 'fr',
  level text,
  assessment_done boolean default false,
  created_at timestamptz default now()
);

alter table public.profiles enable row level security;

drop policy if exists "profiles_select_own" on public.profiles;
create policy "profiles_select_own" on public.profiles
  for select to authenticated using (auth.uid() = id);

drop policy if exists "profiles_update_own" on public.profiles;
create policy "profiles_update_own" on public.profiles
  for update to authenticated using (auth.uid() = id);

drop policy if exists "profiles_insert_own" on public.profiles;
create policy "profiles_insert_own" on public.profiles
  for insert to authenticated with check (auth.uid() = id);

-- ---- User roles (separate table — never on profiles) ---------------
create table if not exists public.user_roles (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  role public.app_role not null,
  unique (user_id, role)
);

alter table public.user_roles enable row level security;

drop policy if exists "user_roles_select_own" on public.user_roles;
create policy "user_roles_select_own" on public.user_roles
  for select to authenticated using (auth.uid() = user_id);

create or replace function public.has_role(_user_id uuid, _role public.app_role)
returns boolean
language sql stable security definer set search_path = public
as $$
  select exists (select 1 from public.user_roles where user_id = _user_id and role = _role)
$$;

-- ---- Auto-create profile + assign role on signup -------------------
-- The admin email is whitelisted here. Change it to match VITE_ADMIN_EMAIL.
create or replace function public.handle_new_user()
returns trigger
language plpgsql security definer set search_path = public
as $$
declare
  admin_email text := 'admin@college.tn'; -- <-- keep in sync with VITE_ADMIN_EMAIL
begin
  insert into public.profiles (id, email)
  values (new.id, new.email)
  on conflict (id) do nothing;

  if lower(new.email) = lower(admin_email) then
    insert into public.user_roles (user_id, role) values (new.id, 'admin')
    on conflict do nothing;
  else
    insert into public.user_roles (user_id, role) values (new.id, 'student')
    on conflict do nothing;
  end if;

  return new;
end $$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ---- Courses --------------------------------------------------------
create table if not exists public.courses (
  id uuid primary key default gen_random_uuid(),
  title text not null,
  description text,
  subject text not null,
  level text not null check (level in ('7','8','9')),
  pdf_path text,           -- path in 'course-files' storage bucket
  created_by uuid references auth.users(id) on delete set null,
  created_at timestamptz default now()
);

alter table public.courses enable row level security;

drop policy if exists "courses_select_all_auth" on public.courses;
create policy "courses_select_all_auth" on public.courses
  for select to authenticated using (true);

drop policy if exists "courses_admin_write" on public.courses;
create policy "courses_admin_write" on public.courses
  for all to authenticated
  using (public.has_role(auth.uid(), 'admin'))
  with check (public.has_role(auth.uid(), 'admin'));

-- ---- Level tests (QCM) ----------------------------------------------
create table if not exists public.level_tests (
  id uuid primary key default gen_random_uuid(),
  title text not null,
  subject text not null,
  level text not null check (level in ('7','8','9')),
  created_by uuid references auth.users(id) on delete set null,
  created_at timestamptz default now()
);

create table if not exists public.test_questions (
  id uuid primary key default gen_random_uuid(),
  test_id uuid not null references public.level_tests(id) on delete cascade,
  question text not null,
  choices jsonb not null,           -- ["A","B","C","D"]
  correct_index int not null,        -- 0..3
  position int default 0
);

alter table public.level_tests enable row level security;
alter table public.test_questions enable row level security;

drop policy if exists "tests_select_all_auth" on public.level_tests;
create policy "tests_select_all_auth" on public.level_tests
  for select to authenticated using (true);
drop policy if exists "tests_admin_write" on public.level_tests;
create policy "tests_admin_write" on public.level_tests
  for all to authenticated
  using (public.has_role(auth.uid(), 'admin'))
  with check (public.has_role(auth.uid(), 'admin'));

drop policy if exists "qs_select_all_auth" on public.test_questions;
create policy "qs_select_all_auth" on public.test_questions
  for select to authenticated using (true);
drop policy if exists "qs_admin_write" on public.test_questions;
create policy "qs_admin_write" on public.test_questions
  for all to authenticated
  using (public.has_role(auth.uid(), 'admin'))
  with check (public.has_role(auth.uid(), 'admin'));

-- ---- Storage bucket for course PDFs --------------------------------
insert into storage.buckets (id, name, public)
values ('course-files', 'course-files', true)
on conflict (id) do nothing;

drop policy if exists "course_files_public_read" on storage.objects;
create policy "course_files_public_read" on storage.objects
  for select to public using (bucket_id = 'course-files');

drop policy if exists "course_files_admin_write" on storage.objects;
create policy "course_files_admin_write" on storage.objects
  for all to authenticated
  using (bucket_id = 'course-files' and public.has_role(auth.uid(), 'admin'))
  with check (bucket_id = 'course-files' and public.has_role(auth.uid(), 'admin'));