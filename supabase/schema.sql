-- =====================================================================
--  PurchaseDrive AI  -  Supabase database + storage setup
--  HOW TO RUN: Supabase Dashboard -> SQL Editor -> New query ->
--              paste this whole file -> click "Run".
--  It is safe to run more than once.
-- =====================================================================

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------
-- 1. INVOICES  (one row per uploaded purchase bill)
-- ---------------------------------------------------------------------
create table if not exists public.invoices (
    id                  uuid primary key default gen_random_uuid(),
    user_id             uuid not null default auth.uid()
                        references auth.users(id) on delete cascade,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),

    -- file information
    original_file_name  text not null,
    file_name           text not null,
    storage_path        text not null,
    folder_path         text not null default 'Inbox',
    mime_type           text,
    file_size           bigint,
    file_sha256         text,

    -- invoice details
    supplier_name       text,
    supplier_gstin      text,
    invoice_number      text,
    invoice_date        date,
    financial_year      text,
    taxable_amount      numeric(14,2),
    cgst_amount         numeric(14,2),
    sgst_amount         numeric(14,2),
    igst_amount         numeric(14,2),
    total_amount        numeric(14,2),
    amount_paid         numeric(14,2),
    payment_status      text not null default 'unknown'
                        check (payment_status in ('unpaid','partially_paid','paid','unknown')),

    -- workflow
    review_status       text not null default 'pending'
                        check (review_status in ('pending','confirmed')),
    is_duplicate        boolean not null default false,
    duplicate_of        uuid references public.invoices(id) on delete set null,

    -- OCR
    ocr_text            text,
    ocr_confidence      numeric(5,2),
    extraction          jsonb not null default '{}'::jsonb,
    notes               text,

    constraint invoices_user_path_unique unique (user_id, storage_path)
);

create index if not exists invoices_user_idx      on public.invoices(user_id);
create index if not exists invoices_date_idx      on public.invoices(user_id, invoice_date);
create index if not exists invoices_supplier_idx  on public.invoices(user_id, supplier_name);
create index if not exists invoices_number_idx    on public.invoices(user_id, invoice_number);
create index if not exists invoices_sha_idx       on public.invoices(user_id, file_sha256);

-- ---------------------------------------------------------------------
-- 2. INVOICE ITEMS  (line items: HSN, qty, rate ...)
-- ---------------------------------------------------------------------
create table if not exists public.invoice_items (
    id              uuid primary key default gen_random_uuid(),
    invoice_id      uuid not null references public.invoices(id) on delete cascade,
    user_id         uuid not null default auth.uid()
                    references auth.users(id) on delete cascade,
    line_no         integer not null default 1,
    description     text,
    hsn_code        text,
    quantity        numeric(14,3),
    unit            text,
    rate            numeric(14,2),
    taxable_amount  numeric(14,2),
    gst_rate        numeric(5,2),
    created_at      timestamptz not null default now()
);
create index if not exists invoice_items_invoice_idx on public.invoice_items(invoice_id);
create index if not exists invoice_items_user_idx    on public.invoice_items(user_id);

-- ---------------------------------------------------------------------
-- 3. USER SETTINGS
-- ---------------------------------------------------------------------
create table if not exists public.user_settings (
    user_id         uuid primary key default auth.uid()
                    references auth.users(id) on delete cascade,
    business_name   text,
    my_gstin        text,
    ocr_enabled     boolean not null default true,
    updated_at      timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- 4. updated_at trigger
-- ---------------------------------------------------------------------
create or replace function public.pd_set_updated_at()
returns trigger language plpgsql
set search_path = ''
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists invoices_set_updated_at on public.invoices;
create trigger invoices_set_updated_at before update on public.invoices
    for each row execute function public.pd_set_updated_at();

drop trigger if exists user_settings_set_updated_at on public.user_settings;
create trigger user_settings_set_updated_at before update on public.user_settings
    for each row execute function public.pd_set_updated_at();

-- ---------------------------------------------------------------------
-- 5. ROW LEVEL SECURITY  - every user sees ONLY their own rows
-- ---------------------------------------------------------------------
alter table public.invoices      enable row level security;
alter table public.invoice_items enable row level security;
alter table public.user_settings enable row level security;

revoke all on public.invoices, public.invoice_items, public.user_settings from anon;
grant select, insert, update, delete
    on public.invoices, public.invoice_items, public.user_settings to authenticated;

-- invoices
drop policy if exists "invoices_select_own" on public.invoices;
drop policy if exists "invoices_insert_own" on public.invoices;
drop policy if exists "invoices_update_own" on public.invoices;
drop policy if exists "invoices_delete_own" on public.invoices;
create policy "invoices_select_own" on public.invoices for select to authenticated
    using ((select auth.uid()) = user_id);
create policy "invoices_insert_own" on public.invoices for insert to authenticated
    with check ((select auth.uid()) = user_id);
create policy "invoices_update_own" on public.invoices for update to authenticated
    using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy "invoices_delete_own" on public.invoices for delete to authenticated
    using ((select auth.uid()) = user_id);

-- invoice items (must also belong to one of the user's own invoices)
drop policy if exists "items_select_own" on public.invoice_items;
drop policy if exists "items_insert_own" on public.invoice_items;
drop policy if exists "items_update_own" on public.invoice_items;
drop policy if exists "items_delete_own" on public.invoice_items;
create policy "items_select_own" on public.invoice_items for select to authenticated
    using ((select auth.uid()) = user_id);
create policy "items_insert_own" on public.invoice_items for insert to authenticated
    with check (
        (select auth.uid()) = user_id
        and exists (select 1 from public.invoices i
                    where i.id = invoice_id and i.user_id = (select auth.uid()))
    );
create policy "items_update_own" on public.invoice_items for update to authenticated
    using ((select auth.uid()) = user_id)
    with check (
        (select auth.uid()) = user_id
        and exists (select 1 from public.invoices i
                    where i.id = invoice_id and i.user_id = (select auth.uid()))
    );
create policy "items_delete_own" on public.invoice_items for delete to authenticated
    using ((select auth.uid()) = user_id);

-- user settings
drop policy if exists "settings_select_own" on public.user_settings;
drop policy if exists "settings_insert_own" on public.user_settings;
drop policy if exists "settings_update_own" on public.user_settings;
drop policy if exists "settings_delete_own" on public.user_settings;
create policy "settings_select_own" on public.user_settings for select to authenticated
    using ((select auth.uid()) = user_id);
create policy "settings_insert_own" on public.user_settings for insert to authenticated
    with check ((select auth.uid()) = user_id);
create policy "settings_update_own" on public.user_settings for update to authenticated
    using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy "settings_delete_own" on public.user_settings for delete to authenticated
    using ((select auth.uid()) = user_id);

-- ---------------------------------------------------------------------
-- 6. PRIVATE STORAGE BUCKET  "invoices"
--    Files are saved as  <user-id>/PurchaseDrive/2026/October/Supplier/file.pdf
--    A user can only touch files inside the folder named with their own id.
-- ---------------------------------------------------------------------
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('invoices', 'invoices', false, 20971520,
        array['image/jpeg', 'image/png', 'application/pdf'])
on conflict (id) do update
    set public = false,
        file_size_limit = excluded.file_size_limit,
        allowed_mime_types = excluded.allowed_mime_types;

drop policy if exists "pd_storage_select_own" on storage.objects;
drop policy if exists "pd_storage_insert_own" on storage.objects;
drop policy if exists "pd_storage_update_own" on storage.objects;
drop policy if exists "pd_storage_delete_own" on storage.objects;

create policy "pd_storage_select_own" on storage.objects for select to authenticated
    using (bucket_id = 'invoices'
           and (storage.foldername(name))[1] = (select auth.uid())::text);
create policy "pd_storage_insert_own" on storage.objects for insert to authenticated
    with check (bucket_id = 'invoices'
                and (storage.foldername(name))[1] = (select auth.uid())::text);
create policy "pd_storage_update_own" on storage.objects for update to authenticated
    using (bucket_id = 'invoices'
           and (storage.foldername(name))[1] = (select auth.uid())::text)
    with check (bucket_id = 'invoices'
                and (storage.foldername(name))[1] = (select auth.uid())::text);
create policy "pd_storage_delete_own" on storage.objects for delete to authenticated
    using (bucket_id = 'invoices'
           and (storage.foldername(name))[1] = (select auth.uid())::text);

-- Done. You should see "Success. No rows returned".
