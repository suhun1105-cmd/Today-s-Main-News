# Supabase 설정

Supabase SQL Editor에서 아래 SQL을 한 번 실행하세요.

```sql
create table if not exists reports (
  report_date text primary key,
  html text not null,
  created_at timestamptz not null default now()
);

create table if not exists push_subscriptions (
  endpoint text primary key,
  subscription jsonb not null,
  updated_at timestamptz not null default now()
);
```

Render Environment에는 아래 값을 추가합니다.

```text
SUPABASE_URL=프로젝트 URL
SUPABASE_SERVICE_ROLE_KEY=service_role key
```

`SUPABASE_SERVICE_ROLE_KEY`는 외부에 공개하면 안 됩니다. Render 환경변수에만 넣으세요.
