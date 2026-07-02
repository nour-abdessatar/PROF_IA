# Prof IA

Plateforme éducative pour les élèves tunisiens (7ème, 8ème, 9ème année) avec espace étudiant et espace administrateur (gestion des cours par matière/niveau + tests de niveau QCM).

## Stack

- React 18 + Vite + TypeScript + Tailwind
- shadcn/ui, framer-motion
- **Supabase** (auth, base de données, storage) — standard, pas Lovable Cloud

## Setup local

### 1. Installer les dépendances

```bash
npm install        # ou: bun install
```

### 2. Créer un projet Supabase

1. Va sur https://app.supabase.com → New project
2. Une fois prêt : **Project Settings → API** → copie l'URL et la **anon public key**

### 3. Configurer l'environnement

```bash
cp .env.example .env.local
```

Édite `.env.local` :

```env
VITE_SUPABASE_URL=https://xxxxx.supabase.co
VITE_SUPABASE_ANON_KEY=eyJhbGciOi...
VITE_ADMIN_EMAIL=admin@college.tn
```

### 4. Initialiser la base de données

Dans le dashboard Supabase → **SQL Editor → New query**, colle le contenu de [`supabase/schema.sql`](./supabase/schema.sql) et exécute (Run).

> ⚠️ Le fichier contient `admin_email := 'admin@college.tn'`. Remplace cette valeur par celle de ton `VITE_ADMIN_EMAIL` **avant** d'exécuter.

Cela crée : `profiles`, `user_roles`, `courses`, `level_tests`, `test_questions`, le bucket de stockage `course-files`, les policies RLS et le trigger d'inscription.

### 5. (Optionnel) Désactiver la confirmation email pour les tests

Supabase → **Authentication → Providers → Email** → désactive *Confirm email* le temps des tests locaux.

### 6. Lancer

```bash
npm run dev
```

Le premier compte créé avec l'email `VITE_ADMIN_EMAIL` deviendra **automatiquement administrateur** et sera redirigé vers `/admin`. Les autres comptes deviennent étudiants et passent par `/onboarding`.

## Rôles

- **Étudiant** : onboarding (langue, prénom, nom IA), choix de niveau, test, dashboard d'activités.
- **Administrateur** : `/admin` — créer / supprimer des cours (avec PDF), créer des tests QCM par matière (10 matières dispo) et par niveau (7/8/9).

## Scripts

```bash
npm run dev        # serveur dev
npm run build      # build production
npm run preview    # prévisualiser le build
npm run test       # tests Vitest
```
